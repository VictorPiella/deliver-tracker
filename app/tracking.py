"""
tracking.py — enlace a la página de seguimiento de cada tienda/transportista.

No se guarda nada nuevo en la base: la URL se construye al vuelo con los datos
que ya tenemos (nº de pedido, nº de seguimiento). Así vale también para los
paquetes que ya estaban guardados, sin migración ninguna.

Los formatos salen de los emails reales, no de adivinar — salvo el de GLS, que
va marcado como tal más abajo.
"""
import re

# Nº de pedido de Amazon: 408-1234567-1234567
AMAZON_ORDER_RE = re.compile(r"^\d{3}-\d{7}-\d{7}$")

# Los ids de pedido de AliExpress son numéricos y largos. Los paquetes que sólo
# tienen un id de paquete (o el 'order-XXX' de reserva) no sirven para el enlace.
ALIEXPRESS_ORDER_RE = re.compile(r"^\d{12,20}$")


def url_de_seguimiento(source: str, order_id: str | None,
                       package_id: str | None = None,
                       tracking_number: str | None = None) -> str | None:
    """
    Devuelve la URL a la que llevar al usuario, o None si no se puede construir
    una fiable. Mejor no poner botón que poner uno que lleva a un 404.
    """
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
        # OJO: este es el ÚNICO que no está sacado de un email. GLS sólo mete
        # enlaces de redirección opacos (click.comunicaciones.gls-spain.com),
        # distintos en cada correo, que no sirven para construir nada estable.
        # Éste es su buscador público; si algún día deja de funcionar, es el
        # primer sitio donde mirar.
        numero = tracking_number or package_id
        if numero:
            return f"https://mygls.gls-spain.es/e/{numero}"
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
