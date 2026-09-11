"""
mock_gmail.py — adaptador de prueba que simula la API de Gmail con datos reales
capturados durante el desarrollo (mismos emails que vimos en la conversación:
WOLTU, Grupo de Seguridad, los paquetes AliExpress 315193141453520012 /
315193140438420019, y el email 'merge' de 2 paquetes).

Uso: sustituye a gmail_search_fn / gmail_get_thread_fn en gmail_sync.run_sync()
mientras no esté conectado el OAuth real. Cuando OAuth esté listo, se sustituye
este módulo por uno que llame a la API real de Gmail (ver NOTA_PRODUCCION en
gmail_sync.py) sin tocar el resto del pipeline.
"""

# Cada mensaje: {id, subject, sender, date}
_MESSAGES = [
    {
        "id": "19f0298eb0bd4003",
        "subject": 'Pedido: "WOLTU Mesitas de Noche, Set..." y 2 productos más',
        "sender": "auto-confirm@amazon.es",
        "date": "2026-06-26T06:23:23+00:00",
    },
    {
        "id": "19f0ce1c15a2981e",
        "subject": 'Enviado: "WOLTU Mesitas de Noche, Set..."',
        "sender": "confirmar-envio@amazon.es",
        "date": "2026-06-28T06:19:09+00:00",
    },
    {
        "id": "19ef544e9ef4a1a5",
        "subject": 'Pedido: "Grupo de Seguridad, Válvula..."',
        "sender": "auto-confirm@amazon.es",
        "date": "2026-06-23T16:16:34+00:00",
    },
    {
        "id": "19efa1c5c3bfd50c",
        "subject": 'Enviado: "Grupo de Seguridad, Válvula..."',
        "sender": "confirmar-envio@amazon.es",
        "date": "2026-06-24T14:50:22+00:00",
    },
    {
        "id": "19efdd5667b13dd8",
        "subject": 'En reparto: "Grupo de Seguridad, Válvula..."',
        "sender": "shipment-tracking@amazon.es",
        "date": "2026-06-25T08:11:20+00:00",
    },
    {
        "id": "19efe7ff0004b307",
        "subject": "Entregado: 1 producto | N.º de pedido 408-3320942-2576360",
        "sender": "order-update@amazon.es",
        "date": "2026-06-25T11:17:37+00:00",
    },
    {
        "id": "19f00d19895f8d65",
        "subject": "Paquete 315193141453520012: en aduanas",
        "sender": "transaction@notice.aliexpress.com",
        "date": "2026-06-25T22:06:02+00:00",
    },
    {
        "id": "19f0350bb90cd87b",
        "subject": "Paquete 315193141453520012: salió de la región de origen",
        "sender": "transaction@notice.aliexpress.com",
        "date": "2026-06-26T09:44:08+00:00",
    },
    {
        "id": "19f155baab3470aa",
        "subject": "Paquete 315193141453520012: en tu país/región",
        "sender": "transaction@notice.aliexpress.com",
        "date": "2026-06-29T21:49:15+00:00",
    },
    {
        "id": "19eb59244961e651",
        "subject": "El paquete 315193140438420019 está en el centro de distribución",
        "sender": "transaction@notice.aliexpress.com",
        "date": "2026-06-11T07:25:21+00:00",
    },
    {
        "id": "19eb22234e190869",
        "subject": "Paquete 315193140438420019: con transportista local",
        "sender": "transaction@notice.aliexpress.com",
        "date": "2026-06-10T15:24:04+00:00",
    },
    {
        "id": "19eabf125b2e20ed",
        "subject": "El paquete 315193140438420019 ha pasado la aduana",
        "sender": "transaction@notice.aliexpress.com",
        "date": "2026-06-09T10:32:47+00:00",
    },
    {
        "id": "19eb65391487f96f",
        "subject": "Paquete 315193140438420019 entregado",
        "sender": "transaction@notice.aliexpress.com",
        "date": "2026-06-11T10:56:29+00:00",
    },
    {
        "id": "19f1422f0a16835c",
        "subject": "Tus 2 paquetes tienen actualizaciones de entrega",
        "sender": "transaction@notice.aliexpress.com",
        "date": "2026-06-29T16:07:40+00:00",
    },
    # Email de devolución: debe ser ignorado por el parser de Amazon (should_ignore)
    {
        "id": "19f05bdc8a853f7d",
        "subject": "Tu reembolso de BRIMETI Luces LED COB Tira LED....",
        "sender": "devolucion@amazon.es",
        "date": "2026-06-26T21:02:30+00:00",
    },
]

# Cuerpos completos solo para los mensajes que el parser realmente necesita
# (Amazon siempre; AliExpress merge para extraer los bloques de paquetes).
# El resto de AliExpress individuales no necesitan cuerpo, así que no se listan.
_BODIES = {
    # Emails de "Pedido realizado". Llevan el nº de pedido y la fecha estimada
    # de entrega en el cuerpo; sin ellos el parser no tiene de dónde agarrar el
    # evento y el email acaba, con razón, en la lista de "sin reconocer".
    "19f0298eb0bd4003": {
        "plaintext_body": (
            "Gracias por tu pedido\n"
            "Llegada entre el 6 de julio y el 7 de julio\n"
            "Pedido n.º\n408-2435062-0199514\n"
            "Ver o gestionar el pedido\n"
            "https://www.amazon.es/gp/css/order-details?orderId=408-2435062-0199514\n"
        ),
        "html_body": (
            '<img class="productImage" width="122" '
            'src="https://m.media-amazon.com/images/I/61YAm9AW-OL._SS90_.jpg" '
            'alt="WOLTU Mesitas de Noche">'
        ),
    },
    "19ef544e9ef4a1a5": {
        "plaintext_body": (
            "Gracias por tu pedido\n"
            "Llegada el martes, 24 de junio\n"
            "Pedido n.º\n408-3320942-2576360\n"
            "Ver o gestionar el pedido\n"
            "https://www.amazon.es/gp/css/order-details?orderId=408-3320942-2576360\n"
        ),
        "html_body": (
            '<img class="productImage" width="122" '
            'src="https://m.media-amazon.com/images/I/41xJ8K2pQrL._SS90_.jpg" '
            'alt="Grupo de Seguridad Valvula">'
        ),
    },
    "19f0ce1c15a2981e": {
        "plaintext_body": (
            "Mis pedidos\n¡Tu paquete se ha enviado!\nPedido\nEnviado\nEn reparto\nEntregado\n"
            "Llegada entre el 6 de julio y el 7 de julio\nVictor - Taradell, Barcelona\n"
            "Pedido n.º\n408-2435062-0199514\nSeguimiento del envío\n"
            "https://www.amazon.es/progress-tracker/package?_encoding=UTF8&orderId=408-2435062-0199514"
            "&packageIndex=0&shipmentId=DLMJWgm4J&vt=NOTIFICATIONS&ref_=p_btn_fed_track_package\n"
        ),
        "html_body": (
            '<img class="productImage" width="122" '
            'src="https://m.media-amazon.com/images/I/61YAm9AW-OL._SS90_.jpg" '
            'alt="WOLTU Mesitas de Noche">'
        ),
    },
    "19efa1c5c3bfd50c": {
        "plaintext_body": (
            "¡Tu paquete se ha enviado!\nPedido n.º\n408-3320942-2576360\n"
            "Seguimiento del envío\nhttps://www.amazon.es/progress-tracker/package?"
            "_encoding=UTF8&orderId=408-3320942-2576360&packageIndex=0&shipmentId=AB12cd34E"
            "&vt=NOTIFICATIONS\n"
        ),
        "html_body": (
            '<img class="productImage" width="122" '
            'src="https://m.media-amazon.com/images/I/41xJ8K2pQrL._SS90_.jpg" '
            'alt="Grupo de Seguridad Valvula">'
        ),
    },
    "19efdd5667b13dd8": {
        "plaintext_body": (
            "En reparto\nPedido n.º\n408-3320942-2576360\n"
            "Seguimiento del envío\nhttps://www.amazon.es/progress-tracker/package?"
            "_encoding=UTF8&orderId=408-3320942-2576360&packageIndex=0&shipmentId=AB12cd34E"
            "&vt=NOTIFICATIONS\n"
        ),
        "html_body": "",
    },
    "19f1422f0a16835c": {
        "plaintext_body": "",
        "html_body": (
            '<a href="https://www.aliexpress.com/p/tracking/index.html?_addShare=no&_login=yes'
            '&tradeOrderId=074309624262839&trackingNumber=LP00827165784034&edm_log_data=...">'
            '<table><tbody><tr>'
            '<td class="EDM-MULTIPLE-PACKAGES-item-header" style="...">'
            '<img width="28" height="28" src="https://ae-pic-a1.aliexpress-media.com/kf/Sea490519c5994e13952bf62997b70a09o/84x84.png">'
            '<span style="...">En tránsito global</span></td>'
            '<td width="36" height="36" rowspan="2" class="EDM-MULTIPLE-PACKAGES-item-action"><img></td>'
            '</tr><tr><td class="EDM-MULTIPLE-PACKAGES-item-body" style="...">'
            '<table><tbody><tr>'
            '<td width="100" height="100" '
            'background="https://ae-pic-a1.aliexpress-media.com/kf/S5f22d5f83a684c7cb497645f45f1a7f10.jpg" '
            'class="EDM-MULTIPLE-PACKAGES-item-image" style="..."><div>&nbsp;</div><span></span></td>'
            '<td width="420" class="EDM-MULTIPLE-PACKAGES-item-content" style="...">'
            '<div class="EDM-MULTIPLE-PACKAGES-item-title" style="...">Tapón colador para fregad...</div>'
            '<div class="EDM-MULTIPLE-PACKAGES-item-sku" style="..."></div></td>'
            '</tr></tbody></table></td></tr></tbody></table></a>'
            '<a href="https://www.aliexpress.com/p/tracking/index.html?_addShare=no&_login=yes'
            '&tradeOrderId=074309624302839&trackingNumber=LP00825690963370&edm_log_data=...">'
            '<table><tbody><tr>'
            '<td class="EDM-MULTIPLE-PACKAGES-item-header" style="...">'
            '<img width="28" height="28" src="...">'
            '<span style="...">Ha salido de la central</span></td>'
            '<td width="36" height="36" rowspan="2" class="EDM-MULTIPLE-PACKAGES-item-action"><img></td>'
            '</tr><tr><td class="EDM-MULTIPLE-PACKAGES-item-body" style="...">'
            '<table><tbody><tr>'
            '<td width="100" height="100" '
            'background="https://ae-pic-a1.aliexpress-media.com/kf/S7df2fafd06e64d519c65ec7ae271579fi.jpg" '
            'class="EDM-MULTIPLE-PACKAGES-item-image" style="..."><div>&nbsp;</div><span></span></td>'
            '<td width="420" class="EDM-MULTIPLE-PACKAGES-item-content" style="...">'
            '<div class="EDM-MULTIPLE-PACKAGES-item-title" style="...">AC 220V DC 12V 24V 1/2 "3...</div>'
            '<div class="EDM-MULTIPLE-PACKAGES-item-sku" style="...">Normally Closed, 12V, 3/4"</div></td>'
            '</tr></tbody></table></td></tr></tbody></table></a>'
        ),
    },
}


def mock_search_fn(query: str) -> list[dict]:
    """
    Ignora la query (es un mock) y devuelve todos los mensajes simulados.
    En producción, esta función pasaría la query real a la API de Gmail.
    """
    return list(_MESSAGES)


def mock_get_thread_fn(message_id: str) -> dict:
    """Devuelve el cuerpo simulado del mensaje, o vacío si no lo necesitamos."""
    return _BODIES.get(message_id, {"plaintext_body": "", "html_body": ""})
