"""
correos_api.py — el seguimiento de Correos, preguntado a Correos.

Hasta aquí el panel sólo sabía lo que contaban los emails, y una tienda sólo
escribe en los hitos: confirmado, enviado, entregado. El transportista, en
cambio, se mueve a diario. Un paquete podía llevar cuatro días poniendo
"Enviado" mientras Correos ya lo tenía en la unidad de reparto.

Esta es la única parte del programa que sale a internet por su cuenta. Está
hecha para que eso no se note cuando falla: si Correos no contesta, no
contesta, y el panel sigue con lo que sepa por los emails. Nunca tira el
escaneo abajo.

LA API
Es la que usa su propia web (www.correos.es/es/es/herramientas/localizador).
No pide clave ni registro:

    GET https://api1.correos.es/digital-services/searchengines/api/v1/envios
        ?text=<nº de envío>&language=ES

Y devuelve, por envío, una lista de eventos con fecha, hora, una fase
(PRE-ADMISIÓN / EN CAMINO / EN ENTREGA / ENTREGADO) y dos textos.

Al no ser una API publicada con contrato, puede cambiar sin avisar. Por eso
todo lo de aquí abajo da por hecho que cualquier campo puede faltar, y por eso
se mira primero el texto y sólo después la fase: el texto es lo que de verdad
describe lo que ha pasado.
"""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

API_URL = "https://api1.correos.es/digital-services/searchengines/api/v1/envios"

# Se puede apagar del todo sin tocar código: es la única parte que sale a
# internet, y quien no la quiera no deberia tener que aguantarla.
HABILITADO = os.environ.get("CORREOS_API_ENABLED", "true").lower() not in ("false", "0", "no")

# Corto a propósito. Esto corre dentro del escaneo, y más vale quedarse sin el
# dato de Correos que dejar el escaneo colgado.
TIMEOUT = float(os.environ.get("CORREOS_API_TIMEOUT", "8"))

# Cuántos envíos se consultan como mucho en una pasada. No es una API nuestra:
# conviene no machacarla.
MAX_CONSULTAS = int(os.environ.get("CORREOS_API_MAX_POR_ESCANEO", "25"))

_CABECERAS = {
    "Accept": "application/json",
    "Referer": "https://www.correos.es/",
    "User-Agent": "deliver-tracker (seguimiento personal de paquetes)",
}

# Los nº de Correos que hemos visto: PHBW6T9812926090108552J, PK...ES,
# 0082800045229702119525. Vale casi cualquier alfanumérico largo, así que esto
# filtra poco a propósito — sirve sobre todo para no preguntar por basura.
NUMERO_PLAUSIBLE_RE = re.compile(r"^[A-Z0-9]{10,30}$", re.IGNORECASE)


def numero_plausible(numero: str | None) -> bool:
    return bool(numero) and bool(NUMERO_PLAUSIBLE_RE.match(numero.strip()))


# --------------------------------------------------------------- los estados

# El texto del evento manda sobre la fase: la fase agrupa mucho ("EN CAMINO"
# cubre desde la admisión hasta el centro logístico) y el texto concreta.
# Orden importante: lo más específico primero.
TEXTO_A_ESTADO = [
    (re.compile(r"\bentregado\b|entrega realizada", re.IGNORECASE), "delivered"),
    (re.compile(r"no se ha podido entregar|ausente|segundo intento"
                r"|intento de entrega", re.IGNORECASE), "delivery_attempted"),
    (re.compile(r"en reparto|salida a reparto|reparto con el cartero", re.IGNORECASE), "out_for_delivery"),
    (re.compile(r"unidad de reparto|unidad responsable de su entrega"
                r"|oficina de destino", re.IGNORECASE), "at_distribution"),
    (re.compile(r"aduana.*(?:despachad|liberad)|despachado de aduanas", re.IGNORECASE), "customs_cleared"),
    (re.compile(r"aduana", re.IGNORECASE), "customs"),
    (re.compile(r"centro log[ií]stico|clasificado", re.IGNORECASE), "at_distribution"),
    (re.compile(r"llegada a españa|llegada al pa[ií]s", re.IGNORECASE), "in_country"),
    (re.compile(r"salida de|export", re.IGNORECASE), "left_origin"),
    (re.compile(r"admitido|admisi[oó]n", re.IGNORECASE), "shipped"),
    (re.compile(r"prerregistrado|pendiente de dep[oó]sito", re.IGNORECASE), "ordered"),
]

# Si el texto no dice nada reconocible, al menos la fase sitúa el envío.
FASE_A_ESTADO = {
    "1": "ordered",            # PRE-ADMISIÓN
    "2": "shipped",            # EN CAMINO
    "3": "out_for_delivery",   # EN ENTREGA
    "4": "delivered",          # ENTREGADO
}


def estado_del_evento(evento: dict) -> str:
    texto = " ".join(str(evento.get(c) or "") for c in ("summaryText", "extendedText"))
    for patron, estado in TEXTO_A_ESTADO:
        if patron.search(texto):
            return estado
    return FASE_A_ESTADO.get(str(evento.get("phase") or "").strip(), "unknown")


def _fecha_del_evento(evento: dict):
    """'05/10/2026' + '12:53:18' -> datetime. None si viene raro."""
    from datetime import datetime

    fecha = (evento.get("eventDate") or "").strip()
    hora = (evento.get("eventTime") or "00:00:00").strip()
    for formato in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(f"{fecha} {hora}", formato)
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------- la llamada

class CorreosNoContesta(Exception):
    """Cualquier fallo al preguntar. Quien llama decide, y normalmente decide seguir."""


def _pedir(numero: str) -> dict:
    url = f"{API_URL}?{urllib.parse.urlencode({'text': numero, 'language': 'ES'})}"
    peticion = urllib.request.Request(url, headers=_CABECERAS)
    try:
        with urllib.request.urlopen(peticion, timeout=TIMEOUT) as respuesta:
            return json.loads(respuesta.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise CorreosNoContesta(f"{type(e).__name__}: {e}") from e


def consultar(numero: str) -> list[dict]:
    """
    Los eventos de un envío, del más viejo al más nuevo:

        [{"fecha": datetime, "estado": "out_for_delivery",
          "texto": "Alta en la unidad de reparto", "id": "correos:<nº>:..."}]

    Lista vacía si Correos no sabe nada de ese número. Levanta CorreosNoContesta
    si no se ha podido preguntar — que no es lo mismo, y conviene distinguirlo:
    un envío desconocido no se vuelve a mirar, un fallo de red sí.
    """
    if not numero_plausible(numero):
        return []

    datos = _pedir(numero.strip())
    envios = datos.get("shipment") or []
    if not isinstance(envios, list):
        return []

    eventos = []
    for envio in envios:
        if not isinstance(envio, dict):
            continue
        codigo = envio.get("shipmentCode") or numero
        for bruto in (envio.get("events") or []):
            if not isinstance(bruto, dict):
                continue
            fecha = _fecha_del_evento(bruto)
            if fecha is None:
                continue
            texto = (bruto.get("summaryText") or bruto.get("extendedText") or "").strip()
            eventos.append({
                "fecha": fecha,
                "estado": estado_del_evento(bruto),
                "texto": texto or "Actualización de Correos",
                # Identificador estable para no apuntar dos veces el mismo
                # evento: Correos no da ninguno, así que se compone con el
                # número y el momento exacto, que no se repiten.
                "id": f"correos:{codigo}:{fecha:%Y%m%d%H%M%S}",
            })

    eventos.sort(key=lambda e: e["fecha"])
    return eventos
