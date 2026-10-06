"""
Seguimiento preguntado a Correos.

Es la única parte del programa que sale a internet, así que la mitad de estos
tests no van sobre lo que hace cuando funciona, sino sobre lo que hace cuando
no: si Correos no contesta, el panel tiene que seguir exactamente igual que
antes de existir esto.

La respuesta de ejemplo es la forma real de la API (comprobada contra un envío
de verdad), con el número cambiado.
"""
import json
from datetime import datetime

import pytest

from app import correos_api
from app.correos_api import CorreosNoContesta, consultar, estado_del_evento
from app.models import Package, PackageEvent, get_session
from app.sync import actualizar_desde_correos, ingest_event, paquetes_a_consultar

from conftest import EVENT_DATE

NUMERO = "PX1234567890123456789Z"


def evento(fecha, hora, fase, resumen, extendido=""):
    return {"eventDate": fecha, "eventTime": hora, "phase": fase,
            "desPhase": "", "summaryText": resumen, "extendedText": extendido}


RESPUESTA = {
    "type": "envio",
    "expedition": None,
    "shipment": [{
        "shipmentCode": NUMERO,
        "events": [
            evento("29/09/2026", "14:19:18", "1", "Prerregistrado",
                   "Envío prerregistrado en los sistemas de Correos pendiente de depósito"),
            evento("03/10/2026", "18:42:00", "2", "Admitido.",
                   "El envío ha tenido admisión en origen."),
            evento("05/10/2026", "12:53:00", "3", "Alta en la unidad de reparto",
                   "El envío ha entrado en la unidad responsable de su entrega"),
        ],
    }],
}


@pytest.fixture
def correos_responde(monkeypatch):
    """Correos contesta con la respuesta grabada. Sin tocar la red."""
    monkeypatch.setattr(correos_api, "HABILITADO", True)
    monkeypatch.setattr(correos_api, "_pedir", lambda numero: json.loads(json.dumps(RESPUESTA)))


@pytest.fixture
def correos_no_contesta(monkeypatch):
    def explota(numero):
        raise CorreosNoContesta("TimeoutError: se acabó el tiempo")
    monkeypatch.setattr(correos_api, "HABILITADO", True)
    monkeypatch.setattr(correos_api, "_pedir", explota)


def paquete_de_correos(db_path, numero=NUMERO, status="shipped"):
    s = get_session(db_path)
    ingest_event(s, {
        "source": "correos", "order_id": numero, "package_id": numero,
        "status": status, "status_label_raw": "Información sobre su envío",
        "title": "Un paquete", "image_url": None,
        "message_id": "m-" + numero, "event_date": EVENT_DATE,
    })
    s.close()


class TestLeerLaRespuesta:
    def test_saca_los_eventos_en_orden(self, correos_responde):
        ev = consultar(NUMERO)
        assert [e["texto"] for e in ev] == [
            "Prerregistrado", "Admitido.", "Alta en la unidad de reparto"]

    def test_con_su_fecha_y_hora(self, correos_responde):
        assert consultar(NUMERO)[2]["fecha"] == datetime(2026, 10, 5, 12, 53, 0)

    def test_cada_evento_trae_un_id_estable(self, correos_responde):
        """
        Correos no da ningún id, así que se compone con el número y el momento.
        Tiene que salir igual en dos consultas seguidas o se apuntaría dos veces.
        """
        assert [e["id"] for e in consultar(NUMERO)] == [e["id"] for e in consultar(NUMERO)]
        assert consultar(NUMERO)[0]["id"].startswith("correos:")

    def test_un_numero_que_no_existe_no_es_un_fallo(self, monkeypatch):
        """
        Que Correos no sepa de un envío y que Correos no conteste son cosas
        distintas: lo primero no se reintenta, lo segundo sí.
        """
        monkeypatch.setattr(correos_api, "HABILITADO", True)
        monkeypatch.setattr(correos_api, "_pedir", lambda n: {"shipment": []})
        assert consultar(NUMERO) == []

    def test_un_envio_que_no_es_suyo_contesta_vacio_y_no_es_un_fallo(self, monkeypatch):
        """
        Correos devuelve 204 y cuerpo vacío para un número que no es suyo. Eso
        es una respuesta, no una caída.

        Reventaba al parsear el JSON y acababa contado como fallo de Correos, lo
        que significaba que el panel reportaba una caída por hora y por cada
        paquete ajeno — que son casi todos. Lo destapó buscar un envío de
        YunExpress en Correos.
        """
        import urllib.request

        class RespuestaVacia:
            def read(self): return b""
            def __enter__(self): return self
            def __exit__(self, *a): return False

        monkeypatch.setattr(correos_api, "HABILITADO", True)
        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: RespuestaVacia())
        assert consultar(NUMERO) == []          # ni excepción ni eventos

    def test_una_respuesta_ilegible_si_es_un_fallo(self, monkeypatch):
        """Vacío es "no lo conozco"; basura es que algo va mal y hay que decirlo."""
        import urllib.request

        class RespuestaBasura:
            def read(self): return b"<html>vaya</html>"
            def __enter__(self): return self
            def __exit__(self, *a): return False

        monkeypatch.setattr(correos_api, "HABILITADO", True)
        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: RespuestaBasura())
        with pytest.raises(CorreosNoContesta):
            consultar(NUMERO)

    def test_un_numero_con_pinta_rara_ni_se_pregunta(self):
        assert consultar("") == []
        assert consultar("corto") == []
        assert consultar(None) == []

    def test_un_evento_con_la_fecha_rota_se_salta_sin_tirar_el_resto(self, monkeypatch):
        roto = json.loads(json.dumps(RESPUESTA))
        roto["shipment"][0]["events"][1]["eventDate"] = "no es una fecha"
        monkeypatch.setattr(correos_api, "HABILITADO", True)
        monkeypatch.setattr(correos_api, "_pedir", lambda n: roto)
        assert len(consultar(NUMERO)) == 2

    def test_una_respuesta_con_otra_forma_no_revienta(self, monkeypatch):
        """No es una API publicada con contrato: puede cambiar sin avisar."""
        monkeypatch.setattr(correos_api, "HABILITADO", True)
        for basura in ({}, {"shipment": None}, {"shipment": "texto"},
                       {"shipment": [None]}, {"shipment": [{"events": None}]}):
            monkeypatch.setattr(correos_api, "_pedir", lambda n, b=basura: b)
            assert consultar(NUMERO) == []


class TestTraducirLosEstados:
    def test_la_fase_pone_el_suelo_y_el_texto_afina_hacia_arriba(self):
        """
        El panel no puede contradecir a Correos. "Alta en la unidad de reparto"
        suena a centro de distribución y así se traducía, pero Correos mete ese
        evento en la fase 3 y en su web lo enseña como "EN ENTREGA": quien
        miraba las dos pantallas veía dos cosas distintas.

        La fase sola tampoco vale: dentro de "EN CAMINO" caben la admisión y el
        centro logístico, que no son lo mismo.
        """
        # El texto dice menos que la fase -> manda la fase.
        assert estado_del_evento(
            {"summaryText": "Alta en la unidad de reparto", "phase": "3"}) == "out_for_delivery"
        # El texto dice más que la fase -> manda el texto.
        assert estado_del_evento(
            {"summaryText": "Clasificado en Centro Logístico", "phase": "2"}) == "at_distribution"
        assert estado_del_evento({"summaryText": "Entregado", "phase": "2"}) == "delivered"

    def test_sin_fase_el_texto_decide_solo(self):
        assert estado_del_evento(
            {"summaryText": "Alta en la unidad de reparto", "phase": ""}) == "at_distribution"

    def test_los_textos_reales(self):
        casos = [
            ("Prerregistrado", "ordered"),
            ("Admitido.", "shipped"),
            ("Clasificado", "at_distribution"),
            ("Alta en la unidad de reparto", "at_distribution"),   # sin fase
            ("En reparto", "out_for_delivery"),
            ("Entregado", "delivered"),
        ]
        for texto, esperado in casos:
            assert estado_del_evento({"summaryText": texto, "phase": ""}) == esperado, texto

    def test_manda_el_texto_sobre_la_fase(self):
        """
        La fase agrupa mucho: "EN CAMINO" cubre desde la admisión hasta el
        centro logístico. El texto es lo que concreta.
        """
        assert estado_del_evento(
            {"summaryText": "Entregado", "phase": "2"}) == "delivered"

    def test_si_el_texto_no_dice_nada_la_fase_situa(self):
        assert estado_del_evento({"summaryText": "Texto nuevo raro", "phase": "3"}) == "out_for_delivery"
        assert estado_del_evento({"summaryText": "", "phase": "4"}) == "delivered"

    def test_sin_texto_ni_fase_es_desconocido(self):
        """'unknown' tiene rango -1, así que no pisa a ningún estado real."""
        assert estado_del_evento({}) == "unknown"

    def test_una_entrega_fallida_no_se_lee_como_entrega(self):
        assert estado_del_evento(
            {"summaryText": "No se ha podido entregar", "phase": "3"}) == "delivery_attempted"


class TestAQuienSeLePregunta:
    def test_a_los_envios_de_correos_que_siguen_moviendose(self, db_path):
        paquete_de_correos(db_path)
        s = get_session(db_path)
        assert [n for _, n in paquetes_a_consultar(s)] == [NUMERO]
        s.close()

    def test_no_a_los_ya_entregados(self, db_path):
        paquete_de_correos(db_path, status="delivered")
        s = get_session(db_path)
        assert paquetes_a_consultar(s) == []
        s.close()

    def test_no_a_los_de_la_papelera(self, db_path, client):
        paquete_de_correos(db_path)
        s = get_session(db_path)
        pid = s.query(Package).one().id
        s.close()
        client.post(f"/package/{pid}/delete")

        s = get_session(db_path)
        assert paquetes_a_consultar(s) == []
        s.close()

    def test_no_a_los_de_otras_fuentes(self, db_path):
        s = get_session(db_path)
        ingest_event(s, {"source": "amazon", "order_id": "408-1", "package_id": "P1",
                         "status": "shipped", "status_label_raw": "x", "title": "x",
                         "image_url": None, "message_id": "m1", "event_date": EVENT_DATE})
        assert paquetes_a_consultar(s) == []
        s.close()

    def test_si_a_un_amazon_que_reparte_correos(self, db_path):
        """Un paquete de Amazon con courier=correos también tiene número suyo."""
        s = get_session(db_path)
        ingest_event(s, {"source": "amazon", "order_id": "408-1", "package_id": "P1",
                         "status": "shipped", "status_label_raw": "x", "title": "x",
                         "image_url": None, "message_id": "m1", "event_date": EVENT_DATE,
                         "courier": "correos", "courier_tracking_number": NUMERO})
        assert [n for _, n in paquetes_a_consultar(s)] == [NUMERO]
        s.close()


class TestLoQueLlegaAlPaquete:
    def test_los_eventos_se_apuntan(self, db_path, correos_responde):
        paquete_de_correos(db_path)
        s = get_session(db_path)
        resumen = actualizar_desde_correos(s, log=lambda *a: None)
        assert resumen == {"consultados": 1, "eventos": 3, "fallos": 0, "descubiertos": 0}
        assert s.query(PackageEvent).count() == 4      # el del email + los 3
        s.close()

    def test_el_estado_avanza(self, db_path, correos_responde):
        paquete_de_correos(db_path)
        s = get_session(db_path)
        actualizar_desde_correos(s, log=lambda *a: None)
        # "Alta en la unidad de reparto" va en la fase 3, que Correos llama
        # "EN ENTREGA" en su web. Ver TestLaFasePoneElSuelo.
        assert s.query(Package).one().status == "out_for_delivery"
        s.close()

    def test_preguntar_dos_veces_no_duplica_nada(self, db_path, correos_responde):
        paquete_de_correos(db_path)
        s = get_session(db_path)
        actualizar_desde_correos(s, log=lambda *a: None)
        segundo = actualizar_desde_correos(s, log=lambda *a: None)
        assert segundo["eventos"] == 0
        assert s.query(PackageEvent).count() == 4
        s.close()

    def test_un_estado_puesto_a_mano_manda_sobre_correos(self, db_path, correos_responde, client):
        """La misma regla que con los emails: si lo fijaste tú, no se toca."""
        paquete_de_correos(db_path)
        s = get_session(db_path)
        pid = s.query(Package).one().id
        s.close()
        client.post(f"/package/{pid}/status", data={"status": "customs"})

        s = get_session(db_path)
        actualizar_desde_correos(s, log=lambda *a: None)
        p = s.get(Package, pid)
        assert p.status == "customs"            # Correos decía at_distribution
        assert p.status_is_manual is True
        # Pero los eventos sí quedan en el histórico: se ve lo que dice Correos
        # aunque el estado sea el que tú pusiste.
        assert s.query(PackageEvent).count() == 4
        s.close()

    def test_marcarlo_entregado_a_mano_deja_de_consultarlo(self, db_path, client):
        """
        Un estado final, lo haya puesto un email o tú, cierra el asunto: no se
        vuelve a preguntar por él. Gastar llamadas en algo que ya no se mueve no
        tiene sentido.
        """
        paquete_de_correos(db_path)
        s = get_session(db_path)
        pid = s.query(Package).one().id
        s.close()
        client.post(f"/package/{pid}/status", data={"status": "delivered"})

        s = get_session(db_path)
        assert paquetes_a_consultar(s) == []
        s.close()

    def test_el_estado_no_retrocede(self, db_path, correos_responde):
        paquete_de_correos(db_path, status="out_for_delivery")
        s = get_session(db_path)
        actualizar_desde_correos(s, log=lambda *a: None)
        assert s.query(Package).one().status == "out_for_delivery"
        s.close()

    def test_la_fecha_es_la_del_evento_no_la_de_ahora(self, db_path, correos_responde):
        """
        Si no, preguntar a Correos subiría el paquete al principio de la lista
        en cada escaneo aunque no hubiera pasado nada nuevo.
        """
        paquete_de_correos(db_path)
        s = get_session(db_path)
        actualizar_desde_correos(s, log=lambda *a: None)
        assert s.query(Package).one().last_updated == datetime(2026, 10, 5, 12, 53, 0)
        s.close()


class TestCuandoCorreosNoContesta:
    """
    Lo que más importa de todo esto. El panel funcionaba sin Correos y tiene que
    seguir funcionando sin Correos.
    """

    def test_no_se_pierde_el_escaneo(self, db_path, correos_no_contesta):
        paquete_de_correos(db_path)
        s = get_session(db_path)
        resumen = actualizar_desde_correos(s, log=lambda *a: None)
        assert resumen["fallos"] == 1
        assert resumen["eventos"] == 0
        s.close()

    def test_el_paquete_se_queda_como_estaba(self, db_path, correos_no_contesta):
        paquete_de_correos(db_path)
        s = get_session(db_path)
        actualizar_desde_correos(s, log=lambda *a: None)
        assert s.query(Package).one().status == "shipped"
        assert s.query(PackageEvent).count() == 1
        s.close()

    def test_un_escaneo_de_emails_completo_no_se_cae_por_esto(self, db_path, monkeypatch):
        """
        Correos va al final del escaneo y en su propio try. Que reviente de
        forma inesperada no puede tirar abajo un escaneo que ya ha ido bien.
        """
        from app.gmail_sync import run_sync

        def revienta(*a, **kw):
            raise RuntimeError("algo muy raro")
        monkeypatch.setattr("app.gmail_sync.actualizar_desde_correos", revienta)

        resumen = run_sync(db_path,
                           lambda q: [{"id": "ali-1",
                                       "subject": "Paquete 315193141453520012: en aduanas",
                                       "sender": "AliExpress <transaction@notice.aliexpress.com>",
                                       "date": "2026-10-01T10:00:00+00:00"}],
                           lambda m: {"plaintext_body": "", "html_body": ""},
                           log=lambda *a: None)
        assert resumen["ingested"] == 1
        assert resumen["synced_at"] is not None     # el escaneo cuenta como hecho

    def test_se_puede_apagar_del_todo(self, db_path, monkeypatch):
        paquete_de_correos(db_path)
        monkeypatch.setattr(correos_api, "HABILITADO", False)
        monkeypatch.setattr(correos_api, "_pedir",
                            lambda n: pytest.fail("no debería preguntarse nada"))
        s = get_session(db_path)
        assert actualizar_desde_correos(s, log=lambda *a: None)["consultados"] == 0
        s.close()

    def test_no_se_consultan_mas_de_la_cuenta(self, db_path, correos_responde, monkeypatch):
        """No es una API nuestra: conviene no machacarla."""
        for i in range(5):
            paquete_de_correos(db_path, numero=f"PX123456789012345678{i}Z")
        monkeypatch.setattr(correos_api, "MAX_CONSULTAS", 2)
        s = get_session(db_path)
        assert actualizar_desde_correos(s, log=lambda *a: None)["consultados"] == 2
        s.close()


class TestLosTestsNoHablanConCorreos:
    def test_la_red_esta_apagada_por_defecto(self, db_path):
        """
        Sin el fixture de conftest, media suite estaría llamando a un servidor
        ajeno. Este test se entera si alguien lo quita.
        """
        paquete_de_correos(db_path)
        s = get_session(db_path)
        assert actualizar_desde_correos(s, log=lambda *a: None)["consultados"] == 0
        s.close()


class TestDescubrirQuienLoReparte:
    """
    Casi ningún paquete viene con "Correos" escrito. El que destapó todo esto es
    un AliExpress con courier a None, y su número lo sigue Correos sin problema:
    ningún email lo dice.
    """

    def paquete_sin_transportista(self, db_path):
        s = get_session(db_path)
        ingest_event(s, {"source": "aliexpress", "order_id": "307", "package_id": NUMERO,
                         "status": "in_country", "status_label_raw": "x", "title": "Algo",
                         "image_url": None, "message_id": "m1", "event_date": EVENT_DATE})
        s.close()

    def test_se_le_pregunta_aunque_no_sepamos_quien_lo_lleva(self, db_path):
        self.paquete_sin_transportista(db_path)
        s = get_session(db_path)
        assert [n for _, n in paquetes_a_consultar(s)] == [NUMERO]
        s.close()

    def test_si_correos_contesta_queda_apuntado(self, db_path, correos_responde):
        self.paquete_sin_transportista(db_path)
        s = get_session(db_path)
        resumen = actualizar_desde_correos(s, log=lambda *a: None)
        assert resumen["descubiertos"] == 1
        assert s.query(Package).one().courier == "correos"
        s.close()

    def test_si_no_contesta_nada_no_se_le_inventa_un_transportista(self, db_path, monkeypatch):
        """Una lista vacía quiere decir "no es mío", no "es mío y está parado"."""
        self.paquete_sin_transportista(db_path)
        monkeypatch.setattr(correos_api, "HABILITADO", True)
        monkeypatch.setattr(correos_api, "_pedir", lambda n: {"shipment": []})
        s = get_session(db_path)
        actualizar_desde_correos(s, log=lambda *a: None)
        assert s.query(Package).one().courier is None
        s.close()

    def test_no_se_pisa_un_transportista_que_ya_sabiamos(self, db_path, correos_responde):
        s = get_session(db_path)
        ingest_event(s, {"source": "amazon", "order_id": "408-1", "package_id": "P1",
                         "status": "shipped", "status_label_raw": "x", "title": "x",
                         "image_url": None, "message_id": "m1", "event_date": EVENT_DATE,
                         "courier": "gls", "courier_tracking_number": NUMERO})
        s.close()
        s = get_session(db_path)
        actualizar_desde_correos(s, log=lambda *a: None)
        assert s.query(Package).one().courier == "gls"
        s.close()

    def test_los_seguros_se_consultan_antes_que_las_apuestas(self, db_path):
        """Con el tope por escaneo, el orden decide quién se queda fuera."""
        self.paquete_sin_transportista(db_path)
        paquete_de_correos(db_path, numero="PX9999999999999999999Z")
        s = get_session(db_path)
        assert [n for _, n in paquetes_a_consultar(s)][0] == "PX9999999999999999999Z"
        s.close()
