from dataclasses import dataclass
from functools import wraps
from itertools import count
import json
import re
import tempfile
import time
import unicodedata
from django.forms import ValidationError
from django.shortcuts import render, redirect, get_object_or_404
from django.http import Http404, JsonResponse, HttpResponse
from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from .models import Apoderado, ConfiguracionMatricula, ConfiguracionSistema, Curso, Estudiante, DocumentoEstudiante, UsuarioPermiso, Usuarios, ConfiguracionFirma, ConfiguracionMatricula, ConfiguracionSistema, MatriculaAnual, AcademicYear
from .forms import LoginForm, EstudianteForm, ConfiguracionFirmaForm, ConfiguracionMatriculaForm, ConfiguracionSistemaForm
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from io import BytesIO
from datetime import date, datetime, timezone
from django.http import FileResponse
from reportlab.lib import colors
from reportlab.platypus import Table, TableStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Spacer, Paragraph, PageTemplate, Frame, Image, PageBreak
#from reportlab.lib.styles import getSampleStyleSheet
from typing import Optional, Dict, List, Tuple, Any

from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Image, Spacer, KeepTogether
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

import os 
from django.db.models import Q, Count, Case, When, F, IntegerField, Value
import csv
from django.utils.dateparse import parse_date
from django.contrib.auth.decorators import login_required
from django.conf import settings


from django.views.decorators.clickjacking import xframe_options_exempt
from django.db.models.deletion import ProtectedError
from django.contrib import messages
from django.views.decorators.http import require_POST
from django.utils.timezone import now
from django.db import IntegrityError, transaction
from types import SimpleNamespace

from django.contrib.auth.hashers import make_password
from reportlab.platypus import HRFlowable
from collections import namedtuple

EstuRow = namedtuple('EstuRow', ['id', 'rut', 'nombre_completo', 'curso', 'estado'])



from app.roles import (
    curso_nivel_to_group,
    niveles_permitidos_por_usuario,
    role_required, 
    has_perm_on_nivel,  # si lo usas
    ADMIN, DIRECTIVO, PROFESOR, ASISTENTE,
    BASICA, MEDIA
)


from app.roles import filtro_q_por_niveles_permitidos

def _parse_date(val):
    if not val:
        return None
    val = val.strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(val, fmt).date()
        except ValueError:
            continue
    return None

PERMITIDOS_TODOS = (ADMIN, DIRECTIVO, PROFESOR, ASISTENTE)

def login_view(request):
    # Obtén la única fila (si no existe, crea una con valor por defecto)
    cfg, _ = ConfiguracionSistema.objects.get_or_create(
        id=1, defaults={'registro_apoderado_habilitado': False}
    )
    registro_habilitado = bool(cfg.registro_apoderado_habilitado)

    if request.method == 'POST':
        run = (request.POST.get('run') or '').strip()
        password = request.POST.get('password') or ''
        user = authenticate(request, username=run, password=password)
        if user is not None:
            login(request, user)
            return redirect('home')
        messages.error(request, 'Usuario o contraseña incorrectos')

    return render(request, 'app/login.html', {
        'registro_habilitado': registro_habilitado
    })


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

@login_required
# Vista principal (home)
def home_view(request):
    # Orden lógico: año, nivel, letra
    
    return render(request, 'app/home.html', {
        
    })

# Vista de logout
@login_required
def logout_view(request):
    logout(request)
    return redirect('login')

@role_required(*PERMITIDOS_TODOS)
def ficha_estudiante_view(request):
    cursos = Curso.objects.all().order_by('anio', 'nivel', 'letra')
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
            est = Estudiante.objects.filter(rut=rut_norm).first()
            is_create = est is None
            if not est:
                est = Estudiante(rut=rut_norm)

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
            est.sistema_salud = (sistema_salud if sistema_salud in ('Fonasa', 'Isapre') else 'Fonasa')
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
            est.save()

            # === MatriculaAnual (SIEMPRE con el año del curso elegido)
            MatriculaAnual.objects.update_or_create(
                anio=curso_obj.anio,
                estudiante=est,
                defaults={'curso': curso_obj, 'estado': est.estado or 'Activo'}
            )

            # Devolver URL de impresión con ?anio correcto
            imprimir_url = request.build_absolute_uri(f"/imprimir-estudiante/{est.rut}/?anio={curso_obj.anio}")
            return JsonResponse({
                'ok': True,
                'imprimir_url': imprimir_url,
                'anio_sel': anio_sel,
                'operation': 'created' if is_create else 'updated'
            })

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
                estudiante = Estudiante.objects.get(rut=rut)
                data = {
                    'rut': estudiante.rut,
                    'nombre_completo': estudiante.nombre_completo,
                    'fecha_nacimiento': estudiante.fecha_nacimiento.strftime('%Y-%m-%d') if estudiante.fecha_nacimiento else '',
                    'sexo': estudiante.sexo,
                    'curso': estudiante.curso,
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
                return JsonResponse({'status': 'success', 'data': data})
            except Estudiante.DoesNotExist:
                return JsonResponse({'status': 'error', 'message': 'No se encontró un estudiante con ese RUT.'})
        else:
            return JsonResponse({'status': 'error', 'message': 'Debe proporcionar un RUT válido.'})
    return JsonResponse({'status': 'error', 'message': 'Método no permitido.'})



def _to_bool(val):
    if val is None:
        return False
    s = str(val).strip().lower()
    return s in ('si', 'sí', 'true', '1', 'on', 'yes')

def _parse_date(val):
    if not val:
        return None
    for fmt in ('%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y'):
        try:
            return datetime.strptime(val.strip(), fmt).date()
        except Exception:
            continue
    return None

def _is_ajax(request):
    return request.headers.get('x-requested-with') == 'XMLHttpRequest'

def _norm_rut(r):
    if not r: 
        return ''
    return r.strip().upper().replace('.', '').replace('-', '')

def _get_text(request, *names, default=""):
    """Devuelve el primer POST[name] no vacío (sirve si el template usa otros names)."""
    for n in names:
        v = (request.POST.get(n) or "").strip()
        if v:
            return v
    return default

# --- CONSTANTES (si no las tienes globales) ---
NIVEL_POR_ASIGNAR = "Por Asignar"
NIVEL_EGRESADOS   = "Egresados"
LABEL_SIN_NIVEL   = "Sin nivel"

@login_required
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
    estudiante = Estudiante.objects.filter(rut=rut).first()
    is_create = estudiante is None
    if not estudiante:
        estudiante = Estudiante(rut=rut)

    # 5) Resolver nivel para permisos:
    #    - Si hay curso en POST => usar ese nivel
    #    - Si no, y el estudiante ya tiene curso => usar nivel del curso actual
    #    - Si no hay forma de resolver, omitir chequeo por nivel (evita .title() sobre None)
    new_nivel = None
    if curso is not None:
        new_nivel = _nivel_from_curso_obj(curso)
    elif getattr(estudiante, 'curso', None):
        new_nivel = _nivel_from_curso_obj(estudiante.curso)

    # 5.1) Chequeo de permisos según operación (add/edit) SOLO si tengo nivel
    #      (así evitamos el AttributeError por .title() en None)
    if new_nivel:
        oper = 'add' if is_create else 'edit'
        if not has_perm_on_nivel(request.user, oper, new_nivel):
            # Evita llamar .title() si por alguna razón no es str
            nivel_label = (new_nivel.title() if isinstance(new_nivel, str) else 'este nivel')
            msg = f'No tienes permiso para {"agregar" if is_create else "editar"} estudiantes en {nivel_label}.'
            if _is_ajax(request):
                return JsonResponse({'ok': False, 'error': msg}, status=403)
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied(msg)

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
        if sistema_salud not in ('Fonasa', 'Isapre'):
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

        # Guardar alumno
        estudiante.full_clean()
        estudiante.save()

        # ---------- MatriculaAnual ----------
        if curso is not None:
            anio_ma = anio_destino if anio_destino is not None else getattr(curso, 'anio', None)
            if anio_ma is not None:
                ma, _ = MatriculaAnual.objects.update_or_create(
                    estudiante=estudiante, anio=anio_ma,
                    defaults={'curso': curso, 'estado': estudiante.estado or 'Activo'}
                )
                # Limpieza de duplicados sin curso
                (MatriculaAnual.objects
                 .filter(estudiante=estudiante, anio=anio_ma, curso__isnull=True)
                 .exclude(pk=ma.pk)
                 .delete())

    except Exception as e:
        if _is_ajax(request):
            return JsonResponse({'ok': False, 'error': str(e)}, status=400)
        messages.error(request, f'Error al guardar el estudiante: {e}')
        return redirect('nuevo_estudiante')

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

    # Redirigir a imprimir con el año coherente
    if curso is not None:
        anio_redirect = anio_destino if anio_destino is not None else curso.anio
        from django.urls import reverse
        url = reverse('imprimir_estudiante', kwargs={'rut': estudiante.rut}) + f'?anio={anio_redirect}'
        return redirect(url)
    return redirect('imprimir_estudiante', rut=estudiante.rut)

@login_required
@role_required(*PERMITIDOS_TODOS)
def listar_estudiantes(request):
    q = (request.GET.get('q') or '').strip()
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

    # Niveles especiales permitidos siempre en la UI
    NIVELES_ESPECIALES = [NIVEL_POR_ASIGNAR, NIVEL_EGRESADOS]

    # Filtros por nivel para Curso y para MatriculaAnual (MA)
    q_niv_curso = Q(nivel__in=niveles_ok) | Q(nivel__in=NIVELES_ESPECIALES)
    q_niv_ma    = Q(curso__nivel__in=niveles_ok) | Q(curso__nivel__in=NIVELES_ESPECIALES) | Q(curso__isnull=True)

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
                .filter(anio=anio_sel, curso__isnull=True)
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
            if curso_sel and not (curso_sel.nivel in niveles_ok or curso_sel.nivel in NIVELES_ESPECIALES):
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
                    curso__nivel__in=(list(niveles_ok) + NIVELES_ESPECIALES)
                )
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

    return render(request, 'app/listar_estudiantes.html', {
        'cursos': cursos,
        'curso_id': curso_id,
        'curso_seleccionado': curso_seleccionado,
        'q': q,
        'estudiantes': estudiantes_rows,
        'anio': anio_sel,
        'anios_disponibles': anios_disponibles,
    })


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

def _curso_en_anio_puro(estudiante, anio):
    """
    SOLO mira el año pedido. Prioriza MA con curso != NULL.
    Retorna (texto, curso_obj) o ("Sin curso (anio)", None).
    """
    ma = (MatriculaAnual.objects
          .filter(estudiante=estudiante, anio=anio)
          .select_related('curso')
          .order_by('-curso__isnull', '-id')
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
    Rol: usa `user.rol` si existe, si no, toma nombres de grupos.
    """
    # Nombre
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
            # Último intento sin exponer username (que podría ser RUT)
            email = getattr(u, 'email', '') or ''
            if email:
                name = email

    if not name:
        name = 'Usuario interno'

    # Rol
    role = None
    if u:
        # 1) Atributo directo
        raw_rol = getattr(u, 'rol', None)
        if raw_rol:
            role = str(raw_rol).replace('_', ' ').title()

        # 2) Si no hay, usar grupos
        if not role:
            try:
                groups = list(u.groups.all())
                if groups:
                    names = [g.name for g in groups if getattr(g, 'name', None)]
                    if names:
                        # Puedes elegir solo el primero o listarlos todos
                        role = ', '.join(sorted(set(names)))
            except Exception:
                pass

    return f"{name} ({role})" if role else name

@role_required(*PERMITIDOS_TODOS)
def imprimir_estudiante(request, rut):
    """
    Imprime la ficha del estudiante para un AÑO ESPECÍFICO.
    Respeta ?anio=AAAA para forzar el año visualizado.
    - Busca SOLO MatriculaAnual(estudiante, anio_view) priorizando curso asignado.
    - Si no hay, intenta Curso "Por Asignar" de ese mismo año (solo visual).
    - No usa estudiante.curso para evitar mezclar años.
    """
    # --- 1) Año forzado por querystring ---
    try:
        anio_forzado = int(request.GET.get('anio')) if request.GET.get('anio') else None
    except (TypeError, ValueError):
        anio_forzado = None

    # Año a visualizar (si no viene, usamos el año calendario)
    anio_view = anio_forzado or timezone.now().year

    # --- 2) Buscar estudiante por RUT ---
    rut_param = _norm_rut(rut)
    base_est = Estudiante.objects.select_related('curso', 'matriculado_por')
    estudiante = base_est.filter(rut=rut_param).first()
    if not estudiante and rut_param.isdigit():
        estudiante = base_est.filter(rut__startswith=rut_param).order_by('rut').first()
    if not estudiante:
        raise Http404("Estudiante no encontrado con el RUT indicado.")

    # --- 3) Traer SOLO la matrícula del año solicitado (priorizando curso asignado) ---
    ma = (
        MatriculaAnual.objects
        .filter(estudiante=estudiante, anio=anio_view)
        .select_related('curso')
        .order_by('-curso__isnull', '-id')  # curso NO nulo primero, luego la más nueva
        .first()
    )
    curso_view = getattr(ma, 'curso', None)

    # Si no hay matrícula y existe placeholder "Por Asignar" de ese año, úsalo SOLO para el título/subtítulo
    if not curso_view:
        placeholder = Curso.objects.filter(anio=anio_view, nivel=NIVEL_POR_ASIGNAR, letra=None).first()
        if placeholder:
            curso_view = placeholder  # OJO: sigue significando "sin curso asignado", es solo para la visual.

    # --- 4) Permisos por nivel (derivados del curso del AÑO solicitado) ---
    nivel_perm = _nivel_from_curso_obj(curso_view)
    if nivel_perm is None:
        # Sin curso “real” (o solo placeholder): exigir permisos amplios
        if not (
            getattr(request.user, 'rol', None) == 'ADMINISTRADOR'
            or has_perm_on_nivel(request.user, 'print', 'BASICA')
            or has_perm_on_nivel(request.user, 'print', 'MEDIA')
        ):
            raise PermissionDenied("Estudiante sin curso asignado en este año: no tienes permisos para imprimir.")
    else:
        if not has_perm_on_nivel(request.user, 'print', nivel_perm):
            raise PermissionDenied(f"No tienes permiso para imprimir estudiantes de {nivel_perm.title()}.")

    # --- 5) Título con año resuelto ---
    cfg = ConfiguracionMatricula.objects.filter(activa=True).first()
    if cfg and hasattr(cfg, 'titulo_resuelto'):
        try:
            titulo_matricula = cfg.titulo_resuelto(anio=anio_view)
        except TypeError:
            titulo_matricula = (cfg.titulo_resuelto() or '').replace('{anio}', str(anio_view)).strip()
            if not titulo_matricula:
                titulo_matricula = f"FICHA DE INSCRIPCIÓN DE MATRÍCULA {anio_view}"
    else:
        titulo_matricula = f"FICHA DE INSCRIPCIÓN DE MATRÍCULA {anio_view}"

    # ------- 6) Generación PDF -------
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30
    )
    elements = []
    styles = getSampleStyleSheet()

    label_style   = ParagraphStyle('label', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=9, leading=11)
    value_style   = ParagraphStyle('value', parent=styles['Normal'], fontName='Helvetica', fontSize=9, leading=11)
    section_style = ParagraphStyle('section', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=10,
                                   textColor=colors.HexColor('#374151'), spaceBefore=6, spaceAfter=4)

    def section_title(texto):
        title = Table([[Paragraph(texto, section_style)]], colWidths=[510])
        title.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f3f4f6')),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('BOX', (0, 0), (-1, -1), 0.25, colors.HexColor('#e5e7eb')),
        ]))
        return title

    def make_info_table(pairs, col_widths=(170, 340)):
        data = [
            [Paragraph(str(k), label_style),
             Paragraph('N/A' if v in [None, ''] else str(v), value_style)]
            for k, v in pairs
        ]
        t = Table(data, colWidths=list(col_widths), hAlign='LEFT')
        t.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ('LINEBELOW', (0, 0), (-1, -2), 0.25, colors.HexColor('#e5e7eb')),
        ]))
        return t

    # Encabezado (logo + datos)
    logo_path = './app/static/app/img/logo.png'
    header_text = Paragraph(
        "LICEO MUNICIPAL DE SAN PEDRO<br/>"
        "R.B.D 10854-5. Carretera de la Fruta km 9.5, San Pedro de Melipilla.<br/>"
        "Provincia de Melipilla, Región Metropolitana.",
        ParagraphStyle('hdr', parent=styles['Normal'], alignment=1, fontSize=8, leading=10)
    )
    if os.path.exists(logo_path):
        header_tbl = Table([[Image(logo_path, width=40, height=40), header_text]], colWidths=[50, 460])
    else:
        header_tbl = Table([[header_text]], colWidths=[510])
    header_tbl.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    elements.append(header_tbl)

    # Título + subtítulo (SIEMPRE con anio_view)
    elements.append(Paragraph(
        titulo_matricula,
        ParagraphStyle('t', parent=styles['Heading1'], alignment=1, fontSize=14, spaceAfter=4)
    ))
    if curso_view:
        sub_txt = (
            f"{curso_view.nivel} {curso_view.letra or ''} ({anio_view})"
            if curso_view.nivel != NIVEL_POR_ASIGNAR else f"Por Asignar ({anio_view})"
        )
        elements.append(Paragraph(
            sub_txt.strip(),
            ParagraphStyle('st', parent=styles['Heading3'], alignment=1, fontSize=11, spaceAfter=6)
        ))
    else:
        elements.append(Paragraph(
            f"Sin curso asignado ({anio_view})",
            ParagraphStyle('st', parent=styles['Heading3'], alignment=1, fontSize=11, spaceAfter=6)
        ))

    # I. Estudiante (curso mostrado SOLO del año solicitado)
    if curso_view and curso_view.nivel != NIVEL_POR_ASIGNAR:
        curso_display = f"{curso_view.nivel} {curso_view.letra or ''} - {anio_view}".strip()
    else:
        curso_display = f"Sin curso - {anio_view}"

    estado_view = getattr(ma, 'estado', None) or estudiante.estado or 'Activo'

    elements.append(Paragraph("I. Datos del Estudiante", section_style))
    estudiante_pairs = [
        ('RUT', estudiante.rut),
        ('Curso', curso_display),
        ('Nombre Completo', estudiante.nombre_completo),
        ('Fecha de Nacimiento', estudiante.fecha_nacimiento.strftime('%d-%m-%Y') if estudiante.fecha_nacimiento else 'N/A'),
        ('Sexo', estudiante.sexo),
        ('Nacionalidad', estudiante.nacionalidad),
        ('Domicilio', estudiante.domicilio),
        ('Comuna', estudiante.comuna),
        ('Ascendencia Indígena', 'Sí' if estudiante.ascendencia_indigena else 'No'),
        ('Etnia', estudiante.etnia if estudiante.ascendencia_indigena and estudiante.etnia else 'N/A'),
        ('¿Ha Repetido Curso?', 'Sí' if estudiante.ha_repetido_curso else 'No'),
        ('Pertenece al PIE', 'Sí' if estudiante.pertenece_al_pie else 'No'),
        ('Estado', estado_view),
        ('Establecimiento de procedencia', estudiante.de_donde_viene),
    ]
    elements.append(KeepTogether(make_info_table(estudiante_pairs)))
    elements.append(Spacer(1, 10))

    # II. Padres
    elements.append(section_title("II. Información de los Padres"))
    padres_pairs = [
        ('Nombre de la Madre', estudiante.nombre_madre or 'N/A'),
        ('RUT de la Madre', estudiante.rut_madre or 'N/A'),
        ('Teléfono de la Madre', estudiante.telefono_madre or 'N/A'),
        ('Email de la Madre', estudiante.email_madre or 'N/A'),
        ('Nivel Educacional Madre', estudiante.nivel_educacional_madre or 'N/A'),
        ('Ocupación de la Madre', estudiante.ocupacion_madre or 'N/A'),
        ('Nombre del Padre', estudiante.nombre_padre or 'N/A'),
        ('RUT del Padre', estudiante.rut_padre or 'N/A'),
        ('Teléfono del Padre', estudiante.telefono_padre or 'N/A'),
        ('Email del Padre', estudiante.email_padre or 'N/A'),
        ('Nivel Educacional Padre', estudiante.nivel_educacional_padre or 'N/A'),
        ('Ocupación del Padre', estudiante.ocupacion_padre or 'N/A'),
    ]
    elements.append(KeepTogether(make_info_table(padres_pairs)))
    elements.append(Spacer(1, 10))

    # III. Apoderado Titular
    elements.append(section_title("III. Apoderado (Titular)"))
    apoderado_pairs = [
        ('Nombre del Apoderado', estudiante.nombre_apoderado or 'N/A'),
        ('RUT del Apoderado', estudiante.rut_apoderado or 'N/A'),
        ('Teléfono del Apoderado', estudiante.telefono_apoderado or 'N/A'),
        ('Email del Apoderado', estudiante.email_apoderado or 'N/A'),
        ('Nivel Educacional Apoderado', estudiante.nivel_educacional_apoderado or 'N/A'),
        ('Ocupación del Apoderado', estudiante.ocupacion_apoderado or 'N/A'),
        ('Fecha nacimiento apoderado', estudiante.fecha_nacimiento_apoderado.strftime('%d-%m-%Y') if getattr(estudiante, 'fecha_nacimiento_apoderado', None) else 'N/A'),
    ]
    elements.append(KeepTogether(make_info_table(apoderado_pairs)))
    elements.append(Spacer(1, 40))

    # IV. Apoderado Suplente
    elements.append(section_title("IV. Apoderado Suplente"))
    apoderado_suplente_pairs = [
        ('Nombre del Apoderado Suplente', estudiante.nombre_apoderado_suplente or 'N/A'),
        ('RUT del Apoderado Suplente', estudiante.rut_apoderado_suplente or 'N/A'),
        ('Parentesco con el estudiante', estudiante.parentesco_apoderado_suplente or 'N/A'),
        ('Teléfono del Apoderado Suplente', estudiante.telefono_apoderado_suplente or 'N/A'),
        ('Email del Apoderado Suplente', estudiante.email_apoderado_suplente or 'N/A'),
        ('Nivel Educacional Apoderado Suplente', estudiante.nivel_educacional_apoderado_suplente or 'N/A'),
        ('Ocupación del Apoderado Suplente', estudiante.ocupacion_apoderado_suplente or 'N/A'),
    ]
    elements.append(KeepTogether(make_info_table(apoderado_suplente_pairs)))
    elements.append(Spacer(1, 10))

    # V. Salud
    elements.append(section_title("V. Salud y Antecedentes"))
    salud_pairs = [
        ('Vive con', estudiante.vive_con or 'N/A'),
        ('Resolución Judicial', 'Sí' if estudiante.resolucion_judicial else 'No'),
        ('Problemas de Salud', 'Sí' if estudiante.problemas_salud else 'No'),
        ('Descripción', estudiante.descripcion_problemas_salud if estudiante.problemas_salud else 'N/A'),
        ('Impide Educación Física', 'Sí' if estudiante.impide_educacion_fisica else 'No'),
        ('Certificado médico', 'Sí' if getattr(estudiante, 'certificado_medico', False) else 'No'),
        ('Sistema de salud', estudiante.sistema_salud or 'N/A'),
        ('Seguro complementario', estudiante.seguro_salud_complementario or 'N/A'),
    ]
    elements.append(KeepTogether(make_info_table(salud_pairs)))
    elements.append(Spacer(1, 10))

    # VI. Transporte/Consentimientos + Quién matricula
    elements.append(section_title("VI. Transporte, Consentimientos y Matrícula"))
    transporte_pairs = [
        ('Tipo de Transporte', estudiante.tipo_transporte or 'N/A'),
        ('Opta por clases de religión', 'Sí' if estudiante.opta_religion else 'No'),
        ('Acepta Fotografías y Videos', 'Sí' if estudiante.acepta_fotografias else 'No'),
        ('Fecha de Matrícula', estudiante.fecha_matricula.strftime('%d-%m-%Y') if estudiante.fecha_matricula else 'N/A'),
        ('Matriculado por', _nombre_y_rol_matriculador(estudiante)),
    ]
    elements.append(KeepTogether(make_info_table(transporte_pairs)))
    elements.append(Spacer(1, 10))

    # VII. Firmas
    firma_path = None
    firma_config = ConfiguracionFirma.objects.filter(activa=True).order_by('-id').first()
    if firma_config and getattr(firma_config, 'imagen_firma', None):
        try:
            firma_path = firma_config.imagen_firma.path
        except Exception:
            firma_path = os.path.join(settings.MEDIA_ROOT, getattr(firma_config.imagen_firma, 'name', ''))
    if not firma_path or not os.path.exists(firma_path):
        firma_path = './app/static/app/img/firma_director.jpeg'

    firma_director_img = Image(firma_path, width=100, height=50) if os.path.exists(firma_path) else None
    p_normal = ParagraphStyle('p_normal', parent=styles['Normal'], fontSize=9)

    LEFT_COL, RIGHT_COL = 200, 310
    LABEL_W, LINE_W = 150, RIGHT_COL - 150
    ROW_H = 14

    def _sign_row(label_text):
        hr = HRFlowable(width='100%', thickness=1, spaceBefore=0, spaceAfter=0, color=colors.black)
        t = Table([[Paragraph(label_text, p_normal), hr]],
                  colWidths=[LABEL_W, LINE_W], rowHeights=[ROW_H], hAlign='LEFT')
        t.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))
        return t

    right_block = Table(
        [[_sign_row("Firma Apoderado Titular:")],
         [Spacer(1, 10)],
         [_sign_row("Firma Apoderado Suplente:")]],
        colWidths=[RIGHT_COL], rowHeights=[ROW_H, 10, ROW_H], hAlign='LEFT'
    )
    right_block.setStyle(TableStyle([
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))

    left_label = Paragraph("Firma Director:", p_normal)
    if firma_director_img:
        left_cell = Table([[left_label, firma_director_img]], colWidths=[80, 120], rowHeights=[ROW_H])
        left_cell.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 10),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))
    else:
        left_cell = _sign_row("Firma Director:")

    firmas_tbl = Table([[left_cell, right_block]], colWidths=[LEFT_COL, RIGHT_COL], hAlign='LEFT')
    firmas_tbl.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
    ]))

    elements.append(Spacer(1, 24))
    elements.append(firmas_tbl)

    # Build
    doc.build(elements)
    buffer.seek(0)

    # Respuesta con cabeceras de depuración y no-cache
    response = FileResponse(buffer, as_attachment=True, filename=f'ficha_estudiante_{estudiante.rut}.pdf')
    response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response['Pragma'] = 'no-cache'
    response['Expires'] = '0'
    response['X-Anio-Usado'] = str(anio_view)
    response['X-Anio-Forzado'] = str(anio_forzado) if anio_forzado else ''
    response['X-TieneMatricula'] = '1' if ma else '0'
    return response


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
    cursos = Curso.objects.filter(anio__in=anios_visibles).order_by('anio', 'nivel', 'letra')

    ctx = {
        'anios_visibles': anios_visibles,   # --> usado en el <select id="anioVisible">
        'anio_actual': anio_actual,         # --> marca la opción selected
        'cursos': cursos,                   # --> options con data-anio
    }
    return render(request, 'app/ficha_estudiante.html', ctx)

from django.views.decorators.http import require_GET, require_POST

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

    estudiantes = (
        Estudiante.objects
        .select_related('curso')
        .filter(rut__icontains=rut)
    )

    data = []
    for e in estudiantes:
        curso_texto, curso_obj, tiene = _curso_en_anio(e, anio_usado)

        data.append({
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

    resp = JsonResponse(data, safe=False)
    resp['X-Anio-Usado'] = str(anio_usado)
    resp['X-Anios-Visibles'] = ",".join(str(a) for a in visibles)
    resp['X-Anio-Fallback'] = '1' if fallback else '0'
    return resp

def _curso_en_anio(estudiante, anio):
    """
    Retorna (curso_texto, curso_obj, tiene_matricula_en_anio)
    - Busca SOLO MatriculaAnual del año dado.
    - Prioriza curso asignado y distinto del placeholder ("Por Asignar").
    """
    ma = (
        MatriculaAnual.objects
        .filter(estudiante=estudiante, anio=anio)
        .select_related('curso')
        .order_by('-curso__isnull', '-id')  # curso no nulo primero, luego la última
        .first()
    )
    curso_obj = getattr(ma, 'curso', None)

    if curso_obj and getattr(curso_obj, 'nivel', None) != NIVEL_POR_ASIGNAR:
        curso_texto = f"{curso_obj.nivel}{' ' + curso_obj.letra if curso_obj.letra else ''} - {anio}"
        return (curso_texto, curso_obj, True)
    return (f"Sin curso asignado ({anio})", None, False)

@require_POST
@role_required(*PERMITIDOS_TODOS)
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

    e = get_object_or_404(Estudiante, rut=rut)
    curso = get_object_or_404(Curso, id=curso_id)

    # Validaciones de consistencia
    if curso.anio != anio:
        return JsonResponse({'ok': False, 'error': 'El curso no pertenece al año seleccionado.'}, status=400)
    if getattr(curso, 'nivel', None) == NIVEL_POR_ASIGNAR:
        return JsonResponse({'ok': False, 'error': 'No puedes asignar el placeholder "Por Asignar" como curso.'}, status=400)

    # Evita duplicados y asigna
    ma, _ = MatriculaAnual.objects.update_or_create(
        estudiante=e, anio=anio, defaults={'curso': curso}
    )

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
    }
    return render(request, 'app/detalles_estudiante.html', ctx)

@role_required(*PERMITIDOS_TODOS)
def descargar_documento(request, documento_id):
    documento = get_object_or_404(DocumentoEstudiante, id=documento_id)
    response = FileResponse(documento.archivo, as_attachment=True, filename=documento.nombre)
    return response


# Asegúrate de tener estas constantes (o impórtalas desde donde las definas)
ADMIN      = 'ADMINISTRADOR'
DIRECTIVO  = 'DIRECTIVO'
PROFESOR   = 'PROFESOR'
ASISTENTE  = 'ASISTENTE'
ROL_CHOICES = {ADMIN, DIRECTIVO, PROFESOR, ASISTENTE}

# from .models import Usuarios
# from .decorators import role_required

def _norm_run(run: str) -> str:
    if not run:
        return ''
    return ''.join(ch for ch in run if ch.isalnum()).upper()

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
            usuario.password = password

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
        return redirect('app/listar_usuarios.html')
    
    return render(request, "app/perfil_usuario.html", {"usuario": usuario})

@role_required(*PERMITIDOS_TODOS)
def listar_estudiantes_admin_view(request):
    # Solo el administrador puede acceder a esta vista
    if not request.user.is_staff and request.user.rol not in ['ADMINISTRADOR', 'DIRECTIVO']:
        messages.error(request, "No tienes permiso para crear usuarios.")
        return redirect("home")
    
    # Listar todos los estudiantes
    estudiantes = Estudiante.objects.all()

    # Filtrar los cursos para enseñanza básica y media
    cursos_basica = ['Prekinder', 'Kinder'] + [f"{i}° Básico" for i in range(1, 9)]
    cursos_media = [f"{i}° Medio" for i in range(1, 5)]

    # Renderizar la plantilla con los estudiantes y los cursos
    return render(request, "app/estudiantes_admin.html", {
        "estudiantes": estudiantes,
        "cursos_basica": cursos_basica,
        "cursos_media": cursos_media,
    })

# def _fmt_date(val, default='—'):
#     """
#     Acepta date/datetime o string 'YYYY-MM-DD' y devuelve 'DD-MM-YYYY'.
#     Si viene None o no se puede parsear, devuelve default.
#     """
#     if isinstance(val, (date, datetime)):
#         return val.strftime('%d-%m-%Y')
#     if isinstance(val, str) and val.strip():
#         for fmt in ('%Y-%m-%d', '%d-%m-%Y', '%Y/%m/%d'):
#             try:
#                 return datetime.strptime(val, fmt).strftime('%d-%m-%Y')
#             except ValueError:
#                 pass
#     return default

def _fmt_date(d):
    try:
        return d.strftime('%d-%m-%Y') if d else 'N/A'
    except Exception:
        return 'N/A'

def imprimir_estudiantes_por_curso(request, curso_id):
    # === 1) Resolver el curso (y por ende el año visualizado) ===
    curso = get_object_or_404(Curso, pk=curso_id)
    anio_view = int(getattr(curso, 'anio', timezone.now().year))

    # === 2) Título dinámico (sin hardcode), respetando el año del curso ===
    cfg = ConfiguracionMatricula.objects.filter(activa=True).first()
    if cfg and hasattr(cfg, 'titulo_resuelto'):
        try:
            titulo_matricula = cfg.titulo_resuelto(anio=anio_view)
        except TypeError:
            # compatibilidad si tu método no acepta kwargs
            titulo_matricula = (cfg.titulo_resuelto() or '').replace('{anio}', str(anio_view)).strip()
            if not titulo_matricula:
                titulo_matricula = f"FICHA DE INSCRIPCIÓN DE MATRÍCULA {anio_view}"
    else:
        titulo_matricula = f"FICHA DE INSCRIPCIÓN DE MATRÍCULA {anio_view}"

    # === 3) Traer SOLO matrículas del curso (y por ende de ese año) ===
    ma_list = (
        MatriculaAnual.objects
        .filter(curso=curso)
        .filter(Q(estado="Activo") | Q(estado__isnull=True))
        .select_related('estudiante', 'curso')
        .order_by('estudiante__nombre_completo')
    )

    # === 4) Preparación PDF ===
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30
    )
    elements = []
    styles = getSampleStyleSheet()

    # Estilos
    label_style   = ParagraphStyle('label', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=9, leading=11)
    value_style   = ParagraphStyle('value', parent=styles['Normal'], fontName='Helvetica', fontSize=9, leading=11)
    section_style = ParagraphStyle('section', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=10,
                                   textColor=colors.HexColor('#374151'), spaceBefore=6, spaceAfter=4)

    def make_info_table(pairs, col_widths=(170, 340)):
        data = [
            [Paragraph(str(k), label_style),
             Paragraph('N/A' if v in [None, ''] else str(v), value_style)]
            for k, v in pairs
        ]
        t = Table(data, colWidths=list(col_widths), hAlign='LEFT', spaceAfter=8)
        t.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ('LINEBELOW', (0, 0), (-1, -2), 0.25, colors.HexColor('#e5e7eb')),
        ]))
        return t

    def section_title(texto):
        title = Table([[Paragraph(texto, section_style)]], colWidths=[510])
        title.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f3f4f6')),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('BOX', (0, 0), (-1, -1), 0.25, colors.HexColor('#e5e7eb')),
        ]))
        return title

    def agregar_encabezado():
        logo_path = './app/static/app/img/logo.png'
        header_text = Paragraph(
            "LICEO MUNICIPAL DE SAN PEDRO<br/>"
            "R.B.D 10854-5. Carretera de la Fruta km 9.5, San Pedro de Melipilla.<br/>"
            "Provincia de Melipilla, Región Metropolitana.",
            ParagraphStyle('hdr', parent=styles['Normal'], alignment=1, fontSize=8, leading=10)
        )
        if os.path.exists(logo_path):
            header_tbl = Table([[Image(logo_path, width=40, height=40), header_text]], colWidths=[50, 460])
        else:
            header_tbl = Table([[header_text]], colWidths=[510])
        header_tbl.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ]))
        elements.append(header_tbl)

        title = Paragraph(
            f"{titulo_matricula} - {curso.nivel} {curso.letra or ''} ({anio_view})",
            ParagraphStyle('t', parent=styles['Heading1'], alignment=1, fontSize=13, spaceAfter=6)
        )
        elements.append(title)

    # === 5) Construcción por alumno (SIEMPRE desde MA del curso/año) ===
    total = ma_list.count()
    for idx, ma in enumerate(ma_list, start=1):
        estudiante = ma.estudiante
        agregar_encabezado()

        # I. Estudiante
        elements.append(section_title("I. Datos del Estudiante"))
        estudiante_pairs = [
            ('RUT', estudiante.rut),
            ('Curso', f"{ma.curso.nivel} {ma.curso.letra or ''} - {ma.curso.anio}"),
            ('Nombre Completo', estudiante.nombre_completo),
            ('Fecha de Nacimiento', _fmt_date(estudiante.fecha_nacimiento)),
            ('Sexo', estudiante.sexo),
            ('Nacionalidad', estudiante.nacionalidad),
            ('Domicilio', estudiante.domicilio),
            ('Comuna', estudiante.comuna),
            ('Ascendencia Indígena', 'Sí' if estudiante.ascendencia_indigena else 'No'),
            ('¿Ha Repetido Curso?', 'Sí' if estudiante.ha_repetido_curso else 'No'),
            ('Pertenece al PIE', 'Sí' if estudiante.pertenece_al_pie else 'No'),
            ('Estado', ma.estado or estudiante.estado or 'Activo'),
            ('Establecimiento de procedencia', estudiante.de_donde_viene),
        ]
        elements.append(KeepTogether(make_info_table(estudiante_pairs)))
        elements.append(Spacer(1, 10))

        # II. Padres
        elements.append(section_title("II. Información de los Padres"))
        padres_pairs = [
            ('Nombre de la Madre', estudiante.nombre_madre or 'N/A'),
            ('RUT de la Madre', estudiante.rut_madre or 'N/A'),
            ('Teléfono de la Madre', estudiante.telefono_madre or 'N/A'),
            ('Email de la Madre', estudiante.email_madre or 'N/A'),
            ('Nivel Educacional Madre', estudiante.nivel_educacional_madre or 'N/A'),
            ('Ocupación de la Madre', estudiante.ocupacion_madre or 'N/A'),
            ('Nombre del Padre', estudiante.nombre_padre or 'N/A'),
            ('RUT del Padre', estudiante.rut_padre or 'N/A'),
            ('Teléfono del Padre', estudiante.telefono_padre or 'N/A'),
            ('Email del Padre', estudiante.email_padre or 'N/A'),
            ('Nivel Educacional Padre', estudiante.nivel_educacional_padre or 'N/A'),
            ('Ocupación del Padre', estudiante.ocupacion_padre or 'N/A'),
        ]
        elements.append(KeepTogether(make_info_table(padres_pairs)))
        elements.append(Spacer(1, 10))

        # III. Apoderado Titular
        elements.append(section_title("III. Apoderado (Titular)"))
        apoderado_pairs = [
            ('Nombre del Apoderado', estudiante.nombre_apoderado or 'N/A'),
            ('RUT del Apoderado', estudiante.rut_apoderado or 'N/A'),
            ('Teléfono del Apoderado', estudiante.telefono_apoderado or 'N/A'),
            ('Email del Apoderado', estudiante.email_apoderado or 'N/A'),
            ('Nivel Educacional Apoderado', estudiante.nivel_educacional_apoderado or 'N/A'),
            ('Ocupación del Apoderado', estudiante.ocupacion_apoderado or 'N/A'),
            ('Fecha de Nacimiento apoderado', _fmt_date(getattr(estudiante, 'fecha_nacimiento_apoderado', None))),
        ]
        elements.append(KeepTogether(make_info_table(apoderado_pairs)))
        elements.append(Spacer(1, 40))

        # IV. Apoderado Suplente
        elements.append(section_title("IV. Apoderado Suplente"))
        suplente_pairs = [
            ('Nombre del Apoderado Suplente', estudiante.nombre_apoderado_suplente or 'N/A'),
            ('RUT del Apoderado Suplente', estudiante.rut_apoderado_suplente or 'N/A'),
            ('Teléfono del Apoderado Suplente', estudiante.telefono_apoderado_suplente or 'N/A'),
            ('Email del Apoderado Suplente', estudiante.email_apoderado_suplente or 'N/A'),
            ('Nivel Educacional Apoderado Suplente', estudiante.nivel_educacional_apoderado_suplente or 'N/A'),
            ('Ocupación del Apoderado Suplente', estudiante.ocupacion_apoderado_suplente or 'N/A'),
            ('Parentesco con el Estudiante', estudiante.parentesco_apoderado_suplente or 'N/A'),
        ]
        elements.append(KeepTogether(make_info_table(suplente_pairs)))
        elements.append(Spacer(1, 10))

        # V. Salud y Antecedentes
        elements.append(section_title("V. Salud y Antecedentes"))
        salud_pairs = [
            ('Vive con', estudiante.vive_con or 'N/A'),
            ('Resolución Judicial', 'Sí' if estudiante.resolucion_judicial else 'No'),
            ('Problemas de Salud', 'Sí' if estudiante.problemas_salud else 'No'),
            ('Descripción', estudiante.descripcion_problemas_salud if estudiante.problemas_salud else 'N/A'),
            ('Impide Educación Física', 'Sí' if estudiante.impide_educacion_fisica else 'No'),
            ('Certificado médico', 'Sí' if getattr(estudiante, 'certificado_medico', False) else 'No'),
            ('Sistema de salud', estudiante.sistema_salud or 'N/A'),
            ('Seguro de Salud Complementario', estudiante.seguro_salud_complementario or 'N/A'),
        ]
        elements.append(KeepTogether(make_info_table(salud_pairs)))
        elements.append(Spacer(1, 10))

        # VI. Transporte y Consentimientos
        elements.append(section_title("VI. Transporte y Consentimientos"))
        transporte_pairs = [
            ('Tipo de Transporte', estudiante.tipo_transporte or 'N/A'),
            ('Opta por clases de religión', 'Sí' if estudiante.opta_religion else 'No'),
            ('Acepta Fotografías y Videos', 'Sí' if estudiante.acepta_fotografias else 'No'),
            ('Fecha de Matrícula', _fmt_date(estudiante.fecha_matricula)),
        ]
        elements.append(KeepTogether(make_info_table(transporte_pairs)))
        elements.append(Spacer(1, 10))

        # VII. Firmas
        firma_path = None
        firma_config = ConfiguracionFirma.objects.filter(activa=True).order_by('-id').first()
        if firma_config and getattr(firma_config, 'imagen_firma', None):
            try:
                firma_path = firma_config.imagen_firma.path
            except Exception:
                firma_path = os.path.join(settings.MEDIA_ROOT, getattr(firma_config.imagen_firma, 'name', ''))
        if not firma_path or not os.path.exists(firma_path):
            firma_path = './app/static/app/img/firma_director.jpeg'

        firma_director_img = Image(firma_path, width=100, height=50) if os.path.exists(firma_path) else None
        p_normal = ParagraphStyle('p_normal', parent=styles['Normal'], fontSize=9)

        LEFT_COL, RIGHT_COL = 200, 310
        LABEL_W, LINE_W = 150, RIGHT_COL - 150
        ROW_H = 14

        def _sign_row(label_text):
            hr = HRFlowable(width='100%', thickness=1, spaceBefore=0, spaceAfter=0, color=colors.black)
            t = Table([[Paragraph(label_text, p_normal), hr]],
                      colWidths=[LABEL_W, LINE_W], rowHeights=[ROW_H], hAlign='LEFT')
            t.setStyle(TableStyle([
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('LEFTPADDING', (0, 0), (-1, -1), 0),
                ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                ('TOPPADDING', (0, 0), (-1, -1), 0),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
            ]))
            return t

        right_block = Table(
            [[_sign_row("Firma Apoderado Titular:")],
             [Spacer(1, 10)],
             [_sign_row("Firma Apoderado Suplente:")]],
            colWidths=[RIGHT_COL], rowHeights=[ROW_H, 10, ROW_H], hAlign='LEFT'
        )
        right_block.setStyle(TableStyle([
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))

        left_label = Paragraph("Firma Director:", p_normal)
        if firma_director_img:
            left_cell = Table([[left_label, firma_director_img]], colWidths=[80, 120], rowHeights=[ROW_H])
            left_cell.setStyle(TableStyle([
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('LEFTPADDING', (0, 0), (-1, -1), 0),
                ('RIGHTPADDING', (0, 0), (-1, -1), 10),
                ('TOPPADDING', (0, 0), (-1, -1), 0),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
            ]))
        else:
            left_cell = _sign_row("Firma Director:")

        firmas_tbl = Table([[left_cell, right_block]], colWidths=[LEFT_COL, RIGHT_COL], hAlign='LEFT')
        firmas_tbl.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ]))

        elements.append(Spacer(1, 24))
        elements.append(firmas_tbl)

        # Salto de página entre estudiantes
        if idx < total:
            elements.append(PageBreak())

    # === 6) Construcción y respuesta ===
    doc.build(elements)
    buffer.seek(0)

    nombre_pdf = f"listado_estudiantes_{curso.nivel}_{curso.letra or ''}_{anio_view}.pdf".replace(' ', '_')
    response = FileResponse(buffer, as_attachment=True, filename=nombre_pdf)
    # Cabeceras útiles
    response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response['Pragma'] = 'no-cache'
    response['Expires'] = '0'
    response['X-Anio-Usado'] = str(anio_view)
    response['X-Curso-Id'] = str(curso.id)
    return response

def _str_to_bool(val):
    """
    Convierte entradas del formulario a booleano.
    Acepta: 'si', 'sí', 'true', '1', 'on' => True
            'no', 'false', '0', 'off'     => False
    Cualquier otra cosa => False
    """
    if val is None:
        return False
    v = str(val).strip().lower()
    return v in ('si', 'sí', 'true', '1', 'on')

def _parse_date(val):
    """
    Acepta 'YYYY-MM-DD' (input type=date) o 'DD-MM-YYYY'.
    Devuelve date o None si no pudo parsear.
    """
    if not val:
        return None
    val = val.strip()
    for fmt in ('%Y-%m-%d', '%d-%m-%Y'):
        try:
            return datetime.strptime(val, fmt).date()
        except ValueError:
            continue
    return None


@transaction.atomic
def registro_alumno_view(request):
    """
    Permite a un apoderado registrar varios estudiantes.
    AHORA incluye: fecha_nacimiento_apoderado y fecha_nacimiento_apoderado_suplente (opcionales).
    """
    # 0) Apoderado desde sesión
    apoderado_id = request.session.get('apoderado_id')
    apoderado = get_object_or_404(Apoderado, id=apoderado_id) if apoderado_id else None

    # 1) Años visibles
    anios_visibles = list(
        AcademicYear.objects.filter(is_visible=True)
        .order_by('anio').values_list('anio', flat=True)
    )
    if not anios_visibles:
        anios_visibles = list(
            Curso.objects.order_by('anio').values_list('anio', flat=True).distinct()
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
    ascendencia_indigena    = _str_to_bool(d.get('ascendencia_indigena'))
    pertenece_al_pie        = _str_to_bool(d.get('pertenece_al_pie'))
    ha_repetido_curso       = _str_to_bool(d.get('ha_repetido_curso'))
    resolucion_judicial     = _str_to_bool(d.get('resolucion_judicial'))
    problemas_salud         = _str_to_bool(d.get('problemas_salud'))
    impide_educacion_fisica = _str_to_bool(d.get('impide_educacion_fisica'))
    opta_religion           = _str_to_bool(d.get('opta_religion'))
    acepta_fotografias      = _str_to_bool(d.get('acepta_fotografias'))
    certificado_medico      = _str_to_bool(d.get('certificado_medico'))

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
            defaults={'curso': curso, 'estado': est.estado or 'Activo'}
        )

        imprimir_url = request.build_absolute_uri(f"/imprimir-estudiante/{est.rut}/")

        if is_ajax:
            keep_year = (submit_action == 'save_new')
            return JsonResponse({
                'ok': True,
                'id': est.id,
                'imprimir_url': imprimir_url,
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
    if request.method == 'POST':
        run = request.POST.get('run')
        nombre = request.POST.get('nombre')
        apellido = request.POST.get('apellido')
        email = request.POST.get('email')
        password = request.POST.get('password')

        # Validación básica
        if Apoderado.objects.filter(run=run).exists():
            return HttpResponse("Ya existe un apoderado con ese RUT")

        # Crear el apoderado
        apoderado = Apoderado.objects.create(
            run=run,
            nombre=nombre,
            apellido=apellido,
            email=email,
            password=password
        )

        # ✅ Guardar el ID del apoderado en la sesión
        request.session['apoderado_id'] = apoderado.id

        # Mensaje opcional
        messages.success(request, f"Bienvenido {apoderado.nombre}, continúa con el registro del estudiante.")

        # Redirigir al formulario de alumno
        return redirect('registro_alumno')

    return render(request, 'app/registro_apoderado.html')


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

@login_required
@xframe_options_exempt  # 👈 permite que esta vista se embeba en un <iframe>
def preview_ficha_pdf(request):
    """
    PDF liviano de PREVISUALIZACIÓN: logo + título configurado + firma activa (si existe).
    No descarga archivo; se renderiza embebido en un <iframe>.
    """
    buffer = BytesIO()
    styles = getSampleStyleSheet()

    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30
    )
    elements = []

    # ===== Configs activas
    cfg = ConfiguracionMatricula.objects.filter(activa=True).first()
    titulo_matricula = (cfg.titulo_resuelto() if cfg else "FICHA DE INSCRIPCIÓN DE MATRÍCULA 2025")

    firma_path = None
    cfg_firma = ConfiguracionFirma.objects.filter(activa=True).first()
    if cfg_firma and getattr(cfg_firma, 'imagen_firma', None):
        try:
            firma_path = cfg_firma.imagen_firma.path
        except Exception:
            firma_path = os.path.join(settings.MEDIA_ROOT, cfg_firma.imagen_firma.name)
    if not firma_path or not os.path.exists(firma_path):
        # fallback opcional a firma estática
        fallback = os.path.join(settings.BASE_DIR, 'app', 'static', 'app', 'img', 'firma_director.jpeg')
        firma_path = fallback if os.path.exists(fallback) else None

    # ===== Encabezado compacto (logo + texto)
    logo_path = os.path.join(settings.BASE_DIR, 'app', 'static', 'app', 'img', 'logo.png')
    logo = Image(logo_path, width=40, height=40) if os.path.exists(logo_path) else Paragraph('', styles['Normal'])

    header_text = Paragraph(
        "LICEO MUNICIPAL DE SAN PEDRO<br/>"
        "R.B.D 10854-5. Carretera de la Fruta km 9.5, San Pedro de Melipilla.<br/>"
        "Provincia de Melipilla, Región Metropolitana.",
        ParagraphStyle('hdr', parent=styles['Normal'], alignment=1, fontSize=8, leading=10)
    )
    header_tbl = Table([[logo, header_text]], colWidths=[50, 460])
    header_tbl.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    elements.append(header_tbl)

    # ===== Título configurado
    title = Paragraph(
        titulo_matricula,
        ParagraphStyle('t', parent=styles['Heading1'], alignment=1, fontSize=14, spaceAfter=6)
    )
    elements.append(title)

    # Subtítulo opcional
    if cfg and getattr(cfg, 'subtitulo', None):
        subtitle = Paragraph(
            cfg.subtitulo,
            ParagraphStyle('st', parent=styles['Normal'], alignment=1, fontSize=10, textColor=colors.HexColor('#374151'))
        )
        elements.append(subtitle)

    elements.append(Spacer(1, 24))

    # ===== Caja de demo + firma
    box_title = Paragraph("Previsualización del bloque de firma", ParagraphStyle(
        'box_t', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=10
    ))
    elements.append(box_title)
    elements.append(Spacer(1, 6))

    firma_img = Image(firma_path, width=120, height=60) if firma_path and os.path.exists(firma_path) else None
    firma_director = Paragraph("Firma Director:", styles['Normal'])

    if firma_img:
        row = [firma_director, firma_img]
        colw = [120, 200]
    else:
        row = [Paragraph("Firma Director: ___________________________", styles['Normal']), Paragraph("", styles['Normal'])]
        colw = [220, 100]

    firma_table = Table([row], colWidths=colw, hAlign='LEFT')
    firma_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('LEFTPADDING', (0, 0), (-1, -1), 2),
        ('RIGHTPADDING', (0, 0), (-1, -1), 2),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#e5e7eb')),
    ]))
    elements.append(firma_table)

    elements.append(Spacer(1, 18))
    foot = Paragraph(
        "Esto es una vista previa. La ficha completa incluye todos los datos del estudiante.",
        ParagraphStyle('f', parent=styles['Normal'], fontSize=8, textColor=colors.HexColor('#6b7280'))
    )
    elements.append(foot)

    # build
    doc.build(elements)
    pdf = buffer.getvalue()
    buffer.close()

    resp = HttpResponse(content_type='application/pdf')
    resp['Content-Disposition'] = 'inline; filename="preview_ficha.pdf"'
    resp.write(pdf)
    return resp

def error_403_view(request, exception=None):
    """
    Muestra una página de error elegante cuando el usuario intenta
    acceder a una vista sin permisos.
    """
    response = render(request, '403.html', status=403)
    response.status_code = 403
    return response

from django.core.exceptions import PermissionDenied

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

from django.views.decorators.csrf import csrf_protect

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
    
# nuevos datos para registros anuales

# --- IMPORTS NECESARIOS ---
from typing import Optional, Dict, Any

import json

from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST

# Ajusta estos imports según tu estructura
from .models import Estudiante, MatriculaAnual, Curso


# --- CAMPOS A SEMBRAR DESDE ESTUDIANTE A MATRICULAANUAL ---
# --- IMPORTS ---
from typing import Optional, Dict, Any
import json

from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST

# Ajusta según tu estructura de proyecto
from .models import Estudiante, MatriculaAnual, Curso

# Nombres exactos de los niveles especiales en tu BD
NIVEL_POR_ASIGNAR = "Por Asignar"
NIVEL_EGRESADOS   = "Egresados"

@login_required
def listar_estudiantes_anio(request):
    # Año seleccionado (GET ?anio=)
    try:
        anio = int(request.GET.get('anio', ''))
    except ValueError:
        anio = 0

    # Años disponibles (según MatriculaAnual)
    years = list(
        MatriculaAnual.objects.order_by('-anio')
        .values_list('anio', flat=True).distinct()
    )
    if not years:
        years = [timezone.now().year]
    if not anio:
        anio = years[0]

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
        Curso.objects.filter(anio=anio)
        .annotate(
            n_est=Count('matriculaanual', filter=Q(matriculaanual__anio=anio)),
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
    sin_nivel_count = MatriculaAnual.objects.filter(anio=anio, curso__isnull=True).count()

    # Query base por año
    qs = (
        MatriculaAnual.objects.filter(anio=anio)
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

    # Búsqueda libre
    if q:
        qs = qs.filter(
            Q(estudiante__nombre_completo__icontains=q) |
            Q(estudiante__rut__icontains=q) |
            Q(curso__nivel__icontains=q) |
            Q(curso__letra__icontains=q)
        )

    ctx = {
        'anio': anio,
        'years': years,
        'cursos': cursos,              # incluye Por Asignar y Egresados (creados si faltaban)
        'curso_id': curso_id,          # para marcar seleccionado en el select
        'curso_seleccionado': None,    # si lo usas en Alpine
        'q': q,
        'matriculas': qs,              # iteración sobre MatriculaAnual
        'sin_nivel_count': sin_nivel_count,  # para la opción "Sin nivel" (id = 'none')
    }
    return render(request, 'estudiantes/listar_estudiantes_anio.html', ctx)

# ---------------------------------------------------------------
#  HELPERS BÁSICOS
# ---------------------------------------------------------------
def is_admin(u) -> bool:
    return bool(u.is_superuser or u.is_staff)


# ---------------------------------------------------------------
#  SEED: Estudiante → MatriculaAnual (manual desde panel)
# ---------------------------------------------------------------
CAMPOS_SEED = [
    'curso', 'estado', 'fecha_matricula', 'nacionalidad', 'domicilio', 'comuna',
    'ascendencia_indigena', 'etnia', 'pertenece_al_pie', 'ha_repetido_curso', 'de_donde_viene',
    'nombre_madre', 'rut_madre', 'nivel_educacional_madre', 'ocupacion_madre', 'telefono_madre', 'email_madre',
    'nombre_padre', 'rut_padre', 'nivel_educacional_padre', 'ocupacion_padre', 'telefono_padre', 'email_padre',
    'nombre_apoderado', 'rut_apoderado', 'nivel_educacional_apoderado', 'ocupacion_apoderado', 'telefono_apoderado', 'email_apoderado',
    'nombre_apoderado_suplente', 'rut_apoderado_suplente', 'nivel_educacional_apoderado_suplente', 'ocupacion_apoderado_suplente',
    'telefono_apoderado_suplente', 'email_apoderado_suplente', 'vive_con', 'resolucion_judicial', 'problemas_salud',
    'descripcion_problemas_salud', 'impide_educacion_fisica', 'tipo_transporte', 'opta_religion',
    'certificado_medico', 'acepta_fotografias', 'sistema_salud'
]

@transaction.atomic
def seed_matricula_from_estudiantes(anio: int, only_activos: bool = True, overwrite: bool = False) -> Dict[str, Any]:
    qs = Estudiante.objects.select_related('curso')
    if only_activos:
        qs = qs.filter(estado='Activo')

    creados = actualizados = omitidos = 0
    hoy = timezone.now().date()

    for e in qs:
        ma, created = MatriculaAnual.objects.get_or_create(estudiante=e, anio=anio)
        if created or overwrite:
            for f in CAMPOS_SEED:
                if hasattr(e, f):
                    setattr(ma, f, getattr(e, f))
            if not ma.fecha_matricula:
                ma.fecha_matricula = hoy
            ma.save()
            if created:
                creados += 1
            else:
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


# ---------------------------------------------------------------
#  CLONADO AÑO A AÑO (A → B)
# ---------------------------------------------------------------
BASICA = ["1° Básico", "2° Básico", "3° Básico", "4° Básico", "5° Básico", "6° Básico", "7° Básico", "8° Básico"]
MEDIA  = ["1° Medio", "2° Medio", "3° Medio", "4° Medio"]

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
        if graduate_level:
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

        defaults = {'curso': curso_dest, 'anio': target_year, 'estado': e.estado or 'Activo'}
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

# ---------------------------------------------------------------
#  CLONAR CURSO → CURSO (AÑO A → AÑO B)
#   - Crea/actualiza MatriculaAnual en el curso destino (año destino)
#   - Opcional: mueve también Estudiante.curso al curso destino
# ---------------------------------------------------------------

from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required, user_passes_test
from django.views.decorators.http import require_POST
import json

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

        defaults = {
            'curso': target,
            'anio': target.anio,
            'estado': estado_final,
        }

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


# ---------------------------------------------------------------
#  PANEL DE AÑOS
# ---------------------------------------------------------------
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



from collections import defaultdict, namedtuple
from django.utils.text import slugify
import io
from collections import defaultdict
from django.forms.models import model_to_dict
# =========================
# HELPERS GENERALES
#Importacion y export de datos
# =========================
def _norm(text):
    return str(text).strip() if text is not None else ""


def _norm_rut(rut):
    return _norm(rut).replace(".", "").replace(" ", "").upper()


def _bool(v):
    return _norm(v).lower() in ("1", "true", "t", "si", "sí", "y", "yes", "on")


def _pick_int(v, default=None):
    s = _norm(v)
    try:
        return int(s) if s else default
    except Exception:
        return default


def _append_log(session, line):
    buff = session.get("import_log", [])
    buff.append(line)
    if len(buff) > 300:
        buff = buff[-300:]
    session["import_log"] = buff
    session.modified = True


def _norm_header(s: str) -> str:
    """Normaliza nombre de columna: sin acentos, lower, trim."""
    if s is None:
        return ""
    if not isinstance(s, str):
        s = str(s)
    s = s.strip().lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return s


def _sanitize_rows(rows):
    """
    Limpia filas leídas desde CSV/JSON:
    - elimina claves None o vacías,
    - recorta espacios en cabeceras y valores string,
    - ignora columnas extra (DictReader las deja en r[None]).
    """
    clean = []
    if not isinstance(rows, list):
        return clean
    for r in rows:
        if not isinstance(r, dict):
            continue
        d = {}
        for k, v in r.items():
            if k is None:
                continue
            ks = str(k).strip()
            if not ks:
                continue
            if isinstance(v, str):
                v = v.strip()
            d[ks] = v
        if any(v not in (None, "") for v in d.values()):
            clean.append(d)
    return clean


def _csv_to_datasets(uploaded_file, selected_datasets):
    """
    Soporta 2 formatos:
      1) CSV con columna 'dataset'/'_dataset'/'tabla'/'tipo' → separa por esa col.
      2) CSV 'seccionado' como el que exportas:
         líneas con '=== ESTUDIANTES ===', luego cabeceras, luego filas; y así por dataset.
    """
    raw = uploaded_file.read().decode("utf-8", errors="ignore")
    sio = io.StringIO(raw)
    sio.seek(0)

    data = defaultdict(list)

    # --- Formato 1: con columna dataset
    try:
        reader = csv.DictReader(sio)
        headers = [h.lower() for h in (reader.fieldnames or [])]
        ds_col = next((h for h in ("dataset", "_dataset", "tabla", "tipo") if h in headers), None)
        if ds_col:
            for r in reader:
                key = slugify(_norm(r.get(ds_col))).replace("-", "_")
                if key in ("alumno", "alumnos", "estudiante", "estudiantes"):
                    key = "estudiantes"
                elif key in ("curso", "cursos"):
                    key = "cursos"
                elif key in ("matricula", "matriculas", "matriculas_anual", "matricula_anual"):
                    key = "matriculas"
                elif key in ("usuario", "usuarios", "users"):
                    key = "usuarios"
                data[key].append(r)
            return dict(data)
    except Exception:
        pass  # cae al modo seccionado

    # --- Formato 2: CSV seccionado (=== DATASET ===)
    data.clear()
    cur_ds = None
    headers = None
    reader = csv.reader(io.StringIO(raw))
    for row in reader:
        if not row:
            continue
        cell0 = (row[0] or "").strip()
        # Detecta encabezado de sección
        if cell0.startswith("===") and cell0.endswith("==="):
            name = cell0.strip("= ").lower()
            if "estudiante" in name:
                cur_ds = "estudiantes"
            elif "curso" in name:
                cur_ds = "cursos"
            elif "matricula" in name:
                cur_ds = "matriculas"
            elif "usuario" in name:
                cur_ds = "usuarios"
            else:
                cur_ds = None
            headers = None
            continue

        # Siguiente línea tras la sección = cabeceras
        if cur_ds and headers is None:
            headers = row
            continue

        # Filas de datos
        if cur_ds and headers:
            rec = {}
            for idx, h in enumerate(headers):
                rec[h] = row[idx] if idx < len(row) else ""
            data[cur_ds].append(rec)

    # Si no vino nada, asume todo para el primer dataset seleccionado (compat anterior)
    if not data:
        ds = selected_datasets[0] if selected_datasets else "estudiantes"
        reader = csv.DictReader(io.StringIO(raw))
        data = {ds: list(reader)}
    return dict(data)


def _validate_columns(data_dict):
    """
    Valida columnas mínimas por dataset (tolerante).
    Requisitos (cualquiera de los grupos por dataset):
      - estudiantes: {rut}  o  {id}
      - cursos: {nivel, anio}  o  {id}
      - matriculas: {anio, estudiante_id}  o  {anio, estudiante_rut}  o  {rut}  o  {anio, estudiante}
      - usuarios: {run}  o  {username}  o  {email}
    """
    required_any = {
        "estudiantes": [
            {"rut"},
            {"id"},
        ],
        "cursos": [
            {"nivel", "anio"},
            {"id"},
        ],
        "matriculas": [
            {"anio", "estudiante_id"},
            {"anio", "estudiante_rut"},
            {"anio", "rut"},
            {"anio", "estudiante"},  # cuando viene de model_to_dict
        ],
        "usuarios": [
            {"run"},
            {"username"},
            {"email"},
        ],
    }

    errors, warnings = [], []

    for ds_name, rows in (data_dict or {}).items():
        if not rows:
            warnings.append(f"{ds_name}: sin filas en el archivo.")
            continue

        colset = set()
        sample_count = min(5, len(rows))
        for i in range(sample_count):
            row = rows[i]
            if not isinstance(row, dict):
                continue
            for k in row.keys():
                if not k:
                    continue
                colset.add(_norm_header(k))

        if "" in colset:
            colset.discard("")
            warnings.append(f"{ds_name}: se detectaron cabeceras vacías; fueron ignoradas.")

        req_groups = required_any.get(ds_name, [])
        if req_groups:
            ok = any(req.issubset(colset) for req in req_groups)
            if not ok:
                human_cols = ", ".join(sorted(colset)) if colset else "(sin columnas)"
                req_text = " ó ".join(["{" + ", ".join(sorted(g)) + "}" for g in req_groups])
                errors.append(
                    f"{ds_name}: columnas mínimas no presentes. Columnas detectadas: [{human_cols}]. "
                    f"Se requiere uno de: {req_text}."
                )

    return errors, warnings


def _resolve_curso(row, anio_defecto=None):
    """
    Devuelve instancia de Curso o None.
    Soporta:
      - curso_id / course_id
      - (curso_nivel|nivel, curso_letra|letra?, curso_anio|anio?)
    """
    cid = _pick_int(row.get("curso_id") or row.get("course_id"))
    if cid:
        try:
            return Curso.objects.get(id=cid)
        except Curso.DoesNotExist:
            return None

    nivel = _norm(row.get("curso_nivel") or row.get("nivel"))
    letra = _norm(row.get("curso_letra") or row.get("letra")) or None
    anio = _pick_int(row.get("curso_anio") or row.get("anio"), anio_defecto)

    if nivel and anio:
        try:
            return Curso.objects.get(nivel=nivel, letra=letra, anio=anio)
        except Curso.DoesNotExist:
            return None
    return None


def _get_or_create_curso_comodin(anio: int) -> "Curso":
    """
    Retorna/crea un curso comodín para el año dado (nivel 'Sin asignar', letra NULL).
    Recomendado porque Estudiante.curso es obligatorio (null=False).
    """
    return Curso.objects.get_or_create(
        nivel="Sin asignar",  # asegúrate de tenerlo en NIVEL_CHOICES
        letra=None,
        anio=anio,
    )[0]


def _safe_defaults_estudiante(row):
    """
    Valores por defecto para campos obligatorios si no vienen en el archivo.
    AJUSTA a tus reglas reales (fechas, comuna, etc).
    """
    def _date_or_default(key, default_dt):
        v = _norm(row.get(key))
        if v:
            try:
                y, m, d = v.split("-")
                return date(int(y), int(m), int(d))
            except Exception:
                return default_dt
        return default_dt

    defaults = {
        "nombre_completo": _norm(row.get("nombre_completo")) or _norm(row.get("nombre")) or "SIN NOMBRE",
        "fecha_nacimiento": _date_or_default("fecha_nacimiento", date(2000, 1, 1)),
        "sexo": _norm(row.get("sexo")) or "Masculino",
        "nacionalidad": _norm(row.get("nacionalidad")) or "Chilena",
        "domicilio": _norm(row.get("domicilio")) or "S/I",
        "comuna": _norm(row.get("comuna")) or "S/I",
        "vive_con": _norm(row.get("vive_con")) or "Otros",
        "fecha_matricula": _date_or_default("fecha_matricula", date.today()),
        "sistema_salud": _norm(row.get("sistema_salud")) or "Fonasa",
    }
    return defaults


def _model_field_names(model):
    """Todos los campos concretos (incluye FK como *_id), sin M2M ni reversos."""
    return [
        f.name
        for f in model._meta.get_fields()
        if getattr(f, "concrete", False) and not f.many_to_many and not f.auto_created
    ]


# =========================
# EXPORTACIÓN (general)
# =========================
@login_required
def export_datos(request):
    year_raw = request.GET.get('anio')
    formato = (_norm(request.GET.get('formato')) or 'csv').lower()
    sep = request.GET.get('sep', ',')
    datasets = request.GET.getlist('datasets')

    solo_activos = _bool(request.GET.get('solo_activos'))
    incluir_sin_nivel = _bool(request.GET.get('incluir_sin_nivel'))

    if not datasets:
        messages.error(request, 'Debes seleccionar al menos un dataset para exportar.')
        return redirect('panel_config_documentos')

    try:
        year = int(year_raw)
    except (TypeError, ValueError):
        messages.error(request, 'Año inválido.')
        return redirect('panel_config_documentos')

    if not AcademicYear.objects.filter(anio=year, is_visible=True).exists():
        messages.error(request, f'El año {year} no está habilitado como visible.')
        return redirect('panel_config_documentos')

    wanted = set(slugify(d).replace("-", "_") for d in datasets)
    all_data = {}

    # === ESTUDIANTES ===
    if 'estudiantes' in wanted:
        qs = Estudiante.objects.select_related('curso')
        if solo_activos:
            qs = qs.filter(estado='Activo')
        if incluir_sin_nivel:
            qs = qs.filter(Q(curso__anio=year) | Q(curso__isnull=True))
        else:
            qs = qs.filter(curso__anio=year)
        qs = qs.distinct()

        out = []
        for e in qs.iterator():
            d = model_to_dict(e)
            d["rut"] = e.rut
            if e.curso_id:
                d["curso_id"] = e.curso_id
                d["curso_nivel"] = e.curso.nivel
                d["curso_letra"] = e.curso.letra
                d["curso_anio"] = e.curso.anio
            out.append(d)
        all_data['estudiantes'] = out

    # === CURSOS ===
    if 'cursos' in wanted:
        cursos = list(Curso.objects.filter(anio=year).values())
        all_data['cursos'] = cursos

    # === MATRICULAS ===
    if 'matriculas' in wanted:
        qs = MatriculaAnual.objects.filter(anio=year).select_related('estudiante', 'curso')
        if solo_activos and hasattr(MatriculaAnual, "estado"):
            qs = qs.filter(estado='Activo')
        out = []
        for m in qs.iterator():
            d = model_to_dict(m)
            d["rut"] = m.estudiante.rut  # clave para enlazar
            if m.curso_id:
                d["curso_nivel"] = m.curso.nivel
                d["curso_letra"] = m.curso.letra
                d["curso_anio"] = m.curso.anio
            out.append(d)
        all_data['matriculas'] = out

    # === USUARIOS ===
    if 'usuarios' in wanted:
        all_data['usuarios'] = list(Usuarios.objects.values())

    # === Salidas ===
    if formato == 'json':
        payload = json.dumps(all_data, ensure_ascii=False, indent=2, cls=DjangoJSONEncoder)
        resp = HttpResponse(payload, content_type='application/json; charset=utf-8')
        resp['Content-Disposition'] = f'attachment; filename="export_{year}.json"'
        return resp

    # CSV combinado (sección por dataset)
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response.write('\ufeff')  # BOM para Excel
    response['Content-Disposition'] = f'attachment; filename="export_{year}.csv"'
    writer = csv.writer(response, delimiter=sep)

    for name, rows in all_data.items():
        writer.writerow([f'=== {name.upper()} ==='])
        if rows:
            headers = sorted({k for r in rows for k in r.keys()})
            writer.writerow(headers)
            for row in rows:
                writer.writerow([row.get(h, '') for h in headers])
        else:
            writer.writerow(['(sin datos)'])
        writer.writerow([])

    return response

from django.core.serializers.json import DjangoJSONEncoder
# =========================
# EXPORTACIÓN (Estudiantes por año - todos los campos)
# =========================
@login_required
def export_estudiantes_por_anio(request):
    """
    Descarga TODOS los datos de Estudiante filtrados por año (curso__anio).
    Soporta:
      - GET anio=YYYY (requerido)
      - GET formato=csv|json  (default: csv)
      - GET sep=,|;||         (default: ,)  [solo CSV]
      - GET solo_activos=on/true
      - GET incluir_sin_nivel=on/true  -> incluye estudiantes con curso NULL
    """
    formato = (_norm(request.GET.get("formato")) or "csv").lower()
    sep = request.GET.get("sep", ",")
    solo_activos = _bool(request.GET.get("solo_activos"))
    incluir_sin_nivel = _bool(request.GET.get("incluir_sin_nivel"))

    # Año (requerido)
    try:
        year = int(request.GET.get("anio"))
    except (TypeError, ValueError):
        return HttpResponse("Parámetro 'anio' inválido.", status=400)

    if not AcademicYear.objects.filter(anio=year, is_visible=True).exists():
        return HttpResponse(f"El año {year} no está habilitado como visible.", status=400)

    qs = Estudiante.objects.select_related("curso")
    if solo_activos:
        qs = qs.filter(estado="Activo")

    if incluir_sin_nivel:
        qs = qs.filter(Q(curso__anio=year) | Q(curso__isnull=True))
    else:
        qs = qs.filter(curso__anio=year)

    qs = qs.distinct()

    base_fields = _model_field_names(Estudiante)  # incluye 'curso_id'
    extra_course_cols = ["curso_nivel", "curso_letra", "curso_anio"]
    headers = base_fields + extra_course_cols

    if formato == "json":
        out = []
        for e in qs.iterator():
            row = {f: getattr(e, f) for f in base_fields}
            if e.curso_id:
                row["curso_nivel"] = e.curso.nivel
                row["curso_letra"] = e.curso.letra
                row["curso_anio"] = e.curso.anio
            else:
                row["curso_nivel"] = None
                row["curso_letra"] = None
                row["curso_anio"] = None
            out.append(row)

        resp = HttpResponse(
            json.dumps(out, ensure_ascii=False, indent=2, cls=DjangoJSONEncoder),
            content_type="application/json; charset=utf-8",
        )
        resp["Content-Disposition"] = f'attachment; filename="estudiantes_{year}.json"'
        return resp

    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response.write("\ufeff")
    response["Content-Disposition"] = f'attachment; filename="estudiantes_{year}.csv"'
    writer = csv.writer(response, delimiter=sep)

    writer.writerow(headers)
    for e in qs.iterator():
        row = {f: getattr(e, f) for f in base_fields}
        if e.curso_id:
            row["curso_nivel"] = e.curso.nivel
            row["curso_letra"] = e.curso.letra
            row["curso_anio"] = e.curso.anio
        else:
            row["curso_nivel"] = ""
            row["curso_letra"] = ""
            row["curso_anio"] = ""
        writer.writerow([row.get(h, "") for h in headers])

    return response


# =========================
# IMPORTACIÓN
# =========================
@login_required
def import_datos_panel(request):
    """
    Importa CSV/JSON a datasets (valida estructura antes).
    Orden de carga: cursos → estudiantes → matriculas → usuarios
    """
    if request.method != "POST":
        return redirect("panel_config_documentos")

    archivo = request.FILES.get("archivo")
    datasets = request.POST.getlist("datasets")
    modo = (_norm(request.POST.get("modo")) or "upsert").lower()        # upsert|overwrite|skip
    anio_destino = _pick_int(request.POST.get("anio_destino"))
    validate_only = _bool(request.POST.get("validate_only"))
    permitir_sin_nivel = _bool(request.POST.get("permitir_sin_nivel"))

    if not archivo:
        messages.error(request, "Debes subir un archivo CSV o JSON.")
        return redirect("panel_config_documentos")

    if not datasets:
        messages.error(request, "Debes seleccionar al menos un dataset.")
        return redirect("panel_config_documentos")

    if not anio_destino or not AcademicYear.objects.filter(anio=anio_destino, is_visible=True).exists():
        messages.error(request, "El año de destino no es visible o no es válido.")
        return redirect("panel_config_documentos")

    wanted = set(slugify(d).replace("-", "_") for d in datasets)

    try:
        if archivo.name.lower().endswith(".json"):
            payload = json.load(archivo)
            if isinstance(payload, dict):
                data = {k: v for k, v in payload.items() if k in wanted}
            elif isinstance(payload, list):
                ds = next(iter(wanted or []), "estudiantes")
                data = {ds: payload}
            else:
                raise ValueError("Formato JSON no soportado.")
        else:
            data = _csv_to_datasets(archivo, list(wanted))
    except Exception as e:
        messages.error(request, f"Error leyendo archivo: {e}")
        return redirect("panel_config_documentos")

    for k, rows in list(data.items()):
        data[k] = _sanitize_rows(rows)

    validation_errors, _validation_warnings = _validate_columns(data)
    if validation_errors:
        for err in validation_errors:
            _append_log(request.session, f"❌ {err}")
        messages.error(request, "Validación fallida: faltan columnas requeridas en uno o más datasets.")
        return redirect("panel_config_documentos")
    else:
        _append_log(request.session, "✅ Validación OK: columnas mínimas presentes.")

    if validate_only:
        messages.success(request, "Validación completada: no se realizaron cambios.")
        return redirect("panel_config_documentos")

    summary = {k: {"created": 0, "updated": 0, "skipped": 0} for k in ("cursos", "estudiantes", "matriculas", "usuarios")}
    ordered = ["cursos", "estudiantes", "matriculas", "usuarios"]

    for ds in ordered:
        rows = data.get(ds, [])
        if not rows or ds not in wanted:
            continue

        try:
            with transaction.atomic():
                # === CURSOS ===
                if ds == "cursos":
                    for r in rows:
                        nivel = _norm(r.get("nivel"))
                        letra = _norm(r.get("letra")) or None
                        anio = _pick_int(r.get("anio"), anio_destino)
                        if not nivel or not anio:
                            summary["cursos"]["skipped"] += 1
                            continue
                        obj, created = Curso.objects.get_or_create(nivel=nivel, letra=letra, anio=anio)
                        if created:
                            summary["cursos"]["created"] += 1
                        else:
                            if modo in ("upsert", "overwrite"):
                                obj.save()
                                summary["cursos"]["updated"] += 1
                            else:
                                summary["cursos"]["skipped"] += 1

                # === ESTUDIANTES ===
                elif ds == "estudiantes":
                    for r in rows:
                        rut = _norm_rut(r.get("rut"))
                        if not rut:
                            summary["estudiantes"]["skipped"] += 1
                            continue

                        curso = _resolve_curso(r, anio_defecto=anio_destino)
                        if not curso:
                            if permitir_sin_nivel:
                                curso = _get_or_create_curso_comodin(anio_destino)
                            else:
                                summary["estudiantes"]["skipped"] += 1
                                continue

                        defaults = _safe_defaults_estudiante(r)

                        e = Estudiante.objects.filter(rut=rut).first()
                        if e:
                            if modo in ("upsert", "overwrite"):
                                if _norm(r.get("nombre_completo")) or _norm(r.get("nombre")):
                                    e.nombre_completo = defaults["nombre_completo"]
                                e.curso = curso
                                e.save()
                                summary["estudiantes"]["updated"] += 1
                            else:
                                summary["estudiantes"]["skipped"] += 1
                        else:
                            Estudiante.objects.create(
                                rut=rut,
                                nombre_completo=defaults["nombre_completo"],
                                fecha_nacimiento=defaults["fecha_nacimiento"],
                                sexo=defaults["sexo"],
                                curso=curso,
                                nacionalidad=defaults["nacionalidad"],
                                domicilio=defaults["domicilio"],
                                comuna=defaults["comuna"],
                                vive_con=defaults["vive_con"],
                                fecha_matricula=defaults["fecha_matricula"],
                                sistema_salud=defaults["sistema_salud"],
                            )
                            summary["estudiantes"]["created"] += 1

                # === MATRICULAS ===
                elif ds == "matriculas":
                    for r in rows:
                        rut = _norm_rut(r.get("rut") or r.get("estudiante_rut"))
                        if not rut:
                            summary["matriculas"]["skipped"] += 1
                            continue

                        e = Estudiante.objects.filter(rut=rut).first()
                        if not e:
                            summary["matriculas"]["skipped"] += 1
                            continue

                        curso = _resolve_curso(r, anio_defecto=anio_destino)
                        if not curso and permitir_sin_nivel:
                            curso = _get_or_create_curso_comodin(anio_destino)

                        m = MatriculaAnual.objects.filter(anio=anio_destino, estudiante=e).first()
                        if m:
                            if modo in ("upsert", "overwrite"):
                                m.curso = curso
                                m.save()
                                summary["matriculas"]["updated"] += 1
                            else:
                                summary["matriculas"]["skipped"] += 1
                        else:
                            MatriculaAnual.objects.create(anio=anio_destino, estudiante=e, curso=curso)
                            summary["matriculas"]["created"] += 1

                # === USUARIOS ===
                elif ds == "usuarios":
                    for r in rows:
                        run = _norm(r.get("run"))
                        email = _norm(r.get("email")) or None
                        if not run and not email:
                            summary["usuarios"]["skipped"] += 1
                            continue
                        u = Usuarios.objects.filter(run=run).first() if run else Usuarios.objects.filter(email=email).first()
                        if u:
                            if modo in ("upsert", "overwrite"):
                                if _norm(r.get("nombre")):
                                    u.nombre = _norm(r.get("nombre"))
                                if _norm(r.get("apellido")):
                                    u.apellido = _norm(r.get("apellido"))
                                if email:
                                    u.email = email
                                u.save()
                                summary["usuarios"]["updated"] += 1
                            else:
                                summary["usuarios"]["skipped"] += 1
                        else:
                            Usuarios.objects.create(
                                run=run or "",
                                nombre=_norm(r.get("nombre")),
                                apellido=_norm(r.get("apellido")),
                                email=email,
                            )
                            summary["usuarios"]["created"] += 1

        except Exception as e:
            _append_log(request.session, f"❌ Error en {ds}: {e}")
            messages.error(request, f"Error importando {ds}: {e}")

    parts = [
        f"{k}: Creados {v['created']}, Actualizados {v['updated']}, Omitidos {v['skipped']}"
        for k, v in summary.items()
        if (v['created'] + v['updated'] + v['skipped']) > 0
    ]
    if parts:
        messages.success(request, f"Importación completada ({modo}). " + " · ".join(parts))
        _append_log(request.session, "✅ " + " · ".join(parts))
    else:
        messages.info(request, "No se importaron filas.")

    return redirect("panel_config_documentos")


######## importacion por sql###########
from app.sql_import_runner import import_sqldump as run_import_sqldump  # 👈 alias seguro


def _is_staff_or_superuser(user):
    return user.is_active and (user.is_staff or user.is_superuser)

def _append_log(session, line):
    buff = session.get("sql_import_log", [])
    buff.append(line)
    if len(buff) > 500:
        buff = buff[-500:]
    session["sql_import_log"] = buff
    session.modified = True

@login_required
@user_passes_test(_is_staff_or_superuser)
def importar_sql_dump(request):
    if request.method != "POST":
        return redirect("panel_config_documentos")

    dump_file = request.FILES.get("dump_sql")
    anio = request.POST.get("anio_destino")
    permitir_sin_nivel = request.POST.get("permitir_sin_nivel") in ("on", "true", "1")

    try:
        anio = int(anio)
    except Exception:
        messages.error(request, "Año destino inválido.")
        return redirect("panel_config_documentos")

    if not dump_file:
        messages.error(request, "Debes seleccionar un archivo .sql")
        return redirect("panel_config_documentos")

    if not AcademicYear.objects.filter(anio=anio, is_visible=True).exists():
        messages.error(request, f"El año {anio} no está habilitado/visible.")
        return redirect("panel_config_documentos")

    # guarda temporal
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".sql", dir=getattr(settings, "FILE_UPLOAD_TEMP_DIR", None)) as tmp:
            for chunk in dump_file.chunks():
                tmp.write(chunk)
            tmp_path = tmp.name
    except Exception as e:
        messages.error(request, f"No se pudo guardar el archivo temporal: {e}")
        return redirect("panel_config_documentos")

    out_io, err_io = io.StringIO(), io.StringIO()
    try:
        run_import_sqldump(
            dump_path=tmp_path,
            anio=anio,
            permitir_sin_nivel=permitir_sin_nivel,
            stdout=out_io,
            stderr=err_io,
        )
        stdout_txt = out_io.getvalue().strip()
        stderr_txt = err_io.getvalue().strip()

        if stdout_txt:
            _append_log(request.session, "=== STDOUT ===")
            for line in stdout_txt.splitlines():
                _append_log(request.session, line)
        if stderr_txt:
            _append_log(request.session, "=== STDERR ===")
            for line in stderr_txt.splitlines():
                _append_log(request.session, line)

        if stderr_txt:
            messages.warning(request, "Import terminado con advertencias. Revisa el log al final del panel.")
        else:
            messages.success(request, "Importación completada desde .sql. Revisa el log al final del panel.")
    except Exception as e:
        _append_log(request.session, f"❌ ERROR: {e}")
        messages.error(request, f"Falló la importación: {e}")
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass

    return redirect("panel_config_documentos")