from django import forms
from django.utils import timezone

from app.models import AcademicYear, Curso, MovimientoEstudiante, RegistroActividad, Usuarios
from app.roles import filtro_q_por_niveles_permitidos


class MovimientoForm(forms.Form):
    tipo = forms.ChoiceField(choices=MovimientoEstudiante.TIPOS, widget=forms.HiddenInput)
    anio = forms.IntegerField(widget=forms.HiddenInput)
    fecha = forms.DateField(label='Fecha del movimiento', widget=forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}))
    curso = forms.ModelChoiceField(Curso.objects.none(), required=False, label='Curso de reincorporación')
    motivo = forms.CharField(max_length=500, label='Motivo', widget=forms.Textarea(attrs={'rows': 2}))
    solicitante = forms.CharField(max_length=150, required=False, label='Apoderado o persona que solicita')
    observaciones = forms.CharField(max_length=2000, required=False, widget=forms.Textarea(attrs={'rows': 3}))

    def __init__(self, *args, user, tipo, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['fecha'].widget.attrs['max'] = timezone.localdate().isoformat()
        if tipo == 'REINCORPORACION':
            visibles = AcademicYear.objects.filter(is_visible=True).values_list('anio', flat=True)
            self.fields['curso'].queryset = Curso.objects.filter(anio__in=visibles).filter(
                filtro_q_por_niveles_permitidos(user, 'nivel', action='edit')
            ).exclude(nivel__in=('Sin asignar', 'Por Asignar', 'Egresados')).order_by('anio', 'nivel', 'letra')
            self.fields['curso'].required = True
        else:
            self.fields.pop('curso')
        for field in self.fields.values():
            if not field.widget.is_hidden:
                field.widget.attrs['class'] = 'w-full border border-slate-300 rounded-lg px-3 py-2'


class ActividadFiltroForm(forms.Form):
    usuario = forms.ModelChoiceField(Usuarios.objects.order_by('nombre', 'apellido'), required=False,
                                    empty_label='Todos los usuarios')
    accion = forms.ChoiceField(required=False, label='Acción')
    desde = forms.DateField(required=False, widget=forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}))
    hasta = forms.DateField(required=False, widget=forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        acciones = RegistroActividad.objects.order_by('accion').values_list('accion', flat=True).distinct()
        self.fields['usuario'].label_from_instance = lambda u: f'{u.nombre} {u.apellido} · {u.get_rol_display()}'
        self.fields['accion'].choices = [('', 'Todas las acciones')] + [(a, a) for a in acciones]
        for field in self.fields.values():
            field.widget.attrs['class'] = 'w-full border border-slate-300 rounded-lg px-3 py-2'

    def clean(self):
        data = super().clean()
        if data.get('desde') and data.get('hasta') and data['desde'] > data['hasta']:
            raise forms.ValidationError('La fecha inicial no puede ser posterior a la final.')
        return data
