"""
Parser de emails de Correos.

A diferencia de GLS, Correos NO incluye el ID de pedido de Amazon/AliExpress
en el email — solo su propio número de envío y el nombre de quien lo remite
("remitido por X"). Sin un identificador compartido no podemos enlazarlo de
forma fiable a un Package ya existente (adivinar arriesga mezclar el tracking
de un paquete con el producto de otro), así que se trata como su propia
entrada independiente: source='correos', con el número de envío como id de
pedido y de paquete a la vez, y el remitente como título (mejor referencia
disponible para identificarlo en el panel).

El texto útil de estos emails viene solo en el HTML (el plaintext llega vacío).
"""
import html
import re

SENDER_DOMAIN = "correos.com"

STATUS_PATTERNS = [
    (re.compile(r"tiene prevista su entrega hoy", re.IGNORECASE), "out_for_delivery"),
    (re.compile(r"ser[aá] entregado en los pr[oó]ximos d[ií]as", re.IGNORECASE), "local_carrier"),
    (re.compile(r"ha sido entregad[oa]|entregamos tu env[ií]o", re.IGNORECASE), "delivered"),
]

TRACKING_RE = re.compile(r"env[ií]o\s+([A-Z0-9]{6,})", re.IGNORECASE)
SENDER_NAME_RE = re.compile(r"remitido por\s+([^.,\n]+)", re.IGNORECASE)


def matches(sender: str) -> bool:
    return SENDER_DOMAIN in sender.lower()


def detect_status(text: str) -> str:
    for pattern, status in STATUS_PATTERNS:
        if pattern.search(text):
            return status
    return "unknown"


def _strip_html(html_body: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html_body)
    text = re.sub(r"\s+", " ", text).strip()
    return html.unescape(text)


def parse(sender: str, subject: str, body_text: str, message_id: str, event_date, html_body: str = ""):
    if not matches(sender):
        return None

    text = _strip_html(html_body) if html_body else (body_text or "")

    tracking_match = TRACKING_RE.search(text)
    if not tracking_match:
        return None
    tracking_number = tracking_match.group(1)

    status = detect_status(text)
    if status == "unknown":
        return None

    sender_match = SENDER_NAME_RE.search(text)
    sender_name = sender_match.group(1).strip() if sender_match else None

    return {
        "source": "correos",
        "order_id": tracking_number,
        "package_id": tracking_number,
        "status": status,
        "status_label_raw": subject,
        "title": sender_name,
        "image_url": None,
        "courier": "correos",
        "courier_tracking_number": tracking_number,
        "message_id": message_id,
        "event_date": event_date,
    }
