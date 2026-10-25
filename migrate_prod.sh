#!/bin/sh
# ============================================================
# Aplica migraciones pendientes a la BD de PRODUCCIÓN.
# Pide confirmación antes de ejecutar.
# ============================================================
set -e
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
  echo "No existe el venv (ver README)." && exit 1
fi

echo "⚠  Plan de migraciones a aplicar en la BD de PRODUCCIÓN:"
echo ""
DB_ENGINE=django.db.backends.mysql .venv/bin/python manage.py migrate --plan 2>/dev/null || true
echo ""
printf "¿Continuar? [y/N]: "
read RESPONSE
if [ "$RESPONSE" = "y" ] || [ "$RESPONSE" = "Y" ]; then
    DB_ENGINE=django.db.backends.mysql .venv/bin/python manage.py migrate
    echo ""
    echo "✔ Migraciones aplicadas."
else
    echo "Cancelado (no se cambió nada)."
fi
