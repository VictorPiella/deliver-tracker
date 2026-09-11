"""
Casos que salieron al conectar Gmail de verdad por primera vez.

Los datos de ejemplo cubrían un puñado de formatos amables. El primer escaneo
real (50 emails) destapó cuatro fallos: cancelaciones marcadas como entregas, un
paquete fantasma por pedido, y dos parsers que no entendían formatos
perfectamente normales.
"""
from datetime import datetime

from app.models import Order, Package, STATUS_CANCELLED
from app.parsers import amazon, correos, gls
from app.sync import apply_status, ingest_event

from conftest import EVENT_DATE

FECHA = datetime(2026, 9, 1, 10, 0)


class TestCancelacionesNoSonEntregas:
    def _amazon(self, **kw):
        base = {
            "source": "amazon", "order_id": "408-5-5", "package_id": "ENVIO1",
            "status": "shipped", "status_label_raw": "x", "title": "Cosa",
            "image_url": None, "message_id": "m1", "event_date": EVENT_DATE,
        }
        base.update(kw)
        return base

    def test_una_cancelacion_manda_sobre_el_estado_anterior(self, session):
        ingest_event(session, self._amazon(message_id="m1", status="out_for_delivery"))
        ingest_event(session, self._amazon(message_id="m2", status=STATUS_CANCELLED))
        assert session.query(Package).one().status == STATUS_CANCELLED

    def test_nada_reanima_un_pedido_cancelado(self, session):
        ingest_event(session, self._amazon(message_id="m1", status=STATUS_CANCELLED))
        ingest_event(session, self._amazon(message_id="m2", status="delivered"))
        assert session.query(Package).one().status == STATUS_CANCELLED

    def test_un_estado_manual_sigue_mandando_sobre_la_cancelacion(self, session):
        ingest_event(session, self._amazon(message_id="m1", status="shipped"))
        p = session.query(Package).one()
        p.status, p.status_is_manual = "delivered", True
        session.commit()

        ingest_event(session, self._amazon(message_id="m2", status=STATUS_CANCELLED))
        assert session.query(Package).one().status == "delivered"

    def test_un_intento_fallido_no_marca_entregado(self, session):
        ingest_event(session, self._amazon(message_id="m1", status="delivery_attempted"))
        assert session.query(Package).one().status == "delivery_attempted"

    def test_pero_la_entrega_posterior_si_avanza(self, session):
        ingest_event(session, self._amazon(message_id="m1", status="delivery_attempted"))
        ingest_event(session, self._amazon(message_id="m2", status="delivered"))
        assert session.query(Package).one().status == "delivered"

    def test_apply_status_devuelve_si_ha_cambiado(self, session):
        ingest_event(session, self._amazon(status="shipped"))
        p = session.query(Package).one()
        assert apply_status(p, STATUS_CANCELLED) is True
        assert apply_status(p, STATUS_CANCELLED) is False   # ya estaba cancelado
        assert apply_status(p, "delivered") is False        # no revive


class TestSinPaqueteFantasma:
    """
    Un pedido con DOS envíos generaba un tercer paquete 'order-XXX' clavado en
    "Pedido realizado". En un escaneo real Gmail devuelve lo más nuevo primero,
    así que el email de "Pedido" llega el último, cuando los envíos ya existen.
    """

    def _amazon(self, **kw):
        base = {
            "source": "amazon", "order_id": "408-5937310-4849958", "package_id": None,
            "status": "ordered", "status_label_raw": "Pedido", "title": "Mars Gaming",
            "image_url": None, "message_id": "m", "event_date": EVENT_DATE,
        }
        base.update(kw)
        return base

    def test_el_pedido_se_engancha_a_un_envio_existente(self, session):
        # Orden real de un escaneo: primero lo nuevo, el "Pedido" al final.
        ingest_event(session, self._amazon(message_id="m1", package_id="TNLRw2z1w", status="delivered"))
        ingest_event(session, self._amazon(message_id="m2", package_id="TgLdwVz1w", status="out_for_delivery"))
        ingest_event(session, self._amazon(message_id="m3", package_id=None, status="ordered"))

        ids = {p.external_package_id for p in session.query(Package).all()}
        assert ids == {"TNLRw2z1w", "TgLdwVz1w"}
        assert "order-408-5937310-4849958" not in ids

    def test_y_no_cambia_el_estado_de_ese_envio(self, session):
        # 'ordered' es el rango más bajo: colgarlo de un envío no le pisa nada.
        ingest_event(session, self._amazon(message_id="m1", package_id="TNLRw2z1w", status="delivered"))
        ingest_event(session, self._amazon(message_id="m2", package_id="TgLdwVz1w", status="out_for_delivery"))
        ingest_event(session, self._amazon(message_id="m3", package_id=None, status="ordered"))

        estados = {p.external_package_id: p.status for p in session.query(Package).all()}
        assert estados == {"TNLRw2z1w": "delivered", "TgLdwVz1w": "out_for_delivery"}

    def test_con_varios_envios_una_entrega_ambigua_sigue_sin_adivinar(self, session):
        # Aquí el paquete de reserva sí hace falta: marcar el envío equivocado
        # como entregado sería peor que tener una entrada de más.
        ingest_event(session, self._amazon(message_id="m1", package_id="ENVIO1", status="shipped"))
        ingest_event(session, self._amazon(message_id="m2", package_id="ENVIO2", status="shipped"))
        ingest_event(session, self._amazon(message_id="m3", package_id=None, status="delivered"))

        assert session.query(Package).count() == 3
        assert session.query(Order).count() == 1


class TestGlsConCualquierTienda:
    CUERPO_REAL = (
        "Hola, Victor Piella Fernandez! Tu pedido 1349764642 de VGL INTERNATIONAL "
        "TRADE MARKET SL con Nº de seguimiento GLS 1349764642 está en camino."
    )

    def test_el_nombre_de_la_tienda_ya_no_tiene_que_ser_Ecommerce(self):
        # Estaba escrito a fuego como "de Ecommerce", así que este email real
        # acababa en la lista de "sin reconocer".
        r = gls.parse("noreply@comunicaciones.gls-spain.com", "Tu envío está en camino",
                      self.CUERPO_REAL, "m1", FECHA)
        assert r is not None
        assert r["tracking_number"] == "1349764642"
        assert r["status"] == "local_carrier"

    def test_el_formato_de_siempre_sigue_funcionando(self):
        cuerpo = ("Tu pedido 315193141453520012 de Ecommerce con Nº de seguimiento "
                  "GLS 1316197997 está en camino.")
        r = gls.parse("no-reply@gls-spain.com", "En camino", cuerpo, "m2", FECHA)
        assert r["package_id"] == "315193141453520012"
        assert r["tracking_number"] == "1316197997"


class TestCorreosSinCssDePorMedio:
    HTML_REAL = (
        "<html><head><style>@font-face { font-family: 'cartero-regular'; "
        "src: url(https://www.market.correos.es/fonts/cartero.woff2) format('woff2'); }"
        "</style><script>var x = 1;</script></head><body>"
        "<p>Hola, Te comunicamos que hemos entregado tu envío PKCLEH0497437010108552K "
        "dirigido a VICTOR PIELLA FERNANDEZ el 31/08/2026 11:54.</p></body></html>"
    )

    def test_el_css_ya_no_se_cuela_en_el_texto(self):
        # _strip_html quitaba las etiquetas pero dejaba el CONTENIDO de <style>,
        # y esos kilobytes de CSS tapaban el texto de verdad.
        texto = correos._strip_html(self.HTML_REAL)
        assert "@font-face" not in texto
        assert "woff2" not in texto
        assert "hemos entregado tu envío" in texto

    def test_hemos_entregado_cuenta_como_entrega(self):
        assert correos.detect_status("hemos entregado tu envío PKCLEH...") == "delivered"

    def test_el_email_real_se_parsea_entero(self):
        r = correos.parse("correos@correos.com", "Información sobre su envío", "",
                          "m1", FECHA, html_body=self.HTML_REAL)
        assert r is not None
        assert r["status"] == "delivered"
        assert r["courier_tracking_number"] == "PKCLEH0497437010108552K"

    def test_las_otras_formas_de_entrega_siguen_valiendo(self):
        assert correos.detect_status("tu envío ha sido entregado") == "delivered"
        assert correos.detect_status("ayer entregamos tu envío") == "delivered"


class TestTitulosDeLosAsuntosNuevos:
    """
    clean_title sólo conocía Pedido/Enviado/En reparto/Entregado, así que el
    título de un pedido cancelado acababa siendo el asunto entero.
    """

    def test_cancelacion_en_plural(self):
        assert amazon.clean_title(
            'Productos cancelados correctamente: 2 “TP-Link RE330 - Repetidor...” y 3 productos más'
        ) == "TP-Link RE330 - Repetidor..."

    def test_cancelacion_en_singular(self):
        assert amazon.clean_title(
            'El producto se ha cancelado correctamente: “Motorola Sound MA1 - el...”'
        ) == "Motorola Sound MA1 - el..."

    def test_intento_de_entrega(self):
        assert amazon.clean_title(
            'Intento de entrega realizado: "Citrato de Magnesio 1545mg..." y 4 productos más'
        ) == "Citrato de Magnesio 1545mg..."

    def test_actualizacion_de_entrega(self):
        assert amazon.clean_title(
            'Actualización de entrega: “2 piezas de decoración...”'
        ) == "2 piezas de decoración..."

    def test_un_numero_que_es_parte_del_nombre_no_se_pierde(self):
        # El recuento va FUERA de las comillas; si va dentro es parte del
        # nombre del producto y tiene que sobrevivir.
        assert amazon.clean_title(
            'Entregado: “4 "by Amazon Granos de Café...”'
        ).startswith("4 ")

    def test_los_prefijos_de_siempre_siguen_funcionando(self):
        assert amazon.clean_title('En reparto: “JSAUX mag-Safe Cargador...”') == "JSAUX mag-Safe Cargador..."
        assert amazon.clean_title('Enviado: "WOLTU Mesitas de Noche, Set..."') == "WOLTU Mesitas de Noche, Set..."
