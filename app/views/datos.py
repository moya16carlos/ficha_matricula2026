"""Exportación e importación de datos (CSV/JSON/SQL dump)."""
from ._common import *  # noqa: F401,F403
import csv
import io
import tempfile
import unicodedata
from collections import defaultdict
from django.core.serializers.json import DjangoJSONEncoder
from django.forms.models import model_to_dict
from django.utils.text import slugify

def _append_csv_log(session, line):
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
            _append_csv_log(request.session, f"❌ {err}")
        messages.error(request, "Validación fallida: faltan columnas requeridas en uno o más datasets.")
        return redirect("panel_config_documentos")
    else:
        _append_csv_log(request.session, "✅ Validación OK: columnas mínimas presentes.")

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

                        e = estudiante_por_rut(rut)  # tolera RUT legado con guion
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

                        e = estudiante_por_rut(rut)  # tolera RUT legado con guion
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
            _append_csv_log(request.session, f"❌ Error en {ds}: {e}")
            messages.error(request, f"Error importando {ds}: {e}")

    parts = [
        f"{k}: Creados {v['created']}, Actualizados {v['updated']}, Omitidos {v['skipped']}"
        for k, v in summary.items()
        if (v['created'] + v['updated'] + v['skipped']) > 0
    ]
    if parts:
        messages.success(request, f"Importación completada ({modo}). " + " · ".join(parts))
        _append_csv_log(request.session, "✅ " + " · ".join(parts))
    else:
        messages.info(request, "No se importaron filas.")

    return redirect("panel_config_documentos")

######## importacion por sql###########
from app.sql_import_runner import import_sqldump as run_import_sqldump  # 👈 alias seguro


def _is_staff_or_superuser(user):
    return user.is_active and (user.is_staff or user.is_superuser)

def _append_sql_log(session, line):
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
            _append_sql_log(request.session, "=== STDOUT ===")
            for line in stdout_txt.splitlines():
                _append_sql_log(request.session, line)
        if stderr_txt:
            _append_sql_log(request.session, "=== STDERR ===")
            for line in stderr_txt.splitlines():
                _append_sql_log(request.session, line)

        if stderr_txt:
            messages.warning(request, "Import terminado con advertencias. Revisa el log al final del panel.")
        else:
            messages.success(request, "Importación completada desde .sql. Revisa el log al final del panel.")
    except Exception as e:
        _append_sql_log(request.session, f"❌ ERROR: {e}")
        messages.error(request, f"Falló la importación: {e}")
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass

    return redirect("panel_config_documentos")
