"""
Tests del panel: rutas, y que la app factory no arrastre los efectos
secundarios que tenía el módulo cuando creaba la app al importarse.
"""
from app.models import Package
from app.sync import ingest_event
from app.web import create_app, status_progress_pct

from conftest import EVENT_DATE, sincronizar


def sembrar(db_path, **kwargs):
    from app.models import get_session
    s = get_session(db_path)
    base = {
        "source": "aliexpress", "order_id": "3074309624382839",
        "package_id": "315193141453520012", "status": "in_country",
        "status_label_raw": "Paquete X: en tu país/región",
        "title": "Tapón colador para fregadero",
        "image_url": "https://ae-pic-a1.aliexpress-media.com/kf/x.jpg",
        "message_id": "m1", "event_date": EVENT_DATE,
    }
    base.update(kwargs)
    ingest_event(s, base)
    s.close()


class TestProgreso:
    def test_avanza_con_el_estado(self):
        assert status_progress_pct("ordered") < status_progress_pct("shipped")
        assert status_progress_pct("delivered") == 100

    def test_estado_desconocido_no_revienta(self):
        assert status_progress_pct("unknown") == 0
        assert status_progress_pct("inventado") == 0


class TestRutas:
    def test_panel_vacio(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "Sin paquetes registrados" in r.get_data(as_text=True)

    def test_panel_lista_los_paquetes(self, client, db_path):
        sembrar(db_path)
        html = client.get("/").get_data(as_text=True)
        assert "Tapón colador para fregadero" in html
        assert "En tu país" in html

    def test_detalle_con_timeline(self, client, db_path):
        sembrar(db_path, message_id="m1", status="shipped")
        sembrar(db_path, message_id="m2", status="customs")
        pkg_id = client.get("/api/packages").get_json()[0]["id"]

        html = client.get(f"/package/{pkg_id}").get_data(as_text=True)
        assert html.count("timeline") >= 1
        assert "Tapón colador para fregadero" in html

    def test_detalle_inexistente_da_404(self, client):
        assert client.get("/package/99999").status_code == 404

    def test_api_packages(self, client, db_path):
        sembrar(db_path)
        data = client.get("/api/packages").get_json()
        assert len(data) == 1
        assert data[0]["external_package_id"] == "315193141453520012"
        assert data[0]["status"] == "in_country"
        assert data[0]["last_updated"] is not None

    def test_healthz(self, client, db_path):
        assert client.get("/healthz").get_json() == {"status": "ok", "packages": 0}
        sembrar(db_path)
        assert client.get("/healthz").get_json()["packages"] == 1

    def test_borrar_paquete(self, client, db_path):
        sembrar(db_path)
        pkg_id = client.get("/api/packages").get_json()[0]["id"]

        r = client.post(f"/package/{pkg_id}/delete")
        assert r.status_code == 302
        assert client.get("/api/packages").get_json() == []

    def test_borrar_inexistente_da_404(self, client):
        assert client.post("/package/99999/delete").status_code == 404


class TestSync:
    def test_el_boton_escanear_ingiere_los_emails_del_mock(self, client, db_path):
        assert client.get("/api/packages").get_json() == []

        r = sincronizar(client)
        assert r.status_code == 302

        paquetes = client.get("/api/packages").get_json()
        assert len(paquetes) == 6
        fuentes = {p["source"] for p in paquetes}
        assert fuentes == {"amazon", "aliexpress"}

    def test_escanear_dos_veces_no_duplica(self, client):
        sincronizar(client)
        primera = client.get("/api/packages").get_json()
        sincronizar(client)
        segunda = client.get("/api/packages").get_json()
        assert len(primera) == len(segunda) == 6

    def test_el_email_de_devolucion_no_entra(self, client):
        sincronizar(client)
        titulos = [p["title"] or "" for p in client.get("/api/packages").get_json()]
        assert not any("reembolso" in t.lower() for t in titulos)


class TestAppFactory:
    def test_no_arranca_el_worker_si_no_se_pide(self, db_path):
        # El worker se arrancaba como efecto secundario del import del módulo,
        # así que con gunicorn --workers 2 corrían dos schedulers a la vez.
        import app.worker as worker_mod

        worker_mod.stop_worker()
        create_app(db_path=db_path, use_mock_gmail=True, enable_worker=False)
        assert worker_mod._scheduler is None

    def test_cada_app_usa_su_propia_base(self, tmp_path):
        a = create_app(db_path=str(tmp_path / "a.db"), use_mock_gmail=True, enable_worker=False)
        b = create_app(db_path=str(tmp_path / "b.db"), use_mock_gmail=True, enable_worker=False)
        a.config["TESTING"] = b.config["TESTING"] = True

        sincronizar(a.test_client())

        assert len(a.test_client().get("/api/packages").get_json()) == 6
        assert b.test_client().get("/api/packages").get_json() == []

    def test_crea_el_directorio_de_la_base_si_no_existe(self, tmp_path):
        # Primer arranque en Unraid: el volumen puede estar recién montado.
        destino = tmp_path / "nueva" / "carpeta" / "packages.db"
        create_app(db_path=str(destino), use_mock_gmail=True, enable_worker=False)
        assert destino.parent.is_dir()


class TestNombreDeLaApp:
    """
    El nombre visible vive en tres sitios (pestaña, cabecera, device de HA);
    estos tests evitan que se queden desparejados al renombrar.
    """

    NOMBRE = "Delivery Tracker"

    def test_en_la_pestaña_y_en_la_cabecera(self, client):
        html = client.get("/").get_data(as_text=True)
        assert f"· {self.NOMBRE}</title>" in html
        assert f'class="brand-name">{self.NOMBRE}<' in html

    def test_tambien_en_la_pagina_de_detalle(self, client, db_path):
        sembrar(db_path)
        pkg_id = client.get("/api/packages").get_json()[0]["id"]
        assert self.NOMBRE in client.get(f"/package/{pkg_id}").get_data(as_text=True)

    def test_el_device_de_home_assistant(self):
        from app.mqtt_publish import DEVICE
        assert DEVICE["name"] == self.NOMBRE
        # El identificador NO sigue al nombre: cambiarlo crearía un device nuevo
        # en HA y dejaría huérfanas las entidades existentes.
        assert DEVICE["identifiers"] == ["deliver_tracker"]
