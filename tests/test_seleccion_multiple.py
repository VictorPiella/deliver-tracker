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


class TestElScriptDeSeleccionNoEstaRoto:
    """
    El arrastre no funcionaba y no era por la lógica: el script entero no
    compilaba. Un `\\n` dentro de un confirm se había convertido en un salto de
    línea de verdad y partía la cadena en dos, así que el navegador descartaba
    TODO el bloque con un SyntaxError silencioso. La página se veía bien y nada
    respondía.

    Un test no puede ejecutar JavaScript, pero sí puede detectar la forma del
    fallo: una cadena de texto no puede cruzar un salto de línea.
    """

    def script_de_seleccion(self, client):
        html = client.get("/").get_data(as_text=True)
        inicio = html.index("const seleccion = new Set()")
        return html[html.index("<script>", inicio - 2000):html.index("</script>", inicio)]

    def test_ninguna_cadena_se_parte_en_dos_lineas(self, client):
        import re as _re

        sincronizar(client)
        for linea in self.script_de_seleccion(client).splitlines():
            # Se quitan las cadenas bien cerradas; si queda una comilla suelta,
            # es que la cadena sigue en la línea siguiente.
            limpia = _re.sub(r"'(?:\\\\.|[^'\\\\])*'", "", linea)
            limpia = _re.sub(r'"(?:\\\\.|[^"\\\\])*"', "", limpia)
            limpia = limpia.split("//")[0]
            assert "'" not in limpia, f"comilla sin cerrar: {linea.strip()[:70]}"

    def test_el_arrastre_no_se_aparta_de_enlaces_ni_botones(self, client):
        """
        Las filas están cubiertas de cosas pinchables. Si el arranque del
        arrastre descartara los enlaces y botones como hace el click, no
        quedaría sitio por donde empezar: pulsaras donde pulsaras, nada.
        """
        sincronizar(client)
        script = self.script_de_seleccion(client)
        arranque = script[script.index("mousedown"):script.index("dragstart")]
        assert "input, textarea, select" in arranque
        assert "a, button" not in arranque

    def test_pero_el_click_si_los_respeta(self, client):
        """Pinchar un enlace tiene que seguir llevándote al enlace."""
        sincronizar(client)
        script = self.script_de_seleccion(client)
        clic = script[script.index("addEventListener('click'"):script.index("mousedown")]
        assert "a, button, input, select, textarea, details" in clic

    def test_hay_un_umbral_antes_de_considerarlo_arrastre(self, client):
        """Sin él, cualquier temblor al pinchar parecería un arrastre."""
        sincronizar(client)
        assert "huboArrastre" in self.script_de_seleccion(client)

    def test_se_cancela_el_click_de_despues_de_arrastrar(self, client):
        """
        Soltar encima del título te llevaría a la ficha, y soltar encima del
        botón de borrar sería bastante peor.
        """
        sincronizar(client)
        script = self.script_de_seleccion(client)
        suelta = script[script.index("mouseup"):]
        assert "preventDefault" in suelta
        assert "stopImmediatePropagation" in suelta
