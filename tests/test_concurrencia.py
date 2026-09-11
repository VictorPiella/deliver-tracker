"""
Lecturas durante un escaneo, y fechas en español.

Desde que el escaneo corre en un hilo aparte hay un escritor largo conviviendo
con los lectores que sirven cada página. En el modo `delete` de SQLite, el
commit del escritor toma un lock exclusivo que echa fuera a los lectores; con un
escaneo que puede tardar minutos y commitear evento a evento, eso son ratos en
los que el panel devuelve "database is locked". WAL quita ese choque.
"""
import threading
from datetime import datetime

import sqlalchemy as sa

from app.models import Package, get_engine, get_session
from app.sync import ingest_event
from app.timeutils import MESES_ES, formatear
from app.web import formato_local

from conftest import EVENT_DATE


def sembrar(db_path, package_id="ENVIO1", message_id="m1"):
    s = get_session(db_path)
    ingest_event(s, {
        "source": "amazon", "order_id": "408-1-1", "package_id": package_id,
        "status": "shipped", "status_label_raw": "Enviado", "title": "Algo",
        "image_url": None, "message_id": message_id, "event_date": EVENT_DATE,
    })
    s.close()


class TestSqliteAfinado:
    def test_arranca_en_modo_wal(self, db_path):
        with get_engine(db_path).connect() as c:
            assert c.execute(sa.text("PRAGMA journal_mode")).scalar().lower() == "wal"

    def test_espera_ante_bloqueos_en_vez_de_reventar(self, db_path):
        with get_engine(db_path).connect() as c:
            assert c.execute(sa.text("PRAGMA busy_timeout")).scalar() >= 5000


class TestLeerMientrasSeEscribe:
    """
    Ojo con lo que prueban estos tests: son de humo, no una reproducción del
    fallo. Con el journal clásico, un escritor que sólo tiene el lock RESERVED
    deja leer igual; el bloqueo duro llega en el commit y dura un instante, algo
    que no sale de forma fiable en un test. La garantía real de que seguimos en
    WAL son las aserciones de PRAGMA de arriba.
    """

    def test_el_panel_responde_con_una_escritura_a_medias(self, db_path):
        """Una lectura durante una transacción de escritura abierta funciona."""
        sembrar(db_path)

        escritura_abierta = threading.Event()
        lectura_hecha = threading.Event()
        fallo = []

        def escritor():
            s = get_session(db_path)
            try:
                ingest_event(s, {
                    "source": "amazon", "order_id": "408-2-2", "package_id": "ENVIO2",
                    "status": "shipped", "status_label_raw": "Enviado", "title": "Otro",
                    "image_url": None, "message_id": "m2", "event_date": EVENT_DATE,
                })
                # Transacción abierta y sin cerrar mientras el lector trabaja.
                s.execute(sa.text("BEGIN IMMEDIATE"))
                s.execute(sa.text(
                    "UPDATE packages SET status_label_raw = 'tocado' WHERE id = 1"))
                escritura_abierta.set()
                lectura_hecha.wait(10)
                s.rollback()
            except Exception as e:      # pragma: no cover
                fallo.append(("escritor", e))
                escritura_abierta.set()
            finally:
                s.close()

        hilo = threading.Thread(target=escritor, daemon=True)
        hilo.start()
        assert escritura_abierta.wait(10), "el escritor no llegó a abrir la transacción"

        try:
            lector = get_session(db_path)
            assert lector.query(Package).count() >= 1
            lector.close()
        except Exception as e:          # pragma: no cover
            fallo.append(("lector", e))
        finally:
            lectura_hecha.set()
            hilo.join(10)

        assert not fallo, f"la concurrencia ha fallado: {fallo}"

    def test_varios_lectores_a_la_vez(self, db_path):
        sembrar(db_path)
        errores = []

        def leer():
            try:
                s = get_session(db_path)
                s.query(Package).all()
                s.close()
            except Exception as e:      # pragma: no cover
                errores.append(e)

        hilos = [threading.Thread(target=leer) for _ in range(8)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join(10)

        assert not errores


class TestFechasEnEspanol:
    def test_los_meses_salen_en_espanol(self):
        # strftime('%b') depende del locale, y en el container es C: salía
        # "Jan", "Aug", "Dec" en un panel escrito en español.
        assert formatear(datetime(2026, 1, 15), "%d %b") == "15 ene"
        assert formatear(datetime(2026, 8, 15), "%d %b") == "15 ago"
        assert formatear(datetime(2026, 12, 15), "%d %b") == "15 dic"

    def test_los_doce_meses_estan_cubiertos(self):
        assert len(MESES_ES) == 12
        for mes in range(1, 13):
            assert formatear(datetime(2026, mes, 1), "%b") == MESES_ES[mes - 1]

    def test_el_resto_del_patron_no_se_toca(self):
        assert formatear(datetime(2026, 3, 9, 14, 5), "%d/%m/%Y %H:%M") == "09/03/2026 14:05"

    def test_el_filtro_de_la_plantilla_convierte_a_hora_local(self, monkeypatch):
        # Guardado en UTC, mostrado en horario peninsular (verano: +2h).
        assert formato_local(datetime(2026, 8, 15, 10, 21)) == "15 ago 2026 · 12:21"

    def test_sin_fecha_no_revienta(self):
        assert formato_local(None) == "—"
