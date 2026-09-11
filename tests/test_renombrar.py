"""
Ponerle nombre a un paquete desde el panel.

El título que sale del email es a veces un churro de referencias
("PKCLEH0497437010108552K", "BCN8") que no dice nada. El alias lo arregla sin
tocar lo que dijo el email.
"""
from app.models import Order, Package, get_session
from app.sync import ingest_event

from conftest import EVENT_DATE, sincronizar


def sembrar(db_path, title="Referencia ilegible 993822X", package_id="P1", order_id="O1"):
    s = get_session(db_path)
    ingest_event(s, {
        "source": "correos", "order_id": order_id, "package_id": package_id,
        "status": "shipped", "status_label_raw": "x", "title": title,
        "image_url": None, "message_id": "m-" + package_id, "event_date": EVENT_DATE,
    })
    pid = s.query(Package).filter_by(external_package_id=package_id).one().id
    s.close()
    return pid


class TestRenombrar:
    def test_el_alias_manda_sobre_el_titulo_del_email(self, client, db_path):
        pid = sembrar(db_path)
        client.post(f"/package/{pid}/rename", data={"alias": "La tarjeta Revolut"})

        datos = [p for p in client.get("/api/packages").get_json() if p["id"] == pid][0]
        assert datos["title"] == "La tarjeta Revolut"
        assert datos["alias"] == "La tarjeta Revolut"

    def test_se_ve_en_la_lista(self, client, db_path):
        pid = sembrar(db_path)
        client.post(f"/package/{pid}/rename", data={"alias": "La tarjeta Revolut"})
        html = client.get("/").get_data(as_text=True)
        assert "La tarjeta Revolut" in html

    def test_vaciar_el_campo_devuelve_el_nombre_del_email(self, client, db_path):
        pid = sembrar(db_path)
        client.post(f"/package/{pid}/rename", data={"alias": "Otro nombre"})
        client.post(f"/package/{pid}/rename", data={"alias": "   "})

        datos = [p for p in client.get("/api/packages").get_json() if p["id"] == pid][0]
        assert datos["alias"] is None
        assert datos["title"] == "Referencia ilegible 993822X"

    def test_se_recorta_si_es_larguisimo(self, client, db_path):
        pid = sembrar(db_path)
        client.post(f"/package/{pid}/rename", data={"alias": "x" * 400})
        s = get_session(db_path)
        assert len(s.get(Package, pid).alias) == 120
        s.close()

    def test_paquete_inexistente_da_404(self, client):
        assert client.post("/package/99999/rename", data={"alias": "x"}).status_code == 404


class TestElAliasSobreviveATodo:
    def test_a_un_escaneo(self, client, db_path):
        """
        Lo importante: la sincronización sólo escribe Order.title, y el alias
        vive en el Package. Si estuviera en el Order, el siguiente email lo
        pisaría.
        """
        sincronizar(client)
        pid = client.get("/api/packages").get_json()[0]["id"]
        client.post(f"/package/{pid}/rename", data={"alias": "Mi nombre"})

        sincronizar(client)

        datos = [p for p in client.get("/api/packages").get_json() if p["id"] == pid][0]
        assert datos["title"] == "Mi nombre"

    def test_a_un_evento_nuevo_del_mismo_pedido(self, session, db_path):
        pid = sembrar(db_path)
        s = get_session(db_path)
        s.get(Package, pid).alias = "Mi nombre"
        s.commit()
        s.close()

        # Llega otro email del mismo pedido con otro título.
        s = get_session(db_path)
        ingest_event(s, {
            "source": "correos", "order_id": "O1", "package_id": "P1",
            "status": "delivered", "status_label_raw": "x",
            "title": "Titulo nuevo del email", "title_preciso": True,
            "image_url": None, "message_id": "m2", "event_date": EVENT_DATE,
        })
        p = s.get(Package, pid)
        assert p.alias == "Mi nombre"          # intacto
        assert p.order.title == "Titulo nuevo del email"   # el del email sí cambia
        s.close()

    def test_a_la_papelera_y_la_vuelta(self, client, db_path):
        pid = sembrar(db_path)
        client.post(f"/package/{pid}/rename", data={"alias": "Mi nombre"})
        client.post(f"/package/{pid}/delete")
        client.post(f"/package/{pid}/restore")

        datos = [p for p in client.get("/api/packages").get_json() if p["id"] == pid][0]
        assert datos["title"] == "Mi nombre"

    def test_dos_envios_del_mismo_pedido_pueden_llamarse_distinto(self, db_path):
        # Por eso el alias va en el Package y no en el Order.
        a = sembrar(db_path, package_id="ENVIO1", order_id="MISMO")
        b = sembrar(db_path, package_id="ENVIO2", order_id="MISMO")

        s = get_session(db_path)
        s.get(Package, a).alias = "El cargador"
        s.get(Package, b).alias = "La funda"
        s.commit()
        assert s.query(Order).count() == 1
        assert {p.alias for p in s.query(Package).all()} == {"El cargador", "La funda"}
        s.close()


class TestEnLaInterfaz:
    def test_cada_fila_trae_su_formulario(self, client):
        sincronizar(client)
        html = client.get("/").get_data(as_text=True)
        n = len(client.get("/api/packages").get_json())
        assert html.count('name="alias"') == n
        assert html.count("data-renombrar=") == n

    def test_el_formulario_lleva_token_csrf(self, client):
        sincronizar(client)
        html = client.get("/").get_data(as_text=True)
        trozo = html[html.index('class="rename-form"'):]
        assert 'name="csrf_token"' in trozo[:400]

    def test_el_detalle_enseña_el_nombre_original(self, client, db_path):
        pid = sembrar(db_path)
        client.post(f"/package/{pid}/rename", data={"alias": "La tarjeta Revolut"})
        html = client.get(f"/package/{pid}").get_data(as_text=True)
        assert "La tarjeta Revolut" in html
        assert "Referencia ilegible 993822X" in html   # sigue estando a la vista


class TestComoSeEntraAlDetalle:
    """
    Al meter el lápiz junto al título dejó de verse por dónde se entraba al
    detalle: el título seguía siendo un enlace, pero sin subrayado ni nada que
    lo delatara. Ahora hay tres caminos, y estos tests los sujetan.
    """

    def test_el_titulo_sigue_siendo_enlace(self, client):
        sincronizar(client)
        html = client.get("/").get_data(as_text=True)
        pid = client.get("/api/packages").get_json()[0]["id"]
        assert f'class="pkg-title" href="/package/{pid}"' in html

    def test_la_miniatura_tambien_lleva_al_detalle(self, client):
        sincronizar(client)
        html = client.get("/").get_data(as_text=True)
        n = len(client.get("/api/packages").get_json())
        assert html.count('class="thumb-wrap" href="/package/') == n

    def test_hay_un_chevron_explicito(self, client):
        sincronizar(client)
        html = client.get("/").get_data(as_text=True)
        n = len(client.get("/api/packages").get_json())
        assert html.count("btn-detalle") == n

    def test_los_tres_apuntan_al_mismo_sitio(self, client):
        import re

        sincronizar(client)
        html = client.get("/").get_data(as_text=True)
        # Se comprueba la coherencia DENTRO de cada fila, sin mezclarla con el
        # orden de la API: el panel ordena por fecha y la API por id.
        for trozo in html.split('<article class="pkg"')[1:]:
            fila = trozo[:trozo.index("</article>")]
            destinos = set(re.findall(r'href="(/package/\d+)"', fila))
            assert len(destinos) == 1, f"una fila apunta a varios paquetes: {destinos}"

    def test_el_detalle_responde(self, client):
        sincronizar(client)
        pid = client.get("/api/packages").get_json()[0]["id"]
        assert client.get(f"/package/{pid}").status_code == 200


class TestElBuscadorEncuentraLosDosNombres:
    def test_se_busca_por_el_nombre_nuevo_y_por_el_del_email(self, client, db_path):
        """
        Al renombrar, el buscador sólo indexaba el nombre nuevo. Si le pones
        "la tarjeta Revolut" a algo que en el email era "VGL INTERNATIONAL",
        buscar por "VGL" dejaba de encontrarlo.
        """
        pid = sembrar(db_path, title="VGL INTERNATIONAL TRADE MARKET SL")
        client.post(f"/package/{pid}/rename", data={"alias": "La tarjeta Revolut"})

        html = client.get("/").get_data(as_text=True)
        indice = html[html.index("data-search="):]
        indice = indice[:indice.index(">")].lower()

        assert "la tarjeta revolut" in indice
        assert "vgl international" in indice
