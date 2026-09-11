"""
Fusionar dos paquetes en uno.

Correos no incluye ningún ID de Amazon/AliExpress en sus emails, así que sus
envíos se trackean como entrada aparte y el mismo paquete sale dos veces.
Enlazarlos automáticamente sería adivinar; esto deja hacerlo a mano.
"""
from app.models import Order, Package, PackageEvent, get_session
from app.sync import ingest_event, merge_packages
from app.web import create_app

from conftest import EVENT_DATE


def evento(**kwargs):
    base = {
        "source": "amazon", "order_id": "408-1-1", "package_id": "ENVIO1",
        "status": "shipped", "status_label_raw": "Enviado", "title": "Mesita",
        "image_url": None, "message_id": "m1", "event_date": EVENT_DATE,
    }
    base.update(kwargs)
    return base


def correos(**kwargs):
    base = {
        "source": "correos", "order_id": "PQ123ES", "package_id": "PQ123ES",
        "status": "out_for_delivery", "status_label_raw": "Entrega hoy",
        "title": "AMAZON EU SARL", "image_url": None,
        "courier": "correos", "courier_tracking_number": "PQ123ES",
        "message_id": "c1", "event_date": EVENT_DATE,
    }
    base.update(kwargs)
    return base


class TestMergePackages:
    def test_mueve_los_eventos_y_borra_el_origen(self, session):
        ingest_event(session, evento())
        ingest_event(session, correos())

        origen = session.query(Package).filter_by(source="correos").one()
        destino = session.query(Package).filter_by(source="amazon").one()

        movidos = merge_packages(session, origen, destino)
        session.commit()

        assert movidos == 1
        assert session.query(Package).count() == 1
        assert session.query(Package).one().source == "amazon"
        # Ningún evento se pierde: los dos cuelgan ahora del paquete bueno.
        assert session.query(PackageEvent).count() == 2

    def test_el_destino_hereda_el_tracking_del_transportista(self, session):
        ingest_event(session, evento())
        ingest_event(session, correos())

        origen = session.query(Package).filter_by(source="correos").one()
        destino = session.query(Package).filter_by(source="amazon").one()
        merge_packages(session, origen, destino)
        session.commit()

        p = session.query(Package).one()
        assert p.courier == "correos"
        assert p.courier_tracking_number == "PQ123ES"

    def test_el_estado_se_recalcula_con_todo_el_historico(self, session):
        ingest_event(session, evento(status="shipped"))
        ingest_event(session, correos(status="out_for_delivery"))

        origen = session.query(Package).filter_by(source="correos").one()
        destino = session.query(Package).filter_by(source="amazon").one()
        merge_packages(session, origen, destino)
        session.commit()

        # El evento más avanzado venía del de Correos.
        assert session.query(Package).one().status == "out_for_delivery"

    def test_respeta_un_estado_puesto_a_mano(self, session):
        ingest_event(session, evento(status="shipped"))
        ingest_event(session, correos(status="out_for_delivery"))

        destino = session.query(Package).filter_by(source="amazon").one()
        destino.status = "delivered"
        destino.status_is_manual = True
        session.commit()

        merge_packages(session, session.query(Package).filter_by(source="correos").one(), destino)
        session.commit()
        assert session.query(Package).one().status == "delivered"

    def test_limpia_el_pedido_que_se_queda_huerfano(self, session):
        ingest_event(session, evento())
        ingest_event(session, correos())
        assert session.query(Order).count() == 2

        merge_packages(session,
                       session.query(Package).filter_by(source="correos").one(),
                       session.query(Package).filter_by(source="amazon").one())
        session.commit()
        assert session.query(Order).count() == 1

    def test_fusionar_consigo_mismo_no_hace_nada(self, session):
        ingest_event(session, evento())
        p = session.query(Package).one()
        assert merge_packages(session, p, p) == 0
        assert session.query(Package).count() == 1


class TestMergeNoResucita:
    def test_el_paquete_fusionado_no_vuelve_al_escanear(self, db_path):
        """
        Los eventos se MUEVEN en vez de borrarse, así que conservan su
        gmail_message_id y el escaneo siguiente no recrea el paquete de origen.
        """
        from app.gmail_sync import run_sync
        from app.mock_gmail import mock_get_thread_fn, mock_search_fn

        run_sync(db_path, mock_search_fn, mock_get_thread_fn, log=lambda *a: None)

        s = get_session(db_path)
        paquetes = s.query(Package).order_by(Package.id).all()
        antes = len(paquetes)
        merge_packages(s, paquetes[0], paquetes[1])
        s.commit()
        s.close()

        run_sync(db_path, mock_search_fn, mock_get_thread_fn, log=lambda *a: None)

        s = get_session(db_path)
        assert s.query(Package).count() == antes - 1
        s.close()


class TestRutaDeFusion:
    def _app(self, db_path):
        app = create_app(db_path=db_path, use_mock_gmail=True, enable_worker=False)
        app.config["TESTING"] = True
        return app

    def test_fusiona_desde_el_panel(self, db_path):
        from conftest import sincronizar
        c = self._app(db_path).test_client()
        sincronizar(c)

        paquetes = c.get("/api/packages").get_json()
        origen, destino = paquetes[0], paquetes[1]

        r = c.post(f"/package/{origen['id']}/merge", data={"target_id": destino["id"]})
        assert r.status_code == 302

        ids = {p["id"] for p in c.get("/api/packages").get_json()}
        assert origen["id"] not in ids
        assert destino["id"] in ids

    def test_destino_invalido_no_rompe_nada(self, db_path):
        from conftest import sincronizar
        c = self._app(db_path).test_client()
        sincronizar(c)
        antes = len(c.get("/api/packages").get_json())
        origen = c.get("/api/packages").get_json()[0]

        assert c.post(f"/package/{origen['id']}/merge", data={"target_id": "99999"}).status_code == 302
        assert c.post(f"/package/{origen['id']}/merge", data={"target_id": "no-numero"}).status_code == 302
        assert len(c.get("/api/packages").get_json()) == antes

    def test_el_formulario_sale_en_la_pagina_de_detalle(self, db_path):
        from conftest import sincronizar
        c = self._app(db_path).test_client()
        sincronizar(c)
        pkg_id = c.get("/api/packages").get_json()[0]["id"]

        html = c.get(f"/package/{pkg_id}").get_data(as_text=True)
        assert 'name="target_id"' in html
        assert "/merge" in html
