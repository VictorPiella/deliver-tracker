"""
Tests del parser de AliExpress: formato individual (un paquete por email) y
formato "merge" (varios paquetes en un email, el único que trae el tracking
number real del courier).
"""
from app.parsers import aliexpress

from conftest import EVENT_DATE


SENDER = "transaction@notice.aliexpress.com"

# Email 'merge' real: dos paquetes, cada uno con su tradeOrderId, trackingNumber,
# estado en texto libre, título e imagen.
MERGE_HTML = (
    '<a href="https://www.aliexpress.com/p/tracking/index.html?_addShare=no&_login=yes'
    '&tradeOrderId=074309624262839&trackingNumber=LP00827165784034&edm_log_data=...">'
    '<table><tbody><tr>'
    '<td class="EDM-MULTIPLE-PACKAGES-item-header" style="...">'
    '<img width="28" height="28" src="https://ae-pic-a1.aliexpress-media.com/kf/Sea49051.png">'
    '<span style="...">En tránsito global</span></td>'
    '<td width="36" height="36" rowspan="2" class="EDM-MULTIPLE-PACKAGES-item-action"><img></td>'
    '</tr><tr><td class="EDM-MULTIPLE-PACKAGES-item-body" style="...">'
    '<table><tbody><tr>'
    '<td width="100" height="100" '
    'background="https://ae-pic-a1.aliexpress-media.com/kf/S5f22d5f83a684c7cb497645f45f1a7f10.jpg" '
    'class="EDM-MULTIPLE-PACKAGES-item-image" style="..."><div>&nbsp;</div><span></span></td>'
    '<td width="420" class="EDM-MULTIPLE-PACKAGES-item-content" style="...">'
    '<div class="EDM-MULTIPLE-PACKAGES-item-title" style="...">Tapón colador para fregad...</div>'
    '<div class="EDM-MULTIPLE-PACKAGES-item-sku" style="..."></div></td>'
    '</tr></tbody></table></td></tr></tbody></table></a>'
    '<a href="https://www.aliexpress.com/p/tracking/index.html?_addShare=no&_login=yes'
    '&tradeOrderId=074309624302839&trackingNumber=LP00825690963370&edm_log_data=...">'
    '<table><tbody><tr>'
    '<td class="EDM-MULTIPLE-PACKAGES-item-header" style="...">'
    '<img width="28" height="28" src="...">'
    '<span style="...">Ha salido de la central</span></td>'
    '<td width="36" height="36" rowspan="2" class="EDM-MULTIPLE-PACKAGES-item-action"><img></td>'
    '</tr><tr><td class="EDM-MULTIPLE-PACKAGES-item-body" style="...">'
    '<table><tbody><tr>'
    '<td width="100" height="100" '
    'background="https://ae-pic-a1.aliexpress-media.com/kf/S7df2fafd06e64d519c65ec7ae271579fi.jpg" '
    'class="EDM-MULTIPLE-PACKAGES-item-image" style="..."><div>&nbsp;</div><span></span></td>'
    '<td width="420" class="EDM-MULTIPLE-PACKAGES-item-content" style="...">'
    '<div class="EDM-MULTIPLE-PACKAGES-item-title" style="...">AC 220V DC 12V 24V 1/2 "3...</div>'
    '<div class="EDM-MULTIPLE-PACKAGES-item-sku" style="...">Normally Closed, 12V, 3/4"</div></td>'
    '</tr></tbody></table></td></tr></tbody></table></a>'
)


def parse(subject, html_body="", message_id="m1"):
    return aliexpress.parse(SENDER, subject, "", message_id, EVENT_DATE, html_body=html_body)


class TestMatching:
    def test_reconoce_el_remitente(self):
        assert aliexpress.matches(SENDER)
        assert aliexpress.matches(f'"AliExpress" <{SENDER}>')

    def test_rechaza_otros(self):
        assert not aliexpress.matches("noreply@amazon.es")
        assert aliexpress.parse(
            "noreply@amazon.es", "Paquete 123: entregado", "", "m1", EVENT_DATE) is None


class TestEstadosIndividuales:
    def test_mapea_cada_asunto_a_su_estado(self):
        casos = [
            ("Pedido 3074309624382839: pedido enviado", "shipped"),
            ("Paquete 315193141453520012: en aduanas", "customs"),
            ("El paquete 315193140438420019 ha pasado la aduana", "customs_cleared"),
            ("Paquete 315193141453520012: salió de la región de origen", "left_origin"),
            ("Paquete 315193140438420019: con transportista local", "local_carrier"),
            ("Paquete 315193141453520012: en tu país/región", "in_country"),
            ("El paquete 315193140438420019 está en el centro de distribución", "at_distribution"),
            ("Paquete 315193140438420019 entregado", "delivered"),
        ]
        for subject, esperado in casos:
            assert aliexpress.detect_status(subject) == esperado, subject

    def test_asunto_desconocido_queda_unknown(self):
        assert aliexpress.detect_status("Paquete 123: algo que no hemos visto nunca") == "unknown"

    def test_actualizacion_generica_queda_unknown(self):
        # "Actualización del paquete AP..." no dice en qué estado está; dejarlo
        # en unknown evita que pise un estado más avanzado ya registrado.
        assert aliexpress.detect_status("Actualización del paquete AP00824363068180") == "unknown"


class TestIdsIndividuales:
    def test_saca_el_package_id_del_asunto(self):
        assert parse("Paquete 315193141453520012: en aduanas")["package_id"] == "315193141453520012"

    def test_variante_sin_dos_puntos(self):
        assert parse("Paquete 315193140438420019 entregado")["package_id"] == "315193140438420019"

    def test_variante_el_paquete_x(self):
        assert parse("El paquete 315193140438420019 ha pasado la aduana")["package_id"] == "315193140438420019"

    def test_formato_ap(self):
        assert parse("Actualización del paquete AP00824363068180")["package_id"] == "AP00824363068180"

    def test_email_de_pedido_usa_el_order_id_como_package_provisional(self):
        # Todavía no hay package_id: AliExpress aún no ha asignado envío.
        resultado = parse("Pedido 3074309624382839: pedido enviado")
        assert resultado["order_id"] == "3074309624382839"
        assert resultado["package_id"] == "order-3074309624382839"


class TestFormatoMerge:
    def test_detecta_el_asunto_merge(self):
        assert aliexpress.is_merge_format("Tus 2 paquetes tienen actualizaciones de entrega")
        assert not aliexpress.is_merge_format("Paquete 315193141453520012: en aduanas")

    def test_devuelve_un_evento_por_paquete(self):
        eventos = parse("Tus 2 paquetes tienen actualizaciones de entrega", MERGE_HTML)
        assert isinstance(eventos, list)
        assert len(eventos) == 2

    def test_extrae_tracking_title_e_imagen_de_cada_bloque(self):
        primero, segundo = parse("Tus 2 paquetes tienen actualizaciones de entrega", MERGE_HTML)

        assert primero["order_id"] == "074309624262839"
        assert primero["courier_tracking_number"] == "LP00827165784034"
        assert primero["package_id"] == "LP00827165784034"
        assert primero["title"] == "Tapón colador para fregad..."
        assert primero["image_url"].endswith("S5f22d5f83a684c7cb497645f45f1a7f10.jpg")
        assert primero["status"] == "left_origin"      # "En tránsito global"

        assert segundo["order_id"] == "074309624302839"
        assert segundo["courier_tracking_number"] == "LP00825690963370"
        assert segundo["status"] == "left_origin"      # "Ha salido de la central"

    def test_message_id_unico_por_paquete(self):
        # Si los dos eventos compartieran message_id, la deduplicación de
        # sync.ingest_event descartaría el segundo paquete del email.
        eventos = parse("Tus 2 paquetes tienen actualizaciones de entrega", MERGE_HTML, message_id="abc")
        ids = [e["message_id"] for e in eventos]
        assert ids == ["abc-pkg0", "abc-pkg1"]
        assert len(set(ids)) == len(ids)

    def test_sin_html_parseable_no_inventa_eventos(self):
        # Si AliExpress cambia la plantilla, mejor no ingerir nada que ingerir
        # eventos vacíos que ensucien la base.
        assert parse("Tus 2 paquetes tienen actualizaciones de entrega", "<p>plantilla nueva</p>") is None


class TestAliExpressTambienEscribeEnIngles:
    """
    Siete emails seguidos de un mismo paquete llegaron en inglés y ninguno se
    entendió: ese envío no existía para el panel. Aparecieron todos en "sin
    reconocer", que es exactamente para lo que está esa pantalla.

    Los asuntos son los del buzón, tal cual.
    """

    def estado(self, asunto):
        return aliexpress.detect_status(asunto)

    def test_los_asuntos_reales_que_no_se_entendian(self):
        casos = [
            ("Package PHBW6T9812926090108552J has cleared customs", "customs_cleared"),
            ("Package PHBW6T9812926090108552J: at customs", "customs"),
            ("Package PHBW6T9812926090108552J: in your country/region", "in_country"),
            ("Package PHBW6T9812926090108552J: left the departure region", "left_origin"),
            ("Order 3076348326202839: collected by the carrier", "local_carrier"),
        ]
        for asunto, esperado in casos:
            assert self.estado(asunto) == esperado, asunto

    def test_aduana_pasada_gana_a_aduana(self):
        """
        "has cleared customs" contiene "customs". Si ganara el patrón de
        aduanas, un paquete que ya ha pasado la aduana retrocedería.
        """
        assert self.estado("Package X has cleared customs") == "customs_cleared"

    def test_sigue_entendiendo_el_castellano(self):
        assert self.estado("Paquete 315193141453520012: en aduanas") == "customs"
        assert self.estado("Pedido 3074309624382839: pedido enviado") == "shipped"
        assert self.estado("Paquete 315193140438420019 entregado") == "delivered"

    def test_el_id_alfanumerico_se_coge(self):
        """
        Con "Paquete\\s+(\\d+)" sólo valían los numéricos, y este no lo es.
        """
        r = aliexpress.parse(aliexpress.SENDER,
                             "Package PHBW6T9812926090108552J has cleared customs",
                             "", "m", EVENT_DATE)
        assert r["package_id"] == "PHBW6T9812926090108552J"
        assert r["status"] == "customs_cleared"

    def test_los_ids_de_siempre_siguen_valiendo(self):
        for asunto, pid in (
            ("Paquete 315193141453520012: en aduanas", "315193141453520012"),
            ("Actualización del paquete AP00824363068180", "AP00824363068180"),
        ):
            assert aliexpress.parse(aliexpress.SENDER, asunto, "", "m", EVENT_DATE)["package_id"] == pid

    def test_una_palabra_no_se_confunde_con_un_id(self):
        """
        Aceptar [A-Za-z0-9] con IGNORECASE hacía que "Package delivered" dejara
        "delivered" de identificador. Se exige que lleve algún dígito.
        """
        r = aliexpress.parse(aliexpress.SENDER, "Package delivered successfully",
                             "", "m", EVENT_DATE)
        assert r["package_id"] != "delivered"

    def test_el_pedido_en_ingles_tambien(self):
        r = aliexpress.parse(aliexpress.SENDER,
                             "Order 3076348326202839: collected by the carrier",
                             "", "m", EVENT_DATE)
        assert r["order_id"] == "3076348326202839"
        assert r["status"] == "local_carrier"

    def test_dejan_de_caer_en_sin_reconocer(self, db_path):
        from app.gmail_sync import run_sync
        from app.models import Package, UnparsedEmail, get_session

        asuntos = [
            "Package PHBW6T9812926090108552J has cleared customs",
            "Package PHBW6T9812926090108552J: at customs",
            "Package PHBW6T9812926090108552J: in your country/region",
        ]
        run_sync(db_path,
                 lambda q: [{"id": f"ali-en-{i}", "subject": a,
                             "sender": "AliExpress <transaction@notice.aliexpress.com>",
                             "date": "2026-10-01T10:00:00+00:00"}
                            for i, a in enumerate(asuntos)],
                 lambda m: {"plaintext_body": "", "html_body": ""},
                 log=lambda *a: None)

        s = get_session(db_path)
        assert s.query(UnparsedEmail).count() == 0
        # Los tres hablan del mismo paquete: uno solo, no tres.
        assert s.query(Package).count() == 1
        p = s.query(Package).one()
        assert p.external_package_id == "PHBW6T9812926090108552J"
        assert p.status == "in_country"      # el mas avanzado de los tres
        s.close()
