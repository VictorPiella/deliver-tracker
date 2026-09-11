"""
Parser de emails de Amazon.

Cada tipo de evento viene de un remitente distinto:
  auto-confirm@amazon.es      -> pedido realizado     ("Pedido: ...")
  confirmar-envio@amazon.es   -> enviado               ("Enviado: ...")
  shipment-tracking@amazon.es -> en reparto             ("En reparto: ...")
  order-update@amazon.es      -> DEPENDE DEL ASUNTO: entregas, cancelaciones,
                                 intentos de entrega fallidos y cambios de
                                 fecha llegan todos desde aquí (ver
                                 ORDER_UPDATE_PATTERNS)
  devolucion@amazon.es        -> IGNORAR (flujo de devoluciones, no de entrada)
"""
import re

SENDER_STATUS_MAP = {
    "auto-confirm@amazon.es": "ordered",
    "confirmar-envio@amazon.es": "shipped",
    "shipment-tracking@amazon.es": "out_for_delivery",
    # order-update NO se mapea aquí: ese remitente manda varias cosas distintas
    # y hay que mirar el asunto. Ver ORDER_UPDATE_PATTERNS.
    "order-update@amazon.es": None,
}

# order-update@amazon.es es un cajón de sastre. Dándolo por "entregado" sin
# mirar, como se hacía antes, salían cosas como estas marcadas como entregadas:
#   "Productos cancelados correctamente: ..."        -> en realidad, cancelado
#   "El producto se ha cancelado correctamente: ..." -> cancelado
#   "Intento de entrega realizado: ..."              -> NO se ha entregado
#   "Actualización de entrega: ..."                  -> sólo cambia la fecha
# Y como 'delivered' es terminal y el estado sólo avanza, se quedaban así para
# siempre — y la purga automática acababa mandándolos a la papelera.
ORDER_UPDATE_PATTERNS = [
    (re.compile(r"^\s*Entregado\b", re.IGNORECASE), "delivered"),
    (re.compile(r"cancelad[oa]s?\s+correctamente|se\s+ha\s+cancelado", re.IGNORECASE), "cancelled"),
    (re.compile(r"intento\s+de\s+entrega", re.IGNORECASE), "delivery_attempted"),
    # "Actualización de entrega" sólo reajusta la fecha estimada: el evento se
    # guarda (la ETA es útil) pero 'unknown' no pisa el estado que ya hubiera.
    (re.compile(r"actualizaci[oó]n\s+de\s+entrega", re.IGNORECASE), "unknown"),
]


def status_desde_asunto_order_update(subject: str) -> str:
    for patron, estado in ORDER_UPDATE_PATTERNS:
        if patron.search(subject or ""):
            return estado
    # Un asunto de order-update que no reconocemos: mejor 'unknown', que no
    # toca nada, que inventarse una entrega.
    return "unknown"

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

# Las miniaturas de los emails vienen recortadas a ~90px (p.ej. "._SS90_.jpg").
# El CDN de Amazon sirve cualquier tamaño con solo cambiar ese sufijo, así que
# pedimos una versión más nítida para el panel (donde se muestra hasta 72px,
# y más aún en pantallas de alta densidad).
IMAGE_SIZE_SUFFIX_RE = re.compile(r"\._[A-Z]{2,3}\d+(?:,\d+)?_\.")
IMAGE_UPSCALE_SUFFIX = "._SL320_."


def _upscale_image_url(url: str) -> str:
    return IMAGE_SIZE_SUFFIX_RE.sub(IMAGE_UPSCALE_SUFFIX, url, count=1)


def extract_image_url(html_body: str = "") -> str | None:
    """Extrae la URL de la imagen del producto del HTML del email, si está presente."""
    if not html_body:
        return None
    m = PRODUCT_IMAGE_RE.search(html_body)
    if m:
        return _upscale_image_url(m.group(1))
    m = PRODUCT_IMAGE_RE_ALT.search(html_body)
    if m:
        return _upscale_image_url(m.group(1))
    return None


# Fecha estimada de entrega. Amazon la escribe en el cuerpo en texto plano, con
# varias formas seg\u00fan el env\u00edo sea un rango o un d\u00eda concreto:
#   "Llegada entre el 6 de julio y el 7 de julio"
#   "Llegada el martes, 8 de julio"
#   "Llegada hoy" / "Llegada ma\u00f1ana"
# Se guarda como texto tal cual. Normalizarlo a fecha exigir\u00eda adivinar el a\u00f1o
# (Amazon no lo pone) y no aporta nada para ense\u00f1arlo en el panel.
ETA_PATTERNS = [
    re.compile(r"Llegada\s+(entre\s+el\s+.{3,40}?\s+y\s+el\s+[^\n]{3,40})", re.IGNORECASE),
    re.compile(r"Llegada\s+(el\s+[^\n]{3,50})", re.IGNORECASE),
    re.compile(r"Llegada\s+(hoy|ma[n\u00f1]ana)\b", re.IGNORECASE),
]


def extract_eta(body_text: str = "") -> str | None:
    """Devuelve la fecha estimada de entrega como texto libre, o None."""
    if not body_text:
        return None
    for patron in ETA_PATTERNS:
        m = patron.search(body_text)
        if m:
            return " ".join(m.group(1).split()).strip(" .,")
    return None


QUOTE_CHARS = ' "\u201c\u201d\u2018\u2019'


# Prefijos de estado que Amazon pone delante del nombre del producto. Los
# cuatro últimos vienen de order-update y faltaban: sin ellos, el título de un
# pedido cancelado acababa siendo literalmente "Productos cancelados
# correctamente: 2 “TP-Link RE330...”".
PREFIJOS_ASUNTO_RE = re.compile(
    r"^\s*(?:"
    r"Pedido|Enviado|En\s+reparto|Entregado"
    r"|Productos?\s+cancelad[oa]s?\s+correctamente"
    r"|El\s+producto\s+se\s+ha\s+cancelado\s+correctamente"
    r"|Intento\s+de\s+entrega\s+realizado"
    r"|Actualizaci[oó]n\s+de\s+entrega"
    r")\s*:?\s*",
    re.IGNORECASE,
)

# Un recuento delante de las comillas ("2 “TP-Link...”") es cuántos productos
# lleva el pedido, no parte del nombre. Exige que le siga una comilla, para no
# comerse nombres que empiezan por número ("2 piezas de decoración...").
CONTEO_INICIAL_RE = re.compile(r'^\s*\d+\s+(?=["“‘«])')


def clean_title(subject: str) -> str:
    """Quita el prefijo de estado del asunto para quedarnos con el nombre del producto."""
    subject = PREFIJOS_ASUNTO_RE.sub("", subject)
    subject = re.sub(r"\d+\s*productos?\s*\|.*$", "", subject, flags=re.IGNORECASE)
    subject = CONTEO_INICIAL_RE.sub("", subject)
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

    remitente_conocido = False
    status = None
    for key, val in SENDER_STATUS_MAP.items():
        if key in sender_l:
            remitente_conocido = True
            status = val
            break

    if not remitente_conocido:
        return None

    # order-update manda varias cosas (entregas, cancelaciones, intentos
    # fallidos, cambios de fecha), así que su estado sale del asunto.
    if status is None:
        status = status_desde_asunto_order_update(subject)

    order_id = extract_order_id(subject, body_text)
    shipment_id = extract_shipment_id(body_text)
    image_url = extract_image_url(html_body)
    title = clean_title(subject)
    eta = extract_eta(body_text)

    return {
        "source": "amazon",
        "eta": eta,
        "order_id": order_id,
        "package_id": shipment_id,  # None si no se pudo extraer; sync.py usará fallback
        "status": status,
        "status_label_raw": subject,
        "title": title,
        "image_url": image_url,
        "message_id": message_id,
        "event_date": event_date,
    }
