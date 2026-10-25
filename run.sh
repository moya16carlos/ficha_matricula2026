#!/bin/sh
# ============================================================
# Arranque LOCAL del proyecto (usa SQLite, NO toca la MySQL del servidor)
#
# Uso:   ./run.sh
# Login: http://127.0.0.1:8000
# ============================================================
set -e
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
  echo "No existe el venv. Crealo con esos 2 comandos y vuelve a correr ./run.sh:"
  echo "  /opt/homebrew/bin/python3.12 -m venv .venv"
  echo "  .venv/bin/pip install -r requirements-local.txt"
  exit 1
fi

# Local SIEMPRE con SQLite (mysqlclient solo compila dentro del contenedor Docker)
export DB_ENGINE=django.db.backends.sqlite3

echo "➜ Migrando BD local (SQLite)..."
.venv/bin/python manage.py migrate

# Usuario ADMIN de desarrollo (solo se crea si la BD local no tiene ninguno)
.venv/bin/python - <<'PY'
import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'matriculas_lsp.settings')
django.setup()
from app.models import Usuarios
if Usuarios.objects.filter(rol='ADMINISTRADOR').exists():
    print('OK: ya existe un ADMINISTRADOR en la BD local')
else:
    Usuarios.objects.create_user(
        run='11111111-1', nombre='Admin', apellido='Local',
        password='admin1234', email=None, rol='ADMINISTRADOR',
    )
    print('ADMIN local creado  ->  RUT: 11111111-1   clave: admin1234')
PY

echo ""
echo "➜ Abre http://127.0.0.1:8000   (Ctrl+C para detener)"
exec .venv/bin/python manage.py runserver 127.0.0.1:8000
