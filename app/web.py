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

from .models import get_session, Package, STATUS_LABELS_ES, STATUS_ORDER
from .gmail_sync import run_sync
from .cleanup import delete_package
from .sync import recompute_status_from_events
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


def status_progress_pct(status: str) -> int:
    try:
        idx = STATUS_ORDER.index(status)
    except ValueError:
        return 0
    return int(((idx + 1) / len(STATUS_ORDER)) * 100)


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

    register_routes(app)
    return app


def register_routes(app: Flask) -> None:

    @app.route("/")
    def index():
        packages = g.db.query(Package).order_by(Package.last_updated.desc()).all()
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
        } for p in packages]

        resumen = {
            "total": len(data),
            "en_transito": sum(1 for p in data if p["status"] != "delivered"),
            "entregados": sum(1 for p in data if p["status"] == "delivered"),
        }
        return render_template(
            "index.html",
            packages=data,
            resumen=resumen,
            estados=[(s, STATUS_LABELS_ES[s]) for s in STATUS_ORDER],
            auto_status=AUTO_STATUS,
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
        }
        return render_template(
            "detail.html",
            package=pkg_data,
            timeline=timeline,
            estados=[(s, STATUS_LABELS_ES[s]) for s in STATUS_ORDER],
            auto_status=AUTO_STATUS,
        )

    @app.route("/api/packages")
    def api_packages():
        packages = g.db.query(Package).all()
        return jsonify([{
            "id": p.id,
            "source": p.source,
            "external_package_id": p.external_package_id,
            "title": p.order.title if p.order else None,
            "status": p.status,
            "status_label": STATUS_LABELS_ES.get(p.status, p.status),
            "status_is_manual": p.status_is_manual,
            "last_updated": p.last_updated.isoformat() if p.last_updated else None,
        } for p in packages])

    @app.route("/healthz")
    def healthz():
        """Sonda del HEALTHCHECK del container: toca la base de verdad."""
        return jsonify({"status": "ok", "packages": g.db.query(Package).count()})

    @app.route("/package/<int:package_id>/delete", methods=["POST"])
    def delete_package_route(package_id):
        p = g.db.get(Package, package_id)
        if p is None:
            return "Paquete no encontrado", 404

        delete_package(g.db, p)
        g.db.commit()
        unpublish_package(package_id)
        flash("Paquete eliminado.", "success")
        return redirect(url_for("index"))

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

    @app.route("/sync", methods=["POST"])
    def trigger_sync():
        try:
            summary = run_sync(
                current_app.config["DB_PATH"],
                current_app.config["GMAIL_SEARCH_FN"],
                current_app.config["GMAIL_GET_THREAD_FN"],
            )
        except RuntimeError as e:
            flash(str(e), "warning")
            return redirect(url_for("index"))

        publish_all_packages(current_app.config["DB_PATH"])
        flash(
            f"Escaneo completo: {summary['scanned']} emails revisados, "
            f"{summary['ingested']} eventos nuevos.",
            "success",
        )
        return redirect(url_for("index"))


if __name__ == "__main__":
    create_app().run(host="0.0.0.0", port=5000, debug=True)
