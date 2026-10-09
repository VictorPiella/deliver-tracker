"""
demo_data.py — un buzón de mentira para la demo y para las capturas.

Existe por dos motivos, y el segundo importa más de lo que parece:

1. Para probar el panel sin conectar una cuenta de Gmail de verdad.
2. Para que las capturas del README no salgan del buzón de nadie. Lo que uno
   compra dice bastante de uno, y un repositorio público lo indexa Google.

Va aparte de mock_gmail.py a propósito: ese lo usan los tests, y varios cuentan
paquetes. Añadirle mensajes los rompería a todos de golpe.

Todo lo de aquí está inventado. Los productos no existen, los números no
corresponden a ningún envío y las direcciones no aparecen. Lo que sí es real es
la FORMA de los emails: son las plantillas que de verdad manda cada remitente,
porque si no la demo no probaría nada.

El recorrido que enseña:

  - Un pedido de Amazon que llega en dos envíos distintos, cada uno con su
    nombre (durante mucho tiempo se veían como dos filas repetidas).
  - Un intento de entrega fallido.
  - Un pedido cancelado.
  - AliExpress escribiendo en inglés, que es como escribe ahora.
  - Un pedido de Shopify con cuatro artículos, tres de ellos el mismo producto
    en ediciones distintas — sin la variante no habría forma de distinguirlos.
  - Ese mismo envío pasando a CTT para la última milla: dos transportistas, un
    solo paquete.
  - Un envío de GLS que no se puede enlazar con ninguna tienda, para que se vea
    la etiqueta de "sin pedido".
"""

HOY = "2026-10-09"


def _f(dia: int, hora: str = "09:00:00") -> str:
    """Una fecha de octubre de 2026, para que la demo se vea reciente."""
    return f"2026-10-{dia:02d}T{hora}+00:00"


MENSAJES = [
    # --- Un pedido de Amazon, dos envíos, dos productos distintos -----------
    {"id": "d-amz-1", "sender": "auto-confirm@amazon.es", "date": _f(1, "10:12:00"),
     "subject": 'Pedido: "Estantería flotante de roble 80 cm" y 1 producto más'},
    {"id": "d-amz-2", "sender": "confirmar-envio@amazon.es", "date": _f(3, "08:40:00"),
     "subject": 'Enviado: "Estantería flotante de roble 80 cm"'},
    {"id": "d-amz-3", "sender": "confirmar-envio@amazon.es", "date": _f(3, "08:41:00"),
     "subject": 'Enviado: "Juego de 6 escuadras de acero negro"'},
    {"id": "d-amz-4", "sender": "shipment-tracking@amazon.es", "date": _f(6, "11:05:00"),
     "subject": 'En reparto: "Estantería flotante de roble 80 cm"'},
    {"id": "d-amz-5", "sender": "shipment-tracking@amazon.es", "date": _f(6, "19:30:00"),
     "subject": 'Entregado: "Estantería flotante de roble 80 cm"'},

    # --- Una entrega que no sale bien ---------------------------------------
    {"id": "d-amz-6", "sender": "auto-confirm@amazon.es", "date": _f(2, "17:20:00"),
     "subject": 'Pedido: "Cafetera italiana de aluminio 6 tazas"'},
    {"id": "d-amz-7", "sender": "confirmar-envio@amazon.es", "date": _f(4, "07:15:00"),
     "subject": 'Enviado: "Cafetera italiana de aluminio 6 tazas"'},
    {"id": "d-amz-8", "sender": "order-update@amazon.es", "date": _f(7, "13:02:00"),
     "subject": 'Intento de entrega: "Cafetera italiana de aluminio 6 tazas"'},

    # --- Y uno que se cancela -----------------------------------------------
    {"id": "d-amz-9", "sender": "auto-confirm@amazon.es", "date": _f(2, "09:05:00"),
     "subject": 'Pedido: "Taladro percutor 750W con maletín"'},
    {"id": "d-amz-10", "sender": "order-update@amazon.es", "date": _f(2, "09:48:00"),
     "subject": 'El producto se ha cancelado correctamente: "Taladro percutor 750W con maletín"'},

    # --- AliExpress, que ahora escribe en inglés ----------------------------
    {"id": "d-ali-1", "sender": "transaction@notice.aliexpress.com", "date": _f(1, "04:30:00"),
     "subject": "Package AP00912345678901: pedido enviado"},
    {"id": "d-ali-2", "sender": "transaction@notice.aliexpress.com", "date": _f(3, "22:10:00"),
     "subject": "Package AP00912345678901: left the departure region"},
    {"id": "d-ali-3", "sender": "transaction@notice.aliexpress.com", "date": _f(5, "06:45:00"),
     "subject": "Package AP00912345678901 has cleared customs"},
    {"id": "d-ali-4", "sender": "transaction@notice.aliexpress.com", "date": _f(6, "18:22:00"),
     "subject": "Package AP00912345678901: with local carrier"},

    # --- CTT se queda la última milla de ese mismo envío de AliExpress ------
    {"id": "d-ctt-1", "sender": "CTT Express <noreplyclientes@cttexpress.org>",
     "date": _f(7, "08:00:00"),
     "subject": "Información envío - 0082800099887766554433"},
    {"id": "d-ctt-2", "sender": "CTT Express <noreplyclientes@cttexpress.org>",
     "date": _f(8, "07:30:00"),
     "subject": "Información envío - 0082800099887766554433"},

    # --- Una tienda Shopify, con su desglose --------------------------------
    {"id": "d-sho-1", "sender": "La Tiendita <pedidos@latiendita.example>",
     "date": _f(4, "12:00:00"), "subject": "Order #10239 confirmed"},
    {"id": "d-sho-2", "sender": "La Tiendita <pedidos@latiendita.example>",
     "date": _f(8, "09:15:00"),
     "subject": "A shipment from order #10239 is on the way"},

    # --- Y un GLS que no se puede enlazar con nada --------------------------
    {"id": "d-gls-1", "sender": "GLS <noreply@comunicaciones.gls-spain.com>",
     "date": _f(7, "16:40:00"),
     "subject": "Tu envío 4455667788 está en camino"},
]


def _amazon_pedido(numero: str, llegada: str, imagen: str) -> dict:
    return {
        "plaintext_body": (
            f"Gracias por tu pedido\nLlegada {llegada}\n"
            f"Pedido n.º\n{numero}\n"
        ),
        "html_body": f'<img class="productImage" width="122" src="{imagen}" alt="">',
    }


_IMG = "https://placehold.co/200x200/1f2430/8ab4ff?text="


def _shopify_items(*items) -> str:
    """Una plantilla de notificación de Shopify con sus artículos."""
    filas = ""
    for titulo, variante, cantidad, img in items:
        v = f'<span class="order-list__item-variant">{variante}</span>' if variante else ""
        filas += (
            f'<td class="order-list__image-cell">'
            f'<img src="{img}" class="order-list__product-image"/></td>'
            f'<td class="order-list__product-description-cell">'
            f'<span class="order-list__item-title">{titulo}&nbsp;&times;&nbsp;{cantidad}</span>{v}</td>'
        )
    return ('<h2 class="order-number">Order #10239</h2>'
            '<div class="customer-info__item">Datos de entrega</div>'
            '<a href="https://shopify.com/policies">Aviso legal</a>' + filas)


CUERPOS = {
    "d-amz-1": _amazon_pedido("408-1122334-5566778", "entre el 3 y el 4 de octubre",
                              _IMG + "Estanteria"),
    "d-amz-6": _amazon_pedido("408-2233445-6677889", "el lunes, 6 de octubre",
                              _IMG + "Cafetera"),
    "d-amz-9": _amazon_pedido("408-3344556-7788990", "el 5 de octubre",
                              _IMG + "Taladro"),
    "d-amz-2": {"plaintext_body": "Tu paquete va en camino\nPedido n.º 408-1122334-5566778\n"
                                  "https://www.amazon.es/progress-tracker/package/"
                                  "ref=pe_?shipmentId=Dmo1EsT4n",
                "html_body": ""},
    "d-amz-3": {"plaintext_body": "Tu paquete va en camino\nPedido n.º 408-1122334-5566778\n"
                                  "https://www.amazon.es/progress-tracker/package/"
                                  "ref=pe_?shipmentId=Dmo2EsQ7a",
                "html_body": ""},
    "d-amz-4": {"plaintext_body": "Pedido n.º 408-1122334-5566778\n"
                                  "shipmentId=Dmo1EsT4n", "html_body": ""},
    "d-amz-5": {"plaintext_body": "Pedido n.º 408-1122334-5566778\n"
                                  "shipmentId=Dmo1EsT4n", "html_body": ""},
    "d-amz-7": {"plaintext_body": "Pedido n.º 408-2233445-6677889\n"
                                  "shipmentId=Dmo3CaF2e", "html_body": ""},
    "d-amz-8": {"plaintext_body": "No hemos podido entregar tu paquete.\n"
                                  "Pedido n.º 408-2233445-6677889\n"
                                  "shipmentId=Dmo3CaF2e", "html_body": ""},
    "d-amz-10": {"plaintext_body": "Pedido n.º 408-3344556-7788990\n", "html_body": ""},

    # CTT trae la referencia de AliExpress, que es lo que permite colgarlo del
    # paquete que ya seguimos en vez de crear una fila suelta.
    "d-ctt-1": {"plaintext_body":
                "CTT Express\nsu envío 0082800099887766554433 está en camino\n"
                "Referencia de envío: AP00912345678901\n"
                "Entrega prevista: 09/10/2026\n", "html_body": ""},
    "d-ctt-2": {"plaintext_body":
                "CTT Express\nsu envío 0082800099887766554433 está en reparto\n"
                "Referencia de envío: AP00912345678901\n", "html_body": ""},

    "d-sho-1": {
        "plaintext_body": "Thank you for your purchase!\nOrder #10239\n"
                          "View your order\n"
                          "( https://latiendita.example/12345/orders/"
                          "demo0000token0000demo/authenticate?key=demo_token )\n",
        "html_body": _shopify_items(
            ("Cuaderno de tapa dura A5", "Edición Bosque", 1, _IMG + "Bosque"),
            ("Cuaderno de tapa dura A5", "Edición Océano", 1, _IMG + "Oceano"),
            ("Cuaderno de tapa dura A5", "Edición Montaña", 1, _IMG + "Montana"),
            ("Pack de 3 bolígrafos de gel", None, 2, _IMG + "Boligrafos"),
        ),
    },
    "d-sho-2": {
        "plaintext_body": "Your order is on the way\nOrder #10239\n"
                          "SEUR tracking number: ES9988776655\n"
                          "View your order\n"
                          "( https://latiendita.example/12345/orders/"
                          "demo0000token0000demo/authenticate?key=demo_token )\n",
        "html_body": _shopify_items(
            ("Cuaderno de tapa dura A5", "Edición Bosque", 1, _IMG + "Bosque"),
        ),
    },

    # AliExpress solo nombra el producto en sus emails con bloque de detalle;
    # sin esto el paquete se queda sin nombre y sale como "Envío AP0091...".
    "d-ali-1": {
        "plaintext_body": "Tu pedido ha sido enviado",
        "html_body": (
            '<td class="EDM-ORDER-LOGISTICS-product-name"><div><span>'
            'Lámpara de escritorio plegable USB'
            '</span></div></td>'
            '<img src="https://ae-pic-a1.aliexpress-media.com/kf/demo.jpg" '
            'style="border-radius: 8px">'
        ),
    },

    "d-gls-1": {"plaintext_body":
                "Tu pedido 4455667788 de ARTESANIA DEL NORTE SL con Nº de "
                "seguimiento GLS 4455667788 está en camino.\n", "html_body": ""},
}


def demo_search_fn(query: str) -> list[dict]:
    """Devuelve el buzón de mentira entero. La query da igual: no hay Gmail."""
    return list(MENSAJES)


def demo_get_thread_fn(message_id: str) -> dict:
    return CUERPOS.get(message_id, {"plaintext_body": "", "html_body": ""})
