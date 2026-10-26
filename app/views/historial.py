from datetime import datetime, time, timedelta

from django.core.paginator import Paginator
from django.http import HttpResponseBadRequest
from django.urls import reverse

from ._common import *  # noqa: F401,F403
from app.forms_historial import ActividadFiltroForm, MovimientoForm
from app.historial import registrar_movimiento, retiro_pendiente
from app.models import RegistroActividad


@role_required(*PERMITIDOS_TODOS)
def movimiento_estudiante(request, estudiante_id):
    estudiante = get_object_or_404(Estudiante.objects.select_related('curso'), pk=estudiante_id)
    _require_curso_perm(request.user, 'edit', estudiante.curso)
    tipo = request.POST.get('tipo') if request.method == 'POST' else request.GET.get('tipo', 'RETIRO')
    if tipo not in ('RETIRO', 'REINCORPORACION'):
        return HttpResponseBadRequest('Tipo de movimiento inválido.')
    pendiente = retiro_pendiente(estudiante)
    if pendiente:
        _require_curso_perm(request.user, 'edit', pendiente.matricula.curso)
    anio = _pick_int(request.POST.get('anio') if request.method == 'POST' else request.GET.get('anio'))
    anio = anio or estudiante.curso.anio
    ma = estudiante.matriculas_anuales.filter(anio=anio).select_related('curso').first()
    if ma:
        _require_curso_perm(request.user, 'edit', ma.curso)
    form = MovimientoForm(request.POST if request.method == 'POST' else None, user=request.user, tipo=tipo,
                          initial={'tipo': tipo, 'anio': anio, 'fecha': timezone.localdate(),
                                   'solicitante': estudiante.nombre_apoderado, 'curso': estudiante.curso_id})
    if tipo == 'REINCORPORACION':
        minimo = max(estudiante.curso.anio, pendiente.matricula.anio if pendiente else 0)
        form.fields['curso'].queryset = form.fields['curso'].queryset.filter(
            anio__gte=minimo, anio__lte=timezone.localdate().year)
    if request.method == 'POST' and form.is_valid():
        try:
            movimiento = registrar_movimiento(request, estudiante.pk, **form.cleaned_data)
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, 'Retiro registrado correctamente.' if tipo == 'RETIRO' else 'Reincorporación registrada correctamente.')
            return redirect(f"{reverse('detalles_estudiante', args=[estudiante.pk])}?anio={movimiento.matricula.anio}#historial-escolar")
    return render(request, 'app/movimiento_estudiante.html', {
        'estudiante': estudiante, 'form': form, 'anio': anio, 'pendiente': pendiente,
        'tipo': tipo, 'titulo': 'Registrar retiro' if tipo == 'RETIRO' else 'Registrar reincorporación',
    }, status=400 if request.method == 'POST' else 200)


@role_required(ADMIN)
def registro_actividad(request):
    form = ActividadFiltroForm(request.GET)
    registros = RegistroActividad.objects.select_related('usuario').all()
    if form.is_valid():
        datos = form.cleaned_data
        if datos.get('usuario'):
            registros = registros.filter(usuario=datos['usuario'])
        if datos.get('accion'):
            registros = registros.filter(accion=datos['accion'])
        if datos.get('desde'):
            registros = registros.filter(registrado_en__gte=timezone.make_aware(datetime.combine(datos['desde'], time.min)))
        if datos.get('hasta') and datos['hasta'] < date.max:
            registros = registros.filter(registrado_en__lt=timezone.make_aware(datetime.combine(datos['hasta'] + timedelta(days=1), time.min)))
    else:
        registros = registros.none()
    query = request.GET.copy()
    query.pop('page', None)
    return render(request, 'app/registro_actividad.html', {
        'form': form, 'pagina': Paginator(registros, 50).get_page(request.GET.get('page')),
        'filtros': query.urlencode(),
    })
