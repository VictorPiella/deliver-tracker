"""
Regresión del scheduler que nunca disparaba, y tests de la purga automática.
"""
from datetime import timedelta

import pytest

import app.cleanup as cleanup
from app.cleanup import (
    hard_delete_package, purge_old_delivered, restore_package, soft_delete_package,
)
from app.models import Order, Package, PackageEvent, get_session
from app.sync import ingest_event
from app.timeutils import utcnow
from app.worker import start_worker, stop_worker

from conftest import EVENT_DATE


def paquete(session, package_id, status, last_updated=None, message_id=None):
    ingest_event(session, {
        "source": "aliexpress",
        "order_id": f"order-{package_id}",
        "package_id": package_id,
        "status": status,
        "status_label_raw": status,
        "title": "algo",
        "image_url": None,
        "message_id": message_id or f"m-{package_id}",
        "event_date": last_updated or EVENT_DATE,
    })
    p = session.query(Package).filter_by(external_package_id=package_id).one()
    if last_updated is not None:
        p.last_updated = last_updated
        session.commit()
    return p


class TestSchedulerNoNacePausado:
    """
    El job se creaba con next_run_time=None. En APScheduler eso no significa
    "usa el valor por defecto", significa "job en pausa": la sincronización
    horaria no llegaba a ejecutarse nunca.
    """

    def teardown_method(self):
        stop_worker()

    def test_el_job_queda_programado(self, db_path):
        scheduler = start_worker(db_path, lambda q: [], lambda m: {})
        try:
            job = scheduler.get_job("gmail_sync_job")
            assert job is not None
            assert job.next_run_time is not None, "el job está en pausa: no se ejecutará nunca"
        finally:
            stop_worker()

    def test_no_dispara_nada_mas_arrancar(self, db_path):
        # Arrancar la app no debe provocar un escaneo inmediato; el primer
        # disparo es dentro de un intervalo completo.
        scheduler = start_worker(db_path, lambda q: [], lambda m: {})
        try:
            job = scheduler.get_job("gmail_sync_job")
            faltan = job.next_run_time - utcnow().replace(tzinfo=job.next_run_time.tzinfo)
            assert faltan > timedelta(minutes=50)
        finally:
            stop_worker()

    def test_es_idempotente(self, db_path):
        primero = start_worker(db_path, lambda q: [], lambda m: {})
        segundo = start_worker(db_path, lambda q: [], lambda m: {})
        try:
            assert primero is segundo   # no arranca un segundo scheduler
        finally:
            stop_worker()


class TestBorradoLogico:
    """
    Borrar manda a la papelera, no destruye la fila. Ver el test de resurrección
    en test_resurreccion.py para el motivo de fondo.
    """

    def test_manda_a_la_papelera_y_conserva_los_eventos(self, session):
        p = paquete(session, "p1", "delivered")
        soft_delete_package(session, p)
        session.commit()

        p = session.query(Package).one()
        assert p.deleted_at is not None
        # Los eventos SIGUEN ahí: son los que impiden que el email se reprocese.
        assert session.query(PackageEvent).count() == 1

    def test_restaurar_lo_devuelve_tal_cual(self, session):
        p = paquete(session, "p1", "in_country")
        soft_delete_package(session, p)
        session.commit()

        restore_package(session, session.query(Package).one())
        session.commit()

        p = session.query(Package).one()
        assert p.deleted_at is None
        assert p.status == "in_country"
        assert len(p.events) == 1

    def test_borrar_dos_veces_no_cambia_la_fecha(self, session):
        p = paquete(session, "p1", "delivered")
        soft_delete_package(session, p)
        session.commit()
        primera = session.query(Package).one().deleted_at

        soft_delete_package(session, session.query(Package).one())
        session.commit()
        assert session.query(Package).one().deleted_at == primera


class TestBorradoDefinitivo:
    def test_borra_el_paquete_y_sus_eventos(self, session):
        p = paquete(session, "p1", "delivered")
        hard_delete_package(session, p)
        session.commit()

        assert session.query(Package).count() == 0
        assert session.query(PackageEvent).count() == 0

    def test_borra_el_pedido_si_se_queda_huerfano(self, session):
        p = paquete(session, "p1", "delivered")
        hard_delete_package(session, p)
        session.commit()
        assert session.query(Order).count() == 0

    def test_conserva_el_pedido_si_le_quedan_paquetes(self, session):
        ingest_event(session, {
            "source": "amazon", "order_id": "408-1-1", "package_id": "ENVIO1",
            "status": "shipped", "status_label_raw": "x", "title": None,
            "image_url": None, "message_id": "m1", "event_date": EVENT_DATE,
        })
        ingest_event(session, {
            "source": "amazon", "order_id": "408-1-1", "package_id": "ENVIO2",
            "status": "shipped", "status_label_raw": "x", "title": None,
            "image_url": None, "message_id": "m2", "event_date": EVENT_DATE,
        })

        hard_delete_package(
            session, session.query(Package).filter_by(external_package_id="ENVIO1").one())
        session.commit()

        assert session.query(Order).count() == 1
        assert session.query(Package).count() == 1


DIAS_ARCHIVADO = 30


class TestArchivadoAutomatico:
    """
    Antes esto BORRABA definitivamente a los 15 días. Con la base como única
    copia duradera, eso destruía el histórico de compras sin vuelta atrás.
    Ahora sólo aparta del panel, a la papelera.
    """

    @pytest.fixture(autouse=True)
    def con_archivado(self, monkeypatch):
        # El resto de la suite lo tiene desactivado (ver conftest); aquí es
        # justo lo que se está probando.
        monkeypatch.setattr(cleanup, "PURGE_DELIVERED_AFTER_DAYS", DIAS_ARCHIVADO)

    def test_archiva_los_entregados_hace_mucho(self, session, db_path):
        viejo = utcnow() - timedelta(days=DIAS_ARCHIVADO + 1)
        paquete(session, "viejo", "delivered", last_updated=viejo)
        session.close()

        assert purge_old_delivered(db_path, log=lambda *a: None) == 1

        s = get_session(db_path)
        p = s.query(Package).one()
        assert p.deleted_at is not None          # a la papelera...
        assert s.query(PackageEvent).count() == 1  # ...pero no se pierde nada

    def test_respeta_los_entregados_recientes(self, session, db_path):
        reciente = utcnow() - timedelta(days=DIAS_ARCHIVADO - 1)
        paquete(session, "reciente", "delivered", last_updated=reciente)
        session.close()

        assert purge_old_delivered(db_path, log=lambda *a: None) == 0
        assert get_session(db_path).query(Package).one().deleted_at is None

    def test_no_toca_los_que_siguen_en_transito(self, session, db_path):
        # Aunque lleve meses parado: si no está entregado, sigue interesando.
        antiguo = utcnow() - timedelta(days=400)
        paquete(session, "atascado", "customs", last_updated=antiguo)
        session.close()

        assert purge_old_delivered(db_path, log=lambda *a: None) == 0
        assert get_session(db_path).query(Package).one().deleted_at is None

    def test_no_rearchiva_lo_que_ya_esta_en_la_papelera(self, session, db_path):
        viejo = utcnow() - timedelta(days=DIAS_ARCHIVADO + 1)
        paquete(session, "viejo", "delivered", last_updated=viejo)
        session.close()

        assert purge_old_delivered(db_path, log=lambda *a: None) == 1
        assert purge_old_delivered(db_path, log=lambda *a: None) == 0

    def test_se_puede_desactivar(self, session, db_path, monkeypatch):
        monkeypatch.setattr(cleanup, "PURGE_DELIVERED_AFTER_DAYS", 0)

        viejo = utcnow() - timedelta(days=999)
        paquete(session, "viejo", "delivered", last_updated=viejo)
        session.close()

        assert cleanup.purge_old_delivered(db_path, log=lambda *a: None) == 0
        assert get_session(db_path).query(Package).one().deleted_at is None
