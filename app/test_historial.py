from datetime import date
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db.models.deletion import ProtectedError
from django.test import Client, RequestFactory
from django.urls import reverse

from app.historial import registrar_movimiento, retiro_pendiente
from app.models import AcademicYear, ConfiguracionSistema, Curso, Estudiante, MatriculaAnual, MovimientoEstudiante, RegistroActividad
from app.test_regresiones import RegresionesBase
from app.views.anios import seed_matricula_from_estudiantes


class HistorialEscolarTests(RegresionesBase):
    def setUp(self):
        self.client.force_login(self.admin)
        self.url = reverse('movimiento_estudiante', args=[self.est.pk])

    def mover(self, tipo='RETIRO', **cambios):
        datos = dict(tipo=tipo, anio=2026, fecha='2026-04-15', motivo='Traslado',
                     solicitante='Apoderada', observaciones='Antecedentes del movimiento')
        return self.client.post(self.url, dict(datos, **cambios))

    def test_retiro_conserva_snapshot_y_responsable_en_ficha(self):
        response = self.mover()
        self.assertEqual(response.status_code, 302)
        self.est.refresh_from_db()
        self.ma.refresh_from_db()
        self.assertEqual((self.est.estado, self.ma.estado), ('Retirado', 'Retirado'))
        movimiento = MovimientoEstudiante.objects.get()
        self.assertEqual(movimiento.datos['domicilio'], 'Domicilio original')
        self.assertEqual(movimiento.usuario, self.admin)
        self.assertEqual(RegistroActividad.objects.filter(accion='Retiro').count(), 1)
        self.est.domicilio = 'Domicilio nuevo'
        self.est.save(update_fields=['domicilio'])
        movimiento.refresh_from_db()
        self.assertEqual(movimiento.datos['domicilio'], 'Domicilio original')
        ficha = self.client.get(reverse('detalles_estudiante', args=[self.est.pk]))
        self.assertContains(ficha, 'Historial escolar')
        self.assertContains(ficha, '15/04/2026')
        self.assertContains(ficha, 'Reincorporar estudiante')
        with self.assertRaises(ProtectedError):
            self.ma.delete()

    def test_formulario_muestra_fecha_iso_y_solo_cursos_del_periodo_posible(self):
        with patch('django.utils.timezone.localdate', return_value=date(2026, 10, 25)):
            response = self.client.get(self.url, {'anio': 2026})
            self.assertContains(response, 'value="2026-10-25"')
            self.mover()
            AcademicYear.objects.create(anio=2025)
            AcademicYear.objects.create(anio=2027)
            Curso.objects.create(nivel='1° Básico', letra='Z', anio=2025)
            Curso.objects.create(nivel='2° Básico', letra='Z', anio=2027)
            response = self.client.get(self.url, {'tipo': 'REINCORPORACION'})
            self.assertEqual(set(response.context['form'].fields['curso'].queryset.values_list('anio', flat=True)), {2026})

    def test_reincorporacion_mismo_anio_con_otra_letra(self):
        self.mover()
        b = Curso.objects.create(nivel='1° Básico', letra='B', anio=2026)
        response = self.mover('REINCORPORACION', fecha='2026-05-10', curso=b.pk, motivo='Regreso al liceo')
        self.assertEqual(response.status_code, 302)
        self.est.refresh_from_db()
        self.ma.refresh_from_db()
        self.assertEqual(self.est.estado, 'Activo')
        self.assertEqual(self.ma.estado, 'Activo')
        self.assertEqual(self.est.curso, b)
        self.assertEqual(self.ma.curso, b)
        self.assertEqual(self.ma.fecha_matricula, date(2026, 3, 1))
        self.assertIsNone(retiro_pendiente(self.est))
        retorno = MovimientoEstudiante.objects.get(tipo='REINCORPORACION')
        self.assertEqual(retorno.retiro.tipo, 'RETIRO')
        self.assertEqual(retorno.retiro.curso_descripcion, str(self.basica))
        self.assertEqual(retorno.curso_descripcion, str(b))
        self.assertEqual(self.mover('REINCORPORACION', fecha='2026-05-11', curso=b.pk).status_code, 400)

    def test_reincorporacion_otro_anio_preserva_retiro_anterior_y_admite_otro_ciclo(self):
        self.mover()
        AcademicYear.objects.create(anio=2027)
        curso = Curso.objects.create(nivel='2° Básico', letra='A', anio=2027)
        with patch('django.utils.timezone.localdate', return_value=date(2027, 3, 15)):
            response = self.mover('REINCORPORACION', curso=curso.pk, fecha='2027-03-10')
            self.assertEqual(response.status_code, 302)
            self.ma.refresh_from_db()
            self.assertEqual(self.ma.estado, 'Retirado')
            nueva = MatriculaAnual.objects.get(estudiante=self.est, anio=2027)
            self.assertEqual(nueva.estado, 'Activo')
            self.assertEqual(nueva.fecha_matricula, date(2027, 3, 10))
            self.assertEqual(self.mover(anio=2027, fecha='2027-03-12').status_code, 302)
            self.assertEqual(self.mover('REINCORPORACION', anio=2027, fecha='2027-03-15', curso=curso.pk).status_code, 302)
        self.assertEqual(MovimientoEstudiante.objects.count(), 4)

    def test_rechaza_fechas_invalidas_y_retiros_duplicados(self):
        for cambios in ({'fecha': '2026-02-01'}, {'fecha': '2099-01-01'}, {'fecha': 'incorrecta'},
                        {'motivo': ''}, {'anio': 2025}, {'motivo': 'x' * 501}):
            with self.subTest(cambios=cambios):
                self.assertEqual(self.mover(**cambios).status_code, 400)
                self.assertFalse(MovimientoEstudiante.objects.exists())
        self.assertEqual(self.mover().status_code, 302)
        self.assertEqual(self.mover().status_code, 400)
        self.assertEqual(self.mover('REINCORPORACION', fecha='2026-04-01', curso=self.basica.pk).status_code, 400)
        self.assertEqual(MovimientoEstudiante.objects.count(), 1)

    def test_editar_importar_o_clonar_no_reactiva_un_retirado(self):
        self.mover()
        response = self.client.post(reverse('guardar_estudiante'), self.payload(), HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 400)
        self.est.refresh_from_db()
        self.assertEqual(self.est.estado, 'Retirado')
        self.assertIn('reincorporación', response.json()['error'])
        self.est.estado = 'Activo'
        with self.assertRaises(ValidationError):
            self.est.save()
        self.ma.refresh_from_db()
        self.ma.estado = 'Activo'
        with self.assertRaises(ValidationError):
            self.ma.save()
        response = self.importar({'estudiantes': [self.payload(estado='Activo')]})
        self.assertRedirects(response, reverse('panel_config_documentos'), fetch_redirect_response=False)
        self.assertContains(self.client.get(reverse('panel_config_documentos')), 'reincorporación')
        self.est.refresh_from_db()
        self.assertEqual(self.est.estado, 'Retirado')
        self.assertEqual(seed_matricula_from_estudiantes(2027, only_activos=False)['creados'], 0)

    def test_cambio_de_curso_no_bypassea_reincorporacion(self):
        self.mover()
        response = self.client.post(reverse('asignar_curso_en_anio'), {
            'rut': self.est.rut, 'anio': 2026, 'curso_id': self.media.pk,
        })
        self.assertEqual(response.status_code, 400)
        self.ma.refresh_from_db()
        self.assertEqual(self.ma.curso, self.basica)
        self.assertEqual(self.ma.estado, 'Retirado')

    def test_rut_legado_no_permite_crear_otra_ficha_tras_el_retiro(self):
        self.est.rut = '12345678-5'
        self.est.save(update_fields=['rut'])
        self.mover()
        datos = {f.attname: getattr(self.est, f.attname) for f in Estudiante._meta.fields if not f.primary_key}
        with self.assertRaises(ValidationError):
            Estudiante.objects.create(**dict(datos, rut='123456785'))
        self.assertEqual(Estudiante.objects.count(), 1)

    def test_no_reescribe_retiro_historico_al_copiar_ficha_actual(self):
        self.mover()
        AcademicYear.objects.create(anio=2027)
        curso = Curso.objects.create(nivel='2° Básico', letra='A', anio=2027)
        with patch('django.utils.timezone.localdate', return_value=date(2027, 3, 15)):
            self.mover('REINCORPORACION', fecha='2027-03-10', curso=curso.pk)
        seed_matricula_from_estudiantes(2026, only_activos=False, overwrite=True)
        self.ma.refresh_from_db()
        self.assertEqual(self.ma.estado, 'Retirado')
        self.assertEqual(self.ma.curso, self.basica)

    def test_el_log_y_el_movimiento_se_guardan_en_una_transaccion(self):
        request = RequestFactory().post(self.url)
        request.user = self.admin
        with patch('app.historial.registrar', side_effect=RuntimeError('Fallo del registro')):
            with self.assertRaises(RuntimeError):
                registrar_movimiento(request, self.est.pk, tipo='RETIRO', fecha=date(2026, 4, 15),
                                     motivo='Traslado', anio=2026)
        self.est.refresh_from_db()
        self.ma.refresh_from_db()
        self.assertEqual(self.est.estado, 'Activo')
        self.assertEqual(self.ma.estado, 'Activo')
        self.assertFalse(MovimientoEstudiante.objects.exists())

    def test_permisos_csrf_y_anios_no_visibles(self):
        self.client.force_login(self.user)
        self.user.permisos_nivel.filter(nivel='BASICA').update(can_view=True)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.mover().status_code, 403)
        self.user.permisos_nivel.filter(nivel='BASICA').update(can_edit=True)
        self.assertEqual(self.mover().status_code, 302)
        self.assertEqual(self.mover('REINCORPORACION', fecha='2026-05-01', curso=self.media.pk).status_code, 400)
        self.est.refresh_from_db()
        self.assertEqual(self.est.estado, 'Retirado')
        AcademicYear.objects.filter(pk=self.year.pk).update(is_visible=False)
        self.client.force_login(self.admin)
        self.assertEqual(self.mover('REINCORPORACION', curso=self.basica.pk, fecha='2026-05-01').status_code, 400)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.admin)
        self.assertEqual(csrf_client.post(self.url, {'tipo': 'RETIRO'}).status_code, 403)

    def test_filtro_retirados_en_listados(self):
        self.mover()
        for ruta in ('listar_estudiantes', 'listar_estudiantes_anio'):
            with self.subTest(ruta=ruta):
                response = self.client.get(reverse(ruta), {'anio': 2026, 'estado': 'Retirado'})
                self.assertContains(response, 'Estudiante Básica')
                self.assertNotContains(self.client.get(reverse(ruta), {'anio': 2026, 'estado': 'Activo'}), 'Estudiante Básica')


class RegistroActividadTests(RegresionesBase):
    def test_matricula_publica_identifica_apoderado_sin_copiar_credenciales(self):
        ConfiguracionSistema.objects.create(pk=1, registro_apoderado_habilitado=True)
        self.client.post(reverse('registro_apoderado'), {
            'run': '333333333', 'nombre': 'Familia', 'apellido': 'Test',
            'email': 'familia@example.test', 'password': 'Secreto-familia-2026',
        })
        self.client.post(reverse('registro_alumno'), self.payload(rut='987654321'),
                         HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        registro = RegistroActividad.objects.first()
        self.assertEqual(registro.accion, 'Matrícula pública')
        self.assertEqual(registro.usuario_nombre, 'Apoderado: Familia Test')
        self.assertIn('Estudiante #', registro.objeto)
        self.assertNotIn('Secreto-familia-2026', str(registro.__dict__))

    def test_consultas_ajax_registran_estudiante_sin_rut_en_el_log(self):
        self.client.force_login(self.admin)
        self.client.get(reverse('buscar_estudiantes_por_rut'), {'rut': self.est.rut, 'anio': 2026})
        registro = RegistroActividad.objects.first()
        self.assertEqual(registro.accion, 'Consultar ficha')
        self.assertIn(f'Estudiante #{self.est.pk}', registro.objeto)
        self.assertNotIn(self.est.rut, str(registro.__dict__))

    def test_operacion_administrativa_denegada_no_se_marca_completada(self):
        self.client.force_login(self.user)
        self.client.post(reverse('api_seed_estudiantes_a_anio'), {'anio': 2026})
        registro = RegistroActividad.objects.first()
        self.assertEqual(registro.accion, 'Copiar matrículas')
        self.assertEqual(registro.resultado, 'Rechazado')

    def test_login_fallido_login_y_logout_sin_guardar_credenciales(self):
        secreto = 'Secreto-no-guardar-2026'
        self.client.post(reverse('login'), {'run': self.admin.run, 'password': secreto})
        self.assertEqual(RegistroActividad.objects.get().resultado, 'Rechazado')
        self.client.post(reverse('login'), {'run': self.admin.run, 'password': 'clave-segura-123'})
        self.assertEqual(RegistroActividad.objects.first().usuario, self.admin)
        self.client.get(reverse('logout'))
        self.assertEqual(RegistroActividad.objects.first().usuario, self.admin)
        self.assertEqual(RegistroActividad.objects.first().accion, 'Cierre de sesión')
        registros = str(list(RegistroActividad.objects.values()))
        self.assertNotIn(secreto, registros)
        self.assertNotIn('clave-segura-123', registros)
        self.assertNotIn(self.admin.run, registros)

    def test_guardado_consulta_y_permiso_rechazado_quedan_registrados(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('guardar_estudiante'), self.payload(domicilio='Dato privado'), HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        registro = RegistroActividad.objects.first()
        self.assertEqual(registro.accion, 'Guardar ficha')
        self.assertIn(f'Estudiante #{self.est.pk}', registro.objeto)
        self.assertIn('domicilio', registro.campos)
        self.assertNotIn('Dato privado', str(registro.__dict__))
        self.client.get(reverse('detalles_estudiante', args=[self.est.pk]))
        self.assertEqual(RegistroActividad.objects.first().accion, 'Consultar ficha')
        self.client.force_login(self.user)
        self.client.get(reverse('detalles_estudiante', args=[self.est.pk]))
        self.assertEqual(RegistroActividad.objects.first().resultado, 'Rechazado')

    def test_consulta_de_logs_solo_admin_filtros_y_usuario_eliminado(self):
        registro = RegistroActividad.objects.create(usuario=self.user, usuario_nombre='Nombre conservado',
            accion='Consultar ficha', resultado='Completado', codigo_http=200, objeto='Estudiante #1')
        self.client.force_login(self.user)
        self.assertRedirects(self.client.get(reverse('registro_actividad')), reverse('home'))
        self.client.force_login(self.admin)
        response = self.client.get(reverse('registro_actividad'), {'usuario': self.user.pk, 'accion': 'Consultar ficha'})
        self.assertContains(response, 'Nombre conservado')
        self.assertNotContains(self.client.get(reverse('registro_actividad'), {'usuario': self.admin.pk}), 'Nombre conservado')
        self.assertContains(self.client.get(reverse('registro_actividad'), {'desde': '2026-12-31', 'hasta': '2026-01-01'}), 'La fecha inicial')
        self.user.delete()
        registro.refresh_from_db()
        self.assertIsNone(registro.usuario)
        self.assertEqual(registro.usuario_nombre, 'Nombre conservado')
