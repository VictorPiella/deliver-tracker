"""
Enlaces a la página de seguimiento de cada tienda/transportista.

Los formatos salen de los emails reales del buzón, no de suponer. El único que
no está verificado contra un email es el de GLS, porque GLS sólo mete enlaces
de redirección opacos y distintos en cada correo.
"""
from app.tracking import (
    requiere_datos_a_mano, url_de_seguimiento, url_del_transportista,
)


class TestAmazon:
    def test_enlaza_al_detalle_del_pedido(self):
        u = url_de_seguimiento("amazon", "406-0254524-0145135")
        assert u == "https://www.amazon.es/your-orders/order-details?orderID=406-0254524-0145135"

    def test_el_parametro_es_orderID_con_D_mayuscula(self):
        # Amazon usa 'orderID' aquí, no 'orderId'. Con la minúscula no carga.
        assert "orderID=" in url_de_seguimiento("amazon", "408-1234567-1234567")

    def test_un_id_de_reserva_no_genera_enlace(self):
        # 'order-408-...' es el id provisional que se inventa sync.py, no un
        # pedido real: el enlace llevaría a un 404.
        assert url_de_seguimiento("amazon", "order-408-1234567-1234567") is None

    def test_sin_pedido_no_hay_enlace(self):
        assert url_de_seguimiento("amazon", None) is None

    def test_un_id_con_formato_raro_se_descarta(self):
        assert url_de_seguimiento("amazon", "12345") is None


class TestAliExpress:
    def test_enlaza_al_detalle_del_pedido(self):
        u = url_de_seguimiento("aliexpress", "3074935419642839")
        assert u == "https://www.aliexpress.com/p/order/detail.html?orderId=3074935419642839"

    def test_no_enlaza_con_el_id_de_reserva(self):
        assert url_de_seguimiento("aliexpress", "order-3074309624382839") is None

    def test_no_enlaza_con_un_tracking_de_courier(self):
        # 'LP00827165784034' es del transportista, no un pedido de AliExpress.
        assert url_de_seguimiento("aliexpress", "LP00827165784034") is None


class TestCorreos:
    def test_enlaza_al_localizador(self):
        u = url_de_seguimiento("correos", "PKCLEH0497437010108552K", None, "PKCLEH0497437010108552K")
        assert "correos.es" in u and "PKCLEH0497437010108552K" in u

    def test_sirve_el_numero_de_paquete_si_no_hay_tracking(self):
        assert url_de_seguimiento("correos", None, "PQ123ES", None) is not None

    def test_sin_numero_no_hay_enlace(self):
        assert url_de_seguimiento("correos", None, None, None) is None


class TestGls:
    """
    GLS no admite enlace directo, y se probaron sus cuatro entradas contra la
    web real: dos redirigen a /not-found, la tercera responde "'Postal code' is
    mandatory to this user" con cualquier numero (roto de su lado, pasa hasta
    desde el formulario de la propia GLS), y la cuarta acepta el numero por URL
    pero no encuentra los envios. La que funciona, /parcel-tracking, es una SPA
    con reCAPTCHA invisible y sin parametros: hay que teclear los datos.
    """

    def test_lleva_a_la_pagina_que_funciona(self):
        u = url_de_seguimiento("gls", None, None, "1349764642")
        assert u == "https://mygls.gls-spain.es/parcel-tracking"

    def test_no_apunta_a_las_entradas_rotas(self):
        u = url_de_seguimiento("gls", None, None, "1349764642")
        assert "/e/" not in u
        assert "tracking_code.php" not in u
        assert "gls-group.com" not in u

    def test_el_enlace_no_lleva_el_numero(self):
        # No sirve de nada ponerselo: esa pagina ignora los parametros de URL.
        assert "1349764642" not in url_de_seguimiento("gls", None, None, "1349764642")

    def test_se_marca_como_que_pide_datos_a_mano(self):
        # El panel usa esto para copiar el nº al portapapeles al pulsar.
        assert requiere_datos_a_mano("gls") is True
        assert requiere_datos_a_mano("amazon") is False
        assert requiere_datos_a_mano(None) is False


class TestTransportista:
    def test_un_amazon_que_trae_correos_enlaza_a_correos(self):
        u = url_del_transportista("correos", "PQ1234567890ES")
        assert u is not None and "correos.es" in u

    def test_sin_transportista_o_sin_numero_no_hay_enlace(self):
        assert url_del_transportista(None, "123") is None
        assert url_del_transportista("correos", None) is None


class TestOrigenDesconocido:
    def test_una_fuente_que_no_conocemos_no_inventa_enlace(self):
        assert url_de_seguimiento("temu", "123456") is None
        assert url_de_seguimiento("", "123456") is None


class TestEnElPanel:
    def test_el_boton_sale_en_la_lista(self, client):
        from conftest import sincronizar
        sincronizar(client)
        html = client.get("/").get_data(as_text=True)
        assert "your-orders/order-details" in html
        # Se abre fuera, y sin dejar que la pestaña nueva toque a la original.
        assert 'rel="noopener noreferrer"' in html

    def test_y_en_el_detalle(self, client):
        from conftest import sincronizar
        sincronizar(client)
        pid = client.get("/api/packages").get_json()[0]["id"]
        html = client.get(f"/package/{pid}").get_data(as_text=True)
        assert "Ver en" in html


class TestAyudaParaGls:
    def test_el_boton_de_gls_lleva_el_numero_para_copiar(self, db_path):
        from app.gmail_sync import run_sync
        from app.web import create_app

        cuerpo = ("Tu pedido 1349764642 de VGL INTERNATIONAL TRADE MARKET SL con "
                  "Nº de seguimiento GLS 1349764642 está en camino.")
        run_sync(db_path, lambda q: [{
            "id": "g1", "subject": "Tu envío está en camino",
            "sender": "GLS <noreply@comunicaciones.gls-spain.com>",
            "date": "2026-09-01T10:00:00+00:00",
        }], lambda m: {"plaintext_body": cuerpo, "html_body": ""}, log=lambda *a: None)

        app = create_app(db_path=db_path, use_mock_gmail=True, enable_worker=False)
        app.config["TESTING"] = True
        html = app.test_client().get("/").get_data(as_text=True)

        assert "mygls.gls-spain.es/parcel-tracking" in html
        assert 'data-copiar="1349764642"' in html
        assert "copia-antes" in html

    def test_el_codigo_postal_se_enseña_si_esta_configurado(self, db_path, monkeypatch):
        import app.web as web
        from app.web import create_app

        from conftest import sincronizar

        monkeypatch.setattr(web, "POSTAL_CODE", "08552")
        app = create_app(db_path=db_path, use_mock_gmail=True, enable_worker=False)
        app.config["TESTING"] = True
        c = app.test_client()
        sincronizar(c)   # el aviso solo se pinta si hay paquetes que listar
        assert 'data-cp="08552"' in c.get("/").get_data(as_text=True)
