import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models import Base, get_session  # noqa: E402


# Fecha fija para los tests: así las aserciones no dependen de cuándo se ejecutan.
EVENT_DATE = datetime(2026, 6, 25, 12, 0, 0)


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
