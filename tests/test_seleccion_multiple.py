"""
Borrar varios paquetes de una vez.

El arrastre y la tecla Supr son cosa del navegador y no se prueban aquí; lo que
sí se prueba es la ruta que acaba ejecutando el borrado, que es donde puede
hacer daño de verdad.
"""
import re

from app.models import Package, get_session
from app.sync import ingest_event

from conftest import EVENT_DATE, sincronizar


def sembrar(db_path, cuantos=3):
    s = get_session(db_path)
    ids = []
    for i in range(cuantos):
        ingest_event(s, {
            "source": "amazon", "order_id": f"408-000000{i}-0000000",
            "package_id": f"P{i}", "status": "shipped", "status_label_raw": "x",
            "title": f"Cosa {i}", "image_url": None,
            "message_id": f"m{i}", "event_date": EVENT_DATE,
        })
    for p in s.query(Package).all():
        ids.append(p.id)
    s.close()
    return ids


class TestBorrarVariosDeUnaVez:
    def test_van_todos_a_la_papelera(self, client, db_path):
        ids = sembrar(db_path)
        client.post("/packages/delete", data={"ids": ",".join(str(i) for i in ids)})

        s = get_session(db_path)
        assert all(s.get(Package, i).deleted_at is not None for i in ids)
        s.close()

    def test_solo_los_seleccionados(self, client, db_path):
        ids = sembrar(db_path)
        client.post("/packages/delete", data={"ids": str(ids[0])})

        s = get_session(db_path)
        assert s.get(Package, ids[0]).deleted_at is not None
        assert s.get(Package, ids[1]).deleted_at is None
        s.close()

    def test_a_la_papelera_no_al_vacio(self, client, db_path):
        """
        Lo que más importa: seleccionar quince y darle a Supr no puede ser más
        definitivo que borrarlos de uno en uno. Siguen recuperables.
        """
        ids = sembrar(db_path)
        client.post("/packages/delete", data={"ids": ",".join(str(i) for i in ids)})
        client.post(f"/package/{ids[0]}/restore")

        s = get_session(db_path)
        assert s.get(Package, ids[0]).deleted_at is None
        s.close()

    def test_acepta_varios_campos_ids(self, client, db_path):
        ids = sembrar(db_path)
        client.post("/packages/delete", data={"ids": [str(ids[0]), str(ids[1])]})

        s = get_session(db_path)
        assert s.get(Package, ids[0]).deleted_at is not None
        assert s.get(Package, ids[1]).deleted_at is not None
        s.close()

    def test_un_id_repetido_no_borra_dos_veces(self, client, db_path):
        ids = sembrar(db_path)
        r = client.post("/packages/delete",
                        data={"ids": f"{ids[0]},{ids[0]},{ids[0]}"},
                        follow_redirects=True)
        assert "1 paquete a la papelera" in r.get_data(as_text=True)

    def test_basura_en_la_lista_no_revienta(self, client, db_path):
        sembrar(db_path)
        r = client.post("/packages/delete", data={"ids": "no,soy,un,numero,,"},
                        follow_redirects=True)
        assert r.status_code == 200

    def test_un_id_que_no_existe_se_ignora(self, client, db_path):
        ids = sembrar(db_path)
        r = client.post("/packages/delete", data={"ids": f"{ids[0]},99999"},
                        follow_redirects=True)
        assert r.status_code == 200
        s = get_session(db_path)
        assert s.get(Package, ids[0]).deleted_at is not None
        s.close()

    def test_sin_nada_seleccionado_no_pasa_nada(self, client, db_path):
        ids = sembrar(db_path)
        client.post("/packages/delete", data={"ids": ""})
        s = get_session(db_path)
        assert all(s.get(Package, i).deleted_at is None for i in ids)
        s.close()

    def test_borrar_dos_veces_no_cuenta_el_mismo_dos_veces(self, client, db_path):
        ids = sembrar(db_path)
        client.post("/packages/delete", data={"ids": str(ids[0])})
        r = client.post("/packages/delete", data={"ids": str(ids[0])},
                        follow_redirects=True)
        assert "No se ha movido nada" in r.get_data(as_text=True)


class TestLaSeleccionEnElPanel:
    def test_cada_fila_dice_quien_es(self, client):
        """Sin data-id, el JS no sabría a quién está seleccionando."""
        sincronizar(client)
        html = client.get("/").get_data(as_text=True)
        n = len(client.get("/api/packages").get_json())
        assert len(re.findall(r'<article class="pkg" data-id="\d+"', html)) == n

    def test_el_formulario_de_borrado_multiple_lleva_csrf(self, client):
        sincronizar(client)
        html = client.get("/").get_data(as_text=True)
        trozo = html[html.index('id="form-borrado-multiple"'):]
        assert 'name="csrf_token"' in trozo[:400]

    def test_sin_token_csrf_no_se_borra_nada(self, db_path):
        """
        Esta ruta borra varios de golpe, así que es justo la que no puede quedar
        abierta a una petición de otra página.

        Con TESTING desactivado a propósito: el resto de la suite salta la
        comprobación para no ir pidiendo tokens en cada POST, así que si no se
        apaga aquí este test no probaría nada. Ver tests/test_csrf.py.
        """
        from app.web import create_app

        ids = sembrar(db_path)
        app = create_app(db_path=db_path, use_mock_gmail=True, enable_worker=False)
        app.config["TESTING"] = False
        app.secret_key = "clave-de-test"

        r = app.test_client().post("/packages/delete", data={"ids": str(ids[0])})
        assert r.status_code == 400

        s = get_session(db_path)
        assert s.get(Package, ids[0]).deleted_at is None
        s.close()
