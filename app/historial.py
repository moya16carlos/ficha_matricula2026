"""Movimientos escolares atómicos: el estudiante se bloquea antes de validar y guardar."""
import json

from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils import timezone

from app.auditoria import nombre_usuario, registrar
from app.models import AcademicYear, Estudiante, MatriculaAnual, MovimientoEstudiante


def retiro_pendiente(estudiante):
    return estudiante.movimientos.filter(tipo='RETIRO', reincorporacion__isnull=True).first()


def datos_ficha(estudiante):
    datos = {f.name: getattr(estudiante, f.name) for f in Estudiante._meta.fields
             if f.name not in ('id', 'curso', 'matriculado_por')}
    return json.loads(json.dumps(datos, cls=DjangoJSONEncoder))


@transaction.atomic
def registrar_movimiento(request, estudiante_id, *, tipo, fecha, motivo,
                         solicitante='', observaciones='', anio=None, curso=None):
    from app.views._common import _estudiante_en_matricula, _require_curso_perm

    estudiante = Estudiante.objects.select_for_update().select_related('curso').get(pk=estudiante_id)
    _require_curso_perm(request.user, 'edit', estudiante.curso)
    ultimo = estudiante.movimientos.order_by('-fecha', '-id').first()
    if fecha > timezone.localdate():
        raise ValidationError('La fecha no puede estar en el futuro.')
    if fecha < estudiante.fecha_nacimiento or (ultimo and fecha < ultimo.fecha):
        raise ValidationError('La fecha debe ser posterior al nacimiento y al último movimiento escolar.')
    if not motivo.strip() or len(motivo) > 500 or len(observaciones) > 2000 or len(solicitante) > 150:
        raise ValidationError('Revisa el motivo, solicitante y observaciones.')
    pendiente = retiro_pendiente(estudiante)
    retiro = None
    if tipo == 'RETIRO':
        if pendiente or estudiante.estado != 'Activo':
            raise ValidationError('Solo puedes retirar a un estudiante activo que no tenga un retiro pendiente.')
        ultima_ma = estudiante.matriculas_anuales.order_by('-anio').first()
        ultimo_anio = max(estudiante.curso.anio, ultima_ma.anio if ultima_ma else 0)
        if anio != ultimo_anio or fecha.year != anio:
            raise ValidationError('Registra el retiro en la matrícula más reciente y con una fecha de ese año.')
        ma = estudiante.matriculas_anuales.filter(anio=anio).select_related('curso').first()
        curso = ma.curso if ma else estudiante.curso
        _require_curso_perm(request.user, 'edit', curso)
        if ma and ma.estado != 'Activo':
            raise ValidationError('La matrícula seleccionada no está activa.')
        fecha_ingreso = ma.fecha_matricula if ma else estudiante.fecha_matricula
        if fecha_ingreso and fecha < fecha_ingreso:
            raise ValidationError('El retiro no puede ser anterior a la fecha de matrícula.')
        if not ma:
            ma = MatriculaAnual.objects.create(estudiante=estudiante, anio=anio,
                                              **MatriculaAnual.defaults_desde_estudiante(estudiante))
        estado = 'Retirado'
    elif tipo == 'REINCORPORACION':
        if not pendiente or estudiante.estado != 'Retirado':
            raise ValidationError('Este estudiante no tiene un retiro pendiente de reincorporación.')
        retiro = pendiente
        _require_curso_perm(request.user, 'edit', retiro.matricula.curso)
        if not curso or curso.anio != fecha.year:
            raise ValidationError('El curso debe corresponder al año de reincorporación.')
        _require_curso_perm(request.user, 'edit', curso)
        if curso.nivel in ('Por Asignar', 'Sin asignar', 'Egresados'):
            raise ValidationError('Selecciona un curso de enseñanza para reincorporarlo.')
        if curso.anio < max(estudiante.curso.anio, retiro.matricula.anio):
            raise ValidationError('No puedes reincorporar en un año anterior a la última matrícula.')
        anio = curso.anio
        ma = estudiante.matriculas_anuales.filter(anio=anio).first()
        if not ma:
            defaults = MatriculaAnual.defaults_desde_estudiante(estudiante)
            defaults.update(curso=curso, estado='Activo', fecha_matricula=fecha)
            ma = MatriculaAnual(estudiante=estudiante, anio=anio, **defaults)
        estudiante.curso = curso
        if anio > retiro.matricula.anio:
            estudiante.fecha_matricula = fecha
        estado = 'Activo'
    else:
        raise ValidationError('Movimiento escolar inválido.')
    if not AcademicYear.objects.filter(anio=anio, is_visible=True).exists():
        raise ValidationError('El año académico debe estar visible para registrar este movimiento.')

    ma._registrando_movimiento = estudiante._registrando_movimiento = True
    ma.estado = estudiante.estado = estado
    ma.curso = curso
    ma.save()
    estudiante.save(update_fields=['estado', 'curso', 'fecha_matricula'])
    ficha = _estudiante_en_matricula(estudiante, ma)
    movimiento = MovimientoEstudiante.objects.create(
        estudiante=estudiante, matricula=ma, tipo=tipo, fecha=fecha, motivo=motivo.strip(),
        solicitante=solicitante.strip(), observaciones=observaciones.strip(),
        curso_descripcion=str(curso) if curso else 'Sin curso', datos=datos_ficha(ficha),
        retiro=retiro, usuario=request.user, usuario_nombre=nombre_usuario(request.user),
    )
    registrar(request, movimiento.get_tipo_display(), f'Estudiante #{estudiante.pk} · {anio}', codigo=302)
    request.audit_registrado = True
    return movimiento
