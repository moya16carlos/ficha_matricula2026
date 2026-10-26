"""Registro de solicitudes de la aplicación, sin valores ni credenciales del formulario."""
import json
import logging

from django.db import DatabaseError

from app.models import RegistroActividad, Usuarios

ACCIONES = {
    'login': 'Inicio de sesión', 'logout': 'Cierre de sesión',
    'guardar_estudiante': 'Guardar ficha', 'nuevo_estudiante': 'Guardar matrícula',
    'registro_alumno': 'Matrícula pública', 'registro_apoderado': 'Registro de apoderado',
    'cambiar_rut_estudiante': 'Cambiar RUT', 'asignar_curso_en_anio': 'Cambiar curso',
    'movimiento_estudiante': 'Movimiento escolar',
    'crear_usuario': 'Crear usuario', 'editar_usuario': 'Editar usuario',
    'perfil_usuario': 'Actualizar perfil', 'actualizar_rol_usuario': 'Cambiar rol',
    'eliminar_usuario': 'Eliminar usuario', 'actualizar_permisos_usuario': 'Cambiar permisos',
    'cambiar_password_usuario': 'Cambiar contraseña',
    'panel_config_documentos': 'Cambiar configuración', 'toggle_year_visibility': 'Visibilidad del año',
    'agregar_anio_academico': 'Crear año', 'toggle_registro_apoderado': 'Registro público',
    'api_year_create': 'Crear año', 'api_seed_estudiantes_a_anio': 'Copiar matrículas',
    'api_clone_anio': 'Clonar año', 'import_datos_panel': 'Importar datos',
    'importar_sql_dump': 'Importar SQL',
    'buscar_estudiante': 'Consultar ficha', 'buscar_estudiantes_por_rut': 'Consultar ficha',
    'detalles_estudiante': 'Consultar ficha', 'descargar_documento': 'Descargar documento',
    'imprimir_estudiante': 'Imprimir ficha', 'imprimir_estudiantes_por_curso': 'Imprimir curso',
    'export_datos': 'Exportar datos', 'export_estudiantes_por_anio': 'Exportar estudiantes',
}
LECTURAS = {'buscar_estudiante', 'buscar_estudiantes_por_rut', 'detalles_estudiante', 'descargar_documento', 'imprimir_estudiante',
            'imprimir_estudiantes_por_curso', 'export_datos', 'export_estudiantes_por_anio', 'logout'}
# Solo nombres conocidos: nunca se guardan valores, archivos, URLs completas ni claves enviadas arbitrariamente.
CAMPOS = {'nombre_completo', 'curso', 'curso_id', 'estado', 'anio', 'anio_filtro',
          'domicilio', 'comuna', 'telefono_apoderado', 'email_apoderado', 'nombre_madre',
          'nombre_padre', 'nombre_apoderado', 'nombre_apoderado_suplente',
          'rol', 'nombre', 'apellido', 'email', 'can_view', 'can_add', 'can_edit', 'can_print'}


def nombre_usuario(usuario):
    return f'{usuario.nombre} {usuario.apellido}'.strip()[:150]


def registrar(request, accion, objeto='', resultado='Completado', codigo=200):
    actor = getattr(request, 'audit_actor', None) or request.user
    nombre = getattr(request, 'audit_nombre', '')
    if not nombre:
        nombre = nombre_usuario(actor) if actor.is_authenticated else 'Sin sesión de funcionario'
    usuario_id = actor.pk if actor.is_authenticated else None
    if usuario_id and not Usuarios.objects.filter(pk=usuario_id).exists():
        usuario_id = None
    RegistroActividad.objects.create(
        usuario_id=usuario_id, usuario_nombre=nombre[:150], accion=accion[:80], objeto=objeto[:150],
        resultado=resultado, codigo_http=codigo,
        campos=sorted(CAMPOS.intersection(request.POST)) if request.method == 'POST' else [],
    )


class RegistroActividadMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.audit_actor = request.user if request.user.is_authenticated else None
        request.audit_nombre = nombre_usuario(request.user) if request.user.is_authenticated else ''
        response = self.get_response(request)
        match = getattr(request, 'resolver_match', None)
        ruta = match.url_name if match else None
        es_admin = bool(match and match.namespace == 'admin' and request.method == 'POST')
        if (ruta not in ACCIONES and not es_admin) or getattr(request, 'audit_registrado', False):
            return response
        if ruta in ('buscar_estudiante', 'buscar_estudiantes_por_rut') and not request.GET.get('rut'):
            return response
        if request.method != 'POST' and ruta not in LECTURAS:
            return response
        resultado = 'Completado' if response.status_code < 400 else 'Rechazado'
        if ruta not in ('registro_alumno', 'registro_apoderado') and not request.user.is_authenticated and not request.audit_actor:
            resultado = 'Rechazado'
        if response.status_code in (301, 302, 303) and '?next=' in response.get('Location', ''):
            resultado = 'Rechazado'
        if any(m.level >= 40 for m in getattr(getattr(request, '_messages', None), '_queued_messages', [])):
            resultado = 'Rechazado'
        if response.get('Content-Type', '').startswith('application/json') and (request.method == 'POST' or ruta in ('buscar_estudiante', 'buscar_estudiantes_por_rut')):
            try:
                data = json.loads(response.content)
                if isinstance(data, dict) and (data.get('ok') is False or data.get('success') is False or bool(data.get('error')) or data.get('status') == 'error'):
                    resultado = 'Rechazado'
            except (ValueError, AttributeError):
                pass
        adminform = getattr(response, 'context_data', None) or {}
        if adminform.get('adminform') and adminform['adminform'].form.errors:
            resultado = 'Rechazado'
        objeto = getattr(request, 'audit_objeto', '')
        if not objeto and match:
            objeto = ', '.join(f"{k.removesuffix('_id').replace('_', ' ').capitalize()} #{v}"
                               for k, v in match.kwargs.items() if k.endswith('_id'))
        if not objeto and request.method == 'POST':
            objeto = ', '.join(f"{('Usuario' if key == 'user_id' else key.removesuffix('_id').replace('_', ' ').capitalize())} #{request.POST[key]}" for key in
                               ('user_id', 'usuario_id', 'curso_id', 'activar_id', 'eliminar_id', 'year_id', 'anio', 'anio_destino', 'source_year', 'target_year')
                               if request.POST.get(key, '').isdigit())
        if es_admin:
            objeto = f'{ruta} · {objeto}'
        accion = ACCIONES.get(ruta, 'Administración Django')
        if ruta == 'panel_config_documentos':
            accion = {
                'matricula': 'Configurar documento de matrícula', 'sistema_update': 'Cambiar preferencias',
                'firma': 'Crear firma', 'firma_toggle': 'Activar firma', 'firma_delete': 'Eliminar firma',
                'curso_create': 'Crear curso', 'curso_delete': 'Eliminar curso',
                'ay_create': 'Crear año', 'ay_toggle_visible': 'Visibilidad del año',
                'ay_delete': 'Eliminar año', 'ay_set_active': 'Seleccionar año activo',
            }.get(request.POST.get('form_type'), accion)
        try:
            registrar(request, accion, objeto, resultado, response.status_code)
        except DatabaseError:
            # La operación ya respondió: un fallo del log no debe inducir al usuario a repetirla.
            logging.getLogger(__name__).exception('No se pudo registrar actividad en %s', ruta)
        return response
