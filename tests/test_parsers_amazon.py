"""
Tests del parser de Amazon, sobre asuntos y cuerpos reales (los mismos que
alimentan app/mock_gmail.py).
"""
from app.parsers import amazon

from conftest import EVENT_DATE


SHIPPED_BODY = (
    "Mis pedidos\n¡Tu paquete se ha enviado!\nPedido\nEnviado\nEn reparto\nEntregado\n"
    "Llegada entre el 6 de julio y el 7 de julio\nVictor - Taradell, Barcelona\n"
    "Pedido n.º\n408-2435062-0199514\nSeguimiento del envío\n"
    "https://www.amazon.es/progress-tracker/package?_encoding=UTF8&orderId=408-2435062-0199514"
    "&packageIndex=0&shipmentId=DLMJWgm4J&vt=NOTIFICATIONS&ref_=p_btn_fed_track_package\n"
)

SHIPPED_HTML = (
    '<img class="productImage" width="122" '
    'src="https://m.media-amazon.com/images/I/61YAm9AW-OL._SS90_.jpg" '
    'alt="WOLTU Mesitas de Noche">'
)


class TestMatching:
    def test_reconoce_los_cuatro_remitentes(self):
        for sender in ("auto-confirm@amazon.es", "confirmar-envio@amazon.es",
                       "shipment-tracking@amazon.es", "order-update@amazon.es"):
            assert amazon.matches(sender), sender

    def test_reconoce_remitente_con_nombre_para_mostrar(self):
        assert amazon.matches('"Amazon.es" <confirmar-envio@amazon.es>')

    def test_ignora_remitentes_desconocidos(self):
        assert not amazon.matches("newsletter@ofertas-amazon.es")

    def test_devoluciones_se_ignoran(self):
        # El flujo de devoluciones no es un pedido entrante: matches() sí, pero
        # parse() debe devolver None para que no entre en la base.
        sender = "devolucion@amazon.es"
        assert amazon.matches(sender)
        assert amazon.should_ignore(sender)
        assert amazon.parse(sender, "Tu reembolso de BRIMETI Luces LED", "", "m1", EVENT_DATE) is None


class TestEstados:
    def test_remitente_determina_el_estado(self):
        casos = [
            ("auto-confirm@amazon.es", "ordered"),
            ("confirmar-envio@amazon.es", "shipped"),
            ("shipment-tracking@amazon.es", "out_for_delivery"),
            ("order-update@amazon.es", "delivered"),
        ]
        for sender, esperado in casos:
            resultado = amazon.parse(sender, "Asunto cualquiera", "", "m1", EVENT_DATE)
            assert resultado["status"] == esperado, sender


class TestOrderId:
    def test_lo_saca_del_asunto_cuando_viene(self):
        subject = "Entregado: 1 producto | N.º de pedido 408-3320942-2576360"
        assert amazon.extract_order_id(subject) == "408-3320942-2576360"

    def test_lo_saca_del_cuerpo_anclado_a_la_etiqueta(self):
        assert amazon.extract_order_id("Enviado: algo", SHIPPED_BODY) == "408-2435062-0199514"

    def test_el_asunto_gana_al_cuerpo(self):
        subject = "Entregado: 1 producto | N.º de pedido 408-1111111-1111111"
        assert amazon.extract_order_id(subject, SHIPPED_BODY) == "408-1111111-1111111"

    def test_devuelve_none_si_no_hay_nada(self):
        assert amazon.extract_order_id("Enviado: algo", "sin numeros aqui") is None


class TestShipmentId:
    def test_lo_saca_del_link_de_seguimiento(self):
        assert amazon.extract_shipment_id(SHIPPED_BODY) == "DLMJWgm4J"

    def test_none_si_el_cuerpo_no_lo_trae(self):
        assert amazon.extract_shipment_id("En reparto\nPedido n.º\n408-1-1") is None
        assert amazon.extract_shipment_id("") is None


class TestImagen:
    def test_extrae_la_imagen_del_producto(self):
        url = amazon.extract_image_url(SHIPPED_HTML)
        assert url.startswith("https://m.media-amazon.com/images/I/61YAm9AW-OL.")

    def test_sube_la_resolucion_de_la_miniatura(self):
        # El email trae ._SS90_ (90px). El panel muestra hasta 72px en pantallas
        # normales y el doble en alta densidad, así que pedimos ._SL320_.
        assert "_SS90_" not in amazon.extract_image_url(SHIPPED_HTML)
        assert "._SL320_." in amazon.extract_image_url(SHIPPED_HTML)

    def test_acepta_el_orden_de_atributos_invertido(self):
        html = ('<img src="https://m.media-amazon.com/images/I/41xJ8K2pQrL._SS90_.jpg" '
                'class="productImage">')
        assert "._SL320_." in amazon.extract_image_url(html)

    def test_none_sin_html(self):
        assert amazon.extract_image_url("") is None
        assert amazon.extract_image_url("<p>sin imagen</p>") is None


class TestTitulo:
    def test_quita_el_prefijo_de_estado(self):
        assert amazon.clean_title('Enviado: "WOLTU Mesitas de Noche, Set..."') == "WOLTU Mesitas de Noche, Set..."

    def test_quita_la_coletilla_de_productos_adicionales(self):
        titulo = amazon.clean_title('Pedido: "WOLTU Mesitas de Noche, Set..." y 2 productos más')
        assert titulo == "WOLTU Mesitas de Noche, Set..."

    def test_el_asunto_de_entrega_no_lleva_nombre_de_producto(self):
        # "Entregado: 1 producto | N.º de pedido ..." no contiene el nombre del
        # producto, así que tras limpiarlo no queda nada aprovechable. El título
        # bueno ya lo aportó el email de 'Pedido'/'Enviado' anterior, y sync.py
        # sólo rellena order.title si aún está vacío.
        assert amazon.clean_title(
            "Entregado: 1 producto | N.º de pedido 408-3320942-2576360") == "(sin título)"

    def test_nunca_devuelve_cadena_vacia(self):
        assert amazon.clean_title("Entregado:") == "(sin título)"


class TestParseCompleto:
    def test_email_de_envio_real(self):
        resultado = amazon.parse(
            "confirmar-envio@amazon.es",
            'Enviado: "WOLTU Mesitas de Noche, Set..."',
            SHIPPED_BODY,
            "19f0ce1c15a2981e",
            EVENT_DATE,
            html_body=SHIPPED_HTML,
        )
        assert resultado["source"] == "amazon"
        assert resultado["status"] == "shipped"
        assert resultado["order_id"] == "408-2435062-0199514"
        assert resultado["package_id"] == "DLMJWgm4J"
        assert resultado["title"] == "WOLTU Mesitas de Noche, Set..."
        assert resultado["image_url"].startswith("https://m.media-amazon.com/")
        assert resultado["message_id"] == "19f0ce1c15a2981e"
        assert resultado["event_date"] == EVENT_DATE
        # El asunto original se guarda tal cual para poder depurar después.
        assert resultado["status_label_raw"] == 'Enviado: "WOLTU Mesitas de Noche, Set..."'
