"""
Regresión de la deriva horaria.

SQLite guarda los DATETIME como texto sin offset. Si se le pasa un datetime con
tzinfo, el offset se pierde en silencio y el valor queda leído como si fuera
UTC: un email de las 23:30+02:00 acababa guardado como las 23:30 UTC, dos horas
en el futuro. Estos tests fijan que todo lo que entra en la base es UTC naive.
"""
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import sqlalchemy as sa

from app.gmail_sync import parse_event_date
from app.models import Package
from app.sync import ingest_event
from app.timeutils import to_utc_naive, utcnow


class TestToUtcNaive:
    def test_convierte_desde_offset_positivo(self):
        dt = datetime(2026, 9, 10, 23, 30, tzinfo=timezone(timedelta(hours=2)))
        assert to_utc_naive(dt) == datetime(2026, 9, 10, 21, 30)

    def test_convierte_desde_offset_negativo(self):
        dt = datetime(2026, 9, 10, 20, 0, tzinfo=timezone(timedelta(hours=-5)))
        assert to_utc_naive(dt) == datetime(2026, 9, 11, 1, 0)

    def test_deja_intactos_los_naive(self):
        dt = datetime(2026, 9, 10, 23, 30)
        assert to_utc_naive(dt) == dt

    def test_el_resultado_nunca_lleva_tzinfo(self):
        dt = datetime(2026, 9, 10, 23, 30, tzinfo=timezone.utc)
        assert to_utc_naive(dt).tzinfo is None

    def test_none_pasa_como_none(self):
        assert to_utc_naive(None) is None


class TestUtcnow:
    def test_es_naive(self):
        assert utcnow().tzinfo is None

    def test_es_utc_de_verdad(self):
        delta = abs(utcnow() - datetime.now(timezone.utc).replace(tzinfo=None))
        assert delta < timedelta(seconds=5)


class TestParseEventDate:
    def test_cabecera_de_gmail_con_offset_local(self):
        # Es el camino real: gmail_oauth pasa la cabecera Date por
        # parsedate_to_datetime().isoformat() y eso conserva el offset.
        iso = parsedate_to_datetime("Wed, 10 Sep 2026 23:30:00 +0200").isoformat()
        assert parse_event_date(iso) == datetime(2026, 9, 10, 21, 30)

    def test_formato_con_z(self):
        assert parse_event_date("2026-06-25T22:06:02Z") == datetime(2026, 6, 25, 22, 6, 2)

    def test_formato_ya_en_utc(self):
        assert parse_event_date("2026-06-25T22:06:02+00:00") == datetime(2026, 6, 25, 22, 6, 2)

    def test_fecha_ilegible_cae_a_ahora(self):
        resultado = parse_event_date("no es una fecha")
        assert resultado.tzinfo is None
        assert abs(resultado - utcnow()) < timedelta(seconds=5)

    def test_fecha_vacia_no_revienta(self):
        assert parse_event_date("").tzinfo is None
        assert parse_event_date(None).tzinfo is None


class TestPersistencia:
    def test_la_fecha_guardada_es_la_utc_correcta(self, session, db_path):
        ingest_event(session, {
            "source": "aliexpress",
            "order_id": "1",
            "package_id": "p1",
            "status": "shipped",
            "status_label_raw": "enviado",
            "title": None,
            "image_url": None,
            "message_id": "m1",
            "event_date": parse_event_date("2026-09-10T23:30:00+02:00"),
        })

        crudo = session.execute(sa.text("SELECT last_updated FROM packages")).scalar()
        assert crudo.startswith("2026-09-10 21:30:00")
        assert session.query(Package).one().last_updated == datetime(2026, 9, 10, 21, 30)

    def test_los_defaults_del_modelo_tambien_son_utc(self, session):
        ingest_event(session, {
            "source": "amazon", "order_id": "408-1-1", "package_id": "x",
            "status": "ordered", "status_label_raw": "Pedido", "title": None,
            "image_url": None, "message_id": "m1", "event_date": utcnow(),
        })
        creado = session.query(Package).one().created_at
        assert creado.tzinfo is None
        assert abs(creado - utcnow()) < timedelta(seconds=10)
