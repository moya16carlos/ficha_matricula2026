"""Flujo público de apoderado: registro y matriculación de alumnos."""
from ._common import *  # noqa: F401,F403
from app.forms import ApoderadoRegistroForm


@transaction.atomic
def registro_alumno_view(request):
    """
    Permite a un apoderado registrar varios estudiantes.
    AHORA incluye: fecha_nacimiento_apoderado y fecha_nacimiento_apoderado_suplente (opcionales).
    """
    if not ConfiguracionSistema.objects.filter(pk=1, registro_apoderado_habilitado=True).exists():
        raise PermissionDenied('El registro de apoderados está deshabilitado.')
    # 0) Apoderado desde sesión
    apoderado_id = request.session.get('apoderado_id')
    apoderado = Apoderado.objects.filter(pk=apoderado_id, is_active=True).first()
    if not apoderado:
        return redirect('registro_apoderado')

    # 1) Años visibles
    anios_visibles = list(
        AcademicYear.objects.filter(is_visible=True)
        .order_by('anio').values_list('anio', flat=True)
    )

    # 2) Año seleccionado
    raw_anio_get = (request.GET.get('anio') or '').strip()
    raw_anio_post = (request.POST.get('anio_filtro') or '').strip()

    anio_sel = None
    for v in (raw_anio_get, raw_anio_post):
        if v.isdigit():
            anio_sel = int(v)
            break

    if anio_sel is None:
        cfg = ConfiguracionSistema.objects.first()
        if cfg and getattr(cfg, 'anio_activo', None):
            try:
                anio_sel = int(cfg.anio_activo)
            except Exception:
                anio_sel = None

    if anio_sel is None and anios_visibles:
        anio_sel = int(anios_visibles[-1])
    if anio_sel not in anios_visibles:
        return HttpResponse('El año seleccionado no está habilitado.', status=400)

    # 3) Cursos del año seleccionado
    cursos = Curso.objects.none()
    if anio_sel is not None:
        cursos = Curso.objects.filter(anio=anio_sel).order_by('nivel', 'letra')

    fecha_actual = date.today().isoformat()

    # (Opcional) Estudiantes ya registrados por este apoderado
    registrados = Estudiante.objects.none()
    if apoderado:
        registrados = Estudiante.objects.filter(rut_apoderado=apoderado.run).order_by('-id')

    # ===================== GET =====================
    if request.method == 'GET':
        quien_matricula_ui = None
        if apoderado:
            nm = f"{(apoderado.nombre or '').strip()} {(apoderado.apellido or '').strip()}".strip()
            quien_matricula_ui = nm or apoderado.run or apoderado.email

        ctx = {
            'anios_visibles': anios_visibles,
            'anio_sel': anio_sel,
            'cursos': cursos,
            'fecha_actual': fecha_actual,
            'registrados': registrados,
            'quien_matricula': quien_matricula_ui,
        }
        if apoderado:
            ctx.update({
                'nombre_apoderado': f"{apoderado.nombre} {apoderado.apellido}",
                'rut_apoderado': apoderado.run,
                'email_apoderado': apoderado.email,
            })
        return render(request, 'app/registro_alumno.html', ctx)

    # ===================== POST =====================
    d = request.POST  # <-- IMPORTANTE: definir 'd' ANTES de usar d.get(...)
    errores = []

    # Acción pedida por los botones
    submit_action = (d.get('submit_action') or '').strip()  # 'save_new' | 'save_finish' | ''
    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'

    # Normaliza RUT y campos base
    rut = _norm_run(d.get('rut'))
    nombre_completo = (d.get('nombre_completo') or '').strip()
    curso_id = (d.get('curso') or '').strip()
    curso = Curso.objects.filter(id=curso_id).first()

    # Validar curso y año
    if not curso:
        errores.append("Debe seleccionar un curso válido.")
    elif anio_sel is not None and curso.anio != anio_sel:
        errores.append(f"El curso seleccionado pertenece al año {curso.anio}, pero el año visible seleccionado es {anio_sel}.")

    # Fechas del estudiante
    fecha_nacimiento = _parse_date(d.get('fecha_nacimiento'))
    fecha_matricula  = _parse_date(d.get('fecha_matricula'))

    # >>> NUEVO: Fechas de nacimiento de apoderados (opcionales)
    fecha_nac_apoderado = _parse_date(d.get('fecha_nacimiento_apoderado')) if d.get('fecha_nacimiento_apoderado') else None

    sexo = (d.get('sexo') or '').strip()
    nacionalidad = (d.get('nacionalidad') or '').strip()
    domicilio = (d.get('domicilio') or '').strip()
    comuna = (d.get('comuna') or '').strip()
    de_donde_viene = (d.get('de_donde_viene') or '').strip()
    sistema_salud = (d.get('sistema_salud') or '').strip()

    if not rut:
        errores.append("El RUT del estudiante es obligatorio.")
    if not nombre_completo:
        errores.append("El nombre completo es obligatorio.")
    if not fecha_nacimiento:
        errores.append("Debe ingresar una fecha de nacimiento válida (DD-MM-YYYY o YYYY-MM-DD).")
    if not fecha_matricula:
        errores.append("Debe ingresar una fecha de matrícula válida (DD-MM-YYYY o YYYY-MM-DD).")
    if sistema_salud and sistema_salud not in ('Fonasa', 'Isapre', 'Particular'):
        errores.append("Sistema de salud inválido (permitido: Fonasa, Isapre o Particular).")

    if errores:
        if is_ajax:
            return JsonResponse({'ok': False, 'errors': errores}, status=400)
        for e in errores:
            messages.error(request, e)
        ctx = {
            'anios_visibles': anios_visibles,
            'anio_sel': anio_sel,
            'cursos': cursos,
            'fecha_actual': fecha_actual,
            'registrados': registrados,
        }
        if apoderado:
            ctx.update({
                'nombre_apoderado': f"{apoderado.nombre} {apoderado.apellido}",
                'rut_apoderado': apoderado.run,
                'email_apoderado': apoderado.email,
            })
        return render(request, 'app/registro_alumno.html', ctx, status=400)

    # Booleans
    ascendencia_indigena    = _to_bool(d.get('ascendencia_indigena'))
    pertenece_al_pie        = _to_bool(d.get('pertenece_al_pie'))
    ha_repetido_curso       = _to_bool(d.get('ha_repetido_curso'))
    resolucion_judicial     = _to_bool(d.get('resolucion_judicial'))
    problemas_salud         = _to_bool(d.get('problemas_salud'))
    impide_educacion_fisica = _to_bool(d.get('impide_educacion_fisica'))
    opta_religion           = _to_bool(d.get('opta_religion'))
    acepta_fotografias      = _to_bool(d.get('acepta_fotografias'))
    certificado_medico      = _to_bool(d.get('certificado_medico'))

    # Datos padres (opcionales)
    nombre_madre  = (d.get('nombre_madre') or '').strip() or None
    rut_madre     = (d.get('rut_madre') or '').strip() or None
    telefono_madre= (d.get('telefono_madre') or '').strip() or None
    nivel_educacional_madre = (d.get('nivel_educacional_madre') or '').strip() or None
    ocupacion_madre = (d.get('ocupacion_madre') or '').strip() or None
    email_madre   = (d.get('email_madre') or '').strip() or None

    nombre_padre  = (d.get('nombre_padre') or '').strip() or None
    rut_padre     = (d.get('rut_padre') or '').strip() or None
    telefono_padre= (d.get('telefono_padre') or '').strip() or None
    nivel_educacional_padre = (d.get('nivel_educacional_padre') or '').strip() or None
    ocupacion_padre = (d.get('ocupacion_padre') or '').strip() or None
    email_padre   = (d.get('email_padre') or '').strip() or None

    # Apoderado suplente
    nombre_apod_sup = (d.get('nombre_apoderado_suplente') or '').strip() or None
    rut_apod_sup    = (d.get('rut_apoderado_suplente') or '').strip() or None
    tel_apod_sup    = (d.get('telefono_apoderado_suplente') or '').strip() or None
    niv_apod_sup    = (d.get('nivel_educacional_apoderado_suplente') or '').strip() or None
    ocup_apod_sup   = (d.get('ocupacion_apoderado_suplente') or '').strip() or None
    email_apod_sup  = (d.get('email_apoderado_suplente') or '').strip() or None
    # >>> NUEVO: Parentesco suplente
    parentesco_apod_sup = (d.get('parentesco_apoderado_suplente') or '').strip() or None

    # >>> NUEVO: Seguro complementario (texto)
    seguro_complementario = (d.get('seguro_salud_complementario') or '').strip() or None

    # === "quién matricula"
    quien_matricula = None
    if apoderado:
        nombre_ap = f"{(apoderado.nombre or '').strip()} {(apoderado.apellido or '').strip()}".strip()
        quien_matricula = nombre_ap or apoderado.run or apoderado.email

    # (Opcional) FK usuario interno
    # matriculado_por = request.user if getattr(request.user, 'is_authenticated', False) else None

    # Construir Estudiante
    est = Estudiante(
        rut=rut,
        nombre_completo=nombre_completo,
        curso=curso,
        fecha_nacimiento=fecha_nacimiento,
        sexo=sexo,
        nacionalidad=nacionalidad,
        domicilio=domicilio,
        comuna=comuna,
        ascendencia_indigena=ascendencia_indigena,
        etnia=(d.get('etnia') or '').strip() or None,
        pertenece_al_pie=pertenece_al_pie,
        ha_repetido_curso=ha_repetido_curso,
        estado=(d.get('estado') or 'Activo'),
        de_donde_viene=de_donde_viene,

        # Madre/Padre
        nombre_madre=nombre_madre, rut_madre=rut_madre, telefono_madre=telefono_madre,
        nivel_educacional_madre=nivel_educacional_madre, ocupacion_madre=ocupacion_madre, email_madre=email_madre,
        nombre_padre=nombre_padre, rut_padre=rut_padre, telefono_padre=telefono_padre,
        nivel_educacional_padre=nivel_educacional_padre, ocupacion_padre=ocupacion_padre, email_padre=email_padre,

        # Apoderado titular (desde sesión si existe)
        nombre_apoderado=(f"{apoderado.nombre} {apoderado.apellido}" if apoderado else (d.get('nombre_apoderado') or '').strip() or None),
        rut_apoderado=(apoderado.run if apoderado else (d.get('rut_apoderado') or '').strip() or None),
        email_apoderado=(apoderado.email if apoderado else (d.get('email_apoderado') or '').strip() or None),
        telefono_apoderado=(d.get('telefono_apoderado') or '').strip(),
        nivel_educacional_apoderado=(d.get('nivel_educacional_apoderado') or '').strip(),
        ocupacion_apoderado=(d.get('ocupacion_apoderado') or '').strip(),
        # >>> NUEVO:
        fecha_nacimiento_apoderado=fecha_nac_apoderado,

        # Apoderado suplente
        nombre_apoderado_suplente=nombre_apod_sup,
        rut_apoderado_suplente=rut_apod_sup,
        telefono_apoderado_suplente=tel_apod_sup,
        nivel_educacional_apoderado_suplente=niv_apod_sup,
        ocupacion_apoderado_suplente=ocup_apod_sup,
        email_apoderado_suplente=email_apod_sup,
        # >>> NUEVO:
        parentesco_apoderado_suplente=parentesco_apod_sup,

        # Sociales / Salud
        vive_con=(d.get('vive_con') or 'Ambos Padres'),
        resolucion_judicial=resolucion_judicial,
        problemas_salud=problemas_salud,
        descripcion_problemas_salud=(d.get('descripcion_problemas_salud') or '').strip(),
        impide_educacion_fisica=impide_educacion_fisica,
        sistema_salud=sistema_salud,
        # >>> NUEVO: seguro complementario
        seguro_salud_complementario=seguro_complementario,

        # Otros
        tipo_transporte=(d.get('tipo_transporte') or '').strip(),
        opta_religion=opta_religion,
        acepta_fotografias=acepta_fotografias,
        certificado_medico=certificado_medico,
        fecha_matricula=fecha_matricula,

        # Auditoría
        quien_matricula=quien_matricula or None,
        # matriculado_por=matriculado_por,
    )

    # Guardar + MatriculaAnual
    try:
        est.full_clean()
        est.save()

        MatriculaAnual.objects.update_or_create(
            anio=curso.anio,
            estudiante=est,
            defaults=MatriculaAnual.defaults_desde_estudiante(est)
        )

        if is_ajax:
            keep_year = (submit_action == 'save_new')
            return JsonResponse({
                'ok': True,
                'id': est.id,
                'anio_sel': anio_sel,
                'reset_only_student_fields': keep_year,
                'quien_matricula': quien_matricula,
            }, status=201)

        if submit_action == 'save_new':
            messages.success(request, "Alumno registrado correctamente. Puedes registrar otro.")
            registrados = Estudiante.objects.filter(rut_apoderado=apoderado.run).order_by('-id') if apoderado else Estudiante.objects.none()
            ctx = {
                'anios_visibles': anios_visibles,
                'anio_sel': anio_sel,
                'cursos': cursos,
                'fecha_actual': fecha_actual,
                'registrados': registrados,
                'quien_matricula': quien_matricula,
            }
            if apoderado:
                ctx.update({
                    'nombre_apoderado': f"{apoderado.nombre} {apoderado.apellido}",
                    'rut_apoderado': apoderado.run,
                    'email_apoderado': apoderado.email,
                })
            return render(request, 'app/registro_alumno.html', ctx)

        messages.success(request, "Alumno registrado correctamente.")
        return redirect('alumno_matriculado')

    except ValidationError as e:
        msg_list = []
        for field, msgs in e.message_dict.items():
            for msg in msgs:
                msg_list.append(f"{field}: {msg}")
        if is_ajax:
            return JsonResponse({'ok': False, 'errors': msg_list}, status=400)
        for m in msg_list:
            messages.error(request, m)
        ctx = {
            'anios_visibles': anios_visibles,
            'anio_sel': anio_sel,
            'cursos': cursos,
            'fecha_actual': fecha_actual,
            'registrados': registrados,
        }
        if apoderado:
            ctx.update({
                'nombre_apoderado': f"{apoderado.nombre} {apoderado.apellido}",
                'rut_apoderado': apoderado.run,
                'email_apoderado': apoderado.email,
            })
        return render(request, 'app/registro_alumno.html', ctx, status=400)

def alumno_matriculado(request):
    return render(request, 'app/alumno_matriculado.html')

##REGISTRO DE APODERADO
def registrar_apoderado(request):
    if not ConfiguracionSistema.objects.filter(pk=1, registro_apoderado_habilitado=True).exists():
        raise PermissionDenied('El registro de apoderados está deshabilitado.')
    if request.method == 'POST':
        form = ApoderadoRegistroForm(request.POST)
        if not form.is_valid():
            return render(request, 'app/registro_apoderado.html', {'form': form}, status=400)
        apoderado = form.save()

        # ✅ Guardar el ID del apoderado en la sesión
        request.session.cycle_key()
        request.session['apoderado_id'] = apoderado.id

        # Mensaje opcional
        messages.success(request, f"Bienvenido {apoderado.nombre}, continúa con el registro del estudiante.")

        # Redirigir al formulario de alumno
        return redirect('registro_alumno')

    return render(request, 'app/registro_apoderado.html')

