import json
import io
from datetime import date
from importlib import import_module
from html.parser import HTMLParser
from tempfile import NamedTemporaryFile
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, connection, transaction
from django.test import TestCase
from django.urls import reverse

from app.models import (
    AcademicYear, Apoderado, ConfiguracionMatricula, ConfiguracionSistema,
    Curso, DocumentoEstudiante, Estudiante, MatriculaAnual, Usuarios,
)
from app.tests import make_user
from app.views.anios import map_curso_destino, next_nivel, seed_matricula_from_estudiantes
from app.views._common import _estudiante_en_matricula


class RegresionesBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user('111111111', rol='ADMINISTRADOR')
        cls.user = make_user('222222222')
        cls.year = AcademicYear.objects.create(anio=2026)
        cls.basica = Curso.objects.create(nivel='1° Básico', letra='A', anio=2026)
        cls.media = Curso.objects.create(nivel='1° Medio', letra='A', anio=2026)
        cls.kinder = Curso.objects.create(nivel='Kinder', letra='A', anio=2026)
        cls.est = Estudiante.objects.create(
            rut='123456785', nombre_completo='Estudiante Básica',
            fecha_nacimiento=date(2015, 1, 1), sexo='Femenino', curso=cls.basica,
            nacionalidad='Chilena', domicilio='Domicilio original', comuna='San Pedro',
            vive_con='Madre', fecha_matricula=date(2026, 3, 1),
            telefono_apoderado='5550123', problemas_salud=True,
            descripcion_problemas_salud='Alergia', acepta_fotografias=False,
            fecha_nacimiento_apoderado=date(1980, 2, 3),
            parentesco_apoderado_suplente='Tía', seguro_salud_complementario='Seguro',
            matriculado_por=cls.admin,
        )
        cls.ma = MatriculaAnual.objects.create(
            estudiante=cls.est, anio=2026,
            **MatriculaAnual.defaults_desde_estudiante(cls.est),
        )

    def payload(self, **changes):
        data = {
            'rut': self.est.rut, 'nombre_completo': self.est.nombre_completo,
            'curso': self.basica.pk, 'anio': 2026, 'anio_filtro': 2026,
            'fecha_nacimiento': '2015-01-01', 'fecha_matricula': '2026-03-01',
            'sexo': 'Femenino', 'nacionalidad': 'Chilena', 'domicilio': 'Domicilio original',
            'comuna': 'San Pedro', 'sistema_salud': 'Fonasa', 'vive_con': 'Madre',
        }
        return dict(data, **changes)

    def importar(self, data, datasets=('estudiantes',), **options):
        upload = SimpleUploadedFile('datos.json', json.dumps(data).encode(), content_type='application/json')
        return self.client.post(reverse('import_datos_panel'), dict(
            archivo=upload, datasets=list(datasets), anio_destino=2026, **options,
        ))


class CampusDisenoTests(RegresionesBase):
    def test_pantallas_internas_comparten_el_diseno(self):
        self.client.force_login(self.admin)
        for name, args in (
            ('home', []), ('listar_estudiantes', []), ('listar_estudiantes_anio', []),
            ('listar_estudiantes_admin', []), ('detalles_estudiante', [self.est.pk]),
            ('nuevo_estudiante', []), ('ficha_estudiante', []), ('listar_usuarios', []),
            ('crear_usuario', []), ('perfil_usuario', [self.admin.pk]),
            ('panel_config_documentos', []),
        ):
            with self.subTest(name=name):
                response = self.client.get(reverse(name, args=args), {'anio': 2026})
                self.assertContains(response, 'app/css/campus.css')
                self.assertContains(response, 'app/js/campus.js')
                self.assertContains(response, 'id="campus-navigation"')

    def test_formularios_guiados_conservan_campos_y_obligatorios(self):
        class Campos(HTMLParser):
            def __init__(self):
                super().__init__()
                self.fields = {}

            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag in ('input', 'select', 'textarea') and attrs.get('name'):
                    self.fields[attrs['name']] = attrs

        ConfiguracionSistema.objects.create(pk=1, registro_apoderado_habilitado=True)
        apoderado = Apoderado.objects.create(run='333333333', nombre='Familia', apellido='Test')
        session = self.client.session
        session['apoderado_id'] = apoderado.pk
        session.save()
        self.client.force_login(self.admin)
        for name in ('nuevo_estudiante', 'ficha_estudiante', 'registro_alumno'):
            with self.subTest(name=name):
                response = self.client.get(reverse(name), {'anio': 2026})
                self.assertContains(response, 'data-campus-wizard')
                fields = Campos()
                fields.feed(response.content.decode())
                for field in ('rut', 'nombre_completo', 'fecha_nacimiento', 'sexo',
                              'nacionalidad', 'domicilio', 'comuna', 'vive_con', 'fecha_matricula'):
                    self.assertIn('required', fields.fields[field])
                for field in ('csrfmiddlewaretoken', 'nombre_madre', 'nombre_padre',
                              'nombre_apoderado', 'nombre_apoderado_suplente', 'acepta_fotografias'):
                    self.assertIn(field, fields.fields)

    def test_navegacion_respeta_permisos_y_detalle_no_ofrece_edicion_sin_permiso(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('home'))
        self.assertNotContains(response, f'href="{reverse("nuevo_estudiante")}"')
        self.assertNotContains(response, f'href="{reverse("panel_config_documentos")}"')
        self.user.permisos_nivel.filter(nivel='BASICA').update(can_view=True, can_add=True)
        self.assertContains(self.client.get(reverse('home')), f'href="{reverse("nuevo_estudiante")}"')
        response = self.client.get(reverse('detalles_estudiante', args=[self.est.pk]))
        self.assertNotContains(response, 'Editar ficha')
        self.assertNotContains(response, 'Descargar PDF')
        self.assertContains(response, self.est.nombre_completo)

    def test_acciones_de_curso_exigen_el_nivel_correcto(self):
        from app.templatetags.perm_extras import permiso_curso, puede_matricular
        self.assertTrue(puede_matricular(self.admin))
        self.assertFalse(puede_matricular(self.user))
        self.user.permisos_nivel.filter(nivel='BASICA').update(can_print=True)
        self.assertTrue(permiso_curso(self.user, self.kinder, 'print'))
        self.assertFalse(permiso_curso(self.user, self.media, 'print'))
        self.assertFalse(permiso_curso(self.user, None, 'print'))

    def test_listado_y_directorio_escapan_texto_del_usuario(self):
        self.client.force_login(self.admin)
        self.est.nombre_completo = '<img src=x onerror=alert(1)>'
        self.est.save(update_fields=['nombre_completo'])
        self.user.nombre = '"><script>alert(1)</script>'
        self.user.save(update_fields=['nombre'])
        for name in ('listar_estudiantes', 'listar_usuarios'):
            response = self.client.get(reverse(name), {'anio': 2026})
            self.assertNotContains(response, '<script>alert(1)</script>')
            self.assertNotContains(response, '<img src=x')
            self.assertContains(response, '&lt;')

    def test_paginas_publicas_y_acceso_denegado_conservan_el_diseno(self):
        ConfiguracionSistema.objects.create(pk=1, registro_apoderado_habilitado=True)
        for name in ('login', 'registro_apoderado', 'alumno_matriculado'):
            self.assertContains(self.client.get(reverse(name)), 'app/css/campus.css')
        self.client.force_login(self.user)
        with self.settings(DEBUG=False):
            response = self.client.get(reverse('detalles_estudiante', args=[self.est.pk]))
        self.assertContains(response, 'app/css/campus.css', status_code=403)
        self.assertNotContains(response, 'id="campus-navigation"', status_code=403)


class PermisosRegresionTests(RegresionesBase):
    def setUp(self):
        self.client.force_login(self.user)

    def test_exportar_importar_y_configuracion_exigen_admin(self):
        for name in ('export_datos', 'export_estudiantes_por_anio'):
            with self.subTest(name=name):
                response = self.client.get(reverse(name), {'anio': 2026, 'datasets': 'usuarios'})
                self.assertRedirects(response, reverse('home'), fetch_redirect_response=False)
        self.importar({'estudiantes': [self.payload(nombre_completo='Alterado')]})
        self.est.refresh_from_db()
        self.assertEqual(self.est.nombre_completo, 'Estudiante Básica')
        self.client.post(reverse('toggle_year_visibility', args=[self.year.pk]))
        self.year.refresh_from_db()
        self.assertTrue(self.year.is_visible)
        self.client.post(reverse('agregar_anio_academico'), {'anio': 2030})
        self.assertFalse(AcademicYear.objects.filter(anio=2030).exists())

    def test_usuario_sin_permisos_no_lee_fichas_ni_documentos(self):
        doc = DocumentoEstudiante.objects.create(estudiante=self.est, nombre='privado', archivo='privado.pdf')
        for name, args, query in (
            ('buscar_estudiante', [], {'rut': self.est.rut}),
            ('detalles_estudiante', [self.est.pk], {}),
            ('descargar_documento', [doc.pk], {}),
            ('imprimir_estudiante', [self.est.rut], {'anio': 2026}),
            ('imprimir_estudiantes_por_curso', [self.basica.pk], {}),
            ('listar_estudiantes_anio', [], {'anio': 2026}),
        ):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name, args=args), query).status_code, 403)
        self.assertEqual(self.client.get(reverse('buscar_estudiantes_por_rut'), {'rut': '123'}).json(), [])
        self.assertEqual(self.client.get(reverse('busqueda_global'), {'q': 'Estudiante'}).json()['resultados'], [])

    def test_usuario_sin_permisos_no_cambia_curso_ni_crea_kinder(self):
        response = self.client.post(reverse('asignar_curso_en_anio'), {
            'rut': self.est.rut, 'anio': 2026, 'curso_id': self.media.pk,
        })
        self.assertEqual(response.status_code, 403)
        self.ma.refresh_from_db()
        self.assertEqual(self.ma.curso_id, self.basica.pk)
        for curso in (self.kinder, Curso.objects.create(nivel='Prekinder', anio=2026)):
            response = self.client.post(reverse('guardar_estudiante'),
                                        self.payload(rut='987654321', curso=curso.pk))
            self.assertEqual(response.status_code, 403)
        self.assertFalse(Estudiante.objects.filter(rut='987654321').exists())

    def test_permiso_basica_filtra_listado_y_busqueda(self):
        self.user.permisos_nivel.filter(nivel='BASICA').update(can_view=True)
        otra = Estudiante.objects.create(**{
            **{f.attname: getattr(self.est, f.attname) for f in Estudiante._meta.fields if not f.primary_key},
            'rut': '987654321', 'nombre_completo': 'Estudiante Media', 'curso_id': self.media.pk,
        })
        MatriculaAnual.objects.create(estudiante=otra, anio=2026, curso=self.media)
        response = self.client.get(reverse('listar_estudiantes_anio'), {'anio': 2026})
        self.assertContains(response, 'Estudiante Básica')
        self.assertNotContains(response, 'Estudiante Media')
        data = self.client.get(reverse('busqueda_global'), {'q': 'Estudiante'}).json()
        self.assertEqual([e['id'] for e in data['resultados']], [self.est.pk])
        self.assertEqual(self.client.get(reverse('detalles_estudiante', args=[otra.pk])).status_code, 403)

    def test_editar_exige_permiso_en_origen_y_destino(self):
        self.user.permisos_nivel.filter(nivel='BASICA').update(can_edit=True)
        response = self.client.post(reverse('asignar_curso_en_anio'), {
            'rut': self.est.rut, 'anio': 2026, 'curso_id': self.media.pk,
        })
        self.assertEqual(response.status_code, 403)
        self.est.curso = self.media
        self.est.save()
        response = self.client.post(reverse('guardar_estudiante'), self.payload())
        self.assertEqual(response.status_code, 403)

    def test_permiso_imprimir_se_aplica_al_curso(self):
        self.user.permisos_nivel.filter(nivel='BASICA').update(can_print=True)
        response = self.client.get(reverse('imprimir_estudiantes_por_curso', args=[self.basica.pk]))
        self.assertEqual(response.status_code, 200)
        response.close()
        self.assertEqual(self.client.get(reverse('imprimir_estudiantes_por_curso', args=[self.media.pk])).status_code, 403)

    def test_staff_sin_rol_admin_no_clona_ni_importa_sql(self):
        self.user.is_staff = True
        self.user.save()
        for name in ('api_clone_anio', 'api_seed_estudiantes_a_anio', 'importar_sql_dump'):
            with self.subTest(name=name):
                self.assertEqual(self.client.post(reverse(name)).status_code, 302)
        self.assertEqual(MatriculaAnual.objects.count(), 1)

    def test_exportacion_admin_no_incluye_credenciales(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('export_datos'), {
            'anio': 2026, 'datasets': 'usuarios', 'formato': 'json',
        })
        self.assertEqual(response.status_code, 200)
        for row in response.json()['usuarios']:
            self.assertNotIn('password', row)
            self.assertNotIn('is_superuser', row)

    def test_sin_nivel_exige_permisos_en_ambos_niveles(self):
        self.user.permisos_nivel.filter(nivel='BASICA').update(can_view=True)
        self.est.curso = Curso.objects.create(nivel='Sin asignar', anio=2026)
        self.est.save()
        self.assertEqual(self.client.get(reverse('buscar_estudiante'), {'rut': self.est.rut}).status_code, 403)
        self.user.permisos_nivel.filter(nivel='MEDIA').update(can_view=True)
        self.assertEqual(self.client.get(reverse('buscar_estudiante'), {'rut': self.est.rut}).status_code, 200)


class RegistroPublicoRegresionTests(RegresionesBase):
    def test_registro_deshabilitado_bloquea_get_y_post(self):
        ConfiguracionSistema.objects.create(pk=1, registro_apoderado_habilitado=False)
        for name in ('registro_apoderado', 'registro_alumno'):
            for method in ('get', 'post'):
                with self.subTest(name=name, method=method):
                    self.assertEqual(getattr(self.client, method)(reverse(name)).status_code, 403)
        self.assertFalse(Apoderado.objects.exists())

    def test_inscribir_exige_apoderado_activo_en_sesion(self):
        ConfiguracionSistema.objects.create(pk=1, registro_apoderado_habilitado=True)
        response = self.client.post(reverse('registro_alumno'), self.payload(rut='987654321'))
        self.assertRedirects(response, reverse('registro_apoderado'))
        self.assertFalse(Estudiante.objects.filter(rut='987654321').exists())
        apoderado = Apoderado.objects.create(
            run='333333333', nombre='Inactivo', apellido='Test',
            email='inactivo@example.com', password='Clave-segura-123', is_active=False,
        )
        session = self.client.session
        session['apoderado_id'] = apoderado.pk
        session.save()
        self.assertRedirects(self.client.get(reverse('registro_alumno')), reverse('registro_apoderado'))

    def test_registro_valido_congela_antecedentes_y_rechaza_anio_oculto(self):
        ConfiguracionSistema.objects.create(pk=1, registro_apoderado_habilitado=True)
        response = self.client.post(reverse('registro_apoderado'), {
            'run': '333333333', 'nombre': 'Apoderada', 'apellido': 'Test',
            'email': 'apoderada@example.com', 'password': 'Clave-segura-123',
        })
        self.assertRedirects(response, reverse('registro_alumno'))
        response = self.client.post(reverse('registro_alumno'),
                                    self.payload(rut='987654321'), HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 201)
        ma = MatriculaAnual.objects.get(estudiante__rut='987654321')
        self.assertTrue(ma.datos_congelados)
        self.assertEqual(ma.domicilio, 'Domicilio original')
        self.year.is_visible = False
        self.year.save()
        self.assertEqual(self.client.get(reverse('registro_alumno')).status_code, 400)


class MatriculasRegresionTests(RegresionesBase):
    def setUp(self):
        self.client.force_login(self.admin)

    def test_fallo_matricula_revierte_estudiante_en_ambos_formularios(self):
        def fallar(*args, **kwargs):
            with transaction.atomic():
                raise IntegrityError('Fallo simulado')
        for name in ('guardar_estudiante', 'nuevo_estudiante'):
            with self.subTest(name=name):
                with patch('app.views.estudiantes.MatriculaAnual.objects.update_or_create', side_effect=fallar):
                    response = self.client.post(reverse(name), self.payload(rut='987654321'),
                                                HTTP_X_REQUESTED_WITH='XMLHttpRequest')
                self.assertEqual(response.status_code, 400)
                self.assertFalse(Estudiante.objects.filter(rut='987654321').exists())

    def test_promocion_parvularia_y_egreso(self):
        for origen, destino in (('Prekinder', 'Kinder'), ('Kinder', '1° Básico'),
                                ('8° Básico', '1° Medio'), ('4° Medio', None)):
            with self.subTest(origen=origen):
                self.assertEqual(next_nivel(origen), destino)
        destino = map_curso_destino(self.kinder, 2027, True, True, 'Por Asignar', 'Egresados')
        self.assertEqual(destino.nivel, '1° Básico')
        desconocido = Curso.objects.create(nivel='Por Asignar', anio=2026)
        self.assertIsNone(map_curso_destino(desconocido, 2027, True, True, 'Por Asignar', 'Egresados'))

    def test_seed_resuelve_curso_del_anio_destino(self):
        seed_matricula_from_estudiantes(2028)
        ma = MatriculaAnual.objects.get(estudiante=self.est, anio=2028)
        self.assertEqual(ma.curso.anio, 2028)
        self.assertEqual(ma.curso.nivel, self.basica.nivel)
        self.assertTrue(ma.datos_congelados)
        with self.assertRaises(ValidationError):
            MatriculaAnual.objects.create(estudiante=self.est, anio=2029, curso=self.basica)

    def test_guardar_conserva_datos_de_otro_anio(self):
        self.est.domicilio = 'Domicilio nuevo'
        self.est.save()
        ficha = _estudiante_en_matricula(self.est, self.ma)
        self.assertEqual(ficha.domicilio, 'Domicilio original')
        self.assertEqual(ficha.telefono_apoderado, '5550123')
        self.assertTrue(ficha.problemas_salud)
        self.assertEqual(ficha.parentesco_apoderado_suplente, 'Tía')
        self.assertEqual(self.est.domicilio, 'Domicilio nuevo')
        response = self.client.get(reverse('detalles_estudiante', args=[self.est.pk]), {'anio': 2026})
        self.assertContains(response, 'Domicilio original')
        self.assertNotContains(response, 'Domicilio nuevo')

    def test_editar_anio_anterior_no_regresa_la_ficha_actual(self):
        curso_actual = Curso.objects.create(nivel='2° Básico', letra='A', anio=2027)
        self.est.curso = curso_actual
        self.est.domicilio = 'Domicilio 2027'
        self.est.save()
        for name in ('guardar_estudiante', 'nuevo_estudiante'):
            with self.subTest(name=name):
                response = self.client.post(reverse(name), self.payload(domicilio='Corregido 2026'),
                                            HTTP_X_REQUESTED_WITH='XMLHttpRequest')
                self.assertIn(response.status_code, (200, 201))
                self.est.refresh_from_db()
                self.ma.refresh_from_db()
                self.assertEqual(self.est.curso_id, curso_actual.pk)
                self.assertEqual(self.est.domicilio, 'Domicilio 2027')
                self.assertEqual(self.ma.domicilio, 'Corregido 2026')

    def test_pdf_individual_y_por_curso_usan_antecedentes_anuales(self):
        from reportlab.platypus import Paragraph
        self.est.domicilio = 'Domicilio nuevo'
        self.est.save()
        for name, args in (('imprimir_estudiante', [self.est.rut]),
                           ('imprimir_estudiantes_por_curso', [self.basica.pk])):
            textos = []
            def capturar(texto, *args, **kwargs):
                textos.append(str(texto))
                return Paragraph(texto, *args, **kwargs)
            with self.subTest(name=name):
                with patch('app.views.impresion_pdf.Paragraph', side_effect=capturar):
                    response = self.client.get(reverse(name, args=args), {'anio': 2026})
                self.assertEqual(response.status_code, 200)
                response.close()
                self.assertIn('Domicilio original', textos)
                self.assertNotIn('Domicilio nuevo', textos)

    def test_ruta_por_anio_y_borrado_de_curso_protegido(self):
        self.assertEqual(self.client.get(reverse('listar_estudiantes_anio_var', args=[2026])).status_code, 200)
        response = self.client.post(reverse('panel_config_documentos'), {
            'form_type': 'curso_delete', 'curso_id': self.basica.pk,
        }, follow=True)
        self.assertContains(response, 'No se puede eliminar el curso')
        self.assertTrue(Curso.objects.filter(pk=self.basica.pk).exists())

    def test_titulo_pdf_respeta_anio_solicitado(self):
        cfg = ConfiguracionMatricula(titulo='Matrícula {anio}', anio=2027)
        self.assertEqual(cfg.titulo_resuelto(anio=2026), 'Matrícula 2026')

    def test_migracion_reconoce_copias_sin_inventar_historia(self):
        migration = import_module('app.migrations.0032_antecedentes_anuales')
        self.ma.datos_congelados = False
        self.ma.save()
        curso = Curso.objects.create(nivel='2° Básico', anio=2025)
        sin_copia = MatriculaAnual.objects.create(estudiante=self.est, curso=curso, anio=2025)
        migration.marcar_antecedentes_existentes(apps, SimpleNamespace(connection=connection))
        self.ma.refresh_from_db()
        sin_copia.refresh_from_db()
        self.assertTrue(self.ma.datos_congelados)
        self.assertFalse(sin_copia.datos_congelados)
        response = self.client.get(reverse('detalles_estudiante', args=[self.est.pk]), {'anio': 2025})
        self.assertContains(response, 'no conserva antecedentes históricos completos')


class ImportacionRegresionTests(RegresionesBase):
    def setUp(self):
        self.client.force_login(self.admin)

    def test_exportar_y_restaurar_json_y_csv_conserva_antecedentes(self):
        for formato, sep in (('json', ','), ('csv', ','), ('csv', ';')):
            with self.subTest(formato=formato, sep=sep):
                response = self.client.get(reverse('export_datos'), {
                    'anio': 2026, 'datasets': ['cursos', 'estudiantes', 'matriculas', 'usuarios'],
                    'formato': formato, 'sep': sep,
                })
                self.assertEqual(response.status_code, 200)
                raw = response.content
                Estudiante.objects.all().delete()
                Curso.objects.all().delete()
                upload = SimpleUploadedFile('datos.' + formato, raw)
                self.client.post(reverse('import_datos_panel'), {
                    'archivo': upload, 'datasets': ['cursos', 'estudiantes', 'matriculas', 'usuarios'],
                    'anio_destino': 2026,
                })
                e = Estudiante.objects.get(rut='123456785')
                ma = MatriculaAnual.objects.get(estudiante=e, anio=2026)
                for obj in (e, ma):
                    self.assertEqual(obj.telefono_apoderado, '5550123')
                    self.assertTrue(obj.problemas_salud)
                    self.assertFalse(obj.acepta_fotografias)
                    self.assertEqual(obj.fecha_nacimiento_apoderado, date(1980, 2, 3))
                    self.assertEqual(obj.parentesco_apoderado_suplente, 'Tía')
                    self.assertEqual(obj.seguro_salud_complementario, 'Seguro')
                    self.assertEqual(obj.curso.anio, 2026)
                self.assertEqual(e.matriculado_por_id, self.admin.pk)
                self.assertTrue(ma.datos_congelados)

    def test_error_en_una_fila_revierte_todo_el_archivo(self):
        self.importar({'estudiantes': [self.payload(nombre_completo='Alterado'),
                                      self.payload(rut='987654321', sexo='inválido')]})
        self.est.refresh_from_db()
        self.assertEqual(self.est.nombre_completo, 'Estudiante Básica')
        self.assertFalse(Estudiante.objects.filter(rut='987654321').exists())

    def test_validar_archivo_no_persiste_cambios(self):
        self.importar({'estudiantes': [self.payload(nombre_completo='Alterado')]}, validate_only='true')
        self.est.refresh_from_db()
        self.assertEqual(self.est.nombre_completo, 'Estudiante Básica')

    def test_exportacion_simple_serializa_relaciones(self):
        response = self.client.get(reverse('export_estudiantes_por_anio'), {'anio': 2026, 'formato': 'json'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]['curso_id'], self.basica.pk)

    def test_usuarios_importados_no_recuperan_credenciales_ni_superpermisos(self):
        self.importar({'usuarios': [{
            'run': '333333333', 'nombre': 'Importado', 'apellido': 'Test',
            'password': self.admin.password, 'is_superuser': True,
        }]}, datasets=('usuarios',))
        user = Usuarios.objects.get(run='333333333')
        self.assertFalse(user.has_usable_password())
        self.assertFalse(user.is_superuser)

    def test_importacion_sql_persiste_booleanos_y_respeta_anio_destino(self):
        from app.sql_import_runner import import_sqldump
        AcademicYear.objects.create(anio=2027)
        sql = (
            'INSERT INTO app_estudiante (rut,nombre_completo,curso_id,ascendencia_indigena) '
            f"VALUES ('123456785','Estudiante SQL',{self.basica.pk},1);"
        )
        with NamedTemporaryFile(mode='w+', suffix='.sql') as dump:
            dump.write(sql)
            dump.flush()
            import_sqldump(dump_path=dump.name, anio=2027, permitir_sin_nivel=False,
                           stdout=io.StringIO(), stderr=io.StringIO())
        self.est.refresh_from_db()
        ma = MatriculaAnual.objects.get(estudiante=self.est, anio=2027)
        self.assertTrue(self.est.ascendencia_indigena)
        self.assertEqual(ma.curso.anio, 2027)
        self.assertTrue(ma.datos_congelados)
