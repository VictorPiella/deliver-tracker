"""
Vaciado automático de la papelera.

Es el único sitio del programa que destruye datos sin que nadie pulse nada, así
que la mitad de estos tests comprueban lo que NO hace.

Y el riesgo de verdad no es borrar de más: es que borrar haga reaparecer los
paquetes. Los eventos guardan los gmail_message_id que impiden reprocesar un
email; al borrarlos se perdían, y si los correos seguían dentro de la ventana de
escaneo el paquete volvía en la pasada siguiente. De ahí la lápida.
"""
from datetime import timedelta

import pytest

import app.cleanup as cleanup
from app.cleanup import (hard_delete_package, soft_delete_package,
                         vaciar_papelera_vieja)
from app.models import EmailProcesado, Order, Package, PackageEvent, get_session
from app.sync import ingest_event
from app.timeutils import utcnow

from conftest import EVENT_DATE


def sembrar(db_path, package_id="P1", message_id="m1"):
    s = get_session(db_path)
    ingest_event(s, {
        "source": "amazon", "order_id": f"408-{package_id}", "package_id": package_id,
        "status": "delivered", "status_label_raw": "Entregado",
        "title": "Una cosa", "image_url": None,
        "message_id": message_id, "event_date": EVENT_DATE,
    })
    pid = s.query(Package).filter_by(external_package_id=package_id).one().id
    s.close()
    return pid


def a_la_papelera(db_path, pid, hace_dias):
    s = get_session(db_path)
    p = s.get(Package, pid)
    soft_delete_package(s, p, motivo="test", log=lambda *a: None)
    p.deleted_at = utcnow() - timedelta(days=hace_dias)
    s.commit()
    s.close()


class TestQueBorraYQueNo:
    def test_borra_lo_que_lleva_mas_del_plazo(self, db_path):
        pid = sembrar(db_path)
        a_la_papelera(db_path, pid, hace_dias=31)

        assert vaciar_papelera_vieja(db_path, log=lambda *a: None) == 1
        s = get_session(db_path)
        assert s.get(Package, pid) is None
        s.close()

    def test_respeta_lo_recien_borrado(self, db_path):
        pid = sembrar(db_path)
        a_la_papelera(db_path, pid, hace_dias=5)

        assert vaciar_papelera_vieja(db_path, log=lambda *a: None) == 0
        s = get_session(db_path)
        assert s.get(Package, pid) is not None
        s.close()

    def test_justo_en_el_limite_no_se_toca(self, db_path):
        pid = sembrar(db_path)
        a_la_papelera(db_path, pid, hace_dias=29)
        assert vaciar_papelera_vieja(db_path, log=lambda *a: None) == 0

    def test_no_toca_lo_que_esta_en_el_panel(self, db_path):
        """Lo que no está en la papelera no es asunto suyo, por viejo que sea."""
        pid = sembrar(db_path)
        s = get_session(db_path)
        s.get(Package, pid).last_updated = utcnow() - timedelta(days=400)
        s.commit()
        s.close()

        assert vaciar_papelera_vieja(db_path, log=lambda *a: None) == 0
        s = get_session(db_path)
        assert s.get(Package, pid) is not None
        s.close()

    def test_se_puede_apagar(self, db_path, monkeypatch):
        pid = sembrar(db_path)
        a_la_papelera(db_path, pid, hace_dias=400)
        monkeypatch.setattr(cleanup, "PURGE_TRASH_AFTER_DAYS", 0)

        assert vaciar_papelera_vieja(db_path, log=lambda *a: None) == 0
        s = get_session(db_path)
        assert s.get(Package, pid) is not None
        s.close()

    def test_lo_dice_en_el_log(self, db_path):
        """Un borrado silencioso que nadie puede reconstruir después es justo
        lo que no se quiere."""
        pid = sembrar(db_path)
        a_la_papelera(db_path, pid, hace_dias=31)

        lineas = []
        vaciar_papelera_vieja(db_path, log=lambda *a: lineas.append(" ".join(map(str, a))))
        registro = "\n".join(lineas)
        assert f"id={pid}" in registro
        assert "P1" in registro


class TestQueNoResucite:
    """
    Lo que de verdad importa. Antes, borrar del todo un paquete hacía que
    volviera en el escaneo siguiente: con sus eventos se iban los
    gmail_message_id, y el email dejaba de constar como visto.
    """

    def test_queda_la_lapida_del_email(self, db_path):
        pid = sembrar(db_path, message_id="m-unico")
        a_la_papelera(db_path, pid, hace_dias=31)
        vaciar_papelera_vieja(db_path, log=lambda *a: None)

        s = get_session(db_path)
        assert s.query(EmailProcesado).filter_by(gmail_message_id="m-unico").count() == 1
        s.close()

    def test_el_mismo_email_ya_no_crea_nada(self, db_path):
        pid = sembrar(db_path, message_id="m-unico")
        a_la_papelera(db_path, pid, hace_dias=31)
        vaciar_papelera_vieja(db_path, log=lambda *a: None)

        # Llega otra vez el mismo email, como en el siguiente escaneo.
        s = get_session(db_path)
        creado = ingest_event(s, {
            "source": "amazon", "order_id": "408-P1", "package_id": "P1",
            "status": "delivered", "status_label_raw": "Entregado",
            "title": "Una cosa", "image_url": None,
            "message_id": "m-unico", "event_date": EVENT_DATE,
        })
        assert creado is False
        assert s.query(Package).count() == 0
        s.close()

    def test_un_escaneo_entero_no_lo_devuelve(self, db_path):
        from app.gmail_sync import run_sync

        mensajes = [{"id": "amz-1", "subject": 'Entregado: "Una cosa"',
                     "sender": "shipment-tracking@amazon.es",
                     "date": "2026-10-01T10:00:00+00:00"}]
        cuerpo = lambda m: {"plaintext_body": "Pedido n.º 406-0254524-0145135",
                            "html_body": ""}

        run_sync(db_path, lambda q: mensajes, cuerpo, log=lambda *a: None)
        s = get_session(db_path)
        pid = s.query(Package).one().id
        s.close()
        a_la_papelera(db_path, pid, hace_dias=31)
        vaciar_papelera_vieja(db_path, log=lambda *a: None)

        run_sync(db_path, lambda q: mensajes, cuerpo, log=lambda *a: None)
        s = get_session(db_path)
        assert s.query(Package).count() == 0, "el paquete ha resucitado"
        s.close()

    def test_la_lapida_no_guarda_nada_del_contenido(self, db_path):
        """Solo el identificador: ni asunto, ni remitente, ni estado."""
        pid = sembrar(db_path, message_id="m-unico")
        a_la_papelera(db_path, pid, hace_dias=31)
        vaciar_papelera_vieja(db_path, log=lambda *a: None)

        s = get_session(db_path)
        lapida = s.query(EmailProcesado).one()
        campos = {c.name for c in lapida.__table__.columns}
        assert campos == {"id", "gmail_message_id", "created_at"}
        s.close()

    def test_borrar_a_mano_tambien_deja_lapida(self, db_path):
        """La papelera tiene su propio botón de borrar del todo, y vale igual."""
        pid = sembrar(db_path, message_id="m-unico")
        s = get_session(db_path)
        hard_delete_package(s, s.get(Package, pid))
        s.commit()
        assert s.query(EmailProcesado).filter_by(gmail_message_id="m-unico").count() == 1
        s.close()


class TestLoQueSeLlevaPorDelante:
    def test_se_van_tambien_sus_eventos(self, db_path):
        pid = sembrar(db_path)
        a_la_papelera(db_path, pid, hace_dias=31)
        vaciar_papelera_vieja(db_path, log=lambda *a: None)

        s = get_session(db_path)
        assert s.query(PackageEvent).count() == 0
        s.close()

    def test_y_el_pedido_si_se_queda_vacio(self, db_path):
        pid = sembrar(db_path)
        a_la_papelera(db_path, pid, hace_dias=31)
        vaciar_papelera_vieja(db_path, log=lambda *a: None)

        s = get_session(db_path)
        assert s.query(Order).count() == 0
        s.close()

    def test_pero_no_si_al_pedido_le_quedan_envios(self, db_path):
        """Un pedido con dos envíos no se va porque se borre uno."""
        s = get_session(db_path)
        for i in range(2):
            ingest_event(s, {
                "source": "amazon", "order_id": "408-COMPARTIDO",
                "package_id": f"ENVIO{i}", "status": "delivered",
                "status_label_raw": "x", "title": "x", "image_url": None,
                "message_id": f"m{i}", "event_date": EVENT_DATE,
            })
        pid = s.query(Package).filter_by(external_package_id="ENVIO0").one().id
        s.close()

        a_la_papelera(db_path, pid, hace_dias=31)
        vaciar_papelera_vieja(db_path, log=lambda *a: None)

        s = get_session(db_path)
        assert s.query(Order).count() == 1
        assert s.query(Package).count() == 1
        s.close()
