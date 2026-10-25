# app/management/commands/import_from_sqldump.py
# -*- coding: utf-8 -*-
"""
Importa datos desde un archivo dump .sql (MySQL) sin restaurar otra BD.
- Lee INSERTs de app_estudiante y app_documentoestudiante
- Mapea 'curso' (texto) a Curso (FK) en tu esquema actual para el --anio dado
- Crea/actualiza Estudiante; crea DocumentoEstudiante

Uso:
  python manage.py import_from_sqldump --dump "/ruta/liceospc_matriculas_lsp.sql" --anio 2025 --permitir-sin-nivel

Notas:
- El parser es tolerante a:
  * INSERT multi-línea
  * Comillas simples con escapes
  * NULLs
  * Varios VALUES por INSERT
- Si en tu dump los nombres de tabla difieren, ajusta TABLE_EST, TABLE_DOC.
"""

import re
import os
from datetime import date, datetime
from typing import Dict, List, Tuple, Iterable, Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.timezone import now

# Ajusta estos imports a tus modelos reales
from app.models import Estudiante, Curso, DocumentoEstudiante, AcademicYear

# === Config del dump ===
TABLE_EST = "app_estudiante"
TABLE_DOC = "app_documentoestudiante"

# Mapea etiquetas de nivel (texto del dump) a tus choices actuales
NIVEL_NORMALIZACION = {
    "prekinder": "Prekinder",
    "kinder": "Kinder",
    "1° básico": "1° Básico",
    "2° básico": "2° Básico",
    "3° básico": "3° Básico",
    "4° básico": "4° Básico",
    "5° básico": "5° Básico",
    "6° básico": "6° Básico",
    "7° básico": "7° Básico",
    "8° básico": "8° Básico",
    "1° medio": "1° Medio",
    "2° medio": "2° Medio",
    "3° medio": "3° Medio",
    "4° medio": "4° Medio",
    # deja fallback al valor original si no está
}


def _norm(s: Any) -> str:
    return (str(s) if s is not None else "").strip()


def _norm_nivel(nivel: str) -> str:
    key = _norm(nivel).lower()
    return NIVEL_NORMALIZACION.get(key, _norm(nivel) or "Sin asignar")


def _norm_rut(rut: str) -> str:
    return _norm(rut).replace(".", "").replace(" ", "").upper()


def _parse_date(s: str, default_dt: date | None = None) -> date | None:
    s = _norm(s)
    if not s:
        return default_dt
    # intenta formatos típicos
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            pass
    return default_dt


def _split_top_level_commas(values_str: str) -> List[str]:
    """
    Divide por comas sin romper comas dentro de strings o paréntesis.
    Útil para separar tuplas VALUES y también columnas.
    """
    out, buf = [], []
    in_str = False
    esc = False
    for ch in values_str:
        if in_str:
            buf.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == "'":
                in_str = False
        else:
            if ch == "'":
                in_str = True
                buf.append(ch)
            elif ch == ",":
                out.append("".join(buf).strip())
                buf = []
            else:
                buf.append(ch)
    if buf:
        out.append("".join(buf).strip())
    return out


def _unquote_sql_value(token: str):
    """
    Convierte token SQL -> Python:
    - 'texto' -> texto (con escapes)
    - NULL -> None
    - números -> str (no casteamos a int para no romper IDs grandes)
    """
    t = token.strip()
    if t.upper() == "NULL":
        return None
    if t.startswith("'") and t.endswith("'"):
        # des-escape básico para MySQL
        inner = t[1:-1]
        inner = inner.replace("\\'", "'").replace("\\\\", "\\")
        return inner
    # deja otros tal cual (números, etc.) como string
    return t


def _iter_insert_blocks(sql: str, table_name: str) -> Iterable[Tuple[List[str], List[List[str]]]]:
    """
    Genera bloques (columns, rows) para cada INSERT INTO `table_name` (...) VALUES (...),(...),...;
    columns: lista de nombres de columnas (en orden)
    rows: lista de listas (tokens crudos, no unquoted)
    """
    # Regex tolerante a saltos de línea, espacios y backticks
    # Captura columnas dentro de (...) y el bloque de VALUES (...) , (...) ;
    pattern = re.compile(
        rf"INSERT\s+INTO\s+`?{re.escape(table_name)}`?\s*\((.*?)\)\s*VALUES\s*(.*?);",
        re.IGNORECASE | re.DOTALL,
    )
    for m in pattern.finditer(sql):
        cols_raw = m.group(1).strip()
        vals_raw = m.group(2).strip()

        # columnas
        columns = [c.strip().strip("`") for c in _split_top_level_commas(cols_raw)]

        # separar tuplas VALUES por paréntesis top-level
        rows = []
        buf = []
        depth = 0
        in_str = False
        esc = False
        for ch in vals_raw:
            buf.append(ch)
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == "'":
                    in_str = False
            else:
                if ch == "'":
                    in_str = True
                elif ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        # cerró una tupla completa
                        tup = "".join(buf).strip()
                        if tup.startswith("(") and tup.endswith(")"):
                            tup_inner = tup[1:-1]
                            # divide por comas a nivel tope
                            tokens = _split_top_level_commas(tup_inner)
                            rows.append(tokens)
                        buf = []
        yield columns, rows


def _build_curso_cache_from_est_rows(est_rows: List[Dict[str, Any]], anio: int, permitir_sin_nivel: bool) -> Dict[str, Curso]:
    """
    Recorre filas de estudiantes (json-like) y crea/obtiene los cursos para el 'anio'.
    Usa el valor textual de la columna 'curso' (si existe en el dump).
    """
    cache: Dict[str, Curso] = {}
    # Asegura comodín si se permite
    if permitir_sin_nivel:
        cache["__SIN__"] = Curso.objects.get_or_create(nivel="Sin asignar", letra=None, anio=anio)[0]

    for r in est_rows:
        nivel_raw = _norm(r.get("curso"))  # columna textual del dump
        if not nivel_raw:
            if permitir_sin_nivel:
                continue
            else:
                # si no se permite, no podemos crear curso sin info
                continue
        if nivel_raw in cache:
            continue
        nivel = _norm_nivel(nivel_raw)
        obj, _ = Curso.objects.get_or_create(nivel=nivel, letra=None, anio=anio)
        cache[nivel_raw] = obj
    return cache


class Command(BaseCommand):
    help = "Importa Estudiantes y Documentos desde un archivo .sql (dump MySQL), sin restaurar BD auxiliar."

    def add_arguments(self, parser):
        parser.add_argument("--dump", required=True, help="Ruta al archivo .sql (dump)")
        parser.add_argument("--anio", type=int, required=True, help="Año académico destino (debe existir como visible)")
        parser.add_argument("--permitir-sin-nivel", action="store_true", help="Si no hay 'curso' en dump, usa/crea 'Sin asignar'")

    def handle(self, *args, **opts):
        dump_path = opts["dump"]
        anio = opts["anio"]
        permitir_sin_nivel = opts["permitir_sin_nivel"]

        if not os.path.isfile(dump_path):
            raise CommandError(f"No existe el archivo: {dump_path}")

        if not AcademicYear.objects.filter(anio=anio, is_visible=True).exists():
            raise CommandError(f"El año {anio} no está visible/habilitado en AcademicYear.")

        self.stdout.write(self.style.NOTICE(f"Leyendo dump: {dump_path}"))
        with open(dump_path, "r", encoding="utf-8", errors="ignore") as f:
            sql = f.read()

        # === 1) Parsear INSERTs de app_estudiante ===
        self.stdout.write(self.style.NOTICE("Parseando INSERTs de app_estudiante…"))
        est_rows: List[Dict[str, Any]] = []
        for columns, rows in _iter_insert_blocks(sql, TABLE_EST):
            for tokens in rows:
                # alinea tokens a columnas; si faltan, completa con None
                rec = {}
                for idx, col in enumerate(columns):
                    tok = tokens[idx] if idx < len(tokens) else "NULL"
                    rec[col] = _unquote_sql_value(tok)
                est_rows.append(rec)

        self.stdout.write(self.style.SUCCESS(f"Estudiantes parseados: {len(est_rows)}"))

        # === 2) Preparar Cursos a partir de 'curso' textual ===
        self.stdout.write(self.style.NOTICE("Creando/mapeando Cursos…"))
        curso_cache = _build_curso_cache_from_est_rows(est_rows, anio, permitir_sin_nivel)

        # === 3) Upsert de Estudiantes ===
        creados_e = actualizados_e = omitidos_e = 0
        self.stdout.write(self.style.NOTICE("Importando Estudiantes…"))
        with transaction.atomic():
            for r in est_rows:
                rut = _norm_rut(r.get("rut"))
                if not rut:
                    omitidos_e += 1
                    continue

                # curso
                curso_raw = _norm(r.get("curso"))
                if curso_raw and curso_raw in curso_cache:
                    curso_obj = curso_cache[curso_raw]
                else:
                    curso_obj = curso_cache.get("__SIN__") if permitir_sin_nivel else None

                if not curso_obj:
                    omitidos_e += 1
                    continue

                # campos mínimos + mapeos (ajusta a tus campos)
                nombre = _norm(r.get("nombre_completo")) or "SIN NOMBRE"
                sexo = _norm(r.get("sexo")) or "Masculino"
                fnac = _parse_date(_norm(r.get("fecha_nacimiento")), date(2000, 1, 1))
                nacionalidad = _norm(r.get("nacionalidad")) or "Chilena"
                domicilio = _norm(r.get("domicilio")) or "S/I"
                comuna = _norm(r.get("comuna")) or "S/I"
                vive_con = _norm(r.get("vive_con")) or "Otros"
                fecha_matricula = _parse_date(_norm(r.get("fecha_matricula")), date.today())
                sistema_salud = _norm(r.get("sistema_salud")) or "Fonasa"
                estado = _norm(r.get("estado")) or "Activo"

                # Booleans comunes en dumps (0/1)
                def _b(v):
                    s = _norm(v).lower()
                    return s in ("1", "true", "t", "si", "sí", "y", "yes")

                est, created = Estudiante.objects.get_or_create(
                    rut=rut,
                    defaults=dict(
                        nombre_completo=nombre,
                        fecha_nacimiento=fnac,
                        sexo=sexo,
                        curso=curso_obj,
                        nacionalidad=nacionalidad,
                        domicilio=domicilio,
                        comuna=comuna,
                        vive_con=vive_con,
                        fecha_matricula=fecha_matricula,
                        sistema_salud=sistema_salud,
                        estado=estado,
                        ascendencia_indigena=_b(r.get("ascendencia_indigena")),
                        pertenece_al_pie=_b(r.get("pertenece_al_pie")),
                        ha_repetido_curso=_b(r.get("ha_repetido_curso")),
                        resolucion_judicial=_b(r.get("resolucion_judicial")),
                        problemas_salud=_b(r.get("problemas_salud")),
                        impide_educacion_fisica=_b(r.get("impide_educacion_fisica")),
                        opta_religion=_b(r.get("opta_religion")),
                        acepta_fotografias=_b(r.get("acepta_fotografias")),
                        certificado_medico=_b(r.get("certificado_medico")),
                        etnia=_norm(r.get("etnia")) or None,
                        tipo_transporte=_norm(r.get("tipo_transporte")) or None,
                        descripcion_problemas_salud=_norm(r.get("descripcion_problemas_salud")) or None,

                        # Familiares (si existen en dump)
                        nombre_madre=_norm(r.get("nombre_madre")) or None,
                        rut_madre=_norm(r.get("rut_madre")) or None,
                        nivel_educacional_madre=_norm(r.get("nivel_educacional_madre")) or None,
                        ocupacion_madre=_norm(r.get("ocupacion_madre")) or None,
                        telefono_madre=_norm(r.get("telefono_madre")) or None,
                        email_madre=_norm(r.get("email_madre")) or None,

                        nombre_padre=_norm(r.get("nombre_padre")) or None,
                        rut_padre=_norm(r.get("rut_padre")) or None,
                        nivel_educacional_padre=_norm(r.get("nivel_educacional_padre")) or None,
                        ocupacion_padre=_norm(r.get("ocupacion_padre")) or None,
                        telefono_padre=_norm(r.get("telefono_padre")) or None,
                        email_padre=_norm(r.get("email_padre")) or None,

                        nombre_apoderado=_norm(r.get("nombre_apoderado")) or None,
                        rut_apoderado=_norm(r.get("rut_apoderado")) or None,
                        nivel_educacional_apoderado=_norm(r.get("nivel_educacional_apoderado")) or None,
                        ocupacion_apoderado=_norm(r.get("ocupacion_apoderado")) or None,
                        telefono_apoderado=_norm(r.get("telefono_apoderado")) or None,
                        email_apoderado=_norm(r.get("email_apoderado")) or None,

                        nombre_apoderado_suplente=_norm(r.get("nombre_apoderado_suplente")) or None,
                        rut_apoderado_suplente=_norm(r.get("rut_apoderado_suplente")) or None,
                        nivel_educacional_apoderado_suplente=_norm(r.get("nivel_educacional_apoderado_suplente")) or None,
                        ocupacion_apoderado_suplente=_norm(r.get("ocupacion_apoderado_suplente")) or None,
                        telefono_apoderado_suplente=_norm(r.get("telefono_apoderado_suplente")) or None,
                        email_apoderado_suplente=_norm(r.get("email_apoderado_suplente")) or None,
                    )
                )
                if created:
                    creados_e += 1
                else:
                    # upsert suave
                    changed = False
                    if nombre and est.nombre_completo != nombre:
                        est.nombre_completo = nombre
                        changed = True
                    if est.curso_id != curso_obj.id:
                        est.curso = curso_obj
                        changed = True
                    if changed:
                        est.save(update_fields=["nombre_completo", "curso"])
                    actualizados_e += 1

        self.stdout.write(self.style.SUCCESS(f"Estudiantes: creados={creados_e}, actualizados={actualizados_e}, omitidos={omitidos_e}"))

        # === 4) Parsear INSERTs de app_documentoestudiante ===
        self.stdout.write(self.style.NOTICE("Parseando INSERTs de app_documentoestudiante…"))
        doc_rows: List[Dict[str, Any]] = []
        for columns, rows in _iter_insert_blocks(sql, TABLE_DOC):
            for tokens in rows:
                rec = {}
                for idx, col in enumerate(columns):
                    tok = tokens[idx] if idx < len(tokens) else "NULL"
                    rec[col] = _unquote_sql_value(tok)
                doc_rows.append(rec)
        self.stdout.write(self.style.SUCCESS(f"Documentos parseados: {len(doc_rows)}"))

        # === 5) Importar Documentos ===
        creados_doc = omitidos_doc = 0
        self.stdout.write(self.style.NOTICE("Importando Documentos…"))
        with transaction.atomic():
            # en el dump, suele venir estudiante_id (legacy PK). No podemos mapear por ese id;
            # mapeamos por RUT si viene en doc? (normalmente NO). Entonces:
            # estrategia: buscar por FK numérica si el dump de documentos usa la misma PK que insertó en estudiantes.
            # Como no la tenemos, buscamos al estudiante por el campo 'estudiante_id' del dump:
            #   1) Resolvemos el legacy_id -> rut desde est_rows para enlazar.
            legacy_id_to_rut = {}
            for r in est_rows:
                if "id" in r and r["id"] is not None:
                    legacy_id_to_rut[str(r["id"])] = _norm_rut(r.get("rut"))

            for d in doc_rows:
                # columnas típicas: id, nombre, archivo, fecha_subida, estudiante_id
                nombre = _norm(d.get("nombre")) or "Documento"
                archivo = _norm(d.get("archivo")) or ""
                fecha_subida = _parse_date(_norm(d.get("fecha_subida"))) or now().date()
                est_legacy_id = _norm(d.get("estudiante_id"))

                est_rut = legacy_id_to_rut.get(est_legacy_id)
                if not est_rut:
                    omitidos_doc += 1
                    continue
                est = Estudiante.objects.filter(rut=est_rut).first()
                if not est:
                    omitidos_doc += 1
                    continue

                DocumentoEstudiante.objects.get_or_create(
                    estudiante=est,
                    nombre=nombre,
                    defaults={
                        "archivo": archivo,
                        "fecha_subida": fecha_subida,
                    }
                )
                creados_doc += 1

        self.stdout.write(self.style.SUCCESS(f"Documentos: creados={creados_doc}, omitidos={omitidos_doc}"))
        self.stdout.write(self.style.SUCCESS("Importación desde .sql completada."))
