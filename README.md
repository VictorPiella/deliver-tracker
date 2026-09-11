# Delivery Tracker

Panel web de seguimiento de pedidos de Amazon y AliExpress, construido alrededor de los **emails de notificación de Gmail** — sin depender de 17track, sin scraping de webs de courier, sin APIs de pago.

Desarrollo en **Windows + Docker Desktop**, despliegue en **Unraid**.

## Cómo funciona

```
Gmail → parsers → SQLite → Flask UI
```

1. Un **worker en background** consulta Gmail cada hora buscando emails de Amazon, AliExpress, GLS y Correos.
2. Los **parsers** (`app/parsers/`) extraen el estado del pedido, número de tracking, producto e imagen directamente del cuerpo del email.
3. Los datos se guardan en una **base de datos SQLite** con una jerarquía `Order → Package → PackageEvent`.
4. El **panel Flask** muestra todos los paquetes con su estado actual, barra de progreso y timeline de eventos.

Todo corre en **un único container**: servidor web, worker y base de datos. No hace falta orquestar nada más.

> La app se llama **Delivery Tracker** (es lo que se ve en el panel y en Home Assistant).
> Los identificadores técnicos — el repo, la imagen, el servicio de compose, el
> container y la ruta de datos en Unraid — siguen siendo `deliver-tracker`: son la
> identidad con la que Docker y HA reconocen las cosas, y cambiarlos obligaría a
> mover `/mnt/user/appdata/deliver-tracker` y recrear las entidades de HA.

## Stack

| Capa | Tecnología |
|------|-----------|
| Backend | Python 3.12 + Flask |
| Base de datos | SQLite vía SQLAlchemy |
| Email | Gmail API (mock en dev, OAuth real en prod) |
| Servidor | gunicorn (1 worker + 8 threads) |
| Despliegue | Docker / Docker Compose |
| UI | Jinja2 + CSS vanilla (tema claro/oscuro, responsive) |

---

## Arranque rápido (Windows)

**Requisito:** [Docker Desktop](https://www.docker.com/products/docker-desktop/) instalado y arrancado.

```bash
copy .env.example .env
docker compose up --build
```

Abre `http://localhost:5000` y pulsa **"Escanear"**.

Por defecto arranca con `USE_MOCK_GMAIL=true`, es decir contra los emails de ejemplo de `app/mock_gmail.py` — **no toca tu Gmail**. Para conectar tu cuenta real, ver [Conectar Gmail real](#conectar-gmail-real-oauth).

### El bucle de desarrollo

`docker compose up` carga automáticamente `docker-compose.override.yml`, que monta `./app` dentro del container. **Al guardar un fichero, el servidor se recarga solo** — no hace falta rebuild.

| Cuándo | Qué hacer |
|--------|-----------|
| Cambias código Python, HTML o CSS | Nada: se recarga solo |
| Cambias `requirements.txt` | `docker compose up --build` |
| Cambias el `Dockerfile` | `docker compose up --build` |

```bash
docker compose logs -f          # ver logs en vivo
docker compose down             # parar
docker compose exec deliver-tracker bash   # shell dentro del container
```

> La recarga usa el reloader **por sondeo** de Werkzeug, no inotify: los cambios en un bind-mount que viene del sistema de ficheros de Windows no generan eventos inotify dentro de la VM de Linux. Por eso `watchdog` **no** está en `requirements-dev.txt` — instalarlo rompería la recarga. Ver `scripts/devserver.py`.

### Tests

```bash
docker compose exec deliver-tracker python -m pytest
```

O en el host, sin Docker:

```bash
py -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

### Depurar con VS Code

1. Pon `DEBUGPY=1` en tu `.env`
2. `docker compose up -d --build`
3. `F5` → **"Docker: adjuntar a deliver-tracker"**

Los breakpoints en `app/` funcionan contra el código que corre dentro del container (`.vscode/launch.json` traduce las rutas). Con `DEBUGPY_WAIT=1` la app no arranca hasta que te enganchas, útil para depurar el propio arranque.

---

## Desplegar en Unraid

La imagen se construye **en Windows** y se carga en Unraid por SSH. Unraid no compila nada y no hace falta ningún registro de imágenes.

```powershell
.\deploy\deploy-unraid.ps1 -UnraidHost 192.168.1.10
```

El script hace: `docker build` → `docker save` → `scp` → `docker load` → `docker compose up -d`.

**Requisito previo:** acceso SSH sin contraseña a Unraid.

```powershell
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh root@192.168.1.10 "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"
```

En Unraid, asegúrate de que la clave persiste a reinicios (*Settings → Management Access*, o copiándola a `/boot/config/ssh/`).

### Qué deja en Unraid

| Ruta | Contenido |
|------|-----------|
| `/boot/config/plugins/compose.manager/projects/deliver-tracker/docker-compose.yml` | El compose (copia de `deploy/docker-compose.unraid.yml`) |
| `/mnt/user/appdata/deliver-tracker/` | `packages.db`, `credentials.json`, `token.json` — **lo único que hay que respaldar** |

El script **no sobrescribe** un `docker-compose.yml` que ya exista en Unraid, para no borrar la `FLASK_SECRET_KEY` ni las credenciales MQTT que hayas editado allí. La primera vez que lo despliegues, edita ese fichero y cambia `FLASK_SECRET_KEY`:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Opciones útiles: `-User`, `-RemoteDir`, `-Tag`, `-SkipCompose` (sólo carga la imagen). `Get-Help .\deploy\deploy-unraid.ps1 -Detailed` para el resto.

---

## Conectar Gmail real (OAuth)

El scope usado es `gmail.readonly`: **sólo lectura**, la app nunca puede enviar ni borrar correo.

### 1. Crear credenciales en Google Cloud Console

1. Entra en [console.cloud.google.com](https://console.cloud.google.com/) y crea un proyecto (o reutiliza uno).
2. **APIs y servicios → Biblioteca** → busca "Gmail API" → **Habilitar**.
3. **APIs y servicios → Pantalla de consentimiento OAuth**:
   - Tipo de usuario: **Externo**
   - Rellena nombre de la app, email de soporte y de contacto (lo mínimo)
   - En "Scopes" no hace falta añadir nada a mano
   - En **"Usuarios de prueba"** añade tu propia cuenta de Gmail — mientras la app esté en modo *Testing*, sólo esas cuentas pueden autorizar
4. **APIs y servicios → Credenciales → Crear credenciales → ID de cliente de OAuth**:
   - Tipo de aplicación: **Aplicación de escritorio**
   - Descarga el JSON
5. Guárdalo como **`data/credentials.json`** en la raíz del repo (esa carpeta es la que monta el compose como `/data`).

### 2. Autorizar (una sola vez)

```bash
docker compose run --rm -p 8080:8080 deliver-tracker python -m scripts.gmail_auth
```

Imprime una URL: ábrela en tu navegador, autoriza, y el script guarda `data/token.json`. A partir de ahí la app sólo refresca ese token, lo que no necesita navegador — por eso funciona headless en Unraid.

> El script levanta el servidor de redirección dentro del container escuchando en `0.0.0.0:8080`, pero le dice a Google que redirija a `http://localhost:8080/`. Como ese puerto está publicado, el navegador de Windows cierra el círculo. Si 8080 te está ocupado: `--port 9090` (y publica ese).

### 3. Activar

Pon `USE_MOCK_GMAIL=false` en tu `.env` y:

```bash
docker compose up -d --build
```

### 4. Llevarlo a Unraid

Copia los dos ficheros al volumen persistente y asegúrate de que el compose de allí tiene `USE_MOCK_GMAIL: "false"`:

```powershell
scp data\credentials.json data\token.json root@192.168.1.10:/mnt/user/appdata/deliver-tracker/
```

> **Nota:** los parsers buscan remitentes de **`amazon.es`**. Si tu cuenta es de otro dominio (`amazon.com`, `amazon.fr`…), añade esos remitentes a `AMAZON_SENDERS` en `app/gmail_sync.py` y al mapa `SENDER_STATUS_MAP` en `app/parsers/amazon.py`.

---

## El panel

Cada fila del panel lleva:

- **Desplegable de estado** — marca un paquete a mano (p.ej. como *Entregado* porque ya lo tienes). Un estado manual **manda sobre los emails**: los que lleguen después se siguen guardando en el histórico, pero no cambian el estado. Se marca con la etiqueta `manual` junto al estado.
- **Volver a automático** (`↺`, sólo aparece si el estado es manual) — suelta el control y recalcula el estado desde los eventos recibidos.
- **Botón de papelera** — borra el paquete y su histórico, y retira su entidad de Home Assistant.

Arriba, las tres tarjetas de recuento (*Total / En tránsito / Entregados*) hacen de filtro, y el buscador filtra por nombre de producto, ID de paquete y número de seguimiento. Ambos funcionan en el cliente: la lista de un panel doméstico cabe entera en la página.

El panel sigue el **tema del sistema** (claro/oscuro) y el botón de la cabecera permite forzar uno; la elección se guarda en `localStorage`. El diseño es responsive: por debajo de 760px cada paquete pasa a ser una ficha apilada con controles a tamaño de dedo.

## Estados normalizados

Los estados de los emails se normalizan a un conjunto común, en orden de progreso:

`ordered` → `shipped` → `customs` → `customs_cleared` → `left_origin` → `local_carrier` → `in_country` → `at_distribution` → `out_for_delivery` → `delivered`

El estado de un paquete **sólo avanza**: un email que llega desordenado o repetido no rebobina uno ya entregado.

## Tracking de última milla (GLS / Correos)

- **GLS** trae el ID de pedido de AliExpress en el email ("Tu pedido `<package_id>` de Ecommerce..."), así que su evento se enlaza automáticamente al paquete ya existente (tracking real + estado).
- **Correos** no incluye ningún ID de Amazon/AliExpress — sólo su propio número de envío y quién lo remite. Enlazarlo a ciegas arriesgaría mezclar el tracking de un paquete con el producto de otro, así que se trackea como **entrada independiente** (sin nombre de producto ni imagen, sólo número de envío + estado).

No hace falta activar nada: ambos parsers corren siempre que el sync corre.

## Integración con Home Assistant (MQTT)

Cada paquete se publica como sensor vía [MQTT Discovery](https://www.home-assistant.io/integrations/mqtt/#discovery), agrupado bajo un device "Delivery Tracker". El `state` es el status normalizado (p.ej. `out_for_delivery`) y los atributos JSON traen título, transportista, tracking number y última actualización — útil para automatizaciones tipo *"avísame cuando algo pase a En reparto"*.

Desactivado por defecto. Pon `MQTT_ENABLED=true` y apunta `MQTT_HOST` al broker de HA. Se publica tras cada sync (manual o del worker) y la entidad se retira al borrar un paquete.

## Estructura

```
app/
├── parsers/
│   ├── amazon.py       # Emails de Amazon (envíos, actualizaciones)
│   ├── aliexpress.py   # AliExpress, incl. formato "merge" con tracking real
│   ├── gls.py          # GLS (última milla): enlaza al Package existente
│   └── correos.py      # Correos (última milla): entrada propia
├── models.py           # SQLAlchemy + estados normalizados + migraciones
├── timeutils.py        # Normalización de fechas a UTC naive
├── gmail_sync.py       # Coordinación: buscar emails → parsear → guardar
├── gmail_oauth.py      # Adaptador real de Gmail (OAuth)
├── mock_gmail.py       # Simulación de la API de Gmail con datos reales
├── sync.py             # Persistencia: Order/Package/Event, reglas de estado
├── mqtt_publish.py     # Publica a Home Assistant vía MQTT Discovery
├── cleanup.py          # Borrado manual + purga de entregados hace +15 días
├── worker.py           # Scheduler en background (sync horaria)
└── web.py              # Flask: app factory, rutas, templates
scripts/
├── gmail_auth.py       # Autorización OAuth inicial
└── devserver.py        # Arranque de desarrollo (recarga + debugpy)
deploy/
├── docker-compose.unraid.yml
└── deploy-unraid.ps1   # build en Windows → carga en Unraid
tests/                  # pytest
```

## Variables de entorno

Se leen de `.env` (copia `.env.example`).

| Variable | Default | Descripción |
|----------|---------|-------------|
| `HOST_PORT` | `5000` | Puerto del panel en el host |
| `DB_PATH` | `/data/packages.db` | Ruta de la base SQLite |
| `USE_MOCK_GMAIL` | `true` (dev) / `false` (prod) | `false` para usar OAuth de Gmail real |
| `ENABLE_BACKGROUND_WORKER` | `false` (dev) / `true` (prod) | Sincronización automática cada hora |
| `FLASK_SECRET_KEY` | `change-me-in-production` | Clave de sesión de Flask |
| `TZ` | `Europe/Madrid` | Zona horaria del container |
| `GMAIL_CREDENTIALS_PATH` | `<dir de DB_PATH>/credentials.json` | `credentials.json` de Google Cloud Console |
| `GMAIL_TOKEN_PATH` | `<dir de DB_PATH>/token.json` | Token OAuth generado por `scripts/gmail_auth.py` |
| `MQTT_ENABLED` | `false` | `true` para publicar a Home Assistant |
| `MQTT_HOST` | `localhost` | Host del broker MQTT |
| `MQTT_PORT` | `1883` | Puerto del broker MQTT |
| `MQTT_USERNAME` / `MQTT_PASSWORD` | _(vacío)_ | Credenciales del broker, si las requiere |
| `MQTT_DISCOVERY_PREFIX` | `homeassistant` | Prefijo de discovery que espera HA |
| `DEBUGPY` | `0` | `1` abre el puerto 5678 para VS Code |
| `DEBUGPY_WAIT` | `0` | `1` congela el arranque hasta que te enganchas |

> Las fechas se guardan siempre en **UTC sin tzinfo**. SQLite descarta el offset al guardar un datetime con `tzinfo`, así que los emails (que llegan con offset local) se convierten antes de persistirse — ver `app/timeutils.py`. `TZ` sólo afecta a los logs del container.

## Estado actual

- [x] Parser de Amazon (pedido realizado, enviado, en reparto, entregado)
- [x] Parser de AliExpress (incluido el formato "merge" con el tracking real del courier)
- [x] Tracking del último tramo (GLS enlazado, Correos como entrada independiente)
- [x] Panel Flask con lista, filtros, buscador y timeline de eventos
- [x] Cambio de estado manual y borrado desde el propio panel
- [x] Worker en background con sincronización horaria
- [x] Adaptador OAuth real de Gmail, autorizable desde dentro del container
- [x] Purga automática de entregados hace +15 días
- [x] Integración con Home Assistant vía MQTT Discovery
- [x] Dockerizado: bucle de desarrollo en Windows + despliegue a Unraid
- [x] Suite de tests (pytest)

## Próximos pasos

1. **Alertas push** — notificación en el móvil cuando un paquete pasa a "En reparto" o "Entregado".
2. **Soporte multi-cuenta Gmail** — varias cuentas (personal + trabajo) desde la UI.
3. **Más tiendas** — Temu, Shein, PCComponentes u otros con email de seguimiento estándar.
4. **Widget Home Assistant** — tarjeta Lovelace con miniatura del producto y barra de progreso.
