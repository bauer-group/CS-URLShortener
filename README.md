# URL Shortener

Produktionsbereiter [Shlink](https://shlink.io/) URL-Shortener für die BAUER GROUP mit PostgreSQL-Datenbank, Admin-Weboberfläche und QR-Code-Generierung.

| Endpunkt   | URL                                          |
|------------|----------------------------------------------|
| Short URLs | `https://go.bauer-group.com/{shortcode}`     |
| REST API   | `https://go.bauer-group.com/rest/v3/...`     |
| Admin UI   | `https://go.bauer-group.com/.ui`             |

## Funktionen

- **URL-Verkürzung** — Kurze Links mit konfigurierbarer Codelänge und QR-Codes
- **REST API** — Vollständige API unter `/rest/v3/...` zur programmatischen Verwaltung
- **Admin-Weboberfläche** — Verwaltung über `/.ui` (Shlink Web Client)
- **Besucherstatistiken** — Klickzählung, Referrer, Geolokalisierung (optional via GeoLite2)
- **Weiterleitungen** — Konfigurierbare Fallback-Weiterleitungen für unbekannte Pfade
- **DSGVO-konform** — IP-Anonymisierung standardmäßig aktiviert
- **Security Headers** — HSTS, X-Frame-Options, Content-Type-Nosniff, Referrer-Policy

## Architektur

```text
                    ┌──────────────────────────────────────────┐
                    │              Traefik / Coolify            │
                    └────┬──────────────────────────┬──────────┘
                         │                          │
              go.bauer-group.com          go.bauer-group.com/.ui
              (short URLs + API)          (Admin UI + StripPrefix)
              Priority 100                Priority 200
                         │                          │
                  ┌──────┴──────┐            ┌──────┴──────┐
                  │shlink-server│            │ shlink-web  │
                  │   :8080     │            │   :8080     │
                  └──────┬──────┘            └─────────────┘
                         │                   (nur proxy-Network,
                  ┌──────┴──────┐             kein DB-Zugriff)
                  │  shlink-db  │
                  │   :5432     │  PostgreSQL 18 (lean)
                  └─────────────┘
```

**Netzwerk-Isolation:**

- `shlink-server` — `local` + `proxy` (braucht DB-Zugriff und Traefik-Erreichbarkeit)
- `shlink-web` — nur `proxy` (reine SPA, keine Server-Kommunikation nötig)
- `shlink-db` — nur `local` (nicht von außen erreichbar)

## Schnellstart

```bash
# 1. .env generieren (Passwörter + API-Key werden automatisch erstellt)
python scripts/generate-env.py

# 2. Lokal starten
docker compose -f docker-compose.development.yml up -d

# 3. Zugriff
#    Shlink API: http://localhost:8080
#    Admin UI:   http://localhost:8081
```

## Deployment

| Variante        | Compose-Datei                      | Anwendungsfall                          |
|-----------------|------------------------------------|-----------------------------------------|
| **Traefik**     | `docker-compose.traefik.yml`       | Produktion mit HTTPS, Let's Encrypt     |
| **Coolify**     | `docker-compose.coolify.yml`       | Produktion via Coolify PaaS             |
| **Development** | `docker-compose.development.yml`   | Lokale Entwicklung ohne Reverse Proxy   |

```bash
# Traefik (Voraussetzung: Traefik läuft, DNS-Record gesetzt)
docker compose -f docker-compose.traefik.yml up -d

# Coolify: Deploy via Coolify UI, Env-Vars im Dashboard setzen
```

> **Development-Modus:** Kein HTTPS, keine Security Headers, keine Netzwerk-Isolation.
> Nicht für den Einsatz im Internet geeignet.

## Konfiguration

Alle Variablen sind in `.env.example` dokumentiert. Die wichtigsten:

| Variable             | Pflicht | Beschreibung                                    |
|----------------------|---------|-------------------------------------------------|
| `POSTGRES_PASSWORD`  | Ja      | Datenbankpasswort                               |
| `SHLINK_API_KEY`     | Ja      | API-Schlüssel für REST API                      |
| `SHLINK_DOMAIN`      | Nein    | Short URL Domain (Default: `go.bauer-group.com`)|
| `SHLINK_GEOLITE_KEY` | Nein    | GeoLite2 Lizenzschlüssel für IP-Geolokalisierung|

### Weiterleitungen

Unbekannte Pfade werden standardmäßig nach `https://bauer-group.com/` weitergeleitet:

| Variable                     | Funktion                                            |
|------------------------------|-----------------------------------------------------|
| `SHLINK_BASE_URL_REDIRECT`   | Besuch der Root-Domain (`go.bauer-group.com/`)      |
| `SHLINK_INVALID_URL_REDIRECT`| Ungültiger oder deaktivierter Shortcode             |
| `SHLINK_404_REDIRECT`        | Sonstige nicht gefundene Pfade                      |

Unterstützt `{DOMAIN}` und `{ORIGINAL_PATH}` Platzhalter (Shlink v2.9+).

### QR-Codes

Jeder Kurzlink hat automatisch einen QR-Code unter `https://go.bauer-group.com/{shortcode}/qr-code`.

Konfigurierbar via `SHLINK_QR_SIZE`, `SHLINK_QR_FORMAT`, `SHLINK_QR_MARGIN`, `SHLINK_QR_ERROR_CORRECTION`.

### API-Schlüssel

Der `SHLINK_API_KEY` (via `INITIAL_API_KEY`) wird nur beim **ersten Start** mit leerer Datenbank angelegt. Weitere API-Keys erstellen:

```bash
docker exec -it url-shortener_SERVER shlink api-key:generate
```

## Admin UI unter /.ui

Die Shlink Web-Oberfläche ist unter `/.ui` erreichbar. Traefik leitet Anfragen mit `PathPrefix(/.ui)` an den Web-Client weiter und entfernt den Prefix via `StripPrefix`-Middleware.

Die SPA enthält **keine Zugangsdaten** — der API-Key wird vom Benutzer manuell im Browser eingegeben und im `localStorage` gespeichert. Dadurch ist kein zusätzlicher Schutz (BasicAuth o.ä.) auf dem Web-Client nötig.

> **Alternativ** kann der kostenlose gehostete Client unter [app.shlink.io](https://app.shlink.io) verwendet werden — einfach Server-URL und API-Key eingeben.

## Scripts

Alle Scripts sind Python 3.6+ und haben **keine externen Abhängigkeiten** (stdlib only).
Shlink-Verbindungsdaten werden automatisch aus `.env` gelesen.

### .env generieren

```bash
python scripts/generate-env.py
```

Generiert automatisch sichere Werte für alle Pflicht-Variablen (`POSTGRES_PASSWORD`, `SHLINK_API_KEY`).
Die erzeugte `.env` wird mit Dateiberechtigungen `600` (nur Owner lesbar) geschrieben.

### CLI — Short URLs verwalten

```bash
python scripts/shlink-cli.py list                          # Alle URLs auflisten
python scripts/shlink-cli.py list --tag yourls              # Nach Tag filtern
python scripts/shlink-cli.py create https://example.com --slug test --tag marketing
python scripts/shlink-cli.py info test                      # Details anzeigen
python scripts/shlink-cli.py update test --url https://new.example.com --title "Neuer Titel"
python scripts/shlink-cli.py delete test                    # Mit Bestätigung
python scripts/shlink-cli.py delete test --yes              # Ohne Rückfrage
```

### Backup & Restore

```bash
python scripts/shlink-backup.py backup                     # URL-Definitionen sichern
python scripts/shlink-backup.py backup --compress           # Mit gzip-Komprimierung
python scripts/shlink-backup.py backup --include-visits     # Inkl. Visit-Daten (Archiv)
python scripts/shlink-backup.py restore --input backup.json --dry-run   # Vorschau
python scripts/shlink-backup.py restore --input backup.json --skip-existing
```

Visit-Daten werden mit `--include-visits` archiviert, können aber nicht wiederhergestellt werden (Shlink API bietet keinen Visit-Import).

### YOURLS-Migration

Migriert alle Short-URLs von einer laufenden YOURLS-Instanz nach Shlink.
Custom-Slugs und Titel werden 1:1 übernommen. Existierende Slugs werden gelöscht und neu angelegt.

```bash
# Dry-Run (Vorschau ohne Änderungen)
python scripts/import-yourls.py \
  --yourls-url https://old.example.com/yourls-api.php \
  --yourls-signature YOUR_TOKEN \
  --dry-run

# Import mit Default-Tag "yourls"
python scripts/import-yourls.py \
  --yourls-url https://old.example.com/yourls-api.php \
  --yourls-signature YOUR_TOKEN

# Mit JSON-Backup der YOURLS-Daten und Custom-Tags
python scripts/import-yourls.py \
  --yourls-url https://old.example.com/yourls-api.php \
  --yourls-username admin --yourls-password SECRET \
  --tag yourls --tag legacy \
  --export yourls-backup.json
```

## Sicherheit

| Maßnahme                        | Traefik/Coolify | Development |
|---------------------------------|:---------------:|:-----------:|
| HTTPS erzwungen                 | ✓               | ✗           |
| Security Headers (HSTS etc.)    | ✓               | ✗           |
| Datenbank nur intern erreichbar | ✓               | ✓           |
| Web-Client ohne Credentials     | ✓               | ✓           |
| API-Key nicht im Quellcode      | ✓               | ✓           |
| Netzwerk-Isolation              | ✓               | ✗           |

Die REST API (`/rest/v3/...`) ist durch den `X-Api-Key` Header geschützt. Für zusätzliche Absicherung kann eine Traefik IP-Allowlist Middleware auf den Server-Router gelegt werden.

## Projektstruktur

```text
URLShortener/
├── scripts/
│   ├── generate-env.py                 # .env Generator (auto-generierte Secrets)
│   ├── shlink-cli.py                   # CLI: create, list, update, delete
│   ├── shlink-backup.py                # Backup & Restore (JSON/gzip)
│   └── import-yourls.py                # YOURLS → Shlink Migration
├── docker-compose.traefik.yml          # Produktion: Traefik + HTTPS
├── docker-compose.coolify.yml          # Produktion: Coolify PaaS
├── docker-compose.development.yml      # Development: Direkte Ports
├── .env.example                        # Dokumentierte Env-Vorlage
└── LICENSE
```

## Weiterführende Dokumentation

- [Shlink Dokumentation](https://shlink.io/documentation/)
- [Shlink Umgebungsvariablen](https://shlink.io/documentation/environment-variables/)
- [Shlink Special Redirects](https://shlink.io/documentation/advanced/special-redirects/)
- [Shlink REST API](https://shlink.io/documentation/api-docs/)

## Lizenz

MIT — BAUER GROUP
