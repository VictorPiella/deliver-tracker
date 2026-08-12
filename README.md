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
│   ├── aliexpress.py   # Parser de emails de AliExpress (incl. formato "merge" con tracking real)
│   ├── gls.py           # GLS (última milla): enlaza al Package ya existente por package_id/tracking
│   └── correos.py      # Correos (última milla): sin ID compartido, se trackea como entrada propia
├── models.py           # SQLAlchemy: Order, Package, PackageEvent + estados normalizados
├── gmail_sync.py       # Coordinación: buscar emails → parsear → guardar en DB
├── gmail_oauth.py      # Adaptador real de Gmail (OAuth), sustituye a mock_gmail.py en producción
├── mock_gmail.py       # Simulación de la API de Gmail con datos reales (para dev)
├── mqtt_publish.py     # Publica cada paquete a Home Assistant vía MQTT Discovery
├── cleanup.py          # Borrado manual + purga automática de entregados hace +15 días
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

## Conectar Gmail real (OAuth)

Mientras `USE_MOCK_GMAIL=true` (default), la app usa datos de ejemplo. Para conectar tu Gmail real:

### 1. Crear credenciales en Google Cloud Console

1. Ve a [console.cloud.google.com](https://console.cloud.google.com/), crea un proyecto (o reutiliza uno).
2. **APIs y servicios → Biblioteca** → busca "Gmail API" → **Habilitar**.
3. **APIs y servicios → Pantalla de consentimiento OAuth**:
   - Tipo de usuario: **Externo**.
   - Rellena nombre de la app, email de soporte y de contacto (lo mínimo).
   - En "Scopes" no hace falta añadir nada a mano.
   - En "Usuarios de prueba", añade tu propia cuenta de Gmail (mientras la app esté en modo "Testing" solo esas cuentas pueden autorizar).
4. **APIs y servicios → Credenciales → Crear credenciales → ID de cliente de OAuth**:
   - Tipo de aplicación: **Aplicación de escritorio**.
   - Descarga el JSON generado.
5. Guarda ese fichero como `data/credentials.json` en la raíz del repo (la carpeta `data/` es la misma que monta `docker-compose.yml`).

### 2. Autorizar (una sola vez, en local — no dentro de Docker)

La autorización abre un navegador, así que se hace en tu Mac, no en el container headless de Unraid:

```bash
source .venv/bin/activate   # o el venv que uses
pip install -r requirements.txt
python -m scripts.gmail_auth
```

Esto abre el navegador para el consentimiento de Google y guarda `data/token.json`. A partir de ahí, la app solo necesita refrescar ese token (no vuelve a pedir navegador).

### 3. Activar en la app

```bash
USE_MOCK_GMAIL=false docker compose up --build
```

En Unraid, copia `credentials.json` y `token.json` al volumen persistente (`/mnt/user/appdata/deliver-tracker/`) y pon `USE_MOCK_GMAIL=false` en las variables de entorno del container.

El scope usado es `gmail.readonly` (solo lectura, la app nunca puede enviar ni borrar correo).

## Tracking de última milla (GLS / Correos)

Además de Amazon/AliExpress, el worker busca también emails de **GLS** y **Correos**
(transportistas que reparten el último tramo en España):

- **GLS** trae el ID de pedido de AliExpress directamente en el email
  ("Tu pedido `<package_id>` de Ecommerce..."), así que su evento se enlaza
  automáticamente al paquete ya existente (tracking number real + estado).
- **Correos** no incluye ningún ID de Amazon/AliExpress en el email — solo su
  propio número de envío y quién lo remite. Enlazarlo a ciegas arriesgaría
  mezclar el tracking de un paquete con el producto de otro, así que se
  trackea como su **propia entrada independiente** en el panel (sin nombre de
  producto ni imagen, solo número de envío + estado).

No hace falta activar nada: ambos parsers corren siempre que el sync corre.

## Integración con Home Assistant (MQTT)

Cada paquete se publica como sensor en HA vía [MQTT Discovery](https://www.home-assistant.io/integrations/mqtt/#discovery),
agrupados bajo un device "Deliver Tracker". El estado (`state`) es el status
normalizado (p.ej. `out_for_delivery`), y los atributos JSON traen título,
transportista, tracking number y última actualización — útil para automatizaciones
tipo "avísame cuando algo pase a 'out_for_delivery'".

Activar con las variables `MQTT_*` (ver tabla abajo). Desactivado por defecto
(`MQTT_ENABLED=false`); sin broker configurado no hace nada. Se publica tras
cada sync (manual o del worker) y se retira la entidad de HA al borrar un paquete
(manual o por la purga automática de entregados).

## Variables de entorno

| Variable | Default | Descripción |
|----------|---------|-------------|
| `DB_PATH` | `/data/packages.db` | Ruta de la base SQLite |
| `USE_MOCK_GMAIL` | `true` | `false` para usar OAuth de Gmail real |
| `ENABLE_BACKGROUND_WORKER` | `true` | Sincronización automática cada hora |
| `FLASK_SECRET_KEY` | `change-me-in-production` | Clave secreta de Flask |
| `GMAIL_CREDENTIALS_PATH` | `<dir de DB_PATH>/credentials.json` | Ruta al `credentials.json` de Google Cloud Console |
| `GMAIL_TOKEN_PATH` | `<dir de DB_PATH>/token.json` | Ruta al token OAuth generado por `scripts/gmail_auth.py` |
| `MQTT_ENABLED` | `false` | `true` para publicar cada paquete a Home Assistant vía MQTT |
| `MQTT_HOST` | `localhost` | Host del broker MQTT |
| `MQTT_PORT` | `1883` | Puerto del broker MQTT |
| `MQTT_USERNAME` / `MQTT_PASSWORD` | _(vacío)_ | Credenciales del broker, si las requiere |
| `MQTT_DISCOVERY_PREFIX` | `homeassistant` | Prefijo de discovery que espera HA |

## Estado actual

- [x] Parser de Amazon (pedido realizado, enviado, entregado, cancelado)
- [x] Parser de AliExpress (incluido el formato "merge" que expone el tracking number real del courier)
- [x] Panel Flask con lista de paquetes y timeline de eventos
- [x] Worker en background con sincronización horaria
- [x] Mock de Gmail con datos reales para desarrollo sin OAuth
- [x] Dockerizado y listo para Unraid
- [x] Adaptador OAuth real de Gmail (`app/gmail_oauth.py` + `scripts/gmail_auth.py`)
- [x] Borrado manual de paquetes + purga automática de entregados hace +15 días
- [x] Tracking del último tramo (GLS enlazado, Correos como entrada independiente)
- [x] Integración con Home Assistant vía MQTT Discovery

## Próximos pasos

### Corto plazo

1. **Alertas push** — notificación en el móvil cuando un paquete pasa a "En reparto" o "Entregado".
2. **Soporte multi-cuenta Gmail** — poder añadir varias cuentas (personal + trabajo) desde la UI.

### Largo plazo

3. **Más tiendas** — ampliar parsers a Temu, Shein, PCComponentes u otros que usen email de seguimiento estándar.
4. **Widget Home Assistant** — tarjeta Lovelace personalizada con miniatura del producto y barra de progreso.
