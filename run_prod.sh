#!/bin/sh
# ============================================================
# Arranque LOCAL conectado a la BD de PRODUCCIÓN (MySQL 5.7.44)
#
# ⚠️  OJO: TODO lo que hagas acá escribe en los datos REALES
#     del liceo (mismos usuarios, mismos estudiantes).
#
# Uso:  ./run_prod.sh   →  http://127.0.0.1:8000
# ============================================================
set -e
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
  echo "No existe el venv. Creado primero con:"
  echo "  /opt/homebrew/bin/python3.12 -m venv .venv"
  echo "  .venv/bin/pip install -r requirements.txt"
  echo "(mysqlclient requiere mysql-client de Homebrew, ya instalado)"
  exit 1
fi

echo "⚠  Conectando a la BD de PRODUCCIÓN ($(grep -m1 '^DB_HOST' .env | cut -d= -f2))"
echo "⚠  Cualquier cambio que hagas en la app afecta datos reales."

echo ""
echo "ℹ  Estado de migraciones en producción (solo revisión, no aplica nada):"
.venv/bin/python manage.py showmigrations app 2>/dev/null | grep "\[ \]" || echo "    todas aplicadas ✔"

echo ""
echo "➜ Abre http://127.0.0.1:8000   (Ctrl+C para detener)"
echo "➜ Inicia sesión con un usuario REAL de producción."
exec .venv/bin/python manage.py runserver 127.0.0.1:8000
