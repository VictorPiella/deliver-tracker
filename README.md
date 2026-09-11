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

La imagen se construye **en Windows** y se empuja a Unraid por SSH. Unraid no
compila nada y no hace falta ningún registro de imágenes.

```powershell
.\deploy\deploy-unraid.ps1 -UnraidHost 192.168.1.10
```

El script hace: `docker build` → `docker save` → `scp` → `docker load` →
`docker compose up -d`.

### Preparar el acceso SSH (una sola vez)

**1. Genera la clave** en Windows, si no la tienes:

```bash
ssh-keygen -t ed25519 -C "deliver-tracker-deploy"
```

Sin contraseña: el script tiene que poder conectarse solo.

**2. Instala la pública en Unraid, desde su terminal web.** Es la via mas
directa: no pide contraseña (ya estás autenticado en la interfaz) y surte
efecto al momento.

En la web de Unraid, arriba a la derecha, hay un icono de terminal (`>_`) que
abre una consola de root en el navegador. Pega ahí esta línea, sustituyendo
`TU_CLAVE_PUBLICA` por el contenido de `~/.ssh/id_ed25519.pub`:

```bash
mkdir -p /root/.ssh /boot/config/ssh && echo 'TU_CLAVE_PUBLICA' >> /root/.ssh/authorized_keys && sort -u /root/.ssh/authorized_keys -o /root/.ssh/authorized_keys && cp /root/.ssh/authorized_keys /boot/config/ssh/root.pubkeys && chmod 700 /root/.ssh && chmod 600 /root/.ssh/authorized_keys /boot/config/ssh/root.pubkeys && echo INSTALADA
```

Comprueba también que en *Settings → Management Access* el acceso SSH de root
está permitido (la opción suele ser **"Allow root SSH login"**; vale tanto *Yes*
como *Only allow keys*, que es más restrictiva y suficiente para esto).

> Otra via, si prefieres no tocar la terminal: el USB de Unraid se comparte en
> red como `flash`, así que puedes abrir `\TU_SERVIDORlash\config\ssh\`
> desde el Explorador de Windows y crear ahí un fichero `root.pubkeys` con tu
> clave pública dentro. Sólo que eso **no surte efecto hasta el siguiente
> reinicio**, porque Unraid instala ese fichero al arrancar.

> **El detalle que hace que esto parezca más difícil de lo que es:** en Unraid
> el sistema de ficheros raíz vive en RAM y se reconstruye desde el USB en cada
> arranque. Una clave puesta sólo en `/root/.ssh/authorized_keys` funciona…
> hasta el primer reinicio, y entonces deja de funcionar sin más explicación.
> Por eso el comando de arriba la copia también a
> `/boot/config/ssh/root.pubkeys`, que sí está en el USB y Unraid reinstala al
> arrancar.

**3. Comprueba que entra sin contraseña:**

```bash
ssh -o BatchMode=yes root@192.168.1.10 "echo conexion OK"
```

Si eso imprime `conexion OK`, ya está.

### Antes del primer despliegue

**Comprueba de quién es el volumen** y ajusta `PUID`/`PGID` en
`deploy/docker-compose.unraid.yml` si no es 99:100:

```bash
ssh root@192.168.1.10 "mkdir -p /mnt/user/appdata/deliver-tracker && ls -ldn /mnt/user/appdata/deliver-tracker"
```

> Esto importa más de lo que parece. La imagen crea su usuario con uid 1000,
> pero en Unraid appdata suele ser de `nobody:users` (99:100). Con el uid
> equivocado el container no puede escribir la base **ni reescribir `token.json`
> al refrescar el token de Gmail** — y eso no falla al arrancar, falla una hora
> después, cuando caduca el access token, dejando la sincronización muerta sin
> que nada lo cante. El entrypoint se adapta al uid que le digas;
> `chown -R 1000:1000` no vale como alternativa, porque la herramienta *Docker
> Safe New Permissions* de Unraid lo revertiría.

**Copia las credenciales de Gmail** al volumen (no van dentro de la imagen):

```bash
scp data/credentials.json data/token.json root@192.168.1.10:/mnt/user/appdata/deliver-tracker/
```

**Despliega:**

```powershell
.\deploy\deploy-unraid.ps1 -UnraidHost 192.168.1.10
```

La primera vez copia también el compose. **Edita entonces `FLASK_SECRET_KEY`**
en Unraid (el script no vuelve a sobrescribir ese fichero, justo para no pisar
lo que edites allí):

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

El panel queda en `http://192.168.1.10:5000`.

### Actualizar

La misma orden. Sólo se reconstruye lo que haya cambiado:

```powershell
.\deploy\deploy-unraid.ps1 -UnraidHost 192.168.1.10
```

Opciones: `-User`, `-RemoteDir`, `-Tag`, `-SkipCompose` (sólo carga la imagen).
`Get-Help .\deploy\deploy-unraid.ps1 -Detailed` para el resto.

### Qué queda en Unraid

| Ruta | Contenido |
|------|-----------|
| `/boot/config/plugins/compose.manager/projects/deliver-tracker/docker-compose.yml` | El compose (copia de `deploy/docker-compose.unraid.yml`) |
| `/mnt/user/appdata/deliver-tracker/` | `packages.db`, `backups/`, `credentials.json`, `token.json` — **lo único que hay que respaldar** |

### Tests en GitHub

`.github/workflows/publicar-imagen.yml` corre la suite en cada push, que es la
red de seguridad que interesa aunque el despliegue se haga por SSH.

Ese mismo workflow puede publicar la imagen en ghcr.io, pero sólo a demanda
(botón *Run workflow*, o una etiqueta `vX.Y.Z`). Si algún día prefieres
desplegar con `docker compose pull` en vez de con el script, ya está todo
hecho: cambia el `image:` del compose de Unraid a
`ghcr.io/victorpiella/deliver-tracker:latest`.

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

Cada fila lleva:

- **Desplegable de estado** — marca un paquete a mano (p.ej. como *Entregado* porque ya lo tienes). Un estado manual **manda sobre los emails**: los que lleguen después se siguen guardando en el histórico, pero no cambian el estado. Se marca con la etiqueta `manual`.
- **Volver a automático** (`↺`, sólo si el estado es manual) — suelta el control y recalcula el estado desde los eventos recibidos.
- **Botón de papelera** — lo aparta del panel. **No lo borra**: ver abajo.
- **Fecha estimada de entrega**, cuando el email la trae (Amazon la escribe como *"Llegada entre el 6 y el 7 de julio"*).

En la página de detalle hay además un **Fusionar**, para unir dos entradas que
son el mismo envío. Sirve sobre todo para Correos: sus emails no traen ningún ID
de Amazon/AliExpress, así que aparecen como entrada aparte y no se pueden
enlazar de forma automática sin arriesgarse a mezclar paquetes.

### Papelera: borrar es reversible

Borrar manda el paquete a la **papelera**, de donde se puede restaurar tal cual
estaba, con su histórico y su estado manual. Desde ahí se puede borrar de forma
definitiva, pero eso ya no tiene vuelta atrás.

> **Por qué no se borra de verdad.** Borrar la fila arrastra en cascada sus
> `package_events`, y esos eventos guardan el `gmail_message_id` — justo lo que
> impide reprocesar un email ya visto. El resultado era que **el paquete
> reaparecía en el siguiente escaneo**, con el estado manual perdido por el
> camino. Marcándolo como borrado, los eventos se conservan, el email no se
> reingiere, y encima se puede deshacer.

Los paquetes **entregados** se van solos a la papelera pasados
`PURGE_DELIVERED_AFTER_DAYS` días (30 por defecto, `0` lo desactiva). Antes esto
los borraba definitivamente a los 15 días: con la base como única copia
duradera, eso destruía el histórico de compras sin avisar.

### Emails sin reconocer

Si llega un email de un remitente que seguimos pero ningún parser sabe leerlo,
aparece un aviso en la cabecera y se lista en `/sin-reconocer` con su asunto.

Es la señal de que Amazon o AliExpress han cambiado una plantilla. Los parsers
son expresiones regulares contra el HTML de sus emails: cuando cambian, `parse()`
devuelve `None` y dejarías de ver paquetes sin enterarte de nada. Con esto la
deriva se ve, en vez de esconderse en un contador de "saltados".

### Copias de seguridad

Cada día, tras el escaneo, se guarda una copia de la base en
`<volumen>/backups/packages-AAAAMMDD.db` (se conservan `BACKUP_KEEP`, 7 por
defecto). Se usa `VACUUM INTO`, que hace la copia de forma transaccional: copiar
el `.db` a pelo mientras el worker escribe produce un fichero roto que parece
bueno.

Como viven dentro del volumen que ya montas, entran solas en el backup de
appdata de Unraid.

El escaneo corre **en segundo plano**: el botón contesta al instante, gira
mientras dura (también si lo ha lanzado el worker horario, no sólo tú) y el panel
se recarga solo al terminar. Antes corría dentro de la propia petición, y tras un
parón largo la ventana adaptativa puede pedir hasta un año de correo — más que
el timeout de gunicorn.

Arriba, las tres tarjetas de recuento (*Total / En tránsito / Entregados*) hacen de filtro, y el buscador filtra por nombre de producto, ID de paquete y número de seguimiento. Ambos funcionan en el cliente: la lista de un panel doméstico cabe entera en la página.

El panel sigue el **tema del sistema** (claro/oscuro) y el botón de la cabecera permite forzar uno; la elección se guarda en `localStorage`. El diseño es responsive: por debajo de 760px cada paquete pasa a ser una ficha apilada con controles a tamaño de dedo.

## Qué escanea, y qué pasa si borras los emails

El escaneo pide a Gmail una única query **filtrada sólo por remitente** — nunca por asunto ni por palabras clave:

```
(from:auto-confirm@amazon.es OR from:confirmar-envio@amazon.es OR
 from:shipment-tracking@amazon.es OR from:order-update@amazon.es)
OR from:transaction@notice.aliexpress.com
OR from:gls-spain.com
OR from:correos.com
newer_than:<N>d
```

En Amazon, **el remitente ES el estado** (una dirección distinta por tipo de evento).
En AliExpress hay un solo remitente y el estado sale del asunto. GLS y Correos se
filtran por dominio y su estado sale de frases del cuerpo.

### Borrar emails

**La base de datos es la fuente de verdad, no Gmail.** Un evento ya escaneado es una
fila permanente en `package_events`; el sync **sólo añade**, nunca reconcilia ni borra.

| Acción sobre el correo | Efecto en el panel |
|---|---|
| Borras emails **ya escaneados** | Ninguno. Los paquetes, estados e histórico siguen ahí |
| **Archivas** emails (los sacas de la bandeja) | Ninguno. La búsqueda de Gmail los sigue encontrando |
| Borras un email **antes** de que se escanee | Ese evento se pierde para siempre |
| Mueves a la **papelera** | Equivale a borrarlo: la API no busca en papelera ni en spam |

Lo único irrecuperable es lo que se borra antes de escanearse. Como el worker corre
cada hora, esa ventana de riesgo es de una hora.

> Lo que sí conviene respaldar es `packages.db`. Si lo pierdes y ya has borrado los
> emails, el histórico no se puede reconstruir.

### La ventana de búsqueda

`newer_than:<N>d` **no limita cuánto tiempo puede durar un envío**: un paquete de
AliExpress de 6 semanas acumula sus eventos según van llegando, y cada uno se escanea
como mucho una hora después de llegar. La ventana sólo decide cuánto correo *antiguo*
se mira en cada pasada.

`N` es adaptativo:

| Situación | Ventana |
|---|---|
| Primer escaneo (nunca sincronizado) | `GMAIL_FIRST_SCAN_DAYS`, por defecto **30 días** |
| Escaneo normal (última sync reciente) | `GMAIL_MIN_LOOKBACK_DAYS`, por defecto **14 días** |
| Tras un parón | **todo el hueco** desde la última sync + 2 días de margen |
| Parón enorme | Tope de `GMAIL_MAX_LOOKBACK_DAYS`, por defecto **365 días** |

Es decir: si el container está apagado tres meses, al arrancar pide 92 días y recupera
lo que se perdió. Reprocesar de más no cuesta nada porque la deduplicación por
`gmail_message_id` descarta lo ya visto.

La fecha del último escaneo con éxito se guarda en la tabla `app_settings` y se muestra
en el panel (*"Último escaneo: hace 12 min"*). **Sólo avanza si el escaneo terminó sin
error**: si Gmail falla a mitad, el marcador se queda donde estaba y el siguiente intento
vuelve a cubrir la misma ventana.

Para una importación inicial más profunda, antes del primer escaneo:

```bash
GMAIL_FIRST_SCAN_DAYS=90 docker compose up -d --build
```

## Estados normalizados

Los estados de los emails se normalizan a un conjunto común, en orden de progreso:

`ordered` → `shipped` → `customs` → `customs_cleared` → `left_origin` → `local_carrier` → `in_country` → `at_distribution` → `out_for_delivery` → `delivery_attempted` → `delivered`

Fuera de esa progresión hay un estado más, **`cancelled`**, que no es un punto más
avanzado del recorrido sino otro final. Manda sobre cualquier otro (si el pedido se
canceló, da igual que antes figurara como enviado) y nada lo reanima después.

> **Ojo con `order-update@amazon.es`.** Ese remitente es un cajón de sastre: manda
> entregas, cancelaciones, intentos de entrega fallidos y cambios de fecha. Darlo por
> "entregado" sin mirar el asunto marcaba como entregados pedidos cancelados y entregas
> que habían fallado — y como `delivered` es terminal, se quedaban así para siempre. El
> estado sale del asunto; ver `ORDER_UPDATE_PATTERNS` en `app/parsers/amazon.py`.

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
| `GMAIL_FIRST_SCAN_DAYS` | `30` | Días hacia atrás en el primer escaneo |
| `GMAIL_MIN_LOOKBACK_DAYS` | `14` | Suelo de la ventana en escaneos normales |
| `GMAIL_MAX_LOOKBACK_DAYS` | `365` | Techo de la ventana tras un parón largo |
| `SYNC_INTERVAL_MINUTES` | `60` | Cada cuánto sincroniza el worker |
| `DISPLAY_TZ` | el de `TZ` | Zona en la que se **muestran** las fechas (se guardan en UTC) |
| `PURGE_DELIVERED_AFTER_DAYS` | `30` | Días hasta que un entregado se archiva en la papelera (`0` desactiva) |
| `BACKUP_ENABLED` | `true` | Copia diaria de la base en `<volumen>/backups` |
| `BACKUP_KEEP` | `7` | Cuántas copias se conservan |
| `PANEL_USER` / `PANEL_PASSWORD` | `admin` / _(vacío)_ | Con contraseña puesta, el panel pide autenticación básica |
| `PUID` / `PGID` | `1000` / `1000` | Usuario con el que corre la app. En Unraid, **99/100** |
| `MQTT_ENABLED` | `false` | `true` para publicar a Home Assistant |
| `MQTT_HOST` | `localhost` | Host del broker MQTT |
| `MQTT_PORT` | `1883` | Puerto del broker MQTT |
| `MQTT_USERNAME` / `MQTT_PASSWORD` | _(vacío)_ | Credenciales del broker, si las requiere |
| `MQTT_DISCOVERY_PREFIX` | `homeassistant` | Prefijo de discovery que espera HA |
| `DEBUGPY` | `0` | `1` abre el puerto 5678 para VS Code |
| `DEBUGPY_WAIT` | `0` | `1` congela el arranque hasta que te enganchas |

> Las fechas se guardan siempre en **UTC sin tzinfo**. SQLite descarta el offset al guardar un datetime con `tzinfo`, así que los emails (que llegan con offset local) se convierten antes de persistirse — ver `app/timeutils.py`. Para **mostrarlas** se reconvierten a `DISPLAY_TZ`; sin eso el panel enseñaba las horas con 1-2h de desfase.

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
- [x] Papelera: borrar es reversible, y ya no resucita en el siguiente escaneo
- [x] Fecha estimada de entrega
- [x] Aviso de emails sin reconocer (deriva de plantillas)
- [x] Copias de seguridad diarias de la base
- [x] Escaneo en segundo plano, sin timeouts
- [x] Fusionar entradas duplicadas (Correos)
- [x] Contraseña opcional del panel
- [x] SQLite en modo WAL: el panel responde mientras el escaneo escribe

## Próximos pasos

1. **Alertas push** — notificación en el móvil cuando un paquete pasa a "En reparto" o "Entregado".
2. **Soporte multi-cuenta Gmail** — varias cuentas (personal + trabajo) desde la UI.
3. **Más tiendas** — Temu, Shein, PCComponentes u otros con email de seguimiento estándar.
4. **Widget Home Assistant** — tarjeta Lovelace con miniatura del producto y barra de progreso.
