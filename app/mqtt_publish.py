"""
mqtt_publish.py — publica el estado de cada paquete a Home Assistant vía
MQTT Discovery (https://www.home-assistant.io/integrations/mqtt/#discovery).

Cada paquete se expone como un sensor HA agrupado bajo un único "device"
("Deliver Tracker"), con:
  - un topic de config (retained) que HA lee para crear la entidad automáticamente,
  - un topic de estado (retained) con el status normalizado (p.ej. "out_for_delivery"),
  - un topic de atributos JSON (retained) con título, transportista, tracking, etc.

Desactivado por defecto (MQTT_ENABLED=false): sin broker configurado, esto no
hace nada. Publicar es "mejor esfuerzo" — un fallo de conexión al broker no
debe romper el sync de Gmail, así que los errores solo se registran.
"""
import os
import json

import paho.mqtt.publish as mqtt_publish

MQTT_ENABLED = os.environ.get("MQTT_ENABLED", "false").lower() == "true"
MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USERNAME = os.environ.get("MQTT_USERNAME") or None
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD") or None
MQTT_DISCOVERY_PREFIX = os.environ.get("MQTT_DISCOVERY_PREFIX", "homeassistant")

DEVICE = {
    "identifiers": ["deliver_tracker"],
    "name": "Deliver Tracker",
    "manufacturer": "deliver-tracker",
}


def _auth():
    if MQTT_USERNAME:
        return {"username": MQTT_USERNAME, "password": MQTT_PASSWORD}
    return None


def _config_topic(package_id: int) -> str:
    return f"{MQTT_DISCOVERY_PREFIX}/sensor/deliver_tracker/package_{package_id}/config"


def _state_topic(package_id: int) -> str:
    return f"homeassistant/sensor/package_{package_id}/state"


def _attributes_topic(package_id: int) -> str:
    return f"homeassistant/sensor/package_{package_id}/attributes"


def _package_payloads(p) -> list:
    """Construye los 3 mensajes (config, state, attributes) para un Package."""
    title = p.order.title if p.order else None
    name = title or f"Paquete {p.external_package_id}"

    config = {
        "name": name,
        "unique_id": f"deliver_tracker_package_{p.id}",
        "state_topic": _state_topic(p.id),
        "json_attributes_topic": _attributes_topic(p.id),
        "icon": "mdi:package-variant",
        "device": DEVICE,
    }
    attributes = {
        "title": title,
        "source": p.source,
        "external_package_id": p.external_package_id,
        "status_label": p.status_label_raw,
        "courier": p.courier,
        "courier_tracking_number": p.courier_tracking_number,
        "last_updated": p.last_updated.isoformat() if p.last_updated else None,
    }
    return [
        {"topic": _config_topic(p.id), "payload": json.dumps(config), "retain": True},
        {"topic": _state_topic(p.id), "payload": p.status, "retain": True},
        {"topic": _attributes_topic(p.id), "payload": json.dumps(attributes), "retain": True},
    ]


def publish_all_packages(db_path: str, log=print) -> int:
    """Publica el estado actual de todos los paquetes a MQTT. Devuelve cuántos se publicaron."""
    if not MQTT_ENABLED:
        return 0

    from .models import get_session, Package

    session = get_session(db_path)
    packages = session.query(Package).all()
    msgs = []
    for p in packages:
        msgs.extend(_package_payloads(p))
    session.close()

    if not msgs:
        return 0

    try:
        mqtt_publish.multiple(msgs, hostname=MQTT_HOST, port=MQTT_PORT, auth=_auth())
    except Exception as e:
        log(f"[mqtt] Publicación omitida (broker no disponible?): {e}")
        return 0

    log(f"[mqtt] {len(packages)} paquete(s) publicados a {MQTT_HOST}:{MQTT_PORT}")
    return len(packages)


def unpublish_package(package_id: int, log=print) -> None:
    """Retira la entidad de un paquete borrado de Home Assistant (payload vacío en el config topic)."""
    if not MQTT_ENABLED:
        return
    try:
        mqtt_publish.single(
            _config_topic(package_id), payload="", retain=True,
            hostname=MQTT_HOST, port=MQTT_PORT, auth=_auth(),
        )
    except Exception as e:
        log(f"[mqtt] No se pudo retirar la entidad de HA (broker no disponible?): {e}")
