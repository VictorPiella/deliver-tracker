"""
cleanup.py — borrado de paquetes: a la papelera desde el panel, borrado
definitivo desde la papelera, y archivado automático de los entregados viejos.

Por qué el borrado es lógico y no físico
----------------------------------------
Borrar la fila de un paquete se lleva por delante, en cascada, sus
package_events. Y esos eventos guardan el gmail_message_id, que es exactamente
lo que impide reprocesar un email ya visto. Resultado: borrabas un paquete y el
escaneo siguiente lo volvía a crear desde los mismos emails, que seguían en la
ventana de búsqueda.

Marcando `deleted_at` en su lugar, los eventos se conservan, el email no se
reingiere, el paquete no reaparece — y encima el borrado deja de ser definitivo.
El borrado físico sigue existiendo (`hard_delete_package`) pero sólo se dispara
a mano desde la papelera, nunca de forma automática.
"""
import os
from datetime import timedelta

from .models import Order, Package, get_session
from .timeutils import utcnow
from .mqtt_publish import unpublish_package

# Días que un paquete entregado sigue en el panel antes de irse solo a la
# papelera (de donde siempre se puede recuperar). 0 desactiva el archivado.
PURGE_DELIVERED_AFTER_DAYS = int(os.environ.get("PURGE_DELIVERED_AFTER_DAYS", "30"))


def soft_delete_package(session, package: Package) -> None:
    """Manda un paquete a la papelera. Reversible con restore_package()."""
    if package.deleted_at is None:
        package.deleted_at = utcnow()


def restore_package(session, package: Package) -> None:
    """Saca un paquete de la papelera. Sus eventos nunca se fueron."""
    package.deleted_at = None


def hard_delete_package(session, package: Package) -> None:
    """
    Borrado definitivo: la fila y, en cascada, sus eventos.

    OJO: al irse los eventos se pierden sus gmail_message_id, así que si los
    emails de origen siguen dentro de la ventana de búsqueda el paquete
    reaparecerá en el próximo escaneo. Es aceptable aquí porque es una acción
    explícita del usuario desde la papelera, pero es justo el motivo de que el
    borrado normal sea lógico.
    """
    order_id = package.order_id
    session.delete(package)
    session.flush()

    # Si era el último paquete de su pedido, el pedido se queda huérfano.
    restantes = session.query(Package).filter_by(order_id=order_id).count()
    if restantes == 0:
        order = session.get(Order, order_id)
        if order is not None:
            session.delete(order)


# Nombre histórico, mantenido para no romper llamadas existentes.
delete_package = soft_delete_package


def purge_old_delivered(db_path: str, log=print) -> int:
    """
    Manda a la papelera los paquetes entregados hace más de
    PURGE_DELIVERED_AFTER_DAYS. Devuelve cuántos se han archivado.

    Antes esto BORRABA definitivamente a los 15 días. Con la base como única
    copia duradera (los emails puede que ya no existan), eso destruía el
    histórico de compras en silencio y sin vuelta atrás. Ahora sólo los aparta
    del panel, y siguen recuperables desde la papelera.
    """
    if PURGE_DELIVERED_AFTER_DAYS <= 0:
        return 0

    corte = utcnow() - timedelta(days=PURGE_DELIVERED_AFTER_DAYS)
    session = get_session(db_path)
    viejos = (
        session.query(Package)
        .filter(
            Package.status == "delivered",
            Package.last_updated < corte,
            Package.deleted_at.is_(None),
        )
        .all()
    )
    for package in viejos:
        soft_delete_package(session, package)
        unpublish_package(package.id, log=log)

    if viejos:
        session.commit()
        log(f"[cleanup] {len(viejos)} paquete(s) entregados hace más de "
            f"{PURGE_DELIVERED_AFTER_DAYS} días, movidos a la papelera")
    session.close()
    return len(viejos)
