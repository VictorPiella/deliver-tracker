"""
Casos que salieron al conectar Gmail de verdad por primera vez.

Los datos de ejemplo cubrían un puñado de formatos amables. El primer escaneo
real (50 emails) destapó cuatro fallos: cancelaciones marcadas como entregas, un
paquete fantasma por pedido, y dos parsers que no entendían formatos
perfectamente normales.
"""
from datetime import datetime

from app.models import Order, Package, STATUS_CANCELLED, get_session
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


class TestGlsQueNoSePuedeEnlazar:
    """
    GLS sólo reparte, no vende, así que lo ideal es enganchar su evento al
    paquete de la tienda. Pero cuando no hay a qué engancharlo, el evento se
    descartaba en silencio: ni salía en el panel ni contaba como "sin
    reconocer". Los cuatro emails de GLS del buzón real desaparecían así.
    """

    CUERPO_VGL = (
        "Hola! Tu pedido 1349764642 de VGL INTERNATIONAL TRADE MARKET SL con "
        "Nº de seguimiento GLS 1349764642 está en camino."
    )
    CUERPO_ALI = (
        "Hola! Tu pedido 315193141453520012 de Ecommerce con Nº de seguimiento "
        "GLS 1316197997 está en camino."
    )

    def _parse(self, cuerpo, mid="g1"):
        return gls.parse("noreply@comunicaciones.gls-spain.com", "Tu envío",
                         cuerpo, mid, FECHA)

    def test_si_el_pedido_es_el_propio_tracking_no_hay_referencia_de_tienda(self):
        # GLS a veces repite su nº de seguimiento donde iría la referencia de la
        # tienda. Buscar un paquete por él no encontraría nada nunca.
        r = self._parse(self.CUERPO_VGL)
        assert r["package_id"] is None
        assert r["tracking_number"] == "1349764642"

    def test_una_referencia_de_tienda_de_verdad_si_se_conserva(self):
        r = self._parse(self.CUERPO_ALI)
        assert r["package_id"] == "315193141453520012"
        assert r["tracking_number"] == "1316197997"

    def test_se_extrae_el_nombre_de_la_tienda(self):
        assert self._parse(self.CUERPO_VGL)["tienda"] == "VGL INTERNATIONAL TRADE MARKET SL"
        assert self._parse(self.CUERPO_ALI)["tienda"] == "Ecommerce"

    def test_la_entrada_propia_lleva_tracking_y_tienda(self):
        entrada = gls.como_entrada_propia(self._parse(self.CUERPO_VGL))
        assert entrada["source"] == "gls"
        assert entrada["courier"] == "gls"
        assert entrada["courier_tracking_number"] == "1349764642"
        assert entrada["title"] == "VGL INTERNATIONAL TRADE MARKET SL"
        assert entrada["package_id"] == "1349764642"

    def test_un_envio_sin_paquete_al_que_engancharse_acaba_en_el_panel(self, db_path):
        from app.gmail_sync import run_sync

        mensajes = [{
            "id": "gls-vgl", "subject": "Tu envío 1349764642 está en camino",
            "sender": "GLS <noreply@comunicaciones.gls-spain.com>",
            "date": "2026-09-01T10:00:00+00:00",
        }]
        resumen = run_sync(db_path, lambda q: mensajes,
                           lambda m: {"plaintext_body": self.CUERPO_VGL, "html_body": ""},
                           log=lambda *a: None)

        assert resumen["ingested"] == 1
        s = get_session(db_path)
        p = s.query(Package).one()
        assert p.source == "gls"
        assert p.courier_tracking_number == "1349764642"
        assert p.status == "local_carrier"
        assert p.order.title == "VGL INTERNATIONAL TRADE MARKET SL"
        s.close()

    def test_si_el_paquete_de_la_tienda_existe_se_engancha_a_el(self, db_path):
        # El camino bueno: no se crea entrada propia, se enriquece el paquete
        # que ya seguimos.
        from app.gmail_sync import run_sync

        s = get_session(db_path)
        ingest_event(s, {
            "source": "aliexpress", "order_id": "3074309624382839",
            "package_id": "315193141453520012", "status": "in_country",
            "status_label_raw": "en tu pais", "title": "Tapón colador",
            "image_url": None, "message_id": "ali-1", "event_date": EVENT_DATE,
        })
        s.close()

        run_sync(db_path, lambda q: [{
            "id": "gls-ali", "subject": "Tu envío está en camino",
            "sender": "GLS <noreply@comunicaciones.gls-spain.com>",
            "date": "2026-09-01T10:00:00+00:00",
        }], lambda m: {"plaintext_body": self.CUERPO_ALI, "html_body": ""},
            log=lambda *a: None)

        s = get_session(db_path)
        assert s.query(Package).count() == 1        # sigue habiendo UNO
        p = s.query(Package).one()
        assert p.source == "aliexpress"             # el de la tienda, no uno de GLS
        assert p.courier == "gls"
        assert p.courier_tracking_number == "1316197997"
        s.close()

    def test_reescanear_no_duplica_la_entrada_propia(self, db_path):
        from app.gmail_sync import run_sync

        mensajes = [{
            "id": "gls-vgl", "subject": "Tu envío está en camino",
            "sender": "GLS <noreply@comunicaciones.gls-spain.com>",
            "date": "2026-09-01T10:00:00+00:00",
        }]
        cuerpo = lambda m: {"plaintext_body": self.CUERPO_VGL, "html_body": ""}

        run_sync(db_path, lambda q: mensajes, cuerpo, log=lambda *a: None)
        segundo = run_sync(db_path, lambda q: mensajes, cuerpo, log=lambda *a: None)

        assert segundo["ingested"] == 0
        assert segundo["duplicated"] == 1
        assert get_session(db_path).query(Package).count() == 1


class TestLastUpdatedNoSeMueveSola:
    """
    La columna significa "cuando se movió el paquete", no "cuando se escribió la
    fila". Con onupdate=utcnow, restaurar un paquete de la papelera o aprenderle
    el transportista lo dejaba como "actualizado ahora mismo" aunque llevara
    semanas quieto — y el panel, que ordena por esa fecha, mentía.
    """

    def _paquete(self, session, **kw):
        base = {
            "source": "amazon", "order_id": "408-9-9", "package_id": "ENVIO9",
            "status": "shipped", "status_label_raw": "Enviado", "title": "Cosa",
            "image_url": None, "message_id": "m1", "event_date": EVENT_DATE,
        }
        base.update(kw)
        ingest_event(session, base)
        return session.query(Package).one()

    def test_un_evento_si_la_actualiza(self, session):
        p = self._paquete(session)
        assert p.last_updated == EVENT_DATE

    def test_restaurar_de_la_papelera_no_la_toca(self, session):
        from app.cleanup import restore_package, soft_delete_package

        p = self._paquete(session)
        antes = p.last_updated

        soft_delete_package(session, p, log=lambda *a: None)
        session.commit()
        restore_package(session, session.query(Package).one())
        session.commit()

        assert session.query(Package).one().last_updated == antes

    def test_aprender_el_transportista_no_la_toca(self, session):
        p = self._paquete(session)
        antes = p.last_updated

        p.courier = "correos"
        p.courier_tracking_number = "PQ1"
        session.commit()

        assert session.query(Package).one().last_updated == antes

    def test_la_reparacion_la_recalcula_desde_los_eventos(self, session):
        from datetime import timedelta

        from app.sync import reparar_last_updated

        p = self._paquete(session)
        # Se estropea a mano, como hacia el onupdate.
        p.last_updated = EVENT_DATE + timedelta(days=30)
        session.commit()

        assert reparar_last_updated(session, log=lambda *a: None) == 1
        assert session.query(Package).one().last_updated == EVENT_DATE

    def test_la_reparacion_no_toca_lo_que_ya_esta_bien(self, session):
        from app.sync import reparar_last_updated

        self._paquete(session)
        assert reparar_last_updated(session, log=lambda *a: None) == 0


class TestUnEmailConVariosPedidos:
    """
    Amazon parte una compra en varios pedidos cuando los artículos salen de
    sitios distintos, y manda UN solo email de confirmación con un bloque por
    pedido. extract_order_id devuelve sólo la primera coincidencia, así que el
    segundo pedido desaparecía sin dejar rastro.
    """

    CUERPO = (
        "¡Gracias por tu pedido!\n"
        "Llega mañana\n"
        "Victor - Taradell, Barcelona\n"
        "Pedido n.º\n408-0917824-6197118\n"
        "Ver o modificar pedido\n"
        "https://www.amazon.es/your-orders/order-details?orderID=408-0917824-6197118\n"
        "* Adaptador de Cargador inalámbrico Tipo C (2 Unidades)\n"
        " Cantidad: 1\n 11.09 EUR\n"
        "Total\n11.09 EUR\n"
        "Llega el jueves\n"
        "Victor - Taradell, Barcelona\n"
        "Pedido n.º\n408-2438266-2656303\n"
        "Ver o modificar pedido\n"
        "https://www.amazon.es/your-orders/order-details?orderID=408-2438266-2656303\n"
        "* JZ Type-C 5V/2000mA Adaptación del Receptor de Carga inalámbrica Qi\n"
        " Cantidad: 1\n 22.99 EUR\n"
        "Total\n26.62 EUR\n"
    )
    ASUNTO = 'Pedido: “JZ Type-C 5V/2000mA...” y 1 producto más'

    def _parse(self, mid="msg-jz"):
        return amazon.parse("auto-confirm@amazon.es", self.ASUNTO, self.CUERPO, mid, FECHA)

    def test_devuelve_un_evento_por_pedido(self):
        r = self._parse()
        assert isinstance(r, list)
        assert len(r) == 2

    def test_cada_uno_con_su_numero_de_pedido(self):
        assert [e["order_id"] for e in self._parse()] == [
            "408-0917824-6197118", "408-2438266-2656303"]

    def test_cada_uno_con_su_producto(self):
        # El asunto sólo nombra uno de los dos: si el título saliera de ahí,
        # los dos pedidos se llamarían igual.
        titulos = [e["title"] for e in self._parse()]
        assert "Adaptador de Cargador" in titulos[0]
        assert "JZ Type-C" in titulos[1]
        assert titulos[0] != titulos[1]

    def test_cada_uno_con_su_fecha(self):
        assert [e["eta"] for e in self._parse()] == ["mañana", "el jueves"]

    def test_message_id_unico_por_pedido(self):
        # Con el mismo id, la deduplicación descartaría el segundo pedido.
        ids = [e["message_id"] for e in self._parse("abc")]
        assert ids == ["abc-ord0", "abc-ord1"]
        assert len(set(ids)) == 2

    def test_los_dos_acaban_en_la_base(self, session):
        for evento in self._parse():
            ingest_event(session, evento)
        assert session.query(Order).count() == 2
        assert session.query(Package).count() == 2

    def test_un_email_de_un_solo_pedido_sigue_devolviendo_un_dict(self):
        cuerpo = ("Llega el martes, 24 de junio\nPedido n.º\n408-3320942-2576360\n"
                  "* Grupo de Seguridad, Válvula\n")
        r = amazon.parse("auto-confirm@amazon.es", 'Pedido: "Grupo de Seguridad"',
                         cuerpo, "m1", FECHA)
        assert isinstance(r, dict)
        assert r["order_id"] == "408-3320942-2576360"
        assert r["title"] == "Grupo de Seguridad, Válvula"


class TestLlegaSinDa:
    def test_llega_cuenta_igual_que_llegada(self):
        # El email de confirmación escribe "Llega mañana"; los de envío,
        # "Llegada entre el...". Con sólo "Llegada" se perdían los primeros.
        assert amazon.extract_eta("Llega mañana\nPedido n.º") == "mañana"
        assert amazon.extract_eta("Llega el jueves\nVictor") == "el jueves"

    def test_el_formato_de_siempre_sigue_valiendo(self):
        assert amazon.extract_eta(
            "Llegada entre el 6 de julio y el 7 de julio\n") == "entre el 6 de julio y el 7 de julio"
        assert amazon.extract_eta("Llegada hoy\n") == "hoy"


class TestElTituloDelBloqueManda:
    """
    El título del asunto es una aproximación: en un email con varios pedidos
    nombra sólo a uno, así que a los demás les pondría el producto equivocado.
    El del bloque de cada pedido es el bueno y tiene que poder pisar al otro.
    """

    def test_el_bloque_marca_su_titulo_como_preciso(self):
        cuerpo = "Llega mañana\nPedido n.º\n408-0917824-6197118\n* Producto de verdad\n"
        r = amazon.parse("auto-confirm@amazon.es", 'Pedido: "Nombre del asunto"',
                         cuerpo, "m1", FECHA)
        assert r["title"] == "Producto de verdad"
        assert r["title_preciso"] is True

    def test_sin_bloque_el_titulo_no_es_preciso(self):
        r = amazon.parse("auto-confirm@amazon.es", 'Pedido: "Nombre del asunto"',
                         "", "m1", FECHA)
        assert r["title_preciso"] is False

    def test_un_titulo_preciso_corrige_al_anterior(self, session):
        # Primero entra el nombre sacado del asunto...
        ingest_event(session, {
            "source": "amazon", "order_id": "408-0917824-6197118", "package_id": None,
            "status": "ordered", "status_label_raw": "Pedido",
            "title": "Nombre del asunto", "image_url": None,
            "message_id": "m1", "event_date": EVENT_DATE,
        })
        assert session.query(Order).one().title == "Nombre del asunto"

        # ...y despues el del bloque, que es el bueno.
        ingest_event(session, {
            "source": "amazon", "order_id": "408-0917824-6197118", "package_id": None,
            "status": "ordered", "status_label_raw": "Pedido",
            "title": "Producto de verdad", "title_preciso": True, "image_url": None,
            "message_id": "m2", "event_date": EVENT_DATE,
        })
        assert session.query(Order).one().title == "Producto de verdad"

    def test_uno_impreciso_no_pisa_al_que_ya_habia(self, session):
        ingest_event(session, {
            "source": "amazon", "order_id": "408-0917824-6197118", "package_id": None,
            "status": "ordered", "status_label_raw": "Pedido",
            "title": "Producto de verdad", "title_preciso": True, "image_url": None,
            "message_id": "m1", "event_date": EVENT_DATE,
        })
        ingest_event(session, {
            "source": "amazon", "order_id": "408-0917824-6197118", "package_id": None,
            "status": "shipped", "status_label_raw": "Enviado",
            "title": "(sin título)", "image_url": None,
            "message_id": "m2", "event_date": EVENT_DATE,
        })
        assert session.query(Order).one().title == "Producto de verdad"


class TestUnaImagenPorPedido:
    """
    En un email con varios pedidos, cada uno trae SU imagen. Al principio se
    cogía una sola y se le daba al primero, así que el segundo salía con el
    cuadrito gris de "sin foto".
    """

    HTML = (
        '<div>408-0917824-6197118</div>'
        '<img class="productImage" src="https://m.media-amazon.com/images/I/613y3lyKdwL._SS90_.jpg">'
        '<a href="...orderID=408-0917824-6197118">Ver</a>'
        '<div>408-2438266-2656303</div>'
        '<img class="productImage" src="https://m.media-amazon.com/images/I/51BFkt-fH-L._SS90_.jpg">'
    )

    def test_cada_pedido_recibe_la_suya(self):
        m = amazon.imagenes_por_pedido(self.HTML)
        assert "613y3lyKdwL" in m["408-0917824-6197118"]
        assert "51BFkt-fH-L" in m["408-2438266-2656303"]

    def test_se_pide_la_version_nitida(self):
        # El email trae miniaturas de 90px; el panel las muestra más grandes.
        m = amazon.imagenes_por_pedido(self.HTML)
        assert all("_SS90_" not in u for u in m.values())
        assert all("._SL320_." in u for u in m.values())

    def test_los_banners_de_recomendados_no_cuentan(self):
        # Esos van sin class="productImage" y con sufijo _SR276,276_.
        html = (self.HTML +
                '<img src="https://m.media-amazon.com/images/I/41KZcsXuztL._SR276,276_.jpg">')
        m = amazon.imagenes_por_pedido(html)
        assert all("_SR276" not in (u or "") for u in m.values())

    def test_un_pedido_sin_imagen_no_roba_la_del_siguiente(self):
        html = ('<div>408-1111111-1111111</div>'
                '<div>408-2222222-2222222</div>'
                '<img class="productImage" src="https://m.media-amazon.com/images/I/AAA._SS90_.jpg">')
        m = amazon.imagenes_por_pedido(html)
        # Al primero le toca la primera imagen que haya por detrás, que es la
        # misma; lo que importa es que el segundo no se quede sin ninguna.
        assert m["408-2222222-2222222"] is not None

    def test_sin_html_no_revienta(self):
        assert amazon.imagenes_por_pedido("") == {}
        assert amazon.imagenes_por_pedido("<p>nada</p>") == {}

    def test_el_parse_completo_da_una_imagen_a_cada_uno(self):
        cuerpo = (
            "Llega mañana\nPedido n.º\n408-0917824-6197118\n* Adaptador\n"
            "Llega el jueves\nPedido n.º\n408-2438266-2656303\n* JZ Type-C\n"
        )
        eventos = amazon.parse("auto-confirm@amazon.es", "Pedido: algo y 1 producto más",
                               cuerpo, "m1", FECHA, html_body=self.HTML)
        imgs = [e["image_url"] for e in eventos]
        assert all(imgs), "algún pedido se ha quedado sin imagen"
        assert imgs[0] != imgs[1]
