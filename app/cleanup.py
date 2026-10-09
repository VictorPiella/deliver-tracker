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

# Cuanto aguanta algo en la papelera antes de borrarse de verdad. 0 la deja
# para siempre, que era el comportamiento de antes.
#
# Ojo con bajarlo mucho: un paquete entregado llega a la papelera solo a los
# PURGE_DELIVERED_AFTER_DAYS dias, asi que entre las dos cifras se decide
# cuanto historial de compras se conserva. Con los valores por defecto son 60
# dias desde la entrega.
PURGE_TRASH_AFTER_DAYS = int(os.environ.get("PURGE_TRASH_AFTER_DAYS", "30"))


def soft_delete_package(session, package: Package, motivo: str = "sin especificar",
                        log=print) -> None:
    """
    Manda un paquete a la papelera. Reversible con restore_package().

    Se deja rastro en el log a proposito. Una vez aparecieron 11 paquetes en la
    papelera sin que la purga automatica pudiera haberlos elegido (varios ni
    siquiera estaban entregados), y no hubo forma de saber quien los habia
    mandado alli: el container se habia recreado y sus logs ya no estaban.
    """
    if package.deleted_at is None:
        package.deleted_at = utcnow()
        log(f"[cleanup] A la papelera: id={package.id} "
            f"({package.source}/{package.external_package_id}) "
            f"estado={package.status} motivo={motivo}")


def restore_package(session, package: Package) -> None:
    """Saca un paquete de la papelera. Sus eventos nunca se fueron."""
    package.deleted_at = None


def hard_delete_package(session, package: Package) -> None:
    """
    Borrado definitivo: la fila y, en cascada, sus eventos.

    Antes esto hacía resucitar paquetes. Al irse los eventos se perdían sus
    gmail_message_id, que son lo que impide reprocesar un email: si los correos
    seguían dentro de la ventana de búsqueda, el paquete volvía a aparecer en el
    escaneo siguiente como si nada.

    Ahora los identificadores se apartan antes de borrar (ver EmailProcesado).
    Se queda la lápida, no el contenido: ni asunto, ni remitente, ni estado.
    """
    from .models import EmailProcesado

    for evento in package.events:
        if not evento.gmail_message_id:
            continue
        ya = (session.query(EmailProcesado)
              .filter_by(gmail_message_id=evento.gmail_message_id).first())
        if ya is None:
            session.add(EmailProcesado(gmail_message_id=evento.gmail_message_id))
    session.flush()

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
        soft_delete_package(session, package,
                            motivo=f"entregado hace mas de {PURGE_DELIVERED_AFTER_DAYS}d",
                            log=log)
        unpublish_package(package.id, log=log)

    if viejos:
        session.commit()
        log(f"[cleanup] {len(viejos)} paquete(s) entregados hace más de "
            f"{PURGE_DELIVERED_AFTER_DAYS} días, movidos a la papelera")
    session.close()
    return len(viejos)


def vaciar_papelera_vieja(db_path: str, log=print) -> int:
    """
    Borra de verdad lo que lleva en la papelera mas de PURGE_TRASH_AFTER_DAYS.

    Es el unico sitio del programa que destruye datos sin que nadie pulse nada,
    asi que va con cuidado:

    - Solo mira deleted_at. Lo que esta en la papelera lo mandaste tu o lo
      archivo el limpiador de entregados; en ambos casos ya pasaron antes por
      el paso reversible.
    - Deja lapida de cada email (ver hard_delete_package), para que vaciarla no
      haga reaparecer los paquetes en el siguiente escaneo.
    - Se puede apagar entera con PURGE_TRASH_AFTER_DAYS=0.

    Y lo dice en el log con nombre y fecha, porque un borrado silencioso que
    nadie puede reconstruir despues es justo lo que no se quiere.
    """
    if PURGE_TRASH_AFTER_DAYS <= 0:
        return 0

    corte = utcnow() - timedelta(days=PURGE_TRASH_AFTER_DAYS)
    session = get_session(db_path)
    viejos = (
        session.query(Package)
        .filter(Package.deleted_at.isnot(None), Package.deleted_at < corte)
        .all()
    )
    for package in viejos:
        log(f"[cleanup] Borrado definitivo: id={package.id} "
            f"({package.source}/{package.external_package_id}) "
            f"en la papelera desde {package.deleted_at:%Y-%m-%d}")
        hard_delete_package(session, package)

    if viejos:
        session.commit()
        log(f"[cleanup] {len(viejos)} paquete(s) borrados definitivamente "
            f"tras mas de {PURGE_TRASH_AFTER_DAYS} dias en la papelera")
    session.close()
    return len(viejos)
