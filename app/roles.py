# app/roles.py
from functools import wraps
from django.contrib import messages
from django.db.models import Q
from django.shortcuts import redirect

# ===== Constantes de roles y niveles =====
ADMIN       = 'ADMINISTRADOR'
DIRECTIVO   = 'DIRECTIVO'
PROFESOR    = 'PROFESOR'
ASISTENTE   = 'ASISTENTE'

PERMITIDOS_TODOS = (ADMIN, DIRECTIVO, PROFESOR, ASISTENTE)

BASICA = 'BASICA'
MEDIA  = 'MEDIA'

# ===== Decorador por rol (rápido y sin dependencias cruzadas) =====
def role_required(*allowed_roles):
    """
    Restringe acceso por rol declarado en Usuarios.rol.
    - ADMINISTRADOR siempre pasa.
    - Si no está autenticado => login
    - Si el rol no está permitido => home + mensaje
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            user = getattr(request, 'user', None)
            if not getattr(user, 'is_authenticated', False):
                return redirect('login')

            user_rol = getattr(user, 'rol', None)
            if user_rol == ADMIN or user.is_superuser:
                return view_func(request, *args, **kwargs)

            if allowed_roles and user_rol not in allowed_roles:
                messages.error(request, 'No tienes permiso para acceder a esta vista.')
                return redirect('home')

            return view_func(request, *args, **kwargs)
        return _wrapped
    return decorator

# ===== Helpers de niveles =====
def _levels_for_group(nivel_group: str):
    basica = [
        'Prekinder', 'Kinder',
        '1° Básico', '2° Básico', '3° Básico', '4° Básico',
        '5° Básico', '6° Básico', '7° Básico', '8° Básico'
    ]
    media = ['1° Medio', '2° Medio', '3° Medio', '4° Medio']
    if nivel_group == BASICA:
        return basica
    if nivel_group == MEDIA:
        return media
    return []

def curso_nivel_to_group(nivel_text: str) -> str | None:
    """
    Mapea el texto de Curso.nivel (p. ej. "1° Medio") a BASICA o MEDIA.
    """
    if nivel_text in MEDIA_LEVELS:
        return MEDIA
    if nivel_text in BASICA_LEVELS:
        return BASICA
    return None

# ===== Permisos por nivel (usa la relación reversa permisos_nivel) =====
def has_perm_on_nivel(user, action: str, nivel_group: str) -> bool:
    """
    action: 'view' | 'add' | 'edit' | 'print'
    """
    if not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'rol', None) == ADMIN or user.is_superuser:
        return True

    try:
        perm = user.permisos_nivel.filter(nivel=nivel_group).first()
    except Exception:
        perm = None

    if not perm:
        return False

    return {
        'view':  perm.can_view,
        'add':   perm.can_add,
        'edit':  perm.can_edit,
        'print': perm.can_print,
    }.get(action, False)

# === Mapas exactos a los choices de Curso.nivel ===
BASICA_LEVELS = [
    'Prekinder', 'Kinder',
    '1° Básico', '2° Básico', '3° Básico', '4° Básico',
    '5° Básico', '6° Básico', '7° Básico', '8° Básico',
]
MEDIA_LEVELS = ['1° Medio', '2° Medio', '3° Medio', '4° Medio']

def niveles_permitidos_por_usuario(user, action='view'):
    """
    Devuelve la lista de valores exactos (de Curso.nivel) permitidos para el usuario,
    según UsuarioPermiso y la acción ('view' | 'add' | 'edit' | 'print').
    """
    if not getattr(user, 'is_authenticated', False):
        return []

    # Admin ve todo
    if getattr(user, 'rol', None) == ADMIN or user.is_superuser:
        return BASICA_LEVELS + MEDIA_LEVELS

    # Busca sus permisos por nivel
    qs = getattr(user, 'permisos_nivel', None)
    if qs is None:
        return []

    permitted = []
    # Básica
    perm_b = qs.filter(nivel='BASICA').first()
    if perm_b and getattr(perm_b, f'can_{action}', False):
        permitted += BASICA_LEVELS

    # Media
    perm_m = qs.filter(nivel='MEDIA').first()
    if perm_m and getattr(perm_m, f'can_{action}', False):
        permitted += MEDIA_LEVELS

    return permitted


def filtro_q_por_niveles_permitidos(user, field='nivel', action='view'):
    """
    Q(field__in=[niveles]) listo para usar en Curso o en relaciones (ej: 'curso__nivel').
    Si no hay permisos, devuelve Q(False) práctico (no matchea nada).
    """
    if user.is_authenticated and (user.rol == ADMIN or user.is_superuser):
        return Q()
    levels = niveles_permitidos_por_usuario(user, action=action)
    if not levels:
        # Q imposible (truco para “none” sin depender de pk)
        return Q(**{f'{field}__in': ['__NO_PERM__']})
    query = Q(**{f'{field}__in': levels})
    if has_perm_on_nivel(user, action, BASICA) and has_perm_on_nivel(user, action, MEDIA):
        query |= Q(**{f'{field}__in': ['Sin asignar', 'Por Asignar', 'Egresados']})
        query |= Q(**{f'{field}__isnull': True})
    return query
