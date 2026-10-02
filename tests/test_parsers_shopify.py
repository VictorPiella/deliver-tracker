"""
Plantilla estándar de Shopify.

El caso raro de este parser: no hay remitente al que agarrarse. Cada tienda
Shopify manda desde su propio dominio, así que reconocer el email es cosa del
cuerpo, no del From. Eso corta en las dos direcciones y los tests van sobre
todo a eso: que reconozca las plantillas de verdad, y que NO se trague todo lo
demás que lleve "order confirmed" en el asunto.
"""
from datetime import datetime

from app.parsers import shopify

FECHA = datetime(2026, 9, 25, 10, 0, 0)
REMITENTE = "Mi Tienda <pedidos@mitienda.com>"


def plantilla(cuerpo_extra: str = "", numero: str = "10239") -> str:
    """
    Trozo representativo de la plantilla de notificaciones de Shopify, con las
    clases que usa de verdad.
    """
    return f"""
    <table class="row">
      <td>
        <h2 class="order-number">Order #{numero}</h2>
        <table class="row order-list">
          <tr>
            <td class="order-list__image-cell">
              <img src="https://cdn.shopify.com/s/files/1/0000/products/cargador.jpg?v=1"
                   align="left" width="60" height="60" class="order-list__product-image" />
            </td>
            <td class="order-list__product-description-cell">
              <span class="order-list__item-title">Cargador MagSafe 15W&nbsp;&times;&nbsp;1</span><br />
            </td>
          </tr>
        </table>
        {cuerpo_extra}
      </td>
    </table>
    """


class TestReconocerLaPlantilla:
    def test_el_asunto_del_usuario_pasa_el_filtro_barato(self):
        assert shopify.posible("Order #10239 confirmed")

    def test_tambien_las_demas_plantillas(self):
        assert shopify.posible("A shipment from order #10239 is on the way")
        assert shopify.posible("Your order is out for delivery")
        assert shopify.posible("Your order has been delivered")
        assert shopify.posible("Order #10239 canceled")
        assert shopify.posible("Pedido #10239 confirmado")

    def test_el_filtro_barato_no_deja_pasar_cualquier_cosa(self):
        assert not shopify.posible("Rebajas de otoño: hasta un 50%")
        assert not shopify.posible("Tu factura de septiembre")
        assert not shopify.posible("")

    def test_hacen_falta_dos_marcas_para_dar_por_bueno_el_cuerpo(self):
        """
        Una sola marca no vale: "shopify.com" sale en el pie de cualquier email
        de una tienda Shopify, newsletters incluidas.
        """
        assert not shopify.es_shopify('<a href="https://shopify.com/policies">Aviso legal</a>')
        assert shopify.es_shopify(plantilla())

    def test_un_cuerpo_vacio_no_es_shopify(self):
        assert not shopify.es_shopify("", "")


class TestPedidoConfirmado:
    """El email del que salió todo esto: "Order #10239 confirmed"."""

    def parsear(self, **kw):
        return shopify.parse(
            kw.get("sender", REMITENTE),
            kw.get("subject", "Order #10239 confirmed"),
            kw.get("body_text", "Thank you for your purchase!"),
            "msg-1", FECHA,
            html_body=kw.get("html_body", plantilla()),
        )

    def test_se_entiende(self):
        assert self.parsear() is not None

    def test_estado_y_fuente(self):
        r = self.parsear()
        assert r["source"] == "shopify"
        assert r["status"] == "ordered"

    def test_saca_el_nombre_del_producto_sin_la_cantidad(self):
        # En el HTML pone "Cargador MagSafe 15W&nbsp;&times;&nbsp;1".
        assert self.parsear()["title"] == "Cargador MagSafe 15W"

    def test_saca_la_imagen(self):
        assert self.parsear()["image_url"].endswith("cargador.jpg?v=1")

    def test_el_titulo_es_preciso_y_puede_pisar_a_los_de_relleno(self):
        assert self.parsear()["title_preciso"] is True

    def test_guarda_el_asunto_tal_cual(self):
        assert self.parsear()["status_label_raw"] == "Order #10239 confirmed"

    def test_la_version_en_castellano_tambien(self):
        r = self.parsear(subject="Pedido #10239 confirmado")
        assert r["status"] == "ordered"
        assert r["order_id"] == "mitienda.com#10239"


class TestElNumeroLlevaLaTiendaDelante:
    """
    Lo más importante del parser. "#1001" no identifica nada por sí solo: es el
    pedido número mil y uno DE UNA TIENDA. Dos tiendas distintas tienen las dos
    su #1001, y sin la tienda delante acabarían siendo el mismo paquete.
    """

    def test_el_order_id_lleva_el_dominio(self):
        r = shopify.parse(REMITENTE, "Order #10239 confirmed", "", "m", FECHA,
                          html_body=plantilla())
        assert r["order_id"] == "mitienda.com#10239"
        assert r["package_id"] == "mitienda.com#10239"

    def test_dos_tiendas_con_el_mismo_numero_no_se_mezclan(self):
        a = shopify.parse("Tienda A <hola@tienda-a.com>", "Order #1001 confirmed",
                          "", "m1", FECHA, html_body=plantilla(numero="1001"))
        b = shopify.parse("Tienda B <hola@tienda-b.es>", "Order #1001 confirmed",
                          "", "m2", FECHA, html_body=plantilla(numero="1001"))
        assert a["order_id"] != b["order_id"]

    def test_el_subdominio_de_envio_no_cambia_la_tienda(self):
        """
        Muchas tiendas mandan desde 'email.mitienda.com' o 'mail.mitienda.com'.
        Si el subdominio contara, el email de confirmación y el de envío podrían
        acabar en dos paquetes distintos.
        """
        a = shopify.parse("T <pedidos@mitienda.com>", "Order #77 confirmed",
                          "", "m1", FECHA, html_body=plantilla(numero="77"))
        b = shopify.parse("T <envios@email.mitienda.com>",
                          "A shipment from order #77 is on the way",
                          "", "m2", FECHA, html_body=plantilla(numero="77"))
        assert a["order_id"] == b["order_id"] == "mitienda.com#77"

    def test_un_remitente_sin_nombre_tambien_vale(self):
        r = shopify.parse("pedidos@mitienda.com", "Order #10239 confirmed",
                          "", "m", FECHA, html_body=plantilla())
        assert r["order_id"] == "mitienda.com#10239"


class TestLasDemasPlantillas:
    def test_envio(self):
        r = shopify.parse(REMITENTE, "A shipment from order #10239 is on the way",
                          "Your order is on the way", "m", FECHA,
                          html_body=plantilla())
        assert r["status"] == "shipped"
        assert r["order_id"] == "mitienda.com#10239"

    def test_en_reparto_saca_el_numero_del_cuerpo(self):
        """El asunto de reparto no lleva número: hay que ir a buscarlo dentro."""
        r = shopify.parse(REMITENTE, "Your order is out for delivery",
                          "Order #10239 is out for delivery", "m", FECHA,
                          html_body=plantilla())
        assert r["status"] == "out_for_delivery"
        assert r["order_id"] == "mitienda.com#10239"

    def test_entregado(self):
        r = shopify.parse(REMITENTE, "Your order has been delivered",
                          "Order #10239 has been delivered", "m", FECHA,
                          html_body=plantilla())
        assert r["status"] == "delivered"

    def test_entregado_gana_a_en_camino(self):
        """
        El email de entrega puede decir "on the way" en el cuerpo. Si ganara ese
        patrón, un paquete entregado volvería a "Enviado".
        """
        r = shopify.parse(REMITENTE, "Your order has been delivered",
                          "Order #10239 was on the way and has been delivered",
                          "m", FECHA, html_body=plantilla())
        assert r["status"] == "delivered"

    def test_cancelado(self):
        r = shopify.parse(REMITENTE, "Order #10239 canceled", "", "m", FECHA,
                          html_body=plantilla())
        assert r["status"] == "cancelled"

    def test_el_numero_de_seguimiento_si_viene(self):
        r = shopify.parse(REMITENTE, "A shipment from order #10239 is on the way",
                          "Tracking number: 1Z999AA10123456784", "m", FECHA,
                          html_body=plantilla())
        assert r["courier_tracking_number"] == "1Z999AA10123456784"

    def test_sin_numero_de_seguimiento_no_se_inventa_ninguno(self):
        r = shopify.parse(REMITENTE, "Order #10239 confirmed", "", "m", FECHA,
                          html_body=plantilla())
        assert r["courier_tracking_number"] is None


class TestLoQueDebeRechazar:
    """
    La otra mitad del trabajo. El filtro del asunto es amplio a propósito, así
    que aquí llega bastante email que no es de Shopify, y tragárselo sería peor
    que no leer ninguno: aparecerían paquetes fantasma en el panel.
    """

    def test_una_newsletter_con_el_asunto_parecido(self):
        assert shopify.parse(
            "Ofertas <news@otracosa.com>", "Your order is on the way",
            "Mira nuestras novedades", "m", FECHA,
            html_body="<html><body><h1>Novedades</h1></body></html>") is None

    def test_un_email_de_shopify_que_no_es_de_un_pedido(self):
        # Pie de página de Shopify pero ninguna marca de la plantilla de pedido.
        assert shopify.parse(
            REMITENTE, "Your order is on the way", "",
            "m", FECHA,
            html_body='<a href="https://shopify.com/legal">Legal</a>') is None

    def test_plantilla_buena_pero_asunto_que_no_dice_nada(self):
        assert shopify.parse(REMITENTE, "Gracias por tu compra", "", "m", FECHA,
                             html_body=plantilla()) is None

    def test_plantilla_buena_sin_numero_de_pedido_por_ningun_lado(self):
        """
        Sin número no hay a qué colgar el evento. Mejor no crear nada que crear
        un paquete que no se puede volver a encontrar.
        """
        html = plantilla().replace("Order #10239", "Tu pedido")
        assert shopify.parse(REMITENTE, "Your order is out for delivery", "",
                             "m", FECHA, html_body=html) is None


class TestCuandoElEmailNoDiceQueHasComprado:
    def test_se_usa_el_nombre_de_la_tienda_como_titulo(self):
        """
        Los emails de envío/entrega no listan los productos. Antes que dejar el
        título vacío, al menos que se vea de quién es el paquete.
        """
        html = plantilla().replace("order-list__item-title", "otra-clase")
        r = shopify.parse(REMITENTE, "Your order has been delivered",
                          "Order #10239 has been delivered", "m", FECHA,
                          html_body=html)
        assert r["title"] == "Mi Tienda · pedido #10239"

    def test_y_ese_titulo_de_relleno_no_pisa_al_bueno(self):
        html = plantilla().replace("order-list__item-title", "otra-clase")
        r = shopify.parse(REMITENTE, "Your order has been delivered",
                          "Order #10239 has been delivered", "m", FECHA,
                          html_body=html)
        assert r["title_preciso"] is False


class TestLaImagenAunqueCambienElOrdenDeLosAtributos:
    def test_class_antes_del_src(self):
        html = ('<td class="order-list__image-cell">'
                '<img class="order-list__product-image" src="https://cdn.shopify.com/a.jpg">'
                '</td><span class="order-list__item-title">Cosa</span>')
        assert shopify.extraer_imagen(html) == "https://cdn.shopify.com/a.jpg"

    def test_src_antes_del_class(self):
        html = ('<img src="https://cdn.shopify.com/b.jpg" class="order-list__product-image">'
                '<span class="order-list__item-title">Cosa</span>')
        assert shopify.extraer_imagen(html) == "https://cdn.shopify.com/b.jpg"

    def test_sin_imagen_devuelve_none(self):
        assert shopify.extraer_imagen("<p>nada</p>") is None


class TestElEmailLlegaHastaElPanel:
    """
    De extremo a extremo por gmail_sync, que es donde vive lo delicado: Shopify
    entra por el asunto y no por el remitente, así que hay que comprobar que la
    query lo busca, que se le pide el cuerpo, y que no se cuela por la rama de
    otro parser.
    """

    def mensaje(self, subject="Order #10239 confirmed", id="sho-1",
                sender=REMITENTE):
        return {"id": id, "subject": subject, "sender": sender,
                "date": "2026-09-25T10:00:00+00:00"}

    def cuerpo(self, html=None, texto="Thank you for your purchase!"):
        return lambda m: {"plaintext_body": texto, "html_body": html or plantilla()}

    def test_la_query_busca_los_asuntos_de_shopify(self):
        from app.gmail_sync import build_search_query

        q = build_search_query(14)
        assert "confirmed" in q
        assert "out for delivery" in q

    def test_el_pedido_acaba_guardado(self, db_path):
        from app.gmail_sync import run_sync
        from app.models import Package, get_session

        resumen = run_sync(db_path, lambda q: [self.mensaje()], self.cuerpo(),
                           log=lambda *a: None)

        assert resumen["ingested"] == 1
        s = get_session(db_path)
        p = s.query(Package).one()
        assert p.source == "shopify"
        assert p.status == "ordered"
        assert p.order.title == "Cargador MagSafe 15W"
        assert p.external_package_id == "mitienda.com#10239"
        s.close()

    def test_confirmacion_y_envio_son_el_mismo_paquete(self, db_path):
        from app.gmail_sync import run_sync
        from app.models import Package, get_session

        run_sync(db_path, lambda q: [self.mensaje()], self.cuerpo(),
                 log=lambda *a: None)
        run_sync(db_path,
                 lambda q: [self.mensaje(
                     subject="A shipment from order #10239 is on the way",
                     id="sho-2")],
                 self.cuerpo(texto="Your order is on the way"),
                 log=lambda *a: None)

        s = get_session(db_path)
        assert s.query(Package).count() == 1
        p = s.query(Package).one()
        assert p.status == "shipped"
        # El nombre del producto sólo venía en el de confirmación: no se pierde.
        assert p.order.title == "Cargador MagSafe 15W"
        s.close()

    def test_reescanear_no_duplica(self, db_path):
        from app.gmail_sync import run_sync
        from app.models import Package, get_session

        run_sync(db_path, lambda q: [self.mensaje()], self.cuerpo(), log=lambda *a: None)
        segundo = run_sync(db_path, lambda q: [self.mensaje()], self.cuerpo(),
                           log=lambda *a: None)

        assert segundo["ingested"] == 0
        s = get_session(db_path)
        assert s.query(Package).count() == 1
        s.close()

    def test_un_email_que_no_es_de_shopify_no_ensucia_sin_reconocer(self, db_path):
        """
        El filtro del asunto es amplio: si cada email que lo cruza acabara en
        "sin reconocer", esa pantalla no serviría para nada. Va a irrelevantes.
        """
        from app.gmail_sync import run_sync
        from app.models import Package, UnparsedEmail, get_session

        resumen = run_sync(
            db_path,
            lambda q: [self.mensaje(subject="Your order is on the way",
                                    sender="Ofertas <news@otracosa.com>")],
            self.cuerpo(html="<html><body>Novedades</body></html>", texto="Novedades"),
            log=lambda *a: None)

        assert resumen["ingested"] == 0
        s = get_session(db_path)
        assert s.query(Package).count() == 0
        assert s.query(UnparsedEmail).count() == 0
        s.close()

    def test_no_le_roba_los_emails_a_amazon(self, db_path):
        """
        "Tu pedido ... está en camino" de Amazon cruza el filtro de asunto de
        Shopify. Si Shopify se lo quedara, lo tiraría a la basura y el paquete
        dejaría de avanzar.
        """
        from app.gmail_sync import run_sync
        from app.models import Package, get_session

        run_sync(
            db_path,
            lambda q: [{"id": "amz-1",
                        "subject": "Tu pedido de Amazon está en camino",
                        "sender": "shipment-tracking@amazon.es",
                        "date": "2026-09-25T10:00:00+00:00"}],
            lambda m: {"plaintext_body": "Pedido n.º 406-0254524-0145135", "html_body": ""},
            log=lambda *a: None)

        s = get_session(db_path)
        paquetes = s.query(Package).all()
        assert all(p.source == "amazon" for p in paquetes)
        s.close()

    def test_se_ve_en_el_panel(self, client, db_path):
        from app.gmail_sync import run_sync

        run_sync(db_path, lambda q: [self.mensaje()], self.cuerpo(),
                 log=lambda *a: None)

        html = client.get("/").get_data(as_text=True)
        assert "Cargador MagSafe 15W" in html
        assert "chip-shopify" in html

    def test_no_pone_un_boton_de_seguimiento_que_no_lleva_a_ningun_sitio(self):
        from app.tracking import url_de_seguimiento

        assert url_de_seguimiento("shopify", "mitienda.com#10239") is None


class TestLoQueEnseñoElEmailDeVerdad:
    """
    Comprobado contra el email real que motivó el parser (Smdge, #10239) y
    contra los 43 correos del buzón que cruzan su filtro de asunto. Los
    nombres de aquí son inventados a propósito: lo que se compra no tiene por
    qué acabar en un repositorio público. Lo que se conserva es la forma.
    """

    def item(self, titulo, variante=None, imagen="https://cdn.shopify.com/x.png"):
        v = (f'<span class="order-list__item-variant">{variante}</span>'
             if variante else "")
        return (f'<td class="order-list__image-cell">'
                f'<img src="{imagen}" class="order-list__product-image"/></td>'
                f'<td class="order-list__product-description-cell">'
                f'<span class="order-list__item-title">{titulo}&nbsp;&times;&nbsp;1</span>{v}</td>')

    def pedido(self, *items, numero="10239"):
        return (f'<h2 class="order-number">Order #{numero}</h2>'
                f'<div class="customer-info__item">x</div>'
                + "".join(items))

    def test_un_pedido_de_cuatro_lineas_no_se_presenta_como_una(self):
        """
        El pedido real traía cuatro artículos. Quedarse con el primero hacía
        que el panel enseñara un solo producto para un paquete con cuatro
        dentro: no es mentira, pero tampoco es verdad, que es lo peor.
        """
        html = self.pedido(
            self.item("Juego de cartas", "Edición A"),
            self.item("Juego de cartas", "Edición B"),
            self.item("Juego de cartas", "Edición C"),
            self.item("Guía completa"),
        )
        r = shopify.parse(REMITENTE, "Order #10239 confirmed", "", "m", FECHA,
                          html_body=html)
        assert r["title"] == "Juego de cartas y 3 productos más"

    def test_dos_lineas_lo_dice_en_singular(self):
        html = self.pedido(self.item("Cosa uno"), self.item("Cosa dos"))
        r = shopify.parse(REMITENTE, "Order #10239 confirmed", "", "m", FECHA,
                          html_body=html)
        assert r["title"] == "Cosa uno y 1 producto más"

    def test_con_una_sola_linea_la_variante_cabe_y_dice_algo(self):
        html = self.pedido(self.item("Juego de cartas", "Edición A"))
        r = shopify.parse(REMITENTE, "Order #10239 confirmed", "", "m", FECHA,
                          html_body=html)
        assert r["title"] == "Juego de cartas · Edición A"

    def test_pero_no_se_repite_si_ya_esta_en_el_nombre(self):
        html = self.pedido(self.item("Juego de cartas Edición A", "Edición A"))
        r = shopify.parse(REMITENTE, "Order #10239 confirmed", "", "m", FECHA,
                          html_body=html)
        assert r["title"] == "Juego de cartas Edición A"

    def test_se_listan_todos_los_articulos(self):
        html = self.pedido(self.item("Uno"), self.item("Dos"), self.item("Tres"))
        assert shopify.extraer_articulos(html) == ["Uno", "Dos", "Tres"]

    def test_la_imagen_es_la_del_primer_articulo(self):
        html = self.pedido(
            self.item("Uno", imagen="https://cdn.shopify.com/primera.png"),
            self.item("Dos", imagen="https://cdn.shopify.com/segunda.png"))
        r = shopify.parse(REMITENTE, "Order #10239 confirmed", "", "m", FECHA,
                          html_body=html)
        assert r["image_url"] == "https://cdn.shopify.com/primera.png"


class TestLasColisionesRealesDelBuzon:
    """
    De los 43 emails del buzón que cruzan el filtro de asunto de Shopify, sólo
    uno lo es. Estos son los choques reales, no imaginados.
    """

    def test_aliexpress_dice_pedido_confirmado_y_no_es_shopify(self, db_path):
        """
        "Pedido 3076348326202839: pedido confirmado" cruza el filtro de asunto.
        Si Shopify se lo quedara, AliExpress dejaría de funcionar.
        """
        from app.gmail_sync import run_sync
        from app.models import Package, get_session

        run_sync(
            db_path,
            lambda q: [{"id": "ali-1",
                        "subject": "Pedido 3076348326202839: pedido confirmado",
                        "sender": "AliExpress <transaction@notice.aliexpress.com>",
                        "date": "2026-09-25T10:00:00+00:00"}],
            lambda m: {"plaintext_body": "", "html_body": ""},
            log=lambda *a: None)

        s = get_session(db_path)
        assert [p.source for p in s.query(Package).all()] == ["aliexpress"]
        s.close()

    def test_una_newsletter_que_dice_en_camino(self, db_path):
        """
        Del buzón: "Algo muy bueno en camino", de una lista de correo. Es
        justo por esto que no basta con el asunto.
        """
        from app.gmail_sync import run_sync
        from app.models import Package, UnparsedEmail, get_session

        run_sync(
            db_path,
            lambda q: [{"id": "news-1", "subject": "Algo muy bueno en camino",
                        "sender": "Alguien <hola@suboletin.com>",
                        "date": "2026-09-25T10:00:00+00:00"}],
            lambda m: {"plaintext_body": "Esta semana te cuento...",
                       "html_body": "<html><body><p>Hola</p></body></html>"},
            log=lambda *a: None)

        s = get_session(db_path)
        assert s.query(Package).count() == 0
        assert s.query(UnparsedEmail).count() == 0
        s.close()


class TestElEnvioNoPisaLoQueDiceElPedido:
    """
    Del email de envío real: el pedido tenía CUATRO líneas y su email de envío
    sólo listaba TRES — "The last items in your order are on the way", o sea un
    envío parcial. Como los dos títulos se daban por precisos, el del envío
    pisaba al del pedido y el panel acababa enseñando menos cosas de las que se
    habían comprado.
    """

    def item(self, titulo):
        return (f'<td class="order-list__image-cell">'
                f'<img src="https://cdn.shopify.com/x.png" class="order-list__product-image"/></td>'
                f'<td class="order-list__product-description-cell">'
                f'<span class="order-list__item-title">{titulo}&nbsp;&times;&nbsp;1</span></td>')

    def pedido(self, *titulos):
        return ('<h2 class="order-number">Order #10239</h2>'
                '<div class="customer-info__item">x</div>'
                + "".join(self.item(t) for t in titulos))

    def test_la_confirmacion_si_es_precisa(self):
        r = shopify.parse(REMITENTE, "Order #10239 confirmed", "", "m", FECHA,
                          html_body=self.pedido("A", "B", "C", "D"))
        assert r["title_preciso"] is True
        assert r["title"] == "A y 3 productos más"

    def test_el_envio_no(self):
        r = shopify.parse(REMITENTE, "A shipment from order #10239 is on the way",
                          "", "m", FECHA, html_body=self.pedido("A", "B", "C"))
        assert r["title_preciso"] is False

    def test_y_asi_el_pedido_conserva_su_titulo_completo(self, db_path):
        from app.gmail_sync import run_sync
        from app.models import Package, get_session

        def correo(subject, id):
            return lambda q: [{"id": id, "subject": subject, "sender": REMITENTE,
                               "date": "2026-09-25T10:00:00+00:00"}]

        cuatro, tres = self.pedido("A", "B", "C", "D"), self.pedido("A", "B", "C")
        run_sync(db_path, correo("Order #10239 confirmed", "c1"),
                 lambda m: {"plaintext_body": "", "html_body": cuatro},
                 log=lambda *a: None)
        run_sync(db_path,
                 correo("A shipment from order #10239 is on the way", "e1"),
                 lambda m: {"plaintext_body": "", "html_body": tres},
                 log=lambda *a: None)

        s = get_session(db_path)
        p = s.query(Package).one()
        assert p.status == "shipped"                     # el estado SI avanza
        assert p.order.title == "A y 3 productos más"    # el titulo no se encoge
        s.close()


class TestElTransportistaYSuEnlace:
    def test_se_saca_el_nombre_del_transportista(self):
        """Del email real: "YunExpress tracking number: YT2626900701670433"."""
        assert shopify.extraer_transportista(
            "YunExpress tracking number: YT2626900701670433") == "yunexpress"

    def test_no_se_confunde_una_palabra_cualquiera_con_un_transportista(self):
        assert shopify.extraer_transportista("Your tracking number: ABC123456") is None
        assert shopify.extraer_transportista("Shipment tracking number: ABC123456") is None

    def test_el_transportista_llega_al_paquete(self, db_path):
        from app.gmail_sync import run_sync
        from app.models import Package, get_session

        html = ('<h2 class="order-number">Order #10239</h2>'
                '<div class="customer-info__item">x</div>'
                '<span class="order-list__item-title">Cosa</span>')
        run_sync(db_path,
                 lambda q: [{"id": "e1",
                             "subject": "A shipment from order #10239 is on the way",
                             "sender": REMITENTE,
                             "date": "2026-09-27T10:00:00+00:00"}],
                 lambda m: {"plaintext_body": "YunExpress tracking number: YT2626900701670433",
                            "html_body": html},
                 log=lambda *a: None)

        s = get_session(db_path)
        p = s.query(Package).one()
        assert p.courier == "yunexpress"
        assert p.courier_tracking_number == "YT2626900701670433"
        s.close()


class TestLaUrlQueVieneDentroDelEmail:
    """
    La página de estado del pedido es el único enlace útil que trae el email, y
    no se puede reconstruir: lleva dos tokens que sólo existen ahí. Antes se
    tiraba y el paquete se quedaba sin ningún botón.
    """

    URL = ("https://mitienda.com/95602934106/orders/"
           "eaa95e51c85aed247a761af899e612c5/authenticate?key=shcct_ABC123")

    def cuerpo(self, url=None):
        return f"Order #10239\n\nView your order\n( {url or self.URL} )\n"

    def test_se_saca_del_texto_plano(self):
        assert shopify.extraer_url_de_estado(self.cuerpo()) == self.URL

    def test_se_recompone_si_el_email_la_ha_partido_en_varias_lineas(self):
        """Los emails cortan las URLs largas; la del pedido real medía 975 caracteres."""
        partida = self.URL[:40] + "\n   " + self.URL[40:80] + "\n   " + self.URL[80:]
        assert shopify.extraer_url_de_estado(f"( {partida} )") == self.URL

    def test_un_parentesis_que_no_lleva_la_url_del_pedido_se_ignora(self):
        assert shopify.extraer_url_de_estado(
            "Baja ( https://mitienda.com/unsubscribe?x=1 )") is None

    def test_sin_parentesis_no_hay_url(self):
        assert shopify.extraer_url_de_estado("No hay enlaces aqui") is None

    def test_se_guarda_en_el_paquete(self, db_path):
        from app.gmail_sync import run_sync
        from app.models import Package, get_session

        html = ('<h2 class="order-number">Order #10239</h2>'
                '<div class="customer-info__item">x</div>'
                '<span class="order-list__item-title">Cosa</span>')
        run_sync(db_path,
                 lambda q: [{"id": "c1", "subject": "Order #10239 confirmed",
                             "sender": REMITENTE,
                             "date": "2026-09-25T10:00:00+00:00"}],
                 lambda m: {"plaintext_body": self.cuerpo(), "html_body": html},
                 log=lambda *a: None)

        s = get_session(db_path)
        assert s.query(Package).one().tracking_url == self.URL
        s.close()

    def test_el_panel_la_usa_como_enlace_de_seguimiento(self):
        from app.tracking import url_de_seguimiento

        # Sin ella, Shopify no tiene enlace posible.
        assert url_de_seguimiento("shopify", "mitienda.com#10239") is None
        # Con ella, se usa tal cual.
        assert url_de_seguimiento("shopify", "mitienda.com#10239",
                                  guardada=self.URL) == self.URL

    def test_manda_sobre_la_url_deducida_de_cualquier_fuente(self):
        """
        Si el email dice a dónde ir, eso vale más que cualquier formato deducido
        aquí a partir del número de pedido.
        """
        from app.tracking import url_de_seguimiento

        deducida = url_de_seguimiento("amazon", "406-0254524-0145135")
        assert "amazon.es" in deducida
        assert url_de_seguimiento("amazon", "406-0254524-0145135",
                                  guardada=self.URL) == self.URL

    def test_un_email_mas_nuevo_refresca_la_url(self, db_path):
        """
        Los tokens caducan, así que la del email de envío sustituye a la del de
        confirmación en vez de respetarla.
        """
        from app.models import Package, get_session
        from app.sync import ingest_event

        s = get_session(db_path)
        base = {"source": "shopify", "order_id": "mitienda.com#10239",
                "package_id": "mitienda.com#10239", "status_label_raw": "x",
                "title": "Cosa", "image_url": None, "event_date": FECHA}
        ingest_event(s, {**base, "status": "ordered", "message_id": "m1",
                         "tracking_url": self.URL})
        ingest_event(s, {**base, "status": "shipped", "message_id": "m2",
                         "tracking_url": self.URL + "-NUEVA"})
        assert s.query(Package).one().tracking_url == self.URL + "-NUEVA"
        s.close()

    def test_la_url_no_aparece_en_los_logs(self, db_path):
        """
        Es una credencial: quien la tenga ve el pedido sin identificarse. No
        tiene por qué quedarse escrita en los logs del container.
        """
        from app.gmail_sync import run_sync

        html = ('<h2 class="order-number">Order #10239</h2>'
                '<div class="customer-info__item">x</div>'
                '<span class="order-list__item-title">Cosa</span>')
        lineas = []
        run_sync(db_path,
                 lambda q: [{"id": "c1", "subject": "Order #10239 confirmed",
                             "sender": REMITENTE,
                             "date": "2026-09-25T10:00:00+00:00"}],
                 lambda m: {"plaintext_body": self.cuerpo(), "html_body": html},
                 log=lambda *a: lineas.append(" ".join(str(x) for x in a)))

        registro = "\n".join(lineas)
        assert "shcct_" not in registro
        assert "/orders/" not in registro
