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
from datetime import datetime, timezone


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
