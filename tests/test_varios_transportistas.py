"""
Un envío que pasa por varios transportistas.

Un paquete de Shopify cruza media Europa con YunExpress y hace el último tramo
con CTT. Cada tramo tiene SU número, y ningún email relaciona uno con otro: el
de la tienda nunca nombra a CTT, y el de CTT nunca nombra a la tienda. Desde
fuera son dos envíos distintos.

Antes el paquete sólo podía guardar un número, y eso tenía una consecuencia
peor que verlo duplicado: al fusionarlos a mano se perdía uno de los dos, así
que el siguiente email de ese transportista volvía a crear otra fila. Fusionar
no se quedaba pegado y había que repetirlo cada vez.

Lo que estos tests sujetan es justo eso: enlazar se hace UNA vez.
"""
from app.models import NumeroDeSeguimiento, Package, get_session
from app.sync import (buscar_por_numero, ingest_carrier_event, ingest_event,
                      merge_packages, registrar_numero)

from conftest import EVENT_DATE

NUM_TIENDA = "YT2626900701670433"        # YunExpress, el del email de la tienda
NUM_ULTIMA_MILLA = "0082800045229702119525"   # CTT, el del email del transportista


def paquete_de_tienda(db_path):
    s = get_session(db_path)
    ingest_event(s, {
        "source": "shopify", "order_id": "mitienda.com#10239",
        "package_id": "mitienda.com#10239", "status": "shipped",
        "status_label_raw": "on the way", "title": "Juego de cartas y 3 productos más",
        "image_url": None, "courier": "yunexpress",
        "courier_tracking_number": NUM_TIENDA,
        "message_id": "tienda-1", "event_date": EVENT_DATE,
    })
    pid = s.query(Package).filter_by(source="shopify").one().id
    s.close()
    return pid


def email_de_ctt(message_id, status="out_for_delivery", eta=None):
    return {
        "package_id": None,                       # CTT no conoce a la tienda
        "tracking_number": NUM_ULTIMA_MILLA,
        "status": status, "status_label_raw": "Información envío",
        "eta": eta, "message_id": message_id, "event_date": EVENT_DATE,
    }


class TestSinEnlazarSonDosEnvios:
    def test_el_email_del_transportista_no_encuentra_nada(self, db_path):
        """
        Es correcto que no lo encuentre: no hay ni un dato en común. Por eso
        enlazarlos tiene que hacerlo una persona la primera vez.
        """
        paquete_de_tienda(db_path)
        s = get_session(db_path)
        assert ingest_carrier_event(s, email_de_ctt("ctt-1"), courier="ctt") is False
        s.close()


class TestEnlazarUnaVezYQueSeQuede:
    def enlazados(self, db_path):
        """Lo que haría el usuario: fusionar la fila de CTT dentro de la buena."""
        pid = paquete_de_tienda(db_path)
        s = get_session(db_path)
        ingest_event(s, {
            "source": "ctt", "order_id": NUM_ULTIMA_MILLA,
            "package_id": NUM_ULTIMA_MILLA, "status": "out_for_delivery",
            "status_label_raw": "Información envío", "title": None, "image_url": None,
            "courier": "ctt", "courier_tracking_number": NUM_ULTIMA_MILLA,
            "message_id": "ctt-1", "event_date": EVENT_DATE,
        })
        origen = s.query(Package).filter_by(source="ctt").one()
        merge_packages(s, origen, s.get(Package, pid))
        s.commit()
        s.close()
        return pid

    def test_queda_un_solo_paquete(self, db_path):
        pid = self.enlazados(db_path)
        s = get_session(db_path)
        assert s.query(Package).filter(Package.deleted_at.is_(None)).count() == 1
        assert s.get(Package, pid) is not None
        s.close()

    def test_se_guardan_los_dos_numeros(self, db_path):
        """
        El del destino también. Si sólo se apuntara el que llega, la cadena
        quedaría a medias: se vería el tramo nuevo y no el original.
        """
        pid = self.enlazados(db_path)
        s = get_session(db_path)
        numeros = {n.numero for n in s.get(Package, pid).numeros}
        assert NUM_TIENDA in numeros
        assert NUM_ULTIMA_MILLA in numeros
        s.close()

    def test_el_siguiente_email_del_transportista_ya_se_engancha_solo(self, db_path):
        """
        Lo que da sentido a todo esto. Antes el número de CTT se perdía al
        fusionar y el email siguiente creaba otra fila: había que fusionar una
        y otra vez, para siempre.
        """
        pid = self.enlazados(db_path)
        s = get_session(db_path)
        creado = ingest_carrier_event(s, email_de_ctt("ctt-2", eta="13/10/2026"),
                                      courier="ctt")
        assert creado is True
        assert s.query(Package).filter(Package.deleted_at.is_(None)).count() == 1
        assert s.get(Package, pid).eta == "13/10/2026"
        s.close()

    def test_el_titulo_bueno_sobrevive(self, db_path):
        """El de CTT no tiene nombre; el de la tienda sí. Gana el que dice algo."""
        pid = self.enlazados(db_path)
        s = get_session(db_path)
        p = s.get(Package, pid)
        assert (p.title or p.order.title) == "Juego de cartas y 3 productos más"
        s.close()

    def test_no_se_pierde_ningun_evento(self, db_path):
        pid = self.enlazados(db_path)
        s = get_session(db_path)
        assert len(s.get(Package, pid).events) == 2
        s.close()


class TestLaTablaDeNumeros:
    def test_un_numero_apunta_a_su_paquete(self, db_path):
        pid = paquete_de_tienda(db_path)
        s = get_session(db_path)
        registrar_numero(s, s.get(Package, pid), NUM_ULTIMA_MILLA, "ctt")
        s.commit()
        assert buscar_por_numero(s, NUM_ULTIMA_MILLA).id == pid
        s.close()

    def test_no_se_apunta_dos_veces(self, db_path):
        pid = paquete_de_tienda(db_path)
        s = get_session(db_path)
        p = s.get(Package, pid)
        assert registrar_numero(s, p, NUM_ULTIMA_MILLA, "ctt") is True
        assert registrar_numero(s, p, NUM_ULTIMA_MILLA, "ctt") is False
        s.commit()
        assert s.query(NumeroDeSeguimiento).filter_by(numero=NUM_ULTIMA_MILLA).count() == 1
        s.close()

    def test_un_numero_de_otro_paquete_no_se_roba(self, db_path):
        """
        Dos paquetes con el mismo número son el mismo envío, pero resolver eso
        es cosa de la fusión. Moverlo a la brava dejaría al otro sin su
        identificador y con sus eventos colgando de nada.
        """
        a = paquete_de_tienda(db_path)
        s = get_session(db_path)
        ingest_event(s, {
            "source": "ctt", "order_id": "OTRO", "package_id": "OTRO",
            "status": "shipped", "status_label_raw": "x", "title": None,
            "image_url": None, "message_id": "m2", "event_date": EVENT_DATE,
        })
        b = s.query(Package).filter_by(external_package_id="OTRO").one()
        registrar_numero(s, s.get(Package, a), NUM_ULTIMA_MILLA, "ctt")
        s.commit()

        assert registrar_numero(s, b, NUM_ULTIMA_MILLA, "ctt") is False
        assert buscar_por_numero(s, NUM_ULTIMA_MILLA).id == a
        s.close()

    def test_tambien_encuentra_los_guardados_antes_de_esta_tabla(self, db_path):
        """Los paquetes viejos sólo tienen la columna de siempre."""
        pid = paquete_de_tienda(db_path)
        s = get_session(db_path)
        s.query(NumeroDeSeguimiento).delete()      # como si la tabla no existiera
        s.commit()
        assert buscar_por_numero(s, NUM_TIENDA).id == pid
        s.close()

    def test_un_numero_vacio_no_apunta_a_nada(self, db_path):
        s = get_session(db_path)
        assert buscar_por_numero(s, None) is None
        assert buscar_por_numero(s, "") is None
        s.close()
