"""Panel de configuración (matrícula, firmas, cursos, años, toggles)."""
from ._common import *  # noqa: F401,F403


@login_required
@require_POST
@role_required(ADMIN)
def toggle_registro_apoderado(request):
    # Asegura única fila (opción 2)
    cfg, _ = ConfiguracionSistema.objects.get_or_create(
        id=1, defaults={'registro_apoderado_habilitado': False}
    )
    cfg.registro_apoderado_habilitado = not bool(cfg.registro_apoderado_habilitado)
    cfg.save(update_fields=['registro_apoderado_habilitado'])

    estado = "habilitado" if cfg.registro_apoderado_habilitado else "deshabilitado"
    messages.success(request, f"Registro de apoderados {estado}.")
    return redirect('panel_config_documentos')

@role_required(ADMIN)
@login_required
def panel_config_documentos(request):
    """
    Entrega TODO lo que tu template necesita e incorpora acciones del Panel:
    - forms: form_m, form_f, form_s
    - cursos
    - anios_disponibles (AcademicYear)
    - usuarios, ROLES
    - firma_url, titulo_preview, registro_habilitado
    - q (búsqueda)
    - firmas (listado para el panel)
    - POST:
        * 'matricula'                 -> guardar Configuración de Matrícula
        * 'sistema_update'            -> actualizar preferencias (registro apoderado)
        * 'firma'                     -> crear nueva firma (exclusiva si activa=True)
        * 'firma_toggle'              -> activar firma existente (exclusiva)
        * 'firma_delete'              -> eliminar firma
        * 'curso_create'              -> crear curso
        * 'curso_delete'              -> eliminar curso
        * 'ay_create'                 -> crear año académico
        * 'ay_toggle_visible'         -> alternar visibilidad año académico
        * 'ay_delete'                 -> eliminar año académico
        * 'ay_set_active'             -> fijar año activo en Configuración de Matrícula
    """
    q = (request.GET.get('q') or '').strip()

    # ===== Configuración de Ficha de Matrícula =====
    cfg_m, _ = ConfiguracionMatricula.objects.get_or_create(
        pk=1,
        defaults={
            'titulo': 'Ficha Matrícula {anio}',
            'anio': timezone.now().year,
            'activa': True,
        }
    )

    # ===== Configuración del Sistema =====
    cfg_s, _ = ConfiguracionSistema.objects.get_or_create(pk=1, defaults={})

    # ===== Manejo de POST (acciones del panel) =====
    if request.method == 'POST':
        form_type = (request.POST.get('form_type') or '').strip()

        # --- Guardar Configuración de Matrícula ---
        if form_type == 'matricula':
            form_m_post = ConfiguracionMatriculaForm(request.POST, instance=cfg_m)
            if form_m_post.is_valid():
                form_m_post.save()
                messages.success(request, "Configuración de matrícula guardada correctamente.")
            else:
                messages.error(request, "Revisa el formulario de matrícula.")
            return redirect('panel_config_documentos')

        # --- Actualizar preferencias del sistema (registro apoderado) ---
        if form_type == 'sistema_update':
            val = (request.POST.get('registro_apoderado_habilitado') or '').lower() in ('1', 'true', 'on', 'si', 'sí')
            cfg_s.registro_apoderado_habilitado = val
            cfg_s.save(update_fields=['registro_apoderado_habilitado'])
            messages.success(request, f"Registro de apoderados {'habilitado' if val else 'deshabilitado'}.")
            return redirect('panel_config_documentos')

        # --- Crear/Subir nueva firma ---
        if form_type == 'firma':
            form_f_post = ConfiguracionFirmaForm(request.POST, request.FILES)
            if form_f_post.is_valid():
                nueva = form_f_post.save(commit=False)
                nueva.save()
                if getattr(nueva, 'activa', False):
                    ConfiguracionFirma.objects.exclude(pk=nueva.pk).update(activa=False)
                messages.success(request, "Firma guardada correctamente.")
            else:
                messages.error(request, "Revisa el formulario de firma.")
            return redirect('panel_config_documentos')

        # --- Activar una firma existente (exclusiva) ---
        if form_type == 'firma_toggle':
            activar_id = (request.POST.get('activar_id') or '').strip()
            if activar_id.isdigit():
                try:
                    with transaction.atomic():
                        ConfiguracionFirma.objects.update(activa=False)
                        f = ConfiguracionFirma.objects.select_for_update().get(pk=int(activar_id))
                        f.activa = True
                        f.save(update_fields=['activa'])
                    messages.success(request, "Firma activada correctamente.")
                except ConfiguracionFirma.DoesNotExist:
                    messages.error(request, "La firma seleccionada no existe.")
            else:
                messages.error(request, "ID de firma inválido.")
            return redirect('panel_config_documentos')

        # --- Eliminar una firma ---
        if form_type == 'firma_delete':
            eliminar_id = (request.POST.get('eliminar_id') or '').strip()
            if eliminar_id.isdigit():
                try:
                    f = ConfiguracionFirma.objects.get(pk=int(eliminar_id))
                    was_active = bool(f.activa)
                    f.delete()
                    if was_active:
                        messages.info(request, "Se eliminó la firma activa. No hay firma activa actualmente.")
                    else:
                        messages.success(request, "Firma eliminada.")
                except ConfiguracionFirma.DoesNotExist:
                    messages.error(request, "La firma a eliminar no existe.")
            else:
                messages.error(request, "ID de firma inválido.")
            return redirect('panel_config_documentos')

        # --- Crear un curso ---
        if form_type == 'curso_create':
            nivel = request.POST.get('nivel', '').strip()
            letra = request.POST.get('letra', '').strip()
            anio = request.POST.get('anio', '').strip()

            if not nivel or not anio:
                messages.error(request, "Debe indicar al menos el nivel y el año del curso.")
                return redirect('panel_config_documentos')

            try:
                anio_int = int(anio)
                curso, created = Curso.objects.get_or_create(
                    nivel=nivel,
                    letra=letra or None,
                    anio=anio_int
                )
                if created:
                    messages.success(request, f"Curso {nivel} {letra or ''} ({anio_int}) creado correctamente.")
                else:
                    messages.info(request, f"El curso {nivel} {letra or ''} ({anio_int}) ya existía.")
            except ValueError:
                messages.error(request, "El año ingresado no es válido.")

            return redirect('panel_config_documentos')

        # --- Eliminar un curso ---
        if form_type == 'curso_delete':
            curso_id = request.POST.get('curso_id')
            if curso_id and curso_id.isdigit():
                try:
                    curso = Curso.objects.get(pk=int(curso_id))
                    try:
                        curso.delete()
                        messages.success(request, f"Curso {curso} eliminado correctamente.")
                    except ProtectedError:
                        messages.error(
                            request,
                            f"No se puede eliminar el curso {curso} porque tiene matrículas asociadas."
                        )
                except Curso.DoesNotExist:
                    messages.error(request, "El curso seleccionado no existe.")
            else:
                messages.error(request, "ID de curso inválido.")
            return redirect('panel_config_documentos')

        # =========================
        #  ACADEMIC YEAR - NUEVO
        # =========================

        # --- Crear año académico ---
        if form_type == 'ay_create':
            anio_str = (request.POST.get('anio') or '').strip()
            visible = (request.POST.get('is_visible') or '').lower() in ('1', 'true', 'on', 'si', 'sí')

            try:
                anio_int = int(anio_str)
            except ValueError:
                messages.error(request, "El año ingresado no es válido.")
                return redirect('panel_config_documentos')

            ay, created = AcademicYear.objects.get_or_create(
                anio=anio_int,
                defaults={'is_visible': visible}
            )
            if not created:
                if ay.is_visible != visible:
                    ay.is_visible = visible
                    ay.save(update_fields=['is_visible'])
                messages.info(request, f"El año {anio_int} ya existía. Visibilidad: {'visible' if ay.is_visible else 'oculto'}.")
            else:
                messages.success(request, f"Año académico {anio_int} creado ({'visible' if visible else 'oculto'}).")
            return redirect('panel_config_documentos')

        # --- Alternar visibilidad de un año académico ---
        if form_type == 'ay_toggle_visible':
            ay_id = (request.POST.get('ay_id') or '').strip()
            if ay_id.isdigit():
                try:
                    ay = AcademicYear.objects.get(pk=int(ay_id))
                    ay.is_visible = not ay.is_visible
                    ay.save(update_fields=['is_visible'])
                    messages.success(request, f"Año {ay.anio} ahora está {'visible' if ay.is_visible else 'oculto'}.")
                except AcademicYear.DoesNotExist:
                    messages.error(request, "El año académico indicado no existe.")
            else:
                messages.error(request, "ID de año académico inválido.")
            return redirect('panel_config_documentos')

        # --- Eliminar año académico ---
        if form_type == 'ay_delete':
            ay_id = (request.POST.get('ay_id') or '').strip()
            if ay_id.isdigit():
                try:
                    ay = AcademicYear.objects.get(pk=int(ay_id))
                    anio_lbl = ay.anio
                    ay.delete()
                    messages.success(request, f"Año {anio_lbl} eliminado.")
                except AcademicYear.DoesNotExist:
                    messages.error(request, "El año académico indicado no existe.")
                except ProtectedError:
                    messages.error(request, "No se puede eliminar: el año tiene datos asociados.")
            else:
                messages.error(request, "ID de año académico inválido.")
            return redirect('panel_config_documentos')

        # --- Fijar año activo en Configuración de Matrícula ---
        if form_type == 'ay_set_active':
            anio_str = (request.POST.get('anio') or '').strip()
            try:
                anio_int = int(anio_str)
            except ValueError:
                messages.error(request, "Año inválido.")
                return redirect('panel_config_documentos')

            if not AcademicYear.objects.filter(anio=anio_int).exists():
                messages.error(request, f"El año {anio_int} no existe en AcademicYear.")
                return redirect('panel_config_documentos')

            cfg_m.anio = anio_int
            cfg_m.save(update_fields=['anio'])
            messages.success(request, f"Año activo de matrícula actualizado a {anio_int}.")
            return redirect('panel_config_documentos')

    # ===== Formularios (GET) =====
    form_m = ConfiguracionMatriculaForm(instance=cfg_m)
    form_s = ConfiguracionSistemaForm(instance=cfg_s)
    registro_habilitado = bool(getattr(cfg_s, 'registro_apoderado_habilitado', False))

    # ===== Firma activa (preview) =====
    firma_activa = ConfiguracionFirma.objects.filter(activa=True).order_by('-id').first()
    form_f = ConfiguracionFirmaForm()  # para subir nueva
    if firma_activa and getattr(firma_activa, 'imagen_firma', None):
        try:
            firma_url = firma_activa.imagen_firma.url
        except Exception:
            firma_url = None
    else:
        firma_url = None

    # ===== Cursos =====
    cursos = Curso.objects.all().order_by('anio', 'nivel', 'letra')

    # ===== Años académicos (visibilidad) =====
    anios_disponibles = AcademicYear.objects.all().order_by('-anio')
    anios_visibles = AcademicYear.objects.filter(is_visible=True).order_by('anio').values_list('anio', flat=True)

    # ===== Usuarios / Roles =====
    ROLES = getattr(Usuarios, 'ROLES', {
        'ADMIN': 'Administrador',
        'DIRECTIVO': 'Directivo',
        'PROFESOR': 'Profesor',
        'ASISTENTE': 'Asistente',
    })
    usuarios_qs = Usuarios.objects.all()
    if q:
        usuarios_qs = usuarios_qs.filter(
            Q(nombre__icontains=q) |
            Q(apellido__icontains=q) |
            Q(run__icontains=q) |
            Q(email__icontains=q)
        )
    usuarios = usuarios_qs.order_by('apellido', 'nombre')

    # ===== Vista previa del título con {anio} =====
    anio_preview = getattr(cfg_m, 'anio', timezone.now().year) or timezone.now().year
    titulo_template = getattr(cfg_m, 'titulo', '') or 'Ficha Matrícula {anio}'
    titulo_preview = titulo_template.replace('{anio}', str(anio_preview))

    # ===== Listado de firmas para el panel =====
    firmas = ConfiguracionFirma.objects.all().order_by('-activa', '-id')

    # Render
    return render(request, 'app/panel_config_documentos.html', {
        # Filtros / búsqueda
        'q': q,

        # Años académicos (panel de visibilidad)
        'anios_disponibles': anios_disponibles,
        'anios_visibles': list(anios_visibles),

        # Cursos (gestión de cursos)
        'cursos': cursos,

        # Formularios
        'form_m': form_m,
        'form_f': form_f,
        'form_s': form_s,

        # Previews y estados
        'titulo_preview': titulo_preview,
        'anio_actual': anio_preview,   # <-- para el template
        'firma_url': firma_url,
        'registro_habilitado': registro_habilitado,

        # Usuarios / Roles
        'usuarios': usuarios,
        'ROLES': ROLES,

        # Panel de firmas
        'firmas': firmas,
    })

@login_required
@user_passes_test(is_admin)
def panel_anios(request):
    years = AcademicYear.objects.all().order_by('-anio')
    return render(request, 'panel/panel_anios.html', {'years': years})

@login_required
@user_passes_test(is_admin)
@require_POST
def api_year_create(request):
    anio_str = (request.POST.get('anio') or '').strip()
    if not anio_str.isdigit():
        return JsonResponse({'ok': False, 'error': 'Año inválido'}, status=400)
    anio = int(anio_str)
    vis = request.POST.get('is_visible') == 'true'
    obj, _ = AcademicYear.objects.get_or_create(anio=anio, defaults={'is_visible': vis})
    obj.is_visible = vis
    obj.save(update_fields=['is_visible'])
    return JsonResponse({'ok': True, 'anio': obj.anio, 'is_visible': obj.is_visible})

@login_required
def toggle_year_visibility(request, year_id):
    year = get_object_or_404(AcademicYear, id=year_id)
    if request.method == 'POST':
        year.is_visible = not year.is_visible
        year.save()
    return redirect('panel_config_documentos')

@login_required
def agregar_anio_academico(request):
    if request.method == 'POST':
        anio_str = request.POST.get('anio')
        if anio_str and anio_str.isdigit():
            anio = int(anio_str)
            obj, created = AcademicYear.objects.get_or_create(anio=anio)
            msg = f"Año {anio} agregado correctamente." if created else f"El año {anio} ya existía."
            messages.success(request, msg)
    return redirect('panel_config_documentos')

