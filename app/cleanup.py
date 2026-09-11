"""
cleanup.py — borrado de paquetes (manual desde la UI, y automático para los
que llevan mucho tiempo entregados).
"""
from datetime import timedelta

from .models import get_session, Order, Package
from .timeutils import utcnow
from .mqtt_publish import unpublish_package

# Cuántos días se conserva un paquete tras marcarse como "delivered" antes
# de purgarlo automáticamente. Pasado ese tiempo ya no aporta nada al panel.
DELETE_DELIVERED_AFTER_DAYS = 15


def delete_package(session, package: Package) -> None:
    """
    Borra un paquete (sus eventos se van por cascade, ver models.Package.events).
    Si era el último paquete de su pedido, borra también el pedido huérfano.
    """
    order_id = package.order_id
    session.delete(package)
    session.flush()

    remaining = session.query(Package).filter_by(order_id=order_id).count()
    if remaining == 0:
        order = session.get(Order, order_id)
        if order is not None:
            session.delete(order)


def purge_old_delivered(db_path: str, log=print) -> int:
    """
    Borra los paquetes en estado 'delivered' con más de DELETE_DELIVERED_AFTER_DAYS
    días desde su última actualización. Devuelve cuántos se han borrado.
    """
    cutoff = utcnow() - timedelta(days=DELETE_DELIVERED_AFTER_DAYS)
    session = get_session(db_path)
    old_packages = (
        session.query(Package)
        .filter(Package.status == "delivered", Package.last_updated < cutoff)
        .all()
    )
    for package in old_packages:
        delete_package(session, package)
        unpublish_package(package.id, log=log)

    if old_packages:
        session.commit()
        log(f"[cleanup] {len(old_packages)} paquete(s) entregados hace "
            f"más de {DELETE_DELIVERED_AFTER_DAYS} días, purgados")
    session.close()
    return len(old_packages)
