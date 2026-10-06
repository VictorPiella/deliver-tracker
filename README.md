# Delivery Tracker

A self-hosted parcel dashboard that reads your **order and shipping emails** and turns
them into one list of where your stuff actually is.

No tracking-aggregator subscription, no browser extension, no pasting tracking
numbers into five different carrier websites. If the shop emailed you about it,
it shows up here.

```
Gmail  ──▶  parsers  ──▶  SQLite  ──▶  web panel
                            ▲
              Correos API ──┘   (carriers move daily; shops only email at milestones)
```

Runs as **one container**. Web server, scheduler and database all inside it.

> **Scope, honestly:** this is built for parcels arriving in **Spain**. It reads
> Amazon.es, AliExpress, GLS Spain, Correos and Shopify emails, in Spanish and
> English, and the interface is in Spanish. The architecture is generic — parsers
> are small, independent modules — but out of the box it will not do much for a
> mailbox full of USPS and DHL. See [Adding a source](#adding-a-source).

---

## What it does

**Reads the emails you already get.** Order confirmations, shipping notices,
customs updates, delivery attempts. Each one becomes an event on a timeline.

**Figures out that they are the same parcel.** An AliExpress order handed to
Correos for the last mile generates two separate email conversations that never
mention each other. The only thing they share is a tracking number — so that is
what gets matched on. Two rows become one, with the full story:

```
29/09 12:18  AliExpress   Package PHBW6T98…J has an update
29/09 14:19  Correos      Prerregistrado
01/10 14:31  AliExpress   in your country/region
02/10 05:53  AliExpress   has cleared customs
03/10 18:01  AliExpress   with local carrier     ← the handover
03/10 18:40  Correos      Clasificado
05/10 12:53  Correos      Alta en la unidad de reparto
```

**Asks the carrier when email is not enough.** A shop emails you three times:
confirmed, shipped, delivered. The carrier moves the parcel every day. For
Correos shipments the panel queries their public API directly, so the status is
current rather than four days stale.

**Stays usable when it does not know something.** Some emails never name the
product. Rather than showing a blank row or a routing code, it falls back to the
tracking number — and there is a pencil next to every title so you can call it
whatever you want.

**Never loses a parcel to a template change.** Emails from a known sender that
the parsers cannot read land on a *Sin reconocer* page instead of being silently
dropped. That page is how a broken parser gets noticed. It has earned its keep
more than once.

### In the panel

- Status pipeline per parcel, with a progress bar and full event history
- Per-shipment titles — a two-item order shipped in two boxes shows two names
- Item breakdown with variants, quantities and images, where the email provides them
- Manual status override that wins over anything the emails or carriers say
- Rename anything; search across titles, tracking numbers and sources
- Drag to select, `Supr` to send to the bin, `Ctrl+A`, `Esc`
- Soft delete — everything is recoverable from the bin
- Optional MQTT publishing for Home Assistant (off by default)

---

## Supported sources

| Source | How it is recognised | Gives |
|---|---|---|
| **Amazon.es** | sender | status, product, image, ETA, multi-order emails |
| **AliExpress** | sender | status, product, image, grouped "N packages" emails |
| **Correos** | sender + **public API** | live carrier events |
| **GLS Spain** | sender | status, links to existing parcels |
| **Shopify** | **email body** | order, items, variants, carrier, order-status link |

Shopify is the odd one. Shopify does not send those emails — every shop sends
them from its own domain, so there is no sender to match. It is identified by the
structure of Shopify's stock notification template, with a cheap subject filter
first to decide whether the body is even worth fetching.

That filter is deliberately loose, so plenty of non-Shopify mail reaches the
parser and gets rejected by the body check. Those count as *irrelevant*, not
*unparsed* — otherwise the "sin reconocer" page fills with noise and stops being
useful.

---

## Quick start

You need Docker, a Google account, and about ten minutes.

### 1. Gmail API access

1. In [Google Cloud Console](https://console.cloud.google.com/), create a project
2. Enable the **Gmail API**
3. Create an **OAuth client ID** of type *Desktop app*
4. Download the JSON as `data/credentials.json`
5. **Publish the app** (OAuth consent screen → *Publish app*)

> Step 5 is not optional in practice. While the app is in *Testing*, Google
> expires the refresh token after **7 days** and syncing stops dead. The panel
> will tell you when that happens — there is a banner for exactly this — but you
> will be re-authorising every week until you publish.

The app only ever requests `gmail.readonly`.

### 2. Configure

```bash
cp .env.example .env
```

Then generate a secret and set a password:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### 3. Authorise and run

```bash
docker compose run --rm -p 8080:8080 deliver-tracker python -m scripts.gmail_auth
docker compose up -d --build
```

The panel is at **http://localhost:5000**. The first scan looks back 30 days.

Development mode is the default via `docker-compose.override.yml`: Flask with
auto-reload, code mounted from the host, background worker off, and
`USE_MOCK_GMAIL=true` so you can work against sample emails without touching a
real mailbox.

---

## Deploying to a server

There is a PowerShell script for Unraid that avoids needing a registry:

```powershell
.\deploy\deploy-unraid.ps1 -UnraidHost 192.168.1.10
```

It builds for `linux/amd64`, `docker save`s the image, copies it over SSH,
loads it, and brings the stack up. It **never overwrites a remote
`docker-compose.yml`** — that is where your secrets live.

Copy `data/credentials.json` and `data/token.json` to the appdata directory and
`chmod 600` them. The container drops privileges to `PUID`/`PGID` via `setpriv`.

For anything else, `deploy/docker-compose.unraid.yml` is a normal compose file.

---

## Configuration

Everything is environment variables; see `.env.example` for the full list.

| Variable | Default | What it does |
|---|---|---|
| `PANEL_USER` / `PANEL_PASSWORD` | `admin` / *empty* | HTTP Basic auth. **Empty means no auth at all.** |
| `FLASK_SECRET_KEY` | — | Signs the session cookie. Generate your own. |
| `DB_PATH` | `/data/packages.db` | SQLite file |
| `SYNC_INTERVAL_MINUTES` | `60` | How often to scan |
| `GMAIL_FIRST_SCAN_DAYS` | `30` | Lookback on a fresh database |
| `GMAIL_MIN_LOOKBACK_DAYS` | `14` | Floor for later scans |
| `CORREOS_API_ENABLED` | `true` | The only outbound network call. Turn it off and nothing else changes. |
| `CORREOS_API_MAX_POR_ESCANEO` | `25` | Cap per scan — it is not our API |
| `PURGE_DELIVERED_AFTER_DAYS` | `30` | Archive delivered parcels to the bin (`0` disables) |
| `BACKUP_ENABLED` / `BACKUP_KEEP` | `true` / `7` | Daily `VACUUM INTO` snapshots |
| `POSTAL_CODE` | — | GLS asks for it by hand; the panel reminds you |
| `MQTT_ENABLED` | `false` | Home Assistant discovery |
| `DISPLAY_TZ` | `Europe/Madrid` | Panel timezone |

---

## Security

Read this before putting it anywhere other than your own LAN.

**It ships with no authentication.** `PANEL_PASSWORD` is empty by default, which
means anyone who can reach the port can delete your parcels. Set it.

**Do not expose it to the internet.** HTTP Basic over plain HTTP sends the
password in the clear on every request. Behind a reverse proxy with TLS it is
defensible; directly on a public IP it is not.

**The panel can display bearer credentials.** Shopify's order-status link
contains tokens that grant access to the order without logging in. It is stored
so the tracking button works, kept out of logs and out of the JSON API — but it
is rendered as a link on the page. Treat the panel as sensitive.

**Your database is your purchase history.** Backups are plain SQLite files.
Permissions are yours to set.

What is already handled: CSRF on every state-changing request, constant-time
password comparison, Jinja autoescaping with no `|safe` anywhere, parameterised
SQL only, no user input in redirects, read-only Gmail scope, non-root container,
and secrets excluded from the image via `.dockerignore`.

Dependencies are pinned and checked with `pip-audit`.

---

## Development

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt
python -m pytest
```

**476 tests**, more test code than application code. That ratio is deliberate:
almost every test in here exists because something broke in a real mailbox.
They are named after the failure, in prose, so a future reader knows what the
test is defending against and not merely what it asserts.

```
app/
  parsers/        one module per source, independent and small
  gmail_sync.py   orchestration: query, dispatch, ingest
  sync.py         persistence, status rules, merging
  correos_api.py  the only outbound network call
  models.py       SQLAlchemy models + additive migrations
  web.py          Flask app, 17 routes
```

Two rules the whole thing leans on:

**Status only moves forward.** A late or duplicate email cannot rewind a
delivered parcel. Cancellation is the exception — it is a different ending, not
a step backwards — and a manual override beats everything.

**Dedupe by message id, and re-enrich on rescan.** Re-reading an email never
creates a duplicate event, but it *does* fill in fields the parser has since
learned to extract. Without that, parcels stored before a parser improvement
would stay incomplete forever, because the raw email body is not kept.

### Adding a source

Write a module in `app/parsers/` exposing `matches(sender)` and `parse(...)`
returning a normalised dict, then register it in `gmail_sync.py`. Look at
`gls.py` for the simple case and `shopify.py` for one that has to recognise
itself from the body.

Write the tests against a real email. Every parser in here got something wrong
the first time precisely because it was written against an idealised template.

---

## Limitations

- **Spain-centric.** See the note at the top.
- **Only Correos has live tracking.** Other carriers either lack a usable public
  API or do not resolve these numbers. GLS has no deep link at all — the panel
  copies the number to your clipboard and reminds you of your postcode, because
  their tracker is a SPA that demands both by hand.
- **An email-based tracker cannot know more than the emails.** Where a carrier
  API fills the gap, it is used. Where there is none, the panel moves at the
  speed the shop writes to you.
- **Single user, single mailbox.** SQLite, one gunicorn worker, no accounts.

## License

MIT — see `LICENSE`.
