"""
Django settings for matriculas_lsp project.

Security model:
- No hay secretos en el código: todo viene de variables de entorno (.env local,
  o `env_file: .env` en docker-compose para el contenedor).
- Si falta un valor requerido, el proceso muere al arrancar con un mensaje claro,
  en vez de usar un fallback silencioso con credenciales reales.
"""

from pathlib import Path
import os

from dotenv import load_dotenv

# =========================
# Rutas + carga de .env
# =========================
BASE_DIR = Path(__file__).resolve().parent.parent

# Local (runserver): carga .env si existe. Docker usa `env_file: .env` del compose.
load_dotenv(BASE_DIR / '.env')


def _required(name: str) -> str:
    """Lee una variable obligatoria; si falta, explica cómo arreglarlo."""
    value = os.environ.get(name)
    if value is None:
        raise RuntimeError(
            f"Falta la variable de entorno {name!r}. "
            f"Cópiala en tu archivo .env (mira .env.example en la raíz del proyecto)."
        )
    return value


# =========================
# Seguridad / entorno
# =========================
try:
    SECRET_KEY = os.environ['DJANGO_SECRET_KEY']
except KeyError as err:
    raise RuntimeError(
        "Falta DJANGO_SECRET_KEY en el entorno (archivo .env). "
        "Genera una con: python -c \"import secrets; print(secrets.token_urlsafe(50))\""
    ) from err

DEBUG = os.getenv('DJANGO_DEBUG', 'False').lower() in ('true', '1', 'yes')

ALLOWED_HOSTS = os.getenv(
    'ALLOWED_HOSTS',
    '127.0.0.1,localhost'
).split(',')

# Orígenes de confianza: solo hace falta el dominio con TLS + los hosts locales para dev
CSRF_TRUSTED_ORIGINS = os.getenv(
    'CSRF_TRUSTED_ORIGINS',
    'http://127.0.0.1:8000,http://localhost:8000'
).split(',')


# =========================
# Application definition
# =========================
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'app',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'matriculas_lsp.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'matriculas_lsp.wsgi.application'


# =========================
# Base de datos
# =========================
DB_ENGINE = os.getenv('DB_ENGINE', 'django.db.backends.mysql')

# El hosting actual corre MySQL 5.7.44, inferior al mínimo exigido por
# Django 4.2+/5.2 (8.0.11). Usamos un backend propio que omite ÚNICAMENTE
# la verificación de versión; todo lo demás es el backend oficial.
# Ver matriculas_lsp/db/mysql57/base.py
if DB_ENGINE == 'django.db.backends.mysql':
    DB_ENGINE = 'matriculas_lsp.db.mysql57'

if DB_ENGINE == 'django.db.backends.sqlite3':
    # Modo local/tests: no requiere MySQL ni mysqlclient instalado.
    # Ojo: DB_NAME normalmente designa la BD MySQL de .env; solo lo usamos como
    # archivo SQLite si termina en .sqlite3 (para poder apuntar a una BD de prueba).
    _sqlite_name = os.getenv('DB_NAME', '')
    if not _sqlite_name.endswith('.sqlite3'):
        _sqlite_name = str(BASE_DIR / 'db.sqlite3')
    DATABASES = {
        'default': {
            'ENGINE': DB_ENGINE,
            'NAME': _sqlite_name,
        }
    }
else:
    # mysqlclient solo compila dentro del contenedor Docker; si alguien intenta
    # correr con MySQL fuera de Docker, que el error sea claro.
    try:
        import MySQLdb  # noqa: F401  (módulo de mysqlclient)
    except ImportError as err:
        raise RuntimeError(
            "El proyecto está configurado para MySQL (DB_ENGINE por .env), pero el "
            "paquete 'mysqlclient' no está instalado en este entorno.\n"
            "  • Para desarrollo local: corre ./run.sh (usa SQLite) o antepone\n"
            "    DB_ENGINE=django.db.backends.sqlite3 a tu comando.\n"
            "  • En Docker no hace falta nada: requirements.txt ya lo incluye."
        ) from err

    DATABASES = {
        'default': {
            'ENGINE': DB_ENGINE,
            'NAME': _required('DB_NAME'),
            'USER': _required('DB_USER'),
            'PASSWORD': _required('DB_PASSWORD'),
            'HOST': os.getenv('DB_HOST', ''),
            'PORT': os.getenv('DB_PORT', '3306'),
            'CONN_MAX_AGE': 60,  # reutiliza la conexión: sin handshake TCP+auth en cada request
            'OPTIONS': {
                'charset': 'utf8mb4',
                'init_command': (
                    "SET sql_mode='STRICT_TRANS_TABLES,"
                    "ERROR_FOR_DIVISION_BY_ZERO,"
                    "NO_ENGINE_SUBSTITUTION', "
                    "time_zone='-03:00'"
                ),
            },
        }
    }


# =========================
# Password validation
# =========================
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# =========================
# Internacionalización
# =========================
LANGUAGE_CODE = 'es-cl'
TIME_ZONE = 'America/Santiago'

USE_I18N = True
USE_TZ = True


# =========================
# Static files
# =========================
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

# Estilo Django >= 4.2 (STATICFILES_STORAGE está eliminado en Django 5).
# Producción (DEBUG=False): storage de WhiteNoise con manifest (requiere collectstatic,
# el contenedor ya lo corre). Desarrollo (DEBUG=True): storage simple, evita el error
# "Missing staticfiles manifest entry" sin necesidad de collectstatic local.
STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': (
            'whitenoise.storage.CompressedManifestStaticFilesStorage'
            if not DEBUG
            else 'django.contrib.staticfiles.storage.StaticFilesStorage'
        ),
    },
}

# Usa esto SOLO si realmente tienes una carpeta "static" en la raíz del proyecto
if (BASE_DIR / 'static').exists():
    STATICFILES_DIRS = [BASE_DIR / 'static']


# =========================
# Media files
# =========================
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'


# =========================
# Modelo de usuario
# =========================
AUTH_USER_MODEL = 'app.Usuarios'

# El login real vive en '/' (name 'login'); el default de Django es
# '/accounts/login/' que aquí no existe y daba 404 a quien no está logueado.
LOGIN_URL = 'login'

AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
]


# =========================
# Proxy / HTTPS detrás de Traefik
# =========================
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# Cookies seguras por defecto en producción; en local (http://localhost) DEBUG=True
# deja cookies inseguras para que login/CSRF funcionen sin HTTPS.
SECURE_COOKIES = os.getenv('SECURE_COOKIES')
if SECURE_COOKIES is None:
    SECURE_COOKIES = 'True' if not DEBUG else 'False'

SESSION_COOKIE_SECURE = SECURE_COOKIES == 'True'
CSRF_COOKIE_SECURE = SECURE_COOKIES == 'True'


# =========================
# Default primary key field type
# =========================
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
