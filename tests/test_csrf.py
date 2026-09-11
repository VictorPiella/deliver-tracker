"""
Proteccion anti-CSRF de los POST destructivos.

Sin esto, cualquier pagina web que visites puede enviar en tu nombre un
formulario a http://<tu-unraid>:5055/package/7/delete: el navegador hace la
peticion igual, con tus cookies, y el panel no tiene forma de distinguirla de un
clic tuyo. Poner contrasena no bastaria — el navegador manda las credenciales de
HTTP Basic sin preguntar.

Estos tests corren con TESTING desactivado a proposito: es el unico sitio donde
la comprobacion esta activa, porque el resto de la suite la salta para no tener
que ir pidiendo tokens en cada POST.
"""
import re

import pytest

from app.web import create_app


@pytest.fixture
def app_real(db_path):
    """App con la comprobacion de CSRF activa, como en produccion."""
    app = create_app(db_path=db_path, use_mock_gmail=True, enable_worker=False)
    app.config["TESTING"] = False
    app.secret_key = "clave-de-test"
    return app


@pytest.fixture
def cliente(app_real):
    return app_real.test_client()


def token_de(cliente, ruta="/"):
    """Saca el token del formulario, como haria un navegador."""
    html = cliente.get(ruta).get_data(as_text=True)
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    return m.group(1) if m else None


class TestSinToken:
    def test_un_post_sin_token_se_rechaza(self, cliente):
        assert cliente.post("/sync").status_code == 400

    def test_borrar_sin_token_se_rechaza(self, cliente):
        assert cliente.post("/package/1/delete").status_code == 400

    def test_vaciar_la_papelera_sin_token_se_rechaza(self, cliente):
        assert cliente.post("/papelera/vaciar").status_code == 400

    def test_cambiar_estado_sin_token_se_rechaza(self, cliente):
        assert cliente.post("/package/1/status", data={"status": "delivered"}).status_code == 400

    def test_un_token_inventado_no_cuela(self, cliente):
        cliente.get("/")   # abre sesion
        assert cliente.post("/sync", data={"csrf_token": "me-lo-invento"}).status_code == 400


class TestConToken:
    def test_el_formulario_trae_el_token(self, cliente):
        assert token_de(cliente) is not None

    def test_con_el_token_del_formulario_funciona(self, cliente):
        t = token_de(cliente)
        assert cliente.post("/sync", data={"csrf_token": t}).status_code == 302

    def test_el_token_se_mantiene_entre_peticiones(self, cliente):
        assert token_de(cliente) == token_de(cliente, "/papelera")


class TestExcepciones:
    def test_healthz_sigue_libre(self, cliente):
        # El HEALTHCHECK de Docker no manda token. Si se le exigiera, Docker
        # daria el container por enfermo. Es GET y no hace nada destructivo.
        assert cliente.get("/healthz").status_code == 200


class TestCookieDeSesion:
    def test_samesite_lax_y_httponly(self, app_real):
        assert app_real.config["SESSION_COOKIE_SAMESITE"] == "Lax"
        assert app_real.config["SESSION_COOKIE_HTTPONLY"] is True


class TestTodosLosFormulariosLoLlevan:
    def test_ninguna_plantilla_se_queda_sin_token(self):
        """
        Un formulario POST sin el campo oculto dejaria de funcionar en cuanto se
        usara: mejor detectarlo aqui que en el panel.
        """
        import glob
        import io

        fallos = []
        for ruta in glob.glob("app/templates/*.html"):
            texto = io.open(ruta, encoding="utf-8").read()
            formularios = re.findall(r'<form\s+[^>]*method="POST"[^>]*>(.*?)</form>',
                                     texto, re.IGNORECASE | re.DOTALL)
            for cuerpo in formularios:
                if "csrf_token" not in cuerpo:
                    fallos.append(ruta)
        assert not fallos, f"formularios POST sin token en: {set(fallos)}"
