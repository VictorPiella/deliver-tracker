"""
Contraseña opcional del panel (PANEL_PASSWORD).

Sin ella el panel va abierto, que es lo razonable en una LAN de casa. Con ella
puesta, todo pide credenciales salvo la sonda de salud del container.
"""
from base64 import b64encode

import pytest

from app.web import create_app


def cabecera(usuario, password):
    token = b64encode(f"{usuario}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture
def client_con_password(db_path, monkeypatch):
    monkeypatch.setenv("PANEL_PASSWORD", "secreta")
    monkeypatch.setenv("PANEL_USER", "victor")
    app = create_app(db_path=db_path, use_mock_gmail=True, enable_worker=False)
    app.config["TESTING"] = True
    return app.test_client()


class TestSinPassword:
    def test_por_defecto_el_panel_va_abierto(self, client):
        assert client.get("/").status_code == 200


class TestConPassword:
    def test_sin_credenciales_pide_autenticacion(self, client_con_password):
        r = client_con_password.get("/")
        assert r.status_code == 401
        assert "Basic" in r.headers.get("WWW-Authenticate", "")

    def test_con_las_credenciales_correctas_entra(self, client_con_password):
        r = client_con_password.get("/", headers=cabecera("victor", "secreta"))
        assert r.status_code == 200

    def test_password_incorrecta(self, client_con_password):
        assert client_con_password.get("/", headers=cabecera("victor", "otra")).status_code == 401

    def test_usuario_incorrecto(self, client_con_password):
        assert client_con_password.get("/", headers=cabecera("otro", "secreta")).status_code == 401

    def test_tambien_protege_las_acciones_destructivas(self, client_con_password):
        # Lo que de verdad importa: que nadie pueda borrar sin credenciales.
        assert client_con_password.post("/package/1/delete").status_code == 401
        assert client_con_password.post("/papelera/vaciar").status_code == 401
        assert client_con_password.post("/sync").status_code == 401

    def test_healthz_queda_fuera(self, client_con_password):
        # El HEALTHCHECK de Docker no manda credenciales; si se le pidieran,
        # Docker daria el container por enfermo y lo reiniciaria en bucle.
        assert client_con_password.get("/healthz").status_code == 200
