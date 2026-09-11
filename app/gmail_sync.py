"""
gmail_sync.py — conecta Gmail con el pipeline de parsers + sync.

Diseño:
- Construye una query de Gmail que cubre los remitentes conocidos de Amazon y AliExpress.
- En el PRIMER escaneo limita a los últimos FIRST_SCAN_DAYS días, para no
  importar años de historial de golpe.
- En escaneos posteriores la ventana es ADAPTATIVA: se calcula desde la fecha
  del último escaneo con éxito (guardada en la base), más un margen. Antes era
  un valor fijo de 14 días, y eso dejaba un agujero real: si el container
  estaba parado más de dos semanas, los emails llegados durante el parón ya
  quedaban fuera de la ventana al volver, y no se recuperaban nunca. Ahora un
  parón de tres meses se recupera solo en el primer escaneo tras arrancar.
- Reprocesar de más no cuesta nada: la deduplicación por gmail_message_id en
  sync.ingest_event descarta lo ya visto.

IMPORTANTE sobre borrar correo: la base de datos es la fuente de verdad, no
Gmail. Un evento ya ingerido es una fila permanente; el sync sólo añade, nunca
reconcilia ni borra. Puedes borrar los emails una vez escaneados sin perder
nada del panel. Lo que sí se pierde para siempre es un email borrado ANTES de
que le diera tiempo a escanearse. Ojo además con que la API de Gmail no busca
en la papelera (includeSpamTrash es false por defecto): mover a la papelera
equivale a borrar de cara al escaneo. Archivar, en cambio, es inocuo.
- Pensado para ejecutarse periódicamente (ver SYNC_INTERVAL_MINUTES) o bajo demanda
  desde el botón "Escanear ahora" del panel.

Este módulo asume que existe una integración con Gmail expuesta como funciones
`gmail_search_threads(query, page_size, page_token)` y `gmail_get_thread(thread_id)`
inyectadas por quien lo ejecute (en Claude, son las tools Gmail:search_threads /
Gmail:get_thread; en producción dentro del container, serán llamadas a la API de
Gmail vía OAuth — ver NOTA_PRODUCCION abajo).
"""
import os
from datetime import datetime

from .models import (
    get_last_sync, get_session, set_last_sync, Package, UnparsedEmail,
)
from .timeutils import utcnow, to_utc_naive
from .sync import evento_anclable, ingest_event, ingest_carrier_event
from .parsers import amazon, aliexpress, gls, correos

# --- Configuración ---
# Todas ajustables por entorno; los valores por defecto sirven para un uso normal.

# Cuánto mirar hacia atrás en el primerísimo escaneo. 30 días cubre de sobra los
# paquetes de AliExpress que ya estén en vuelo al conectar la cuenta por primera
# vez (suelen tardar 2-5 semanas). Súbelo puntualmente para una importación
# inicial más profunda: GMAIL_FIRST_SCAN_DAYS=90.
FIRST_SCAN_DAYS = int(os.environ.get("GMAIL_FIRST_SCAN_DAYS", "30"))

# Suelo de la ventana adaptativa: aunque el último escaneo fuera hace diez
# minutos, se piden siempre al menos estos días por si algún email entró con
# fecha anterior a la de su llegada.
MIN_LOOKBACK_DAYS = int(os.environ.get("GMAIL_MIN_LOOKBACK_DAYS", "14"))

# Techo: evita que un parón larguísimo dispare una query que se traiga medio
# buzón. Un año es más que suficiente para un panel de paquetes.
MAX_LOOKBACK_DAYS = int(os.environ.get("GMAIL_MAX_LOOKBACK_DAYS", "365"))

# Margen que se añade al hueco desde el último escaneo, para cubrir desfases de
# reloj y emails que llegan con retraso.
LOOKBACK_MARGIN_DAYS = 2

SYNC_INTERVAL_MINUTES = int(os.environ.get("SYNC_INTERVAL_MINUTES", "60"))

AMAZON_SENDERS = [
    "auto-confirm@amazon.es",
    "confirmar-envio@amazon.es",
    "shipment-tracking@amazon.es",
    "order-update@amazon.es",
    # devolucion@amazon.es NO se incluye: el parser la ignora, y no aporta nada
    # tracking pedidos entrantes, así que ni la buscamos.
]
ALIEXPRESS_SENDER = "transaction@notice.aliexpress.com"
# Transportistas de última milla: GLS se enlaza a un Package ya existente
# (trae el package_id de AliExpress en el cuerpo); Correos no trae ningún ID
# compartido, así que se trackea como entrada independiente (ver parsers/correos.py).
GLS_SENDER_DOMAIN = gls.SENDER_DOMAIN
CORREOS_SENDER_DOMAIN = correos.SENDER_DOMAIN


def build_search_query(newer_than_days: int) -> str:
    """
    Construye una query de Gmail que cubre todos los remitentes relevantes,
    acotada a los últimos N días.
    """
    amazon_clause = " OR ".join(f"from:{s}" for s in AMAZON_SENDERS)
    query = (
        f"({amazon_clause}) OR from:{ALIEXPRESS_SENDER} "
        f"OR from:{GLS_SENDER_DOMAIN} OR from:{CORREOS_SENDER_DOMAIN}"
    )
    query += f" newer_than:{newer_than_days}d"
    return query


def compute_lookback_days(session) -> tuple[int, bool]:
    """
    Devuelve (días a mirar hacia atrás, es_primer_escaneo).

    Si nunca se ha sincronizado con éxito, ventana de primer escaneo. Si sí, se
    cubre todo el hueco desde entonces más un margen, acotado entre
    MIN_LOOKBACK_DAYS y MAX_LOOKBACK_DAYS. Así un container que ha estado
    semanas apagado recupera lo que se perdió en cuanto vuelve.
    """
    ultimo = get_last_sync(session)
    if ultimo is None:
        return FIRST_SCAN_DAYS, True

    hueco = (utcnow() - ultimo).total_seconds() / 86400
    dias = int(hueco) + LOOKBACK_MARGIN_DAYS
    return max(MIN_LOOKBACK_DAYS, min(dias, MAX_LOOKBACK_DAYS)), False


def parse_event_date(date_str: str) -> datetime:
    """
    Pasa la fecha del email (ISO, normalmente con offset local tipo +02:00) a
    UTC naive. Sin esta conversión el offset se perdía al guardar en SQLite y
    cada evento quedaba desplazado 1-2h — ver app/timeutils.py.
    """
    try:
        return to_utc_naive(datetime.fromisoformat(date_str.replace("Z", "+00:00")))
    except (ValueError, AttributeError):
        return utcnow()


def record_unparsed(session, message_id: str, sender: str, subject: str, event_date) -> None:
    """
    Apunta un email de un remitente conocido que ningún parser ha sabido leer.
    Es la única forma de enterarse de que Amazon o AliExpress han cambiado la
    plantilla: sin esto, parse() devuelve None, el contador de "saltados" sube
    y dejas de ver paquetes sin ninguna señal.
    """
    ya = session.query(UnparsedEmail).filter_by(gmail_message_id=message_id).first()
    if ya is not None:
        return
    session.add(UnparsedEmail(
        gmail_message_id=message_id,
        sender=sender,
        subject=subject,
        event_date=event_date,
    ))


def parse_message(sender: str, subject: str, plaintext_body: str, html_body: str, message_id: str, date_str: str):
    """
    Aplica el parser correspondiente según el remitente. Devuelve el dict
    normalizado o None si el mensaje no aplica a ningún parser conocido.
    """
    event_date = parse_event_date(date_str)

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
    session = get_session(db_path)
    days, first_scan = compute_lookback_days(session)
    query = build_search_query(days)

    log(f"[gmail_sync] {'Primer escaneo' if first_scan else 'Escaneo periódico'}, "
        f"ventana={days}d, query={query!r}")

    scanned = 0
    ingested = 0
    # 'duplicated' = ya lo teníamos (lo normal en cada pasada).
    # 'irrelevant' = remitente que no nos interesa, colado por la query.
    # 'unparsed'   = remitente que SÍ nos interesa pero no supimos leer: esto es
    #                lo que delata un cambio de plantilla y antes se perdía
    #                dentro de un único contador de "saltados".
    duplicated = 0
    irrelevant = 0
    unparsed = 0

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
        if not (amazon.matches(sender) or aliexpress.matches(sender)
                or gls.matches(sender) or correos.matches(sender)):
            irrelevant += 1
            continue

        body = gmail_get_thread_fn(message_id)
        plaintext_body = body.get("plaintext_body", "") if body else ""
        html_body = body.get("html_body", "") if body else ""

        if gls.matches(sender):
            # GLS se enlaza a un Package ya existente (Amazon/AliExpress), no
            # crea uno nuevo — ver sync.ingest_carrier_event.
            event_date = parse_event_date(date_str)
            parsed = gls.parse(sender, subject, plaintext_body or "", message_id, event_date)
            if parsed is None:
                record_unparsed(session, message_id, sender, subject, event_date)
                unparsed += 1
                continue
            created = ingest_carrier_event(session, parsed, courier="gls")
            if created:
                ingested += 1
                log(f"[gmail_sync] + gls {parsed['status']:<16} {subject[:50]}")
            else:
                duplicated += 1
            continue

        if correos.matches(sender):
            # Correos no trae ningún ID de Amazon/AliExpress: se trackea como
            # entrada independiente (source='correos'), igual que Amazon/AliExpress.
            event_date = parse_event_date(date_str)
            parsed = correos.parse(sender, subject, plaintext_body or "", message_id, event_date, html_body=html_body or "")
            if parsed is None:
                record_unparsed(session, message_id, sender, subject, event_date)
                unparsed += 1
                continue
            created = ingest_event(session, parsed)
            if created:
                ingested += 1
                log(f"[gmail_sync] + correos {parsed['status']:<16} {subject[:50]}")
            else:
                duplicated += 1
            continue

        parsed = parse_message(sender, subject, plaintext_body, html_body, message_id, date_str)
        if parsed is None:
            # Amazon ignora a propósito los emails de devolución: no son un
            # fallo de parseo, así que no ensucian el contador.
            if not amazon.should_ignore(sender):
                record_unparsed(session, message_id, sender, subject, parse_event_date(date_str))
                unparsed += 1
            else:
                irrelevant += 1
            continue

        # AliExpress en formato 'merge' devuelve una lista (varios paquetes en un email);
        # el resto de casos devuelven un único dict. Normalizamos a lista para procesar igual.
        events_to_ingest = parsed if isinstance(parsed, list) else [parsed]

        for event in events_to_ingest:
            # Un evento sin ningún identificador no se puede colgar de un
            # paquete. ingest_event lo tiraría a la basura sin decir nada, y es
            # justo la pinta que tiene una plantilla que ha cambiado.
            if not evento_anclable(event):
                record_unparsed(session, message_id, sender, subject, event["event_date"])
                unparsed += 1
                continue

            created = ingest_event(session, event)
            if created:
                ingested += 1
                log(f"[gmail_sync] + {event['source']} {event['status']:<16} {subject[:50]}")
            else:
                duplicated += 1

    # Sólo se marca el escaneo como hecho si se ha llegado hasta aquí sin
    # excepción. Si Gmail falla a mitad, el marcador no avanza y el siguiente
    # intento vuelve a cubrir la misma ventana.
    momento = set_last_sync(session)
    session.commit()
    session.close()

    summary = {
        "scanned": scanned,
        "ingested": ingested,
        "duplicated": duplicated,
        "irrelevant": irrelevant,
        "unparsed": unparsed,
        # 'skipped' se conserva como suma de los tres, por compatibilidad.
        "skipped": duplicated + irrelevant + unparsed,
        "lookback_days": days,
        "synced_at": momento,
    }
    log(f"[gmail_sync] Resumen: {summary}")
    if unparsed:
        log(f"[gmail_sync] AVISO: {unparsed} email(s) de remitentes conocidos "
            f"que no se han sabido interpretar. Puede que haya cambiado una "
            f"plantilla; míralos en /sin-reconocer")
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
