"""
gmail_oauth.py — adaptador real de Gmail vía OAuth (google-api-python-client).

Sustituye a mock_gmail.py cuando USE_MOCK_GMAIL=false (ver web.py). Implementa
el contrato descrito en gmail_sync.NOTA_PRODUCCION: gmail_search_fn(query) y
gmail_get_thread_fn(message_id).

Autenticación:
- La autorización inicial (consentimiento OAuth vía navegador) no puede hacerse
  dentro del container Docker (headless, sin navegador). Se hace una única vez
  en local con `python -m scripts.gmail_auth`, que genera token.json.
- credentials.json (descargado de Google Cloud Console) y token.json viven en
  el mismo volumen persistente que la base de datos (junto a DB_PATH) para
  sobrevivir reinicios del container.
- En cada arranque del container solo se refresca el access token usando el
  refresh_token ya guardado — eso no necesita navegador.
"""
import base64
import os
from email.utils import parsedate_to_datetime

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# Solo lectura: la app nunca necesita enviar ni modificar correo.
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

_DATA_DIR = os.path.dirname(os.environ.get("DB_PATH", "data/packages.db")) or "."
CREDENTIALS_PATH = os.environ.get("GMAIL_CREDENTIALS_PATH", os.path.join(_DATA_DIR, "credentials.json"))
TOKEN_PATH = os.environ.get("GMAIL_TOKEN_PATH", os.path.join(_DATA_DIR, "token.json"))

_service = None


def _load_credentials() -> Credentials:
    if not os.path.exists(TOKEN_PATH):
        raise RuntimeError(
            f"No se encontró {TOKEN_PATH}. Ejecuta la autorización inicial en local "
            "con 'python -m scripts.gmail_auth' y copia el token.json generado a este volumen."
        )
    creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
    return creds


def _get_service():
    global _service
    if _service is None:
        _service = build("gmail", "v1", credentials=_load_credentials(), cache_discovery=False)
    return _service


def _header(headers: list, name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _parse_date(raw: str) -> str:
    try:
        return parsedate_to_datetime(raw).isoformat()
    except (TypeError, ValueError):
        return ""


def _decode_part(data: str) -> str:
    # Gmail codifica los cuerpos en base64url; el padding puede faltar.
    padded = data.encode("ascii") + b"=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")


def _walk_parts(payload: dict, plaintext: list, html: list) -> None:
    mime_type = payload.get("mimeType", "")
    body = payload.get("body", {})
    if mime_type == "text/plain" and body.get("data"):
        plaintext.append(_decode_part(body["data"]))
    elif mime_type == "text/html" and body.get("data"):
        html.append(_decode_part(body["data"]))
    for part in payload.get("parts", []) or []:
        _walk_parts(part, plaintext, html)


# Gmail acepta como mucho 100 peticiones por lote.
BATCH_SIZE = 100


def _fetch_metadata_batch(service, ids: list) -> list:
    """
    Pide las cabeceras de varios mensajes en UNA sola petición HTTP.

    Antes se hacía un messages().get() por mensaje, en serie: para una ventana
    de 30 días son cientos de idas y vueltas HTTPS (decenas de segundos), y
    tras un parón largo la ventana adaptativa puede pedir un año, con miles.
    Con lotes de 100 son ~100 veces menos viajes.
    """
    resultados = {}
    errores = []

    def _callback(request_id, response, exception):
        if exception is not None:
            errores.append((request_id, exception))
            return
        headers = response.get("payload", {}).get("headers", [])
        resultados[response["id"]] = {
            "id": response["id"],
            "subject": _header(headers, "Subject"),
            "sender": _header(headers, "From"),
            "date": _parse_date(_header(headers, "Date")),
        }

    batch = service.new_batch_http_request(callback=_callback)
    for mid in ids:
        batch.add(service.users().messages().get(
            userId="me", id=mid, format="metadata",
            metadataHeaders=["From", "Subject", "Date"],
        ))
    batch.execute()

    if errores:
        print(f"[gmail_oauth] {len(errores)} mensaje(s) no se pudieron leer en el lote: "
              f"{errores[0][1]}")

    # Se respeta el orden pedido; los que fallaron sencillamente no están.
    return [resultados[mid] for mid in ids if mid in resultados]


def gmail_search_fn(query: str) -> list:
    """Busca mensajes y devuelve {id, subject, sender, date} por cada uno."""
    service = _get_service()

    ids = []
    page_token = None
    while True:
        resp = service.users().messages().list(
            userId="me", q=query, pageToken=page_token, maxResults=500
        ).execute()
        ids.extend(m["id"] for m in resp.get("messages", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    messages = []
    for i in range(0, len(ids), BATCH_SIZE):
        messages.extend(_fetch_metadata_batch(service, ids[i:i + BATCH_SIZE]))
    return messages


def gmail_get_thread_fn(message_id: str) -> dict:
    """Devuelve el cuerpo (texto plano + HTML) de un mensaje."""
    service = _get_service()
    msg = service.users().messages().get(userId="me", id=message_id, format="full").execute()
    plaintext_parts, html_parts = [], []
    _walk_parts(msg.get("payload", {}), plaintext_parts, html_parts)
    return {
        "plaintext_body": "\n".join(plaintext_parts),
        "html_body": "\n".join(html_parts),
    }
