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


def imagenes_por_pedido(html_body: str = "") -> dict:
    """
    Empareja cada nº de pedido con SU imagen, para los emails que traen varios.

    En el HTML, cada pedido va seguido de la imagen de su producto, así que a
    cada uno le toca la primera imagen que aparece por detrás. Se empareja por
    posición y no por orden de aparición en dos listas sueltas, para que un
    pedido sin imagen no desplace las de los siguientes.

    Ojo con qué cuenta como imagen: el email trae además banners de productos
    recomendados (`_SR276,276_`). La clase `productImage` los deja fuera, que
    es justo para lo que sirve.
    """
    if not html_body:
        return {}

    imagenes = []
    for patron, grupo in ((PRODUCT_IMAGE_RE, 1), (PRODUCT_IMAGE_RE_ALT, 1)):
        for m in patron.finditer(html_body):
            imagenes.append((m.start(), _upscale_image_url(m.group(grupo))))
    imagenes.sort()

    resultado = {}
    for m in ORDER_ID_RE.finditer(html_body):
        order_id = m.group(1)
        if order_id in resultado:
            continue   # el nº se repite en los enlaces; vale la primera vez
        resultado[order_id] = next((u for pos, u in imagenes if pos > m.start()), None)
    return resultado


# Fecha estimada de entrega. Amazon la escribe en el cuerpo en texto plano, con
# varias formas seg\u00fan el env\u00edo sea un rango o un d\u00eda concreto:
#   "Llegada entre el 6 de julio y el 7 de julio"
#   "Llegada el martes, 8 de julio"
#   "Llegada hoy" / "Llegada ma\u00f1ana"
# Se guarda como texto tal cual. Normalizarlo a fecha exigir\u00eda adivinar el a\u00f1o
# (Amazon no lo pone) y no aporta nada para ense\u00f1arlo en el panel.
# "Llegada" en unos emails y "Llega" en otros (el de confirmaci\u00f3n de pedido usa
# "Llega ma\u00f1ana", "Llega el jueves"). Con s\u00f3lo "Llegada" se perd\u00edan esos.
ETA_PATTERNS = [
    re.compile(r"Llegad?a?\s+(entre\s+el\s+.{3,40}?\s+y\s+el\s+[^\n]{3,40})", re.IGNORECASE),
    re.compile(r"Llegad?a?\s+(el\s+[^\n]{3,50})", re.IGNORECASE),
    re.compile(r"Llegad?a?\s+(hoy|ma[n\u00f1]ana)\b", re.IGNORECASE),
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


# Un mismo email de confirmaci\u00f3n puede cubrir VARIOS pedidos. Amazon parte la
# compra cuando los art\u00edculos salen de sitios distintos, y manda un solo correo
# con un bloque por pedido:
#
#     Llega ma\u00f1ana                     <- fecha estimada del pedido 1
#     Pedido n.\u00ba
#     408-0917824-6197118
#     * Adaptador de Cargador...       <- producto del pedido 1
#     Total 11.09 EUR
#     Llega el jueves                  <- y aqu\u00ed empieza el pedido 2
#     Pedido n.\u00ba
#     408-2438266-2656303
#     * JZ Type-C 5V/2000mA...
#
# Como extract_order_id devuelve s\u00f3lo la PRIMERA coincidencia, el segundo pedido
# desaparec\u00eda sin dejar rastro. Ojo con el orden dentro del bloque: la fecha va
# ANTES del n\u00ba de pedido y el producto DESPU\u00c9S, as\u00ed que se busca cada una hacia
# su lado.
PRODUCTO_RE = re.compile(r"^\s*\*\s+(.{5,200}?)\s*$", re.MULTILINE)


def extraer_pedidos_del_cuerpo(body_text: str) -> list[dict]:
    """
    Devuelve un bloque por pedido: {order_id, title, eta}. Lista vac\u00eda si el
    cuerpo no trae ninguno anclado a la etiqueta "Pedido n.\u00ba".
    """
    if not body_text:
        return []

    marcas = list(ORDER_ID_LABELED_RE.finditer(body_text))
    if not marcas:
        return []

    bloques = []
    vistos = set()
    for i, m in enumerate(marcas):
        order_id = m.group(1)
        if order_id in vistos:
            continue
        vistos.add(order_id)

        desde = marcas[i - 1].end() if i else 0
        hasta = marcas[i + 1].start() if i + 1 < len(marcas) else len(body_text)

        # La fecha queda por detr\u00e1s del n\u00ba de pedido: se coge la \u00faltima que
        # aparezca entre el pedido anterior y \u00e9ste.
        etas = ETA_PATTERNS[0].findall(body_text[desde:m.start()]) \
            or ETA_PATTERNS[1].findall(body_text[desde:m.start()]) \
            or ETA_PATTERNS[2].findall(body_text[desde:m.start()])
        eta = " ".join(etas[-1].split()).strip(" .,") if etas else None

        # El producto, por delante.
        producto = PRODUCTO_RE.search(body_text[m.end():hasta])
        title = " ".join(producto.group(1).split()) if producto else None

        bloques.append({"order_id": order_id, "title": title, "eta": eta})
    return bloques


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

    image_url = extract_image_url(html_body)

    # Un email puede cubrir varios pedidos (ver extraer_pedidos_del_cuerpo). En
    # ese caso se devuelve una lista, igual que hace el formato 'merge' de
    # AliExpress; gmail_sync ya sabe tratar ambos casos.
    bloques = extraer_pedidos_del_cuerpo(body_text)
    if len(bloques) > 1:
        # Cada pedido tiene SU imagen en el HTML. Antes se cogia una sola y se
        # le daba al primero, asi que el segundo salia sin foto.
        imagenes = imagenes_por_pedido(html_body)
        eventos = []
        for i, bloque in enumerate(bloques):
            eventos.append({
                "source": "amazon",
                "order_id": bloque["order_id"],
                # Sin shipmentId todavía: el email de "Enviado" de cada pedido
                # traerá el suyo y adoptará este paquete provisional.
                "package_id": None,
                "status": status,
                "status_label_raw": subject,
                # El nombre sale del bloque, no del asunto: el asunto sólo
                # nombra un producto ("JZ Type-C..." y 1 producto más) y le
                # pondría el mismo título a los dos pedidos.
                "title": bloque["title"] or clean_title(subject),
                # Este titulo sale del bloque del propio pedido, no del asunto:
                # es el bueno y puede pisar a uno anterior. El del asunto es una
                # aproximacion, y en un email con varios pedidos nombra solo a
                # uno, asi que a los demas les pondria el nombre equivocado.
                "title_preciso": bool(bloque["title"]),
                "image_url": imagenes.get(bloque["order_id"]) or (image_url if i == 0 else None),
                "eta": bloque["eta"],
                # Único por pedido: si no, la deduplicación por message_id
                # descartaría todos menos el primero.
                "message_id": f"{message_id}-ord{i}",
                "event_date": event_date,
            })
        return eventos

    order_id = extract_order_id(subject, body_text)
    shipment_id = extract_shipment_id(body_text)
    title = clean_title(subject)
    eta = extract_eta(body_text)
    title_preciso = False
    if bloques:
        # Un solo pedido: el bloque afina el nombre y la fecha.
        if bloques[0]["title"]:
            title = bloques[0]["title"]
            title_preciso = True
        eta = bloques[0]["eta"] or eta

    return {
        "title_preciso": title_preciso,
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
