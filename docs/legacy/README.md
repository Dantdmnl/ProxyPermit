> Legacy-documentatie: beschrijft de oorspronkelijke twee-node-inrichting. Zie de [actuele README](../../README.md).

# ProxyPermit

ProxyPermit is een webgebaseerde policy- en whitelistmanager voor Tinyproxy,
ontworpen voor een veilige twee-node opstelling met synchronisatie, failover en
operationele logging.

---

## Backend Architectuur (Technisch)

De backend bestaat uit een Python Flask-applicatie (`app/main.py`) en een Whitelist Manager module (`app/whitelist_manager.py`).

- **main.py**: Bevat de Flask webserver, API-routes, authenticatie, CORS, en service management.
- **whitelist_manager.py**: Regelt veilig whitelistbeheer (CRUD, backup, rollback, validatie, logging).
- **validators.py**: Valideert en normaliseert whitelist entries (domein → regex).

Belangrijkste eigenschappen:

- Atomisch schrijven van whitelist-bestanden met automatische backup en rollback
- Session- en HMAC-authenticatie (voor web en sync)
- Rate limiting, nonce-replay bescherming en IP-whitelisting voor sync
- Logging van wijzigingen en technische fouten (AVG-proof)

Zie inline docstrings in de code voor details en uitleg per functie/klasse.

---

Deze documentatie beschrijft de volledige proxy server oplossing voor centraal beheer, whitelisting en high availability.

**Achtergrond & keuze:**
Voorheen werd webverkeer gefilterd via een FortiGate firewall met geïntegreerde proxy. In recente FortiOS-versies is deze proxyfunctionaliteit echter verwijderd. Daarom is gekozen voor een open source alternatief dat voldoet aan de volgende eisen:

- Eenvoudig beheer (liefst via webinterface)
- Draait stabiel op Ubuntu 24.04
- Niet end-of-life, actief onderhouden
- Ondersteunt High Availability (HA/failover)
- Whitelisting van domeinen/URL's (geen deep packet inspection)

**Let op:**
Tinyproxy wordt hier uitsluitend gebruikt als forward proxy met domein-whitelisting. Er is geen caching, geen deep packet inspection, en geen MITM van HTTPS. Dit sluit aan bij de oorspronkelijke FortiGate use-case: alleen verkeer naar goedgekeurde domeinen toestaan, met centraal beheer en logging.

---

Platform: Ubuntu Server 24.04 LTS

Tinyproxy: 1.11.1

---

## Quick start

```bash
# 1. Plaats de release map
cd /opt
sudo mkdir -p /opt/proxypermit
sudo cp -R /path/to/proxypermit/* /opt/proxypermit/
cd /opt/proxypermit

# 2. Deploy (start wizard automatisch als .env ontbreekt)
chmod +x deploy.sh
sudo ./deploy.sh

# 3. Open browser
# https://`server-ip`:8080
```

Optioneel(pas na de installatie):

```bash
sudo ./pre-prod-check.sh
```

Wat `deploy.sh` automatisch doet:

- Installeert dependencies (Python, Tinyproxy, keepalived).
- Start de configuratie-wizard als `.env` ontbreekt.
- Zet veilige opslagmappen klaar.
- Ruimt legacy services op (indien aanwezig).
- Installeert en start de systemd service.
- Installeert en activeert de sync timer/service.
- Genereert `sync-secret.key` (kopieer naar de andere node).
- Genereert `flask-secret.key` voor sessies (kopieer naar de andere node).

---

## Lokale Demo op Windows (UI-only)

Voor demo-doeleinden kun je de webinterface lokaal op Windows starten zonder Tinyproxy, Keepalived en systemd.

Gebruik het script:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-demo-windows.ps1
```

Optioneel met eigen host/poort:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-demo-windows.ps1 -HostAddress 127.0.0.1 -Port 8080
```

Wat dit script doet:

- Zet demo-omgevingsvariabelen voor paden die op Linux normaal onder `/etc` en `/var/lib` staan.
- Maakt een lokale demo-whitelist in `.demo/windows/data/whitelist.txt`.
- Zet tijdelijke secrets/state-files lokaal in `.demo/windows/data/`.
- Schrijft entry-log lokaal naar `.demo/windows/data/whitelist_entry_log.json`.
- Zet `DEMO_MODE=true` zodat statusweergave niet onnodig "tinyproxy down" toont in demo-context.
- Start Flask direct via `wsgi:app`.

Let op:

- Dit is alleen voor UI/API demo en validatie van invoer/exports.
- Geen echte proxy-functionaliteit op Windows (geen systemd/tinyproxy servicebeheer).

---

## Features

- Web interface voor whitelist beheer met zoekfunctie.
- Entry descriptions (zoals firewall rules).
- Automatische domein → regex conversie.
- Real-time validatie met preview.
- Automatische backups met rollback.
- Drag & drop voor volgorde.
- Service restart na wijzigingen.
- Session login (standaard ingeschakeld).
- Gunicorn + Flask in Python venv.
- AVG/GDPR compliant (geen persoonlijke data logging).
- Structured audit/auth logging: de app schrijft gestructureerde JSON-lines voor audit events (auth + whitelist wijzigingen) naar een configureerbare audit logfile en maakt die beschikbaar via de `/api/logs` endpoint (events worden gemarkeerd met `service: audit` en UI-duplicaten worden gededupliceerd).
- Proxy traffic logs worden verrijkt met IP/host context zodat de UI per verbinding beter te filteren is.
- Optionele header logo-ondersteuning via `MANAGER_LOGO_URL`/`MANAGER_LOGO_ALT` met lokale fallback in `app/static/`.

---

## Architectuur

```text
              VIP (Keepalived / VRRP)
                           │
             ┌─────────────┴──────────────┐
             │                            │
    ┌────────▼────────┐          ┌────────▼────────┐
    │ Proxy Node 1    │◄───────► │ Proxy Node 2    │
    │ (MASTER)        │  VRRP    │ (BACKUP)        │
    │                 │ adverts  │                 │
    │                 │ + Sync   │                 │
    │                 │  v2 API  │                 │
    │ Tinyproxy :3128 │          │ Tinyproxy :3128 │
    │ Nginx :80       │          │ Nginx :80       │
    │ Nginx :8080 TLS │          │ Nginx :8080 TLS │
    │ Gunicorn (unix) │          │ Gunicorn (unix) │
    │ Flask Manager   │          │ Flask Manager   │
    └─────────────────┘          └─────────────────┘

Datapad management UI:
Browser -> Nginx :80 (301) -> Nginx :8080 (HTTPS) -> Gunicorn socket -> Flask

Datapad node-to-node sync:
sync_whitelist_v2.py -> peer Nginx :8080 -> Flask /api/sync/*

Datapad HA / failover:
Keepalived VRRP advertisements tussen beide nodes bepalen MASTER/BACKUP.
Bij MASTER-failure verhuist de VIP automatisch naar de BACKUP node.
```

HA componenten:

- Keepalived: VRRP voor VIP management en automatic failover.
- Tinyproxy: forward proxy met ERE whitelist filtering op poort `3128`.
- Nginx: TLS terminatie op manager-poort, HTTP->HTTPS redirect, security headers en endpoint filtering.
- Gunicorn: draait intern (bij voorkeur Unix socket) achter Nginx.
- Flask Manager API/UI: whitelistbeheer, validatie, service-acties en sync-endpoints.
- API Sync v2: bidirectionele whitelist sync via systemd timer + HMAC authenticatie.
- Keepalived state model: 1 actieve `MASTER` + 1 standby `BACKUP`; VIP volgt altijd de actuele `MASTER`.

Nginx endpoint model (belangrijk):

- UI endpoints (zoals `/api/sync/status`, `/api/sync/peer-status`, `/api/sync/run*`) gaan via normale app-auth.
- Peer-only endpoints (`/api/sync/download`, `/api/sync/upload`, `/api/sync/info`, `/api/sync/set-conflict`, `/api/sync/clear-conflict`) worden in Nginx extra IP-beperkt.

---

## Vereisten

### Hardware

- 2x Ubuntu Server 24.04 LTS
- 2GB RAM per node (minimum)
- 20GB disk space

### Software

- Python 3.13 + python3.13-venv (of python3 + python3-venv)
- python3-pip
- build-essential
- Tinyproxy 1.11.1 (apt package)
- Keepalived
- Sync v2 (systemd timer, meegeleverd)

---

## Deployment

Standaard (wizard + deploy):

```bash
cd /opt/proxypermit
sudo ./deploy.sh
```

De wizard vraagt o.a.:

- Admin credentials
- VIP/Node adressen voor de UI
- Keepalived instellingen
- Tinyproxy whitelist/ACL
- HTTPS/SSL settings (poort, cert, key)
- Sync API settings (http/https, poort, verify)

Belangrijk:

- `sync-secret.key` moet identiek zijn op beide nodes.
- `flask-secret.key` moet identiek zijn op beide nodes (seamless login).

---

## HTTPS & poort configuratie

De manager (webinterface) en sync-API ondersteunen zowel HTTP als HTTPS, en een configureerbare poort. Dit wordt geregeld via de `.env` file en de setup-wizard.

### Belangrijke .env variabelen

Volledige lijst met alle variabelen en defaults: [ENVIRONMENT.md](ENVIRONMENT.md).

- `ENABLE_AUTH` — `true`/`false` (standaard: `false`)
- `AUTH_USERNAME` — Admin gebruikersnaam
- `AUTH_PASSWORD_HASH` — One-way password hash (aanbevolen, Cisco-secret-achtig)
- `AUTH_PASSWORD` — Legacy plaintext fallback (alleen tijdelijk voor migratie)
- `AUTH_LOGIN_RATE_LIMIT_ENABLED` — `true`/`false` (standaard: `true`) om login lockout in/uit te schakelen
- `AUTH_LOGIN_MAX_ATTEMPTS` — Max mislukte loginpogingen binnen venster (standaard: `5`)
- `AUTH_LOGIN_WINDOW_SECONDS` — Tijdsvenster voor mislukte pogingen in seconden (standaard: `300`)
- `AUTH_LOGIN_LOCKOUT_SECONDS` — Basis lockout na te veel failures in seconden (standaard: `120`)
- `AUTH_LOGIN_DYNAMIC_LOCKOUT_ENABLED` — `true`/`false` (standaard: `false`), verdubbelt lockout bij herhaalde lockouts
- `AUTH_LOGIN_MAX_LOCKOUT_SECONDS` — Maximum lockout bij dynamische lockout (standaard: `900`)
- `LOG_API_MAX_PAGE_LIMIT` — Maximum aantal logregels per pagina via `/api/logs` (standaard: `5000`)
- `LOG_API_MAX_SCAN_LIMIT` — Maximum scan-window voor logopvraging (standaard: `120000`)
- `LOG_API_MAX_PER_SOURCE_LIMIT` — Maximum regels per logbron/service tijdens scanning (standaard: `50000`)
- `LOG_IP_MODE` — IP weergave in logs: `full`, `masked` of `hash` (standaard: `full`).
- `CLIENT_IP_MAP_PATH` — Pad naar JSON file met `{ "ip": "systeemnaam" }` mapping voor logverrijking.
- `CLIENT_IP_MAP_JSON` — JSON string met `{ "ip": "systeemnaam" }` mapping (override/aanvulling op file).
- `AUDIT_ENTRY_MODE` — Audit entry payload: `preview`, `full` of `hash` (standaard: `preview`).
- `AUDIT_ENTRY_MAX_LEN` — Max lengte van audit entry preview (standaard: `160`).
- **RADIUS integratie (optioneel, experimenteel/ongetest):**
  - `ENABLE_RADIUS` — `true`/`false` (standaard: `false`) — Enable RADIUS authenticatie voor web UI
  - `RADIUS_SERVER` — RADIUS server hostname/IP (bijv. `auth.example.com`)
  - `RADIUS_PORT` — RADIUS port (standaard: `1812`)
  - `RADIUS_SECRET` — Shared secret tussen RADIUS client/server
  - `RADIUS_TIMEOUT` — RADIUS timeout in seconden (standaard: `5`)
  - `RADIUS_ALLOW_LOCAL_FALLBACK` — `true`/`false` (standaard: `true`) — Fall back op local password als RADIUS onbereikbaar
- `MANAGER_SCHEME` — `http` of `https` (standaard: `http`)
- `MANAGER_PORT` — Poort voor de webinterface (standaard: `8080`)
- `MANAGER_BIND` — Bind-adres (standaard: `0.0.0.0`)
- `MANAGER_CERT` — Pad naar SSL-certificaat (alleen vereist bij `https`)
- `MANAGER_KEY` — Pad naar SSL-key (alleen vereist bij `https`)
- `PROXY_FIX_ENABLED` — `true`/`false` (standaard: `false`). Zet op `true` als de app achter een reverse proxy (bijv. Nginx) draait.
- `PROXY_FIX_X_FOR` — Aantal trusted reverse proxy hops voor client-IP (standaard: `1`)
- `PROXY_FIX_X_PROTO` — Aantal trusted reverse proxy hops voor protocol (standaard: `1`)
- `PROXY_FIX_X_HOST` — Aantal trusted reverse proxy hops voor host (standaard: `1`)
- `PROXY_FIX_X_PORT` — Aantal trusted reverse proxy hops voor poort (standaard: `1`)
- `GUNICORN_BIND` — Optionele override voor Gunicorn bind-adres (bijv. `unix:/run/proxypermit/gunicorn.sock`)
- `GUNICORN_USE_TLS` — `true`/`false` (optioneel). Overschrijft TLS-gedrag van Gunicorn.
- `ENABLE_HSTS` — `true`/`false` (standaard: `true`)
- `HSTS_MAX_AGE` — HSTS max-age in seconden (standaard: `31536000`)
- `HSTS_INCLUDE_SUBDOMAINS` — `true`/`false` (standaard: `true`)
- `HSTS_PRELOAD` — `true`/`false` (standaard: `false`)
- `SYNC_SCHEME` — `http` of `https` voor sync-API (standaard: volgt `MANAGER_SCHEME`)
- `SYNC_PORT` — Poort voor sync-API (standaard: volgt `MANAGER_PORT`)
- `SYNC_VERIFY_CERT` — `true`/`false` (standaard: `true`). Zet op `false` voor self-signed certs.
- `ALLOW_LOCALHOST_SYNC` — `true`/`false` (standaard: `false`). Handig als sync via reverse proxy met localhost-bron loopt.
- `SYNC_MASTER_HOST` — (optioneel) hostnaam of IP van de node die als "master" beschouwd moet worden bij
    near-simultaneous wijzigingen. Voorbeeld: `SYNC_MASTER_HOST=172.16.11.10`.
- `SYNC_MASTER_PREFERENCE` — `true`/`false` (standaard: `false`). Als `true` en `SYNC_MASTER_HOST` is ingesteld,
    zal de sync-job proberen de opgegeven master als bron-van-waarheid te gebruiken bij conflicten,
    in plaats van automatisch in conflict-mode te gaan.
- `SYNC_MAX_WHITELIST_SIZE` — Maximale grootte van het whitelist-bestand (in bytes) die de sync-job accepteert.
    Standaard: `1048576` (1 MiB). Gebruik deze variabele om de veiligheidscap te verhogen of te verlagen
    afhankelijk van verwachte whitelist-grootte. Als je een zeer grote whitelist hebt, verhoog dit voorzichtig
    (bijv. `5242880` voor 5 MiB), maar houd rekening met geheugen-/IO-limieten op je nodes.

- `AUDIT_LOG_PATH` — Pad naar de audit logfile waar gestructureerde JSON-lines worden weggeschreven (standaard: `audit.log`).
- `AUDIT_LOG_MAX_BYTES` — Max grootte (bytes) voor audit logfile rotatie (standaard: `2097152` / 2 MiB).
- `AUDIT_LOG_BACKUP_COUNT` — Aantal bewaarde backups voor audit logfile (standaard: `5`).
- `MANAGER_LOGO_URL` — Optionele URL of statisch pad naar het logo dat rechtsboven in de header wordt getoond (bijv. `/static/logo.png` of `https://cdn.example.com/logo.png`).
- `MANAGER_LOGO_ALT` — Alternatieve tekst voor het logo (accessibility / tooltip).

### Voorbeeld .env voor HTTPS

```env
MANAGER_SCHEME=https
MANAGER_PORT=8443
MANAGER_CERT=/etc/ssl/certs/proxypermit.crt
MANAGER_KEY=/etc/ssl/private/proxypermit.key
SYNC_SCHEME=https
SYNC_PORT=8443
SYNC_VERIFY_CERT=false
```

### Certificaat opties

#### 1. Self-signed (dev/test)

```bash
openssl req -x509 -newkey rsa:4096 -keyout /etc/ssl/private/proxypermit.key -out /etc/ssl/certs/proxypermit.crt -days 365 -nodes -subj "/CN=proxy.example.local"
```

Dan in `.env`:

```env
MANAGER_SCHEME=https
MANAGER_CERT=/etc/ssl/certs/proxypermit.crt
MANAGER_KEY=/etc/ssl/private/proxypermit.key
SYNC_VERIFY_CERT=false
```

#### 2. Let's Encrypt + DNS Challenge (productie, aanbevolen)

**Voordelen**: Geldig certificaat, automatische renewals, ondersteunt meerdere DNS providers.

**Ondersteunde DNS providers**:

- CloudFlare
- AWS Route53
- Azure DNS
- Google Cloud DNS
- PowerDNS (manual mode met API automation optie)
- Manual DNS validation

**Setup (gemakkelijk)**:

```bash
sudo bash scripts/deploy/setup-letsencrypt.sh
```

Dit script:

1. Vraagt om je DNS provider (bijv. PowerDNS)
2. Configuratie van credentials
3. Verzoekt Let's Encrypt certificaat
4. Activeer automatische renewals

**Voor PowerDNS specifiek**:

```bash
sudo bash scripts/deploy/setup-letsencrypt.sh
# Kies: 5. PowerDNS
# Voer PowerDNS API URL en Key in
# Volg DNS validation prompts
```

Optioneel: Maak auth hook voor automatische DNS updates via PowerDNS API (zie output van setup script).

**Resultaat in `.env`:**

```env
MANAGER_SCHEME=https
MANAGER_CERT=/etc/letsencrypt/live/proxy.name.nl/fullchain.pem
MANAGER_KEY=/etc/letsencrypt/live/proxy.name.nl/privkey.pem
SYNC_VERIFY_CERT=true
```

### Let op

- Bij `MANAGER_SCHEME=https` zijn `MANAGER_CERT` en `MANAGER_KEY` verplicht.
- Sync gebruikt standaard dezelfde scheme/poort als de manager, tenzij anders opgegeven.
- Zet `SYNC_VERIFY_CERT=false` voor self-signed certs.
- Gebruik `SYNC_VERIFY_CERT=true` voor Let's Encrypt certs (geldig CA).
- Gebruik in productie bij voorkeur `AUTH_PASSWORD_HASH`; laat `AUTH_PASSWORD` leeg.
- Let's Encrypt certificaten vervallen na 90 dagen; automatische renewal is verplicht.

### Nginx migratie (HTTPS op poort 8080)

Wil je migreren naar Nginx zonder je publieke poort (`8080`) te wijzigen, gebruik dan Nginx als TLS terminator op `:8080` en laat Gunicorn intern op een Unix socket draaien.

1. Voeg deze `.env` instellingen toe:

```env
MANAGER_SCHEME=https
MANAGER_PORT=8080
PROXY_FIX_ENABLED=true

# Gunicorn achter Nginx (intern)
GUNICORN_BIND=unix:/run/proxypermit/gunicorn.sock
GUNICORN_USE_TLS=false

# Sync via Nginx endpoint
SYNC_SCHEME=https
SYNC_PORT=8080
SYNC_VERIFY_CERT=true
```

1. Herlaad de service:

```bash
sudo systemctl daemon-reload
sudo systemctl restart proxypermit
```

1. Zet automatisch het Nginx-migratieprofiel in `.env`:

```bash
make nginx-env-8080
```

Dit zet o.a. deze variabelen goed voor de migratie:

- `MANAGER_SCHEME=https`
- `MANAGER_PORT=8080`
- `PROXY_FIX_ENABLED=true`
- `GUNICORN_BIND=unix:/run/proxypermit/gunicorn.sock`
- `GUNICORN_USE_TLS=false`
- `SYNC_SCHEME=https`
- `SYNC_PORT=8080`

1. Render en installeer de Nginx-config direct uit `.env`:

```bash
make nginx-install-config
```

Hiermee wordt eerst `nginx/proxypermit.generated.conf` gegenereerd en daarna geinstalleerd als:

- `/etc/nginx/sites-available/proxypermit.conf`
- Nginx wordt automatisch geinstalleerd als die nog ontbreekt
- Nginx service wordt automatisch enabled + gestart
- default Nginx site (`sites-enabled/default` / `default.conf`) wordt uitgeschakeld
- HSTS header wordt in Nginx gezet op basis van `ENABLE_HSTS` + `HSTS_*` variabelen uit `.env`
- HTTP op poort 80 wordt automatisch met `301` doorgestuurd naar HTTPS op je manager-poort (bijv. `:8080`)

1. Herlaad Nginx en de manager-service:

```bash
make nginx-reload
```

Alles in 1 stap kan ook:

```bash
make nginx-migrate-8080
```

Revert (deinstall) kan met:

```bash
make nginx-uninstall-config
```

Dit doet:

- disable/remove van de Nginx site config
- default Nginx site wordt (indien aanwezig) weer ingeschakeld
- restore van de laatste `.env.bak.*` (indien aanwezig)
- Nginx service wordt gestopt + disabled
- restart van de manager-service

Optioneel: statische voorbeeldtemplate in de repo:

- `deploy/nginx/proxypermit-https-8080.conf`

Referentie-config (vereenvoudigd):

```nginx
server {
    listen 8080 ssl http2;
    server_name proxy.example.local;

    ssl_certificate /etc/letsencrypt/live/proxy.example.local/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/proxy.example.local/privkey.pem;

    location / {
        proxy_pass http://unix:/run/proxypermit/gunicorn.sock;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Tip: beperk in Nginx alleen peer-endpoints (zoals `/api/sync/download`, `/api/sync/upload`, `/api/sync/info`, `/api/sync/set-conflict`, `/api/sync/clear-conflict`) tot peer-node IP's; laat UI-endpoints via app-auth lopen.

### AUTH_PASSWORD_HASH genereren

```bash
python3 scripts/tools/generate_auth_hash.py
```

Dit script print direct de regels die je in `.env` kunt zetten.

### RADIUS integratie (optioneel, experimenteel/ongetest)

> Waarschuwing: RADIUS support is momenteel experimenteel en nog niet volledig productie-getest.
> Gebruik dit alleen na eigen validatie in een testomgeving.

Voor centralized user management kun je RADIUS authenticatie inschakelen:

**Setup:**

1. **Installeer pyrad library:**

   ```bash
   pip install pyrad==2.4
   ```

2. **Configureer .env variabelen:**

   ```bash
   ENABLE_RADIUS=true
   RADIUS_SERVER=auth.example.com
   RADIUS_PORT=1812
   RADIUS_SECRET=YourSharedSecret
   RADIUS_TIMEOUT=5
   RADIUS_ALLOW_LOCAL_FALLBACK=true  # Fall back op local password als RADIUS down
   ```

3. **Zorg dat je RADIUS server accessible is** (port 1812/UDP openstellen als nodig).

**Behavior:**

- Bij login RADIUS-poging aangevraagd
- Als RADIUS authenticate succesvol: gebruiker ingelogd
- Als RADIUS timeout/error en `RADIUS_ALLOW_LOCAL_FALLBACK=true`: fallback op local password (AUTH_PASSWORD_HASH)
- Als `RADIUS_ALLOW_LOCAL_FALLBACK=false` en RADIUS error: login geweigerd
- Login-logs tonen of auth via RADIUS of local gebeurde

**Fallback scenario:**
Als je RADIUS server onderhoud ondergaat, users kunnen zich nog altijd aanmelden met `AUTH_PASSWORD_HASH` (als fallback ingeschakeld).

### Sync over HTTPS

Alle sync-API calls (info/download/upload/conflict) gebruiken het ingestelde protocol en poort. Controleer dat beide nodes dezelfde settings gebruiken.

---

## Peer hostnames mapping voor HTTPS

Als je peers via HTTPS wilt benaderen zonder DNS of certificaatproblemen, voeg dan een mapping toe van IP naar hostname. Dit zorgt ervoor dat de frontend bij https altijd de juiste hostname gebruikt (vereist voor geldige certificaatcontrole in de browser).

### Handmatig toevoegen aan .env

Voeg de volgende regel toe aan je `.env` (gebruik de juiste hostnames die overeenkomen met je certificaat):

```env
PEER_HOSTNAMES='{"192.168.6.82": "node0.example.com", "192.168.6.83": "node1.example.com"}'
```

**Let op:**

- Bij HTTP wordt gewoon het IP gebruikt.
- Bij HTTPS wordt de hostname uit deze mapping gebruikt. Ontbreekt deze, dan zal de browser de request blokkeren als het certificaat niet klopt.
- De hostnames moeten exact overeenkomen met de CN/SAN van het TLS-certificaat.

### Automatisch via setup-wizard

De setup-wizard vraagt nu optioneel om hostnames voor elke peer. Deze mapping wordt automatisch als PEER_HOSTNAMES in de .env gezet.

### Voorbeeld .env fragment

```env
NODE0_ADDRESS=192.168.6.82
NODE1_ADDRESS=192.168.6.83
PEER_HOSTNAMES='{"192.168.6.82": "node0.example.com", "192.168.6.83": "node1.example.com"}'
```

---

## Gebruik (web interface)

1. Open http://`server-ip`:8080
2. Login met credentials uit de wizard
3. Entry toevoegen:
   - Type: Domeinnaam (aanbevolen)
   - Domein: github.com
   - Beschrijving: GitHub voor CI/CD pipelines
   - Klik Entry Toevoegen

Tips:

- Gebruik Domeinnaam voor 99% van de cases.
- Regex is alleen voor experts.
- Regex mag uitgebreider, maar met guardrails: alleen ERE (geen PCRE/lookarounds), max 1000 tekens, geen geneste wildcard-repetitie (bijv. (.*)+), en extreem hoge herhalingen (>999) worden geweigerd.
- Laat auto-restart aan.

---

## Import en export

- Import: standaard **Toevoegen (append)**; kies **Vervangen** om de volledige whitelist te overschrijven (backup wordt gemaakt). Server-side validatie en normalisatie worden toegepast; commentaarregels (`#`) zijn toegestaan.
- Export: kies `Domein/Wildcard` (gebruiksvriendelijk, standaard) of `Regex`. Eenvoudige Tinyproxy-regexes worden zo mogelijk teruggezet naar schone domeinen/wildcards; complexe regexes blijven onveranderd.
- Preview: de import-modal toont een compacte diff-achtige preview (kort voorbeeld, `Toon alles` voor volledige lijst). De server bepaalt uiteindelijk welke regels worden opgeslagen.

Zie `app/main.py`, `app/whitelist_manager.py` en `app/validators.py` voor details.

---

## Keepalived (failover)

Voorbeeld `keepalived.conf` (master):

```conf
vrrp_script chk_tinyproxy {
    script "/etc/keepalived/check_tinyproxy.sh"
    interval 5
    weight -60
    fall 2
    rise 2
    user keepalived_script
}

vrrp_instance VI_PROXY {
    state MASTER
    interface ens192
    virtual_router_id 51
    priority 150
    advert_int 1
    preempt_delay 30

    unicast_src_ip <local-ip>
    unicast_peer {
        <peer-ip>
    }

    authentication {
        auth_type PASS
        auth_pass <StrongSecret>
    }

    virtual_ipaddress {
        <vip-ip>/<cidr>
    }

    track_script {
        chk_tinyproxy
    }

    notify /etc/keepalived/notify.sh
}
```

Backup node:

- state BACKUP
- priority lager dan master

Wizard maakt `check_tinyproxy.sh` en `notify.sh` aan.

`check_tinyproxy.sh` doet:

- `systemctl is-active tinyproxy`
- Controle of `tinyproxy.conf` bestaat
- Controle of poort 3128 bereikbaar

---

## Sync v2 (bidirectioneel, veilig)

Endpoints:

- `/api/sync/info` (GET)
- `/api/sync/download` (GET)
- `/api/sync/upload` (POST)
- `/api/sync/status` (GET) — UI-friendly; returns a sanitized state when unauthenticated
- `/api/sync/peer-status` (GET) — **Backend performs peer health check; browser never contacts peer directly** (security/production). Returns peer node sync status via backend-to-backend communication.
- `/api/sync/set-conflict` (POST)
- `/api/sync/clear-conflict` (POST)

Security & behaviour (kort):

- HMAC-SHA256 met headers `X-Sync-Timestamp`, `X-Sync-Nonce`, `X-Sync-Signature` (server-side verificatie).
- Timestamp tolerance: 60s (`MAX_TIMESTAMP_DIFF`). Nonces ouder dan 120s worden gerecycled (`MAX_NONCE_AGE`).
- Nonce-store: `/var/lib/proxypermit/sync-nonces.json`.
- IP-allowlist gebruikt `NODE0_ADDRESS`/`NODE1_ADDRESS` (127.0.0.1 optioneel voor tests). `clear-conflict` heeft een iets soepelere toelating in conflict-situaties.
- Rate limiting: standaard 20 requests/min per IP (`RATE_LIMIT_MAX_REQUESTS` / `RATE_LIMIT_WINDOW`).
- Upload: server verifieert SHA256 van de geschreven temp-file; `/api/sync/info` geeft de huidige file-hash terug zodat peers kunnen vergelijken.
- Backup: er wordt altijd een backup gemaakt voor overschrijven; backups beheerd door de manager (recentste versies worden bewaard).
- Browser isolatie: `/api/sync/peer-status` zorgt ervoor dat de browser (frontend) nooit rechtstreeks contact maakt met peer nodes in productie—backend voert alle peer checks uit. Dit voorkomt ongeautoriseerde directe verbindingen van browsers/clients.

Belangrijke paden:

- Nonce store: `/var/lib/proxypermit/sync-nonces.json`
- Sync state: `/var/lib/proxypermit/sync-state.json`
- Manual sync state: `/var/lib/proxypermit/manual-sync.json`

Opmerking: er bestaan aanvullende helper-endpoints voor handmatige runs en merge flows (bijv. `/api/sync/run`, `/api/sync/merge-data`, `/api/sync/merge-apply`).

---

## Health en monitoring

Health endpoint:

- `GET /health` — Geeft status en checks terug (keepalived, VIP, tinyproxy, port)

Andere checks:

```bash
systemctl is-active tinyproxy
systemctl is-active keepalived
ip addr show | grep <vip>
```

---

## Security en privacy

**Security:**

- Session login verplicht (`ENABLE_AUTH=true`).
- Gebruik `AUTH_PASSWORD_HASH` (one-way hash), geen plaintext in `.env`.
- `FLASK_SECRET_KEY` verplicht.
- HMAC auth voor sync endpoints.
- Security headers en input validatie.

**Privacy:**

- Geen persoonlijke data logging.
- Alleen functionele data (whitelist, backups, technische logs).
- Sessiecookie is HttpOnly.

---

## Troubleshooting

Proxy werkt niet:

```bash
sudo systemctl status tinyproxy
sudo tail -f /var/log/tinyproxy/tinyproxy.log
curl -x http://`vip`:3128 https://google.com
```

Management GUI niet bereikbaar:

```bash
systemctl status proxypermit
journalctl -u proxypermit -n 200 --no-pager
```

Audit / Auth logs (voorbeeld):

```bash
# Controleer de audit logfile (pad instelbaar via `AUDIT_LOG_PATH`)
sudo tail -f /var/log/proxypermit/audit.log
```

Keepalived failover werkt niet:

```bash
sudo systemctl status keepalived
sudo journalctl -u keepalived -n 100 --no-pager
ip addr show | grep <vip>
```

Sync v2 faalt:

```bash
sudo systemctl status whitelist-sync-v2.timer --no-pager
sudo journalctl -u whitelist-sync-v2 -n 50 --no-pager
```

---

## Belangrijke paden

`/opt/proxypermit/`

- `.env` (bevat credentials)
- `proxypermit.service`
- `app/` (Flask)
- `requirements/base.txt`

`/etc/tinyproxy/`

- `tinyproxy.conf`
- `whitelist.txt`
- `.backups/`

`/etc/proxypermit/`

- `sync-secret.key`
- `flask-secret.key`

`/var/lib/proxypermit/`

- `sync-state.json`
- `manual-sync.json`
- `session-state.json`

`/var/log/`

- `tinyproxy/tinyproxy.log`
- `tinyproxy-sync.log`

---

## tinyproxy.conf uitleg en best practices

De file `/etc/tinyproxy/tinyproxy.conf` is het hart van de proxy-configuratie. Deze wordt automatisch gegenereerd door de setup-wizard, maar kan handmatig worden aangepast voor geavanceerde use-cases.

### Voorbeeld tinyproxy.conf

```conf
# Connectivity
Port 3128
Listen 0.0.0.0
Timeout 600

# Logging
LogFile "/var/log/tinyproxy/tinyproxy.log"
PidFile "/run/tinyproxy/tinyproxy.pid"
LogLevel Info

# ACL - alleen toegang voor opgegeven netwerken (Allow)
Allow 10.0.0.0/8
Allow 172.16.0.0/12
Allow 192.168.0.0/16
Deny 0.0.0.0/0

# Whitelist filtering (default deny)
Filter "/etc/tinyproxy/whitelist.txt"
FilterDefaultDeny Yes
FilterType ere
FilterCaseSensitive No
FilterURLs Off

# HTTPS support
ConnectPort 443
ConnectPort 8443
```

### Uitleg van de belangrijkste parameters

- **Port**: Luisterpoort voor proxy-verkeer (standaard 3128).
- **Listen**: IP-adres/interface waarop geluisterd wordt. Standaard `0.0.0.0` (alle interfaces).
- **Timeout**: Idle timeout in seconden.
- **LogFile / LogLevel**: Locatie en detailniveau van logging.
- **Allow/Deny**: Toegestane netwerken. Voeg alleen trusted ranges toe! `Deny 0.0.0.0/0` blokkeert alles wat niet expliciet is toegestaan.
- **Filter**: Pad naar de whitelist file. Alleen domeinen/regexen in deze file zijn toegestaan.
- **FilterDefaultDeny**: Blokkeer alles wat niet in de whitelist staat.
- **FilterType ere**: Gebruik ERE (Extended Regular Expressions) voor whitelist matching.
- **FilterCaseSensitive/URLs**: Extra filteropties, standaard veilig ingesteld.
- **ConnectPort**: Toegestane poorten voor CONNECT (voor HTTPS proxying).

### Best practices

- Pas alleen trusted netwerken toe in de Allow-lijst.
- Laat FilterDefaultDeny altijd op Yes staan voor maximale veiligheid.
- Gebruik de webinterface voor whitelist beheer; pas de whitelist file niet handmatig aan.
- Herstart tinyproxy na wijzigingen in deze file: `sudo systemctl restart tinyproxy`.
- Maak altijd een backup voor handmatige aanpassingen.

De wizard kan deze file opnieuw genereren. Voor geavanceerde tuning zie de [Tinyproxy documentatie](https://tinyproxy.github.io/).

---

### FilterType ERE — Gebruik & testen

- Doel: Tinyproxy draait met `FilterType ere`, zodat whitelist-regels als ERE (extended regular expressions) worden geïnterpreteerd. De helper in `app/validators.py` converteert ingevoerde domeinen naar ERE-patronen; in de huidige implementatie levert dat de volgende vormen op:

```text
*.example.com          -> ^(.+\.)?example\.com(:[0-9]+)?$              (matcht root + alle subdomeinen)
example.com            -> ^example\.com(:[0-9]+)?$                       (exacte match alleen)
graph.microsoft.com    -> ^graph\.microsoft\.com(:[0-9]+)?$              (exacte match alleen)
```

Zorg dat `FilterURLs` op `Off` staat zodat Tinyproxy op host/poort matcht in plaats van op volledige URL's.

-- Belangrijke configuratie: voeg de volgende regels toe aan `tinyproxy.conf` of laat de wizard deze genereren:

```conf
Filter "/etc/tinyproxy/whitelist.txt"
FilterDefaultDeny Yes
FilterType ere
FilterCaseSensitive No
FilterURLs Off

```

- Wat wordt gegenereerd: wanneer een gebruiker een domein invoert wordt dit geconverteerd naar een ERE-compatibel patroon door `WhitelistValidator.domain_to_regex()`:

```text
*.example.com          -> ^(.+\.)?example\.com(:[0-9]+)?$              (root + alle subdomeinen)
example.com            -> ^example\.com(:[0-9]+)?$                       (exacte match alleen)
graph.microsoft.com    -> ^graph\.microsoft\.com(:[0-9]+)?$              (exacte match, geen extra sub-labels)
192.168.1.1            -> ^192\.168\.1\.1(:[0-9]+)?$                     (exacte IPv4 match)
```

Let op het verschil met de FortiGate-logica:

- `*.example.com` — matcht `example.com` **en** `www.example.com` **en** `api.v2.example.com` (root + alle subdomeinen, dankzij de optionele `(.+\.)?` prefix).
- `example.com` — matcht **alleen** `example.com`, niet `www.example.com`.
- `graph.microsoft.com` — matcht **alleen** dat specifieke subdomein, niet `internal.graph.microsoft.com`.

- Testen lokaal: gebruik het meegeleverde hulpscript om patronen te valideren tegen voorbeeld-hosts.

```bash
python scripts/tools/validate_ere.py --whitelist ./tests/fixtures/example_whitelist.txt --hosts "example.com" "sub.example.com" "example.com:8443"
```

Het script leest non-comment regels uit de whitelist, compileert elke regel als ERE en toont welke hosts door welk patroon gematcht worden. Dit simuleert het gedrag van Tinyproxy en helpt bij validatie voor deploy.

Opmerkingen:

- Het hulpscript probeert de gedeelde productie-validator (`WhitelistValidator` in `app/validators.py`) te gebruiken voor normalisatie en veiligheidschecks. Daarom is het aan te raden het script vanuit de repository-root te draaien (zodat `app` op `PYTHONPATH` staat), bijv. `python scripts/tools/validate_ere.py ...`.
- Als `app.core.validators` niet importeerbaar is, valt het script terug op basis-regex-matching en toont een waarschuwing. Het resultaat blijft bruikbaar, maar kan minder strikt zijn dan de productie-validator.
- Voor CI of pre-deploy checks kun je dit script toevoegen als stap en failen op basis van de output (optioneel - ik kan een `--fail-on-invalid` toevoeging implementeren als gewenst).
- De validator blokkeert patronen die alles matchen (.* / .+), geneste wildcard-repetitie (bijv. (.*)+) en extreem hoge herhalingen, om performance-issues te voorkomen.

## Makefile & hulpscripts

Voor veelvoorkomende beheer- en deploymenttaken is een Makefile meegeleverd. Hiermee kun je snel veelgebruikte acties uitvoeren zonder lange commando's te onthouden.

### Belangrijkste Makefile commando's

- `make help` — Overzicht van alle beschikbare targets/commando's.
- `make setup` — Start de interactieve setup wizard (`setup-wizard.py`).
- `make deploy` — Volledige installatie via `deploy.sh` (aanbevolen voor productie).
- `make venv` — Zet de Python virtual environment op en installeer dependencies.
- `make auth-hash` — Genereer interactief een `AUTH_PASSWORD_HASH` regel voor `.env`.
- `make install` — Installeer de systemd service (handmatig alternatief voor deploy).
- `make install-logrotate` — Installeer (indien nodig) het `logrotate` package en plaats de rotatieconfig op `/etc/logrotate.d/proxypermit`.
- `make uninstall` — Verwijder manager + sync units + logrotate/config/state gerelateerde componenten.
- `make start` / `make stop` / `make restart` — Start/stop/herstart de service.
- `make status` — Bekijk de status van de service.
- `make logs` — Bekijk live logs van de service.
- `make fix-perms` — Zet productiegerichte permissies (o.a. `.env`, secrets, state-files en scripts).
- `make backup` — Maak een handmatige backup van de whitelist.
- `make restore` — Herstel een backup van de whitelist.
- `make prod-check` — Draai een pre-productie checklist (nadat alles geïnstalleerd is).

### Nginx migratie commando's

- `make nginx-env-8080` — Zet `.env` op het Nginx migratieprofiel (HTTPS op `:8080`, proxyfix en socket-bind).
- `make nginx-render-config` — Genereer `nginx/proxypermit.generated.conf` uit `.env`.
- `make nginx-install-config` — Installeer Nginx (indien nodig), installeer de gegenereerde site-config, schakel default site uit, enable/start Nginx.
- `make nginx-reload` — `systemd daemon-reload`, restart manager, `nginx -t` en reload Nginx.
- `make nginx-migrate-8080` — One-shot migratie (`nginx-env-8080` + `nginx-install-config` + `nginx-reload`).
- `make nginx-uninstall-config` — Revert migratie: verwijder custom site, herstel default site (indien aanwezig), herstel laatste `.env.bak.*`, stop/disable Nginx.

### Sync & HA commando's

- `make sync-generate-secret` — Genereer een nieuwe sync secret key.
- `make sync-verify-secret` — Controleer of de sync secret correct is ingesteld.
- `make sync-install` — Installeer de sync v2 systemd timer/service.
- `make sync-status` — Bekijk de status van de sync timer.
- `make sync-logs` / `make sync-logs-follow` — Bekijk (live) logs van de sync service.
- `make sync-test` — Trigger een handmatige sync.
- `make sync-state` — Bekijk de huidige sync state (JSON).

### Overige hulpscripts

- `deploy.sh` — Hoofdinstallatiescript voor productie (installeert alles, start wizard indien nodig).
- `pre-prod-check.sh` — Draait een uitgebreide pre-productie checklist (controleert o.a. configuratie, dependencies, permissies).
- `deploy-sync-v2.sh` — Installeert/activeert de sync v2 systemd timer/service.
- `generate-sync-secret.sh` — Genereert een nieuwe sync secret key (los script).

Zie de Makefile zelf voor alle opties en details. De meeste taken kunnen via `make <commando>` of direct via de scripts uitgevoerd worden.
