"""
app/views/_common.py
====================
Nombre común del paquete de vistas: TODAS las importaciones externas,
constantes compartidas y helpers multi-módulo viven aquí.

Los módulos hermanos (auth, estudiantes, pdf, ...) hacen `from ._common import *`.
`__all__` define exactamente qué se exporta al hacer * (incluye helpers privados
con `_` para mantener paridad con el antiguo views.py monolítico).
"""
from functools import wraps
from types import SimpleNamespace
from typing import Optional, Dict, List, Tuple, Any
from collections import namedtuple
from datetime import date, datetime

import json
import os
import re

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.hashers import make_password
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Q, Count, Case, When, F, IntegerField, Value
from django.forms import ValidationError
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.http import require_GET, require_POST

# --- Base de datos de la app ---
from app.models import (
    AcademicYear,
    Apoderado,
    ConfiguracionFirma,
    ConfiguracionMatricula,
    ConfiguracionSistema,
    Curso,
    DocumentoEstudiante,
    Estudiante,
    MatriculaAnual,
    UsuarioPermiso,
    Usuarios,
)

# --- Formularios ---
from app.forms import (
    ConfiguracionFirmaForm,
    ConfiguracionMatriculaForm,
    ConfiguracionSistemaForm,
    EstudianteForm,
    LoginForm,
)

# --- Roles y permisos ---
from app.roles import (
    ADMIN,
    ASISTENTE,
    BASICA,
    DIRECTIVO,
    MEDIA,
    PROFESOR,
    curso_nivel_to_group,
    filtro_q_por_niveles_permitidos,
    has_perm_on_nivel,
    niveles_permitidos_por_usuario,
    role_required,
)

# Alias seguro hacia el runner de importación SQL (solo lo usa datos.py)
from app.sql_import_runner import import_sqldump as run_import_sqldump

# =========================
#  Constantes compartidas
# =========================
PERMITIDOS_TODOS = (ADMIN, DIRECTIVO, PROFESOR, ASISTENTE)

NIVEL_POR_ASIGNAR = "Por Asignar"
NIVEL_EGRESADOS = "Egresados"
LABEL_SIN_NIVEL = "Sin nivel"

# Fila ligera para listar estudiantes (usada por listar_estudiantes)
EstuRow = namedtuple('EstuRow', ['id', 'rut', 'nombre_completo', 'curso', 'estado'])

# =========================
#  Helpers compartidos
# =========================


def _parse_date(val):
    """Acepta 'YYYY-MM-DD', 'DD-MM-YYYY' o 'DD/MM/YYYY'. Devuelve date o None."""
    if not val:
        return None
    val = val.strip()
    for fmt in ('%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y'):
        try:
            return datetime.strptime(val, fmt).date()
        except Exception:
            continue
    return None


def _to_bool(val):
    """Convierte entradas de formulario en booleano:
    'si'/'sí'/'true'/'t'/'1'/'on'/'yes'/'y' (case-insensitive) => True."""
    if val is None:
        return False
    s = str(val).strip().lower()
    return s in ('si', 'sí', 'true', 't', '1', 'on', 'yes', 'y')


def _is_ajax(request):
    return request.headers.get('x-requested-with') == 'XMLHttpRequest'


def _norm(text):
    return str(text).strip() if text is not None else ""


def _norm_rut(rut):
    """Normaliza RUT: sin puntos, sin guiones, sin espacios, en mayúsculas.
    (Consolidación de las dos variantes del monolito: la estricta, la que
    usan crear/guardar/registro de estudiantes y permite comparar RUTs.)"""
    return _norm(rut).replace(".", "").replace("-", "").replace(" ", "").upper()


def _norm_run(run: str) -> str:
    """Deja solo alfanuméricos en mayúsculas (versión tolerante)."""
    if not run:
        return ''
    return ''.join(ch for ch in run if ch.isalnum()).upper()


def _rut_variants(rut_norm):
    """Variantes de almacenamiento de un RUT: normalizado nuevo ('123456785')
    y formato legado con guion ('12345678-5') usado por importaciones antiguas."""
    if not rut_norm:
        return []
    rut_norm = str(rut_norm)
    variants = [rut_norm]
    if len(rut_norm) >= 2:
        variants.append(f"{rut_norm[:-1]}-{rut_norm[-1]}")
    return variants


def estudiante_por_rut(rut_norm):
    """Busca un estudiante tolerando el formato en que esté guardado su RUT
    (sin guion nuevo vs. con guion legado). Devuelve inst o None.
    Prioriza la fila normalizada exacta si existieran ambas (duplicados previos)."""
    if not rut_norm:
        return None
    exact = Estudiante.objects.filter(rut=rut_norm).first()
    if exact is not None:
        return exact
    variantes = _rut_variants(rut_norm)
    if len(variantes) > 1:
        return Estudiante.objects.filter(rut__in=variantes).first()
    return None


def _bool(v):
    return _norm(v).lower() in ("1", "true", "t", "si", "sí", "y", "yes", "on")


def _pick_int(v, default=None):
    s = _norm(v)
    try:
        return int(s) if s else default
    except Exception:
        return default


def _get_text(request, *names, default=""):
    """Devuelve el primer POST[name] no vacío (sirve si el template usa otros names)."""
    for n in names:
        v = (request.POST.get(n) or "").strip()
        if v:
            return v
    return default


def _fmt_date(d):
    try:
        return d.strftime('%d-%m-%Y') if d else 'N/A'
    except Exception:
        return 'N/A'


def _nivel_from_curso_obj(curso):
    """
    Devuelve 'BASICA' | 'MEDIA' | None dependiendo del curso.
    """
    if not curso:
        return None
    nivel = (curso.nivel or '').lower()
    if 'básic' in nivel or 'basico' in nivel or 'basica' in nivel:
        return 'BASICA'
    if 'medio' in nivel or 'media' in nivel:
        return 'MEDIA'
    return None


def _curso_en_anio(estudiante, anio):
    """
    Retorna (curso_texto, curso_obj, tiene_matricula_en_anio).
    - Busca SOLO MatriculaAnual del año dado.
    - Prioriza curso asignado y distinto del placeholder ("Por Asignar").
    """
    ma = (
        MatriculaAnual.objects
        .filter(estudiante=estudiante, anio=anio)
        .select_related('curso')
        # compat Django 5: "-curso__isnull" ya no es order_by válido;
        # Case/When es portable (MySQL + SQLite) con la misma semántica
        .annotate(tiene_curso=Case(When(curso__isnull=False, then=1), default=0, output_field=IntegerField()))
        .order_by('-tiene_curso', '-id')  # curso no nulo primero, luego la última
        .first()
    )
    curso_obj = getattr(ma, 'curso', None)

    if curso_obj and getattr(curso_obj, 'nivel', None) != NIVEL_POR_ASIGNAR:
        curso_texto = f"{curso_obj.nivel}{' ' + curso_obj.letra if curso_obj.letra else ''} - {anio}"
        return (curso_texto, curso_obj, True)
    return (f"Sin curso asignado ({anio})", None, False)


def _curso_en_anio_puro(estudiante, anio):
    """
    SOLO mira el año pedido. Prioriza MA con curso != NULL.
    Retorna (texto, curso_obj) o ("Sin curso (anio)", None).
    """
    ma = (MatriculaAnual.objects
          .filter(estudiante=estudiante, anio=anio)
          .select_related('curso')
          .annotate(tiene_curso=Case(When(curso__isnull=False, then=1), default=0, output_field=IntegerField()))
          .order_by('-tiene_curso', '-id')
          .first())
    curso_obj = getattr(ma, 'curso', None)
    if curso_obj and getattr(curso_obj, 'nivel', None) != NIVEL_POR_ASIGNAR:
        txt = f"{curso_obj.nivel}{' ' + curso_obj.letra if curso_obj.letra else ''} - {anio}"
        return txt, curso_obj
    return f"Sin curso - {anio}", None


def _nombre_y_rol_matriculador(est):
    """
    Devuelve 'Nombre (Rol)' del usuario que matriculó, evitando mostrar username/RUT.
    Prioriza `quien_matricula` (texto libre). Si no hay, arma nombre humano desde el usuario.
    Rol: usa `user.rol` si existe; si no, toma nombres de grupos.
    """
    name = (getattr(est, 'quien_matricula', '') or '').strip()
    u = getattr(est, 'matriculado_por', None)

    if not name and u:
        parts = []
        for attr in ('first_name', 'nombre'):
            v = getattr(u, attr, '') or ''
            if v: parts.append(v)
        for attr in ('last_name', 'apellido'):
            v = getattr(u, attr, '') or ''
            if v: parts.append(v)
        name = ' '.join(p.strip() for p in parts if p).strip()

        if not name:
            email = getattr(u, 'email', '') or ''
            if email:
                name = email

    if not name:
        name = 'Usuario interno'

    role = None
    if u:
        raw_rol = getattr(u, 'rol', None)
        if raw_rol:
            role = str(raw_rol).replace('_', ' ').title()

        if not role:
            try:
                groups = list(u.groups.all())
                if groups:
                    names = [g.name for g in groups if getattr(g, 'name', None)]
                    if names:
                        role = ', '.join(sorted(set(names)))
            except Exception:
                pass

    return f"{name} ({role})" if role else name


def is_admin(u) -> bool:
    return bool(u.is_superuser or u.is_staff)


# =========================
#  Contrato del star-import
# =========================
__all__ = [
    # stdlib / utilidades
    'json', 'os', 're',
    'wraps', 'SimpleNamespace',
    'Optional', 'Dict', 'List', 'Tuple', 'Any', 'namedtuple',
    'date', 'datetime', 'timezone',
    # Django
    'settings', 'messages',
    'authenticate', 'login', 'logout',
    'render', 'redirect', 'get_object_or_404',
    'Http404', 'JsonResponse', 'HttpResponse', 'FileResponse',
    'ValidationError', 'PermissionDenied', 'IntegrityError',
    'transaction', 'make_password',
    'Q', 'Count', 'Case', 'When', 'F', 'IntegerField', 'Value',
    'login_required', 'user_passes_test',
    'require_GET', 'require_POST',
    'xframe_options_exempt',
    # Modelos
    'AcademicYear', 'Apoderado', 'ConfiguracionFirma', 'ConfiguracionMatricula',
    'ConfiguracionSistema', 'Curso', 'DocumentoEstudiante', 'Estudiante',
    'MatriculaAnual', 'UsuarioPermiso', 'Usuarios',
    # Formularios
    'ConfiguracionFirmaForm', 'ConfiguracionMatriculaForm', 'ConfiguracionSistemaForm',
    'EstudianteForm', 'LoginForm',
    # Roles y permisos
    'ADMIN', 'ASISTENTE', 'BASICA', 'DIRECTIVO', 'MEDIA', 'PROFESOR',
    'curso_nivel_to_group', 'filtro_q_por_niveles_permitidos',
    'has_perm_on_nivel', 'niveles_permitidos_por_usuario', 'role_required',
    'run_import_sqldump',
    # Constantes y helpers compartidos
    'PERMITIDOS_TODOS', 'NIVEL_POR_ASIGNAR', 'NIVEL_EGRESADOS', 'LABEL_SIN_NIVEL',
    'EstuRow',
    '_parse_date', '_to_bool', '_is_ajax', '_norm', '_norm_rut', '_norm_run',
    '_bool', '_pick_int', '_get_text', '_fmt_date',
    '_nivel_from_curso_obj', '_curso_en_anio', '_curso_en_anio_puro',
    '_nombre_y_rol_matriculador', 'is_admin',
    '_rut_variants', 'estudiante_por_rut',
]
