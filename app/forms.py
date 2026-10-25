# app/forms.py

from django import forms
from .models import AcademicYear, Apoderado, ConfiguracionMatricula, ConfiguracionSistema, Curso, Estudiante, ConfiguracionFirma

class LoginForm(forms.Form):
    run = forms.CharField(
        max_length=9,
        label='RUT',
        widget=forms.TextInput(attrs={
            'placeholder': 'RUT',
            'required': True
        })
    )
    password = forms.CharField(
        label='Contraseña',
        widget=forms.PasswordInput(attrs={
            'placeholder': 'Contraseña',
            'required': True
        })
    )

    def clean_run(self):
        run = self.cleaned_data.get('run', '').strip()
        # Aquí podrías agregar lógica de validación para asegurarte de que el RUT tiene el formato correcto.
        # Por ejemplo, podrías verificar si es un número válido, si tiene 9 dígitos, etc.
        # Esto es un ejemplo simple:
        if not run.isdigit() or len(run) not in [8, 9]:
            raise forms.ValidationError("El RUT debe contener entre 8 y 9 dígitos numéricos.")
        return run

class EstudianteForm(forms.ModelForm):
    fecha_nacimiento = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))  # Renderizar con un input de tipo date
    fecha_matricula = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))  # Renderizar con un input de tipo date
    class Meta:
        model = Estudiante
        fields = [
            'rut', 'nombre_completo', 'fecha_nacimiento', 'sexo','curso', 'nacionalidad', 'domicilio', 'comuna',
            'ascendencia_indigena', 'etnia', 'pertenece_al_pie', 'ha_repetido_curso', 'de_donde_viene',
            'nombre_madre', 'rut_madre', 'nivel_educacional_madre', 'ocupacion_madre', 'telefono_madre', 'email_madre',
            'nombre_padre', 'rut_padre', 'nivel_educacional_padre', 'ocupacion_padre', 'telefono_padre', 'email_padre',
            'nombre_apoderado', 'rut_apoderado', 'nivel_educacional_apoderado', 'ocupacion_apoderado', 'telefono_apoderado', 'email_apoderado',
            'fecha_nacimiento_apoderado',
            'vive_con', 'resolucion_judicial', 'problemas_salud', 'descripcion_problemas_salud', 'impide_educacion_fisica',
            'tipo_transporte', 'opta_religion', 'acepta_fotografias', 'fecha_matricula', 'parentesco_apoderado_suplente', 'seguro_salud_complementario'
        ]
    def __init__(self, *args, visible_years=None, selected_year=None, **kwargs):
        """
        visible_years: iterable de enteros con años visibles (si no viene, consulta AcademicYear)
        selected_year: si quieres forzar un año concreto (por GET ?anio=2026, por ejemplo)
        """
        super().__init__(*args, **kwargs)

        # 1) obtener años visibles
        if visible_years is None:
            visible_years = AcademicYear.objects.filter(is_visible=True).values_list('anio', flat=True)
        years = list(visible_years)  # asegúrate que es lista

        # 2) base queryset: cursos SOLO de años visibles
        qs = Curso.objects.filter(anio__in=years).order_by('anio', 'nivel', 'letra')

        # 3) si me pasas un año concreto en selected_year, filtro aún más
        if selected_year and str(selected_year).isdigit():
            qs = qs.filter(anio=int(selected_year))

        # aplicar al campo
        self.fields['curso'].queryset = qs
        self.fields['curso'].empty_label = '— Selecciona curso —'


#registro apoderado
class ApoderadoRegistroForm(forms.ModelForm):
    password = forms.CharField(widget=forms.PasswordInput(), label="Contraseña")

    class Meta:
        model = Apoderado
        fields = ['run', 'nombre', 'apellido', 'email', 'password']


class ConfiguracionFirmaForm(forms.ModelForm):
    class Meta:
        model = ConfiguracionFirma
        fields = ['nombre_director', 'imagen_firma', 'activa']
        widgets = {
            'nombre_director': forms.TextInput(attrs={'class': 'border bg-gray-100 text-black rounded-lg w-full px-3 py-2'}),
            'imagen_firma': forms.ClearableFileInput(attrs={'class': 'block w-full'}),
            'activa': forms.CheckboxInput(attrs={'class': 'h-4 w-4'}),
        }
        
class ConfiguracionMatriculaForm(forms.ModelForm):
    class Meta:
        model = ConfiguracionMatricula
        fields = ['titulo', 'anio', 'subtitulo', 'activa']
        widgets = {
            'titulo': forms.TextInput(attrs={'class': 'border bg-gray-100 text-black rounded-lg w-full px-3 py-2'}),
            'anio': forms.NumberInput(attrs={'class': 'border bg-gray-100 text-black rounded-lg w-full px-3 py-2'}),
            'subtitulo': forms.TextInput(attrs={'class': 'border bg-gray-100 text-black rounded-lg w-full px-3 py-2'}),
            'activa': forms.CheckboxInput(attrs={'class': 'h-4 w-4'}),
        }

class ConfiguracionSistemaForm(forms.ModelForm):
    class Meta:
        model = ConfiguracionSistema
        fields = ['registro_apoderado_habilitado']
        widgets = {
            'registro_apoderado_habilitado': forms.CheckboxInput(attrs={
                'class': 'h-4 w-4 text-indigo-600 rounded'
            })
        }