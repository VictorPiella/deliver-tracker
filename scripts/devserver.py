"""
scripts/devserver.py — arranque de desarrollo dentro del container.

Es el CMD del target `dev` del Dockerfile. Hace dos cosas que
`flask run` por sí solo no hace bien en este montaje:

1. Recarga en caliente por SONDEO. El reloader por eventos (watchdog/inotify)
   no sirve aquí: los cambios en un bind-mount que viene del sistema de
   ficheros de Windows no generan eventos inotify dentro de la VM de Linux.
   Werkzeug cae a su reloader `stat` cuando watchdog no está instalado, y por
   eso watchdog NO está en requirements-dev.txt.

2. debugpy opcional. Con DEBUGPY=1 abre el puerto 5678 para engancharse desde
   VS Code (ver .vscode/launch.json). Se activa sólo en el proceso hijo del
   reloader — el que ejecuta la app de verdad — porque si no, el padre
   ocuparía el puerto y los breakpoints no dispararían nunca.
"""
import os
import sys

sys.path.insert(0, "/srv")


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def main():
    use_reloader = not _flag("NO_RELOAD")
    # Werkzeug marca el proceso hijo del reloader con WERKZEUG_RUN_MAIN=true.
    # Sin reloader no hay hijo, así que el proceso actual ya es el bueno.
    es_el_proceso_de_la_app = os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not use_reloader

    if _flag("DEBUGPY") and es_el_proceso_de_la_app:
        import debugpy

        puerto = int(os.environ.get("DEBUGPY_PORT", "5678"))
        debugpy.listen(("0.0.0.0", puerto))
        print(f"[devserver] debugpy escuchando en 0.0.0.0:{puerto}", flush=True)
        if _flag("DEBUGPY_WAIT"):
            print("[devserver] esperando a que VS Code se enganche...", flush=True)
            debugpy.wait_for_client()
            print("[devserver] depurador enganchado", flush=True)

    from app.web import create_app

    create_app().run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "5000")),
        debug=True,
        use_reloader=use_reloader,
        # Con el reloader activo, Flask reinicia en cada guardado; los threads
        # permiten que el panel siga respondiendo mientras un escaneo manual corre.
        threaded=True,
    )


if __name__ == "__main__":
    main()
