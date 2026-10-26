"""Vistas de generación de PDF (ficha individual, listado por curso, preview)."""
from ._common import *  # noqa: F401,F403
from io import BytesIO
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfgen import canvas  # noqa: F401 (paridad con el monolito)
from reportlab.platypus import (
    HRFlowable, Image, KeepTogether, PageBreak,
    Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)
from PIL import Image as PILImage

# El logo original y las firmas subidas pueden ser fotos enormes (el logo es
# 5906x5906): incrustarlas a resolución completa infla cada PDF a ~1.4MB y lo
# vuelve lento. Se reducen a máx 512px y se cachean por ruta+mtime.
_img_cache = {}

def _img_chica(path):
    try:
        mtime = os.path.getmtime(path)
        hit = _img_cache.get(path)
        if hit and hit[0] == mtime:
            return BytesIO(hit[1])
        im = PILImage.open(path)
        im.thumbnail((512, 512))
        buf = BytesIO()
        if im.mode in ('RGBA', 'LA') or (im.mode == 'P' and 'transparency' in im.info):
            im.save(buf, 'PNG', optimize=True)
        else:
            im.convert('RGB').save(buf, 'JPEG', quality=85)
        _img_cache[path] = (mtime, buf.getvalue())
        return BytesIO(buf.getvalue())
    except Exception:
        return path  # sin Pillow o imagen rota: usa el archivo original


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

    # --- 2) Buscar estudiante por RUT (tolera formato legado con guion) ---
    rut_param = _norm_rut(rut)
    base_est = Estudiante.objects.select_related('curso', 'matriculado_por')
    estudiante = estudiante_por_rut(rut_param)
    if not estudiante and rut_param.isdigit():
        estudiante = base_est.filter(rut__startswith=rut_param).order_by('rut').first()
    if not estudiante:
        raise Http404("Estudiante no encontrado con el RUT indicado.")

    # --- 3) Traer SOLO la matrícula del año solicitado (priorizando curso asignado) ---
    ma = (
        MatriculaAnual.objects
        .filter(estudiante=estudiante, anio=anio_view)
        .select_related('curso')
        # compat Django 5: "-curso__isnull" ya no es order_by válido
        .annotate(tiene_curso=Case(When(curso__isnull=False, then=1), default=0, output_field=IntegerField()))
        .order_by('-tiene_curso', '-id')  # curso NO nulo primero, luego la más nueva
        .first()
    )
    curso_view = getattr(ma, 'curso', None)

    # Si no hay matrícula y existe placeholder "Por Asignar" de ese año, úsalo SOLO para el título/subtítulo
    if not curso_view:
        placeholder = Curso.objects.filter(anio=anio_view, nivel=NIVEL_POR_ASIGNAR, letra=None).first()
        if placeholder:
            curso_view = placeholder  # OJO: sigue significando "sin curso asignado", es solo para la visual.

    _require_curso_perm(request.user, 'print', getattr(ma, 'curso', None) or estudiante.curso)
    _require_curso_perm(request.user, 'print', estudiante.curso)
    estudiante = _estudiante_en_matricula(estudiante, ma)

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

    if ma and not ma.datos_congelados:
        elements.append(Paragraph(
            'Esta matrícula no conserva antecedentes históricos completos; se muestran los datos actuales.',
            styles['Normal'],
        ))

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
    logo_path = os.path.join(settings.BASE_DIR, 'app', 'static', 'app', 'img', 'logo.png')
    header_text = Paragraph(
        "LICEO MUNICIPAL DE SAN PEDRO<br/>"
        "R.B.D 10854-5. Carretera de la Fruta km 9.5, San Pedro de Melipilla.<br/>"
        "Provincia de Melipilla, Región Metropolitana.",
        ParagraphStyle('hdr', parent=styles['Normal'], alignment=1, fontSize=8, leading=10)
    )
    if os.path.exists(logo_path):
        header_tbl = Table([[Image(_img_chica(logo_path), width=40, height=40), header_text]], colWidths=[50, 460])
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
        firma_path = os.path.join(settings.BASE_DIR, 'app', 'static', 'app', 'img', 'firma_director.jpeg')

    firma_director_img = Image(_img_chica(firma_path), width=100, height=50) if os.path.exists(firma_path) else None
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
@role_required(*PERMITIDOS_TODOS)  # seguridad: antes esta vista era pública
def imprimir_estudiantes_por_curso(request, curso_id):
    # === 1) Resolver el curso (y por ende el año visualizado) ===
    curso = get_object_or_404(Curso, pk=curso_id)
    _require_curso_perm(request.user, 'print', curso)
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
        logo_path = os.path.join(settings.BASE_DIR, 'app', 'static', 'app', 'img', 'logo.png')
        header_text = Paragraph(
            "LICEO MUNICIPAL DE SAN PEDRO<br/>"
            "R.B.D 10854-5. Carretera de la Fruta km 9.5, San Pedro de Melipilla.<br/>"
            "Provincia de Melipilla, Región Metropolitana.",
            ParagraphStyle('hdr', parent=styles['Normal'], alignment=1, fontSize=8, leading=10)
        )
        if os.path.exists(logo_path):
            header_tbl = Table([[Image(_img_chica(logo_path), width=40, height=40), header_text]], colWidths=[50, 460])
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
        _require_curso_perm(request.user, 'print', ma.estudiante.curso)
        estudiante = _estudiante_en_matricula(ma.estudiante, ma)
        agregar_encabezado()
        if not ma.datos_congelados:
            elements.append(Paragraph(
                'Esta matrícula no conserva antecedentes históricos completos; se muestran los datos actuales.',
                styles['Normal'],
            ))

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
            firma_path = os.path.join(settings.BASE_DIR, 'app', 'static', 'app', 'img', 'firma_director.jpeg')

        firma_director_img = Image(_img_chica(firma_path), width=100, height=50) if os.path.exists(firma_path) else None
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
    logo = Image(_img_chica(logo_path), width=40, height=40) if os.path.exists(logo_path) else Paragraph('', styles['Normal'])

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

    firma_img = Image(_img_chica(firma_path), width=120, height=60) if firma_path and os.path.exists(firma_path) else None
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

