"""Vistas CRUD y listados de estudiantes."""
from ._common import *  # noqa: F401,F403


@role_required(*PERMITIDOS_TODOS)
def ficha_estudiante_view(request):
    cursos = Curso.objects.filter(
        filtro_q_por_niveles_permitidos(request.user, 'nivel', action='edit')
    ).order_by('anio', 'nivel', 'letra')
    # Lógica de la ficha del estudiante
    return render(request, 'app/ficha_estudiante.html', {'cursos': cursos})

@role_required(*PERMITIDOS_TODOS)
@transaction.atomic
def nuevo_estudiante_view(request):
    # === Quién matricula (visible y auditado) ===
    if request.user.is_authenticated:
        full_name = f"{getattr(request.user, 'nombre', '')} {getattr(request.user, 'apellido', '')}".strip()
        quien_matricula = full_name or request.user.get_username()
    else:
        quien_matricula = "No autenticado"

    # 1) Años visibles
    anios_visibles = list(
        AcademicYear.objects.filter(is_visible=True)
        .order_by('anio').values_list('anio', flat=True)
    )
    if not anios_visibles:
        anios_visibles = list(
            Curso.objects.order_by('anio').values_list('anio', flat=True).distinct()
        )

    # 2) Resolver año seleccionado (GET o POST oculto anio_filtro)
    raw_anio_get = (request.GET.get('anio') or '').strip()
    raw_anio_post = (request.POST.get('anio_filtro') or '').strip()
    anio_sel = None
    for val in (raw_anio_get, raw_anio_post):
        if val and val.isdigit():
            anio_sel = int(val)
            break

    # Fallback: anio_activo desde ConfiguracionSistema
    if anio_sel is None:
        cfg = ConfiguracionSistema.objects.first()
        if cfg and getattr(cfg, 'anio_activo', None):
            try:
                anio_sel = int(cfg.anio_activo)
            except Exception:
                anio_sel = None

    # Otro fallback: último visible
    if anio_sel is None and anios_visibles:
        anio_sel = int(anios_visibles[-1])

    # ====== [PERMISOS] Q por niveles permitidos para el modelo Curso (acción 'add') ======
    q_niv_curso = filtro_q_por_niveles_permitidos(request.user, 'nivel', action='add')

    # 3) Cursos filtrados por año seleccionado **y por permisos de nivel**
    cursos = Curso.objects.none()
    if anio_sel is not None:
        cursos = (
            Curso.objects
            .filter(anio=anio_sel)
            .filter(q_niv_curso)
            .order_by('nivel', 'letra')
        )

    # ====================== POST ======================
    if request.method == 'POST':
        try:
            # --- Datos mínimos + normalizaciones
            rut = (request.POST.get('rut') or '').strip().replace('.', '').replace('-', '').upper()
            nombre_completo = (request.POST.get('nombre_completo') or '').strip()

            # --- Curso (acepta curso_id o curso)
            curso_id = (request.POST.get('curso_id') or request.POST.get('curso') or '').strip()
            curso_obj = (
                Curso.objects
                .filter(id=curso_id or None, anio=anio_sel)
                .filter(q_niv_curso)
                .first()
            )
            if not curso_obj:
                msg = "Debes seleccionar un curso válido para el año visible y tus permisos."
                if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                    return JsonResponse({'ok': False, 'error': msg}, status=400)
                messages.error(request, msg)
                return render(request, 'app/nuevo_estudiante.html', {
                    'anios_visibles': anios_visibles,
                    'anio_sel': anio_sel,
                    'cursos': cursos,
                    'quien_matricula': quien_matricula,
                })

            # Chequeo de permiso por operación (add/edit) en el grupo del curso elegido
            nivel_group = curso_nivel_to_group(curso_obj.nivel)

            # --- Fechas
            fecha_nacimiento = request.POST.get('fecha_nacimiento') or None  # YYYY-MM-DD

            # NEW: fecha de nacimiento del apoderado titular
            fecha_nacimiento_apoderado_str = request.POST.get('fecha_nacimiento_apoderado') or None
            fecha_nacimiento_apoderado = None
            if fecha_nacimiento_apoderado_str:
                try:
                    fecha_nacimiento_apoderado = datetime.strptime(fecha_nacimiento_apoderado_str, "%Y-%m-%d").date()
                except ValueError:
                    fecha_nacimiento_apoderado = None

            sexo = (request.POST.get('sexo') or '').strip()
            nacionalidad = (request.POST.get('nacionalidad') or '').strip()
            domicilio = (request.POST.get('domicilio') or '').strip()
            comuna = (request.POST.get('comuna') or '').strip()
            ascendencia_indigena = (request.POST.get('ascendencia_indigena') or '') == 'Sí'
            etnia = (request.POST.get('etnia') or '').strip()
            pertenece_al_pie = (request.POST.get('pertenece_al_pie') or '') == 'Sí'
            ha_repetido_curso = (request.POST.get('ha_repetido_curso') or '') == 'Sí'
            estado = (request.POST.get('estado') or '').strip() or 'Activo'
            de_donde_viene = (request.POST.get('de_donde_viene') or '').strip()

            # Madre
            nombre_madre = (request.POST.get('nombre_madre') or '').strip()
            rut_madre = (request.POST.get('rut_madre') or '').strip()
            telefono_madre = (request.POST.get('telefono_madre') or '').strip()
            nivel_educacional_madre = (request.POST.get('nivel_educacional_madre') or '').strip()
            ocupacion_madre = (request.POST.get('ocupacion_madre') or '').strip()
            email_madre = (request.POST.get('email_madre') or '').strip()

            # Padre
            nombre_padre = (request.POST.get('nombre_padre') or '').strip()
            rut_padre = (request.POST.get('rut_padre') or '').strip()
            telefono_padre = (request.POST.get('telefono_padre') or '').strip()
            nivel_educacional_padre = (request.POST.get('nivel_educacional_padre') or '').strip()
            ocupacion_padre = (request.POST.get('ocupacion_padre') or '').strip()
            email_padre = (request.POST.get('email_padre') or '').strip()

            # Apoderado titular
            nombre_apoderado = (request.POST.get('nombre_apoderado') or '').strip()
            rut_apoderado = (request.POST.get('rut_apoderado') or '').strip()
            telefono_apoderado = (request.POST.get('telefono_apoderado') or '').strip()
            nivel_educacional_apoderado = (request.POST.get('nivel_educacional_apoderado') or '').strip()
            ocupacion_apoderado = (request.POST.get('ocupacion_apoderado') or '').strip()
            email_apoderado = (request.POST.get('email_apoderado') or '').strip()

            # Apoderado suplente
            nombre_apoderado_suplente = (request.POST.get('nombre_apoderado_suplente') or '').strip()
            parentesco_apoderado_suplente = (request.POST.get('parentesco_apoderado_suplente') or '').strip()
            rut_apoderado_suplente = (request.POST.get('rut_apoderado_suplente') or '').strip()
            telefono_apoderado_suplente = (request.POST.get('telefono_apoderado_suplente') or '').strip()
            nivel_educacional_apoderado_suplente = (request.POST.get('nivel_educacional_apoderado_suplente') or '').strip()
            ocupacion_apoderado_suplente = (request.POST.get('ocupacion_apoderado_suplente') or '').strip()
            email_apoderado_suplente = (request.POST.get('email_apoderado_suplente') or '').strip()

            # Social / Salud
            vive_con = (request.POST.get('vive_con') or '')
            resolucion_judicial = (request.POST.get('resolucion_judicial') or '') == 'Sí'
            problemas_salud = (request.POST.get('problemas_salud') or '') == 'Sí'
            descripcion_problemas_salud = (request.POST.get('descripcion_problemas_salud') or '').strip()
            impide_educacion_fisica = (request.POST.get('impide_educacion_fisica') or '') == 'Sí'
            sistema_salud = (request.POST.get('sistema_salud') or '').strip()

            # FIX nombre correcto desde el form
            seguro_salud_complementario = (request.POST.get('seguro_salud_complementario') or 'N/A').strip()

            # Transporte / otros
            tipo_transporte = (request.POST.get('tipo_transporte') or '').strip()
            opta_religion = (request.POST.get('opta_religion') or '') in ('1', 'Sí', 'si', 'SI', 'true', 'True')
            acepta_fotografias = (request.POST.get('acepta_fotografias') or '') in ('1', 'Sí', 'si', 'SI', 'true', 'True')
            fecha_matricula = request.POST.get('fecha_matricula') or None  # YYYY-MM-DD

            # Auditoría (FK)
            matriculado_por = request.user if getattr(request.user, 'is_authenticated', False) else None

            # ---------- Helpers internos ----------
            def _parse_ymd(s):
                if not s:
                    return None
                try:
                    return datetime.strptime(s, "%Y-%m-%d").date()
                except ValueError:
                    return None

            # ---------- Upsert por RUT ----------
            rut_norm = _norm_rut(rut) if '_norm_rut' in globals() else rut
            # Tolerante al formato guardado: matchea RUT normalizado o legado con guion
            est = estudiante_por_rut(rut_norm)
            is_create = est is None
            if not est:
                est = Estudiante(rut=rut_norm)
            elif est.rut != rut_norm:
                # Convergencia: la fila con RUT legado queda normalizada al guardar
                est.rut = rut_norm

            if not is_create:
                _require_curso_perm(request.user, 'edit', est.curso)

            # Permiso según operación en el nivel del curso elegido
            op = 'add' if is_create else 'edit'
            if not has_perm_on_nivel(request.user, op, nivel_group):
                msg = f"No tienes permiso para {'agregar' if is_create else 'editar'} estudiantes en {nivel_group.title()}."
                if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                    return JsonResponse({'ok': False, 'error': msg}, status=403)
                messages.error(request, msg)
                return render(request, 'app/nuevo_estudiante.html', {
                    'anios_visibles': anios_visibles,
                    'anio_sel': anio_sel,
                    'cursos': cursos,
                    'quien_matricula': quien_matricula,
                })

            actualizar_ficha = is_create or curso_obj.anio >= est.curso.anio

            # ---------- Set de campos ----------
            est.nombre_completo = nombre_completo
            est.fecha_nacimiento = _parse_ymd(fecha_nacimiento)
            est.sexo = sexo
            est.curso = curso_obj
            est.nacionalidad = nacionalidad
            est.domicilio = domicilio
            est.comuna = comuna
            est.ascendencia_indigena = ascendencia_indigena
            est.etnia = (etnia or None)
            est.pertenece_al_pie = pertenece_al_pie
            est.ha_repetido_curso = ha_repetido_curso
            est.estado = estado
            est.de_donde_viene = de_donde_viene

            # Madre
            est.nombre_madre = nombre_madre or None
            est.rut_madre = (_norm_rut(rut_madre) if '_norm_rut' in globals() else rut_madre) or None
            est.telefono_madre = telefono_madre or None
            est.nivel_educacional_madre = nivel_educacional_madre or None
            est.ocupacion_madre = ocupacion_madre or None
            est.email_madre = email_madre or None

            # Padre
            est.nombre_padre = nombre_padre or None
            est.rut_padre = (_norm_rut(rut_padre) if '_norm_rut' in globals() else rut_padre) or None
            est.telefono_padre = telefono_padre or None
            est.nivel_educacional_padre = nivel_educacional_padre or None
            est.ocupacion_padre = ocupacion_padre or None
            est.email_padre = email_padre or None

            # Apoderado titular
            est.nombre_apoderado = nombre_apoderado or None
            est.rut_apoderado = (_norm_rut(rut_apoderado) if '_norm_rut' in globals() else rut_apoderado) or None
            est.telefono_apoderado = telefono_apoderado or None
            est.nivel_educacional_apoderado = nivel_educacional_apoderado or None
            est.ocupacion_apoderado = ocupacion_apoderado or None
            est.email_apoderado = email_apoderado or None
            est.fecha_nacimiento_apoderado = fecha_nacimiento_apoderado  # ya parseada

            # Apoderado suplente
            est.nombre_apoderado_suplente = nombre_apoderado_suplente or None
            est.parentesco_apoderado_suplente = parentesco_apoderado_suplente or None
            est.rut_apoderado_suplente = (_norm_rut(rut_apoderado_suplente) if '_norm_rut' in globals() else rut_apoderado_suplente) or None
            est.telefono_apoderado_suplente = telefono_apoderado_suplente or None
            est.nivel_educacional_apoderado_suplente = nivel_educacional_apoderado_suplente or None
            est.ocupacion_apoderado_suplente = ocupacion_apoderado_suplente or None
            est.email_apoderado_suplente = email_apoderado_suplente or None

            # Social / Salud
            est.vive_con = vive_con
            est.resolucion_judicial = resolucion_judicial
            est.problemas_salud = problemas_salud
            est.descripcion_problemas_salud = descripcion_problemas_salud or None
            est.impide_educacion_fisica = impide_educacion_fisica
            est.sistema_salud = (sistema_salud if sistema_salud in ('Fonasa', 'Isapre', 'Particular') else 'Fonasa')
            est.seguro_salud_complementario = seguro_salud_complementario or None

            # Otros
            est.tipo_transporte = tipo_transporte or None
            est.opta_religion = opta_religion
            est.acepta_fotografias = acepta_fotografias
            est.fecha_matricula = _parse_ymd(fecha_matricula)

            # Auditoría (si tu modelo lo guarda al editar también)
            est.quien_matricula = quien_matricula or est.quien_matricula
            est.matriculado_por = matriculado_por or est.matriculado_por

            # Validación & save
            est.full_clean()
            with transaction.atomic():
                if actualizar_ficha:
                    est.save()
                MatriculaAnual.objects.update_or_create(
                    anio=curso_obj.anio, estudiante=est,
                    defaults=MatriculaAnual.defaults_desde_estudiante(est),
                )

            request.audit_objeto = f'Estudiante #{est.pk} · {curso_obj.anio}'
            # Devolver URL de impresión con ?anio correcto
            imprimir_url = request.build_absolute_uri(f"/imprimir-estudiante/{est.rut}/?anio={curso_obj.anio}")
            return JsonResponse({
                'ok': True,
                'imprimir_url': imprimir_url,
                'anio_sel': anio_sel,
                'operation': 'created' if is_create else 'updated'
            })

        except PermissionDenied:
            raise
        except Exception as e:
            err = str(e)
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({'ok': False, 'error': err}, status=400)
            messages.error(request, f'Ocurrió un error al guardar el estudiante: {err}')
            return render(request, 'app/nuevo_estudiante.html', {
                'anios_visibles': anios_visibles,
                'anio_sel': anio_sel,
                'cursos': cursos,
                'quien_matricula': quien_matricula,
            })

    # ====================== GET ======================
    return render(request, 'app/nuevo_estudiante.html', {
        'anios_visibles': anios_visibles,
        'anio_sel': anio_sel,
        'cursos': cursos,
        'quien_matricula': quien_matricula,
    })


@role_required(*PERMITIDOS_TODOS)
def buscar_estudiante(request):
    if request.method == 'GET':
        rut = request.GET.get('rut')
        
        # Verificar si `rut` fue proporcionado en la solicitud
        if rut:
            rut = rut.strip()  # Solo intenta hacer `strip()` si `rut` no es `None`
            try:
                # RUT tolerante al formato guardado ('123456785' o '12345678-5')
                estudiante = estudiante_por_rut(_norm_rut(rut))
                if estudiante is None:
                    raise Estudiante.DoesNotExist
                _require_curso_perm(request.user, 'view', estudiante.curso)
                data = {
                    'rut': estudiante.rut,
                    'nombre_completo': estudiante.nombre_completo,
                    'fecha_nacimiento': estudiante.fecha_nacimiento.strftime('%Y-%m-%d') if estudiante.fecha_nacimiento else '',
                    'sexo': estudiante.sexo,
                    # JSON serializable: antes crasheaba (Curso no es serializable)
                    'curso': str(estudiante.curso) if estudiante.curso else '',
                    'curso_id': estudiante.curso_id,
                    'nacionalidad': estudiante.nacionalidad,
                    'domicilio': estudiante.domicilio,
                    'comuna': estudiante.comuna,
                    'ascendencia_indigena': 'Sí' if estudiante.ascendencia_indigena else 'No',
                    'etnia': estudiante.etnia or '',
                    'pertenece_al_pie': 'Sí' if estudiante.pertenece_al_pie else 'No',
                    'ha_repetido_curso': 'Sí' if estudiante.ha_repetido_curso else 'No',
                    'estado': estudiante.estado,  # <-- Añadir el estado aquí
                    'de_donde_viene': estudiante.de_donde_viene,
                    'vive_con': estudiante.vive_con,
                    'resolucion_judicial': 'Sí' if estudiante.resolucion_judicial else 'No',
                    'problemas_salud': 'Sí' if estudiante.problemas_salud else 'No',
                    'descripcion_problemas_salud': estudiante.descripcion_problemas_salud or '',
                    'impide_educacion_fisica': 'Sí' if estudiante.impide_educacion_fisica else 'No',
                    'tipo_transporte': estudiante.tipo_transporte or '',
                    'opta_religion': 'Sí' if estudiante.opta_religion else 'No',
                    'acepta_fotografias': 'Sí' if estudiante.acepta_fotografias else 'No',
                    'fecha_matricula': estudiante.fecha_matricula.strftime('%Y-%m-%d') if estudiante.fecha_matricula else '',
                    
                    # Información de la madre
                    'nombre_madre': estudiante.nombre_madre or '',
                    'rut_madre': estudiante.rut_madre or '',
                    'nivel_educacional_madre': estudiante.nivel_educacional_madre or '',
                    'ocupacion_madre': estudiante.ocupacion_madre or '',
                    'telefono_madre': estudiante.telefono_madre or '',
                    'email_madre': estudiante.email_madre or '',
                    
                    # Información del padre
                    'nombre_padre': estudiante.nombre_padre or '',
                    'rut_padre': estudiante.rut_padre or '',
                    'nivel_educacional_padre': estudiante.nivel_educacional_padre or '',
                    'ocupacion_padre': estudiante.ocupacion_padre or '',
                    'telefono_padre': estudiante.telefono_padre or '',
                    'email_padre': estudiante.email_padre or '',
                    
                    # Información del apoderado
                    'nombre_apoderado': estudiante.nombre_apoderado or '',
                    'rut_apoderado': estudiante.rut_apoderado or '',
                    'nivel_educacional_apoderado': estudiante.nivel_educacional_apoderado or '',
                    'ocupacion_apoderado': estudiante.ocupacion_apoderado or '',
                    'telefono_apoderado': estudiante.telefono_apoderado or '',
                    'email_apoderado': estudiante.email_apoderado or '',

                    # Información del apoderado suplente
                    'nombre_apoderado_suplente': estudiante.nombre_apoderado_suplente or '',
                    'rut_apoderado_suplente': estudiante.rut_apoderado_suplente or '',
                    'Parentesco con el Estudiante': estudiante.parentesco_apoderado_suplente or '',
                    'nivel_educacional_apoderado_suplente': estudiante.nivel_educacional_apoderado_suplente or '',
                    'ocupacion_apoderado_suplente': estudiante.ocupacion_apoderado_suplente or '',
                    'telefono_apoderado_suplente': estudiante.telefono_apoderado_suplente or '',
                    'email_apoderado_suplente': estudiante.email_apoderado_suplente or '',
                }
                request.audit_objeto = f'Estudiante #{estudiante.pk}'
                return JsonResponse({'status': 'success', 'data': data})
            except Estudiante.DoesNotExist:
                return JsonResponse({'status': 'error', 'message': 'No se encontró un estudiante con ese RUT.'})
        else:
            return JsonResponse({'status': 'error', 'message': 'Debe proporcionar un RUT válido.'})
    return JsonResponse({'status': 'error', 'message': 'Método no permitido.'})

@login_required
@transaction.atomic  # est.save() + MatriculaAnual son un todo; falla → rollback completo
def guardar_estudiante(request):
    if request.method != 'POST':
        return redirect('home')

    # 0) Año destino (acepta varias claves)
    anio_raw = (
        (request.POST.get('anio') or '').strip()
        or (request.POST.get('anio_destino') or '').strip()
        or (request.POST.get('anio_filtro') or '').strip()
        or (request.GET.get('anio') or '').strip()
    )
    anio_destino = int(anio_raw) if anio_raw.isdigit() else None

    # 1) RUT normalizado
    rut_raw = (request.POST.get('rut') or '').strip()
    rut = _norm_rut(rut_raw)
    if not rut:
        msg = 'El RUT es un campo obligatorio.'
        if _is_ajax(request):
            return JsonResponse({'ok': False, 'error': msg}, status=400)
        messages.error(request, msg)
        return redirect('nuevo_estudiante')

    # 2) Curso / nivel permiso
    curso_key = request.POST.get('curso_id') or request.POST.get('curso')
    curso = None
    nivel_perm = None
    if curso_key:
        try:
            curso = Curso.objects.get(pk=int(curso_key))
            nivel_perm = _nivel_from_curso_obj(curso)  # BASICA / MEDIA (o lo que uses)
        except (Curso.DoesNotExist, ValueError):
            curso = None
            nivel_perm = None

    # 2.1) Validaciones de consistencia del curso respecto del año destino
    if curso is not None:
        if getattr(curso, 'nivel', None) == NIVEL_POR_ASIGNAR:
            msg = 'No puedes asignar el curso "Por Asignar" como curso real.'
            if _is_ajax(request):
                return JsonResponse({'ok': False, 'error': msg}, status=400)
            messages.error(request, msg)
            return redirect('nuevo_estudiante')

        if anio_destino is not None and curso.anio != anio_destino:
            msg = f'El curso seleccionado pertenece a {curso.anio}, pero el año destino es {anio_destino}.'
            if _is_ajax(request):
                return JsonResponse({'ok': False, 'error': msg}, status=400)
            messages.error(request, msg)
            return redirect('nuevo_estudiante')

    # 3) Parsear fechas *antes* de tocar BD
    fecha_str = (request.POST.get('fecha_nacimiento') or '').strip()
    fecha_nac = None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
        try:
            fecha_nac = datetime.strptime(fecha_str, fmt).date()
            break
        except ValueError:
            continue
    if not fecha_nac:
        msg = "La fecha de nacimiento del estudiante es obligatoria o tiene formato inválido."
        if _is_ajax(request):
            return JsonResponse({'ok': False, 'error': msg}, status=400)
        messages.error(request, msg)
        return redirect('nuevo_estudiante')

    fecha_apod_str = (request.POST.get('fecha_nacimiento_apoderado') or '').strip()
    fecha_nac_apod = None
    if fecha_apod_str:
        for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
            try:
                fecha_nac_apod = datetime.strptime(fecha_apod_str, fmt).date()
                break
            except ValueError:
                continue

    # 4) Obtener o preparar instancia SIN guardar (upsert por RUT)
    estudiante = estudiante_por_rut(rut)  # tolera el formato legado (guion) guardado
    is_create = estudiante is None
    if not estudiante:
        estudiante = Estudiante(rut=rut)
    elif estudiante.rut != rut:
        # convergencia: la fila legada queda con RUT normalizado al guardar
        estudiante.rut = rut

    _require_curso_perm(request.user, 'add' if is_create else 'edit', curso)
    if not is_create:
        _require_curso_perm(request.user, 'edit', estudiante.curso)

    actualizar_ficha = is_create or (curso is not None and curso.anio >= estudiante.curso.anio)

    # 6) Setear campos del modelo
    try:
        # ---------- Curso “actual” (opcional) ----------
        estudiante.curso = curso  # puede quedar None

        # ---------- Básicos ----------
        estudiante.nombre_completo = (request.POST.get('nombre_completo') or '').strip()
        if not estudiante.nombre_completo:
            msg = "El nombre completo es obligatorio."
            if _is_ajax(request):
                return JsonResponse({'ok': False, 'error': msg}, status=400)
            messages.error(request, msg)
            return redirect('nuevo_estudiante')

        estudiante.fecha_nacimiento = fecha_nac
        estudiante.sexo = (request.POST.get('sexo') or '').strip()
        estudiante.nacionalidad = (request.POST.get('nacionalidad') or '').strip()
        estudiante.domicilio = (request.POST.get('domicilio') or '').strip()
        estudiante.comuna = (request.POST.get('comuna') or '').strip()

        # ---------- Condición ----------
        estudiante.ascendencia_indigena = _to_bool(request.POST.get('ascendencia_indigena'))
        estudiante.etnia = (request.POST.get('etnia') or '').strip() or None
        estudiante.pertenece_al_pie = _to_bool(request.POST.get('pertenece_al_pie'))
        estudiante.ha_repetido_curso = _to_bool(request.POST.get('ha_repetido_curso'))
        estudiante.estado = (request.POST.get('estado') or 'Activo').strip()
        estudiante.de_donde_viene = (request.POST.get('de_donde_viene') or '').strip()

        # ---------- Madre ----------
        estudiante.nombre_madre = (request.POST.get('nombre_madre') or '').strip()
        estudiante.rut_madre = _norm_rut(request.POST.get('rut_madre') or '')
        estudiante.telefono_madre = (request.POST.get('telefono_madre') or '').strip()
        estudiante.nivel_educacional_madre = (request.POST.get('nivel_educacional_madre') or '').strip()
        estudiante.ocupacion_madre = (request.POST.get('ocupacion_madre') or '').strip()
        estudiante.email_madre = (request.POST.get('email_madre') or '').strip()

        # ---------- Padre ----------
        estudiante.nombre_padre = (request.POST.get('nombre_padre') or '').strip()
        estudiante.rut_padre = _norm_rut(request.POST.get('rut_padre') or '')
        estudiante.telefono_padre = (request.POST.get('telefono_padre') or '').strip()
        estudiante.nivel_educacional_padre = (request.POST.get('nivel_educacional_padre') or '').strip()
        estudiante.ocupacion_padre = (request.POST.get('ocupacion_padre') or '').strip()
        estudiante.email_padre = (request.POST.get('email_padre') or '').strip()

        # ---------- Apoderado titular ----------
        estudiante.nombre_apoderado = (request.POST.get('nombre_apoderado') or '').strip()
        estudiante.rut_apoderado = _norm_rut(request.POST.get('rut_apoderado') or '')
        estudiante.telefono_apoderado = (request.POST.get('telefono_apoderado') or '').strip()
        estudiante.nivel_educacional_apoderado = (request.POST.get('nivel_educacional_apoderado') or '').strip()
        estudiante.ocupacion_apoderado = (request.POST.get('ocupacion_apoderado') or '').strip()
        estudiante.email_apoderado = (request.POST.get('email_apoderado') or '').strip()
        estudiante.fecha_nacimiento_apoderado = fecha_nac_apod

        # ---------- Apoderado suplente ----------
        estudiante.nombre_apoderado_suplente = (request.POST.get('nombre_apoderado_suplente') or '').strip()
        estudiante.rut_apoderado_suplente = _norm_rut(request.POST.get('rut_apoderado_suplente') or '')
        estudiante.parentesco_apoderado_suplente = _get_text(request, 'parentesco_apoderado_suplente') or None
        estudiante.telefono_apoderado_suplente = (request.POST.get('telefono_apoderado_suplente') or '').strip()
        estudiante.nivel_educacional_apoderado_suplente = (request.POST.get('nivel_educacional_apoderado_suplente') or '').strip()
        estudiante.ocupacion_apoderado_suplente = (request.POST.get('ocupacion_apoderado_suplente') or '').strip()
        estudiante.email_apoderado_suplente = (request.POST.get('email_apoderado_suplente') or '').strip()

        # ---------- Social / Salud ----------
        estudiante.vive_con = (request.POST.get('vive_con') or '').strip()
        estudiante.resolucion_judicial = _to_bool(request.POST.get('resolucion_judicial'))
        estudiante.problemas_salud = _to_bool(request.POST.get('problemas_salud'))
        estudiante.descripcion_problemas_salud = (request.POST.get('descripcion_problemas_salud') or '').strip()
        estudiante.impide_educacion_fisica = _to_bool(request.POST.get('impide_educacion_fisica'))

        sistema_salud = (request.POST.get('sistema_salud') or '').strip()
        if sistema_salud not in ('Fonasa', 'Isapre', 'Particular'):  # 'Particular' existe en el modelo
            sistema_salud = 'Fonasa'
        estudiante.sistema_salud = sistema_salud

        if hasattr(estudiante, 'certificado_medico'):
            flag_cm = request.POST.get('certificado_medico') or request.POST.get('presenta_certificado')
            estudiante.certificado_medico = _to_bool(flag_cm)

        estudiante.seguro_salud_complementario = _get_text(request, 'seguro_salud_complementario') or None

        # ---------- Otros ----------
        estudiante.tipo_transporte = (request.POST.get('tipo_transporte') or '').strip()
        estudiante.opta_religion = _to_bool(request.POST.get('opta_religion'))
        estudiante.acepta_fotografias = _to_bool(request.POST.get('acepta_fotografias'))
        estudiante.fecha_matricula = _parse_date(request.POST.get('fecha_matricula'))

        estudiante.full_clean()
        with transaction.atomic():
            if actualizar_ficha:
                estudiante.save()
            MatriculaAnual.objects.update_or_create(
                estudiante=estudiante, anio=curso.anio,
                defaults=MatriculaAnual.defaults_desde_estudiante(estudiante),
            )

    except Exception as e:
        if _is_ajax(request):
            return JsonResponse({'ok': False, 'error': str(e)}, status=400)
        messages.error(request, f'Error al guardar el estudiante: {e}')
        return redirect('nuevo_estudiante')

    request.audit_objeto = f'Estudiante #{estudiante.pk} · {curso.anio}'
    # Éxito
    if _is_ajax(request):
        payload_ok = {"ok": True, "id": estudiante.id, "rut": estudiante.rut}
        if curso is not None:
            payload_ok.update({
                "curso_id": curso.id,
                "curso_nivel": curso.nivel,
                "curso_letra": curso.letra,
                "anio": (anio_destino if anio_destino is not None else curso.anio),
            })
        payload_ok["operation"] = "created" if is_create else "updated"
        return JsonResponse(payload_ok, status=201)

    messages.success(request, 'Estudiante guardado correctamente.')
    # Volvemos a la ficha (imprimir queda a un clic en la barra de acciones)
    return redirect('detalles_estudiante', estudiante_id=estudiante.id)

@login_required
@role_required(*PERMITIDOS_TODOS)
def listar_estudiantes(request):
    q = (request.GET.get('q') or '').strip()
    estado_filtro = request.GET.get('estado', '')
    if estado_filtro not in dict(Estudiante._meta.get_field('estado').choices):
        estado_filtro = ''
    curso_id = (request.GET.get('curso') or '').strip()
    anio_param = (request.GET.get('anio') or '').strip()

    # ===== permisos por nivel =====
    niveles_ok = niveles_permitidos_por_usuario(request.user, action='view')
    if not niveles_ok:
        messages.error(request, 'No tienes permisos para ver estudiantes en Básica ni en Media.')
        return render(request, 'app/listar_estudiantes.html', {
            'cursos': [], 'curso_id': '', 'curso_seleccionado': None,
            'q': q, 'estudiantes': [], 'anio': None, 'anios_disponibles': [],
        })

    # Filtros por nivel para Curso y para MatriculaAnual (MA)
    q_niv_curso = filtro_q_por_niveles_permitidos(request.user, 'nivel')
    q_niv_ma = filtro_q_por_niveles_permitidos(request.user, 'curso__nivel')

    # ===== años disponibles =====
    visible_years = list(
        AcademicYear.objects.filter(is_visible=True)
        .values_list('anio', flat=True).order_by('anio')
    )
    # IMPORTANTe: limitar años de MA por niveles permitidos
    anios_ma = list(
        MatriculaAnual.objects.filter(q_niv_ma)
        .values_list('anio', flat=True).distinct().order_by('anio')
    )
    # Cursos por niveles permitidos
    anios_curso = list(
        Curso.objects.filter(q_niv_curso)
        .values_list('anio', flat=True).distinct().order_by('anio')
    )

    if visible_years:
        anios_disponibles = sorted((set(anios_ma) | set(anios_curso)) & set(visible_years))
    else:
        anios_disponibles = sorted(set(anios_ma) | set(anios_curso))

    if not anios_disponibles:
        # fallback extra por si no hay MA ni AcademicYear marcados
        anios_disponibles = list(
            Curso.objects.filter(q_niv_curso).values_list('anio', flat=True).distinct().order_by('anio')
        ) or [timezone.now().year]

    # ===== año seleccionado =====
    anio_sel: Optional[int] = None
    if anio_param.isdigit():
        cand = int(anio_param)
        if (not visible_years) or (cand in anios_disponibles):
            anio_sel = cand
    if anio_sel is None:
        anio_sel = anios_disponibles[-1]

    # Asegura que existan Por Asignar/Egresados (no estorba si ya existen)
    Curso.objects.get_or_create(anio=anio_sel, nivel=NIVEL_POR_ASIGNAR, letra=None)
    Curso.objects.get_or_create(anio=anio_sel, nivel=NIVEL_EGRESADOS,   letra=None)

    # ===== cursos para selector (filtrados por nivel permitido + año) =====
    cursos_qs = (
        Curso.objects.filter(q_niv_curso, anio=anio_sel)
        .annotate(
            n_ma=Count('matriculas', filter=Q(matriculas__anio=anio_sel), distinct=True),
            n_estudiantes=Count('estudiantes', distinct=True),
        )
        .annotate(
            n_est=Case(
                When(n_ma__gt=0, then=F('n_ma')),
                default=F('n_estudiantes'),
                output_field=IntegerField()
            ),
            ord_especial=Case(
                When(nivel=NIVEL_POR_ASIGNAR, then=Value(0)),
                When(nivel=NIVEL_EGRESADOS,   then=Value(2)),
                default=Value(1),
                output_field=IntegerField()
            )
        )
        .order_by('ord_especial', 'nivel', 'letra')
    )

    # Item virtual: SIN NIVEL (solo existe en MA)
    sin_nivel_count = MatriculaAnual.objects.filter(anio=anio_sel).filter(q_niv_ma, curso__isnull=True).count()
    cursos = list(cursos_qs)
    cursos.append(
        SimpleNamespace(
            id='none', anio=anio_sel, nivel=LABEL_SIN_NIVEL, letra=None,
            n_est=sin_nivel_count, ord_especial=3
        )
    )

    estudiantes_rows: List[EstuRow] = []
    curso_seleccionado = None
    _sin_nivel_course = SimpleNamespace(nivel=LABEL_SIN_NIVEL, letra=None, anio=anio_sel)

    # ===== con curso seleccionado =====
    if curso_id:
        if curso_id == 'none':
            # Mostrar SOLO los MA sin curso del año (pero igual respetamos permisos por si acaso)
            curso_seleccionado = f"{LABEL_SIN_NIVEL} - {anio_sel}"
            ma_qs = (
                MatriculaAnual.objects
                .filter(anio=anio_sel, curso__isnull=True).filter(q_niv_ma)
                .select_related('estudiante')
                .order_by('estudiante__nombre_completo')
            )
            if q:
                ma_qs = ma_qs.filter(
                    Q(estudiante__nombre_completo__icontains=q) |
                    Q(estudiante__rut__icontains=q)
                )
            for ma in ma_qs:
                e = ma.estudiante
                estudiantes_rows.append(
                    EstuRow(e.id, e.rut, e.nombre_completo, _sin_nivel_course, (ma.estado or e.estado))
                )

        else:
            # Curso normal
            try:
                curso_sel = get_object_or_404(Curso, pk=int(curso_id))
            except (ValueError, Curso.DoesNotExist):
                curso_sel = None

            # Seguridad extra: no permitir cursos fuera de los niveles permitidos
            if curso_sel and not Curso.objects.filter(pk=curso_sel.pk).filter(q_niv_curso).exists():
                curso_sel = None  # ignora curso no permitido

            if curso_sel and curso_sel.anio == anio_sel:
                curso_seleccionado = f"{curso_sel.nivel}{' ' + curso_sel.letra if curso_sel.letra else ''} - {curso_sel.anio}"

                ma_qs = (
                    MatriculaAnual.objects
                    .filter(anio=anio_sel, curso_id=curso_sel.id)
                    .select_related('estudiante', 'curso')
                    .order_by('estudiante__nombre_completo')
                )
                if q:
                    ma_qs = ma_qs.filter(
                        Q(estudiante__nombre_completo__icontains=q) |
                        Q(estudiante__rut__icontains=q)
                    )

                if ma_qs.exists():
                    for ma in ma_qs:
                        e = ma.estudiante
                        estudiantes_rows.append(
                            EstuRow(e.id, e.rut, e.nombre_completo, (ma.curso or _sin_nivel_course), (ma.estado or e.estado))
                        )
                else:
                    # === Fallback: si no hay MA de este curso en este año, mostrar Estudiante del curso (pero mismo filtro de nivel) ===
                    est_qs = (
                        Estudiante.objects
                        .filter(curso_id=curso_sel.id)
                        .select_related('curso')
                        .order_by('nombre_completo')
                    )
                    if q:
                        est_qs = est_qs.filter(Q(nombre_completo__icontains=q) | Q(rut__icontains=q))
                    for e in est_qs:
                        estudiantes_rows.append(
                            EstuRow(e.id, e.rut, e.nombre_completo, e.curso, e.estado)
                        )

    # ===== sin curso seleccionado =====
    if not curso_id:
        # Mostrar TODOS los MA del año (incluye sin nivel) PERO solo de niveles permitidos
        ma_qs_all = MatriculaAnual.objects.filter(anio=anio_sel).filter(q_niv_ma)
        if ma_qs_all.exists():
            qs = (
                ma_qs_all.select_related('estudiante', 'curso')
                .order_by('curso__nivel', 'curso__letra', 'estudiante__nombre_completo')
            )
            if q:
                qs = qs.filter(
                    Q(estudiante__nombre_completo__icontains=q) |
                    Q(estudiante__rut__icontains=q) |
                    Q(curso__nivel__icontains=q) |
                    Q(curso__letra__icontains=q)
                )
            for ma in qs:
                e = ma.estudiante
                estudiantes_rows.append(
                    EstuRow(e.id, e.rut, e.nombre_completo, (ma.curso or _sin_nivel_course), (ma.estado or e.estado))
                )
        else:
            # === Fallback: listar por Estudiante (curso__anio=año) PERO restringiendo nivel ===
            est_qs = (
                Estudiante.objects.filter(
                    curso__anio=anio_sel,
                ).filter(q_niv_ma)
                .select_related('curso')
                .order_by('curso__nivel', 'curso__letra', 'nombre_completo')
            )
            if q:
                est_qs = est_qs.filter(
                    Q(nombre_completo__icontains=q) | Q(rut__icontains=q) |
                    Q(curso__nivel__icontains=q) | Q(curso__letra__icontains=q)
                )
            for e in est_qs:
                estudiantes_rows.append(
                    EstuRow(e.id, e.rut, e.nombre_completo, e.curso, e.estado)
                )

    if estado_filtro:
        estudiantes_rows = [e for e in estudiantes_rows if e.estado == estado_filtro]
    return render(request, 'app/listar_estudiantes.html', {
        'estado_filtro': estado_filtro,
        'cursos': cursos,
        'curso_id': curso_id,
        'curso_seleccionado': curso_seleccionado,
        'q': q,
        'estudiantes': estudiantes_rows,
        'anio': anio_sel,
        'anios_disponibles': anios_disponibles,
    })

@login_required
def ficha_matricula(request):
    # Años visibles
    anios_visibles = list(
        AcademicYear.objects.filter(is_visible=True)
        .order_by('anio')
        .values_list('anio', flat=True)
    )

    # Resolver año actual: ?anio=... -> Config activa -> último visible
    anio_get = request.GET.get('anio')
    anio_cfg = (ConfiguracionMatricula.objects.filter(activa=True)
                .values_list('anio', flat=True).first())
    try:
        anio_actual = int(anio_get or anio_cfg or (anios_visibles[-1] if anios_visibles else 0))
    except (TypeError, ValueError):
        anio_actual = anios_visibles[-1] if anios_visibles else None

    cursos = Curso.objects.all().order_by('anio', 'nivel', 'letra', 'id')

    return render(request, 'app/tu_template_ficha.html', {
        'anios_visibles': anios_visibles,
        'anio_actual': anio_actual,
        'cursos': cursos,
    })

@role_required(*PERMITIDOS_TODOS)
def ficha_estudiante(request):
    # Años visibles desde tu tabla (si no hay, hacemos fallback con años detectados en Curso)
    anios_visibles = list(
        AcademicYear.objects.filter(is_visible=True)
        .order_by('anio')
        .values_list('anio', flat=True)
    )

    # Fallback si no hay años visibles configurados
    if not anios_visibles:
        anios_visibles = list(
            Curso.objects.order_by('anio')
            .values_list('anio', flat=True)
            .distinct()
        )

    # Determinar anio_actual (querystring > config activa > último visible > año calendario)
    qs_anio = request.GET.get('anio')
    anio_actual = None
    try:
        anio_qs = int(qs_anio) if qs_anio else None
    except ValueError:
        anio_qs = None

    if anio_qs and anio_qs in anios_visibles:
        anio_actual = anio_qs
    else:
        cfg = ConfiguracionMatricula.objects.filter(activa=True).first()
        if cfg and getattr(cfg, 'anio', None) in anios_visibles:
            anio_actual = cfg.anio
        elif anios_visibles:
            anio_actual = max(anios_visibles)
        else:
            anio_actual = timezone.now().year

    # Cursos para poblar el <select>, todos los de los años visibles
    cursos = Curso.objects.filter(anio__in=anios_visibles).filter(
        filtro_q_por_niveles_permitidos(request.user, 'nivel', action='edit')
    ).order_by('anio', 'nivel', 'letra')

    ctx = {
        'anios_visibles': anios_visibles,   # --> usado en el <select id="anioVisible">
        'anio_actual': anio_actual,         # --> marca la opción selected
        'cursos': cursos,                   # --> options con data-anio
    }
    return render(request, 'app/ficha_estudiante.html', ctx)

@require_GET
@role_required(*PERMITIDOS_TODOS)
def buscar_estudiantes_por_rut(request):
    rut = (request.GET.get('rut') or '').strip()

    # Año usado
    try:
        anio_param = int(request.GET.get('anio')) if request.GET.get('anio') else None
    except (TypeError, ValueError):
        anio_param = None

    visibles_qs = AcademicYear.objects.filter(is_visible=True).values_list('anio', flat=True)
    visibles = sorted({int(a) for a in visibles_qs})
    anio_calendario = timezone.now().year
    if anio_param and anio_param in visibles:
        anio_usado, fallback = anio_param, False
    else:
        anio_usado, fallback = (visibles[0] if visibles else anio_calendario), True

    if not rut:
        resp = JsonResponse([], safe=False)
        resp['X-Anio-Usado'] = str(anio_usado)
        resp['X-Anios-Visibles'] = ",".join(str(a) for a in visibles)
        resp['X-Anio-Fallback'] = '1' if (anio_param is None or anio_param not in visibles) else '0'
        return resp

    # Búsqueda tolerante al formato: RUT guardado como '123456785' o '12345678-5'
    rut_norm = _norm_rut(rut) or rut
    estudiantes = (
        Estudiante.objects
        .select_related('curso')
        .filter(filtro_q_por_niveles_permitidos(request.user, 'curso__nivel'))
        .filter(Q(rut__in=_rut_variants(rut_norm)) | Q(rut__startswith=rut_norm))
        .order_by('id')
    )

    data = []
    vistos = set()
    for e in estudiantes:
        clave = _norm_rut(e.rut)
        if clave in vistos:
            continue  # misma persona por dos formatos (duplicados legados)
        vistos.add(clave)
        curso_texto, curso_obj, tiene = _curso_en_anio(e, anio_usado)

        data.append({
            'id': e.id,
            'estado_actual': e.estado,
            'rut': e.rut,
            'nombre_completo': e.nombre_completo,
            'curso_text': curso_texto,
            'curso_id': getattr(curso_obj, 'id', None),
            'anio_mostrado': anio_usado,
            'tiene_matricula_en_anio': tiene,
            # ... (resto de campos como ya los tienes) ...
            'fecha_nacimiento': e.fecha_nacimiento.strftime('%Y-%m-%d') if e.fecha_nacimiento else None,
            'sexo': e.sexo,
            'nacionalidad': e.nacionalidad,
            'domicilio': e.domicilio,
            'comuna': e.comuna,
            'ascendencia_indigena': 'Sí' if e.ascendencia_indigena else 'No',
            'etnia': e.etnia,
            'pertenece_al_pie': 'Sí' if e.pertenece_al_pie else 'No',
            'ha_repetido_curso': 'Sí' if e.ha_repetido_curso else 'No',
            'estado': e.estado,
            'de_donde_viene': e.de_donde_viene,
            'nombre_madre': e.nombre_madre,
            'rut_madre': e.rut_madre,
            'telefono_madre': e.telefono_madre,
            'nivel_educacional_madre': e.nivel_educacional_madre,
            'ocupacion_madre': e.ocupacion_madre,
            'email_madre': e.email_madre,
            'nombre_padre': e.nombre_padre,
            'rut_padre': e.rut_padre,
            'telefono_padre': e.telefono_padre,
            'nivel_educacional_padre': e.nivel_educacional_padre,
            'ocupacion_padre': e.ocupacion_padre,
            'email_padre': e.email_padre,
            'nombre_apoderado': e.nombre_apoderado,
            'rut_apoderado': e.rut_apoderado,
            'fecha_nacimiento_apoderado': (e.fecha_nacimiento_apoderado.strftime('%Y-%m-%d')
                                           if getattr(e, 'fecha_nacimiento_apoderado', None) else None),
            'telefono_apoderado': e.telefono_apoderado,
            'nivel_educacional_apoderado': e.nivel_educacional_apoderado,
            'ocupacion_apoderado': e.ocupacion_apoderado,
            'email_apoderado': e.email_apoderado,
            'nombre_apoderado_suplente': e.nombre_apoderado_suplente,
            'rut_apoderado_suplente': e.rut_apoderado_suplente,
            'parentesco_apoderado_suplente': e.parentesco_apoderado_suplente,
            'telefono_apoderado_suplente': e.telefono_apoderado_suplente,
            'nivel_educacional_apoderado_suplente': e.nivel_educacional_apoderado_suplente,
            'ocupacion_apoderado_suplente': e.ocupacion_apoderado_suplente,
            'email_apoderado_suplente': e.email_apoderado_suplente,
            'vive_con': e.vive_con,
            'resolucion_judicial': 'Sí' if e.resolucion_judicial else 'No',
            'problemas_salud': 'Sí' if e.problemas_salud else 'No',
            'descripcion_problemas_salud': e.descripcion_problemas_salud,
            'impide_educacion_fisica': 'Sí' if e.impide_educacion_fisica else 'No',
            'sistema_salud': e.sistema_salud,
            'presenta_certificado': 'Sí' if getattr(e, 'certificado_medico', False) else 'No',
            'seguro_salud_complementario': e.seguro_salud_complementario,
            'tipo_transporte': e.tipo_transporte,
            'opta_religion': e.opta_religion,
            'acepta_fotografias': e.acepta_fotografias,
            'fecha_matricula': e.fecha_matricula.strftime('%Y-%m-%d') if e.fecha_matricula else None,
        })

    request.audit_objeto = ', '.join(f"Estudiante #{e['id']}" for e in data) or 'Sin coincidencias'
    resp = JsonResponse(data, safe=False)
    resp['X-Anio-Usado'] = str(anio_usado)
    resp['X-Anios-Visibles'] = ",".join(str(a) for a in visibles)
    resp['X-Anio-Fallback'] = '1' if fallback else '0'
    return resp

@require_POST
@role_required(*PERMITIDOS_TODOS)
@transaction.atomic
def asignar_curso_en_anio(request):
    import json
    payload = {}

    # Soporta JSON y form-data
    if request.content_type and 'application/json' in request.content_type:
        try:
            payload = json.loads(request.body.decode('utf-8') or '{}')
        except Exception:
            payload = {}
    rut = (payload.get('rut') or request.POST.get('rut') or '').strip()
    anio_raw = payload.get('anio') or request.POST.get('anio')
    curso_id_raw = payload.get('curso_id') or request.POST.get('curso_id')

    if not rut or not anio_raw or not curso_id_raw:
        return JsonResponse({'ok': False, 'error': 'Faltan parámetros: rut, anio, curso_id.'}, status=400)

    try:
        anio = int(anio_raw)
        curso_id = int(curso_id_raw)
    except (TypeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'anio y curso_id deben ser enteros.'}, status=400)

    # Búsqueda tolerante al formato del RUT guardado (normalizado o con guion legado)
    e = estudiante_por_rut(_norm_rut(rut))
    if e is None:
        return JsonResponse({'ok': False, 'error': 'Estudiante no encontrado con ese RUT.'}, status=404)
    curso = get_object_or_404(Curso, id=curso_id)

    # Validaciones de consistencia
    if curso.anio != anio:
        return JsonResponse({'ok': False, 'error': 'El curso no pertenece al año seleccionado.'}, status=400)
    if getattr(curso, 'nivel', None) == NIVEL_POR_ASIGNAR:
        return JsonResponse({'ok': False, 'error': 'No puedes asignar el placeholder "Por Asignar" como curso.'}, status=400)

    _require_curso_perm(request.user, 'edit', e.curso)
    _require_curso_perm(request.user, 'edit', curso)
    existente = MatriculaAnual.objects.filter(estudiante=e, anio=anio).select_related('curso').first()
    if existente:
        _require_curso_perm(request.user, 'edit', existente.curso)

    request.audit_objeto = f'Estudiante #{e.pk} · {anio}'
    if e.estado == 'Retirado' or (existente and existente.estado == 'Retirado'):
        return JsonResponse({'ok': False, 'error': 'Registra la reincorporación desde el Historial escolar antes de cambiar el curso.'}, status=400)

    # Evita duplicados y asigna
    ma, _ = MatriculaAnual.objects.update_or_create(
        estudiante=e, anio=anio,
        defaults={'curso': curso},
        create_defaults=dict(MatriculaAnual.defaults_desde_estudiante(e), curso=curso),
    )

    # La ficha (Estudiante.curso) sigue la misma regla forward-only del clonado de años:
    # así la ficha y el listado del año nunca quedan desincronizados.
    if e.curso_id is None or anio >= getattr(e.curso, 'anio', anio):
        if e.curso_id != curso.id:
            e.curso = curso
            e.save(update_fields=['curso'])

    request.audit_objeto = f'Estudiante #{e.pk} · {anio}'
    # Verificación inmediata
    curso_texto, curso_obj, tiene = _curso_en_anio(e, anio)
    return JsonResponse({
        'ok': True,
        'rut': e.rut,
        'anio': anio,
        'curso_id': curso_obj.id if curso_obj else None,
        'curso_texto': curso_texto,
        'tiene_matricula_en_anio': tiene
    })

@role_required(*PERMITIDOS_TODOS)
def detalles_estudiante(request, estudiante_id):
    # 1) Estudiante
    estudiante = get_object_or_404(
        Estudiante.objects.select_related('curso'),  # por si hay que caer a este FK
        id=estudiante_id
    )

    # 2) Año a mostrar: ?anio=2026 o año activo visible; si no, el más reciente que tenga el alumno
    try:
        anio_forzado = int(request.GET.get('anio')) if request.GET.get('anio') else None
    except ValueError:
        anio_forzado = None

    anio_activo = (
        AcademicYear.objects.filter(is_visible=True)
        .order_by('-anio')
        .values_list('anio', flat=True)
        .first()
    )

    # 3) Buscar MA del alumno
    ma_qs = MatriculaAnual.objects.filter(estudiante=estudiante).select_related('curso')
    ma = None
    if anio_forzado:
        ma = ma_qs.filter(anio=anio_forzado).order_by('-id').first()
    elif anio_activo:
        ma = ma_qs.filter(anio=anio_activo).order_by('-id').first()
    if not ma:
        # cae a la más reciente que tenga, si no hay para el activo/forzado
        ma = ma_qs.order_by('-anio', '-id').first()

    # 4) Curso/Año a mostrar en la ficha
    curso_view = getattr(ma, 'curso', None) or getattr(estudiante, 'curso', None)
    anio_view = getattr(curso_view, 'anio', None)

    _require_curso_perm(request.user, 'view', curso_view)
    _require_curso_perm(request.user, 'view', estudiante.curso)
    estudiante = _estudiante_en_matricula(estudiante, ma)

    from app.historial import retiro_pendiente
    from app.models import MovimientoEstudiante
    actual = Estudiante.objects.select_related('curso').get(pk=estudiante_id)
    movimientos = MovimientoEstudiante.objects.filter(estudiante_id=estudiante_id).filter(
        filtro_q_por_niveles_permitidos(request.user, 'matricula__curso__nivel')
    ).select_related('matricula__curso')
    pendiente = retiro_pendiente(actual)
    ultimo_anio = max(actual.curso.anio, ma_qs.order_by('-anio').values_list('anio', flat=True).first() or 0)
    try:
        _require_curso_perm(request.user, 'edit', actual.curso)
        _require_curso_perm(request.user, 'edit', curso_view)
        puede_movimiento = True
    except PermissionDenied:
        puede_movimiento = False

    # 5) Documentos (los de siempre)
    documentos = DocumentoEstudiante.objects.filter(estudiante=estudiante)

    ctx = {
        'estudiante': estudiante,
        'documentos': documentos,
        'ma': ma,                     # matrícula anual (puede ser None)
        'curso_view': curso_view,     # curso a mostrar (del año correcto si existe MA)
        'anio_view': anio_view,       # año que estás mostrando
        'anio_forzado': anio_forzado,
        'anio_activo': anio_activo,
        'movimientos': movimientos, 'retiro_pendiente': pendiente,
        'puede_retirar': puede_movimiento and actual.estado == 'Activo' and anio_view == ultimo_anio,
        'puede_reincorporar': puede_movimiento and pendiente is not None,
    }
    return render(request, 'app/detalles_estudiante.html', ctx)

@role_required(*PERMITIDOS_TODOS)
def descargar_documento(request, documento_id):
    documento = get_object_or_404(DocumentoEstudiante, id=documento_id)
    _require_curso_perm(request.user, 'view', documento.estudiante.curso)
    request.audit_objeto = f'Documento #{documento.pk} · Estudiante #{documento.estudiante_id}'
    response = FileResponse(documento.archivo, as_attachment=True, filename=documento.nombre)
    return response


@login_required
@role_required(*PERMITIDOS_TODOS)  # misma puerta que listar_estudiantes
def listar_estudiantes_anio(request, anio=None):
    if not niveles_permitidos_por_usuario(request.user, action='view'):
        raise PermissionDenied('No tienes permisos para consultar estudiantes.')
    # Año seleccionado (GET ?anio=)
    try:
        anio = int(anio or request.GET.get('anio', ''))
    except ValueError:
        anio = 0

    # Años disponibles (según MatriculaAnual)
    years = list(
        MatriculaAnual.objects.filter(
            filtro_q_por_niveles_permitidos(request.user, 'curso__nivel')
        ).order_by('-anio')
        .values_list('anio', flat=True).distinct()
    )
    if not years:
        years = [timezone.now().year]
    if not anio:
        anio = years[0]

    if not is_admin(request.user):
        visibles = set(AcademicYear.objects.filter(is_visible=True).values_list('anio', flat=True))
        years = [year for year in years if year in visibles]
        if anio not in years:
            raise PermissionDenied('No tienes acceso a este año académico.')

    # === Asegurar que existan los placeholders "Por Asignar" y "Egresados" para el año ===
    # (si ya existen, no hace nada)
    Curso.objects.get_or_create(anio=anio, nivel=NIVEL_POR_ASIGNAR, letra=None)
    Curso.objects.get_or_create(anio=anio, nivel=NIVEL_EGRESADOS,   letra=None)

    # Filtros
    q = (request.GET.get('q') or '').strip()
    curso_id = (request.GET.get('curso') or '').strip()

    # Catálogo de cursos del año + conteo
    # Ordenamos con "Por Asignar" primero, luego todos los normales por nivel/letra, y "Egresados" al final.
    cursos = (
        Curso.objects.filter(anio=anio).filter(
            filtro_q_por_niveles_permitidos(request.user, 'nivel')
        )
        .annotate(
            n_est=Count('matriculas', filter=Q(matriculas__anio=anio)),
            ord_especial=Case(
                When(nivel=NIVEL_POR_ASIGNAR, then=Value(0)),
                When(nivel=NIVEL_EGRESADOS,   then=Value(2)),
                default=Value(1),
                output_field=IntegerField(),
            )
        )
        .order_by('ord_especial', 'nivel', 'letra')
    )

    # Conteo de alumnos "Sin nivel" (curso NULL en MA)
    sin_nivel_count = MatriculaAnual.objects.filter(anio=anio, curso__isnull=True).filter(
        filtro_q_por_niveles_permitidos(request.user, 'curso__nivel')
    ).count()

    # Query base por año
    qs = (
        MatriculaAnual.objects.filter(anio=anio)
        .filter(filtro_q_por_niveles_permitidos(request.user, 'curso__nivel'))
        .select_related('estudiante', 'curso')
        .order_by('curso__nivel', 'curso__letra', 'estudiante__nombre_completo')
    )

    # Filtrar por curso (incluye la opción especial 'none' para "Sin nivel")
    if curso_id:
        if curso_id == 'none':
            qs = qs.filter(curso__isnull=True)
        else:
            try:
                qs = qs.filter(curso_id=int(curso_id))
            except ValueError:
                pass

    estado_filtro = request.GET.get('estado', '')
    if estado_filtro in dict(Estudiante._meta.get_field('estado').choices):
        qs = qs.filter(estado=estado_filtro)
    else:
        estado_filtro = ''

    # Búsqueda libre
    if q:
        qs = qs.filter(
            Q(estudiante__nombre_completo__icontains=q) |
            Q(estudiante__rut__icontains=q) |
            Q(curso__nivel__icontains=q) |
            Q(curso__letra__icontains=q)
        )

    ctx = {
        'estado_filtro': estado_filtro,
        'anio': anio,
        'years': years,
        'cursos': cursos,              # incluye Por Asignar y Egresados (creados si faltaban)
        'curso_id': curso_id,          # para marcar seleccionado en el select
        'curso_seleccionado': None,    # si lo usas en Alpine
        'q': q,
        'sin_nivel_count': sin_nivel_count,  # para la opción "Sin nivel" (id = 'none')
    }
    mats = list(qs)
    prov_rows = [m for m in mats if _es_rut_provisional(m.estudiante.rut)]
    prov_filter = bool((request.GET.get('provisionales') or '').strip())
    mats = prov_rows if prov_filter else mats

    # Paginación: 50 por página para que la tabla siga ágil con el año completo
    from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
    paginator = Paginator(mats, 50)
    try:
        page_obj = paginator.page(request.GET.get('page') or 1)
    except (PageNotAnInteger, EmptyPage):
        page_obj = paginator.page(1)

    params = request.GET.copy()
    params.pop('page', None)

    ctx.update({
        'total_count': len(mats),
        'prov_count': len(prov_rows),
        'prov_filter': prov_filter,
        'prov_ids': {m.pk for m in prov_rows},
        'page_obj': page_obj,
        'params': params.urlencode(),
        'matriculas': page_obj,
    })
    return render(request, 'estudiantes/listar_estudiantes_anio.html', ctx)


def _es_rut_provisional(rut):
    """RUT provisional Registro Civil (extranjeros): cuerpo 9.0M–9.9M o ≥100M."""
    n = _norm_rut(rut)
    if not n.isdigit() or len(n) < 2:
        return False
    cuerpo = int(n[:-1])
    return 9_000_000 <= cuerpo <= 9_999_999 or cuerpo >= 100_000_000


# ======================================================================
# RUT provisional → definitivo (estudiantes extranjeros)
# ======================================================================
def _dv_rut(cuerpo):
    """Dígito verificador módulo 11 de la parte numérica del RUN."""
    suma, factor = 0, 2
    for d in reversed(cuerpo):
        suma += int(d) * factor
        factor = factor + 1 if factor < 7 else 2
    r = 11 - (suma % 11)
    return '0' if r == 11 else ('K' if r == 10 else str(r))


@require_POST
@role_required(DIRECTIVO)
@transaction.atomic
def cambiar_rut_estudiante(request, estudiante_id):
    """Cambia el RUT del estudiante (p. ej. provisional de extranjero → definitivo),
    validando el DV del nuevo RUN y bloqueando colisiones con otros estudiantes."""
    est = get_object_or_404(Estudiante, id=estudiante_id)
    _require_curso_perm(request.user, 'edit', est.curso)

    nuevo = _norm_rut(request.POST.get('rut_nuevo') or '')
    if not nuevo or not nuevo.isdigit() or len(nuevo) < 8:
        messages.error(request, "Ingresa el RUN definitivo completo (cuerpo + dígito verificador).")
        return redirect('detalles_estudiante', estudiante_id=est.id)

    cuerpo, dv = nuevo[:-1], nuevo[-1]
    dv_ok = _dv_rut(cuerpo)
    if dv != dv_ok:
        messages.error(request, f"El dígito verificador no cuadra: {cuerpo}-{dv} debería terminar en {dv_ok}. Revisa el RUT.")
        return redirect('detalles_estudiante', estudiante_id=est.id)

    if nuevo == _norm_rut(est.rut):
        messages.info(request, "El RUT ingresado es igual al actual; no se realizó ningún cambio.")
        return redirect('detalles_estudiante', estudiante_id=est.id)

    otro = (Estudiante.objects
            .filter(Q(rut=nuevo) | Q(rut=f"{cuerpo}-{dv}"))
            .exclude(pk=est.pk)
            .first())
    if otro:
        messages.error(request, f"No se actualizó: el RUT {nuevo} ya pertenece a {otro.nombre_completo} ({otro.rut}).")
        return redirect('detalles_estudiante', estudiante_id=est.id)

    if request.user.is_authenticated:
        full_name = f"{getattr(request.user, 'nombre', '')} {getattr(request.user, 'apellido', '')}".strip()
        quien = full_name or request.user.get_username()
    else:
        quien = "No autenticado"

    est.rut_anterior = est.rut
    est.rut_actualizado_en = timezone.now()
    est.rut_actualizado_por = quien
    est.rut = nuevo
    est.save(update_fields=['rut', 'rut_anterior', 'rut_actualizado_en', 'rut_actualizado_por'])

    messages.success(
        request,
        f"RUT actualizado correctamente: {est.rut_anterior} → {nuevo} (registrado por {quien})."
    )
    return redirect('detalles_estudiante', estudiante_id=est.id)


@require_GET
@role_required(*PERMITIDOS_TODOS)
def busqueda_global(request):
    """Resultados rápidos para el buscador del navbar (por nombre o RUT)."""
    q = (request.GET.get('q') or '').strip()
    if len(q) < 3:
        return JsonResponse({'resultados': []})
    qs = (Estudiante.objects.select_related('curso')
          .filter(filtro_q_por_niveles_permitidos(request.user, 'curso__nivel'))
          .filter(Q(nombre_completo__icontains=q) | Q(rut__icontains=_norm_rut(q)))
          .order_by('nombre_completo')[:8])
    return JsonResponse({'resultados': [
        {'id': e.id, 'rut': e.rut, 'nombre_completo': e.nombre_completo,
         'curso': str(e.curso) if e.curso else '', 'estado': e.estado or ''}
        for e in qs
    ]})

