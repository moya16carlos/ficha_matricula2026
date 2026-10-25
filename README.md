# Ficha de Matrícula — Liceo Municipal San Pedro

Sistema de matrículas Django (ficha.liceosp.cl): ficha por estudiante, matrículas
anuales por curso, flujo público de apoderado, generación de PDFs (ReportLab),
gestión de usuarios con roles y permisos por nivel (Básica/Media).

**Stack:** Django 5.2 LTS · Python 3.12 · MySQL (prod) / SQLite (local·tests) ·
Whitenoise · Gunicorn · Docker + Traefik.

---

## 1. Desarrollo local

```bash
./run.sh
```

Ese script único hace todo: usa SQLite automáticamente, migra la base local,
crea un usuario admin de desarrollo y deja el servidor en **http://127.0.0.1:8000**.

- **Login de desarrollo** → RUT: `11111111-1` · clave: `admin1234` (solo BD local,
  cámbiala con "perfil usuario"). Es independiente de los usuarios reales del servidor.
- La BD local es `db.sqlite3` en la raíz; bórrala para partir limpio y corre `./run.sh` de nuevo.
- Si necesitas otros usuarios de prueba (cualquier rol):
  ```bash
  DB_ENGINE=django.db.backends.sqlite3 .venv/bin/python manage.py shell -c \
    "from app.models import Usuarios; Usuarios.objects.create_user(run='99999999-9', nombre='Profe', apellido='Test', password='clave1234', rol='PROFESOR')"
  ```

Pasos manuales (si prefieres, equivale a lo que hace `run.sh`):

```bash
# 1) Entorno virtual (Python 3.12)
/opt/homebrew/bin/python3.12 -m venv .venv
.venv/bin/pip install -r requirements-local.txt

# 2) Configura tu .env
cp .env.example .env
# → genera una clave: python -c "import secrets; print(secrets.token_urlsafe(50))"

# 3) Migra y arranca en modo local (sin MySQL):
DB_ENGINE=django.db.backends.sqlite3 .venv/bin/python manage.py migrate
DB_ENGINE=django.db.backends.sqlite3 .venv/bin/python manage.py runserver
```

mysqlclient solo compila bien dentro de Docker... o instalalo local con Homebrew
(`brew install mysql-client` + `pip install mysqlclient`) — ya está hecho en el
venv actual. Para correr local (y tests), fuerza SQLite con la variable
`DB_ENGINE`; `settings.py` la soporta nativamente (o usa directamente `./run.sh`,
que ya lo hace por ti):

```bash
DB_ENGINE=django.db.backends.sqlite3 .venv/bin/python manage.py migrate
DB_ENGINE=django.db.backends.sqlite3 .venv/bin/python manage.py runserver
```

Estructura principal:

| Ruta | Rol |
|---|---|
| `app/views/` | Paquete de vistas por dominio (`_common.py` tiene helpers e importaciones compartidas) |
| `app/roles.py` | Roles, permisos por nivel y filtros de queryset |
| `app/models.py` | Modelos: Estudiante, MatriculaAnual, Curso, Usuarios, permisos… |
| `matriculas_lsp/settings.py` | Config por variables de entorno (falla si falta algo) |
| `matriculas_lsp/settings_test.py` | Settings de tests (SQLite `:memory:`) |

**⚠ Con la BD de producción.** Existe un segundo modo: trabajar local pero
leyendo/escribiendo la MySQL real del hosting:

```bash
./run_prod.sh      # runserver contra la MySQL real (NO aplica migraciones)
./migrate_prod.sh  # aplica migraciones pendientes a producción (pide confirmación)
```

Con `run_prod.sh` inicias sesión con un usuario **real** de producción.

- Cada cambio que hagas en la app toca datos reales: no borres estudiantes de verdad.
- `run.sh` y `run_prod.sh` no se interfieren: `run.sh` SIEMPRE fuerza la
  BD SQLite local (`db.sqlite3`); `run_prod.sh` toma el motor del `.env` (MySQL).
- Estado revisado 25-oct-2026: MySQL 5.7.44, solo migración pendiente `0030`
  (inofensiva, no genera SQL).

> Nota: `views_monolito_backup.py` (raíz) es el respaldo del viejo `views.py`
> de 4.5k líneas; referencia histórica, no se ejecuta.

## 2. Tests

Nunca apuntan a la MySQL real (usan SQLite en memoria):

```bash
DJANGO_SETTINGS_MODULE=matriculas_lsp.settings_test .venv/bin/python manage.py test app -v 1
```

Cubren: modelo/permisos de usuario, decoradores de rol, helpers de RUT/fecha,
seed y clonación de años con promoción de curso, login, y smoke-test de PDFs.

## 3. Migraciones

```bash
DB_ENGINE=django.db.backends.sqlite3 .venv/bin/python manage.py makemigrations app
# verificar estado sin cambiar nada:
DB_ENGINE=django.db.backends.sqlite3 .venv/bin/python manage.py makemigrations app --check --dry-run
```

En el servidor, el contenedor ya corre `migrate` al arrancar (docker-compose),
así que no hace falta ejecutarlo a mano — solo asegúrate de que el `.env` del
servidor exista antes de `docker compose up -d`.

## 4. Despliegue (checklist)

1. **Crear/actualizar `.env` en el servidor** (raíz del proyecto, junto al `docker-compose.yml`).
   El compose lo inyecta vía `env_file`. Sin este archivo el contenedor **no arranca**
   (settings con fail-fast). Valores requeridos:
   - `DJANGO_SECRET_KEY` → **una nueva**, no la del `.env` local
   - `DJANGO_DEBUG=False`
   - `DB_NAME` / `DB_USER` / `DB_PASSWORD` / `DB_HOST` / `DB_PORT`
   - `ALLOWED_HOSTS=ficha.liceosp.cl`
   - `CSRF_TRUSTED_ORIGINS=https://ficha.liceosp.cl`
   - `SECURE_COOKIES=True` (producción va tras TLS de Traefik)

2. **Rotar la contraseña de MySQL en cPanel/hosting** — la clave original quedó
   expuesta en el código fuente (vieja SECRET_KEY + password en el zip).
   Cambiarla en la BD y reflejarla en el `.env` del servidor.

3. Reconstruir y subir:
   ```bash
   docker compose build && docker compose up -d
   docker compose logs -f web   # verás migrate + collectstatic + gunicorn
   ```

4. Verificar: login en `https://ficha.liceosp.cl`, generación de un PDF,
   y panel de configuración.

5. **Nota de infraestructura (MySQL 5.7):** el hosting actual corre MySQL
   **5.7.44**, y Django 5.2 exige oficialmente 8.0.11+. El proyecto incluye el
   backend relajado `matriculas_lsp/db/mysql57/` que se activa automáticamente
   al usar `DB_ENGINE=mysql` (local o Docker) — no hay que configurar nada,
   pero pide al hosting migrar a **MySQL 8 / MariaDB 10.4+** cuanto antes:
   5.7 no recibe parches de seguridad desde oct-2023.

## 5. Decisiones de seguridad ya aplicadas

- Ningún secreto en el código: todo por `.env`; si falta algo, el proceso
  termina con mensaje claro en vez de seguir con credenciales por defecto.
- `DEBUG` por variable de entorno (`DJANGO_DEBUG`), cookies `Secure` en producción.
- `eliminar_usuario` exige rol ADMINISTRADOR (antes podía eliminar cualquier usuario logueado).
- `editar_usuario` hashea la contraseña con `set_password` (antes quedaba texto plano).
- `imprimir_estudiantes_por_curso` requiere sesión + rol permitido (antes era pública).
