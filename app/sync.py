"""
Orquesta: coge eventos ya parseados (dicts normalizados de los parsers) y los
persiste en la base de datos, creando Order/Package si no existen y actualizando
el estado del Package solo si el nuevo evento supone un AVANCE real (para que
emails desordenados o duplicados no rebobinen el estado).
"""
from datetime import datetime
from sqlalchemy.exc import IntegrityError
from .models import Order, OrderItem, Package, PackageEvent, STATUS_CANCELLED, STATUS_ORDER


def status_rank(status: str) -> int:
    try:
        return STATUS_ORDER.index(status)
    except ValueError:
        return -1  # 'unknown' u otros no listados van antes que todo, nunca pisan nada


def evento_ya_visto(session, message_id: str) -> bool:
    """
    ¿Se ha procesado ya este email? Los ingest_* devuelven False tanto para un
    duplicado como para un evento que no se ha podido colgar de ningún paquete,
    y quien llama necesita distinguirlos: en el segundo caso hay que intentar un
    plan B, en el primero no.
    """
    return session.query(PackageEvent).filter_by(gmail_message_id=message_id).first() is not None


def evento_anclable(event: dict) -> bool:
    """
    ¿Tiene el evento algún identificador al que agarrarse?

    Es el sintoma tipico de una plantilla cambiada: el parser reconoce al
    remitente y devuelve un dict, pero no ha encontrado ni order_id ni
    package_id en el asunto, asi que el evento no se puede colgar de ningun
    paquete. ingest_event lo descartaria en silencio; gmail_sync lo usa para
    poder contarlo como "no reconocido" en vez de como duplicado.
    """
    return bool(event.get("order_id") or event.get("package_id"))


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

    # Una cancelación manda sobre todo lo demás: no es un punto del recorrido,
    # es otro final. Da igual que el paquete figurara como enviado o en reparto.
    if nuevo_status == STATUS_CANCELLED:
        if package.status == STATUS_CANCELLED:
            return False
        package.status = STATUS_CANCELLED
        return True

    # Y al revés: nada reanima un pedido ya cancelado.
    if package.status == STATUS_CANCELLED:
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


def merge_packages(session, origen: Package, destino: Package) -> int:
    """
    Funde `origen` dentro de `destino`: le pasa sus eventos y lo elimina.
    Devuelve cuántos eventos se han movido.

    Para qué: Correos no incluye ningún ID de Amazon/AliExpress en sus emails,
    así que un paquete repartido por Correos aparece dos veces — la entrada de
    la tienda y la de Correos. Enlazarlas automáticamente sería adivinar (ver
    parsers/correos.py), pero tú sí sabes cuáles son la misma, y esto te deja
    unirlas a mano.

    Los eventos se MUEVEN, no se borran: conservan su gmail_message_id, que es
    lo que impide que el escaneo siguiente vuelva a crear el paquete de origen.
    """
    if origen.id == destino.id:
        return 0

    # Los eventos se reasignan con un UPDATE directo y luego se invalidan las
    # colecciones en memoria. Si se cambiara evento.package_id sin más, el
    # `cascade="all, delete-orphan"` de Package.events seguiría viendo los
    # eventos colgando del origen y se los llevaría por delante al borrarlo.
    ids = [e.id for e in origen.events]
    movidos = len(ids)
    if ids:
        session.query(PackageEvent).filter(PackageEvent.id.in_(ids)).update(
            {PackageEvent.package_id: destino.id}, synchronize_session=False
        )
        session.expire(origen, ["events"])
        session.expire(destino, ["events"])
    session.flush()

    # El origen suele ser el del transportista: es el que trae el tracking real.
    if origen.courier and not destino.courier:
        destino.courier = origen.courier
    if origen.courier_tracking_number and not destino.courier_tracking_number:
        destino.courier_tracking_number = origen.courier_tracking_number
    if origen.eta and not destino.eta:
        destino.eta = origen.eta
    if origen.tracking_url and not destino.tracking_url:
        destino.tracking_url = origen.tracking_url
    if origen.title and not destino.title:
        destino.title = origen.title
    # Y el destino suele ser el de la tienda: es el que trae nombre e imagen.
    if origen.order and destino.order:
        if origen.order.title and not destino.order.title:
            destino.order.title = origen.order.title
        if origen.order.image_url and not destino.order.image_url:
            destino.order.image_url = origen.order.image_url

    order_id_origen = origen.order_id
    session.delete(origen)
    session.flush()

    if session.query(Package).filter_by(order_id=order_id_origen).count() == 0:
        huerfano = session.get(Order, order_id_origen)
        if huerfano is not None:
            session.delete(huerfano)

    if not destino.status_is_manual:
        recompute_status_from_events(destino)
    return movidos


def reparar_last_updated(session, log=print) -> int:
    """
    Recalcula last_updated desde los eventos. Repara las filas que quedaron con
    la fecha equivocada mientras la columna tenia onupdate (ver models.Package):
    cualquier escritura la ponia "ahora", asi que el panel las ordenaba mal y la
    columna "Actualizado" mentia.
    """
    arreglados = 0
    for package in session.query(Package).all():
        if not package.events:
            continue
        real = max(e.event_date for e in package.events)
        if package.last_updated != real:
            package.last_updated = real
            arreglados += 1
    if arreglados:
        session.commit()
        log(f"[sync] last_updated recalculado en {arreglados} paquete(s)")
    return arreglados


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
    """
    Busca el paquete incluyendo los que están en la papelera, y a propósito: si
    borraste un paquete y luego llega otro email suyo, el evento se apunta al
    mismo paquete (que sigue en la papelera, listo por si lo restauras) en vez
    de crear un duplicado visible que "resucita" lo que habías quitado.
    """
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


# Campos que un reescaneo puede rellenar si estaban vacíos. El estado no está
# aquí a propósito: eso lo decide apply_status, con sus reglas de avance y de
# estado manual.
CAMPOS_RELLENABLES = ("courier", "courier_tracking_number", "eta", "tracking_url")


def _rellenar_huecos(session, package, event: dict) -> None:
    """Pone los valores que el paquete no tiene todavía. No pisa nada."""
    if package is None:
        return
    for campo in CAMPOS_RELLENABLES:
        if event.get(campo) and getattr(package, campo, None) is None:
            setattr(package, campo, event[campo])

    # El título NO se trata como un hueco más. Un escaneo va del email más
    # reciente al más viejo, así que el del envío llega ANTES que el de
    # confirmación: rellenando sin más, el envío parcial se quedaba el nombre
    # ("y 2 productos más" en un pedido de cuatro). Aquí vale la misma regla que
    # en el camino normal — un título preciso pisa, uno de relleno sólo rellena.
    if event.get("title") and (not package.title or event.get("title_preciso")):
        package.title = event["title"]
    if package.order is not None:
        if event.get("title") and (not package.order.title or event.get("title_preciso")):
            package.order.title = event["title"]
        _guardar_articulos(session, package.order, event)


def _guardar_articulos(session, order: Order, event: dict) -> None:
    """
    Guarda el desglose del pedido, si el email lo trae.

    Solo manda el email que habla del PEDIDO entero (el de confirmacion, el que
    viene con title_preciso). Los de envio listan lo que va en ESE envio, que
    puede ser una parte: el pedido real tenia cuatro lineas y su email de envio
    solo tres. Si esos pisaran, el desglose encogeria. Aun asi se guardan cuando
    no hay nada, que es mejor que una ficha vacia.
    """
    articulos = event.get("items")
    if not articulos:
        return
    if order.items and not event.get("title_preciso"):
        return

    for viejo in list(order.items):
        session.delete(viejo)
    session.flush()

    for i, a in enumerate(articulos):
        session.add(OrderItem(
            order_id=order.id, posicion=i, title=a["title"],
            variant=a.get("variant"), quantity=a.get("quantity"),
            image_url=a.get("image_url"),
        ))


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
        # Mismo email ya procesado: no se crea otro evento. Pero sí se rellenan
        # los campos que estuvieran vacíos.
        #
        # Hace falta porque los parsers aprenden a sacar cosas nuevas con el
        # tiempo (el transportista y la URL de seguimiento de Shopify se
        # añadieron después), y sin esto los paquetes ya guardados se quedaban
        # sin ellas para siempre: su email ya estaba visto, así que por mucho que
        # se reescaneara nunca se volvían a mirar. Sólo se rellena lo que está a
        # None; nada que ya tenga valor se toca.
        _rellenar_huecos(session, existing.package, event)
        session.commit()
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

    # El email de "Pedido realizado" de Amazon no trae shipmentId, así que crea
    # un paquete provisional con id 'order-<pedido>'. Cuando después llega el de
    # "Enviado", que SÍ lo trae, se creaba un paquete NUEVO: el mismo pedido
    # aparecía dos veces en el panel, uno clavado en "Pedido realizado" y otro
    # avanzando. Aquí el envío real adopta el provisional y se queda su
    # histórico, en vez de duplicarlo.
    if had_own_package_id:
        provisional_id = f"order-{order_id_ext}"
        if package_id_ext != provisional_id:
            ya_existe = (
                session.query(Package)
                .filter_by(source=source, external_package_id=package_id_ext)
                .first()
            )
            if ya_existe is None:
                provisional = (
                    session.query(Package)
                    .filter_by(source=source, external_package_id=provisional_id,
                               order_id=order.id)
                    .first()
                )
                if provisional is not None:
                    provisional.external_package_id = package_id_ext
                    session.flush()

    if not had_own_package_id:
        existing_packages = (
            session.query(Package)
            .filter_by(order_id=order.id, source=source)
            .order_by(Package.id)
            .all()
        )
        if len(existing_packages) == 1:
            package = existing_packages[0]
        elif existing_packages and event["status"] == "ordered":
            # El email de "Pedido realizado" es del PEDIDO, no de un envío
            # concreto, y en un escaneo llega el último (Gmail devuelve primero
            # lo más nuevo). Si el pedido ya tiene varios envíos, el fallback de
            # abajo creaba un tercer paquete fantasma 'order-XXX' clavado en
            # "Pedido realizado" para siempre. Colgarlo de uno de los envíos
            # reales no puede estropear nada: 'ordered' es el estado de menor
            # rango, así que apply_status no lo aplicará sobre ninguno.
            package = existing_packages[0]
        else:
            # Varios envíos y un evento que SÍ cambiaría el estado (una entrega,
            # una cancelación): no hay forma de saber a cuál se refiere, así que
            # mejor un paquete de más que marcar el envío equivocado.
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
    # La URL SI se sobreescribe cuando llega una nueva: la del email de envio es
    # mas util que la del de confirmacion, y un token viejo puede haber caducado.
    if event.get("tracking_url"):
        package.tracking_url = event["tracking_url"]
    # La ETA sí se pisa cuando llega una nueva: Amazon la reajusta en cada
    # email, y la última es la buena.
    if event.get("eta"):
        package.eta = event["eta"]

    # Un titulo "preciso" (sacado del bloque del propio pedido) pisa al que
    # hubiera; el del asunto solo rellena si no habia nada. Sin esto, un pedido
    # creado antes desde un email con varios pedidos se quedaba con el nombre
    # del producto equivocado para siempre.
    if event.get("title") and (not order.title or event.get("title_preciso")):
        order.title = event["title"]
    # Y el del propio envio, con la misma regla pero mirando al paquete. Es lo
    # que hace que dos envios de un mismo pedido dejen de llamarse igual.
    if event.get("title") and (not package.title or event.get("title_preciso")):
        package.title = event["title"]
    if event.get("image_url") and not order.image_url:
        order.image_url = event["image_url"]

    _guardar_articulos(session, order, event)

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
