"""
Ventana de búsqueda y durabilidad de lo ya escaneado.

Las dos preguntas que cubren estos tests:

1. ¿Qué pasa si el container está parado más tiempo del que abarca la ventana?
   Antes la ventana era fija (14 días) y los emails llegados durante un parón
   más largo quedaban fuera para siempre. Ahora se calcula desde el último
   escaneo con éxito, así que el parón se recupera solo.

2. ¿Qué pasa si borro los emails? Nada de lo ya escaneado: la base es la fuente
   de verdad y el sync sólo añade, nunca reconcilia ni borra.
"""
from datetime import timedelta

import pytest

from app.gmail_sync import (
    FIRST_SCAN_DAYS, MAX_LOOKBACK_DAYS, MIN_LOOKBACK_DAYS,
    build_search_query, compute_lookback_days, run_sync,
)
from app.models import Package, PackageEvent, get_last_sync, get_session, set_last_sync
from app.timeutils import utcnow


def sin_log(*args, **kwargs):
    pass


class TestVentanaAdaptativa:
    def test_sin_escaneo_previo_usa_la_ventana_de_primer_escaneo(self, session):
        assert compute_lookback_days(session) == (FIRST_SCAN_DAYS, True)

    def test_escaneo_reciente_usa_el_suelo(self, session):
        set_last_sync(session, utcnow() - timedelta(hours=1))
        session.commit()
        dias, primero = compute_lookback_days(session)
        assert dias == MIN_LOOKBACK_DAYS
        assert primero is False

    @pytest.mark.parametrize("dias_parado,minimo_esperado", [
        (20, 20),
        (45, 45),
        (90, 90),
    ])
    def test_un_paron_largo_se_recupera_entero(self, session, dias_parado, minimo_esperado):
        # La clave: la ventana cubre TODO el hueco, no un valor fijo. Si no,
        # los emails del parón no se recuperarían nunca.
        set_last_sync(session, utcnow() - timedelta(days=dias_parado))
        session.commit()
        dias, _ = compute_lookback_days(session)
        assert dias >= minimo_esperado

    def test_hay_un_techo_para_no_traerse_medio_buzon(self, session):
        set_last_sync(session, utcnow() - timedelta(days=5 * 365))
        session.commit()
        dias, _ = compute_lookback_days(session)
        assert dias == MAX_LOOKBACK_DAYS

    def test_la_ventana_acaba_en_la_query(self, session):
        set_last_sync(session, utcnow() - timedelta(days=40))
        session.commit()
        dias, _ = compute_lookback_days(session)
        assert build_search_query(dias).endswith(f"newer_than:{dias}d")


class TestMarcadorDeUltimoEscaneo:
    def test_un_sync_con_exito_lo_deja_puesto(self, db_path):
        from app.mock_gmail import mock_get_thread_fn, mock_search_fn

        assert get_last_sync(get_session(db_path)) is None
        resumen = run_sync(db_path, mock_search_fn, mock_get_thread_fn, log=sin_log)

        guardado = get_last_sync(get_session(db_path))
        assert guardado is not None
        assert abs(guardado - utcnow()) < timedelta(seconds=30)
        assert resumen["synced_at"] == guardado

    def test_si_gmail_falla_el_marcador_no_avanza(self, db_path):
        def gmail_roto(query):
            raise RuntimeError("token caducado")

        set_last_sync(get_session(db_path), utcnow() - timedelta(days=30))
        get_session(db_path).commit()
        antes = get_last_sync(get_session(db_path))

        with pytest.raises(RuntimeError):
            run_sync(db_path, gmail_roto, lambda m: {}, log=sin_log)

        # Si avanzara, el siguiente escaneo miraría una ventana corta y se
        # saltaría justo los emails que este intento no llegó a leer.
        assert get_last_sync(get_session(db_path)) == antes

    def test_el_segundo_sync_ya_no_es_primer_escaneo(self, db_path):
        from app.mock_gmail import mock_get_thread_fn, mock_search_fn

        assert compute_lookback_days(get_session(db_path))[1] is True
        run_sync(db_path, mock_search_fn, mock_get_thread_fn, log=sin_log)
        assert compute_lookback_days(get_session(db_path))[1] is False


class TestBorrarLosEmailsNoBorraNada:
    """
    Simula borrar el correo: el buzón se queda vacío y se vuelve a escanear.
    Nada de lo ya guardado debe desaparecer.
    """

    def test_los_paquetes_sobreviven_al_buzon_vacio(self, db_path):
        from app.mock_gmail import mock_get_thread_fn, mock_search_fn

        run_sync(db_path, mock_search_fn, mock_get_thread_fn, log=sin_log)
        paquetes_antes = get_session(db_path).query(Package).count()
        eventos_antes = get_session(db_path).query(PackageEvent).count()
        assert paquetes_antes == 6

        # El usuario borra todos los emails; Gmail ya no devuelve nada.
        resumen = run_sync(db_path, lambda q: [], lambda m: {}, log=sin_log)

        assert resumen["scanned"] == 0
        assert get_session(db_path).query(Package).count() == paquetes_antes
        assert get_session(db_path).query(PackageEvent).count() == eventos_antes

    def test_el_estado_y_el_historico_siguen_intactos(self, db_path):
        from app.mock_gmail import mock_get_thread_fn, mock_search_fn

        run_sync(db_path, mock_search_fn, mock_get_thread_fn, log=sin_log)
        s = get_session(db_path)
        antes = {p.external_package_id: (p.status, len(p.events))
                 for p in s.query(Package).all()}
        s.close()

        run_sync(db_path, lambda q: [], lambda m: {}, log=sin_log)

        s = get_session(db_path)
        despues = {p.external_package_id: (p.status, len(p.events))
                   for p in s.query(Package).all()}
        s.close()
        assert despues == antes

    def test_un_email_borrado_antes_de_escanearse_si_se_pierde(self, db_path):
        """
        La otra cara: lo que no llegó a escanearse no se recupera. Es el motivo
        de que la ventana adaptativa importe.
        """
        from app.mock_gmail import _MESSAGES, mock_get_thread_fn

        # El buzón sólo tiene la mitad de los mensajes cuando se escanea.
        mitad = list(_MESSAGES)[:6]
        run_sync(db_path, lambda q: mitad, mock_get_thread_fn, log=sin_log)
        parciales = get_session(db_path).query(PackageEvent).count()

        # Los otros se borran antes del siguiente escaneo: no aparecen nunca.
        run_sync(db_path, lambda q: mitad, mock_get_thread_fn, log=sin_log)
        assert get_session(db_path).query(PackageEvent).count() == parciales
