"""
Parser de la plantilla estándar de Shopify.

A diferencia del resto de parsers, aquí NO hay un remitente fijo. Shopify no
manda los emails: los manda cada tienda desde su propio dominio, con su propio
nombre. Así que no se puede preguntar "¿viene de shopify.com?" — hay que mirar
el email por dentro y reconocer la plantilla.

De ahí el doble filtro:

  posible(subject)  -> ¿el asunto tiene pinta? Es barato y decide si merece la
                       pena pedirle el cuerpo a Gmail (una llamada más a la API).
  parse(...)        -> ¿el cuerpo es de verdad una plantilla de Shopify? Exige
                       marcas estructurales suyas. Una newsletter que ponga
                       "order confirmed" en el asunto no pasa de aquí.

El asunto solo no basta, y por eso no se usa solo: "Your order is on the way"
lo escribe media internet.

IDENTIDAD DEL PEDIDO
El "#10239" es único dentro de UNA tienda, no en el mundo. Dos tiendas Shopify
pueden tener las dos un pedido #1001 perfectamente. Por eso el order_id que se
guarda lleva delante la tienda: "mitienda.com#10239". Sin eso, dos pedidos de
dos tiendas distintas acabarían fusionados en uno.
"""
import html as html_mod
import re

# --------------------------------------------------------------- el asunto

# "Order #10239 confirmed", "Pedido #10239 confirmado"
ASUNTO_CONFIRMADO_RE = re.compile(
    r"\b(?:order|pedido)\b.{0,40}?#\s*(\w+).{0,20}?\b(?:confirmed|confirmado)\b",
    re.IGNORECASE,
)
# "Order #10239 canceled" (Shopify lo escribe con una sola ele)
ASUNTO_CANCELADO_RE = re.compile(
    r"\b(?:order|pedido)\b.{0,40}?#\s*(\w+).{0,20}?\b(?:cancell?ed|cancelado)\b",
    re.IGNORECASE,
)
# "A shipment from order #10239 is on the way"
ASUNTO_ENVIADO_RE = re.compile(
    r"\b(?:shipment|env[ií]o)\b.{0,40}?#\s*(\w+)",
    re.IGNORECASE,
)

# Los de reparto y entrega NO llevan el número de pedido en el asunto
# ("Your order is out for delivery"): hay que sacarlo del cuerpo.
ASUNTOS_SIN_NUMERO = [
    (re.compile(r"out for delivery|en reparto|sale a reparto", re.IGNORECASE), "out_for_delivery"),
    (re.compile(r"(?:has been|was) delivered|ha sido entregado|se ha entregado", re.IGNORECASE), "delivered"),
    (re.compile(r"(?:order|pedido).{0,30}(?:is )?on (?:the|its) way|est[aá] en camino", re.IGNORECASE), "shipped"),
]

# Cualquier cosa que haga que valga la pena abrir el email. Deliberadamente
# generoso: filtrar de verdad es cosa de es_shopify(), que mira el cuerpo.
PISTAS_ASUNTO = [ASUNTO_CONFIRMADO_RE, ASUNTO_CANCELADO_RE, ASUNTO_ENVIADO_RE] + \
                [p for p, _ in ASUNTOS_SIN_NUMERO]


def posible(subject: str) -> bool:
    """Filtro barato, sólo con el asunto, para decidir si pedir el cuerpo."""
    return any(p.search(subject or "") for p in PISTAS_ASUNTO)


# ---------------------------------------------- reconocer de verdad el HTML

# Nombres de clase de la plantilla de notificaciones de Shopify. Son lo más
# fiable que hay: el texto lo puede cambiar cualquier tienda desde su panel,
# pero el armazón de la plantilla casi nadie lo toca.
MARCAS_SHOPIFY = [
    re.compile(r"order-list__item-title", re.IGNORECASE),
    re.compile(r"order-list__product-description-cell", re.IGNORECASE),
    re.compile(r"order-list__image-cell", re.IGNORECASE),
    re.compile(r"order-list__product-image", re.IGNORECASE),
    re.compile(r'class="[^"]*\border-number\b', re.IGNORECASE),
    re.compile(r"customer-info__item", re.IGNORECASE),
    re.compile(r"shopify\.com/", re.IGNORECASE),
    re.compile(r"cdn\.shopify\.com|shopifycdn\.(?:com|net)", re.IGNORECASE),
    re.compile(r"\bshop\.app\b", re.IGNORECASE),
]
# Con una marca sola no basta: "shopify.com" sale en cualquier email de una
# tienda Shopify, newsletters incluidas. Dos ya es la plantilla.
MARCAS_MINIMAS = 2


def es_shopify(html_body: str, body_text: str = "") -> bool:
    material = (html_body or "") + "\n" + (body_text or "")
    if not material.strip():
        return False
    return sum(1 for m in MARCAS_SHOPIFY if m.search(material)) >= MARCAS_MINIMAS


# ------------------------------------------------------------- el contenido

# '<h2 class="order-number">Order #10239</h2>', o el mismo texto suelto.
NUMERO_EN_CUERPO_RE = re.compile(r"(?:order|pedido)\s*#\s*(\w+)", re.IGNORECASE)

# Nombre del producto. Shopify lo escribe como "Producto&nbsp;&times;&nbsp;1":
# el "× 1" es la cantidad y sobra en el título.
TITULO_RE = re.compile(
    r'order-list__item-title[^>]*>\s*(.*?)\s*<', re.IGNORECASE | re.DOTALL
)
CANTIDAD_COLA_RE = re.compile(r"\s*[x×]\s*\d+\s*$", re.IGNORECASE)

# La imagen del producto. El atributo class puede ir antes o después del src
# según la versión de la plantilla, así que se prueban los dos órdenes.
IMAGEN_RES = [
    re.compile(r'order-list__product-image[^>]*\bsrc="([^"]+)"', re.IGNORECASE),
    re.compile(r'<img[^>]*\bsrc="([^"]+)"[^>]*order-list__product-image', re.IGNORECASE),
    re.compile(r'order-list__image-cell[^>]*>\s*<img[^>]*\bsrc="([^"]+)"', re.IGNORECASE),
]

# "Tracking number: 1Z999AA10123456784" / "Nº de seguimiento: ..."
SEGUIMIENTO_RE = re.compile(
    r"(?:tracking\s*(?:number|no\.?)|n[ºo°]?\.?\s*de\s*seguimiento)\s*:?\s*([A-Z0-9][A-Z0-9-]{5,})",
    re.IGNORECASE,
)


def _limpiar(texto: str) -> str:
    texto = html_mod.unescape(texto or "")
    # &nbsp; se convierte en \xa0, que no es un espacio normal y estropea los
    # recortes de después.
    texto = texto.replace("\xa0", " ")
    return re.sub(r"\s+", " ", texto).strip()


def extraer_titulo(html_body: str) -> str | None:
    m = TITULO_RE.search(html_body or "")
    if not m:
        return None
    titulo = CANTIDAD_COLA_RE.sub("", _limpiar(m.group(1)))
    return titulo or None


def extraer_imagen(html_body: str) -> str | None:
    for patron in IMAGEN_RES:
        m = patron.search(html_body or "")
        if m:
            return html_mod.unescape(m.group(1))
    return None


def nombre_de_tienda(sender: str) -> str | None:
    """
    El nombre que la tienda se pone a sí misma en el remitente:
    "Mi Tienda <pedidos@mitienda.com>" -> "Mi Tienda".
    """
    sender = (sender or "").strip()
    m = re.match(r'^\s*"?([^"<]+?)"?\s*<', sender)
    if m:
        nombre = m.group(1).strip()
        # "pedidos@mitienda.com <pedidos@mitienda.com>" no es un nombre.
        if nombre and "@" not in nombre:
            return nombre
    return None


def clave_de_tienda(sender: str) -> str:
    """
    Identificador estable de la tienda, para que el "#10239" de una no se
    confunda con el de otra. Se usa el dominio del remitente porque no cambia
    aunque la tienda se cambie el nombre.
    """
    m = re.search(r"@([\w.-]+)", sender or "")
    if m:
        dominio = m.group(1).lower().strip(".")
        # Muchas tiendas mandan desde un subdominio de envío; lo que identifica
        # a la tienda es el dominio de segundo nivel.
        partes = dominio.split(".")
        if len(partes) > 2:
            dominio = ".".join(partes[-2:])
        return dominio
    return (nombre_de_tienda(sender) or "shopify").lower()


def detectar_estado(subject: str) -> tuple[str, str | None]:
    """Devuelve (estado, número de pedido si venía en el asunto)."""
    subject = subject or ""

    m = ASUNTO_CANCELADO_RE.search(subject)
    if m:
        return "cancelled", m.group(1)

    m = ASUNTO_CONFIRMADO_RE.search(subject)
    if m:
        return "ordered", m.group(1)

    # El orden importa: "out for delivery" y "delivered" se miran antes que
    # "on the way", porque el email de reparto también puede decir "on the way".
    for patron, estado in ASUNTOS_SIN_NUMERO:
        if patron.search(subject):
            m = ASUNTO_ENVIADO_RE.search(subject)
            return estado, (m.group(1) if m else None)

    m = ASUNTO_ENVIADO_RE.search(subject)
    if m:
        return "shipped", m.group(1)

    return "unknown", None


def parse(sender: str, subject: str, body_text: str, message_id: str, event_date,
          html_body: str = ""):
    """
    Devuelve el dict normalizado, o None si esto no es una plantilla de Shopify
    que sepamos leer.

    Devolver None aquí es lo normal y no significa que se haya roto nada: el
    filtro del asunto es generoso a propósito, y muchos de los emails que llegan
    hasta aquí simplemente no son de Shopify. Por eso gmail_sync los cuenta como
    irrelevantes y no como "sin reconocer".
    """
    if not es_shopify(html_body, body_text):
        return None

    estado, numero = detectar_estado(subject)
    if estado == "unknown":
        return None

    if not numero:
        # Los emails de reparto/entrega no llevan el número en el asunto.
        m = NUMERO_EN_CUERPO_RE.search(_limpiar(body_text)) or \
            NUMERO_EN_CUERPO_RE.search(html_body or "")
        numero = m.group(1) if m else None

    if not numero:
        # Sin número no hay a qué colgar el evento: un "tu pedido va en camino"
        # sin decir cuál no sirve de nada.
        return None

    tienda = clave_de_tienda(sender)
    order_id = f"{tienda}#{numero}"

    texto = _limpiar(body_text)
    m = SEGUIMIENTO_RE.search(texto) or SEGUIMIENTO_RE.search(html_body or "")
    tracking = m.group(1) if m else None

    titulo_real = extraer_titulo(html_body)
    # Al menos que se vea de quién es: es más útil que un id a secas.
    nombre = nombre_de_tienda(sender) or tienda
    titulo = titulo_real or f"{nombre} · pedido #{numero}"

    return {
        "source": "shopify",
        "order_id": order_id,
        "package_id": order_id,
        "status": estado,
        "status_label_raw": subject,
        "title": titulo,
        # El nombre del producto sale del email de confirmación, que es el único
        # que lista lo comprado. Los de envío/entrega traen un título de relleno
        # que no debe pisar al bueno.
        "title_preciso": bool(titulo_real),
        "image_url": extraer_imagen(html_body),
        "courier_tracking_number": tracking,
        "tienda": nombre,
        "message_id": message_id,
        "event_date": event_date,
    }
