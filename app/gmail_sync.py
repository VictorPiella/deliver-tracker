"""
gmail_sync.py — conecta Gmail con el pipeline de parsers + sync.

Diseño:
- Construye una query de Gmail que cubre los remitentes conocidos de Amazon y AliExpress.
- En el PRIMER escaneo (cuando la base está vacía), limita a los últimos N días
  (FIRST_SCAN_DAYS) para no importar años de historial de golpe.
- En escaneos posteriores, no hace falta limitar por fecha: la deduplicación por
  gmail_message_id en sync.ingest_event ya evita reprocesar lo visto, así que
  basta con volver a pedir un rango razonablemente amplio (LOOKBACK_DAYS) para
  capturar ediciones tardías sin reprocesar todo el histórico cada vez.
- Pensado para ejecutarse periódicamente (ver SYNC_INTERVAL_MINUTES) o bajo demanda
  desde el botón "Escanear ahora" del panel.

Este módulo asume que existe una integración con Gmail expuesta como funciones
`gmail_search_threads(query, page_size, page_token)` y `gmail_get_thread(thread_id)`
inyectadas por quien lo ejecute (en Claude, son las tools Gmail:search_threads /
Gmail:get_thread; en producción dentro del container, serán llamadas a la API de
Gmail vía OAuth — ver NOTA_PRODUCCION abajo).
"""
from datetime import datetime, timedelta
from .models import get_session, Package
from .sync import ingest_event
from .parsers import amazon, aliexpress

# --- Configuración ---
FIRST_SCAN_DAYS = 10       # cuántos días hacia atrás mirar en el primerísimo escaneo
LOOKBACK_DAYS = 14         # ventana de búsqueda en escaneos normales (margen de sobra
                            # sobre el intervalo real, así no se pierde nada si el
                            # worker estuvo parado un tiempo)
SYNC_INTERVAL_MINUTES = 60 # frecuencia del worker en background

AMAZON_SENDERS = [
    "auto-confirm@amazon.es",
    "confirmar-envio@amazon.es",
    "shipment-tracking@amazon.es",
    "order-update@amazon.es",
    # devolucion@amazon.es NO se incluye: el parser la ignora, y no aporta nada
    # tracking pedidos entrantes, así que ni la buscamos.
]
ALIEXPRESS_SENDER = "transaction@notice.aliexpress.com"


def build_search_query(newer_than_days: int) -> str:
    """
    Construye una query de Gmail que cubre todos los remitentes relevantes,
    acotada a los últimos N días.
    """
    amazon_clause = " OR ".join(f"from:{s}" for s in AMAZON_SENDERS)
    query = f"({amazon_clause}) OR from:{ALIEXPRESS_SENDER}"
    query += f" newer_than:{newer_than_days}d"
    return query


def is_first_scan(db_path: str) -> bool:
    session = get_session(db_path)
    count = session.query(Package).count()
    session.close()
    return count == 0


def parse_message(sender: str, subject: str, plaintext_body: str, html_body: str, message_id: str, date_str: str):
    """
    Aplica el parser correspondiente según el remitente. Devuelve el dict
    normalizado o None si el mensaje no aplica a ningún parser conocido.
    """
    try:
        event_date = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        event_date = datetime.utcnow()

    if amazon.matches(sender):
        return amazon.parse(sender, subject, plaintext_body or "", message_id, event_date, html_body=html_body or "")

    if aliexpress.matches(sender):
        return aliexpress.parse(sender, subject, plaintext_body or "", message_id, event_date, html_body=html_body or "")

    return None


def run_sync(db_path: str, gmail_search_fn, gmail_get_thread_fn, log=print) -> dict:
    """
    Ejecuta un ciclo completo de sincronización.

    gmail_search_fn(query: str) -> list[dict] con al menos {id, subject, sender, date}
        por cada mensaje encontrado (formato simplificado; ver adaptador en
        NOTA_PRODUCCION para mapear desde la respuesta real de Gmail:search_threads).
    gmail_get_thread_fn(thread_id: str) -> dict con {plaintext_body, html_body}
        para el mensaje, usado solo si el parser necesita el cuerpo (Amazon
        siempre; AliExpress solo para la imagen).

    Devuelve un resumen: {'scanned': N, 'ingested': N, 'skipped': N}
    """
    first_scan = is_first_scan(db_path)
    days = FIRST_SCAN_DAYS if first_scan else LOOKBACK_DAYS
    query = build_search_query(days)

    log(f"[gmail_sync] {'Primer escaneo' if first_scan else 'Escaneo periódico'}, "
        f"ventana={days}d, query={query!r}")

    session = get_session(db_path)
    scanned = 0
    ingested = 0
    skipped = 0

    messages = gmail_search_fn(query)

    for msg in messages:
        scanned += 1
        sender = msg.get("sender", "")
        subject = msg.get("subject", "")
        message_id = msg.get("id", "")
        date_str = msg.get("date", "")

        # Solo pedimos el cuerpo completo (otra llamada a la API) si el mensaje
        # es de un remitente que efectivamente vamos a parsear; evita llamadas
        # de más a get_thread para mensajes irrelevantes que se cuelan en la query.
        if not (amazon.matches(sender) or aliexpress.matches(sender)):
            skipped += 1
            continue

        body = gmail_get_thread_fn(message_id)
        plaintext_body = body.get("plaintext_body", "") if body else ""
        html_body = body.get("html_body", "") if body else ""

        parsed = parse_message(sender, subject, plaintext_body, html_body, message_id, date_str)
        if parsed is None:
            skipped += 1
            continue

        # AliExpress en formato 'merge' devuelve una lista (varios paquetes en un email);
        # el resto de casos devuelven un único dict. Normalizamos a lista para procesar igual.
        events_to_ingest = parsed if isinstance(parsed, list) else [parsed]

        for event in events_to_ingest:
            created = ingest_event(session, event)
            if created:
                ingested += 1
                log(f"[gmail_sync] + {event['source']} {event['status']:<16} {subject[:50]}")
            else:
                skipped += 1

    session.close()
    summary = {"scanned": scanned, "ingested": ingested, "skipped": skipped}
    log(f"[gmail_sync] Resumen: {summary}")
    return summary


# --- NOTA_PRODUCCION ---
# Dentro del container Docker en Unraid, gmail_search_fn y gmail_get_thread_fn
# deben implementarse contra la API real de Gmail (google-api-python-client)
# usando credenciales OAuth guardadas en un volumen persistente. El adaptador
# necesario es:
#
#   def gmail_search_fn(query: str) -> list[dict]:
#       results = gmail_service.users().messages().list(userId='me', q=query).execute()
#       return [
#           {
#               'id': m['id'],
#               'subject': <leer de payload.headers>,
#               'sender': <leer de payload.headers>,
#               'date': <leer de payload.headers, formato RFC2822 -> parsear a ISO>,
#           }
#           for m in results.get('messages', [])
#       ]
#
#   def gmail_get_thread_fn(message_id: str) -> dict:
#       msg = gmail_service.users().messages().get(userId='me', id=message_id, format='full').execute()
#       return {
#           'plaintext_body': <decodificar parte text/plain de payload>,
#           'html_body': <decodificar parte text/html de payload>,
#       }
#
# La autenticación OAuth (token.json) debe vivir en el volumen /data junto a
# packages.db para sobrevivir reinicios del container.
