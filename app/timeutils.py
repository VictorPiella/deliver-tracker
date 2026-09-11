"""
timeutils.py — normalización de fechas.

Toda fecha que entra en la base de datos es **naive en UTC**. El motivo:
SQLite (vía SQLAlchemy) guarda los DATETIME como texto sin offset, así que si
se le pasa un datetime con tzinfo el offset se pierde en silencio y el valor
queda interpretado como si fuera UTC. Los emails de Gmail vienen con offset
local (+02:00 en España), de modo que guardarlos tal cual desplazaba cada
evento 1-2 horas y los dejaba incomparables con datetime.utcnow().

Las dos funciones de aquí son el único sitio donde se construyen fechas:
convertir a UTC *antes* de guardar hace que el desplazamiento no ocurra.
"""
import os
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:   # pragma: no cover - Python < 3.9
    ZoneInfo = None


def utcnow() -> datetime:
    """Ahora mismo, en UTC y sin tzinfo (el formato que guardamos)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_utc_naive(dt: datetime | None) -> datetime | None:
    """
    Pasa cualquier datetime a UTC naive. Los que ya vienen sin tzinfo se asumen
    UTC y se devuelven tal cual (es lo que ya hacía el código anterior).
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


# --- Zona para MOSTRAR ---
# Guardamos siempre en UTC, pero el panel tiene que enseñar la hora del reloj de
# casa. Sin esto se veían las horas con el desfase de UTC (1-2h en España).
DISPLAY_TZ = os.environ.get("DISPLAY_TZ") or os.environ.get("TZ") or "Europe/Madrid"


def _zona_local():
    if ZoneInfo is None:
        return timezone.utc
    try:
        return ZoneInfo(DISPLAY_TZ)
    except Exception:
        # Zona mal escrita o sin tzdata en el sistema: mejor enseñar UTC que
        # reventar el panel entero.
        return timezone.utc


def a_zona_local(dt: datetime | None) -> datetime | None:
    """
    Pasa un datetime UTC naive (lo que hay en la base) a la zona de DISPLAY_TZ,
    listo para formatear. Sólo para mostrar: nunca para guardar.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_zona_local())


# strftime('%b') depende del locale del sistema, y dentro del container el
# locale es C: el panel, que está en español, pintaba "Jan", "Aug", "Dec".
# Instalar locales españoles en la imagen por tres letras es desproporcionado,
# así que se traducen aquí.
MESES_ES = [
    "ene", "feb", "mar", "abr", "may", "jun",
    "jul", "ago", "sep", "oct", "nov", "dic",
]


def formatear(dt: datetime, patron: str) -> str:
    """
    Como strftime, pero con los meses en español. Sustituye %b antes de pasar
    el resto a strftime, para que el locale no tenga ni voz ni voto.
    """
    if "%b" in patron:
        patron = patron.replace("%b", MESES_ES[dt.month - 1])
    return dt.strftime(patron)
