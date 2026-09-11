"""
Tests de los parsers de última milla.

La diferencia clave entre ambos está en si el email trae un identificador que
permita enlazarlo a un paquete ya conocido:
  - GLS sí  -> evento sobre un Package existente (ingest_carrier_event).
  - Correos no -> entrada independiente (ingest_event).
"""
from app.parsers import correos, gls

from conftest import EVENT_DATE


GLS_SENDER = "no-reply@gls-spain.com"
CORREOS_SENDER = "notificaciones@correos.com"


class TestGLS:
    def test_reconoce_los_dos_remitentes_vistos(self):
        assert gls.matches("no-reply@gls-spain.com")
        assert gls.matches("noreply@comunicaciones.gls-spain.com")
        assert not gls.matches("info@otro-courier.es")

    def test_extrae_package_id_y_tracking(self):
        body = ("Hola Victor, tu pedido 315193141453520012 de Ecommerce con Nº de "
                "seguimiento GLS 1316197997 está en camino.")
        resultado = gls.parse(GLS_SENDER, "Tu envío está en camino", body, "m1", EVENT_DATE)
        assert resultado["package_id"] == "315193141453520012"
        assert resultado["tracking_number"] == "1316197997"
        assert resultado["status"] == "local_carrier"

    def test_decodifica_entidades_html_en_el_texto_plano(self):
        # Algunas plantillas de GLS mandan "est&aacute;" sin decodificar en el
        # text/plain; sin unescape el patrón de estado no casaría.
        body = ("Tu pedido 315193141453520012 de Ecommerce con Nº de seguimiento "
                "GLS 1316197997 ya est&aacute; en reparto.")
        resultado = gls.parse(GLS_SENDER, "En reparto", body, "m1", EVENT_DATE)
        assert resultado is not None
        assert resultado["status"] == "out_for_delivery"

    def test_email_de_valoracion_solo_trae_tracking(self):
        # El email post-entrega no lleva package_id; se enlazará por el
        # courier_tracking_number ya guardado (ver test_sync).
        body = "Ayer te entregamos tu envío 1221358275 de Ecommerce. ¿Qué tal fue?"
        resultado = gls.parse(GLS_SENDER, "¿Cómo ha ido?", body, "m1", EVENT_DATE)
        assert resultado["package_id"] is None
        assert resultado["tracking_number"] == "1221358275"
        assert resultado["status"] == "delivered"

    def test_descarta_lo_que_no_reconoce(self):
        assert gls.parse(GLS_SENDER, "Newsletter", "Ofertas de verano", "m1", EVENT_DATE) is None
        assert gls.parse("otro@courier.es", "x", "está en camino", "m1", EVENT_DATE) is None


class TestCorreos:
    def _html(self, cuerpo):
        return f"<html><body><table><tr><td>{cuerpo}</td></tr></table></body></html>"

    def test_reconoce_el_dominio(self):
        assert correos.matches(CORREOS_SENDER)
        assert not correos.matches("no-reply@gls-spain.com")

    def test_lee_el_html_porque_el_texto_plano_viene_vacio(self):
        html = self._html("Tu envío PQ1234567890ES tiene prevista su entrega hoy, "
                          "remitido por AMAZON EU SARL.")
        resultado = correos.parse(CORREOS_SENDER, "Entrega hoy", "", "m1", EVENT_DATE, html_body=html)
        assert resultado is not None
        assert resultado["courier_tracking_number"] == "PQ1234567890ES"
        assert resultado["status"] == "out_for_delivery"

    def test_se_trackea_como_entrada_propia(self):
        # Sin ID compartido con Amazon/AliExpress, el número de envío hace de
        # order_id y package_id a la vez.
        html = self._html("Tu envío PQ1234567890ES ha sido entregado, remitido por AMAZON EU SARL.")
        resultado = correos.parse(CORREOS_SENDER, "Entregado", "", "m1", EVENT_DATE, html_body=html)
        assert resultado["source"] == "correos"
        assert resultado["order_id"] == resultado["package_id"] == "PQ1234567890ES"
        assert resultado["courier"] == "correos"
        assert resultado["status"] == "delivered"

    def test_usa_el_remitente_como_titulo(self):
        # Es la única pista sobre qué es el paquete; el email no trae producto.
        html = self._html("Tu envío PQ1234567890ES será entregado en los próximos días, "
                          "remitido por ALIEXPRESS.")
        resultado = correos.parse(CORREOS_SENDER, "En camino", "", "m1", EVENT_DATE, html_body=html)
        assert resultado["title"] == "ALIEXPRESS"
        assert resultado["status"] == "local_carrier"
        assert resultado["image_url"] is None

    def test_descarta_sin_numero_de_envio(self):
        html = self._html("Tenemos novedades sobre tu entrega de hoy.")
        assert correos.parse(CORREOS_SENDER, "Aviso", "", "m1", EVENT_DATE, html_body=html) is None

    def test_descarta_estado_desconocido(self):
        html = self._html("Tu envío PQ1234567890ES: encuesta de satisfacción.")
        assert correos.parse(CORREOS_SENDER, "Encuesta", "", "m1", EVENT_DATE, html_body=html) is None
