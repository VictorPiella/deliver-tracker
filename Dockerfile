# Dos targets sobre una base común:
#   prod (por defecto) -> lo que se despliega en Unraid: gunicorn, sin dev deps.
#   dev                -> servidor de desarrollo de Flask + pytest + debugpy,
#                          pensado para usarse con el bind-mount de
#                          docker-compose.override.yml (recarga en caliente).
#
#   docker compose up --build                 # dev  (override automático)
#   docker build -t deliver-tracker:latest .  # prod (último stage)

# ---------------------------------------------------------------- base
FROM python:3.12-slim AS base

# PYTHONUNBUFFERED: sin esto los print() del worker se quedan en el buffer y no
# aparecen en `docker logs` hasta que el proceso muere.
# PYTHONIOENCODING: los logs llevan acentos (español); fija UTF-8 pase lo que pase.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# curl es para el HEALTHCHECK de más abajo.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Las dependencias van en su propia capa, antes del código: mientras
# requirements.txt no cambie, editar código no reinstala nada. En el bucle de
# desarrollo en Windows esto es la diferencia entre un rebuild de 2s y uno de 60s.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY scripts/ ./scripts/

# Usuario sin privilegios con el que corre la app. Su uid/gid se ajusta en
# arranque a PUID/PGID: en Unraid el share appdata es de nobody:users (99:100),
# y con uid 1000 el container no podría escribir la base ni reescribir
# token.json al refrescar el token de Gmail.
RUN useradd --create-home --uid 1000 --shell /bin/bash tracker \
    && mkdir -p /data \
    && chown -R tracker:tracker /data /srv

ENV PUID=1000 \
    PGID=1000

# El entrypoint arranca como root, ajusta el usuario y BAJA privilegios con
# setpriv antes de ejecutar el comando: la app nunca corre como root.
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

# La base de datos vive en /data, montado como volumen persistente desde
# Unraid (o Docker Desktop en Windows) para que sobreviva reinicios del container.
# FLASK_SECRET_KEY no se define aquí a propósito: hornear un valor por defecto
# en la imagen hace que todo despliegue que se olvide de ponerlo comparta la
# misma clave de sesión (y el linter de Docker lo marca, con razón). Lo pone el
# compose; si falta, app/web.py cae a un valor de desarrollo.
ENV DB_PATH=/data/packages.db \
    USE_MOCK_GMAIL=true \
    ENABLE_BACKGROUND_WORKER=true

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1:5000/healthz || exit 1

# ---------------------------------------------------------------- dev
FROM base AS dev

COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt

# Silencia el aviso de debugpy sobre validación de ficheros .pyc.
ENV PYDEVD_DISABLE_FILE_VALIDATION=1

# Servidor de desarrollo: recarga al guardar + debugpy opcional (DEBUGPY=1).
# Ver scripts/devserver.py para por qué no se usa `flask run` directamente.
# -Xfrozen_modules=off: con los módulos congelados de Python 3.11+ debugpy se
# salta breakpoints en el código de arranque. Sólo afecta al target dev.
CMD ["python", "-Xfrozen_modules=off", "-m", "scripts.devserver"]

# ---------------------------------------------------------------- prod
FROM base AS prod

# --workers 1 a propósito: la app usa SQLite y lleva el scheduler de APScheduler
# dentro del proceso. Con 2+ workers habría dos schedulers sincronizando Gmail en
# paralelo sobre el mismo fichero. La concurrencia que necesita un panel
# doméstico la dan de sobra los threads.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", \
     "--workers", "1", "--threads", "8", \
     "--timeout", "120", \
     "--access-logfile", "-", \
     "--chdir", "/srv", \
     "app.web:create_app()"]
