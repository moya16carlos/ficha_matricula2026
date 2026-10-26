"""Gestión de usuarios del sistema, roles y permisos."""
from ._common import *  # noqa: F401,F403


@role_required(ADMIN)
@transaction.atomic
def crear_usuario_view(request):
    """
    - El PRIMER usuario con rol ADMINISTRADOR que se cree se eleva a superusuario automáticamente.
    - Solo un is_superuser puede marcar 'make_superadmin' para elevar a superusuario a otros.
    """
    # Extra: por seguridad, solo staff o ADMIN/DIRECTIVO pueden entrar (mantiene tu lógica original)
    if not (request.user.is_staff or getattr(request.user, 'rol', None) in [ADMIN, DIRECTIVO]):
        messages.error(request, "No tienes permiso para crear usuarios.")
        return redirect("home")

    # Contexto común para GET y para re-render en errores
    ctx = {
        "roles": [ADMIN, DIRECTIVO, PROFESOR, ASISTENTE],
        "can_make_superadmin": bool(getattr(request.user, "is_superuser", False)),
    }

    if request.method == "POST":
        run = _norm_run(request.POST.get('run', '').strip())
        nombre = (request.POST.get('nombre') or '').strip()
        apellido = (request.POST.get('apellido') or '').strip()
        email = (request.POST.get('email') or '').strip() or None
        password = (request.POST.get('password') or '').strip()
        rol = (request.POST.get('rol') or '').strip()

        make_superadmin_flag = str(request.POST.get('make_superadmin', '')).lower() in ('1', 'true', 'on', 'yes')

        # Validaciones mínimas
        if not run:
            messages.error(request, "El RUT es obligatorio.")
            return render(request, "app/crear_usuario.html", ctx)
        if not nombre or not apellido:
            messages.error(request, "Nombre y apellido son obligatorios.")
            return render(request, "app/crear_usuario.html", ctx)
        if not password or len(password) < 8:
            messages.error(request, "La contraseña es obligatoria y debe tener al menos 8 caracteres.")
            return render(request, "app/crear_usuario.html", ctx)
        if rol not in ROL_CHOICES:
            messages.error(request, "Rol seleccionado inválido.")
            return render(request, "app/crear_usuario.html", ctx)

        # Reglas de superadmin
        is_first_admin = (rol == ADMIN and not Usuarios.objects.filter(rol=ADMIN).exists())
        wants_superadmin = make_superadmin_flag

        make_superuser = False
        reason_msg = None

        if is_first_admin:
            make_superuser = True
            reason_msg = "Se elevó a superusuario por ser el primer ADMINISTRADOR del sistema."
        elif wants_superadmin:
            if request.user.is_superuser:
                make_superuser = True
                reason_msg = "Se elevó a superusuario por autorización de un Super Admin."
            else:
                messages.warning(request, "No tienes privilegios para otorgar superadmin; se creará sin superpermisos.")

        try:
            usuario = Usuarios.objects.create_user(
                run=run,
                nombre=nombre,
                apellido=apellido,
                password=password,
                email=email,
                rol=rol
            )

            # Staff según rol
            if rol in (ADMIN, DIRECTIVO):
                usuario.is_staff = True

            # Superusuario si corresponde
            if make_superuser:
                usuario.is_superuser = True
                usuario.is_staff = True  # coherencia con Django Admin

            usuario.save()

            messages.success(request, "Usuario creado exitosamente.")
            if reason_msg:
                messages.info(request, reason_msg)
            try:
                return redirect("listar_usuarios")
            except Exception:
                return redirect("home")

        except Exception as e:
            messages.error(request, f"Error al crear el usuario: {str(e)}")
            return render(request, "app/crear_usuario.html", ctx)

    # GET
    return render(request, "app/crear_usuario.html", ctx)


@role_required(ADMIN, DIRECTIVO)
def listar_usuarios_view(request):
    # Solo el administrador puede ver la lista completa de usuarios
    if not request.user.is_staff and request.user.rol not in ['ADMINISTRADOR', 'DIRECTIVO']:
        messages.error(request, "No tienes permiso para crear usuarios.")
        return redirect("home")
    
    usuarios = Usuarios.objects.all()
    return render(request, "app/listar_usuarios.html", {"usuarios": usuarios})

@role_required(ADMIN)
def editar_usuario(request, usuario_id):
    # Obtener el usuario a editar
    usuario = get_object_or_404(Usuarios, id_usuario=usuario_id)
    
    if request.method == "POST":
        # Usar valores por defecto vacíos para evitar errores de None
        nombre = request.POST.get('nombre', '').strip()
        apellido = request.POST.get('apellido', '').strip()
        email = request.POST.get('email', '').strip()
        rol = request.POST.get('rol', '').strip()
        password =request.POST.get('password', '')

        if nombre:
            usuario.nombre = nombre
        if apellido:
            usuario.apellido = apellido
        if email:
            usuario.email = email
        if rol:
            usuario.rol = rol
        if password:
            # SEGURIDAD: hashear la contraseña (antes se guardaba texto plano)
            usuario.set_password(password)

        # Guardar cambios en el usuario
        usuario.save()
        return redirect("perfil_usuario", usuario_id=usuario.id_usuario)
    
    # Retornar a la vista de perfil
    return render(request, "app/perfil_usuario.html", {"usuario": usuario})

@role_required(ADMIN)
def perfil_usuario_view(request, usuario_id):
    # Obtener el usuario por ID
    usuario = get_object_or_404(Usuarios, id_usuario=usuario_id)

    # Actualizar foto de perfil si se envía
    if request.method == "POST" and 'foto_perfil' in request.FILES:
        usuario.foto_perfil = request.FILES['foto_perfil']
        usuario.save()
        messages.success(request, "Foto de perfil actualizada exitosamente.")
        return redirect('perfil_usuario', usuario_id=usuario.id_usuario)
    
    # Actualizar información del usuario
    if request.method == "POST":
        nombre = request.POST.get('nombre', '').strip()
        apellido = request.POST.get('apellido', '').strip()
        email = request.POST.get('email', '').strip()
        rol = request.POST.get('rol', '').strip()
        password = request.POST.get('password', '').strip()

        if nombre:
            usuario.nombre = nombre
        if apellido:
            usuario.apellido = apellido
        if email:
            usuario.email = email
        if rol:
            usuario.rol = rol
        if password:
            usuario.set_password(password)
        
        usuario.save()
        messages.success(request, "Información del usuario actualizada exitosamente.")
        return redirect('listar_usuarios')  # fix: antes redirigía a una ruta inválida
    
    return render(request, "app/perfil_usuario.html", {"usuario": usuario})

@role_required(*PERMITIDOS_TODOS)
def listar_estudiantes_admin_view(request):
    # Solo el administrador puede acceder a esta vista
    if not request.user.is_staff and request.user.rol not in ['ADMINISTRADOR', 'DIRECTIVO']:
        messages.error(request, "No tienes permiso para crear usuarios.")
        return redirect("home")
    
    # Listar todos los estudiantes
    estudiantes = Estudiante.objects.filter(
        filtro_q_por_niveles_permitidos(request.user, 'curso__nivel')
    )

    # Filtrar los cursos para enseñanza básica y media
    cursos_basica = ['Prekinder', 'Kinder'] + [f"{i}° Básico" for i in range(1, 9)]
    cursos_media = [f"{i}° Medio" for i in range(1, 5)]

    # Renderizar la plantilla con los estudiantes y los cursos
    return render(request, "app/estudiantes_admin.html", {
        "estudiantes": estudiantes,
        "cursos_basica": cursos_basica,
        "cursos_media": cursos_media,
    })

@require_POST
@role_required('ADMINISTRADOR')
def actualizar_rol_usuario(request):
    user_id = request.POST.get('user_id')
    rol = request.POST.get('rol')

    if not user_id or not rol:
        return JsonResponse({'ok': False, 'error': 'Parámetros incompletos.'}, status=400)

    # Validar rol contra las choices del modelo
    valid_roles = dict(Usuarios.ROL_CHOICES).keys()
    if rol not in valid_roles:
        return JsonResponse({'ok': False, 'error': 'Rol inválido.'}, status=400)

    try:
        # pk apunta a id_usuario porque es primary_key=True
        u = Usuarios.objects.get(pk=user_id)
    except Usuarios.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Usuario no existe.'}, status=404)

    u.rol = rol
    u.save(update_fields=['rol'])
    return JsonResponse({'ok': True, 'rol': u.get_rol_display()})

@require_POST
@login_required
@role_required('ADMINISTRADOR')  # seguridad: solo admin puede borrar usuarios
def eliminar_usuario(request):
    user_id = request.POST.get('user_id')
    if not user_id:
        return JsonResponse({'ok': False, 'error': 'Falta el ID de usuario'}, status=400)

    if request.user.id_usuario == int(user_id):
        return JsonResponse({'ok': False, 'error': 'No puedes eliminarte a ti mismo'}, status=400)

    try:
        usuario = Usuarios.objects.get(pk=user_id)
        usuario.delete()
        return JsonResponse({'ok': True})
    except Usuarios.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Usuario no encontrado'}, status=404)
    except Exception as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=500)
    
@login_required
@role_required('ADMINISTRADOR')
@require_POST
def cambiar_password_usuario(request):
    user_id = request.POST.get('user_id')
    nueva_password = (request.POST.get('password') or '').strip()

    if not user_id or not nueva_password:
        return JsonResponse({'ok': False, 'error': 'Faltan datos'}, status=400)

    try:
        usuario = Usuarios.objects.get(pk=user_id)
        usuario.password = make_password(nueva_password)
        usuario.save(update_fields=['password'])
        return JsonResponse({'ok': True})
    except Usuarios.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Usuario no encontrado'}, status=404)
    except Exception as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=500)

def nivel_permission_required(nivel, action):
    """
    nivel: 'BASICA' | 'MEDIA'
    action: 'view' | 'add' | 'edit' | 'print'
    Admin pasa directo. Otros deben tener el permiso específico.
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            u = getattr(request, 'user', None)
            if not u or not u.is_authenticated:
                raise PermissionDenied("No autenticado")
            # Admin ve todo
            if getattr(u, 'rol', None) == 'ADMINISTRADOR':
                return view_func(request, *args, **kwargs)
            if not hasattr(u, 'has_nivel_perm') or not u.has_nivel_perm(nivel, action):
                raise PermissionDenied("No autorizado para este nivel/acción")
            return view_func(request, *args, **kwargs)
        return _wrapped
    return decorator

@require_POST
# @csrf_protect
@login_required
def actualizar_permisos_usuario(request):
    """
    Espera:
      user_id: int
      nivel: BASICA | MEDIA
      can_view, can_add, can_edit, can_print: 'true' | 'false'
    Solo ADMINISTRADOR puede actualizar.
    """
    # Solo administradores
    if getattr(request.user, 'rol', None) != 'ADMINISTRADOR':
        return JsonResponse({'ok': False, 'error': 'No autorizado'}, status=403)

    user_id = (request.POST.get('user_id') or '').strip()
    nivel = (request.POST.get('nivel') or '').strip().upper()

    if not user_id or nivel not in ('BASICA', 'MEDIA'):
        return JsonResponse({'ok': False, 'error': 'Datos incompletos o nivel inválido'}, status=400)

    # Parseo de flags booleanos
    flags = {}
    for key in ('can_view', 'can_add', 'can_edit', 'can_print'):
        val = (request.POST.get(key) or '').strip().lower()
        if val not in ('true', 'false'):
            return JsonResponse({'ok': False, 'error': f'Valor inválido para {key}'}, status=400)
        flags[key] = (val == 'true')

    # Cargar modelos
    try:
        usuario = Usuarios.objects.get(pk=user_id)
    except Usuarios.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Usuario no encontrado'}, status=404)

    # Si quieres evitar que un admin se quite a sí mismo todos los permisos de ambos niveles, puedes forzar alguna regla aquí.
    # if usuario == request.user and not any(flags.values()):
    #     return JsonResponse({'ok': False, 'error': 'No puedes quitarte todos los permisos'}, status=400)

    try:
        perm, _ = UsuarioPermiso.objects.get_or_create(usuario=usuario, nivel=nivel)
        perm.can_view  = flags['can_view']
        perm.can_add   = flags['can_add']
        perm.can_edit  = flags['can_edit']
        perm.can_print = flags['can_print']
        perm.save()
        return JsonResponse({'ok': True})
    except Exception as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=500)

