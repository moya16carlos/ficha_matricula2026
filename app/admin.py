from django.contrib import admin
from .models import AcademicYear, Curso, MatriculaAnual, Usuarios
from .models import Estudiante
from .models import Apoderado
from .models import ConfiguracionFirma, ConfiguracionMatricula
from django.utils.html import format_html

# Registrar el modelo Usuarios en el panel de administración
admin.site.register(Usuarios)
admin.site.register(Estudiante)
admin.site.register(Apoderado)

@admin.register(ConfiguracionFirma)
class ConfiguracionFirmaAdmin(admin.ModelAdmin):
    list_display = ('nombre_director', 'vista_previa', 'activa')
    list_editable = ('activa',)
    readonly_fields = ('vista_previa',)
    search_fields = ('nombre_director',)
    list_filter = ('activa',)
    actions = ['marcar_como_activa']

    # Mostrar imagen en el listado y el detalle
    def vista_previa(self, obj):
        if obj.imagen_firma:
            return format_html(
                '<img src="{}" style="height:60px;object-fit:contain;border:1px solid #ccc;padding:4px;border-radius:6px;" />',
                obj.imagen_firma.url
            )
        return "—"
    vista_previa.short_description = "Vista previa"

    # Acción para marcar una firma como activa y desactivar las demás
    @admin.action(description="Marcar como activa (desactiva las demás)")
    def marcar_como_activa(self, request, queryset):
        seleccionadas = queryset.all()
        if not seleccionadas.exists():
            self.message_user(request, "No se seleccionó ninguna firma.")
            return
        # Dejamos activa solo la última seleccionada
        ConfiguracionFirma.objects.update(activa=False)
        activa = seleccionadas.last()
        activa.activa = True
        activa.save()
        self.message_user(request, f"La firma '{activa.nombre_director}' se ha marcado como activa.")


@admin.register(ConfiguracionMatricula)
class ConfiguracionMatriculaAdmin(admin.ModelAdmin):
    list_display = ('anio', 'titulo', 'activa')
    list_editable = ('activa',)
    search_fields = ('anio', 'titulo')

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        # Asegura una sola activa
        if obj.activa:
            ConfiguracionMatricula.objects.exclude(pk=obj.pk).update(activa=False)

@admin.register(Curso)
class CursoAdmin(admin.ModelAdmin):
    list_display = ('nivel', 'letra', 'anio')
    list_filter = ('anio', 'nivel')
    search_fields = ('nivel', 'letra')
    ordering = ('anio', 'nivel', 'letra')



@admin.register(MatriculaAnual)
class MatriculaAnualAdmin(admin.ModelAdmin):
    list_display = ('anio','estudiante','curso','estado')
    list_filter = ('anio','curso__nivel','estado')
    search_fields = ('estudiante__nombre_completo','estudiante__rut')


@admin.register(AcademicYear)
class AcademicYearAdmin(admin.ModelAdmin):
    list_display = ('anio', 'is_visible', 'is_default')
    list_editable = ('is_visible', 'is_default')
    search_fields = ('anio',)