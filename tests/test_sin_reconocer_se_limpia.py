"""
La pantalla de "sin reconocer" tiene que decir la verdad.

Su único valor es avisar de que una plantilla ha cambiado y estás dejando de
ver paquetes. Si se llena de avisos ya resueltos deja de servir para eso: nadie
mira una pantalla que siempre tiene siete cosas en rojo.

Y se llenaba sola. Los parsers aprenden — los emails de AliExpress en inglés
estuvieron semanas sin entenderse y un día se entendieron — pero sus avisos se
quedaban puestos para siempre.
"""
from app.gmail_sync import barrer_unparsed_resueltos, run_sync
from app.models import EmailProcesado, Package, PackageEvent, UnparsedEmail, get_session
from app.sync import ingest_event

from conftest import EVENT_DATE

ASUNTO_INGLES = "Package PHBW6T9812926090108552J has cleared customs"
REMITENTE_ALI = "AliExpress <transaction@notice.aliexpress.com>"


def aviso(db_path, message_id="m1", subject=ASUNTO_INGLES):
    s = get_session(db_path)
    s.add(UnparsedEmail(gmail_message_id=message_id, sender=REMITENTE_ALI,
                        subject=subject, event_date=EVENT_DATE))
    s.commit()
    s.close()


class TestElAvisoSeRetiraSoloCuandoSeResuelve:
    def test_si_el_email_acabo_entendiendose(self, db_path):
        aviso(db_path, "m1")
        s = get_session(db_path)
        ingest_event(s, {
            "source": "aliexpress", "order_id": "O1", "package_id": "P1",
            "status": "customs_cleared", "status_label_raw": ASUNTO_INGLES,
            "title": None, "image_url": None,
            "message_id": "m1", "event_date": EVENT_DATE,
        })
        assert barrer_unparsed_resueltos(s, log=lambda *a: None) == 1
        s.commit()
        assert s.query(UnparsedEmail).count() == 0
        s.close()

    def test_si_sigue_sin_entenderse_el_aviso_se_queda(self, db_path):
        """Lo importante de verdad: que no se borre lo que sí hay que mirar."""
        aviso(db_path, "m-raro", subject="Plantilla nueva que nadie sabe leer")
        s = get_session(db_path)
        assert barrer_unparsed_resueltos(s, log=lambda *a: None) == 0
        assert s.query(UnparsedEmail).count() == 1
        s.close()

    def test_un_email_enterrado_a_proposito_tampoco_es_un_aviso(self, db_path):
        """
        Si su paquete se borró del todo, el email tiene lápida y nunca volverá
        a entrar. Dejarlo en "sin reconocer" decía lo contrario de lo que pasa,
        y encima no había forma de quitarlo: reintentar no sirve, porque la
        lápida existe justo para que no vuelva.
        """
        aviso(db_path, "m-enterrado")
        s = get_session(db_path)
        s.add(EmailProcesado(gmail_message_id="m-enterrado"))
        s.commit()

        assert barrer_unparsed_resueltos(s, log=lambda *a: None) == 1
        s.commit()
        assert s.query(UnparsedEmail).count() == 0
        s.close()

    def test_el_escaneo_lo_hace_en_cada_pasada(self, db_path):
        aviso(db_path, "m1")
        s = get_session(db_path)
        ingest_event(s, {
            "source": "aliexpress", "order_id": "O1", "package_id": "P1",
            "status": "customs_cleared", "status_label_raw": "x",
            "title": None, "image_url": None,
            "message_id": "m1", "event_date": EVENT_DATE,
        })
        s.close()

        run_sync(db_path, lambda q: [], lambda m: {}, log=lambda *a: None)
        s = get_session(db_path)
        assert s.query(UnparsedEmail).count() == 0
        s.close()

    def test_entender_un_email_retira_su_aviso_en_el_acto(self, db_path):
        """Sin esperar al barrido: si entra en el panel, deja de ser un aviso."""
        aviso(db_path, "ali-1")
        run_sync(db_path,
                 lambda q: [{"id": "ali-1", "subject": ASUNTO_INGLES,
                             "sender": REMITENTE_ALI,
                             "date": "2026-10-01T10:00:00+00:00"}],
                 lambda m: {"plaintext_body": "", "html_body": ""},
                 log=lambda *a: None)
        s = get_session(db_path)
        assert s.query(UnparsedEmail).count() == 0
        assert s.query(Package).count() == 1
        s.close()


class TestReintentarLosQueQuedaronFuera:
    """
    El escaneo sólo mira su ventana de búsqueda. Un email que no se entendió en
    septiembre no vuelve a intentarse nunca, por mucho que el parser haya
    aprendido: ya ha quedado fuera.
    """

    def test_el_boton_los_recupera(self, client, db_path, app):
        aviso(db_path, "ali-viejo")
        app.config["GMAIL_GET_THREAD_FN"] = lambda mid: {"plaintext_body": "",
                                                         "html_body": ""}
        r = client.post("/sin-reconocer/reintentar", follow_redirects=True)
        assert "ya se entienden" in r.get_data(as_text=True)

        s = get_session(db_path)
        assert s.query(UnparsedEmail).count() == 0
        assert s.query(PackageEvent).filter_by(gmail_message_id="ali-viejo").count() == 1
        s.close()

    def test_lo_que_sigue_sin_entenderse_se_queda_y_lo_dice(self, client, db_path, app):
        aviso(db_path, "m-raro", subject="Plantilla nueva que nadie sabe leer")
        app.config["GMAIL_GET_THREAD_FN"] = lambda mid: {"plaintext_body": "",
                                                         "html_body": ""}
        r = client.post("/sin-reconocer/reintentar", follow_redirects=True)
        assert "se entiende todav" in r.get_data(as_text=True).lower()

        s = get_session(db_path)
        assert s.query(UnparsedEmail).count() == 1
        s.close()

    def test_un_email_que_ya_no_esta_en_gmail_no_rompe_nada(self, client, db_path, app):
        aviso(db_path, "m-borrado")

        def no_existe(mid):
            raise RuntimeError("404 del lado de Gmail")
        app.config["GMAIL_GET_THREAD_FN"] = no_existe

        r = client.post("/sin-reconocer/reintentar", follow_redirects=True)
        assert r.status_code == 200
        s = get_session(db_path)
        assert s.query(UnparsedEmail).count() == 1     # se queda, no se pierde
        s.close()

    def test_sin_nada_que_reintentar_lo_dice(self, client):
        r = client.post("/sin-reconocer/reintentar", follow_redirects=True)
        assert "No hay nada que reintentar" in r.get_data(as_text=True)


class TestDescartarAvisosAMano:
    """
    A veces un aviso es correcto y aun así no quieres volver a verlo: una
    promoción de Amazon que nunca será un paquete, un email raro de una sola
    vez. Descartarlo es decir "ya lo he mirado", no "esto se entiende".
    """

    def test_se_descarta_uno_solo(self, client, db_path):
        aviso(db_path, "m1", subject="Uno")
        aviso(db_path, "m2", subject="Dos")

        s = get_session(db_path)
        uno = s.query(UnparsedEmail).filter_by(subject="Uno").one().id
        s.close()

        client.post(f"/sin-reconocer/{uno}/descartar")

        # La fila no se borra, se marca: borrarla haría que el escaneo la
        # volviera a crear. Lo que cambia es lo que se ve.
        s = get_session(db_path)
        visibles = [f.subject for f in s.query(UnparsedEmail)
                    .filter(UnparsedEmail.descartado_at.is_(None)).all()]
        assert visibles == ["Dos"]
        assert s.query(UnparsedEmail).count() == 2
        s.close()

        html = client.get("/sin-reconocer").get_data(as_text=True)
        assert html.count("btn-descartar") == 1

    def test_lo_dice_al_descartar(self, client, db_path):
        aviso(db_path, "m1", subject="Plantilla rarisima")
        s = get_session(db_path)
        aid = s.query(UnparsedEmail).one().id
        s.close()

        r = client.post(f"/sin-reconocer/{aid}/descartar", follow_redirects=True)
        assert "Plantilla rarisima" in r.get_data(as_text=True)

    def test_un_aviso_que_no_existe_da_404(self, client):
        assert client.post("/sin-reconocer/99999/descartar").status_code == 404

    def test_descartar_no_lo_devuelve_en_el_siguiente_escaneo(self, client, db_path):
        """
        Lo que haría inútil el botón: que el escaneo volviera a ponerlo. No
        pasa, porque el email ya consta como visto por su gmail_message_id.
        """
        aviso(db_path, "ali-1", subject="Plantilla que nadie sabe leer")
        s = get_session(db_path)
        aid = s.query(UnparsedEmail).one().id
        s.close()
        client.post(f"/sin-reconocer/{aid}/descartar")

        run_sync(db_path,
                 lambda q: [{"id": "ali-1", "subject": "Plantilla que nadie sabe leer",
                             "sender": REMITENTE_ALI,
                             "date": "2026-10-01T10:00:00+00:00"}],
                 lambda m: {"plaintext_body": "", "html_body": ""},
                 log=lambda *a: None)

        # La fila sigue ahí, marcada: borrarla haría que el escaneo la creara
        # otra vez. Lo que importa es que no vuelva a la pantalla.
        #
        # Se cuentan los botones de descartar y no el texto del asunto: el
        # asunto sale igualmente en el mensaje de "Aviso descartado: ...", así
        # que buscarlo en la página daba un falso fallo.
        html = client.get("/sin-reconocer").get_data(as_text=True)
        assert html.count("btn-descartar") == 0

        s = get_session(db_path)
        assert s.query(UnparsedEmail).filter(
            UnparsedEmail.descartado_at.is_(None)).count() == 0
        s.close()

    def test_cada_fila_trae_su_boton(self, client, db_path):
        aviso(db_path, "m1", subject="Uno")
        aviso(db_path, "m2", subject="Dos")
        html = client.get("/sin-reconocer").get_data(as_text=True)
        assert html.count("btn-descartar") == 2
        assert html.count('name="csrf_token"') >= 2
