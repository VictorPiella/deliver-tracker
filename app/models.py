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
    create_engine, inspect, text, Column, Integer, String, Boolean, DateTime, ForeignKey, Text
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

from .timeutils import utcnow

Base = declarative_base()


class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True)
    source = Column(String, nullable=False)          # "amazon" | "aliexpress"
    external_order_id = Column(String, nullable=False, index=True)  # 408-XXXXXXX-XXXXXXX / 3074309624382839
    title = Column(String)                            # nombre del primer producto, para mostrar en UI
    image_url = Column(String)                         # URL externa de la imagen del producto, si el email la trae
    created_at = Column(DateTime, default=utcnow)

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
    # Estado puesto a mano desde el panel. Mientras esté activo, la sincronización
    # sigue guardando los eventos que lleguen por email pero NO toca el estado:
    # si has marcado algo como entregado porque lo tienes en la mano, un email
    # que llega tarde no debe devolverlo a "en reparto".
    status_is_manual = Column(Boolean, nullable=False, default=False)
    # Borrado LOGICO. El borrado fisico resucitaba el paquete: al borrar en
    # cascada sus package_events se destruian los gmail_message_id, que son
    # justo lo que impide reprocesar un email, asi que el escaneo siguiente
    # volvia a crearlo. Marcandolo aqui, los eventos se conservan (el email ya
    # no se reingiere) y ademas el borrado es reversible desde la papelera.
    deleted_at = Column(DateTime, index=True)
    eta = Column(String)                                # fecha estimada de entrega (texto libre del email)
    last_updated = Column(DateTime, default=utcnow, onupdate=utcnow)
    created_at = Column(DateTime, default=utcnow)

    order = relationship("Order", back_populates="packages")
    events = relationship("PackageEvent", back_populates="package", cascade="all, delete-orphan", order_by="PackageEvent.event_date")


class UnparsedEmail(Base):
    """
    Emails de un remitente que SI nos interesa pero que ningun parser ha sabido
    leer. Los parsers son expresiones regulares contra el HTML de marketing de
    Amazon/AliExpress: el dia que cambien la plantilla, parse() devolvera None
    en silencio y dejarias de ver paquetes sin enterarte. Guardarlos aqui hace
    visible esa deriva en vez de esconderla en un contador de "saltados".
    """
    __tablename__ = "unparsed_emails"

    id = Column(Integer, primary_key=True)
    gmail_message_id = Column(String, index=True, unique=True)
    sender = Column(String)
    subject = Column(String)
    event_date = Column(DateTime)
    seen_at = Column(DateTime, default=utcnow)


class AppSetting(Base):
    """
    Pares clave/valor de la propia app. Ahora mismo guarda una sola cosa, la
    fecha del último escaneo con éxito, que es lo que permite calcular cuánto
    hay que mirar hacia atrás la próxima vez (ver gmail_sync.compute_lookback_days).
    """
    __tablename__ = "app_settings"

    key = Column(String, primary_key=True)
    value = Column(String)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


LAST_SYNC_KEY = "last_sync_at"


def get_setting(session, key: str) -> str | None:
    row = session.get(AppSetting, key)
    return row.value if row is not None else None


def set_setting(session, key: str, value: str) -> None:
    row = session.get(AppSetting, key)
    if row is None:
        session.add(AppSetting(key=key, value=value))
    else:
        row.value = value
        row.updated_at = utcnow()


def get_last_sync(session) -> datetime | None:
    crudo = get_setting(session, LAST_SYNC_KEY)
    if not crudo:
        return None
    try:
        return datetime.fromisoformat(crudo)
    except ValueError:
        return None


def set_last_sync(session, momento: datetime | None = None) -> datetime:
    momento = momento or utcnow()
    set_setting(session, LAST_SYNC_KEY, momento.isoformat())
    return momento


class PackageEvent(Base):
    __tablename__ = "package_events"

    id = Column(Integer, primary_key=True)
    package_id = Column(Integer, ForeignKey("packages.id"), nullable=False)
    status = Column(String, nullable=False)
    status_label_raw = Column(String)
    gmail_message_id = Column(String, index=True, unique=True)  # para no reprocesar el mismo email
    gmail_subject = Column(String)
    event_date = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=utcnow)

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
    "delivery_attempted", # intento de entrega fallido (nadie en casa, etc.)
    "delivered",          # entregado
]

# 'cancelled' NO va en STATUS_ORDER: no es un punto más avanzado del recorrido,
# es un final distinto. Se trata aparte en sync.apply_status, donde manda sobre
# cualquier otro estado — si el pedido se ha cancelado, da igual lo que dijeran
# los emails anteriores.
STATUS_CANCELLED = "cancelled"

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
    "delivery_attempted": "Entrega fallida",
    "delivered": "Entregado",
    "cancelled": "Cancelado",
    "unknown": "Desconocido",
}

# Estados en los que el paquete ya no va a moverse más.
STATUS_FINALES = {"delivered", STATUS_CANCELLED}


# Un engine (y su pool de conexiones) por ruta de base de datos. Antes se creaba
# uno nuevo en cada get_session(), lo que significaba abrir el fichero y ejecutar
# create_all() en *cada request* — coste inútil y una fuente de "database is
# locked" cuando el worker sincronizaba mientras el panel servía una página.
_ENGINES = {}
_SESSION_FACTORIES = {}


def get_engine(db_path="/data/packages.db"):
    engine = _ENGINES.get(db_path)
    if engine is None:
        engine = create_engine(
            f"sqlite:///{db_path}",
            echo=False,
            # El worker corre en otro thread que el servidor web y comparte engine.
            connect_args={"check_same_thread": False, "timeout": 15},
        )
        _afinar_sqlite(engine)
        Base.metadata.create_all(engine)
        _migrate(engine)
        _ENGINES[db_path] = engine
        _SESSION_FACTORIES[db_path] = sessionmaker(bind=engine)
    return engine


def _afinar_sqlite(engine) -> None:
    """
    WAL + espera ante bloqueos, en cada conexión nueva.

    Desde que el escaneo corre en un hilo aparte, hay un escritor (el escaneo,
    que puede tardar minutos) a la vez que lectores (cada página que sirves). En
    el modo `delete` por defecto, un escritor bloquea a TODOS los lectores: el
    panel se queda colgado mientras dura el escaneo y acaba dando "database is
    locked". Con WAL, lectores y escritor conviven sin estorbarse.

    El PRAGMA va por conexión, no por base, así que hay que ponerlo en cada una;
    `journal_mode=WAL` sí es persistente en el fichero, pero repetirlo no cuesta
    nada y deja el código honesto sobre lo que hace falta.
    """
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _configurar(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")   # suficiente con WAL
            cursor.execute("PRAGMA busy_timeout=15000")   # esperar, no reventar
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()


# Columnas añadidas después de la primera versión, con el SQL para incorporarlas
# a una base ya existente. create_all() sólo crea tablas que faltan por completo:
# a una tabla que ya existe no le añade columnas nuevas, así que sin esto una
# base creada con una versión anterior reventaría en la primera consulta.
_MIGRATIONS = [
    ("packages", "status_is_manual",
     "ALTER TABLE packages ADD COLUMN status_is_manual BOOLEAN NOT NULL DEFAULT 0"),
    ("packages", "deleted_at",
     "ALTER TABLE packages ADD COLUMN deleted_at DATETIME"),
]


def _migrate(engine) -> None:
    inspector = inspect(engine)
    tablas = set(inspector.get_table_names())
    for tabla, columna, sql in _MIGRATIONS:
        if tabla not in tablas:
            continue
        if columna in {c["name"] for c in inspector.get_columns(tabla)}:
            continue
        with engine.begin() as conn:
            conn.execute(text(sql))
        print(f"[models] Migración aplicada: {tabla}.{columna}")


def get_session(db_path="/data/packages.db"):
    get_engine(db_path)  # asegura engine + tablas creadas
    return _SESSION_FACTORIES[db_path]()
