"""
Estado puesto a mano desde el panel.

La regla: si has marcado un paquete a mano, los emails que lleguen después se
siguen guardando en el histórico pero NO cambian el estado. Si no fuera así,
marcar algo como entregado porque lo tienes en la mano duraría hasta el
siguiente escaneo.
"""
import re
from app.models import Package, PackageEvent
from app.sync import (
    apply_status, ingest_carrier_event, ingest_event, recompute_status_from_events,
)
from app.web import AUTO_STATUS

from conftest import EVENT_DATE, sincronizar


def evento(**kwargs):
    base = {
        "source": "aliexpress",
        "order_id": "3074309624382839",
        "package_id": "315193141453520012",
        "status": "shipped",
        "status_label_raw": "Paquete X: enviado",
        "title": "Tapón colador",
        "image_url": None,
        "message_id": "msg-1",
        "event_date": EVENT_DATE,
    }
    base.update(kwargs)
    return base


class TestApplyStatus:
    def test_por_defecto_no_es_manual(self, session):
        ingest_event(session, evento())
        assert session.query(Package).one().status_is_manual is False

    def test_un_estado_manual_bloquea_los_emails(self, session):
        ingest_event(session, evento(message_id="m1", status="shipped"))
        package = session.query(Package).one()

        package.status = "delivered"
        package.status_is_manual = True
        session.commit()

        # Llega un email de "en aduanas": se guarda, pero no toca el estado.
        assert ingest_event(session, evento(message_id="m2", status="customs")) is True
        package = session.query(Package).one()
        assert package.status == "delivered"
        assert session.query(PackageEvent).count() == 2

    def test_tambien_bloquea_un_email_que_avanzaria(self, session):
        # Aunque el email traiga un estado MÁS avanzado, manda el manual: si lo
        # has puesto a mano es porque sabes algo que el email no.
        ingest_event(session, evento(message_id="m1", status="shipped"))
        package = session.query(Package).one()
        package.status = "customs"
        package.status_is_manual = True
        session.commit()

        ingest_event(session, evento(message_id="m2", status="delivered"))
        assert session.query(Package).one().status == "customs"

    def test_bloquea_tambien_los_eventos_de_transportista(self, session):
        ingest_event(session, evento(message_id="m1", status="in_country"))
        package = session.query(Package).one()
        package.status = "delivered"
        package.status_is_manual = True
        session.commit()

        creado = ingest_carrier_event(session, {
            "package_id": "315193141453520012",
            "tracking_number": "1316197997",
            "status": "out_for_delivery",
            "status_label_raw": "En reparto",
            "message_id": "gls-1",
            "event_date": EVENT_DATE,
        }, courier="gls")

        assert creado is True                      # el evento sí se registra
        package = session.query(Package).one()
        assert package.status == "delivered"       # el estado no se toca
        assert package.courier == "gls"            # pero el transportista sí se aprende

    def test_devuelve_si_ha_cambiado_algo(self, session):
        ingest_event(session, evento(status="shipped"))
        package = session.query(Package).one()

        assert apply_status(package, "customs") is True
        assert apply_status(package, "ordered") is False     # retroceso
        package.status_is_manual = True
        assert apply_status(package, "delivered") is False   # bloqueado


class TestRecomputar:
    def test_vuelve_al_estado_que_dicen_los_eventos(self, session):
        ingest_event(session, evento(message_id="m1", status="shipped"))
        ingest_event(session, evento(message_id="m2", status="in_country"))

        package = session.query(Package).one()
        package.status = "ordered"           # marcado a mano hacia atrás
        package.status_is_manual = True
        session.commit()

        package.status_is_manual = False
        assert recompute_status_from_events(package) == "in_country"
        session.commit()
        assert session.query(Package).one().status == "in_country"

    def test_sin_eventos_no_cambia_nada(self, session):
        ingest_event(session, evento(status="shipped"))
        package = session.query(Package).one()
        for e in list(package.events):
            session.delete(e)
        session.commit()
        session.refresh(package)

        assert recompute_status_from_events(package) == package.status


class TestRutaDeEstado:
    def _id(self, client):
        return client.get("/api/packages").get_json()[0]["id"]

    def test_cambiar_estado_desde_el_panel(self, client):
        sincronizar(client)
        pkg_id = self._id(client)

        r = client.post(f"/package/{pkg_id}/status", data={"status": "out_for_delivery"})
        assert r.status_code == 302

        datos = [p for p in client.get("/api/packages").get_json() if p["id"] == pkg_id][0]
        assert datos["status"] == "out_for_delivery"
        assert datos["status_is_manual"] is True

    def test_volver_a_automatico(self, client):
        sincronizar(client)
        pkg_id = self._id(client)

        client.post(f"/package/{pkg_id}/status", data={"status": "ordered"})
        assert [p for p in client.get("/api/packages").get_json()
                if p["id"] == pkg_id][0]["status_is_manual"] is True

        client.post(f"/package/{pkg_id}/status", data={"status": AUTO_STATUS})
        datos = [p for p in client.get("/api/packages").get_json() if p["id"] == pkg_id][0]
        assert datos["status_is_manual"] is False
        assert datos["status"] != "ordered"      # recalculado desde los eventos

    def test_estado_invalido_se_rechaza(self, client):
        sincronizar(client)
        pkg_id = self._id(client)
        antes = [p for p in client.get("/api/packages").get_json() if p["id"] == pkg_id][0]

        r = client.post(f"/package/{pkg_id}/status", data={"status": "teletransportado"})
        assert r.status_code == 302

        despues = [p for p in client.get("/api/packages").get_json() if p["id"] == pkg_id][0]
        assert despues["status"] == antes["status"]
        assert despues["status_is_manual"] is False

    def test_paquete_inexistente_da_404(self, client):
        assert client.post("/package/99999/status", data={"status": "delivered"}).status_code == 404

    def test_el_panel_muestra_la_marca_de_manual(self, client):
        sincronizar(client)
        pkg_id = self._id(client)
        assert 'class="manual-mark"' not in client.get("/").get_data(as_text=True)

        client.post(f"/package/{pkg_id}/status", data={"status": "delivered"})
        assert 'class="manual-mark"' in client.get("/").get_data(as_text=True)


class TestControlesEnElPanel:
    def test_cada_fila_trae_desplegable_y_boton_de_borrar(self, client):
        sincronizar(client)
        html = client.get("/").get_data(as_text=True)
        n = len(client.get("/api/packages").get_json())

        assert html.count('name="status"') == n
        assert html.count("/status") >= n
        # Se cuentan las rutas por paquete, no "/delete" a secas: el panel tiene
        # además un formulario de borrado múltiple que apunta a /packages/delete.
        assert len(re.findall(r"/package/\d+/delete", html)) == n
        # El desplegable lista los 10 estados normalizados por fila.
        assert html.count('value="delivered"') == n

    def test_la_opcion_de_volver_a_automatico_solo_sale_si_hace_falta(self, client):
        sincronizar(client)
        assert AUTO_STATUS not in client.get("/").get_data(as_text=True)

        pkg_id = client.get("/api/packages").get_json()[0]["id"]
        client.post(f"/package/{pkg_id}/status", data={"status": "delivered"})
        assert AUTO_STATUS in client.get("/").get_data(as_text=True)

    def test_el_panel_trae_filtros_y_buscador(self, client):
        sincronizar(client)
        html = client.get("/").get_data(as_text=True)
        assert 'data-filter="transito"' in html
        assert 'data-filter="entregados"' in html
        assert 'id="buscador"' in html
        # Cada fila lleva los datos que usa el filtro en cliente.
        assert html.count("data-delivered=") == len(client.get("/api/packages").get_json())


class TestMigracion:
    def test_una_base_antigua_recibe_la_columna_nueva(self, tmp_path):
        """
        create_all() no añade columnas a tablas que ya existen, así que una base
        de la versión anterior se quedaría sin status_is_manual.
        """
        import sqlalchemy as sa
        from app.models import get_engine, get_session, _ENGINES, _SESSION_FACTORIES

        db = str(tmp_path / "antigua.db")

        # Simula el esquema viejo: packages sin status_is_manual.
        motor = sa.create_engine(f"sqlite:///{db}")
        with motor.begin() as conn:
            conn.execute(sa.text(
                "CREATE TABLE packages ("
                " id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL, source TEXT NOT NULL,"
                " external_package_id TEXT, courier TEXT, courier_tracking_number TEXT,"
                " status TEXT NOT NULL DEFAULT 'unknown', status_label_raw TEXT, eta TEXT,"
                " last_updated DATETIME, created_at DATETIME)"
            ))
            conn.execute(sa.text(
                "INSERT INTO packages (id, order_id, source, external_package_id, status)"
                " VALUES (1, 1, 'amazon', 'VIEJO', 'shipped')"
            ))
        motor.dispose()

        _ENGINES.pop(db, None)
        _SESSION_FACTORIES.pop(db, None)

        columnas = {c["name"] for c in sa.inspect(get_engine(db)).get_columns("packages")}
        assert "status_is_manual" in columnas

        # Y los datos que ya había siguen ahí, con el valor por defecto.
        package = get_session(db).query(Package).one()
        assert package.external_package_id == "VIEJO"
        assert package.status_is_manual is False
