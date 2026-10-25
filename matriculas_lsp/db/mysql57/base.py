"""
Backend MySQL "relajado" para Django 5.2 con MySQL 5.7.

Contexto:
- El MySQL del hosting actual es 5.7.44 (fin de soporte oficial: oct-2023).
- Django 4.2/5.2 exigen 8.0.11+ y abortan la conexión con NotSupportedError.
- Este backend es idéntico al oficial (django.db.backends.mysql); lo único que
  cambia es que no rechaza servidores antiguos.

Riesgo asumido: 5.7 es tercera edad y no recibe parches de seguridad, así que
pide al hosting migrar a MySQL 8 o MariaDB 10.4+ cuanto antes. Si eso ocurre,
este backend sigue funcionando sin cambios (simplemente no re-chequea versión).
"""
from django.db.backends.mysql import base


class DatabaseWrapper(base.DatabaseWrapper):
    def check_database_version_supported(self):
        # Permitido a sabiendas: el hosting corre MySQL 5.7.44.
        # (El método padre compararía contra features.minimum_database_version=(8,0,11))
        return
