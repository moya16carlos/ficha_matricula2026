# from django.db import migrations, models
# import django.db.models.deletion


# def parse_nivel_letra(texto):
#     if not texto:
#         return None, None
#     s = str(texto).strip()
#     partes = s.split()
#     if len(partes) >= 2 and partes[-1].isalpha() and len(partes[-1]) <= 2:
#         letra = partes[-1].upper()
#         nivel = " ".join(partes[:-1])
#     else:
#         nivel, letra = s, None
#     return nivel, letra


# def _bulk_update_fk(schema_editor, Estudiante, pairs):
#     if not pairs:
#         return
#     ids = [str(eid) for eid, _ in pairs]
#     cases = " ".join([f"WHEN {eid} THEN {cid}" for eid, cid in pairs])
#     table = Estudiante._meta.db_table
#     sql = f"UPDATE {table} SET curso_fk_id = CASE id {cases} END WHERE id IN ({','.join(ids)});"
#     with schema_editor.connection.cursor() as cur:
#         cur.execute(sql)


# def forwards_fill_fk(apps, schema_editor):
#     Estudiante = apps.get_model('app', 'Estudiante')
#     Curso = apps.get_model('app', 'Curso')

#     ANIO = 2025
#     batch = []
#     BATCH_SIZE = 500

#     # Trae ambas posibles columnas para no disparar descriptores
#     qs = Estudiante.objects.all().only('id')  # evitamos .only('curso') porque puede mapear a relación
#     for e in qs.iterator(chunk_size=1000):
#         d = e.__dict__
#         curso_fk_val = d.get('curso_fk_id')  # si por alguna razón ya existe
#         curso_id_val = d.get('curso_id')     # si 'curso' ya era FK en BD
#         curso_txt_val = d.get('curso')       # si 'curso' aún es CharField en BD

#         # 1) Caso: ya hay un id numérico (curso_id o curso_fk_id)
#         v_id = curso_fk_val or curso_id_val
#         if isinstance(v_id, int) and v_id:
#             # si el id existe en Curso, úsalo; si no, crea "Sin Nivel"
#             if Curso.objects.filter(id=v_id).exists():
#                 batch.append((e.id, v_id))
#             else:
#                 misc, _ = Curso.objects.get_or_create(nivel='Sin Nivel', letra=None, anio=ANIO)
#                 batch.append((e.id, misc.id))

#         else:
#             # 2) Caso: viene texto en la columna 'curso'
#             nivel, letra = parse_nivel_letra(curso_txt_val or '')
#             if nivel:
#                 curso, _ = Curso.objects.get_or_create(nivel=nivel, letra=letra, anio=ANIO)
#                 batch.append((e.id, curso.id))
#             else:
#                 misc, _ = Curso.objects.get_or_create(nivel='Sin Nivel', letra=None, anio=ANIO)
#                 batch.append((e.id, misc.id))

#         # flush por lotes
#         if len(batch) >= BATCH_SIZE:
#             _bulk_update_fk(schema_editor, Estudiante, batch)
#             batch = []

#     if batch:
#         _bulk_update_fk(schema_editor, Estudiante, batch)


# def noop_reverse(apps, schema_editor):
#     pass


# class Migration(migrations.Migration):

#     dependencies = [
#         ('app', '0016_auto_20251031_2308'),
#     ]

#     operations = [
#         migrations.SeparateDatabaseAndState(
#             state_operations=[
#                 migrations.AddField(
#                     model_name='estudiante',
#                     name='curso_fk',
#                     field=models.ForeignKey(
#                         null=True, blank=True,
#                         to='app.curso',
#                         on_delete=django.db.models.deletion.PROTECT,
#                         related_name='estudiantes'
#                     ),
#                 ),
#             ],
#             database_operations=[],
#         ),

#         migrations.RunPython(forwards_fill_fk, reverse_code=noop_reverse),

#         migrations.AlterField(
#             model_name='estudiante',
#             name='curso_fk',
#             field=models.ForeignKey(
#                 null=False,
#                 to='app.curso',
#                 on_delete=django.db.models.deletion.PROTECT,
#                 related_name='estudiantes'
#             ),
#         ),

#         # 🔥 Eliminar columna vieja de texto antes de renombrar
#         migrations.RemoveField(
#             model_name='estudiante',
#             name='curso',
#         ),

#         # ✅ Ahora sí renombramos curso_fk -> curso
#         migrations.RenameField(
#             model_name='estudiante',
#             old_name='curso_fk',
#             new_name='curso',
#         ),
#     ]

from django.db import migrations, models
import django.db.models.deletion


def parse_nivel_letra(texto):
    if not texto:
        return None, None
    s = str(texto).strip()
    partes = s.split()
    if len(partes) >= 2 and partes[-1].isalpha() and len(partes[-1]) <= 2:
        letra = partes[-1].upper()
        nivel = " ".join(partes[:-1])
    else:
        nivel, letra = s, None
    return nivel, letra


def _bulk_update_fk(schema_editor, Estudiante, pairs):
    if not pairs:
        return
    ids = [str(eid) for eid, _ in pairs]
    cases = " ".join([f"WHEN {eid} THEN {cid}" for eid, cid in pairs])
    table = Estudiante._meta.db_table
    sql = f"UPDATE {table} SET curso_id = CASE id {cases} END WHERE id IN ({','.join(ids)});"
    # NOTA: Después renombraremos el campo a 'curso', pero mientras se llama 'curso_fk',
    # la columna física es 'curso_fk_id'. Para simplificar, primero lo agregamos con nombre 'curso_fk',
    # rellenamos 'curso_fk_id', y recién al final lo renombramos a 'curso'.
    # Si prefieres escribir a 'curso_fk_id' explícitamente, cambia `curso_id` por `curso_fk_id` arriba.
    with schema_editor.connection.cursor() as cur:
        # Intento 1: escribir a 'curso_fk_id' (nombre real tras AddField)
        try:
            cur.execute(sql.replace("curso_id", "curso_fk_id"))
        except Exception:
            # Intento 2: fallback si la columna temporalmente se llama distinto
            cur.execute(sql)


def forwards_fill_fk(apps, schema_editor):
    Estudiante = apps.get_model('app', 'Estudiante')
    Curso = apps.get_model('app', 'Curso')

    ANIO = 2025
    batch = []
    BATCH_SIZE = 500

    qs = Estudiante.objects.all().only('id')
    for e in qs.iterator(chunk_size=1000):
        d = e.__dict__
        curso_fk_val = d.get('curso_fk_id')
        curso_id_val = d.get('curso_id')   # si existiera un FK previo llamado 'curso'
        curso_txt_val = d.get('curso')     # si era CharField

        v_id = curso_fk_val or curso_id_val
        if isinstance(v_id, int) and v_id:
            if Curso.objects.filter(id=v_id).exists():
                batch.append((e.id, v_id))
            else:
                misc, _ = Curso.objects.get_or_create(nivel='Sin Nivel', letra=None, anio=ANIO)
                batch.append((e.id, misc.id))
        else:
            nivel, letra = parse_nivel_letra(curso_txt_val or '')
            if nivel:
                curso, _ = Curso.objects.get_or_create(nivel=nivel, letra=letra, anio=ANIO)
                batch.append((e.id, curso.id))
            else:
                misc, _ = Curso.objects.get_or_create(nivel='Sin Nivel', letra=None, anio=ANIO)
                batch.append((e.id, misc.id))

        if len(batch) >= BATCH_SIZE:
            _bulk_update_fk(schema_editor, Estudiante, batch)
            batch = []

    if batch:
        _bulk_update_fk(schema_editor, Estudiante, batch)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0016_auto_20251031_2308'),
    ]

    operations = [
        # 1) AGREGAR realmente la columna en la BD (no solo en el estado)
        migrations.AddField(
            model_name='estudiante',
            name='curso_fk',
            field=models.ForeignKey(
                null=True, blank=True,
                to='app.curso',
                on_delete=django.db.models.deletion.PROTECT,
                related_name='estudiantes'
            ),
        ),

        # 2) RELLENAR datos del FK nuevo desde el antiguo (FK o texto)
        migrations.RunPython(forwards_fill_fk, reverse_code=noop_reverse),

        # 3) Hacerlo NOT NULL una vez poblado
        migrations.AlterField(
            model_name='estudiante',
            name='curso_fk',
            field=models.ForeignKey(
                null=False,
                to='app.curso',
                on_delete=django.db.models.deletion.PROTECT,
                related_name='estudiantes'
            ),
        ),

        # 4) Borrar la columna vieja de texto (si existía)
        migrations.RemoveField(
            model_name='estudiante',
            name='curso',
        ),

        # 5) Renombrar curso_fk -> curso (nombre final en modelos)
        migrations.RenameField(
            model_name='estudiante',
            old_name='curso_fk',
            new_name='curso',
        ),
    ]

