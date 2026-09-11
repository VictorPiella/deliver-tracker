#!/bin/sh
# Ajusta el usuario del proceso a PUID/PGID y le cede el volumen de datos.
#
# Por qué hace falta
# ------------------
# La imagen crea un usuario 'tracker' con uid 1000, pero en Unraid el share
# /mnt/user/appdata es de nobody:users (99:100). Con uid 1000 el container no
# puede escribir en /data: ni crea la base, ni puede reescribir token.json al
# refrescar el token de Gmail — y eso último rompe la sincronización en cuanto
# caduca el access token, una hora después de arrancar.
#
# Hacer `chown -R 1000:1000` en el share tampoco vale: la herramienta "Docker
# Safe New Permissions" de Unraid lo devuelve a 99:100 y el problema vuelve.
# Por eso se hace al revés, que es la convención de Unraid: el container se
# adapta al uid que le digas.
#
# El script arranca como root, hace los ajustes, y baja privilegios con setpriv
# antes de ejecutar el comando. El proceso de la app NUNCA corre como root.
set -e

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

USUARIO=tracker
DATA_DIR="$(dirname "${DB_PATH:-/data/packages.db}")"

if [ "$(id -u)" = "0" ]; then
    ACTUAL_UID="$(id -u "$USUARIO")"
    ACTUAL_GID="$(id -g "$USUARIO")"

    if [ "$PGID" != "$ACTUAL_GID" ]; then
        groupmod -o -g "$PGID" "$USUARIO"
    fi
    if [ "$PUID" != "$ACTUAL_UID" ]; then
        usermod -o -u "$PUID" "$USUARIO"
    fi

    mkdir -p "$DATA_DIR"
    # Sólo si hace falta: un chown -R en cada arranque sobre un volumen de red
    # es lento y tampoco aporta nada si ya está bien.
    PROPIETARIO="$(stat -c '%u:%g' "$DATA_DIR")"
    if [ "$PROPIETARIO" != "$PUID:$PGID" ]; then
        echo "[entrypoint] Ajustando $DATA_DIR a $PUID:$PGID (estaba en $PROPIETARIO)"
        chown -R "$PUID:$PGID" "$DATA_DIR" || \
            echo "[entrypoint] AVISO: no se pudo cambiar el propietario de $DATA_DIR"
    fi
    # /srv es el código: basta con poder leerlo.
    chown "$PUID:$PGID" /srv 2>/dev/null || true

    echo "[entrypoint] Arrancando como $USUARIO ($PUID:$PGID)"
    exec setpriv --reuid="$PUID" --regid="$PGID" --init-groups -- "$@"
fi

# Si alguien fuerza `user:` en el compose, ya no somos root: no hay nada que
# ajustar y se ejecuta tal cual.
echo "[entrypoint] Sin privilegios para ajustar el usuario; se ejecuta como $(id -un) ($(id -u):$(id -g))"
exec "$@"
