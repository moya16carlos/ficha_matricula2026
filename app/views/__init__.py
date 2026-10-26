"""
app.views — paquete de vistas (refactor del antiguo views.py monolítico).

Módulos:
- _common.py       → importaciones compartidas, helpers y constantes
- auth.py          → login, home, logout, error 403
- estudiantes.py   → CRUD/listados de estudiantes
- impresion_pdf.py → PDFs (ficha, listado por curso, preview)
- registro_publico.py → flujo apoderado (registro alumno / apoderado)
- usuarios.py      → gestión de usuarios, roles y permisos
- panel.py         → panel de configuración
- anios.py         → semillas/clonado de años académicos y cursos
- datos.py         → export/import CSV·JSON·SQL

Reexportamos aquí los nombres que consume el resto del proyecto
(urls.py, app.sql_import_runner, handler403) para mantener paridad
total con el monolito.
"""
from ._common import *  # noqa: F401,F403

from .auth import (
    error_403_view,
    home_view,
    login_view,
    logout_view,
)
from .estudiantes import (
    asignar_curso_en_anio,
    buscar_estudiante,
    buscar_estudiantes_por_rut,
    busqueda_global,
    cambiar_rut_estudiante,
    detalles_estudiante,
    descargar_documento,
    ficha_estudiante,
    ficha_estudiante_view,
    ficha_matricula,
    guardar_estudiante,
    listar_estudiantes,
    listar_estudiantes_anio,
    nuevo_estudiante_view,
)
from .impresion_pdf import (
    imprimir_estudiante,
    imprimir_estudiantes_por_curso,
    preview_ficha_pdf,
)
from .registro_publico import (
    alumno_matriculado,
    registrar_apoderado,
    registro_alumno_view,
)
from .usuarios import (
    actualizar_permisos_usuario,
    actualizar_rol_usuario,
    cambiar_password_usuario,
    crear_usuario_view,
    editar_usuario,
    eliminar_usuario,
    listar_estudiantes_admin_view,
    listar_usuarios_view,
    nivel_permission_required,
    perfil_usuario_view,
)
from .panel import (
    agregar_anio_academico,
    api_year_create,
    panel_anios,
    panel_config_documentos,
    toggle_registro_apoderado,
    toggle_year_visibility,
)
from .anios import (
    CAMPOS_SEED,
    api_clone_anio,
    api_clone_curso_a_curso,
    api_seed_estudiantes_a_anio,
    clone_curso_to_curso,
    clone_matriculas_anio,
    ensure_academic_year,
    ensure_curso,
    map_curso_destino,
    next_nivel,
    seed_matricula_from_estudiantes,
)
from .datos import (
    export_datos,
    export_estudiantes_por_anio,
    import_datos_panel,
    importar_sql_dump,
)

from .historial import movimiento_estudiante, registro_actividad
