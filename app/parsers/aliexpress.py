"""
Parser de emails de AliExpress.

Remitente único: transaction@notice.aliexpress.com
Dos tipos de asunto observados:

  "Pedido 3074309624382839: pedido enviado"             -> evento a nivel ORDER
  "Paquete 315193141453520012: en aduanas"               -> evento a nivel PACKAGE
  "El paquete 315193141453520012 ha pasado la aduana"    -> variante con "El paquete X ha/está..."
  "Paquete 315193140438420019 entregado"                 -> variante sin ":"
  "Actualización del paquete AP00824363068180"           -> variante con prefijo AP, sin estado explícito en asunto

El cuerpo del email SÍ trae el order_id interno (parámetro o_id en los links) pero
no es fiable extraerlo del link por lo cambiante del formato; de momento order_id real
de AliExpress solo lo sacamos del email "Pedido X: pedido enviado". El resto de eventos
quedan asociados solo por package_id hasta que tengamos confirmación visual de más casos.
"""
import re

SENDER = "transaction@notice.aliexpress.com"

# Frases de texto libre -> estado normalizado. Iteramos en orden: la primera que matchea gana.
#
# En castellano Y en inglés. AliExpress manda las dos versiones a la misma
# cuenta — siete emails seguidos de un mismo paquete llegaron en inglés y
# ninguno se entendió, así que ese envío no existía para el panel. Salieron en
# la pantalla de "sin reconocer", que para eso está.
#
# Ojo con el orden: "has cleared customs" tiene que ir ANTES que "customs", si
# no un paquete que ya ha pasado la aduana se quedaría en "en aduanas".
STATUS_PATTERNS = [
    (re.compile(r"pedido enviado|order shipped", re.IGNORECASE), "shipped"),
    (re.compile(r"ha pasado la aduana|aduana superada"
                r"|(?:has |have )?cleared customs|customs clear", re.IGNORECASE), "customs_cleared"),
    (re.compile(r"en aduanas?|at customs|in customs", re.IGNORECASE), "customs"),
    (re.compile(r"sali[oó] de la regi[oó]n de origen"
                r"|left the (?:departure|origin)", re.IGNORECASE), "left_origin"),
    (re.compile(r"con transportista local|with (?:the )?local carrier"
                r"|collected by the carrier", re.IGNORECASE), "local_carrier"),
    (re.compile(r"en tu pa[ií]s/regi[oó]n|en tu pa[ií]s"
                r"|in your country(?:/region)?", re.IGNORECASE), "in_country"),
    (re.compile(r"centro de distribuci[oó]n|distribution cent(?:er|re)", re.IGNORECASE), "at_distribution"),
    (re.compile(r"entregado|delivered", re.IGNORECASE), "delivered"),
    # Sin estado claro en el asunto; se deja unknown para que no pise a nada.
    (re.compile(r"actualizaci[oó]n del paquete|has an update", re.IGNORECASE), "unknown"),
]

ORDER_SUBJECT_RE = re.compile(r"(?:Pedido|Order)\s+(\d+)\s*:", re.IGNORECASE)
# El id de paquete NO es siempre numérico: junto a los de toda la vida
# (315193141453520012) y los "AP...", llegan otros como
# PHBW6T9812926090108552J. Con "Paquete\s+(\d+)" esos no se cogían.
#
# Se exige mayúsculas y que lleve algún dígito a propósito: con [A-Za-z0-9] y
# IGNORECASE, un "Package delivered" dejaba "delivered" de id.
PACKAGE_SUBJECT_RE = re.compile(r"(?i:Paquete|Package)\s+((?=[A-Z0-9]*\d)[A-Z0-9]{8,})")
AP_PACKAGE_RE = re.compile(r"(?i:paquete|package)\s+(AP\d+)")

# La imagen del producto va en un <td class="...productImage"><img src="...">
# servida desde ae-pic-a1.aliexpress-media.com o ae01.alicdn.com
PRODUCT_IMAGE_RE = re.compile(
    r'productImage[^>]*>\s*<img\s+src="([^"]+)"',
    re.IGNORECASE
)


def extract_image_url(html_body: str = "") -> str | None:
    """Extrae la URL de la primera imagen de producto del HTML del email, si está presente."""
    if not html_body:
        return None
    m = PRODUCT_IMAGE_RE.search(html_body)
    return m.group(1) if m else None


# Formato "detalle de pedido" (usado en "pendiente de confirmación", "¿cómo ha
# ido?" y algún email de seguimiento): a diferencia del resto de emails
# individuales, este SÍ trae nombre + imagen del producto, en un bloque
# EDM-ORDER-LOGISTICS-product-name distinto del formato 'merge'.
ORDER_DETAIL_IMAGE_RE = re.compile(
    r'<img\s+src="(https://ae-pic-a1\.aliexpress-media\.com/kf/[^"]+)"\s+style="border-radius',
    re.IGNORECASE
)
ORDER_DETAIL_TITLE_RE = re.compile(
    r'class="EDM-ORDER-LOGISTICS-product-name"[^>]*>\s*<div>\s*<span>([^<]+)</span>',
    re.IGNORECASE | re.DOTALL
)


def extract_order_detail_product(html_body: str = "") -> tuple:
    """Extrae (título, imagen) del bloque de detalle de pedido, si está presente."""
    if not html_body:
        return None, None
    title_match = ORDER_DETAIL_TITLE_RE.search(html_body)
    image_match = ORDER_DETAIL_IMAGE_RE.search(html_body)
    title = title_match.group(1).strip() if title_match else None
    image_url = image_match.group(1) if image_match else None
    return title, image_url


# --- Formato "merge": "Tus N paquetes tienen actualizaciones de entrega" ---
# Este formato agrupa varios paquetes en un único email, y a diferencia de los
# individuales SÍ trae el trackingNumber real del courier (Cainiao/4PX/etc) y
# el nombre + imagen del producto. Cada paquete viene en su propio bloque
# EDM-MULTIPLE-PACKAGES-product-card; lo parseamos por separado y devolvemos
# una LISTA de eventos en vez de uno solo (ver parse_merge()).
MERGE_SUBJECT_RE = re.compile(r"\d+\s+paquetes?\s+tienen?\s+actualizaciones", re.IGNORECASE)

# Nombre del producto: primer <div class="...item-title"...>NOMBRE</div> tras cada bloque
ITEM_TITLE_RE = re.compile(r'item-title"[^>]*>\s*([^<]+?)\s*</div>', re.IGNORECASE)
ITEM_IMAGE_BG_RE = re.compile(r'item-image"\s+style="background:\s*url\(([^)]+)\)', re.IGNORECASE)
# El HTML real de AliExpress pone background="URL" en el <td>, ANTES del atributo class
ITEM_IMAGE_ATTR_RE = re.compile(r'background="([^"]+)"[^>]*item-image', re.IGNORECASE)


def is_merge_format(subject: str) -> bool:
    return bool(MERGE_SUBJECT_RE.search(subject))


# Los textos libres del formato merge no coinciden 1:1 con los asuntos
# individuales, así que tienen su propio mapeo (mismos estados normalizados).
MERGE_STATUS_PATTERNS = [
    (re.compile(r"tr[aá]nsito global", re.IGNORECASE), "left_origin"),
    (re.compile(r"sali[oó]\b|salido", re.IGNORECASE), "left_origin"),
    (re.compile(r"en aduanas?", re.IGNORECASE), "customs"),
    (re.compile(r"aduana", re.IGNORECASE), "customs_cleared"),
    (re.compile(r"transportista local", re.IGNORECASE), "local_carrier"),
    (re.compile(r"en tu pa[ií]s", re.IGNORECASE), "in_country"),
    (re.compile(r"distribuci[oó]n", re.IGNORECASE), "at_distribution"),
    (re.compile(r"entregado", re.IGNORECASE), "delivered"),
]


def detect_merge_status(status_text: str | None) -> str:
    if not status_text:
        return "unknown"
    for pattern, status in MERGE_STATUS_PATTERNS:
        if pattern.search(status_text):
            return status
    return "unknown"


def extract_merge_packages(html_body: str) -> list[dict]:
    """
    Extrae los paquetes individuales de un email 'merge'. Cada bloque trae:
    order_id, courier_tracking_number, status_text (texto libre tipo "En
    tránsito global"), title, image_url. No siempre vienen todos los campos
    si el HTML cambia ligeramente entre variantes de la plantilla.
    """
    if not html_body:
        return []

    packages = []
    # Cada bloque empieza en un href con tradeOrderId=...&trackingNumber=...
    # Partimos el HTML en esos puntos para procesar bloque a bloque, más
    # tolerante a variaciones de la plantilla que un único regex monolítico.
    block_starts = list(re.finditer(r'tradeOrderId=(\d+)&trackingNumber=([A-Za-z0-9]+)', html_body))
    for i, m in enumerate(block_starts):
        order_id, tracking_number = m.group(1), m.group(2)
        start = m.end()
        end = block_starts[i + 1].start() if i + 1 < len(block_starts) else len(html_body)
        block = html_body[start:end]

        status_match = re.search(r'item-header[^>]*>.*?<span[^>]*>([^<]+)</span>', block, re.IGNORECASE | re.DOTALL)
        status_text = status_match.group(1).strip() if status_match else None

        title_match = ITEM_TITLE_RE.search(block)
        title = title_match.group(1).strip() if title_match else None

        image_match = ITEM_IMAGE_ATTR_RE.search(block) or ITEM_IMAGE_BG_RE.search(block)
        image_url = image_match.group(1) if image_match else None

        packages.append({
            "order_id": order_id,
            "courier_tracking_number": tracking_number,
            "status_text": status_text,
            "title": title,
            "image_url": image_url,
        })

    return packages


def matches(sender: str) -> bool:
    return SENDER in sender.lower()


def detect_status(subject: str) -> str:
    for pattern, status in STATUS_PATTERNS:
        if pattern.search(subject):
            return status
    return "unknown"


def parse(sender: str, subject: str, body_text: str, message_id: str, event_date, html_body: str = ""):
    """
    Devuelve:
    - Un dict normalizado para el formato individual (un paquete por email).
    - Una LISTA de dicts para el formato 'merge' (varios paquetes por email),
      uno por cada paquete agrupado en ese email. El llamador (gmail_sync.py)
      debe aceptar ambos casos y procesar cada dict de la lista igual que uno
      individual.
    - None si el remitente no aplica.

    Dict normalizado:
    {
        'source': 'aliexpress',
        'order_id': '3074309624382839' | None,
        'package_id': '315193141453520012' | 'AP00824363068180' | None,
        'status': 'in_country',
        'status_label_raw': subject,
        'title': 'MC-38 Sensor...' | None,  # solo si el email trae el bloque de detalle de pedido (ver extract_order_detail_product)
        'image_url': 'https://ae-pic-a1.aliexpress-media.com/...' | None,
        'courier_tracking_number': 'LP00827165784034' | None,  # solo disponible en formato merge
        'message_id': ...,
        'event_date': ...,
    }
    """
    if not matches(sender):
        return None

    if is_merge_format(subject):
        merge_packages = extract_merge_packages(html_body)
        if not merge_packages:
            # No pudimos extraer nada del HTML (plantilla cambiada); mejor no
            # devolver un evento vacío que ensucie la base con datos a ciegas.
            return None
        events = []
        for i, pkg in enumerate(merge_packages):
            events.append({
                "source": "aliexpress",
                "order_id": pkg["order_id"],
                # Usamos el tracking_number real del courier como package_id:
                # es más estable y útil que un id provisional, y nos permite
                # mostrar directamente el tracking number real en el panel.
                "package_id": pkg["courier_tracking_number"] or f"order-{pkg['order_id']}",
                "status": detect_merge_status(pkg["status_text"]),
                "status_label_raw": pkg["status_text"] or subject,
                "title": pkg["title"],
                "image_url": pkg["image_url"],
                "courier_tracking_number": pkg["courier_tracking_number"],
                # message_id debe ser único por paquete dentro del mismo email,
                # si no la deduplicación de sync.py descartaría todos menos el primero.
                "message_id": f"{message_id}-pkg{i}",
                "event_date": event_date,
            })
        return events

    status = detect_status(subject)

    order_match = ORDER_SUBJECT_RE.search(subject)
    package_match = PACKAGE_SUBJECT_RE.search(subject)
    ap_match = AP_PACKAGE_RE.search(subject)

    order_id = order_match.group(1) if order_match else None
    package_id = package_match.group(1) if package_match else (ap_match.group(1) if ap_match else None)

    image_url = extract_image_url(html_body)
    detail_title, detail_image_url = extract_order_detail_product(html_body)
    if image_url is None:
        image_url = detail_image_url

    # Si el asunto es "Pedido X: pedido enviado", no hay package_id propio todavía;
    # en ese caso usamos el order_id también como package_id provisional, y cuando
    # llegue el primer evento "Paquete Y: ..." para ese pedido haremos el merge si
    # el cuerpo del email lo permite confirmar (ver TODO en gmail_sync.py).
    if package_id is None and order_id is not None:
        package_id = f"order-{order_id}"

    return {
        "source": "aliexpress",
        "order_id": order_id,
        "package_id": package_id,
        "status": status,
        "status_label_raw": subject,
        "title": detail_title,
        "image_url": image_url,
        "courier_tracking_number": None,
        "message_id": message_id,
        "event_date": event_date,
    }
