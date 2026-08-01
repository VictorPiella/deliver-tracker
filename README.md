# deliver-tracker

Panel web de seguimiento de pedidos de Amazon y AliExpress, construido alrededor de los **emails de notificación de Gmail** — sin depender de 17track, sin scraping de webs de courier, sin APIs de pago.

## Cómo funciona

```
Gmail → parsers → SQLite → Flask UI
```

1. Un **worker en background** consulta Gmail cada hora buscando emails de Amazon y AliExpress.
2. Los **parsers** (`app/parsers/`) extraen el estado del pedido, número de tracking, producto e imagen directamente del cuerpo del email.
3. Los datos se guardan en una **base de datos SQLite** con una jerarquía `Order → Package → PackageEvent`.
4. El **panel Flask** muestra todos los paquetes con su estado actual, barra de progreso y timeline de eventos.

## Stack

| Capa | Tecnología |
|------|-----------|
| Backend | Python 3.11 + Flask |
| Base de datos | SQLite vía SQLAlchemy |
| Email | Gmail API (mock en dev, OAuth real en prod) |
| Despliegue | Docker / Docker Compose |
| UI | Jinja2 + CSS vanilla |

## Estructura

```
app/
├── parsers/
│   ├── amazon.py       # Parser de emails de Amazon (envíos, actualizaciones)
│   └── aliexpress.py   # Parser de emails de AliExpress (incl. formato "merge" con tracking real)
├── models.py           # SQLAlchemy: Order, Package, PackageEvent + estados normalizados
├── gmail_sync.py       # Coordinación: buscar emails → parsear → guardar en DB
├── mock_gmail.py       # Simulación de la API de Gmail con datos reales (para dev)
├── worker.py           # Background thread que lanza sincronización cada hora
└── web.py              # Flask: rutas, templates, /sync endpoint
```

## Estados normalizados

Los estados de los emails de Amazon y AliExpress se normalizan a un conjunto común:

`ordered` → `shipped` → `customs` → `customs_cleared` → `left_origin` → `local_carrier` → `in_country` → `at_distribution` → `out_for_delivery` → `delivered`

## Levantar en local (Docker)

**Requisito:** [Docker Desktop](https://www.docker.com/products/docker-desktop/) instalado.

```bash
docker compose up --build
```

Abre `http://localhost:5000`. Pulsa **"Escanear ahora"** para sincronizar (en dev, contra datos de ejemplo).

Los datos persisten en `./data/packages.db` fuera del container.

## Levantar en Unraid

Mismo `docker-compose.yml`. Cambia la ruta del volumen:

```yaml
volumes:
  - /mnt/user/appdata/deliver-tracker:/data
```

## Variables de entorno

| Variable | Default | Descripción |
|----------|---------|-------------|
| `DB_PATH` | `/data/packages.db` | Ruta de la base SQLite |
| `USE_MOCK_GMAIL` | `true` | `false` para usar OAuth de Gmail real |
| `ENABLE_BACKGROUND_WORKER` | `true` | Sincronización automática cada hora |
| `FLASK_SECRET_KEY` | `change-me-in-production` | Clave secreta de Flask |

## Estado actual

- [x] Parser de Amazon (pedido realizado, enviado, entregado, cancelado)
- [x] Parser de AliExpress (incluido el formato "merge" que expone el tracking number real del courier)
- [x] Panel Flask con lista de paquetes y timeline de eventos
- [x] Worker en background con sincronización horaria
- [x] Mock de Gmail con datos reales para desarrollo sin OAuth
- [x] Dockerizado y listo para Unraid
- [ ] OAuth real de Gmail conectado
- [ ] Integración con Home Assistant vía MQTT

## Próximos pasos

### Corto plazo

1. **Gmail OAuth real** — completar el adaptador en `app/gmail_sync.py` (sección `NOTA_PRODUCCION`). Cambiar `USE_MOCK_GMAIL=false` en producción.
2. **Notificaciones Home Assistant** — publicar el estado de cada paquete en un topic MQTT (`homeassistant/sensor/package_<id>/state`) para crear entidades en HA y disparar automatizaciones (ej. notificación cuando llega a reparto).

### Medio plazo

3. **Tracking del último tramo** — parsear emails de Correos y GLS para tener el tracking real en España y no depender solo de los emails de origen.
4. **Alertas push** — notificación en el móvil cuando un paquete pasa a "En reparto" o "Entregado".
5. **Soporte multi-cuenta Gmail** — poder añadir varias cuentas (personal + trabajo) desde la UI.

### Largo plazo

6. **Más tiendas** — ampliar parsers a Temu, Shein, PCComponentes u otros que usen email de seguimiento estándar.
7. **Widget Home Assistant** — tarjeta Lovelace personalizada con miniatura del producto y barra de progreso.
