"""
backup.py — copias de seguridad de la base.

Por qué hace falta
------------------
La base es la única copia duradera de todo esto. Los emails de origen puede que
ya no existan (los hayas borrado tú, o Gmail los haya movido a la papelera), y
en ese caso el histórico no se puede reconstruir de ninguna manera. Un fichero
SQLite corrupto o un `docker compose down -v` a destiempo se lleva años de
pedidos por delante.

Cómo
----
`VACUUM INTO` en vez de copiar el fichero a pelo: SQLite hace la copia de forma
transaccional, así que sale coherente aunque el worker esté escribiendo en ese
momento. Copiar el .db con cp mientras hay una escritura a medias produce una
copia rota que parece buena.

Las copias van a <carpeta de la base>/backups/, es decir dentro del mismo
volumen que ya montas — así entran en el backup de appdata de Unraid sin
configurar nada más.
"""
import os
from datetime import timedelta

from sqlalchemy import text

from .models import get_engine, get_last_sync, get_session, get_setting, set_setting
from .timeutils import utcnow

# Cuántas copias diarias se conservan. 7 cabe de sobra en cualquier sitio: la
# base de un panel doméstico pesa unos pocos MB.
BACKUP_KEEP = int(os.environ.get("BACKUP_KEEP", "7"))
BACKUP_ENABLED = os.environ.get("BACKUP_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")

LAST_BACKUP_KEY = "last_backup_at"


def backup_dir(db_path: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), "backups")


def _prune(directorio: str, keep: int, log=print) -> int:
    copias = sorted(
        (f for f in os.listdir(directorio) if f.startswith("packages-") and f.endswith(".db")),
        reverse=True,
    )
    borradas = 0
    for viejo in copias[keep:]:
        try:
            os.remove(os.path.join(directorio, viejo))
            borradas += 1
        except OSError as e:
            log(f"[backup] No se pudo borrar {viejo}: {e}")
    return borradas


def make_backup(db_path: str, keep: int | None = None, log=print) -> str | None:
    """
    Hace una copia de la base. Devuelve la ruta, o None si no se ha podido.
    Es "mejor esfuerzo": un fallo aquí nunca debe tumbar el escaneo.
    """
    keep = BACKUP_KEEP if keep is None else keep
    destino_dir = backup_dir(db_path)

    try:
        os.makedirs(destino_dir, exist_ok=True)
        nombre = f"packages-{utcnow().strftime('%Y%m%d')}.db"
        destino = os.path.join(destino_dir, nombre)

        if os.path.exists(destino):
            os.remove(destino)   # VACUUM INTO falla si el destino ya existe

        with get_engine(db_path).connect() as conn:
            conn.execute(text("VACUUM INTO :destino"), {"destino": destino})

        borradas = _prune(destino_dir, keep, log=log)
        tam = os.path.getsize(destino) / 1024
        log(f"[backup] Copia en {destino} ({tam:.0f} KB)"
            + (f", {borradas} antigua(s) borrada(s)" if borradas else ""))
        return destino
    except Exception as e:
        log(f"[backup] Copia omitida: {e}")
        return None


def backup_if_due(db_path: str, log=print) -> str | None:
    """
    Hace una copia como mucho una vez al día. Se llama tras cada escaneo, que es
    justo cuando la base acaba de cambiar.
    """
    if not BACKUP_ENABLED:
        return None

    session = get_session(db_path)
    crudo = get_setting(session, LAST_BACKUP_KEY)
    session.close()

    if crudo:
        from datetime import datetime
        try:
            if utcnow() - datetime.fromisoformat(crudo) < timedelta(hours=23):
                return None
        except ValueError:
            pass   # valor corrupto: se hace la copia y se reescribe

    destino = make_backup(db_path, log=log)
    if destino is not None:
        session = get_session(db_path)
        set_setting(session, LAST_BACKUP_KEY, utcnow().isoformat())
        session.commit()
        session.close()
    return destino
