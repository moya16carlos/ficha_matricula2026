from django.conf import settings
from django.conf.urls.static import static
from django.urls import path

from . import views
from .views import (
    agregar_anio_academico,
    alumno_matriculado,
    api_seed_estudiantes_a_anio,
    api_year_create,
    asignar_curso_en_anio,
    buscar_estudiante,
    buscar_estudiantes_por_rut,
    busqueda_global,
    cambiar_rut_estudiante,
    crear_usuario_view,
    detalles_estudiante,
    descargar_documento,
    editar_usuario,
    export_datos,
    export_estudiantes_por_anio,
    ficha_estudiante_view,
    guardar_estudiante,
    home_view,
    import_datos_panel,
    importar_sql_dump,
    imprimir_estudiante,
    imprimir_estudiantes_por_curso,
    listar_estudiantes,
    listar_estudiantes_admin_view,
    listar_estudiantes_anio,
    listar_usuarios_view,
    login_view,
    logout_view,
    nuevo_estudiante_view,
    panel_anios,
    panel_config_documentos,
    perfil_usuario_view,
    preview_ficha_pdf,
    registrar_apoderado,
    registro_alumno_view,
    toggle_registro_apoderado,
    toggle_year_visibility,
)

urlpatterns = [
    # Auth / Home
    path('', login_view, name='login'),
    path('logout/', logout_view, name='logout'),
    path('home/', home_view, name='home'),

    # Estudiantes (CRUD + utilidades)
    path('ficha-estudiante/', ficha_estudiante_view, name='ficha_estudiante'),
    path('nuevo-estudiante/', nuevo_estudiante_view, name='nuevo_estudiante'),
    path('guardar-estudiante/', guardar_estudiante, name='guardar_estudiante'),
    path('estudiante/<int:estudiante_id>/cambiar-rut/', cambiar_rut_estudiante, name='cambiar_rut_estudiante'),
    path('estudiante/asignar-curso/', asignar_curso_en_anio, name='asignar_curso_en_anio'),

    # Búsqueda (convención: "buscar-estudiante" general, "buscar-estudiantes" por RUT para JS)
    path('buscar-estudiante/', buscar_estudiante, name='buscar_estudiante'),
    path('buscar-estudiantes/', buscar_estudiantes_por_rut, name='buscar_estudiantes_por_rut'),
    path('api/busqueda-global/', busqueda_global, name='busqueda_global'),

    # Listados / detalle / impresión
    path('listar-estudiantes/', listar_estudiantes, name='listar_estudiantes'),
    path('estudiantes/', listar_estudiantes_anio, name='listar_estudiantes_anio'),  # ?anio=2026
    path('estudiantes/<int:anio>/', listar_estudiantes_anio, name='listar_estudiantes_anio_var'),
    path('detalles-estudiante/<int:estudiante_id>/', detalles_estudiante, name='detalles_estudiante'),
    path('imprimir-estudiante/<str:rut>/', imprimir_estudiante, name='imprimir_estudiante'),
    path('imprimir_estudiantes/<int:curso_id>/', imprimir_estudiantes_por_curso, name='imprimir_estudiantes_por_curso'),

    path('estudiante/<int:estudiante_id>/movimiento/', views.movimiento_estudiante, name='movimiento_estudiante'),
    path('config/actividad/', views.registro_actividad, name='registro_actividad'),

    # Documentos
    path('descargar-documento/<int:documento_id>/', descargar_documento, name='descargar_documento'),

    # Gestión de usuarios del sistema
    path('crear_usuario/', crear_usuario_view, name='crear_usuario'),
    path('listar_usuarios/', listar_usuarios_view, name='listar_usuarios'),
    path('perfil_usuario/<int:usuario_id>/', perfil_usuario_view, name='perfil_usuario'),
    path('editar_usuario/<int:usuario_id>/', editar_usuario, name='editar_usuario'),
    path('listar_estudiantes_admin/', listar_estudiantes_admin_view, name='listar_estudiantes_admin'),

    # Flujo Apoderado (registro + matriculación alumno)
    path('registro_apoderado/', registrar_apoderado, name='registro_apoderado'),
    path('registro_alumno/', registro_alumno_view, name='registro_alumno'),
    path('alumno_matriculado/', alumno_matriculado, name='alumno_matriculado'),

    # Panel de configuración
    path('config/documentos/', panel_config_documentos, name='panel_config_documentos'),
    path('config/documentos/toggle/<int:year_id>/', toggle_year_visibility, name='toggle_year_visibility'),
    path('config/documentos/agregar/', agregar_anio_academico, name='agregar_anio_academico'),
    path('config/documentos/preview.pdf', preview_ficha_pdf, name='preview_ficha_pdf'),
    path('config/registro-apoderado/toggle/', toggle_registro_apoderado, name='toggle_registro_apoderado'),
    path('config/usuarios/rol/', views.actualizar_rol_usuario, name='actualizar_rol_usuario'),
    path('config/usuarios/eliminar/', views.eliminar_usuario, name='eliminar_usuario'),
    path('config/usuarios/permisos/actualizar/', views.actualizar_permisos_usuario, name='actualizar_permisos_usuario'),
    path('config/usuarios/cambiar-password/', views.cambiar_password_usuario, name='cambiar_password_usuario'),

    # API años académicos
    path('api/anio/seed-estudiantes/', api_seed_estudiantes_a_anio, name='api_seed_estudiantes_a_anio'),
    path('api/anio/clone/', views.api_clone_anio, name='api_clone_anio'),

    # Exportar / importar datos
    path('config/exportar/', export_datos, name='export_datos'),
    path('config/importar/', import_datos_panel, name='import_datos_panel'),
    path('export/estudiantes/', export_estudiantes_por_anio, name='export_estudiantes_por_anio'),
    path('config/importar-sql/', views.importar_sql_dump, name='importar_sql_dump'),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

handler403 = 'app.views.error_403_view'
