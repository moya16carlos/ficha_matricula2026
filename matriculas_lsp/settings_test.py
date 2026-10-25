"""
Settings de TESTS (no producción).

Usado con: DJANGO_SETTINGS_MODULE=matriculas_lsp.settings_test

- SQLite en memoria: nunca toca la MySQL real (ni crea `_test` en el servidor).
- Hasheador rápido para acelerar los tests.
- StaticFiles sin "manifest" para no exigir collectstatic antes de testear.
"""
from .settings import *  # noqa: F401,F403

DEBUG = True
ALLOWED_HOSTS = ['*']

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
}

PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']

STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}
