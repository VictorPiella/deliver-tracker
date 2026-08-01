"""
Modelo de datos para package-tracker.

Jerarquía:
  Order (pedido)  -- 1:N -->  Package (paquete/envío)  -- 1:N -->  PackageEvent (cada email/estado)

- Amazon: normalmente 1 pedido = 1 paquete, pero puede haber varios envíos parciales.
- AliExpress: 1 pedido puede partirse en varios paquetes (vimos un caso con 4 productos
  en el mismo paquete, pero el pedido global puede tener más).
"""
from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, String, DateTime, ForeignKey, Text
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

Base = declarative_base()


class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True)
    source = Column(String, nullable=False)          # "amazon" | "aliexpress"
    external_order_id = Column(String, nullable=False, index=True)  # 408-XXXXXXX-XXXXXXX / 3074309624382839
    title = Column(String)                            # nombre del primer producto, para mostrar en UI
    image_url = Column(String)                         # URL externa de la imagen del producto, si el email la trae
    created_at = Column(DateTime, default=datetime.utcnow)

    packages = relationship("Package", back_populates="order", cascade="all, delete-orphan")

    __table_args__ = ()


class Package(Base):
    __tablename__ = "packages"

    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    source = Column(String, nullable=False)           # "amazon" | "aliexpress"
    external_package_id = Column(String, index=True)  # package ID de AliExpress, o None en Amazon (usa order id)
    courier = Column(String)                           # "correos" | "gls" | "amazon_logistics" | "cainiao" | None aún
    courier_tracking_number = Column(String)            # si lo conseguimos en el futuro (Correos/GLS real)
    status = Column(String, nullable=False, default="unknown")  # estado normalizado, ver STATUS_* abajo
    status_label_raw = Column(String)                  # texto tal cual vino del email, para debug
    eta = Column(String)                                # fecha estimada de entrega si viene en el email (texto libre por ahora)
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)

    order = relationship("Order", back_populates="packages")
    events = relationship("PackageEvent", back_populates="package", cascade="all, delete-orphan", order_by="PackageEvent.event_date")


class PackageEvent(Base):
    __tablename__ = "package_events"

    id = Column(Integer, primary_key=True)
    package_id = Column(Integer, ForeignKey("packages.id"), nullable=False)
    status = Column(String, nullable=False)
    status_label_raw = Column(String)
    gmail_message_id = Column(String, index=True, unique=True)  # para no reprocesar el mismo email
    gmail_subject = Column(String)
    event_date = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    package = relationship("Package", back_populates="events")


# Estados normalizados, en orden lógico de progreso (sirve para detectar si un evento es un "avance" o un duplicado/retroceso)
STATUS_ORDER = [
    "ordered",            # pedido realizado
    "shipped",            # enviado / pedido enviado
    "customs",            # en aduanas
    "customs_cleared",    # aduana superada
    "left_origin",        # salió de región de origen
    "local_carrier",      # con transportista local
    "in_country",         # en tu país/región
    "at_distribution",    # en centro de distribución
    "out_for_delivery",   # en reparto
    "delivered",          # entregado
]

STATUS_LABELS_ES = {
    "ordered": "Pedido realizado",
    "shipped": "Enviado",
    "customs": "En aduanas",
    "customs_cleared": "Aduana superada",
    "left_origin": "Salió de origen",
    "local_carrier": "Con transportista local",
    "in_country": "En tu país",
    "at_distribution": "En centro de distribución",
    "out_for_delivery": "En reparto",
    "delivered": "Entregado",
    "unknown": "Desconocido",
}


def get_engine(db_path="/data/packages.db"):
    return create_engine(f"sqlite:///{db_path}", echo=False)


def get_session(db_path="/data/packages.db"):
    engine = get_engine(db_path)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session()
