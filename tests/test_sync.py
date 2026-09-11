"""
Tests de la capa de persistencia: crear Order/Package, deduplicar emails, y la
regla de que el estado de un paquete sólo avanza (nunca retrocede por un email
que llega tarde o desordenado).
"""
from datetime import datetime, timedelta

from app.models import Order, Package, PackageEvent
from app.sync import ingest_event, ingest_carrier_event, status_rank

from conftest import EVENT_DATE


def evento(**kwargs):
    base = {
        "source": "aliexpress",
        "order_id": "3074309624382839",
        "package_id": "315193141453520012",
        "status": "shipped",
        "status_label_raw": "Paquete X: enviado",
        "title": "Tapón colador",
        "image_url": None,
        "message_id": "msg-1",
        "event_date": EVENT_DATE,
    }
    base.update(kwargs)
    return base


class TestStatusRank:
    def test_sigue_el_orden_logico(self):
        assert status_rank("ordered") < status_rank("shipped") < status_rank("delivered")

    def test_unknown_va_antes_que_todo(self):
        # Así un evento 'unknown' nunca pisa un estado real ya registrado.
        assert status_rank("unknown") == -1
        assert status_rank("unknown") < status_rank("ordered")


class TestIngestEvent:
    def test_crea_order_package_y_evento(self, session):
        assert ingest_event(session, evento()) is True

        order = session.query(Order).one()
        package = session.query(Package).one()
        event = session.query(PackageEvent).one()

        assert order.external_order_id == "3074309624382839"
        assert order.title == "Tapón colador"
        assert package.external_package_id == "315193141453520012"
        assert package.status == "shipped"
        assert event.gmail_message_id == "msg-1"
        assert event.package_id == package.id

    def test_deduplica_por_message_id(self, session):
        assert ingest_event(session, evento()) is True
        assert ingest_event(session, evento()) is False
        assert session.query(PackageEvent).count() == 1

    def test_eventos_sucesivos_comparten_paquete(self, session):
        ingest_event(session, evento(message_id="m1", status="shipped"))
        ingest_event(session, evento(message_id="m2", status="customs"))
        ingest_event(session, evento(message_id="m3", status="delivered"))

        assert session.query(Package).count() == 1
        assert session.query(PackageEvent).count() == 3
        assert session.query(Package).one().status == "delivered"

    def test_descarta_eventos_sin_ningun_identificador(self, session):
        assert ingest_event(session, evento(order_id=None, package_id=None)) is False
        assert session.query(Package).count() == 0

    def test_rellena_titulo_e_imagen_si_faltaban(self, session):
        ingest_event(session, evento(message_id="m1", title=None, image_url=None))
        assert session.query(Order).one().title is None

        ingest_event(session, evento(message_id="m2", title="Nombre real",
                                     image_url="https://ejemplo/x.jpg"))
        order = session.query(Order).one()
        assert order.title == "Nombre real"
        assert order.image_url == "https://ejemplo/x.jpg"

    def test_no_pisa_un_titulo_ya_bueno(self, session):
        ingest_event(session, evento(message_id="m1", title="Nombre bueno"))
        ingest_event(session, evento(message_id="m2", title="(sin título)"))
        assert session.query(Order).one().title == "Nombre bueno"


class TestElEstadoSoloAvanza:
    def test_un_email_atrasado_no_rebobina_el_estado(self, session):
        ingest_event(session, evento(message_id="m1", status="delivered"))
        # Un email de 'en aduanas' que llega después (reenvío, orden alterado):
        # se guarda como evento del histórico, pero el paquete sigue entregado.
        ingest_event(session, evento(message_id="m2", status="customs"))

        assert session.query(Package).one().status == "delivered"
        assert session.query(PackageEvent).count() == 2

    def test_unknown_no_pisa_un_estado_real(self, session):
        ingest_event(session, evento(message_id="m1", status="in_country"))
        ingest_event(session, evento(message_id="m2", status="unknown"))
        assert session.query(Package).one().status == "in_country"

    def test_desde_unknown_cualquier_estado_entra(self, session):
        ingest_event(session, evento(message_id="m1", status="unknown"))
        assert session.query(Package).one().status == "unknown"
        ingest_event(session, evento(message_id="m2", status="customs"))
        assert session.query(Package).one().status == "customs"


class TestAmazonSinShipmentId:
    """
    Amazon manda el email de 'Entregado' sin shipmentId. Si el pedido ya tiene
    exactamente un paquete, el evento va a ese paquete; si hay ambigüedad se usa
    un id de reserva antes que arriesgarse a mezclar dos envíos.
    """

    def _amazon(self, **kwargs):
        base = {
            "source": "amazon",
            "order_id": "408-3320942-2576360",
            "package_id": None,
            "status": "delivered",
            "status_label_raw": "Entregado",
            "title": None,
            "image_url": None,
            "message_id": "m-del",
            "event_date": EVENT_DATE,
        }
        base.update(kwargs)
        return base

    def test_se_engancha_al_unico_paquete_del_pedido(self, session):
        ingest_event(session, self._amazon(message_id="m1", package_id="AB12cd34E", status="shipped"))
        ingest_event(session, self._amazon(message_id="m2", package_id=None, status="delivered"))

        assert session.query(Package).count() == 1
        package = session.query(Package).one()
        assert package.external_package_id == "AB12cd34E"
        assert package.status == "delivered"

    def test_con_dos_paquetes_no_adivina(self, session):
        ingest_event(session, self._amazon(message_id="m1", package_id="ENVIO1", status="shipped"))
        ingest_event(session, self._amazon(message_id="m2", package_id="ENVIO2", status="shipped"))
        ingest_event(session, self._amazon(message_id="m3", package_id=None, status="delivered"))

        # 3 paquetes: los dos reales + el de reserva. Un paquete de más es
        # preferible a marcar como entregado el envío equivocado.
        assert session.query(Package).count() == 3
        ids = {p.external_package_id for p in session.query(Package).all()}
        assert ids == {"ENVIO1", "ENVIO2", "order-408-3320942-2576360"}

    def test_sin_paquete_previo_usa_el_id_de_reserva(self, session):
        ingest_event(session, self._amazon(package_id=None))
        assert session.query(Package).one().external_package_id == "order-408-3320942-2576360"


class TestIngestCarrierEvent:
    """GLS no crea paquetes: sólo añade eventos a uno que ya exista."""

    def test_enlaza_por_package_id(self, session):
        ingest_event(session, evento(message_id="m1", status="in_country"))

        creado = ingest_carrier_event(session, {
            "package_id": "315193141453520012",
            "tracking_number": "1316197997",
            "status": "out_for_delivery",
            "status_label_raw": "Tu envío está en reparto",
            "message_id": "gls-1",
            "event_date": EVENT_DATE,
        }, courier="gls")

        assert creado is True
        package = session.query(Package).one()
        assert package.status == "out_for_delivery"
        assert package.courier == "gls"
        assert package.courier_tracking_number == "1316197997"
        assert session.query(Package).count() == 1   # no ha creado otro

    def test_enlaza_por_tracking_cuando_no_hay_package_id(self, session):
        # Caso del email de valoración de GLS: sólo trae el nº de seguimiento.
        ingest_event(session, evento(message_id="m1", courier_tracking_number="1221358275"))

        creado = ingest_carrier_event(session, {
            "package_id": None,
            "tracking_number": "1221358275",
            "status": "delivered",
            "status_label_raw": "¿Cómo ha ido?",
            "message_id": "gls-2",
            "event_date": EVENT_DATE,
        }, courier="gls")

        assert creado is True
        assert session.query(Package).one().status == "delivered"

    def test_descarta_si_no_encuentra_a_quien_enlazarlo(self, session):
        # Mejor perder el evento que colgarlo del paquete equivocado.
        creado = ingest_carrier_event(session, {
            "package_id": "no-existe",
            "tracking_number": "tampoco",
            "status": "delivered",
            "status_label_raw": "Entregado",
            "message_id": "gls-3",
            "event_date": EVENT_DATE,
        }, courier="gls")

        assert creado is False
        assert session.query(Package).count() == 0
        assert session.query(PackageEvent).count() == 0

    def test_deduplica_igual_que_ingest_event(self, session):
        ingest_event(session, evento(message_id="m1"))
        carrier = {
            "package_id": "315193141453520012",
            "tracking_number": "1316197997",
            "status": "out_for_delivery",
            "status_label_raw": "En reparto",
            "message_id": "gls-1",
            "event_date": EVENT_DATE,
        }
        assert ingest_carrier_event(session, carrier, courier="gls") is True
        assert ingest_carrier_event(session, carrier, courier="gls") is False


class TestQueryDeBusqueda:
    """
    Qué le pide exactamente el escaneo a Gmail. Si se añade una tienda o un
    transportista, esto debería fallar hasta actualizarlo a conciencia.
    """

    def test_cubre_los_cuatro_remitentes_de_amazon(self):
        from app.gmail_sync import build_search_query
        query = build_search_query(14)
        for remitente in ("auto-confirm@amazon.es", "confirmar-envio@amazon.es",
                          "shipment-tracking@amazon.es", "order-update@amazon.es"):
            assert f"from:{remitente}" in query

    def test_cubre_aliexpress_y_los_transportistas(self):
        from app.gmail_sync import build_search_query
        query = build_search_query(14)
        assert "from:transaction@notice.aliexpress.com" in query
        assert "from:gls-spain.com" in query
        assert "from:correos.com" in query

    def test_no_busca_los_emails_de_devolucion(self):
        # El parser los ignora, así que ni se piden: menos llamadas a la API.
        from app.gmail_sync import build_search_query
        assert "devolucion@amazon.es" not in build_search_query(14)

    def test_acota_por_fecha(self):
        from app.gmail_sync import build_search_query
        assert build_search_query(10).endswith("newer_than:10d")
        assert build_search_query(14).endswith("newer_than:14d")

    def test_el_primer_escaneo_cubre_los_paquetes_ya_en_vuelo(self):
        # Un paquete de AliExpress tarda de 2 a 5 semanas: la ventana del primer
        # escaneo tiene que dar para recoger los que ya estén en camino cuando
        # conectas la cuenta por primera vez.
        from app.gmail_sync import FIRST_SCAN_DAYS
        assert FIRST_SCAN_DAYS >= 21
