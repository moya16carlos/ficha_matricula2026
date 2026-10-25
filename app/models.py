# app/models.py

from django.conf import settings
from django.db import models
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.dispatch import receiver
from django.db.models.signals import post_save

# Crear un administrador de usuarios personalizado
# ==============================
#  MANAGER PERSONALIZADO
# ==============================
class UsuarioManager(BaseUserManager):
    def create_user(self, run, nombre, apellido, password=None, **extra_fields):
        if not run:
            raise ValueError("El RUT es obligatorio")
        user = self.model(run=run, nombre=nombre, apellido=apellido, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, run, nombre, apellido, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)

        if extra_fields.get('is_staff') is not True:
            raise ValueError("El superusuario debe tener is_staff=True.")
        if extra_fields.get('is_superuser') is not True:
            raise ValueError("El superusuario debe tener is_superuser=True.")

        return self.create_user(run, nombre, apellido, password, **extra_fields)


# ==============================
#  MODELO DE USUARIOS
# ==============================
class Usuarios(AbstractBaseUser, PermissionsMixin):
    ROL_CHOICES = [
        ('ADMINISTRADOR', 'Administrador'),
        ('DIRECTIVO', 'Directivo'),
        ('PROFESOR', 'Profesor'),
        ('ASISTENTE', 'Asistente'),
    ]

    id_usuario = models.AutoField(primary_key=True)
    run = models.CharField(max_length=9, unique=True)  # RUT único
    nombre = models.CharField(max_length=50)
    apellido = models.CharField(max_length=50)
    email = models.EmailField(unique=True, null=True, blank=True)
    rol = models.CharField(max_length=15, choices=ROL_CHOICES, default='ASISTENTE')
    foto_perfil = models.ImageField(upload_to='fotos_perfil/', null=True, blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(auto_now_add=True)

    USERNAME_FIELD = 'run'
    REQUIRED_FIELDS = ['nombre', 'apellido']

    objects = UsuarioManager()

    class Meta:
        verbose_name = "Usuario"
        verbose_name_plural = "Usuarios"

    def __str__(self):
        return f"{self.nombre} {self.apellido} ({self.run}) - {self.rol}"

    # ==============================
    #   MÉTODO DE PERMISOS POR NIVEL
    # ==============================
    def has_nivel_perm(self, nivel, action):
        """
        Verifica si el usuario tiene permisos específicos por nivel (BASICA o MEDIA).
        Los administradores siempre tienen todos los permisos.
        action: 'view' | 'add' | 'edit' | 'print'
        """
        if self.rol == 'ADMINISTRADOR':
            return True

        perm = self.permisos_nivel.filter(nivel=nivel).first()
        if not perm:
            return False

        mapping = {
            'view': perm.can_view,
            'add': perm.can_add,
            'edit': perm.can_edit,
            'print': perm.can_print,
        }
        return mapping.get(action, False)


# ==============================
#  MODELO DE PERMISOS POR NIVEL
# ==============================
NIVEL_CHOICES = [
    ('BASICA', 'Básica'),
    ('MEDIA', 'Media'),
]

class UsuarioPermiso(models.Model):
    usuario = models.ForeignKey(
        'Usuarios',
        on_delete=models.CASCADE,
        related_name='permisos_nivel'
    )
    nivel = models.CharField(max_length=10, choices=NIVEL_CHOICES)
    can_view = models.BooleanField(default=False)
    can_add = models.BooleanField(default=False)
    can_edit = models.BooleanField(default=False)
    can_print = models.BooleanField(default=False)

    class Meta:
        unique_together = ('usuario', 'nivel')
        verbose_name = "Permiso por Nivel"
        verbose_name_plural = "Permisos por Nivel"

    def __str__(self):
        return f"{self.usuario.run} - {self.get_nivel_display()}"


# ==============================
#  SEÑAL: CREAR PERMISOS POR DEFECTO
# ==============================
@receiver(post_save, sender=Usuarios)
def crear_permisos_por_defecto(sender, instance, created, **kwargs):
    """
    Crea automáticamente permisos vacíos para los usuarios nuevos
    (excepto administradores).
    """
    if not created:
        return

    if instance.rol == 'ADMINISTRADOR':
        return

    for nv in ('BASICA', 'MEDIA'):
        UsuarioPermiso.objects.get_or_create(usuario=instance, nivel=nv)


class Curso(models.Model):
    NIVEL_CHOICES = [
        ('Sin asignar', 'Sin asignar'),  # <-- agrega esta línea
        ('Prekinder', 'Prekinder'),
        ('Kinder', 'Kinder'),
        ('1° Básico', '1° Básico'),
        ('2° Básico', '2° Básico'),
        ('3° Básico', '3° Básico'),
        ('4° Básico', '4° Básico'),
        ('5° Básico', '5° Básico'),
        ('6° Básico', '6° Básico'),
        ('7° Básico', '7° Básico'),
        ('8° Básico', '8° Básico'),
        ('1° Medio', '1° Medio'),
        ('2° Medio', '2° Medio'),
        ('3° Medio', '3° Medio'),
        ('4° Medio', '4° Medio'),
    ]

    nivel = models.CharField(max_length=20, choices=NIVEL_CHOICES)
    letra = models.CharField(max_length=2, blank=True, null=True, help_text="Ej: A, B, C")
    anio = models.PositiveIntegerField(default=2025, help_text="Año académico (ej: 2025)")

    class Meta:
        verbose_name = "Curso"
        verbose_name_plural = "Cursos"
        unique_together = ('nivel', 'letra', 'anio')
        ordering = ['anio', 'nivel', 'letra']

    def __str__(self):
        return f"{self.nivel} {self.letra or ''} ({self.anio})".strip()

class Estudiante(models.Model):
    # Información del Estudiante
    rut = models.CharField(max_length=13, unique=True)  # RUT del estudiante (sin guiones ni puntos)
    nombre_completo = models.CharField(max_length=100)  # Nombre completo del estudiante
    fecha_nacimiento = models.DateField()  # Fecha de nacimiento
    sexo = models.CharField(max_length=10, choices=[
        ('Masculino', 'Masculino'),
        ('Femenino', 'Femenino')
    ])  # Sexo del estudiante

    curso = models.ForeignKey('Curso', on_delete=models.PROTECT, related_name='estudiantes')
    #curso_fk = models.ForeignKey('Curso', null=True, blank=True, on_delete=models.SET_NULL, related_name='estudiantes')
    nacionalidad = models.CharField(max_length=50)  # Nacionalidad
    domicilio = models.CharField(max_length=200)  # Domicilio
    comuna = models.CharField(max_length=100)  # Comuna
    ascendencia_indigena = models.BooleanField(default=False)  # ¿Tiene ascendencia indígena?
    etnia = models.CharField(max_length=100, blank=True, null=True)  # Etnia (si tiene ascendencia indígena)
    pertenece_al_pie = models.BooleanField(default=False)  # ¿Pertenece al PIE?
    ha_repetido_curso = models.BooleanField(default=False)  # ¿Ha repetido curso?
    estado = models.CharField(max_length=20, choices=[
        ('Activo', 'Activo'),
        ('Inactivo', 'Inactivo'),
        ('Egresado', 'Egresado')
    ], default='Activo')  # Estado del estudiante
    de_donde_viene = models.CharField(max_length=100, blank=True, null=True)  # Colegio o institución de procedencia

    # Información de la Madre
    nombre_madre = models.CharField(max_length=100, blank=True, null=True)
    rut_madre = models.CharField(max_length=13, blank=True, null=True)
    nivel_educacional_madre = models.CharField(max_length=50, blank=True, null=True)
    ocupacion_madre = models.CharField(max_length=100, blank=True, null=True)
    telefono_madre = models.CharField(max_length=20, blank=True, null=True)
    email_madre = models.EmailField(max_length=100, blank=True, null=True)

    # Información del Padre
    nombre_padre = models.CharField(max_length=100, blank=True, null=True)
    rut_padre = models.CharField(max_length=13, blank=True, null=True)
    nivel_educacional_padre = models.CharField(max_length=50, blank=True, null=True)
    ocupacion_padre = models.CharField(max_length=100, blank=True, null=True)
    telefono_padre = models.CharField(max_length=20, blank=True, null=True)
    email_padre = models.EmailField(max_length=100, blank=True, null=True)

    # Información del Apoderado
    nombre_apoderado = models.CharField(max_length=100, blank=True, null=True)
    rut_apoderado = models.CharField(max_length=13, blank=True, null=True)
    nivel_educacional_apoderado = models.CharField(max_length=50, blank=True, null=True)
    ocupacion_apoderado = models.CharField(max_length=100, blank=True, null=True)
    telefono_apoderado = models.CharField(max_length=20, blank=True, null=True)
    email_apoderado = models.EmailField(max_length=100, blank=True, null=True)
    fecha_nacimiento_apoderado = models.DateField(blank=True, null=True)  #NUEVO CAMPO

    # Apoderado Suplente
    nombre_apoderado_suplente = models.CharField(max_length=100, blank=True, null=True)
    rut_apoderado_suplente = models.CharField(max_length=13, blank=True, null=True)
    parentesco_apoderado_suplente = models.CharField(max_length=40, blank=True, null=True,help_text="Parentesco del apoderado suplente con el estudiante (ej: Tía, Abuelo, Hermana...)")
    nivel_educacional_apoderado_suplente = models.CharField(max_length=50, blank=True, null=True)
    ocupacion_apoderado_suplente = models.CharField(max_length=100, blank=True, null=True)
    telefono_apoderado_suplente = models.CharField(max_length=20, blank=True, null=True)
    email_apoderado_suplente = models.EmailField(max_length=100, blank=True, null=True)

    # Información Social y de Salud
    vive_con = models.CharField(max_length=50, choices=[
        ('Ambos Padres', 'Ambos Padres'),
        ('Madre', 'Madre'),
        ('Padre', 'Padre'),
        ('Otros', 'Otros')
    ])
    resolucion_judicial = models.BooleanField(default=False)
    problemas_salud = models.BooleanField(default=False)
    descripcion_problemas_salud = models.TextField(blank=True, null=True)
    impide_educacion_fisica = models.BooleanField(default=False)
    tipo_transporte = models.CharField(max_length=100, blank=True, null=True)
    opta_religion = models.BooleanField(default=False)
    certificado_medico = models.BooleanField(default=False)
    acepta_fotografias = models.BooleanField(default=False)
    fecha_matricula = models.DateField()
    sistema_salud = models.CharField(max_length=10, choices=[
        ('Fonasa', 'Fonasa'),
        ('Isapre', 'Isapre'),
        ('Particular', 'Particular')
    ], default='Fonasa')

    # === Nuevos campos de auditoría ===
    quien_matricula = models.CharField(
        "Quién matricula",
        max_length=150,
        blank=True,
        null=True,
        help_text="Nombre del usuario o funcionario que realizó la matrícula."
    )
    
    matriculado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="estudiantes_matriculados",
        verbose_name="Usuario que matricula",
        help_text="Usuario autenticado que registró al estudiante."
    )
     
    seguro_salud_complementario = models.CharField(
        max_length=100, blank=True, null=True,
        help_text="Nombre del seguro de salud complementario o descripción (ej: Colmena, Banmédica, No tiene)"
    )

    # === Actualización de RUT (estudiantes extranjeros: provisional → definitivo) ===
    rut_anterior = models.CharField(
        max_length=13, blank=True, null=True,
        help_text="RUT del estudiante antes de su última actualización."
    )
    rut_actualizado_en = models.DateTimeField(
        blank=True, null=True,
        help_text="Fecha y hora de la última actualización de RUT."
    )
    rut_actualizado_por = models.CharField(
        max_length=150, blank=True, null=True,
        help_text="Usuario que realizó la última actualización de RUT."
    )

    def __str__(self):
        return f"{self.nombre_completo} ({self.rut})"


class DocumentoEstudiante(models.Model):
    estudiante = models.ForeignKey(Estudiante, related_name='documentos', on_delete=models.CASCADE)
    nombre = models.CharField(max_length=255)  # Nombre del documento
    archivo = models.FileField(upload_to='documentos_estudiantes/')  # Ruta donde se guarda el archivo
    fecha_subida = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.nombre} - {self.estudiante.nombre_completo}"
    


#clase apoderado
class Apoderado(models.Model):
    run = models.CharField(max_length=10, unique=True)
    nombre = models.CharField(max_length=50)
    apellido = models.CharField(max_length=50)
    email = models.EmailField(unique=True)
    password = models.CharField(max_length=128)  # Se guarda cifrada
    is_active = models.BooleanField(default=True)

    def save(self, *args, **kwargs):
        # Si la clave no está ya cifrada, se cifra antes de guardar
        if not self.password.startswith('pbkdf2'):
            self.password = make_password(self.password)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.nombre} {self.apellido} ({self.run})"
    
#Firma Director modificable
class ConfiguracionFirma(models.Model):
    nombre_director = models.CharField(max_length=100, default="Director Liceo San Pedro")
    imagen_firma = models.ImageField(upload_to='firmas/', blank=True, null=True)
    activa = models.BooleanField(default=True)

    def __str__(self):
        return f"Firma de {self.nombre_director}"
    

class ConfiguracionMatricula(models.Model):
    titulo = models.CharField(
        max_length=200,
        default='FICHA DE INSCRIPCIÓN DE MATRÍCULA {anio}',
        help_text="Puedes usar {anio} para insertar el año automáticamente."
    )
    anio = models.PositiveIntegerField(default=2025)
    subtitulo = models.CharField(max_length=200, blank=True, null=True)
    activa = models.BooleanField(default=True, help_text="Debe haber solo una activa.")

    class Meta:
        verbose_name = "Configuración de Matrícula"
        verbose_name_plural = "Configuraciones de Matrícula"

    def __str__(self):
        return f"{self.anio} - {'Activa' if self.activa else 'Inactiva'}"

    def titulo_resuelto(self):
        # Permite usar {anio} en el título
        return (self.titulo or '').format(anio=self.anio)
    

class ConfiguracionSistema(models.Model):
    registro_apoderado_habilitado = models.BooleanField(default=True)

    def __str__(self):
        return "Configuración General del Sistema"
    

# === NUEVO: MatriculaAnual (no modifica Estudiante) ===
class MatriculaAnual(models.Model):
    estudiante = models.ForeignKey('Estudiante', on_delete=models.CASCADE, related_name='matriculas_anuales')
    curso = models.ForeignKey('Curso', on_delete=models.PROTECT, null=True, blank=True,
                              related_name='matriculas')
    anio = models.PositiveIntegerField()

    # “congelamos”/copiamos datos del estudiante para ese año
    # (los campos coinciden con tu Estudiante para permitir seed/clone)
    estado = models.CharField(
        max_length=20,
        choices=[('Activo', 'Activo'), ('Inactivo', 'Inactivo'), ('Egresado', 'Egresado')],
        default='Activo'
    )
    fecha_matricula = models.DateField(null=True, blank=True)

    nacionalidad = models.CharField(max_length=50, blank=True, null=True)
    domicilio = models.CharField(max_length=200, blank=True, null=True)
    comuna = models.CharField(max_length=100, blank=True, null=True)

    ascendencia_indigena = models.BooleanField(default=False)
    etnia = models.CharField(max_length=100, blank=True, null=True)
    pertenece_al_pie = models.BooleanField(default=False)
    ha_repetido_curso = models.BooleanField(default=False)
    de_donde_viene = models.CharField(max_length=100, blank=True, null=True)

    nombre_madre = models.CharField(max_length=100, blank=True, null=True)
    rut_madre = models.CharField(max_length=13, blank=True, null=True)
    nivel_educacional_madre = models.CharField(max_length=50, blank=True, null=True)
    ocupacion_madre = models.CharField(max_length=100, blank=True, null=True)
    telefono_madre = models.CharField(max_length=20, blank=True, null=True)
    email_madre = models.EmailField(max_length=100, blank=True, null=True)

    nombre_padre = models.CharField(max_length=100, blank=True, null=True)
    rut_padre = models.CharField(max_length=13, blank=True, null=True)
    nivel_educacional_padre = models.CharField(max_length=50, blank=True, null=True)
    ocupacion_padre = models.CharField(max_length=100, blank=True, null=True)
    telefono_padre = models.CharField(max_length=20, blank=True, null=True)
    email_padre = models.EmailField(max_length=100, blank=True, null=True)

    nombre_apoderado = models.CharField(max_length=100, blank=True, null=True)
    rut_apoderado = models.CharField(max_length=13, blank=True, null=True)
    nivel_educacional_apoderado = models.CharField(max_length=50, blank=True, null=True)
    ocupacion_apoderado = models.CharField(max_length=100, blank=True, null=True)
    telefono_apoderado = models.CharField(max_length=20, blank=True, null=True)
    email_apoderado = models.EmailField(max_length=100, blank=True, null=True)

    nombre_apoderado_suplente = models.CharField(max_length=100, blank=True, null=True)
    rut_apoderado_suplente = models.CharField(max_length=13, blank=True, null=True)
    nivel_educacional_apoderado_suplente = models.CharField(max_length=50, blank=True, null=True)
    ocupacion_apoderado_suplente = models.CharField(max_length=100, blank=True, null=True)
    telefono_apoderado_suplente = models.CharField(max_length=20, blank=True, null=True)
    email_apoderado_suplente = models.EmailField(max_length=100, blank=True, null=True)

    vive_con = models.CharField(
        max_length=50,
        choices=[('Ambos Padres', 'Ambos Padres'), ('Madre', 'Madre'), ('Padre', 'Padre'), ('Otros', 'Otros')],
        blank=True, null=True
    )
    resolucion_judicial = models.BooleanField(default=False)
    problemas_salud = models.BooleanField(default=False)
    descripcion_problemas_salud = models.TextField(blank=True, null=True)
    impide_educacion_fisica = models.BooleanField(default=False)

    tipo_transporte = models.CharField(max_length=100, blank=True, null=True)
    opta_religion = models.BooleanField(default=False)
    certificado_medico = models.BooleanField(default=False)
    acepta_fotografias = models.BooleanField(default=False)
    sistema_salud = models.CharField(
        max_length=10,
        choices=[('Fonasa', 'Fonasa'), ('Isapre', 'Isapre'), ('Particular', 'Particular')],
        default='Fonasa'
    )

    nombre_snapshot = models.CharField(max_length=120, blank=True, null=True)

    class Meta:
        verbose_name = "Matrícula anual"
        verbose_name_plural = "Matrículas anuales"
        ordering = ['anio', 'curso__nivel', 'curso__letra', 'estudiante__nombre_completo']
        constraints = [
            models.UniqueConstraint(fields=['estudiante', 'anio'], name='uniq_matricula_por_anio')
        ]
        # Nota: los índices inferiores se perdían antes por el doble `class Meta`
        # (el segundo sobrescribía al primero). Solo el constraint llegó a la BD
        # real; makemigrations añadirá los índices con la próxima migración.
        indexes = [
            models.Index(fields=['anio']),
            models.Index(fields=['anio', 'curso']),
        ]

    def __str__(self):
        est = getattr(self, 'estudiante', None)
        return f"{self.anio} · {est.nombre_completo if est else '—'}"
    

class AcademicYear(models.Model):
    """
    Controla visibilidad de un año en 'Listar Estudiantes'.
    """
    anio = models.PositiveIntegerField(unique=True)
    is_visible = models.BooleanField(default=True)   # si aparece para usuarios no admin
    is_default = models.BooleanField(default=False)  # opcional: año sugerido por defecto

    class Meta:
        ordering = ['-anio']

    def __str__(self):
        return f"{self.anio} ({'visible' if self.is_visible else 'oculto'})"