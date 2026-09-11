"""
Parser de emails de GLS (transportista de última milla en España).

A diferencia de Amazon/AliExpress, GLS no es la tienda: es quien reparte
paquetes que ya estamos siguiendo (típicamente AliExpress/Cainiao). El email
de GLS trae el ID de pedido de AliExpress directamente en el texto
("Tu pedido <package_id> de Ecommerce..."), lo que permite enlazarlo sin
ambigüedad al Package ya existente. El email de valoración post-entrega NO
trae ese package_id, solo el número de seguimiento GLS — en ese caso se
enlaza por courier_tracking_number ya guardado (ver sync.ingest_carrier_event).

Remitentes observados: no-reply@gls-spain.com y noreply@comunicaciones.gls-spain.com
(ambos bajo el dominio gls-spain.com).
"""
import html
import re

SENDER_DOMAIN = "gls-spain.com"

STATUS_PATTERNS = [
    (re.compile(r"est[aá] en camino", re.IGNORECASE), "local_carrier"),
    (re.compile(r"ya est[aá] en reparto", re.IGNORECASE), "out_for_delivery"),
    (re.compile(r"entregamos tu env[ií]o|hemos entregado tu env[ií]o", re.IGNORECASE), "delivered"),
]

# "Tu pedido 315193141453520012 de Ecommerce con Nº de seguimiento GLS 1316197997"
#
# El nombre del remitente NO es siempre "Ecommerce": tenerlo escrito a fuego
# hacía que emails perfectamente normales no se entendieran, p.ej.
#   "Tu pedido 1349764642 de VGL INTERNATIONAL TRADE MARKET SL con Nº de
#    seguimiento GLS 1349764642 está en camino."
# Ahora se acepta cualquier nombre de tienda entre "de" y "con Nº de seguimiento".
# El grupo del medio es la tienda: "Ecommerce", "VGL INTERNATIONAL TRADE MARKET
# SL"... Sirve de título cuando el envío acaba siendo una entrada propia, que es
# la única pista que hay sobre qué es el paquete.
PACKAGE_AND_TRACKING_RE = re.compile(
    r"pedido\s+(\w+)\s+de\s+(.{1,80}?)\s+con\s+N[ºo°]?\.?\s*de\s+seguimiento\s+GLS\s+(\w+)",
    re.IGNORECASE
)
# "...entregamos tu envio 1221358275 de Ecommerce..." (solo tracking, sin package_id)
TRACKING_ONLY_RE = re.compile(r"env[ií]o\s+(\w+)\s+de\s+\w", re.IGNORECASE)


def matches(sender: str) -> bool:
    return SENDER_DOMAIN in sender.lower()


def detect_status(text: str) -> str:
    for pattern, status in STATUS_PATTERNS:
        if pattern.search(text):
            return status
    return "unknown"


def parse(sender: str, subject: str, body_text: str, message_id: str, event_date, html_body: str = ""):
    """
    Devuelve {package_id, tracking_number, status, status_label_raw, message_id,
    event_date} o None. NO trae 'source' ni 'title'/'image_url': el llamador
    (gmail_sync.py) lo procesa vía sync.ingest_carrier_event, que busca el
    Package ya existente en vez de crear uno nuevo.
    """
    if not matches(sender):
        return None

    # Algunas plantillas de GLS traen el texto/plain con entidades HTML sin
    # decodificar (p.ej. "est&aacute;" en vez de "está"), lo que rompe el
    # matching de los patrones de estado si no se normaliza antes.
    text = html.unescape(body_text or "")
    status = detect_status(text)
    if status == "unknown":
        return None

    tienda = None
    m = PACKAGE_AND_TRACKING_RE.search(text)
    if m:
        package_id, tienda, tracking_number = m.group(1), m.group(2).strip(), m.group(3)
        # A veces GLS pone su propio nº de seguimiento donde debería ir la
        # referencia de la tienda ("Tu pedido 1349764642 ... seguimiento GLS
        # 1349764642"). Eso no es un id de AliExpress, así que buscar un
        # paquete por él no encontraría nada nunca: mejor admitir que no hay
        # referencia de tienda y tratarlo como envío suelto.
        if package_id == tracking_number:
            package_id = None
    else:
        package_id = None
        m2 = TRACKING_ONLY_RE.search(text)
        tracking_number = m2.group(1) if m2 else None

    if not package_id and not tracking_number:
        return None

    return {
        "package_id": package_id,
        "tracking_number": tracking_number,
        "tienda": tienda,
        "status": status,
        "status_label_raw": subject,
        "message_id": message_id,
        "event_date": event_date,
    }


def como_entrada_propia(parsed: dict) -> dict:
    """
    Convierte un evento de GLS en una entrada independiente, con el mismo
    formato que devuelven los parsers de tienda (source/order_id/package_id...).

    Se usa cuando el evento no se puede enganchar a ningún paquete existente.
    Antes, en ese caso, el evento se descartaba en silencio: ni aparecía en el
    panel ni contaba como "sin reconocer", así que un envío de GLS podía no
    existir para la app sin que nada lo dijera. Con esto al menos se ve, con su
    número de seguimiento y la tienda como título — y si más adelante aparece el
    paquete de la tienda, se pueden unir a mano con "Fusionar".
    """
    referencia = parsed.get("tracking_number") or parsed.get("package_id")
    return {
        "source": "gls",
        "order_id": referencia,
        "package_id": referencia,
        "status": parsed["status"],
        "status_label_raw": parsed["status_label_raw"],
        "title": parsed.get("tienda"),
        "image_url": None,
        "courier": "gls",
        "courier_tracking_number": parsed.get("tracking_number"),
        "message_id": parsed["message_id"],
        "event_date": parsed["event_date"],
    }
