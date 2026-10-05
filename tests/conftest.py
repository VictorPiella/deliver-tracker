import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models import Base, get_session  # noqa: E402


# Fecha fija para los tests: así las aserciones no dependen de cuándo se ejecutan.
EVENT_DATE = datetime(2026, 6, 25, 12, 0, 0)


@pytest.fixture(autouse=True)
def sync_limpio():
    """
    Deja el estado del escaneo en blanco antes y después de cada test.

    worker._sync_state y worker._sync_lock son globales del módulo. Si el hilo
    de un test sigue vivo cuando arranca el siguiente, trigger_sync_now()
    devuelve False, no se lanza nada, y el test lee un estado que no es suyo:
    tests que pasan o fallan según lo cargada que esté la máquina.
    """
    import app.worker as worker

    worker.wait_for_sync(30)
    worker._sync_state.update(
        running=False, started_at=None, finished_at=None,
        last_summary=None, last_error=None,
    )
    worker._sync_state.pop("thread", None)
    yield
    worker.wait_for_sync(30)


@pytest.fixture(autouse=True)
def sin_archivado_automatico(monkeypatch):
    """
    Desactiva el archivado automático en todos los tests salvo los que lo
    prueban a propósito.

    Los emails de ejemplo son de junio de 2026. Como el escaneo archiva los
    entregados que pasan de PURGE_DELIVERED_AFTER_DAYS, cualquier test que
    sincronice vería dos paquetes irse a la papelera por una razón que no tiene
    nada que ver con lo que está comprobando.
    """
    import app.cleanup as cleanup
    monkeypatch.setattr(cleanup, "PURGE_DELIVERED_AFTER_DAYS", 0)


@pytest.fixture(autouse=True)
def sin_salir_a_internet(monkeypatch):
    """
    Ningún test habla con Correos de verdad.

    run_sync pregunta a la API de Correos al final (ver sync.actualizar_desde_correos),
    así que sin esto media suite estaría llamando a un servidor ajeno: lenta,
    dependiente de que haya red, y dando la lata a una API que no es nuestra.
    Los tests que prueban esa parte se traen su propia respuesta grabada.
    """
    import app.correos_api as correos_api
    monkeypatch.setattr(correos_api, "HABILITADO", False)


@pytest.fixture
def db_path(tmp_path):
    """Ruta a una SQLite nueva por test."""
    return str(tmp_path / "test.db")


@pytest.fixture
def session(db_path):
    s = get_session(db_path)
    yield s
    s.close()


@pytest.fixture
def app(db_path):
    from app.web import create_app
    application = create_app(db_path=db_path, use_mock_gmail=True, enable_worker=False)
    application.config["TESTING"] = True
    return application


@pytest.fixture
def client(app):
    return app.test_client()


def sincronizar(client):
    """
    Lanza el escaneo y espera a que acabe. /sync es asíncrono (ver
    worker.trigger_sync_now), así que un test que lea justo después vería la
    base a medio llenar.
    """
    from app.worker import wait_for_sync
    respuesta = client.post("/sync")
    assert wait_for_sync(30), "el escaneo no terminó a tiempo"
    return respuesta
