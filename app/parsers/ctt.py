"""
Parser de los emails de CTT Express.

CTT es transportista de última milla, no tienda: reparte paquetes que ya
seguimos por otro lado. Y a diferencia de Correos, sus emails SÍ traen la
referencia de la tienda ("Referencia de envío: AP00827559353141", que es de
AliExpress), así que casi siempre se pueden colgar del paquete que ya existe
en vez de crear una fila nueva — igual que GLS.

Cuando esa referencia no sirve para encontrar nada, el envío se queda como
entrada propia en vez de tirarse. Pasa, por ejemplo, con un paquete de Shopify
cuya última milla hace CTT: el email de CTT lleva una referencia interna que no
aparece en ningún email de la tienda, así que no hay forma de enlazarlos
automáticamente. Mejor una fila de más, con su estado de verdad, que un envío
invisible.

EL ASUNTO NO DICE NADA
Todos los emails se llaman igual: "Información envío - 0082800...". El estado
está siempre dentro del cuerpo, así que aquí no hay atajo por asunto.

PRIVACIDAD
Estos emails llevan el nombre y la dirección de casa. De aquí no sale nada de
eso: sólo número, referencia, estado y fecha prevista. Si alguna vez se amplía
este parser, que siga siendo así.
"""
import html as html_mod
import re

SENDER_DOMAIN = "cttexpress"

# "Información envío - 0082800045229702119525". El número son 20-24 dígitos.
NUMERO_RE = re.compile(r"\b(\d{20,24})\b")

# "Referencia de envío: AP00827559353141" — la referencia de la tienda, que es
# lo que permite enlazar con el paquete que ya seguimos.
REFERENCIA_RE = re.compile(r"Referencia\s+de\s+env[ií]o:?\s*([A-Za-z0-9-]{6,40})", re.IGNORECASE)

# "Entrega prevista: 08/05/2026"
ETA_RE = re.compile(r"Entrega\s+prevista:?\s*(\d{2}/\d{2}/\d{4})", re.IGNORECASE)

# Las frases reales de sus emails, de más avanzada a menos. El orden importa:
# "ha sido entregado" tiene que mirarse antes que nada.
ESTADOS = [
    (re.compile(r"ha sido entregado|se ha entregado", re.IGNORECASE), "delivered"),
    (re.compile(r"no (?:se )?ha(?:mos)? podido entregar|destinatario ausente"
                r"|no se encontraba nadie", re.IGNORECASE), "delivery_attempted"),
    (re.compile(r"est[aá] en reparto", re.IGNORECASE), "out_for_delivery"),
    (re.compile(r"tiene prevista (?:su |la )?entrega", re.IGNORECASE), "at_distribution"),
    (re.compile(r"est[aá] en camino", re.IGNORECASE), "shipped"),
]


def matches(sender: str) -> bool:
    return SENDER_DOMAIN in (sender or "").lower()


def _a_texto(body_text: str, html_body: str) -> str:
    """
    El texto del email. Muchos de estos llegan sólo en HTML, así que si no hay
    versión en texto se desnuda el HTML a mano.
    """
    texto = body_text or ""
    if not texto.strip() and html_body:
        sin_bloques = re.sub(r"(?is)<(script|style).*?</\1>", " ", html_body)
        texto = re.sub(r"<[^>]+>", " ", sin_bloques)
    return re.sub(r"\s+", " ", html_mod.unescape(texto)).strip()


def detect_status(texto: str) -> str:
    """
    El estado, según lo que dice el propio email.

    Ojo con la palabra "incidencia": aparece en la cabecera de algunos de estos
    correos ("CTT Incidencia en la entrega") mientras el cuerpo dice tan
    tranquilo que el envío está en reparto, y de hecho ese mismo día se entrega.
    Es un titular de plantilla, no un intento fallido, así que no cuenta como
    tal. Para marcar una entrega fallida hace falta que el texto lo diga.
    """
    for patron, estado in ESTADOS:
        if patron.search(texto):
            return estado
    return "unknown"


def parse(sender: str, subject: str, body_text: str, message_id: str, event_date,
          html_body: str = ""):
    """
    Devuelve {tracking_number, package_id, status, eta, ...} o None.

    'package_id' es la referencia de la tienda cuando viene, que es por donde
    sync.ingest_carrier_event busca el paquete ya existente.
    """
    if not matches(sender):
        return None

    texto = _a_texto(body_text, html_body)
    estado = detect_status(texto)
    if estado == "unknown":
        return None

    m = NUMERO_RE.search(subject or "") or NUMERO_RE.search(texto)
    numero = m.group(1) if m else None
    if not numero:
        return None

    referencia = REFERENCIA_RE.search(texto)
    referencia = referencia.group(1) if referencia else None
    # La referencia a veces es el propio número de CTT; eso no enlaza con nada.
    if referencia == numero:
        referencia = None

    eta = ETA_RE.search(texto)

    return {
        "package_id": referencia,
        "tracking_number": numero,
        "status": estado,
        "status_label_raw": subject,
        "eta": eta.group(1) if eta else None,
        "message_id": message_id,
        "event_date": event_date,
    }


def como_entrada_propia(parsed: dict) -> dict:
    """
    El envío como fila independiente, cuando no se ha podido colgar de ningún
    paquete conocido. Mismo formato que devuelven los parsers de tienda.

    Sin título: estos emails no dicen qué llevan dentro, y poner "CTT Express"
    de nombre sería ruido. El panel ya se encarga de enseñar el número cuando
    no hay nombre, y tienes el lápiz para ponerle el que quieras.
    """
    referencia = parsed.get("tracking_number")
    return {
        "source": "ctt",
        "order_id": referencia,
        "package_id": referencia,
        "status": parsed["status"],
        "status_label_raw": parsed["status_label_raw"],
        "title": None,
        "image_url": None,
        "courier": "ctt",
        "courier_tracking_number": parsed.get("tracking_number"),
        "eta": parsed.get("eta"),
        "message_id": parsed["message_id"],
        "event_date": parsed["event_date"],
    }
