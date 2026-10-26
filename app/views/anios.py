"""Semillas y clonado de años académicos/cursos (APIs)."""
from ._common import *  # noqa: F401,F403


CAMPOS_SEED = [
    campo.name for campo in MatriculaAnual._meta.fields
    if campo.name not in ('id', 'estudiante') and hasattr(Estudiante, campo.name)
]

@transaction.atomic
def seed_matricula_from_estudiantes(anio: int, only_activos: bool = True, overwrite: bool = False) -> Dict[str, Any]:
    qs = Estudiante.objects.select_related('curso')
    if only_activos:
        qs = qs.filter(estado='Activo')

    creados = actualizados = omitidos = 0

    for e in qs:
        curso_destino = map_curso_destino(
            e.curso, anio, False, True, NIVEL_POR_ASIGNAR, None,
        )
        defaults = MatriculaAnual.defaults_desde_estudiante(e)
        defaults['curso'] = curso_destino
        if overwrite:
            ma, created = MatriculaAnual.objects.update_or_create(
                estudiante=e, anio=anio, defaults=defaults,
            )
        else:
            ma, created = MatriculaAnual.objects.get_or_create(
                estudiante=e, anio=anio, defaults=defaults,
            )
        if created:
            creados += 1
        elif overwrite:
            actualizados += 1
        else:
            omitidos += 1

    return {
        'anio': anio,
        'total_fuente': qs.count(),
        'creados': creados,
        'actualizados': actualizados,
        'omitidos': omitidos,
    }


@login_required
@user_passes_test(is_admin)
@require_POST
def api_seed_estudiantes_a_anio(request):
    raw_anio = (request.POST.get('anio') or '').strip()
    if not raw_anio.isdigit():
        return JsonResponse({'ok': False, 'error': 'Año inválido'}, status=400)
    anio = int(raw_anio)
    if not (2000 <= anio <= 2100):
        return JsonResponse({'ok': False, 'error': 'Año fuera de rango'}, status=400)

    only_activos = request.POST.get('only_activos') == 'true'
    overwrite = request.POST.get('overwrite') == 'true'

    data = seed_matricula_from_estudiantes(anio, only_activos, overwrite)
    return JsonResponse({'ok': True, **data}, status=200)

BASICA_NIVELES = ["1° Básico", "2° Básico", "3° Básico", "4° Básico", "5° Básico", "6° Básico", "7° Básico", "8° Básico"]
MEDIA_NIVELES = ["1° Medio", "2° Medio", "3° Medio", "4° Medio"]

def _parse_basica_n(n):
    if not n: return None
    m = re.search(r'([1-8])', n)
    return int(m.group(1)) if 'básic' in n.lower() or 'basico' in n.lower() else None

def _parse_media_n(n):
    if not n: return None
    m = re.search(r'([1-4])', n)
    return int(m.group(1)) if 'medio' in n.lower() else None

def _fmt_basica(n): return f"{n}° Básico"
def _fmt_media(n): return f"{n}° Medio"

def next_nivel(nivel):
    if nivel == 'Prekinder':
        return 'Kinder'
    if nivel == 'Kinder':
        return '1° Básico'
    b = _parse_basica_n(nivel)
    if b: return _fmt_basica(b + 1) if b < 8 else "1° Medio"
    m = _parse_media_n(nivel)
    if m: return _fmt_media(m + 1) if m < 4 else None
    return None

def ensure_curso(anio, nivel, letra):
    letra = (letra or '').strip() or None
    obj, _ = Curso.objects.get_or_create(anio=anio, nivel=nivel, letra=letra)
    return obj

def ensure_academic_year(anio: int, visible=True):
    obj, _ = AcademicYear.objects.get_or_create(anio=anio, defaults={'is_visible': visible})
    return obj

def map_curso_destino(curso_origen, target_year, promote, auto_create, placeholder_level, graduate_level):
    if not curso_origen:
        return ensure_curso(target_year, placeholder_level, None) if placeholder_level and auto_create else None

    nivel, letra = curso_origen.nivel, curso_origen.letra
    if promote:
        next_level = next_nivel(nivel)
        if next_level:
            return ensure_curso(target_year, next_level, letra) if auto_create else Curso.objects.filter(anio=target_year, nivel=next_level, letra=letra).first()
        if nivel == '4° Medio' and graduate_level:
            return ensure_curso(target_year, graduate_level, None) if auto_create else Curso.objects.filter(anio=target_year, nivel=graduate_level).first()
        return None
    else:
        return ensure_curso(target_year, nivel, letra) if auto_create else Curso.objects.filter(anio=target_year, nivel=nivel, letra=letra).first()

@transaction.atomic
def clone_matriculas_anio(source_year, target_year, only_activos=False, promote_courses=False,
                          overwrite=False, snapshot_name=None, auto_create_courses=False,
                          placeholder_level=None, graduate_level=None):

    ensure_academic_year(source_year, True)
    ensure_academic_year(target_year, True)

    qs_source = MatriculaAnual.objects.filter(anio=source_year)
    if only_activos:
        qs_source = qs_source.filter(Q(estado='Activo') | Q(estado__isnull=True))

    warnings, created, updated, skipped = [], 0, 0, 0

    for ma in qs_source.select_related('estudiante', 'curso'):
        e = ma.estudiante
        if only_activos and e.estado != 'Activo':
            skipped += 1
            continue

        curso_dest = map_curso_destino(
            ma.curso, target_year, promote_courses, auto_create_courses, placeholder_level, graduate_level
        )

        if not curso_dest:
            warnings.append(f"No se encontró/creó curso destino para {e.nombre_completo} desde {ma.curso or 'Sin Nivel'} ({source_year}) → {target_year}")
            skipped += 1
            continue

        defaults = dict(MatriculaAnual.defaults_desde_estudiante(e),
                        curso=curso_dest, estado=e.estado or 'Activo')
        obj, is_created = MatriculaAnual.objects.get_or_create(estudiante=e, anio=target_year, defaults=defaults)
        if is_created:
            created += 1
        elif overwrite:
            for k, v in defaults.items():
                setattr(obj, k, v)
            obj.save(update_fields=list(defaults.keys()))
            updated += 1
        else:
            skipped += 1

        # Opción A: la ficha (Estudiante.curso) sigue al curso del año destino.
        # Solo hacia adelante: un clonado histórico no regresa el curso "actual";
        # incluso en filas omitidas (sin overwrite), obj.curso ya es la verdad de ese año.
        destino_final = obj.curso
        if destino_final and (e.curso_id is None or target_year >= e.curso.anio) \
                and e.curso_id != destino_final.id:
            e.curso = destino_final
            e.save(update_fields=['curso'])

    return {'source_year': source_year, 'target_year': target_year,
            'created': created, 'updated': updated, 'skipped': skipped, 'warnings': warnings[:100]}

@login_required
@user_passes_test(is_admin)
@require_POST
def api_clone_anio(request):
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except Exception:
        return JsonResponse({'ok': False, 'error': 'JSON inválido'}, status=400)

    try:
        source = int(payload.get('source_year'))
        target = int(payload.get('target_year'))
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Años inválidos'}, status=400)

    if source == target:
        return JsonResponse({'ok': False, 'error': 'Origen y destino no pueden ser iguales'}, status=400)

    result = clone_matriculas_anio(
        source_year=source,
        target_year=target,
        only_activos=payload.get('only_activos', False),
        promote_courses=payload.get('promote_courses', False),
        overwrite=payload.get('overwrite', False),
        snapshot_name=payload.get('snapshot_name'),
        auto_create_courses=payload.get('auto_create_courses', False),
        placeholder_level=payload.get('placeholder_level'),
        graduate_level=payload.get('graduate_level'),
    )
    return JsonResponse({'ok': True, **result}, status=200)

def _rows_source_for_course(source_curso, only_activos: bool):
    """
    Genera filas (estudiante, tipo, ma_src) a clonar.
    Prioriza MatriculaAnual del año del curso origen; si no hay, cae a Estudiante.
    """
    # 1) Primero intentar desde MatriculaAnual (más fiable por año)
    ma_qs = (
        MatriculaAnual.objects
        .filter(anio=source_curso.anio, curso=source_curso)
        .select_related('estudiante')
    )
    if only_activos:
        ma_qs = ma_qs.filter(Q(estado='Activo') | Q(estado__isnull=True))

    if ma_qs.exists():
        for ma in ma_qs.iterator():
            yield (ma.estudiante, 'MA', ma)
        return

    # 2) Si no hay MatriculaAnual, caer a Estudiante del curso origen
    e_qs = Estudiante.objects.filter(curso=source_curso).select_related('curso')
    if only_activos:
        e_qs = e_qs.filter(estado='Activo')

    for e in e_qs.iterator():
        yield (e, 'EST', None)


@transaction.atomic
def clone_curso_to_curso(
    source_curso_id: int,
    target_curso_id: int,
    *,
    overwrite: bool = False,
    only_activos: bool = False,
    move_estudiante_fk: bool = False,
    snapshot_name: str | None = None
):
    """
    Clona la matrícula de un curso origen (año A) a un curso destino (año B).
    - Crea/actualiza MatriculaAnual(estudiante, anio=destino, curso=destino, estado).
    - overwrite=True → sobreescribe curso/anio/estado en MatriculaAnual si ya existe.
    - only_activos=True → solo estudiantes/MA con estado 'Activo'.
    - move_estudiante_fk=True → también cambia Estudiante.curso al curso destino.
    - snapshot_name (opcional) por si quieres registrar auditoría aparte.
    """
    source = get_object_or_404(Curso, pk=source_curso_id)
    target = get_object_or_404(Curso, pk=target_curso_id)
    if source.id == target.id:
        raise ValueError("El curso origen y destino no pueden ser el mismo.")

    # Contadores
    creados = actualizados = omitidos = movidos_fk = 0

    for e, tipo, ma_src in _rows_source_for_course(source, only_activos):
        # Estado por defecto
        estado_final = (ma_src.estado if ma_src and getattr(ma_src, 'estado', None) else (e.estado or 'Activo'))

        defaults = dict(MatriculaAnual.defaults_desde_estudiante(e),
                        curso=target, estado=estado_final)

        # Unique recomendado: (estudiante, anio). Si no lo tienes, agrégalo en el modelo.
        obj, created = MatriculaAnual.objects.get_or_create(
            estudiante=e,
            anio=target.anio,
            defaults=defaults
        )

        if created:
            creados += 1
        elif overwrite:
            # Solo actualizamos si hay diferencias para evitar locks innecesarios
            changed = False
            if obj.curso_id != target.id:
                obj.curso = target
                changed = True
            if obj.estado != estado_final:
                obj.estado = estado_final
                changed = True
            if changed:
                obj.save(update_fields=['curso', 'estado'])
                actualizados += 1
            else:
                omitidos += 1
        else:
            omitidos += 1

        # Opcional: mover también el FK del Estudiante al curso destino
        if move_estudiante_fk and e.curso_id != target.id:
            e.curso = target
            # si además manejas 'estado' del estudiante por año, decide si tocarlo o no:
            # e.estado = estado_final   # (solo si es tu política)
            e.save(update_fields=['curso'])
            movidos_fk += 1

    return {
        'ok': True,
        'curso_origen': str(source),
        'curso_destino': str(target),
        'anio_origen': source.anio,
        'anio_destino': target.anio,
        'creados': creados,
        'actualizados': actualizados,
        'omitidos': omitidos,
        'movidos_fk_estudiante': movidos_fk,
        'solo_activos': only_activos,
        'overwrite': overwrite,
        'move_estudiante_fk': move_estudiante_fk,
        'snapshot_name': snapshot_name,
    }


@login_required
@user_passes_test(is_admin)
@require_POST
def api_clone_curso_a_curso(request):
    payload = json.loads(request.body.decode('utf-8')) if request.content_type.startswith('application/json') else request.POST
    try:
        data = clone_curso_to_curso(
            source_curso_id=int(payload.get('source_curso_id')),
            target_curso_id=int(payload.get('target_curso_id')),
            overwrite=str(payload.get('overwrite', 'false')).lower() == 'true',
            only_activos=str(payload.get('only_activos', 'false')).lower() == 'true',
            move_estudiante_fk=str(payload.get('move_estudiante_fk', 'false')).lower() == 'true',
            snapshot_name=(payload.get('snapshot_name') or '').strip() or None
        )
        return JsonResponse(data, status=200)
    except Exception as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=400)

