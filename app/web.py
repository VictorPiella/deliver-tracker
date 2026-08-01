import os
from flask import Flask, render_template, jsonify, redirect, url_for, flash
from .models import get_session, Order, Package, PackageEvent, STATUS_LABELS_ES, STATUS_ORDER
from .gmail_sync import run_sync

DB_PATH = os.environ.get("DB_PATH", "data/packages.db")
# Mientras no esté conectado el OAuth real (ver gmail_sync.NOTA_PRODUCCION),
# usamos el mock con datos reales capturados durante el desarrollo. Cuando el
# OAuth esté listo, basta con cambiar este import por el adaptador real.
USE_MOCK_GMAIL = os.environ.get("USE_MOCK_GMAIL", "true").lower() == "true"

if USE_MOCK_GMAIL:
    from .mock_gmail import mock_search_fn as gmail_search_fn, mock_get_thread_fn as gmail_get_thread_fn
else:
    # NOTA_PRODUCCION: sustituir por el adaptador real de Gmail (google-api-python-client + OAuth)
    gmail_search_fn = None
    gmail_get_thread_fn = None

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-only-change-me")

if os.environ.get("ENABLE_BACKGROUND_WORKER", "true").lower() == "true":
    from .worker import start_worker
    start_worker(DB_PATH, gmail_search_fn, gmail_get_thread_fn)


def status_progress_pct(status: str) -> int:
    try:
        idx = STATUS_ORDER.index(status)
    except ValueError:
        return 0
    return int(((idx + 1) / len(STATUS_ORDER)) * 100)


@app.route("/")
def index():
    session = get_session(DB_PATH)
    packages = (
        session.query(Package)
        .order_by(Package.last_updated.desc())
        .all()
    )
    data = []
    for p in packages:
        data.append({
            "id": p.id,
            "source": p.source,
            "external_package_id": p.external_package_id,
            "title": p.order.title if p.order else None,
            "image_url": p.order.image_url if p.order else None,
            "status": p.status,
            "status_label": STATUS_LABELS_ES.get(p.status, p.status),
            "status_label_raw": p.status_label_raw,
            "last_updated": p.last_updated,
            "progress_pct": status_progress_pct(p.status),
            "n_events": len(p.events),
        })
    session.close()
    return render_template("index.html", packages=data)


@app.route("/package/<int:package_id>")
def package_detail(package_id):
    session = get_session(DB_PATH)
    p = session.query(Package).get(package_id)
    if p is None:
        session.close()
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
        "courier": p.courier,
        "courier_tracking_number": p.courier_tracking_number,
    }
    session.close()
    return render_template("detail.html", package=pkg_data, timeline=timeline)


@app.route("/api/packages")
def api_packages():
    session = get_session(DB_PATH)
    packages = session.query(Package).all()
    data = [{
        "id": p.id,
        "source": p.source,
        "external_package_id": p.external_package_id,
        "title": p.order.title if p.order else None,
        "status": p.status,
        "status_label": STATUS_LABELS_ES.get(p.status, p.status),
        "last_updated": p.last_updated.isoformat() if p.last_updated else None,
    } for p in packages]
    session.close()
    return jsonify(data)


@app.route("/sync", methods=["POST"])
def trigger_sync():
    if gmail_search_fn is None:
        flash("Gmail no está conectado todavía (falta configurar OAuth). Usando datos de ejemplo.", "warning")
        return redirect(url_for("index"))

    summary = run_sync(DB_PATH, gmail_search_fn, gmail_get_thread_fn)
    flash(
        f"Escaneo completo: {summary['scanned']} emails revisados, "
        f"{summary['ingested']} eventos nuevos.",
        "success",
    )
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
