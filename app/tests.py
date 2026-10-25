"""
Tests base del sistema de matrículas.

Run:  DJANGO_SETTINGS_MODULE=matriculas_lsp.settings_test .venv/bin/python manage.py test app
"""
from datetime import date

from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory, TestCase
from django.urls import reverse

from app import views
from app.models import (
    AcademicYear,
    Curso,
    Estudiante,
    MatriculaAnual,
    UsuarioPermiso,
    Usuarios,
)
from app.roles import (
    role_required,
    has_perm_on_nivel,
    niveles_permitidos_por_usuario,
)


def make_user(run, rol='ASISTENTE', password='clave-segura-123', email=None):
    return Usuarios.objects.create_user(
        run=run, nombre='Test', apellido=rol.title(),
        password=password, email=email, rol=rol,
    )


class UsuarioModelTests(TestCase):
    def test_perfil_signal_crea_permisos_por_defecto(self):
        u = make_user('11111111', rol='PROFESOR')
        niveles = {p.nivel for p in u.permisos_nivel.all()}
        self.assertEqual(niveles, {'BASICA', 'MEDIA'})

        admin = make_user('22222222', rol='ADMINISTRADOR')
        self.assertEqual(admin.permisos_nivel.count(), 0)  # admin no necesita filas

    def test_has_nivel_perm(self):
        u = make_user('33333333', rol='PROFESOR')
        self.assertFalse(u.has_nivel_perm('BASICA', 'view'))
        perm = u.permisos_nivel.get(nivel='BASICA')
        perm.can_view = True
        perm.can_print = True
        perm.save()
        self.assertTrue(u.has_nivel_perm('BASICA', 'view'))
        self.assertTrue(u.has_nivel_perm('BASICA', 'print'))
        self.assertFalse(u.has_nivel_perm('BASICA', 'edit'))
        self.assertFalse(u.has_nivel_perm('MEDIA', 'view'))

        admin = make_user('44444444', rol='ADMINISTRADOR')
        self.assertTrue(admin.has_nivel_perm('BASICA', 'edit'))


class RolesTests(TestCase):
    def _request(self, user):
        factory = RequestFactory()
        request = factory.get('/')
        request.user = user
        setattr(request, 'session', 'session')
        messages_storage = FallbackStorage(request)
        setattr(request, '_messages', messages_storage)
        return request

    def test_role_required_admite_y_bloquea(self):
        @role_required('PROFESOR')
        def vista(request):
            return 'ok'

        profesor = make_user('55555555', rol='PROFESOR')
        admin = make_user('66666666', rol='ADMINISTRADOR')
        asistente = make_user('77777777', rol='ASISTENTE')

        self.assertEqual(vista(self._request(profesor)), 'ok')
        self.assertEqual(vista(self._request(admin)), 'ok')
        # ASISTENTE no tiene el rol → devuelve redirect (ResponseHttp302)
        self.assertTrue(hasattr(vista(self._request(asistente)), 'status_code'))

    def test_niveles_permitidos_por_usuario(self):
        sin_permisos = make_user('88888888', rol='ASISTENTE')
        self.assertEqual(niveles_permitidos_por_usuario(sin_permisos), [])

        profe_basica = make_user('99999999', rol='PROFESOR')
        p = profe_basica.permisos_nivel.get(nivel='BASICA')
        p.can_view = True
        p.save()
        niveles = niveles_permitidos_por_usuario(profe_basica)
        self.assertIn('1° Básico', niveles)
        self.assertNotIn('1° Medio', niveles)


class HelpersTests(TestCase):
    def test_norm_rut(self):
        self.assertEqual(views._norm_rut(' 12.345.678-9 '), '123456789')
        self.assertEqual(views._norm_rut(None), '')

    def test_norm_run(self):
        self.assertEqual(views._norm_run('12.345.678-9'), '123456789')

    def test_parse_date(self):
        self.assertEqual(views._parse_date('2025-03-01'), date(2025, 3, 1))
        self.assertEqual(views._parse_date('01-03-2025'), date(2025, 3, 1))
        self.assertEqual(views._parse_date('01/03/2025'), date(2025, 3, 1))
        self.assertIsNone(views._parse_date(''))

    def test_to_bool(self):
        for v in ('si', 'Sí', 'true', '1', 'on', 'YES'):
            self.assertTrue(views._to_bool(v))
        for v in ('no', 'false', '0', None, '', 'otra'):
            self.assertFalse(views._to_bool(v))

    def test_fmt_date(self):
        self.assertEqual(views._fmt_date(date(2025, 1, 2)), '02-01-2025')
        self.assertEqual(views._fmt_date(None), 'N/A')


class MatriculaDeEstudiantesTests(TestCase):
    """Seed y clonado de años académicos."""

    def setUp(self):
        self.curso_2025 = Curso.objects.create(nivel='1° Básico', letra='A', anio=2025)
        self.activo = Estudiante.objects.create(
            rut='12345678-5', nombre_completo='Ana Activa',
            fecha_nacimiento=date(2015, 1, 1), sexo='Femenino',
            curso=self.curso_2025, nacionalidad='Chilena', domicilio='S/I',
            comuna='San Pedro', vive_con='Ambos Padres', fecha_matricula=date(2025, 3, 1),
        )
        self.inactivo = Estudiante.objects.create(
            rut='98765432-1', nombre_completo='Bruno Inactivo',
            fecha_nacimiento=date(2014, 2, 2), sexo='Masculino',
            curso=self.curso_2025, nacionalidad='Chilena', domicilio='S/I',
            comuna='San Pedro', vive_con='Ambos Padres', fecha_matricula=date(2025, 3, 1),
            estado='Inactivo',
        )
        AcademicYear.objects.get_or_create(anio=2025, defaults={'is_visible': True})

    def test_seed_solo_activos(self):
        res = views.seed_matricula_from_estudiantes(2025, only_activos=True)
        self.assertEqual(res['creados'], 1)
        self.assertEqual(res['omitidos'], 0)  # Inactivo ya está filtrado en la query
        ma = MatriculaAnual.objects.get(anio=2025, estudiante=self.activo)
        self.assertEqual(ma.curso, self.curso_2025)
        self.assertEqual(ma.estado, 'Activo')
        self.assertFalse(MatriculaAnual.objects.filter(estudiante=self.inactivo).exists())

    def test_seed_idempotente_y_overwrite(self):
        views.seed_matricula_from_estudiantes(2025, only_activos=True)
        res = views.seed_matricula_from_estudiantes(2025, only_activos=True, overwrite=False)
        self.assertEqual(res['creados'], 0)
        self.assertEqual(res['omitidos'], 1)
        res2 = views.seed_matricula_from_estudiantes(2025, only_activos=True, overwrite=True)
        self.assertEqual(res2['actualizados'], 1)

    def test_clone_promueve_curso(self):
        views.seed_matricula_from_estudiantes(2025, only_activos=True)
        res = views.clone_matriculas_anio(
            source_year=2025, target_year=2026,
            only_activos=True, promote_courses=True, auto_create_courses=True,
        )
        self.assertEqual(res['created'], 1)
        curso_2026 = MatriculaAnual.objects.get(anio=2026, estudiante=self.activo).curso
        self.assertEqual(f"{curso_2026.nivel}", '2° Básico')
        self.assertEqual(curso_2026.anio, 2026)

    def test_unique_matricula_por_anio(self):
        views.seed_matricula_from_estudiantes(2025, only_activos=True)
        views.seed_matricula_from_estudiantes(2025, only_activos=True, overwrite=False)
        self.assertEqual(
            MatriculaAnual.objects.filter(estudiante=self.activo, anio=2025).count(),
            1,
        )


class AuthFlowTests(TestCase):
    def test_login_ok_y_fallido(self):
        make_user('10101010', rol='ADMINISTRADOR', password='clave-segura-123')

        resp = self.client.post('/', {'run': '10101010', 'password': 'clave-segura-123'})
        self.assertRedirects(resp, reverse('home'))

        self.client.logout()
        resp = self.client.post('/', {'run': '10101010', 'password': 'incorrecta'})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Usuario o contraseña incorrectos')

    def test_login_para_publico_redirige(self):
        # Sin sesión, la home ya no muestra el flujo anónimo; login es la raíz
        resp = self.client.get('/')
        self.assertEqual(resp.status_code, 200)


class ImpresionPdfSmokeTests(TestCase):
    """Smoke test: los PDFs se generan sin errores de ReportLab."""

    def setUp(self):
        self.admin = make_user('12121212', rol='ADMINISTRADOR')
        curso = Curso.objects.create(nivel='1° Básico', letra='A', anio=2025)
        # RUT normalizado (así lo crea _norm_rut -> guardar/registro de la app)
        self.estudiante = Estudiante.objects.create(
            rut='131313135', nombre_completo='Carla Impresa',
            fecha_nacimiento=date(2015, 6, 6), sexo='Femenino',
            curso=curso, nacionalidad='Chilena', domicilio='S/I',
            comuna='San Pedro', vive_con='Ambos Padres', fecha_matricula=date(2025, 3, 2),
        )
        MatriculaAnual.objects.create(
            estudiante=self.estudiante, curso=curso, anio=2025, estado='Activo',
        )

    def test_pdf_por_curso(self):
        self.client.force_login(self.admin)
        ma = MatriculaAnual.objects.get(estudiante=self.estudiante, anio=2025)
        resp = self.client.get(reverse('imprimir_estudiantes_por_curso', args=[ma.curso_id]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'application/pdf')
        # FileResponse es streaming: unimos los chunks para ver el header
        cuerpo = b''.join(resp.streaming_content)
        self.assertTrue(cuerpo[:5].startswith(b'%PDF'))

    def test_pdf_ficha_estudiante(self):
        self.client.force_login(self.admin)
        resp = self.client.get(
            reverse('imprimir_estudiante', args=[self.estudiante.rut]) + '?anio=2025'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'application/pdf')


class CambioRutExtranjeroTests(TestCase):
    """Cambio de RUT provisional → definitivo (estudiantes extranjeros):
    valida DV, bloquea colisiones y deja auditoría en la ficha."""

    def setUp(self):
        self.directivo = make_user('22222222', rol='DIRECTIVO')
        self.curso = Curso.objects.create(nivel='1° Medio', letra='A', anio=2026)
        self.est = Estudiante.objects.create(
            rut='1003333724', nombre_completo='Extranjero Provisional',
            fecha_nacimiento=date(2010, 3, 3), sexo='M',
            curso=self.curso, nacionalidad='Venezolana', domicilio='S/I',
            comuna='San Pedro', vive_con='Madre', fecha_matricula=date(2026, 3, 4),
        )
        MatriculaAnual.objects.create(estudiante=self.est, curso=self.curso, anio=2026, estado='Activo')
        self.url = reverse('cambiar_rut_estudiante', args=[self.est.id])

    def _post(self, rut, who=None):
        self.client.force_login(who or self.directivo)
        return self.client.post(self.url, {'rut_nuevo': rut})

    def test_cambio_valido_deja_auditoria(self):
        resp = self._post('26012345-9')
        self.assertEqual(resp.status_code, 302)
        e = Estudiante.objects.get(pk=self.est.pk)
        self.assertEqual(e.rut, '260123459')
        self.assertEqual(e.rut_anterior, '1003333724')
        self.assertEqual(e.rut_actualizado_por, 'Test Directivo')
        self.assertIsNotNone(e.rut_actualizado_en)
        # Nada se recrea: la matrícula anual del estudiante sigue intacta
        self.assertEqual(MatriculaAnual.objects.filter(estudiante=e).count(), 1)

    def test_dv_invalido_rechaza(self):
        resp = self._post('26012345-0')
        self.assertEqual(resp.status_code, 302)
        e = Estudiante.objects.get(pk=self.est.pk)
        self.assertEqual(e.rut, '1003333724')
        self.assertIsNone(e.rut_anterior)

    def test_colision_con_otro_estudiante_rechaza(self):
        Estudiante.objects.create(
            rut='260123459', nombre_completo='Otro Con Ese Rut',
            fecha_nacimiento=date(2011, 1, 1), sexo='F', curso=self.curso,
            nacionalidad='Chilena', domicilio='S/I', comuna='San Pedro',
            vive_con='Ambos Padres', fecha_matricula=date(2026, 3, 4),
        )
        resp = self._post('26012345-9')
        self.assertEqual(resp.status_code, 302)
        e = Estudiante.objects.get(pk=self.est.pk)
        self.assertEqual(e.rut, '1003333724')
        self.assertIsNone(e.rut_anterior)

    def test_mismo_rut_no_cambia_nada(self):
        resp = self._post('1003333724')
        self.assertEqual(resp.status_code, 302)
        e = Estudiante.objects.get(pk=self.est.pk)
        self.assertIsNone(e.rut_anterior)

    def test_rol_sin_permiso_rechaza(self):
        resp = self._post('26012345-9', who=make_user('12345678', rol='PROFESOR'))
        self.assertEqual(resp.status_code, 302)  # redirect a home con mensaje
        e = Estudiante.objects.get(pk=self.est.pk)
        self.assertEqual(e.rut, '1003333724')


class ColaRutProvisionalesTests(TestCase):
    """El chip del listado por año filtra la cola de RUT provisionales."""

    def setUp(self):
        self.user = make_user('12123434', rol='DIRECTIVO')
        curso = Curso.objects.create(nivel='1° Medio', letra='B', anio=2026)
        self.prov = Estudiante.objects.create(
            rut='1003333724', nombre_completo='Prov Cola Test',
            fecha_nacimiento=date(2010, 3, 3), sexo='F', curso=curso,
            nacionalidad='Venezolana', domicilio='S/I', comuna='San Pedro',
            vive_con='Madre', fecha_matricula=date(2026, 3, 4),
        )
        self.normal = Estudiante.objects.create(
            rut='131313135', nombre_completo='Normal Fuera',
            fecha_nacimiento=date(2010, 3, 3), sexo='M', curso=curso,
            nacionalidad='Chilena', domicilio='S/I', comuna='San Pedro',
            vive_con='Ambos Padres', fecha_matricula=date(2026, 3, 4),
        )
        for e in (self.prov, self.normal):
            MatriculaAnual.objects.create(estudiante=e, curso=curso, anio=2026, estado='Activo')

    def test_filtro_provisionales_muestra_solo_esos(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse('listar_estudiantes_anio') + '?anio=2026&provisionales=1')
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn('Prov Cola Test', html)
        self.assertNotIn('Normal Fuera', html)
        self.assertIn('RUT prov.', html)

    def test_sin_filtro_los_incluye_todos_con_chip(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse('listar_estudiantes_anio') + '?anio=2026')
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn('Prov Cola Test', html)
        self.assertIn('Normal Fuera', html)
        self.assertIn('RUT provisionales (1)', html)  # chip con enlace a la cola


class BusquedaGlobalTests(TestCase):
    """El buscador global del navbar (nombre o RUT) devuelve la ficha a abrir."""

    def setUp(self):
        self.user = make_user('12123436', rol='DIRECTIVO')
        curso = Curso.objects.create(nivel='1° Medio', letra='A', anio=2026)
        self.est = Estudiante.objects.create(
            rut='131313135', nombre_completo='Camila Busqueda Zeta',
            fecha_nacimiento=date(2010, 3, 3), sexo='F', curso=curso,
            nacionalidad='Chilena', domicilio='S/I', comuna='San Pedro',
            vive_con='Ambos Padres', fecha_matricula=date(2026, 3, 4),
        )

    def test_busca_por_nombre(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse('busqueda_global') + '?q=camila')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data['resultados']), 1)
        self.assertEqual(data['resultados'][0]['id'], self.est.id)
        self.assertEqual(data['resultados'][0]['rut'], '131313135')

    def test_busca_por_rut(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse('busqueda_global') + '?q=131313')
        self.assertEqual(len(resp.json()['resultados']), 1)

    def test_consulta_muy_corta_devuelve_vacio(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse('busqueda_global') + '?q=ca')
        self.assertEqual(resp.json()['resultados'], [])


class AsignarCursoInlineTests(TestCase):
    """Cambio de curso al instante desde el listado (endpoint AJAX reactivado)."""

    def setUp(self):
        self.user = make_user('12123435', rol='DIRECTIVO')
        self.curso_a = Curso.objects.create(nivel='1° Medio', letra='A', anio=2026)
        self.curso_b = Curso.objects.create(nivel='1° Medio', letra='B', anio=2026)
        self.est = Estudiante.objects.create(
            rut='238280443', nombre_completo='Fila Listado Test',
            fecha_nacimiento=date(2010, 3, 3), sexo='M', curso=self.curso_a,
            nacionalidad='Chilena', domicilio='S/I', comuna='San Pedro',
            vive_con='Ambos Padres', fecha_matricula=date(2026, 3, 4),
        )
        MatriculaAnual.objects.create(estudiante=self.est, curso=self.curso_a, anio=2026, estado='Activo')

    def test_cambio_curso_instante(self):
        self.client.force_login(self.user)
        resp = self.client.post(reverse('asignar_curso_en_anio'),
                                {'rut': '238280443', 'anio': 2026, 'curso_id': self.curso_b.id})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get('ok'), True)
        e = Estudiante.objects.get(pk=self.est.pk)
        # Cambia la matrícula del año…
        self.assertEqual(MatriculaAnual.objects.get(estudiante=e, anio=2026).curso_id, self.curso_b.id)
        # …y sincroniza la ficha (misma regla forward-only del clonado)
        self.assertEqual(e.curso_id, self.curso_b.id)
        # Sin duplicados
        self.assertEqual(MatriculaAnual.objects.filter(estudiante=e).count(), 1)

    def test_rechaza_curso_de_otro_anio(self):
        self.client.force_login(self.user)
        otro = Curso.objects.create(nivel='2° Medio', letra='A', anio=2027)
        resp = self.client.post(reverse('asignar_curso_en_anio'),
                                {'rut': '238280443', 'anio': 2026, 'curso_id': otro.id})
        self.assertEqual(resp.status_code, 400)
        e = Estudiante.objects.get(pk=self.est.pk)
        self.assertEqual(e.curso_id, self.curso_a.id)  # intacto
