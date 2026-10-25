# app/sql_import_runner.py
from datetime import date
from typing import Optional
import io, re, unicodedata

from django.db import transaction

from app.models import (
    AcademicYear,
    Curso,
    Estudiante,
    MatriculaAnual,
)

try:
    from app.views import NIVEL_POR_ASIGNAR
except Exception:
    NIVEL_POR_ASIGNAR = "Por Asignar"

# ---------------- Helpers ----------------
def _norm(s):
    return str(s).strip() if s is not None else ""

def _norm_rut(rut):
    return _norm(rut).replace(".", "").replace(" ", "").upper()

def _coerce_bool(v):
    s = _norm(v).lower()
    return s in ("1", "true", "t", "si", "sí", "y", "yes", "on")

def _coerce_date(v, fallback):
    v = _norm(v)
    if not v: return fallback
    try:
        if "-" in v:
            y, m, d = v.split("-"); return date(int(y), int(m), int(d))
        if "/" in v:
            d, m, y = v.split("/"); return date(int(y), int(m), int(d))
    except Exception:
        return fallback
    return fallback

def _pick(*keys, _rec, _default=None):
    for k in keys:
        val = _rec.get(k)
        if val not in (None, "", "NULL"):
            return val
    return _default

def _strip_accents_low(s: str) -> str:
    s = _norm(s).lower()
    s = unicodedata.normalize("NFD", s)
    return "".join(ch for ch in s if unicodedata.category(ch) != "Mn")

def _canon_letra(letra: Optional[str]) -> Optional[str]:
    l = _norm(letra)
    if not l: return None
    m = re.search(r"[a-zA-Z]", l)
    return m.group(0).upper() if m else None

def _canon_nivel(raw: str | None) -> str | None:
    if not raw: return None
    s = _strip_accents_low(raw).replace("º", "").replace("°", "")
    s = s.replace("grado", "").replace("curso", "").strip()

    # Parvularia
    if s in {"prekinder", "pre-kinder", "pre kinder", "preescolar", "pk", "pre-kinder a", "pre-kinder b"}:
        return "Prekinder"
    if s.startswith("kinder") or s.startswith("kínder"):
        return "Kinder"

    BASICO_MAP = {
        ("1","primero"): "1° Básico", ("2","segundo"): "2° Básico",
        ("3","tercero"): "3° Básico", ("4","cuarto"):  "4° Básico",
        ("5","quinto"):  "5° Básico", ("6","sexto"):   "6° Básico",
        ("7","septimo","séptimo"): "7° Básico",
        ("8","octavo"):  "8° Básico",
    }
    MEDIO_MAP = {
        ("1","primero"): "1° Medio", ("2","segundo"): "2° Medio",
        ("3","tercero"): "3° Medio", ("4","cuarto"):  "4° Medio",
    }

    tokens = s.replace("-", " ").replace("_", " ").split()
    def has_any(keys): return any(k in tokens for k in keys)

    if "basico" in tokens or "básico" in s:
        for keys, lab in BASICO_MAP.items():
            if has_any(keys): return lab

    if "medio" in tokens or s.endswith("medio"):
        for keys, lab in MEDIO_MAP.items():
            if has_any(keys): return lab

    EXACTOS = {
        "prekinder":"Prekinder","kinder":"Kinder",
        "1 basico":"1° Básico","2 basico":"2° Básico","3 basico":"3° Básico","4 basico":"4° Básico",
        "5 basico":"5° Básico","6 basico":"6° Básico","7 basico":"7° Básico","8 basico":"8° Básico",
        "1 medio":"1° Medio","2 medio":"2° Medio","3 medio":"3° Medio","4 medio":"4° Medio",
        _strip_accents_low(NIVEL_POR_ASIGNAR): NIVEL_POR_ASIGNAR,
    }
    return EXACTOS.get(s)

# --- Parsers de texto de curso ---
# 1) Números + Básico/Medio
CURSO_TEXT_RE_NUM = re.compile(
    r"""
    ^\s*
    (?:
        (?P<num>[1-8]|primero|segundo|tercero|cuarto|quinto|sexto|septimo|séptimo|octavo)
        \s*(?:º|°)?\s*
        (?P<ciclo>basico|básico|medio)
        (?:\s+(?P<letra>[A-Za-z]))?
    )
    (?:\s+(?P<anio>\d{4}))?
    \s*$
    """,
    re.IGNORECASE | re.VERBOSE
)

# 2) Kinder / Prekinder (con o sin letra / año)
CURSO_TEXT_RE_K = re.compile(
    r"""
    ^\s*
    (?P<niv>pre[-\s]?kinder|k[ií]nder|kinder|prekinder|pre[-\s]?k[ií]nder)
    (?:\s+(?P<letra>[A-Za-z]))?
    (?:\s+(?P<anio>\d{4}))?
    \s*$
    """,
    re.IGNORECASE | re.VERBOSE
)

def _parse_curso_text(txt: str):
    if not txt: return None, None, None
    sraw = _norm(txt)
    s = _strip_accents_low(sraw).replace("º","").replace("°","")

    m = CURSO_TEXT_RE_NUM.match(s)
    if m:
        num = _norm(m.group("num"))
        ciclo = _norm(m.group("ciclo"))
        letra = _canon_letra(m.group("letra"))
        anio = m.group("anio")
        nivel = _canon_nivel(f"{num} {'basico' if ciclo.startswith('basi') else 'medio'}")
        anio = int(anio) if anio and anio.isdigit() else None
        return nivel, letra, anio

    m = CURSO_TEXT_RE_K.match(s)
    if m:
        niv = _norm(m.group("niv"))
        letra = _canon_letra(m.group("letra"))
        anio = m.group("anio")
        nivel = _canon_nivel(niv)  # → "Kinder" o "Prekinder"
        anio = int(anio) if anio and anio.isdigit() else None
        return nivel, letra, anio

    return None, None, None

def _get_or_create_curso_comodin(anio: int) -> Curso:
    obj, _ = Curso.objects.get_or_create(nivel=NIVEL_POR_ASIGNAR, letra=None, anio=anio)
    return obj

def _ensure_matricula(anio_int: int, est: Estudiante, cur: Curso):
    ma, created = MatriculaAnual.objects.get_or_create(
        anio=anio_int,
        estudiante=est,
        defaults={"curso": cur, "estado": getattr(est, "estado", "Activo")},
    )
    if not created and ma.curso_id != (cur.id if cur else None):
        ma.curso = cur
        ma.save(update_fields=["curso"])

# ---- Preferencias para Parvularia sin letra ----
def _pick_k_preferida(anio: int, nivel: str) -> Curso | None:
    # iExact no ignora tildes → hacemos ambas rutas
    qs = Curso.objects.filter(anio=anio)
    # normalizamos
    target = _strip_accents_low(nivel)
    candidatos = [c for c in qs if _strip_accents_low(c.nivel) == target]
    if not candidatos:
        return None
    # preferir A
    for c in candidatos:
        if (_canon_letra(c.letra) or "") == "A":
            return c
    # si no hay A, la primera por letra
    candidatos = sorted(candidatos, key=lambda x: (_canon_letra(x.letra) or "Z"))
    return candidatos[0] if candidatos else None

# ---- Búsquedas tolerantes de curso ----
def _find_curso_tolerante(anio: int, nivel: str | None, letra: str | None) -> Curso | None:
    if not nivel:
        return None
    letra = _canon_letra(letra)
    nivel_can = _canon_nivel(nivel) or nivel

    # exacto por nivel/letra
    qs = Curso.objects.filter(anio=anio, nivel=nivel_can)
    if letra: qs = qs.filter(letra=letra)
    c = qs.first()
    if c: return c

    # por normalización (sin tildes)
    target = _strip_accents_low(nivel_can)
    candidatos = [c for c in Curso.objects.filter(anio=anio) if _strip_accents_low(c.nivel) == target]
    if letra:
        candidatos = [c for c in candidatos if (_canon_letra(c.letra) or None) == letra]
    if candidatos:
        for x in candidatos:
            if (_canon_letra(x.letra) or "") == "A":
                return x
        return sorted(candidatos, key=lambda x: _canon_letra(x.letra) or "Z")[0]

    return None

# ------------- Parser INSERT -------------
def _parse_insert_lines(sql_text: str):
    INSERT_RE = re.compile(
        r"INSERT\s+INTO\s+`?(?P<table>[\w\d_]+)`?\s*\((?P<cols>[^)]+)\)\s+VALUES\s*(?P<values>.+?);",
        re.IGNORECASE | re.DOTALL
    )
    TUPLES_RE = re.compile(r"\((.*?)\)", re.DOTALL)

    for m in INSERT_RE.finditer(sql_text):
        table = m.group("table")
        cols = [c.strip(" `") for c in m.group("cols").split(",")]
        values_blob = m.group("values")
        tuples = TUPLES_RE.findall(values_blob)

        rows = []
        for tup in tuples:
            parts, cur, in_str, quote = [], [], False, None
            i = 0
            while i < len(tup):
                ch = tup[i]
                if in_str:
                    if ch == quote:
                        in_str = False; cur.append(ch)
                    elif ch == "\\" and i + 1 < len(tup):
                        cur.append(ch); i += 1; cur.append(tup[i])
                    else:
                        cur.append(ch)
                else:
                    if ch in ("'", '"'):
                        in_str = True; quote = ch; cur.append(ch)
                    elif ch == ",":
                        parts.append("".join(cur).strip()); cur = []
                    else:
                        cur.append(ch)
                i += 1
            if cur: parts.append("".join(cur).strip())

            clean = []
            for p in parts:
                if p is None:
                    clean.append(None)
                else:
                    s = str(p)
                    if s.upper() == "NULL":
                        clean.append(None)
                    elif len(s) >= 2 and ((s[0] == "'" and s[-1] == "'") or (s[0] == '"' and s[-1] == '"')):
                        clean.append(s[1:-1])
                    else:
                        clean.append(s)
            rows.append(clean)

        yield table, cols, rows

# ------------- Runner principal -------------
def import_sqldump(*, dump_path: str, anio: int, permitir_sin_nivel: bool, stdout: io.TextIOBase, stderr: io.TextIOBase):
    if not AcademicYear.objects.filter(anio=anio, is_visible=True).exists():
        raise ValueError(f"El año {anio} no está habilitado/visible.")

    with open(dump_path, "r", encoding="utf-8", errors="ignore") as f:
        sql_text = f.read()

    print(f"Iniciando import desde dump: {dump_path}", file=stdout)
    print(f"Año destino: {anio} | permitir_sin_nivel={permitir_sin_nivel}", file=stdout)

    cursos_cache = {}  # (nivel, letra, anio) -> Curso
    created = {"cursos": 0, "estudiantes": 0}
    updated = {"cursos": 0, "estudiantes": 0}
    skipped = {"cursos": 0, "estudiantes": 0}
    fallbacks = []  # ejemplos que cayeron a comodín

    curso_comodin = _get_or_create_curso_comodin(anio) if permitir_sin_nivel else None

    @transaction.atomic
    def _apply_inserts():
        for table, cols, rows in _parse_insert_lines(sql_text):
            t = table.lower()

            # -------- CURSOS (si el dump trae tabla de cursos) --------
            if t in ("app_curso", "curso", "cursos"):
                for vals in rows:
                    rec = dict(zip(cols, vals))
                    nivel_raw = _norm(rec.get("nivel"))
                    nivel = _canon_nivel(nivel_raw) or nivel_raw
                    letra = _canon_letra(rec.get("letra"))
                    try:
                        anio_rec = int(rec.get("anio") or anio)
                    except Exception:
                        anio_rec = anio

                    if not nivel or not anio_rec:
                        skipped["cursos"] += 1
                        continue

                    key = (nivel, letra, anio_rec)
                    if key not in cursos_cache:
                        c, was_created = Curso.objects.get_or_create(nivel=nivel, letra=letra, anio=anio_rec)
                        cursos_cache[key] = c
                        if was_created: created["cursos"] += 1
                        else: updated["cursos"] += 1

            # -------- ESTUDIANTES --------
            elif t in ("app_estudiante", "estudiante", "estudiantes"):
                for vals in rows:
                    rec = dict(zip(cols, vals))

                    rut = _norm_rut(_pick("rut", "run", "dni", _rec=rec))
                    nombre = _norm(_pick("nombre_completo", "nombre", "nombres", _rec=rec)) or "SIN NOMBRE"

                    curso = None
                    # 1) FK directa
                    curso_id_val = _pick("curso_id", "curso_fk_id", "curso_id_fk", _rec=rec)
                    if curso_id_val:
                        try:
                            curso = Curso.objects.get(id=int(curso_id_val))
                        except Exception:
                            curso = None

                    # 2) Resolver por nivel/letra/año
                    if not curso:
                        # Por defecto, usamos el año de importación
                        anio_rec = anio

                        nivel_raw = _pick("curso_nivel", "nivel", _rec=rec)
                        letra_raw = _pick("curso_letra", "letra", _rec=rec)
                        nivel = _canon_nivel(nivel_raw) if nivel_raw else None
                        letra = _canon_letra(letra_raw)

                        # 2.1 texto combinado (puede traer año)
                        if not nivel:
                            curso_txt = _pick("curso", "curso_nombre", "curso_texto", "curso_str", _rec=rec)
                            if curso_txt:
                                nivel_guess, letra_guess, anio_guess = _parse_curso_text(curso_txt)
                                if nivel_guess: nivel = nivel_guess
                                if letra_guess and not letra: letra = letra_guess
                                if anio_guess: anio_rec = anio_guess  # solo si lo especifica el string

                        # 2.2 Kinder/Prekinder sin letra → elegir sección existente
                        if nivel in {"Prekinder", "Kinder"} and not letra:
                            pref = _pick_k_preferida(anio_rec, nivel)
                            if pref:
                                curso = pref

                        # 2.3 Resolver contra catálogo (tolerante)
                        if not curso and nivel:
                            key = (nivel, letra, anio_rec)
                            curso = cursos_cache.get(key)
                            if not curso:
                                curso = _find_curso_tolerante(anio_rec, nivel, letra)
                                if not curso and letra:
                                    curso = Curso.objects.filter(anio=anio_rec, nivel=nivel, letra=letra).first()
                                if not curso:
                                    qs = Curso.objects.filter(anio=anio_rec)
                                    # si hay solo una sección para ese nivel, úsala
                                    cand = [c for c in qs if _strip_accents_low(c.nivel) == _strip_accents_low(nivel)]
                                    if len(cand) == 1:
                                        curso = cand[0]
                                if not curso:
                                    # última opción: crear el curso
                                    curso, _ = Curso.objects.get_or_create(anio=anio_rec, nivel=nivel, letra=letra)
                                cursos_cache[key] = curso

                    if not curso and permitir_sin_nivel:
                        fallbacks.append((
                            rut,
                            _norm(_pick("nivel","curso_nivel","curso","curso_nombre","curso_texto","curso_str", _rec=rec)),
                            _norm(_pick("letra","curso_letra", _rec=rec))
                        ))
                        curso = curso_comodin

                    if not rut or not curso:
                        skipped["estudiantes"] += 1
                        continue

                    # Campos extendidos (con defaults seguros)
                    fecha_nacimiento = _coerce_date(_pick("fecha_nacimiento","nacimiento", _rec=rec), date(2000,1,1))
                    sexo = _norm(_pick("sexo","genero","género", _rec=rec)) or "Masculino"
                    nacionalidad = _norm(_pick("nacionalidad","pais","país", _rec=rec)) or "Chilena"
                    domicilio = _norm(_pick("domicilio","direccion","dirección", _rec=rec)) or "S/I"
                    comuna = _norm(_pick("comuna","ciudad", _rec=rec)) or "S/I"

                    ascendencia_indigena = _coerce_bool(_pick("ascendencia_indigena","ascendencia", _rec=rec))
                    etnia = _norm(_pick("etnia", _rec=rec))
                    pertenece_al_pie = _coerce_bool(_pick("pertenece_al_pie","pie", _rec=rec))
                    ha_repetido_curso = _coerce_bool(_pick("ha_repetido_curso","repitente", _rec=rec))

                    nombre_madre = _norm(_pick("nombre_madre", _rec=rec))
                    rut_madre = _norm_rut(_pick("rut_madre", _rec=rec))
                    nivel_educacional_madre = _norm(_pick("nivel_educacional_madre", _rec=rec))
                    ocupacion_madre = _norm(_pick("ocupacion_madre", _rec=rec))
                    telefono_madre = _norm(_pick("telefono_madre","fono_madre", _rec=rec))
                    email_madre = _norm(_pick("email_madre","correo_madre", _rec=rec))

                    nombre_padre = _norm(_pick("nombre_padre", _rec=rec))
                    rut_padre = _norm_rut(_pick("rut_padre", _rec=rec))
                    nivel_educacional_padre = _norm(_pick("nivel_educacional_padre", _rec=rec))
                    ocupacion_padre = _norm(_pick("ocupacion_padre", _rec=rec))
                    telefono_padre = _norm(_pick("telefono_padre","fono_padre", _rec=rec))
                    email_padre = _norm(_pick("email_padre","correo_padre", _rec=rec))

                    nombre_apoderado = _norm(_pick("nombre_apoderado", _rec=rec))
                    rut_apoderado = _norm_rut(_pick("rut_apoderado", _rec=rec))
                    nivel_educacional_apoderado = _norm(_pick("nivel_educacional_apoderado", _rec=rec))
                    ocupacion_apoderado = _norm(_pick("ocupacion_apoderado", _rec=rec))
                    telefono_apoderado = _norm(_pick("telefono_apoderado","fono_apoderado", _rec=rec))
                    email_apoderado = _norm(_pick("email_apoderado","correo_apoderado", _rec=rec))
                    fecha_nacimiento_apoderado = _coerce_date(_pick("fecha_nacimiento_apoderado", _rec=rec), None)

                    nombre_apoderado_suplente = _norm(_pick("nombre_apoderado_suplente", _rec=rec))
                    rut_apoderado_suplente = _norm_rut(_pick("rut_apoderado_suplente", _rec=rec))
                    nivel_educacional_apoderado_suplente = _norm(_pick("nivel_educacional_apoderado_suplente", _rec=rec))
                    ocupacion_apoderado_suplente = _norm(_pick("ocupacion_apoderado_suplente", _rec=rec))
                    telefono_apoderado_suplente = _norm(_pick("telefono_apoderado_suplente","fono_apoderado_suplente", _rec=rec))
                    email_apoderado_suplente = _norm(_pick("email_apoderado_suplente","correo_apoderado_suplente", _rec=rec))

                    vive_con = _norm(_pick("vive_con", _rec=rec)) or "Otros"
                    resolucion_judicial = _coerce_bool(_pick("resolucion_judicial", _rec=rec))
                    problemas_salud = _coerce_bool(_pick("problemas_salud", _rec=rec))
                    descripcion_problemas_salud = _norm(_pick("descripcion_problemas_salud","detalle_salud", _rec=rec))
                    impide_educacion_fisica = _coerce_bool(_pick("impide_educacion_fisica","eximicion_ef","exención_ef", _rec=rec))
                    tipo_transporte = _norm(_pick("tipo_transporte", _rec=rec))
                    opta_religion = _coerce_bool(_pick("opta_religion", _rec=rec))
                    acepta_fotografias = _coerce_bool(_pick("acepta_fotografias", _rec=rec))
                    certificado_medico = _coerce_bool(_pick("certificado_medico", _rec=rec))
                    de_donde_viene = _norm(_pick("de_donde_viene","procedencia", _rec=rec))
                    fecha_matricula = _coerce_date(_pick("fecha_matricula", _rec=rec), date.today())
                    sistema_salud = _norm(_pick("sistema_salud", _rec=rec)) or "Fonasa"
                    estado = _norm(_pick("estado", _rec=rec)) or "Activo"

                    obj, was_created = Estudiante.objects.get_or_create(
                        rut=rut,
                        defaults=dict(
                            nombre_completo=nombre,
                            fecha_nacimiento=fecha_nacimiento,
                            sexo=sexo,
                            nacionalidad=nacionalidad,
                            domicilio=domicilio,
                            comuna=comuna,
                            ascendencia_indigena=ascendencia_indigena,
                            etnia=etnia or None,
                            pertenece_al_pie=pertenece_al_pie,
                            ha_repetido_curso=ha_repetido_curso,
                            nombre_madre=nombre_madre or None,
                            rut_madre=rut_madre or None,
                            nivel_educacional_madre=nivel_educacional_madre or None,
                            ocupacion_madre=ocupacion_madre or None,
                            telefono_madre=telefono_madre or None,
                            email_madre=email_madre or None,
                            nombre_padre=nombre_padre or None,
                            rut_padre=rut_padre or None,
                            nivel_educacional_padre=nivel_educacional_padre or None,
                            ocupacion_padre=ocupacion_padre or None,
                            telefono_padre=telefono_padre or None,
                            email_padre=email_padre or None,
                            nombre_apoderado=nombre_apoderado or None,
                            rut_apoderado=rut_apoderado or None,
                            nivel_educacional_apoderado=nivel_educacional_apoderado or None,
                            ocupacion_apoderado=ocupacion_apoderado or None,
                            telefono_apoderado=telefono_apoderado or None,
                            email_apoderado=email_apoderado or None,
                            nombre_apoderado_suplente=nombre_apoderado_suplente or None,
                            rut_apoderado_suplente=rut_apoderado_suplente or None,
                            nivel_educacional_apoderado_suplente=nivel_educacional_apoderado_suplente or None,
                            ocupacion_apoderado_suplente=ocupacion_apoderado_suplente or None,
                            telefono_apoderado_suplente=telefono_apoderado_suplente or None,
                            email_apoderado_suplente=email_apoderado_suplente or None,
                            vive_con=vive_con,
                            resolucion_judicial=resolucion_judicial,
                            problemas_salud=problemas_salud,
                            descripcion_problemas_salud=descripcion_problemas_salud or None,
                            impide_educacion_fisica=impide_educacion_fisica,
                            tipo_transporte=tipo_transporte or None,
                            opta_religion=opta_religion,
                            acepta_fotografias=acepta_fotografias,
                            estado=estado,
                            certificado_medico=certificado_medico,
                            de_donde_viene=de_donde_viene or None,
                            fecha_matricula=fecha_matricula,
                            sistema_salud=sistema_salud,
                            curso=curso,
                            fecha_nacimiento_apoderado=fecha_nacimiento_apoderado,
                        )
                    )
                    if was_created:
                        created["estudiantes"] += 1
                        _ensure_matricula(anio, obj, curso)
                    else:
                        fields_to_update = []
                        def _set(field, value):
                            nonlocal fields_to_update
                            if value not in (None, "", "NULL"):
                                setattr(obj, field, value); fields_to_update.append(field)

                        _set("nombre_completo", nombre)
                        _set("fecha_nacimiento", fecha_nacimiento or date(2000,1,1))
                        _set("sexo", sexo or "Masculino")
                        _set("nacionalidad", nacionalidad or "Chilena")
                        _set("domicilio", domicilio or "S/I")
                        _set("comuna", comuna or "S/I")
                        _set("sistema_salud", sistema_salud or "Fonasa")
                        _set("estado", estado or "Activo")
                        _set("curso", curso)
                        _set("fecha_matricula", fecha_matricula or date.today())

                        _set("etnia", etnia)
                        _set("descripcion_problemas_salud", descripcion_problemas_salud)
                        _set("tipo_transporte", tipo_transporte)
                        _set("de_donde_viene", de_donde_viene)

                        # flags booleanas
                        obj.ascendencia_indigena = ascendencia_indigena
                        obj.pertenece_al_pie = pertenece_al_pie
                        obj.ha_repetido_curso = ha_repetido_curso
                        obj.resolucion_judicial = resolucion_judicial
                        obj.problemas_salud = problemas_salud
                        obj.impide_educacion_fisica = impide_educacion_fisica
                        obj.opta_religion = opta_religion
                        obj.acepta_fotografias = acepta_fotografias
                        obj.certificado_medico = certificado_medico

                        obj.save(update_fields=list(set(fields_to_update)))
                        updated["estudiantes"] += 1
                        _ensure_matricula(anio, obj, curso)

            else:
                continue

    _apply_inserts()

    print("==== RESUMEN ====", file=stdout)
    for k in ("cursos", "estudiantes"):
        print(f"{k}: creados={created[k]} | actualizados={updated[k]} | omitidos={skipped[k]}", file=stdout)

    if fallbacks:
        print("---- Advertencias (se usó curso comodín) ----", file=stdout)
        for rut, niv, let in fallbacks[:25]:
            print(f"RUT {rut}: nivel/curso='{niv}' letra='{let}' no mapeó → {NIVEL_POR_ASIGNAR}", file=stdout)
        if len(fallbacks) > 25:
            print(f"... y {len(fallbacks)-25} más", file=stdout)

    print("Importación completada.", file=stdout)
