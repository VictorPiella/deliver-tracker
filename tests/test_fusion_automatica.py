"""
Juntar solo lo que sin duda es el mismo envío.

Un mismo paquete podía salir dos veces en el panel: la entrada de la tienda y
la del transportista. Unirlas era cosa del usuario, con el botón "Fusionar",
porque hacerlo a ciegas era adivinar.

Por NÚMERO no se adivina nada: un número de seguimiento identifica un envío. Lo
que estos tests sujetan, sobre todo, es la otra mitad — que no se junte nada
por parecerse.
"""
from app.models import Package, PackageEvent, get_session
from app.sync import fusionar_duplicados_por_numero, ingest_event

from conftest import EVENT_DATE

NUMERO = "PHBW6T9812926090108552J"


def paquete(db_path, *, source, package_id, titulo=None, status="shipped",
            message_id=None, tracking=None):
    s = get_session(db_path)
    ingest_event(s, {
        "source": source, "order_id": package_id, "package_id": package_id,
        "status": status, "status_label_raw": "x", "title": titulo,
        "image_url": None, "message_id": message_id or f"m-{source}-{package_id}",
        "event_date": EVENT_DATE,
        "courier_tracking_number": tracking,
    })
    # filter_by(source=...) también: el caso que se prueba es justo que dos
    # paquetes de fuentes distintas lleven el mismo id.
    pid = (s.query(Package)
           .filter_by(external_package_id=package_id, source=source).one().id)
    s.close()
    return pid


class TestElCasoReal:
    """
    El del buzón: PHBW6T98...J entró como AliExpress (con el nombre del
    producto) y otra vez como Correos (con "COE-AE", que es un código de ruta).
    """

    def sembrar(self, db_path):
        a = paquete(db_path, source="aliexpress", package_id=NUMERO,
                    titulo="Cargador MagSafe", message_id="ali-1")
        b = paquete(db_path, source="correos", package_id=NUMERO,
                    titulo="COE-AE", message_id="correos-1", status="out_for_delivery")
        return a, b

    def test_se_quedan_en_uno(self, db_path):
        self.sembrar(db_path)
        s = get_session(db_path)
        assert fusionar_duplicados_por_numero(s, log=lambda *a: None) == 1
        assert s.query(Package).filter(Package.deleted_at.is_(None)).count() == 1
        s.close()

    def test_sobrevive_el_que_tiene_nombre_de_verdad(self, db_path):
        """"COE-AE" no es un nombre, es un código de ruta."""
        self.sembrar(db_path)
        s = get_session(db_path)
        fusionar_duplicados_por_numero(s, log=lambda *a: None)
        vivo = s.query(Package).filter(Package.deleted_at.is_(None)).one()
        assert vivo.title == "Cargador MagSafe"
        s.close()

    def test_no_se_pierde_ningun_evento(self, db_path):
        self.sembrar(db_path)
        s = get_session(db_path)
        antes = s.query(PackageEvent).count()
        fusionar_duplicados_por_numero(s, log=lambda *a: None)
        assert s.query(PackageEvent).count() == antes
        vivo = s.query(Package).filter(Package.deleted_at.is_(None)).one()
        assert len(vivo.events) == 2
        s.close()

    def test_el_estado_mas_avanzado_se_conserva(self, db_path):
        self.sembrar(db_path)
        s = get_session(db_path)
        fusionar_duplicados_por_numero(s, log=lambda *a: None)
        vivo = s.query(Package).filter(Package.deleted_at.is_(None)).one()
        assert vivo.status == "out_for_delivery"
        s.close()

    def test_hacerlo_dos_veces_no_cambia_nada(self, db_path):
        self.sembrar(db_path)
        s = get_session(db_path)
        fusionar_duplicados_por_numero(s, log=lambda *a: None)
        assert fusionar_duplicados_por_numero(s, log=lambda *a: None) == 0
        s.close()


class TestTambienPorElNumeroDelTransportista:
    def test_el_id_de_uno_contra_el_tracking_del_otro(self, db_path):
        """
        No hace falta que coincida el mismo campo: si el nº de seguimiento de un
        paquete es el id de otro, es el mismo envío.
        """
        paquete(db_path, source="aliexpress", package_id="ALI-1",
                titulo="Cargador", tracking=NUMERO, message_id="ali-1")
        paquete(db_path, source="correos", package_id=NUMERO,
                titulo="COE-AE", message_id="correos-1")

        s = get_session(db_path)
        assert fusionar_duplicados_por_numero(s, log=lambda *a: None) == 1
        s.close()


class TestLoQueNoSeDebeJuntar:
    """La mitad importante: que no se lleve por delante cosas distintas."""

    def test_dos_paquetes_con_numeros_distintos(self, db_path):
        paquete(db_path, source="amazon", package_id="ENVIO1", titulo="Cosa uno")
        paquete(db_path, source="amazon", package_id="ENVIO2", titulo="Cosa dos")
        s = get_session(db_path)
        assert fusionar_duplicados_por_numero(s, log=lambda *a: None) == 0
        assert s.query(Package).count() == 2
        s.close()

    def test_dos_envios_del_mismo_pedido_siguen_separados(self, db_path):
        """
        Un pedido de Amazon con dos productos llega en dos envíos. Se parecen
        muchísimo — mismo pedido, mismo día — pero son dos paquetes.
        """
        s = get_session(db_path)
        for i, titulo in enumerate(("VOANZO Rodillo", "DollaTek Pantalla")):
            ingest_event(s, {"source": "amazon", "order_id": "408-0898802-9786726",
                             "package_id": f"ENVIO{i}", "status": "shipped",
                             "status_label_raw": "x", "title": titulo, "image_url": None,
                             "message_id": f"m{i}", "event_date": EVENT_DATE})
        s.close()

        s = get_session(db_path)
        assert fusionar_duplicados_por_numero(s, log=lambda *a: None) == 0
        assert s.query(Package).count() == 2
        s.close()

    def test_un_titulo_igual_no_basta_para_juntarlos(self, db_path):
        """Se compran dos veces las mismas zapatillas y son dos paquetes."""
        paquete(db_path, source="amazon", package_id="ENVIO1", titulo="Las mismas zapatillas")
        paquete(db_path, source="amazon", package_id="ENVIO2", titulo="Las mismas zapatillas")
        s = get_session(db_path)
        assert fusionar_duplicados_por_numero(s, log=lambda *a: None) == 0
        s.close()

    def test_los_numeros_vacios_no_juntan_a_nadie(self, db_path):
        """Dos paquetes sin nº de seguimiento no comparten nada: comparten None."""
        s = get_session(db_path)
        for i in range(2):
            ingest_event(s, {"source": "amazon", "order_id": f"408-{i}",
                             "package_id": f"P{i}", "status": "shipped",
                             "status_label_raw": "x", "title": "x", "image_url": None,
                             "message_id": f"m{i}", "event_date": EVENT_DATE,
                             "courier_tracking_number": None})
        s.close()
        s = get_session(db_path)
        assert fusionar_duplicados_por_numero(s, log=lambda *a: None) == 0
        s.close()

    def test_lo_que_esta_en_la_papelera_se_queda_ahi(self, db_path, client):
        """
        Si lo borraste, no debe volver por la puerta de atrás metido dentro de
        otro paquete.
        """
        a = paquete(db_path, source="aliexpress", package_id=NUMERO,
                    titulo="Cargador", message_id="ali-1")
        client.post(f"/package/{a}/delete")
        paquete(db_path, source="correos", package_id=NUMERO,
                titulo="COE-AE", message_id="correos-1")

        s = get_session(db_path)
        assert fusionar_duplicados_por_numero(s, log=lambda *a: None) == 0
        assert s.get(Package, a).deleted_at is not None
        s.close()


class TestSeHaceEnCadaEscaneo:
    def test_el_escaneo_los_junta_y_lo_cuenta(self, db_path):
        from app.gmail_sync import run_sync

        paquete(db_path, source="aliexpress", package_id=NUMERO,
                titulo="Cargador", message_id="ali-1")
        paquete(db_path, source="correos", package_id=NUMERO,
                titulo="COE-AE", message_id="correos-1")

        resumen = run_sync(db_path, lambda q: [], lambda m: {}, log=lambda *a: None)
        assert resumen["fusionados"] == 1

        s = get_session(db_path)
        assert s.query(Package).filter(Package.deleted_at.is_(None)).count() == 1
        s.close()

    def test_un_fallo_fusionando_no_tira_el_escaneo(self, db_path, monkeypatch):
        from app.gmail_sync import run_sync

        def revienta(*a, **kw):
            raise RuntimeError("algo raro")
        monkeypatch.setattr("app.gmail_sync.fusionar_duplicados_por_numero", revienta)

        resumen = run_sync(db_path,
                           lambda q: [{"id": "ali-1",
                                       "subject": "Paquete 315193141453520012: en aduanas",
                                       "sender": "AliExpress <transaction@notice.aliexpress.com>",
                                       "date": "2026-10-01T10:00:00+00:00"}],
                           lambda m: {"plaintext_body": "", "html_body": ""},
                           log=lambda *a: None)
        assert resumen["ingested"] == 1
        assert resumen["fusionados"] == 0
