"""
Orquesta: coge eventos ya parseados (dicts normalizados de los parsers) y los
persiste en la base de datos, creando Order/Package si no existen y actualizando
el estado del Package solo si el nuevo evento supone un AVANCE real (para que
emails desordenados o duplicados no rebobinen el estado).
"""
from datetime import datetime
from sqlalchemy.exc import IntegrityError
from .models import Order, Package, PackageEvent, STATUS_ORDER


def status_rank(status: str) -> int:
    try:
        return STATUS_ORDER.index(status)
    except ValueError:
        return -1  # 'unknown' u otros no listados van antes que todo, nunca pisan nada


def apply_status(package, nuevo_status: str) -> bool:
    """
    Aplica un estado venido de un email al Package, si procede. Devuelve True si
    lo ha cambiado.

    Dos reglas:
    - Un estado fijado a mano desde el panel manda sobre lo que digan los emails.
      El evento se guarda igual (queda en el histórico), pero el estado no se toca.
    - Si no, el estado sólo avanza: un email que llega desordenado o repetido no
      rebobina un paquete ya entregado.
    """
    if package.status_is_manual:
        return False
    if package.status == "unknown" or status_rank(nuevo_status) >= status_rank(package.status):
        package.status = nuevo_status
        return True
    return False


def recompute_status_from_events(package) -> str:
    """
    Recalcula el estado a partir de los eventos guardados, que es lo que hay que
    hacer al soltar un estado manual: se vuelve al que dicen los emails, no al
    que había antes de tocarlo.
    """
    if not package.events:
        return package.status
    mejor = max(package.events, key=lambda e: (status_rank(e.status), e.event_date))
    package.status = mejor.status
    package.status_label_raw = mejor.status_label_raw
    package.last_updated = mejor.event_date
    return mejor.status


def get_or_create_order(session, source: str, external_order_id: str, title: str | None, image_url: str | None = None) -> Order:
    order = (
        session.query(Order)
        .filter_by(source=source, external_order_id=external_order_id)
        .first()
    )
    if order is None:
        order = Order(source=source, external_order_id=external_order_id, title=title, image_url=image_url)
        session.add(order)
        session.flush()  # para tener order.id disponible
    else:
        if title and not order.title:
            order.title = title
        if image_url and not order.image_url:
            order.image_url = image_url
    return order


def get_or_create_package(session, order: Order, source: str, external_package_id: str) -> Package:
    package = (
        session.query(Package)
        .filter_by(source=source, external_package_id=external_package_id)
        .first()
    )
    if package is None:
        package = Package(
            order_id=order.id,
            source=source,
            external_package_id=external_package_id,
            status="unknown",
        )
        session.add(package)
        session.flush()
    return package


def ingest_event(session, event: dict) -> bool:
    """
    Procesa un evento normalizado (salida de los parsers) y lo persiste.
    Devuelve True si se ha creado un evento nuevo, False si ya existía (duplicado, mismo message_id).
    """
    # Evitar reprocesar el mismo email
    existing = (
        session.query(PackageEvent)
        .filter_by(gmail_message_id=event["message_id"])
        .first()
    )
    if existing is not None:
        return False

    source = event["source"]
    order_id_ext = event.get("order_id")
    package_id_ext = event.get("package_id")

    # Si no hay ni order_id ni package_id no podemos anclar el evento a nada -> lo descartamos
    if not order_id_ext and not package_id_ext:
        return False

    # Si solo tenemos order_id (típico: Amazon 'Pedido'/'Enviado'/'En reparto' antes del email
    # de entregado que sí trae el nº de pedido), usamos el order_id también como package_id
    # provisional, igual que hacemos en el parser de AliExpress.
    if not order_id_ext:
        order_id_ext = package_id_ext
    if not package_id_ext:
        package_id_ext = f"order-{order_id_ext}"

    order = get_or_create_order(session, source, order_id_ext, event.get("title"), event.get("image_url"))

    # Si el evento no traía package_id propio (p.ej. Amazon 'Entregado' sin shipmentId)
    # y el pedido ya tiene EXACTAMENTE un paquete registrado, lo reutilizamos en vez
    # de crear uno nuevo con el id de fallback 'order-XXX'. Si hay 0 o 2+ paquetes
    # para el pedido, no podemos saber a cuál pertenece con certeza, así que se usa
    # el fallback (mejor un paquete de más que mezclar datos de dos paquetes distintos).
    had_own_package_id = bool(event.get("package_id"))
    if not had_own_package_id:
        existing_packages = (
            session.query(Package)
            .filter_by(order_id=order.id, source=source)
            .all()
        )
        if len(existing_packages) == 1:
            package = existing_packages[0]
        else:
            package = get_or_create_package(session, order, source, package_id_ext)
    else:
        package = get_or_create_package(session, order, source, package_id_ext)

    pe = PackageEvent(
        package_id=package.id,
        status=event["status"],
        status_label_raw=event["status_label_raw"],
        gmail_message_id=event["message_id"],
        gmail_subject=event["status_label_raw"],
        event_date=event["event_date"],
    )
    session.add(pe)

    # Actualiza el estado "actual" del package solo si es un avance (o si el actual
    # es 'unknown'), y nunca si lo has fijado a mano desde el panel.
    if apply_status(package, event["status"]):
        package.status_label_raw = event["status_label_raw"]
        package.last_updated = event["event_date"]

    if event.get("courier_tracking_number") and not package.courier_tracking_number:
        package.courier_tracking_number = event["courier_tracking_number"]
    if event.get("courier") and not package.courier:
        package.courier = event["courier"]

    if event.get("title") and not order.title:
        order.title = event["title"]
    if event.get("image_url") and not order.image_url:
        order.image_url = event["image_url"]

    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        return False

    return True


def ingest_carrier_event(session, event: dict, courier: str) -> bool:
    """
    Ingesta un evento de transportista de última milla (GLS) sobre un Package
    YA EXISTENTE (de Amazon/AliExpress) — a diferencia de ingest_event, no
    crea Order/Package nuevos. Busca el Package por package_id (external_package_id,
    de cualquier fuente) y si no por courier_tracking_number ya guardado. Si no
    hay un match inequívoco, descarta el evento (mejor no enlazar que enlazar mal).
    Devuelve True si se ha creado un evento nuevo.
    """
    existing = (
        session.query(PackageEvent)
        .filter_by(gmail_message_id=event["message_id"])
        .first()
    )
    if existing is not None:
        return False

    package = None
    if event.get("package_id"):
        package = session.query(Package).filter_by(external_package_id=event["package_id"]).first()
    if package is None and event.get("tracking_number"):
        package = session.query(Package).filter_by(courier_tracking_number=event["tracking_number"]).first()
    if package is None:
        return False

    pe = PackageEvent(
        package_id=package.id,
        status=event["status"],
        status_label_raw=event["status_label_raw"],
        gmail_message_id=event["message_id"],
        gmail_subject=event["status_label_raw"],
        event_date=event["event_date"],
    )
    session.add(pe)

    if apply_status(package, event["status"]):
        package.status_label_raw = event["status_label_raw"]
        package.last_updated = event["event_date"]

    if not package.courier:
        package.courier = courier
    if event.get("tracking_number") and not package.courier_tracking_number:
        package.courier_tracking_number = event["tracking_number"]

    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        return False

    return True
