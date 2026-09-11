"""
web.py — panel Flask.

Expone una *app factory* (`create_app`) en vez de una app global creada al
importar el módulo. Dos motivos:

- El worker en background se arrancaba como efecto secundario del import. Con
  gunicorn --workers 2 eso significaba DOS schedulers escribiendo en la misma
  SQLite, cada uno sincronizando Gmail por su cuenta. Ahora el worker se
  arranca dentro de create_app(), y el container arranca con un único worker
  de gunicorn y varios threads (ver Dockerfile), que es lo que corresponde a
  una app con SQLite y un scheduler en proceso.
- Los tests pueden construir la app apuntando a una base temporal y con el
  worker desactivado, sin depender de variables de entorno globales.
"""
import os

from flask import (
    Flask, current_app, g, render_template, jsonify, redirect, request, url_for, flash
)

from .models import (
    get_last_sync, get_session, Package, UnparsedEmail,
    STATUS_LABELS_ES, STATUS_ORDER,
)
from .timeutils import utcnow, a_zona_local, formatear
from .gmail_sync import run_sync
from .cleanup import hard_delete_package, restore_package, soft_delete_package
from .sync import merge_packages, recompute_status_from_events
from .mqtt_publish import publish_all_packages, unpublish_package

# Valor especial del desplegable de estado: suelta el estado manual y vuelve a
# dejar que manden los emails.
AUTO_STATUS = "__auto__"

DEFAULT_DB_PATH = "data/packages.db"


def _env_flag(name: str, default: str) -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def get_gmail_adapters(use_mock: bool):
    """
    Devuelve (search_fn, get_thread_fn). El import es perezoso a propósito:
    gmail_oauth arrastra google-api-python-client, que no hace falta cargar
    cuando se trabaja con el mock.
    """
    if use_mock:
        from .mock_gmail import mock_search_fn, mock_get_thread_fn
        return mock_search_fn, mock_get_thread_fn
    from .gmail_oauth import gmail_search_fn, gmail_get_thread_fn
    return gmail_search_fn, gmail_get_thread_fn


def formato_local(momento, patron: str = "%d %b %Y · %H:%M") -> str:
    """
    Filtro Jinja para pintar fechas. TODO lo que hay en la base es UTC, así que
    sin convertir aquí el panel mostraba horas con 1-2h de desfase respecto al
    reloj de casa. La zona sale de TZ (ver app/timeutils.py).
    """
    if momento is None:
        return "—"
    return formatear(a_zona_local(momento), patron)


def humanizar_antiguedad(momento) -> str:
    """
    "hace 12 min" / "hace 3 h" / "hace 2 días". Sirve para que se vea de un
    vistazo si el worker lleva parado más de la cuenta.
    """
    if momento is None:
        return "nunca"
    segundos = (utcnow() - momento).total_seconds()
    if segundos < 0:
        return "hace un momento"
    minutos = segundos / 60
    if minutos < 2:
        return "hace un momento"
    if minutos < 60:
        return f"hace {int(minutos)} min"
    horas = minutos / 60
    if horas < 24:
        return f"hace {int(horas)} h"
    dias = int(horas / 24)
    return f"hace {dias} día{'' if dias == 1 else 's'}"


def status_progress_pct(status: str) -> int:
    try:
        idx = STATUS_ORDER.index(status)
    except ValueError:
        return 0
    return int(((idx + 1) / len(STATUS_ORDER)) * 100)


def _instalar_auth(app: Flask, password: str) -> None:
    """
    Autenticación básica HTTP, opcional (sólo si PANEL_PASSWORD está puesta).

    En una LAN de casa no hace falta, pero en cuanto el panel se publica detrás
    de un proxy inverso cualquiera que llegue a él puede borrar paquetes. Es
    HTTP Basic a propósito: sin sesiones ni formulario de login que mantener, y
    el proxy ya pone el TLS. No sustituye a no exponerlo a internet.
    """
    import hmac
    from flask import Response, request as peticion

    usuario_esperado = os.environ.get("PANEL_USER", "admin")

    @app.before_request
    def _exigir_password():
        # La sonda del HEALTHCHECK corre dentro del container y no lleva
        # credenciales; dejarla fuera evita que Docker mate el container.
        if peticion.endpoint == "healthz":
            return None

        auth = peticion.authorization
        ok = (
            auth is not None
            and hmac.compare_digest(auth.username or "", usuario_esperado)
            and hmac.compare_digest(auth.password or "", password)
        )
        if ok:
            return None
        return Response(
            "Autenticación requerida.", 401,
            {"WWW-Authenticate": 'Basic realm="Delivery Tracker"'},
        )


def create_app(db_path: str | None = None, use_mock_gmail: bool | None = None,
               enable_worker: bool | None = None) -> Flask:
    app = Flask(__name__)

    db_path = db_path or os.environ.get("DB_PATH", DEFAULT_DB_PATH)
    if use_mock_gmail is None:
        use_mock_gmail = _env_flag("USE_MOCK_GMAIL", "true")
    if enable_worker is None:
        enable_worker = _env_flag("ENABLE_BACKGROUND_WORKER", "true")

    app.config["DB_PATH"] = db_path
    app.config["USE_MOCK_GMAIL"] = use_mock_gmail
    app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-only-change-me")

    # La carpeta de la base puede no existir todavía (primer arranque, volumen
    # recién montado en Unraid); crearla aquí evita un fallo al abrir SQLite.
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)

    search_fn, get_thread_fn = get_gmail_adapters(use_mock_gmail)
    app.config["GMAIL_SEARCH_FN"] = search_fn
    app.config["GMAIL_GET_THREAD_FN"] = get_thread_fn

    if enable_worker:
        from .worker import start_worker
        start_worker(db_path, search_fn, get_thread_fn)

    # El orden importa: la comprobación de contraseña se registra antes que la
    # apertura de sesión, así una petición no autenticada ni toca la base.
    panel_password = os.environ.get("PANEL_PASSWORD", "").strip()
    if panel_password:
        _instalar_auth(app, panel_password)

    @app.before_request
    def _open_session():
        g.db = get_session(current_app.config["DB_PATH"])

    @app.teardown_appcontext
    def _close_session(exc):
        # Antes cada ruta cerraba su sesión a mano, así que una excepción a
        # mitad de request la dejaba abierta. Con teardown se cierra siempre.
        db = g.pop("db", None)
        if db is not None:
            if exc is not None:
                db.rollback()
            db.close()

    app.jinja_env.filters["local"] = formato_local

    @app.context_processor
    def _contadores_nav():
        """
        Contadores para los enlaces de la cabecera. Van en un context processor
        para que estén en todas las plantillas sin repetirlos en cada ruta.
        """
        if not hasattr(g, "db"):
            return {}
        return {
            "n_papelera": g.db.query(Package).filter(Package.deleted_at.isnot(None)).count(),
            "n_sin_reconocer": g.db.query(UnparsedEmail).count(),
        }

    register_routes(app)
    return app


def register_routes(app: Flask) -> None:

    @app.route("/")
    def index():
        packages = (
            g.db.query(Package)
            .filter(Package.deleted_at.is_(None))
            .order_by(Package.last_updated.desc())
            .all()
        )
        data = [{
            "id": p.id,
            "source": p.source,
            "external_package_id": p.external_package_id,
            "title": p.order.title if p.order else None,
            "image_url": p.order.image_url if p.order else None,
            "status": p.status,
            "status_label": STATUS_LABELS_ES.get(p.status, p.status),
            "status_label_raw": p.status_label_raw,
            "status_is_manual": p.status_is_manual,
            "last_updated": p.last_updated,
            "progress_pct": status_progress_pct(p.status),
            "n_events": len(p.events),
            "courier_tracking_number": p.courier_tracking_number,
            "eta": p.eta,
        } for p in packages]

        resumen = {
            "total": len(data),
            "en_transito": sum(1 for p in data if p["status"] != "delivered"),
            "entregados": sum(1 for p in data if p["status"] == "delivered"),
        }
        ultimo = get_last_sync(g.db)
        return render_template(
            "index.html",
            packages=data,
            resumen=resumen,
            estados=[(s, STATUS_LABELS_ES[s]) for s in STATUS_ORDER],
            auto_status=AUTO_STATUS,
            ultimo_escaneo=ultimo,
            ultimo_escaneo_rel=humanizar_antiguedad(ultimo),
        )

    @app.route("/package/<int:package_id>")
    def package_detail(package_id):
        p = g.db.get(Package, package_id)
        if p is None:
            return "Paquete no encontrado", 404

        events = sorted(p.events, key=lambda e: e.event_date)
        timeline = [{
            "status": e.status,
            "status_label": STATUS_LABELS_ES.get(e.status, e.status),
            "status_label_raw": e.status_label_raw,
            "event_date": e.event_date,
            "gmail_subject": e.gmail_subject,
        } for e in events]

        pkg_data = {
            "id": p.id,
            "source": p.source,
            "external_package_id": p.external_package_id,
            "title": p.order.title if p.order else None,
            "image_url": p.order.image_url if p.order else None,
            "order_external_id": p.order.external_order_id if p.order else None,
            "status": p.status,
            "status_label": STATUS_LABELS_ES.get(p.status, p.status),
            "progress_pct": status_progress_pct(p.status),
            "status_is_manual": p.status_is_manual,
            "courier": p.courier,
            "courier_tracking_number": p.courier_tracking_number,
            "eta": p.eta,
        }
        candidatos = [{
            "id": otro.id,
            "etiqueta": f"{(otro.order.title if otro.order else None) or otro.external_package_id}"
                        f" · {otro.source}",
        } for otro in (
            g.db.query(Package)
            .filter(Package.deleted_at.is_(None), Package.id != p.id)
            .order_by(Package.last_updated.desc())
            .all()
        )]

        return render_template(
            "detail.html",
            package=pkg_data,
            timeline=timeline,
            candidatos=candidatos,
            estados=[(s, STATUS_LABELS_ES[s]) for s in STATUS_ORDER],
            auto_status=AUTO_STATUS,
        )

    @app.route("/api/packages")
    def api_packages():
        packages = g.db.query(Package).filter(Package.deleted_at.is_(None)).all()
        return jsonify([{
            "id": p.id,
            "source": p.source,
            "external_package_id": p.external_package_id,
            "title": p.order.title if p.order else None,
            "status": p.status,
            "status_label": STATUS_LABELS_ES.get(p.status, p.status),
            "status_is_manual": p.status_is_manual,
            "eta": p.eta,
            "last_updated": p.last_updated.isoformat() if p.last_updated else None,
        } for p in packages])

    @app.route("/healthz")
    def healthz():
        """Sonda del HEALTHCHECK del container: toca la base de verdad."""
        return jsonify({
            "status": "ok",
            "packages": g.db.query(Package).filter(Package.deleted_at.is_(None)).count(),
        })

    @app.route("/package/<int:package_id>/delete", methods=["POST"])
    def delete_package_route(package_id):
        """
        Manda el paquete a la papelera. NO borra la fila: hacerlo se llevaba por
        delante sus eventos, y con ellos los gmail_message_id que impiden
        reprocesar un email, así que el paquete reaparecía en el escaneo
        siguiente. Ver app/cleanup.py.
        """
        p = g.db.get(Package, package_id)
        if p is None:
            return "Paquete no encontrado", 404

        soft_delete_package(g.db, p)
        g.db.commit()
        unpublish_package(package_id)
        flash("Paquete movido a la papelera.", "success", )
        return redirect(url_for("index"))

    @app.route("/papelera")
    def papelera():
        borrados = (
            g.db.query(Package)
            .filter(Package.deleted_at.isnot(None))
            .order_by(Package.deleted_at.desc())
            .all()
        )
        data = [{
            "id": p.id,
            "source": p.source,
            "external_package_id": p.external_package_id,
            "title": p.order.title if p.order else None,
            "image_url": p.order.image_url if p.order else None,
            "status": p.status,
            "status_label": STATUS_LABELS_ES.get(p.status, p.status),
            "deleted_at": p.deleted_at,
            "n_events": len(p.events),
        } for p in borrados]
        return render_template("papelera.html", packages=data)

    @app.route("/package/<int:package_id>/restore", methods=["POST"])
    def restore_package_route(package_id):
        p = g.db.get(Package, package_id)
        if p is None:
            return "Paquete no encontrado", 404

        restore_package(g.db, p)
        g.db.commit()
        publish_all_packages(current_app.config["DB_PATH"])
        flash("Paquete restaurado.", "success")
        return redirect(url_for("papelera"))

    @app.route("/package/<int:package_id>/purge", methods=["POST"])
    def purge_package_route(package_id):
        """
        Borrado definitivo desde la papelera. Aquí sí desaparecen los eventos,
        así que si los emails siguen dentro de la ventana de búsqueda el
        paquete volverá a aparecer en el próximo escaneo. Se avisa en la UI.
        """
        p = g.db.get(Package, package_id)
        if p is None:
            return "Paquete no encontrado", 404

        hard_delete_package(g.db, p)
        g.db.commit()
        unpublish_package(package_id)
        flash("Paquete borrado definitivamente.", "success")
        return redirect(url_for("papelera"))

    @app.route("/papelera/vaciar", methods=["POST"])
    def vaciar_papelera():
        borrados = g.db.query(Package).filter(Package.deleted_at.isnot(None)).all()
        for p in borrados:
            unpublish_package(p.id)
            hard_delete_package(g.db, p)
        g.db.commit()
        flash(f"Papelera vaciada: {len(borrados)} paquete(s) borrados definitivamente.", "success")
        return redirect(url_for("papelera"))

    @app.route("/sin-reconocer")
    def sin_reconocer():
        """
        Emails de remitentes conocidos que ningún parser supo leer. Es la señal
        de que Amazon o AliExpress han cambiado una plantilla.
        """
        filas = (
            g.db.query(UnparsedEmail)
            .order_by(UnparsedEmail.event_date.desc())
            .limit(200)
            .all()
        )
        return render_template("sin_reconocer.html", emails=filas)

    @app.route("/sin-reconocer/limpiar", methods=["POST"])
    def limpiar_sin_reconocer():
        n = g.db.query(UnparsedEmail).delete()
        g.db.commit()
        flash(f"{n} email(s) descartados de la lista.", "success")
        return redirect(url_for("sin_reconocer"))

    @app.route("/package/<int:package_id>/status", methods=["POST"])
    def set_status_route(package_id):
        """
        Cambia el estado a mano desde el panel. El estado manual manda sobre lo
        que digan los emails posteriores (ver sync.apply_status); con AUTO_STATUS
        se suelta y el estado vuelve a calcularse desde los eventos recibidos.
        """
        p = g.db.get(Package, package_id)
        if p is None:
            return "Paquete no encontrado", 404

        nuevo = (request.form.get("status") or "").strip()

        if nuevo == AUTO_STATUS:
            p.status_is_manual = False
            recompute_status_from_events(p)
            g.db.commit()
            flash(f"Estado devuelto al automático: {STATUS_LABELS_ES.get(p.status, p.status)}.", "success")
        elif nuevo in STATUS_ORDER:
            p.status = nuevo
            p.status_is_manual = True
            p.status_label_raw = "Marcado a mano desde el panel"
            g.db.commit()
            flash(f"Estado cambiado a «{STATUS_LABELS_ES[nuevo]}».", "success")
        else:
            flash("Estado no válido.", "warning")
            return redirect(request.referrer or url_for("index"))

        publish_all_packages(current_app.config["DB_PATH"])
        return redirect(request.referrer or url_for("index"))

    @app.route("/package/<int:package_id>/merge", methods=["POST"])
    def merge_package_route(package_id):
        """
        Funde este paquete dentro de otro. Sirve sobre todo para los envíos de
        Correos, que llegan como entrada aparte porque su email no trae ningún
        ID de la tienda con el que enlazarlos automáticamente.
        """
        origen = g.db.get(Package, package_id)
        if origen is None:
            return "Paquete no encontrado", 404

        try:
            destino_id = int(request.form.get("target_id", ""))
        except ValueError:
            flash("No se ha indicado con qué paquete fusionar.", "warning")
            return redirect(url_for("package_detail", package_id=package_id))

        destino = g.db.get(Package, destino_id)
        if destino is None or destino.id == origen.id:
            flash("Paquete de destino no válido.", "warning")
            return redirect(url_for("package_detail", package_id=package_id))

        movidos = merge_packages(g.db, origen, destino)
        g.db.commit()
        unpublish_package(package_id)
        publish_all_packages(current_app.config["DB_PATH"])
        flash(f"Paquetes fusionados: {movidos} evento(s) movidos.", "success")
        return redirect(url_for("package_detail", package_id=destino.id))

    @app.route("/sync", methods=["POST"])
    def trigger_sync():
        """
        Lanza el escaneo en segundo plano y contesta al momento. Antes corría
        dentro de la propia petición, y tras un parón largo la ventana
        adaptativa puede pedir hasta un año de correo: más de los 120s de
        timeout de gunicorn. La petición moría, y como el marcador de último
        escaneo se pone al final, el reintento fallaba exactamente igual.
        """
        from .worker import trigger_sync_now

        lanzado = trigger_sync_now(
            current_app.config["DB_PATH"],
            current_app.config["GMAIL_SEARCH_FN"],
            current_app.config["GMAIL_GET_THREAD_FN"],
        )
        if lanzado:
            flash("Escaneo en marcha. El panel se actualizará al terminar.", "success")
        else:
            flash("Ya hay un escaneo en marcha.", "warning")
        return redirect(url_for("index"))

    @app.route("/api/sync-status")
    def api_sync_status():
        """Lo consulta el panel para saber cuándo recargar."""
        from .worker import sync_state

        estado = sync_state()
        resumen = estado.get("last_summary") or {}
        return jsonify({
            "running": estado["running"],
            "error": estado.get("last_error"),
            "ingested": resumen.get("ingested"),
            "scanned": resumen.get("scanned"),
            "unparsed": resumen.get("unparsed"),
        })


if __name__ == "__main__":
    create_app().run(host="0.0.0.0", port=5000, debug=True)
