"""
Parser de emails de Amazon.

Cada tipo de evento viene de un remitente distinto:
  auto-confirm@amazon.es      -> pedido realizado     ("Pedido: ...")
  confirmar-envio@amazon.es   -> enviado               ("Enviado: ...")
  shipment-tracking@amazon.es -> en reparto             ("En reparto: ...")
  order-update@amazon.es      -> entregado              ("Entregado: N productos | N.º de pedido XXX-XXXXXXX-XXXXXXX")
  devolucion@amazon.es        -> IGNORAR (flujo de devoluciones, no de entrada)
"""
import re

SENDER_STATUS_MAP = {
    "auto-confirm@amazon.es": "ordered",
    "confirmar-envio@amazon.es": "shipped",
    "shipment-tracking@amazon.es": "out_for_delivery",
    "order-update@amazon.es": "delivered",
}

IGNORED_SENDERS = {
    "devolucion@amazon.es",
}

ORDER_ID_RE = re.compile(r"\b(\d{3}-\d{7}-\d{7})\b")

# En el cuerpo en texto plano, el patrón real visto es:
#   "Pedido n.º\n408-2435062-0199514"
# Preferimos anclar a esta etiqueta antes que un regex de formato suelto,
# para evitar falsos positivos con otros números de 17 dígitos con guiones
# que puedan aparecer en banners de productos relacionados, etc.
ORDER_ID_LABELED_RE = re.compile(r"Pedido n\.?[ºo]\s*\n?\s*(\d{3}-\d{7}-\d{7})", re.IGNORECASE)

# El link de seguimiento también trae shipmentId, útil como identificador de
# envío más granular que el order_id cuando un pedido se divide en varios paquetes.
SHIPMENT_ID_RE = re.compile(r"shipmentId=([A-Za-z0-9]+)")


def matches(sender: str) -> bool:
    sender = sender.lower()
    return any(s in sender for s in SENDER_STATUS_MAP) or any(s in sender for s in IGNORED_SENDERS)


def should_ignore(sender: str) -> bool:
    sender = sender.lower()
    return any(s in sender for s in IGNORED_SENDERS)


def extract_order_id(subject: str, body_text: str = "") -> str | None:
    """
    Busca el nº de pedido. Prioridad:
      1. Asunto (caso 'Entregado: ... N.º de pedido 408-...')
      2. Cuerpo, anclado a la etiqueta 'Pedido n.º' (caso 'Enviado'/'En reparto')
      3. Cuerpo, regex de formato suelto como último recurso
    """
    m = ORDER_ID_RE.search(subject)
    if m:
        return m.group(1)
    if body_text:
        m = ORDER_ID_LABELED_RE.search(body_text)
        if m:
            return m.group(1)
        m = ORDER_ID_RE.search(body_text)
        if m:
            return m.group(1)
    return None


def extract_shipment_id(body_text: str = "") -> str | None:
    """Extrae el shipmentId del link de seguimiento si está presente en el cuerpo."""
    if not body_text:
        return None
    m = SHIPMENT_ID_RE.search(body_text)
    return m.group(1) if m else None


# El email de Amazon trae la imagen del producto en un <img class="productImage" src="...">
# dentro de m.media-amazon.com. Buscamos la primera ocurrencia.
PRODUCT_IMAGE_RE = re.compile(
    r'class="productImage"[^>]*src="(https://m\.media-amazon\.com/images/[^"]+)"',
    re.IGNORECASE
)
# Algunos emails tienen el orden de atributos invertido (src antes que class)
PRODUCT_IMAGE_RE_ALT = re.compile(
    r'src="(https://m\.media-amazon\.com/images/I/[^"]+)"[^>]*class="productImage"',
    re.IGNORECASE
)


def extract_image_url(html_body: str = "") -> str | None:
    """Extrae la URL de la imagen del producto del HTML del email, si está presente."""
    if not html_body:
        return None
    m = PRODUCT_IMAGE_RE.search(html_body)
    if m:
        return m.group(1)
    m = PRODUCT_IMAGE_RE_ALT.search(html_body)
    if m:
        return m.group(1)
    return None


QUOTE_CHARS = ' "\u201c\u201d\u2018\u2019'


def clean_title(subject: str) -> str:
    """Quita el prefijo de estado del asunto para quedarnos con el nombre del producto."""
    # Quita prefijos tipo 'Enviado: ', 'Pedido: ', 'En reparto: ', 'Entregado: N productos | ...'
    subject = re.sub(r"^(Pedido|Enviado|En reparto|Entregado)[:\s]*", "", subject, flags=re.IGNORECASE)
    subject = re.sub(r"\d+\s*productos?\s*\|.*$", "", subject, flags=re.IGNORECASE)
    subject = subject.strip(QUOTE_CHARS)
    # quita sufijo tipo ' y 2 productos más' que puede quedar tras la limpieza anterior
    subject = re.sub(r"\s+y\s+\d+\s*productos?\s*m[aá]s\s*$", "", subject, flags=re.IGNORECASE)
    subject = subject.strip(QUOTE_CHARS)
    return subject or "(sin título)"


def parse(sender: str, subject: str, body_text: str, message_id: str, event_date, html_body: str = ""):
    """
    Devuelve un dict normalizado o None si no aplica / hay que ignorar.
    {
        'source': 'amazon',
        'order_id': '408-1234567-1234567' | None,
        'package_id': None,   # amazon no expone un package id distinto del order id
        'status': 'shipped',
        'status_label_raw': subject,
        'title': 'WOLTU Mesitas de Noche...',
        'image_url': 'https://m.media-amazon.com/...' | None,
        'message_id': ...,
        'event_date': ...,
    }
    """
    sender_l = sender.lower()

    if should_ignore(sender_l):
        return None

    status = None
    for key, val in SENDER_STATUS_MAP.items():
        if key in sender_l:
            status = val
            break

    if status is None:
        return None

    order_id = extract_order_id(subject, body_text)
    shipment_id = extract_shipment_id(body_text)
    image_url = extract_image_url(html_body)
    title = clean_title(subject)

    return {
        "source": "amazon",
        "order_id": order_id,
        "package_id": shipment_id,  # None si no se pudo extraer; sync.py usará fallback
        "status": status,
        "status_label_raw": subject,
        "title": title,
        "image_url": image_url,
        "message_id": message_id,
        "event_date": event_date,
    }
