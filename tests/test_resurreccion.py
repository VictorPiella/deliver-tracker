"""
Regresión del bug de resurrección.

Borrar un paquete destruía en cascada sus package_events, y esos eventos son los
que guardan el gmail_message_id — justo lo que impide reprocesar un email ya
visto. Resultado: borrabas un paquete y el siguiente escaneo lo volvía a crear
desde los mismos emails, que seguían dentro de la ventana de búsqueda. Encima se
llevaba por delante el estado manual, porque el paquete recreado era una fila
nueva.

El arreglo es el borrado lógico: la fila se marca con deleted_at, los eventos se
quedan, el email no se reingiere y además el borrado es reversible.
"""
from conftest import sincronizar
from app.models import Package, PackageEvent, UnparsedEmail, get_session
from app.web import create_app


def app_de_pruebas(db_path):
    app = create_app(db_path=db_path, use_mock_gmail=True, enable_worker=False)
    # TESTING desactiva la comprobacion de CSRF; sin esto todos los POST de los
    # tests darian 400. La proteccion se prueba aparte, en test_csrf.py.
    app.config["TESTING"] = True
    return app


class TestBorrarNoResucita:
    def test_un_paquete_borrado_no_vuelve_tras_escanear(self, db_path):
        c = app_de_pruebas(db_path).test_client()
        sincronizar(c)

        paquetes = c.get("/api/packages").get_json()
        objetivo = paquetes[0]
        assert len(paquetes) == 6

        c.post(f"/package/{objetivo['id']}/delete")
        assert len(c.get("/api/packages").get_json()) == 5

        # El escaneo vuelve a ver exactamente los mismos emails.
        sincronizar(c)

        despues = c.get("/api/packages").get_json()
        assert len(despues) == 5
        ids = {p["external_package_id"] for p in despues}
        assert objetivo["external_package_id"] not in ids

    def test_aguanta_varios_escaneos_seguidos(self, db_path):
        c = app_de_pruebas(db_path).test_client()
        sincronizar(c)
        objetivo = c.get("/api/packages").get_json()[0]
        c.post(f"/package/{objetivo['id']}/delete")

        for _ in range(3):
            sincronizar(c)

        assert len(c.get("/api/packages").get_json()) == 5

    def test_los_eventos_se_conservan_para_poder_restaurar(self, db_path):
        c = app_de_pruebas(db_path).test_client()
        sincronizar(c)
        objetivo = c.get("/api/packages").get_json()[0]

        s = get_session(db_path)
        eventos_antes = s.query(PackageEvent).count()
        s.close()

        c.post(f"/package/{objetivo['id']}/delete")
        sincronizar(c)

        s = get_session(db_path)
        assert s.query(PackageEvent).count() == eventos_antes
        s.close()

    def test_el_estado_manual_sobrevive_al_borrado_y_restauracion(self, db_path):
        c = app_de_pruebas(db_path).test_client()
        sincronizar(c)
        objetivo = c.get("/api/packages").get_json()[0]

        c.post(f"/package/{objetivo['id']}/status", data={"status": "customs"})
        c.post(f"/package/{objetivo['id']}/delete")
        sincronizar(c)
        c.post(f"/package/{objetivo['id']}/restore")

        restaurado = [p for p in c.get("/api/packages").get_json()
                      if p["id"] == objetivo["id"]][0]
        assert restaurado["status"] == "customs"
        assert restaurado["status_is_manual"] is True


class TestPapelera:
    def test_el_borrado_aparece_en_la_papelera(self, db_path):
        c = app_de_pruebas(db_path).test_client()
        sincronizar(c)
        objetivo = c.get("/api/packages").get_json()[0]
        c.post(f"/package/{objetivo['id']}/delete")

        html = c.get("/papelera").get_data(as_text=True)
        assert objetivo["external_package_id"] in html

    def test_restaurar_lo_devuelve_al_panel(self, db_path):
        c = app_de_pruebas(db_path).test_client()
        sincronizar(c)
        objetivo = c.get("/api/packages").get_json()[0]

        c.post(f"/package/{objetivo['id']}/delete")
        r = c.post(f"/package/{objetivo['id']}/restore")
        assert r.status_code == 302

        assert len(c.get("/api/packages").get_json()) == 6
        assert objetivo["external_package_id"] not in c.get("/papelera").get_data(as_text=True)

    def test_borrado_definitivo_quita_la_fila_entera(self, db_path):
        c = app_de_pruebas(db_path).test_client()
        sincronizar(c)
        objetivo = c.get("/api/packages").get_json()[0]

        c.post(f"/package/{objetivo['id']}/delete")
        c.post(f"/package/{objetivo['id']}/purge")

        s = get_session(db_path)
        assert s.query(Package).filter_by(id=objetivo["id"]).first() is None
        s.close()

    def test_vaciar_la_papelera(self, db_path):
        c = app_de_pruebas(db_path).test_client()
        sincronizar(c)
        for p in c.get("/api/packages").get_json()[:2]:
            c.post(f"/package/{p['id']}/delete")

        c.post("/papelera/vaciar")

        s = get_session(db_path)
        assert s.query(Package).filter(Package.deleted_at.isnot(None)).count() == 0
        s.close()

    def test_la_papelera_no_ensucia_el_panel_ni_healthz(self, db_path):
        c = app_de_pruebas(db_path).test_client()
        sincronizar(c)
        objetivo = c.get("/api/packages").get_json()[0]
        c.post(f"/package/{objetivo['id']}/delete")

        assert c.get("/healthz").get_json()["packages"] == 5
        html = c.get("/").get_data(as_text=True)
        assert html.count("data-delivered=") == 5

    def test_la_cabecera_cuenta_lo_que_hay_en_la_papelera(self, db_path):
        c = app_de_pruebas(db_path).test_client()
        sincronizar(c)
        objetivo = c.get("/api/packages").get_json()[0]

        c.post(f"/package/{objetivo['id']}/delete")
        assert 'class="navbadge">1<' in c.get("/").get_data(as_text=True)


class TestEmailsSinReconocer:
    def test_un_remitente_conocido_que_no_se_parsea_queda_registrado(self, db_path):
        app = app_de_pruebas(db_path)

        # Un email de AliExpress con un asunto que ningún patrón reconoce.
        mensajes = [{
            "id": "raro-1",
            "subject": "Plantilla nueva que no hemos visto nunca",
            "sender": "transaction@notice.aliexpress.com",
            "date": "2026-06-25T10:00:00+00:00",
        }]
        with app.app_context():
            from app.gmail_sync import run_sync
            resumen = run_sync(db_path, lambda q: mensajes, lambda m: {},
                               log=lambda *a: None)

        assert resumen["unparsed"] == 1
        s = get_session(db_path)
        fila = s.query(UnparsedEmail).one()
        assert fila.sender == "transaction@notice.aliexpress.com"
        assert "Plantilla nueva" in fila.subject
        s.close()

    def test_los_remitentes_irrelevantes_no_cuentan_como_fallo(self, db_path):
        from app.gmail_sync import run_sync

        mensajes = [{
            "id": "spam-1", "subject": "Ofertas", "sender": "marketing@loquesea.com",
            "date": "2026-06-25T10:00:00+00:00",
        }]
        resumen = run_sync(db_path, lambda q: mensajes, lambda m: {}, log=lambda *a: None)

        assert resumen["unparsed"] == 0
        assert resumen["irrelevant"] == 1
        assert get_session(db_path).query(UnparsedEmail).count() == 0

    def test_las_devoluciones_de_amazon_no_cuentan_como_fallo(self, db_path):
        # El parser las ignora a propósito; no son una plantilla rota.
        from app.gmail_sync import run_sync

        mensajes = [{
            "id": "dev-1", "subject": "Tu reembolso de BRIMETI",
            "sender": "devolucion@amazon.es", "date": "2026-06-25T10:00:00+00:00",
        }]
        resumen = run_sync(db_path, lambda q: mensajes, lambda m: {"plaintext_body": "", "html_body": ""},
                           log=lambda *a: None)

        assert resumen["unparsed"] == 0
        assert get_session(db_path).query(UnparsedEmail).count() == 0

    def test_no_duplica_el_mismo_email(self, db_path):
        from app.gmail_sync import run_sync

        mensajes = [{
            "id": "raro-1", "subject": "Plantilla nueva",
            "sender": "transaction@notice.aliexpress.com",
            "date": "2026-06-25T10:00:00+00:00",
        }]
        run_sync(db_path, lambda q: mensajes, lambda m: {}, log=lambda *a: None)
        run_sync(db_path, lambda q: mensajes, lambda m: {}, log=lambda *a: None)

        assert get_session(db_path).query(UnparsedEmail).count() == 1

    def test_el_aviso_sale_en_la_cabecera(self, db_path):
        from app.gmail_sync import run_sync

        run_sync(db_path, lambda q: [{
            "id": "raro-1", "subject": "Plantilla nueva",
            "sender": "transaction@notice.aliexpress.com",
            "date": "2026-06-25T10:00:00+00:00",
        }], lambda m: {}, log=lambda *a: None)

        c = app_de_pruebas(db_path).test_client()
        assert "Sin reconocer" in c.get("/").get_data(as_text=True)
        assert "Plantilla nueva" in c.get("/sin-reconocer").get_data(as_text=True)
