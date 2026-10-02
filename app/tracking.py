"""
tracking.py — enlace a la página de seguimiento de cada tienda/transportista.

No se guarda nada nuevo en la base: la URL se construye al vuelo con los datos
que ya tenemos (nº de pedido, nº de seguimiento). Así vale también para los
paquetes que ya estaban guardados, sin migración ninguna.

Los formatos salen de los emails reales, no de adivinar — salvo el de GLS, que
va marcado como tal más abajo.
"""
import os
import re

# Nº de pedido de Amazon: 408-1234567-1234567
AMAZON_ORDER_RE = re.compile(r"^\d{3}-\d{7}-\d{7}$")

# Los ids de pedido de AliExpress son numéricos y largos. Los paquetes que sólo
# tienen un id de paquete (o el 'order-XXX' de reserva) no sirven para el enlace.
ALIEXPRESS_ORDER_RE = re.compile(r"^\d{12,20}$")


def url_de_seguimiento(source: str, order_id: str | None,
                       package_id: str | None = None,
                       tracking_number: str | None = None,
                       guardada: str | None = None) -> str | None:
    """
    Devuelve la URL a la que llevar al usuario, o None si no se puede construir
    una fiable. Mejor no poner botón que poner uno que lleva a un 404.

    'guardada' es la URL que venía dentro del email (Package.tracking_url) y
    manda sobre todo lo demás: si el propio email dice a dónde ir, eso es más
    fiable que cualquier formato deducido aquí.
    """
    if guardada:
        return guardada

    source = (source or "").lower()

    if source == "amazon":
        # Formato de la página de detalle del pedido. Ojo: el parámetro es
        # 'orderID' con la D mayúscula.
        if order_id and AMAZON_ORDER_RE.match(order_id):
            return f"https://www.amazon.es/your-orders/order-details?orderID={order_id}"
        return None

    if source == "aliexpress":
        # https://www.aliexpress.com/p/order/detail.html?orderId=3074935419642839
        if order_id and ALIEXPRESS_ORDER_RE.match(order_id):
            return f"https://www.aliexpress.com/p/order/detail.html?orderId={order_id}"
        return None

    if source == "correos":
        # El que Correos pone en sus propios emails.
        numero = tracking_number or package_id
        if numero:
            return f"https://www.correos.es/es/es/herramientas/localizador/envios/detalle?tracking-number={numero}"
        return None

    if source == "gls":
        # GLS no admite enlace directo, y no por falta de intentarlo. Probadas
        # sus cuatro entradas contra la web real:
        #   mygls.gls-spain.es/e/<nº>              -> /not-found
        #   mygls.gls-spain.es/e/?codigo=&cpDst=   -> /not-found
        #   trackin-gls/inc/tracking_code.php      -> "'Postal code' is mandatory
        #       to this user" con cualquier numero, incluso desde el formulario
        #       de la propia GLS: roto de su lado
        #   gls-group.com/...?match=<nº>           -> acepta el numero por URL y
        #       lanza la busqueda, pero no encuentra estos envios (ni con el
        #       numero tal cual ni rellenado a 15 digitos con ceros)
        #
        # La pagina que si funciona es /parcel-tracking, pero es una SPA con
        # reCAPTCHA invisible y sin parametros en la URL: hay que teclear el nº
        # de envio y el codigo postal a mano. Asi que el enlace lleva ahi y el
        # panel se encarga de poner el numero en el portapapeles y recordarte el
        # codigo postal (ver POSTAL_CODE).
        return "https://mygls.gls-spain.es/parcel-tracking"

    if source == "shopify":
        # Aqui no hay nada que construir: el token de la pagina de estado del
        # pedido solo existe dentro del email. Pero ya no se tira — se guarda al
        # leerlo y llega por 'guardada', que se ha resuelto arriba. Si falta
        # (paquetes de antes de guardarla), no hay enlace y punto: inventar un
        # /account/orders llevaria a una pantalla de login.
        #
        # Lo que NO se hace es deducir la pagina del buscador de la tienda
        # ({tienda}/apps/trackyourorder?nums=...). Ese /apps/<lo-que-sea> es un
        # App Proxy de Shopify y la ruta la elige cada tienda segun la app de
        # seguimiento que haya instalado (/apps/track, /apps/parcelpanel,
        # /apps/track123...). Funciona en la tienda donde se vio y da un 404 en
        # la siguiente.
        return None

    return None


def url_del_transportista(courier: str | None, tracking_number: str | None) -> str | None:
    """
    Enlace al transportista cuando es distinto de la tienda: un paquete de
    Amazon repartido por Correos tiene dos páginas útiles, no una.
    """
    if not courier or not tracking_number:
        return None
    return url_de_seguimiento(courier.lower(), None, None, tracking_number)


# Codigo postal de destino. GLS lo pide a mano en su buscador y no hay forma de
# pasarselo por URL, asi que el panel al menos te lo recuerda en vez de que
# tengas que acordarte cada vez.
POSTAL_CODE = os.environ.get("POSTAL_CODE", "").strip()

# Fuentes cuyo seguimiento no admite enlace directo: hay que meter los datos a
# mano en su web.
FUENTES_A_MANO = {"gls"}


def requiere_datos_a_mano(source: str | None) -> bool:
    return (source or "").lower() in FUENTES_A_MANO
