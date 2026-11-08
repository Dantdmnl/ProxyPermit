> Legacy-documentatie: beschrijft de oorspronkelijke twee-node-inrichting. Zie de [actuele README](../../README.md).

# Omgevingsvariabelen

Dit document beschrijft de omgevingsvariabelen die gebruikt worden door de ProxyPermit app en hulpscripts.
Tenzij anders vermeld, zet je ze in .env. Boolean waarden accepteren true/false (niet hoofdlettergevoelig).
De kolom Opties geeft toegestane waarden of het verwachte formaat.

## Kernpaden en services

| Variabele | Standaard | Opties | Beschrijving |
| --- | --- | --- | --- |
| FLASK_SECRET_KEY | verplicht | string (min 32 tekens) | Secret key voor Flask sessies en tokens. Moet in productie gezet zijn. |
| FLASK_ENV | production (wizard) | string | Flask runtime env. Niet direct gebruikt door de app, maar behouden voor compatibiliteit. |
| WHITELIST_PATH | /etc/tinyproxy/whitelist.txt | pad | Pad naar Tinyproxy whitelist bestand. Gebruikt door app en sync. |
| TINYPROXY_SERVICE | tinyproxy | string | systemd service naam voor status en restart acties. |
| TINYPROXY_LOGFILE | /var/log/tinyproxy/tinyproxy.log | pad | Pad naar Tinyproxy logbestand (voor UI log features). |
| TINYPROXY_PORT | leeg | integer 1-65535 of leeg | Optionele TCP port check voor /api/health. Als gezet, checkt de app localhost. |
| WHITELIST_ENTRY_LOG_PATH | whitelist dir/whitelist_entry_log.json | pad | JSON log voor whitelist entry wijzigingen. |
| MAX_REQUEST_SIZE | 2097152 | integer bytes, minimaal 1024 | Maximale HTTP request body voor onder andere imports (standaard 2 MiB). |

## Authenticatie en sessiebeveiliging

| Variabele | Standaard | Opties | Beschrijving |
| --- | --- | --- | --- |
| ENABLE_AUTH | false | true, false | Zet UI authenticatie aan of uit. |
| AUTH_USERNAME | leeg | string | Enkele admin username (verplicht als ENABLE_AUTH=true). |
| AUTH_PASSWORD_HASH | leeg | string | Hashed wachtwoord (aanbevolen). |
| AUTH_PASSWORD | leeg | string | Legacy plaintext fallback (vermijd in productie). |
| SESSION_COOKIE_SECURE | false | true, false | Force secure cookie flag. Wordt automatisch aan bij MANAGER_SCHEME=https. |
| DEMO_MODE | false | true, false | Demo UI mode (onderdrukt enkele status waarschuwingen). |

## Inlog rate limiting

| Variabele | Standaard | Opties | Beschrijving |
| --- | --- | --- | --- |
| AUTH_LOGIN_RATE_LIMIT_ENABLED | true | true, false | Zet login rate limiting en lockout aan of uit. |
| AUTH_LOGIN_MAX_ATTEMPTS | 5 | integer >=1 | Max mislukte pogingen binnen het venster. |
| AUTH_LOGIN_WINDOW_SECONDS | 300 | integer seconden >=10 | Venster grootte voor mislukte pogingen. |
| AUTH_LOGIN_LOCKOUT_SECONDS | 120 | integer seconden >=10 | Lockout duur na overschrijding. |
| AUTH_LOGIN_DYNAMIC_LOCKOUT_ENABLED | false | true, false | Exponentiele lockout bij herhaald misbruik. |
| AUTH_LOGIN_MAX_LOCKOUT_SECONDS | 900 | integer seconden >=10 | Max lockout duur bij dynamische lockout. |
| AUTH_LOGIN_STRIKE_RESET_SECONDS | 3600 | integer seconden >=60 | Tijdvenster voor reset van strikes. |

## RADIUS (optioneel)

| Variabele | Standaard | Opties | Beschrijving |
| --- | --- | --- | --- |
| ENABLE_RADIUS | false | true, false | Zet RADIUS auth aan voor UI login. Vereist pyrad in de venv. |
| RADIUS_SERVER | leeg | hostname of IP | RADIUS server hostname of IP. |
| RADIUS_PORT | 1812 | integer 1-65535 | RADIUS UDP port. |
| RADIUS_SECRET | leeg | string | Shared secret. Behandel als secret. |
| RADIUS_TIMEOUT | 5 | integer seconden | Timeout in seconden. |
| RADIUS_ALLOW_LOCAL_FALLBACK | true | true, false | Sta local auth toe als RADIUS onbereikbaar is. |

## Manager URLs, CORS en UI hints

| Variabele | Standaard | Opties | Beschrijving |
| --- | --- | --- | --- |
| MANAGER_SCHEME | http | http, https | Scheme voor manager URLs. |
| MANAGER_PORT | 8080 | integer 1-65535 | Manager port. |
| MANAGER_BIND | 0.0.0.0 | ip of hostname | Gunicorn bind adres als GUNICORN_BIND leeg is. |
| MANAGER_CERT | leeg | pad | TLS cert pad (verplicht als Gunicorn TLS aan staat). |
| MANAGER_KEY | leeg | pad | TLS key pad (verplicht als Gunicorn TLS aan staat). |
| MANAGER_ALLOWED_ORIGINS | leeg | comma-separated URLs | Extra toegestane origins voor CORS (comma-separated). |
| VIP_ADDRESS | leeg | ip of hostname | VIP adres voor UI status en peer discovery. |
| NODE0_ADDRESS | leeg | ip of hostname | Node0 adres voor UI status en peer discovery. |
| NODE1_ADDRESS | leeg | ip of hostname | Node1 adres voor UI status en peer discovery. |
| PEER_HOSTNAMES | {} | JSON object | JSON map van IP naar hostname (voor HTTPS peer URLs). |

## ProxyFix (reverse proxy headers)

| Variabele | Standaard | Opties | Beschrijving |
| --- | --- | --- | --- |
| PROXY_FIX_ENABLED | false | true, false | Zet ProxyFix aan voor reverse proxy headers. |
| PROXY_FIX_X_FOR | 1 | integer >=0 | Trusted hop count voor X-Forwarded-For. |
| PROXY_FIX_X_PROTO | 1 | integer >=0 | Trusted hop count voor X-Forwarded-Proto. |
| PROXY_FIX_X_HOST | 1 | integer >=0 | Trusted hop count voor X-Forwarded-Host. |
| PROXY_FIX_X_PORT | 1 | integer >=0 | Trusted hop count voor X-Forwarded-Port. |

## Gunicorn

| Variabele | Standaard | Opties | Beschrijving |
| --- | --- | --- | --- |
| GUNICORN_BIND | leeg | unix:/path.sock of host:port | Override bind adres (bijv. unix:/run/proxypermit/gunicorn.sock). |
| GUNICORN_USE_TLS | leeg | true, false, leeg | Force TLS aan of uit. Overschrijft auto detectie. |
| GUNICORN_WORKERS | 2 | integer >=1 | Gunicorn worker count. |
| GUNICORN_LOGLEVEL | info | debug, info, warning, error, critical | Gunicorn log level. |

## HSTS

| Variabele | Standaard | Opties | Beschrijving |
| --- | --- | --- | --- |
| ENABLE_HSTS | true | true, false | Zet HSTS header aan voor HTTPS responses. |
| HSTS_MAX_AGE | 31536000 | integer seconden | HSTS max-age in seconden. |
| HSTS_INCLUDE_SUBDOMAINS | true | true, false | Voeg subdomains toe aan HSTS. |
| HSTS_PRELOAD | false | true, false | Voeg preload directive toe (alleen als geschikt). |

## Sync en HA

| Variabele | Standaard | Opties | Beschrijving |
| --- | --- | --- | --- |
| SYNC_SECRET_FILE | /etc/proxypermit/sync-secret.key | pad | HMAC secret file voor sync endpoints. Moet op beide nodes gelijk zijn. |
| SYNC_SECRET_PATH | /etc/proxypermit/sync-secret.key | pad | Secret pad voor remote merge preview. Houd gelijk aan SYNC_SECRET_FILE. |
| SYNC_SCHEME | MANAGER_SCHEME | http, https | Scheme voor sync API calls. |
| SYNC_PORT | MANAGER_PORT | integer 1-65535 | Port voor sync API calls. |
| SYNC_VERIFY_CERT | true | true, false | TLS cert verificatie voor sync calls. Zet false bij self-signed certs. |
| ALLOW_LOCALHOST_SYNC | false | true, false | Sta 127.0.0.1 toe in peer list (demo of proxy). |
| SYNC_MASTER_HOST | leeg | hostname of IP | Optionele master host of IP voor conflict resolutie. |
| SYNC_MASTER_PREFERENCE | false | true, false | Prefer master host bij near-simultane wijzigingen. |
| SYNC_NEAR_SIMULTANEOUS_WINDOW | 30 | integer seconden >=5 | Window voor near-simultane edits. Minimum 5. |
| SYNC_DEFER_ON_NEAR_SIMULTANEOUS | true | true, false | Defer sync als near-simultane edits gedetecteerd worden. |
| SYNC_MAX_WHITELIST_SIZE | 1048576 | integer bytes >=1024 | Max whitelist size die de sync accepteert. |
| MAX_WHITELIST_SIZE | legacy | integer bytes >=1024 | Legacy fallback voor SYNC_MAX_WHITELIST_SIZE. |
| KEEPALIVED_UNICAST_SRC | leeg | IP | Local unicast src IP (identificeert deze node). |

## Sync state bestanden

| Variabele | Standaard | Opties | Beschrijving |
| --- | --- | --- | --- |
| NONCE_STORE_FILE | /var/lib/proxypermit/sync-nonces.json | pad | Nonce cache voor replay protection. |
| SESSION_STATE_FILE | /var/lib/proxypermit/session-state.json | pad | Session state storage. |
| SYNC_STATE_FILE | /var/lib/proxypermit/sync-state.json | pad | Sync status state. |
| MANUAL_SYNC_STATE_FILE | /var/lib/proxypermit/manual-sync.json | pad | Manual sync state. |

## Logging en privacy

| Variabele | Standaard | Opties | Beschrijving |
| --- | --- | --- | --- |
| AUDIT_LOG_PATH | audit.log | pad | Audit log pad (JSON lines). Relatieve paden resolven vanaf de werkdirectory. |
| AUDIT_LOG_MAX_BYTES | 2097152 | integer bytes >=1 | Max audit log size voor rotatie. |
| AUDIT_LOG_BACKUP_COUNT | 5 | integer >=1 | Aantal rotated audit logs om te bewaren. |
| LOG_API_MAX_PAGE_LIMIT | 5000 | integer >=100 | Max logregels per pagina via /api/logs. |
| LOG_API_MAX_SCAN_LIMIT | 120000 | integer >=1000 | Max scan window voor log queries. |
| LOG_API_MAX_PER_SOURCE_LIMIT | 50000 | integer >=500 | Max regels per logbron tijdens scan. |
| LOG_API_MANAGER_PER_SOURCE_LIMIT | 3000 | integer >=500 | Max regels uit manager logs tijdens scan. |
| LOG_IP_MODE | full | full, masked, hash | IP weergave mode: full, masked, hash. |
| CLIENT_IP_MAP_PATH | leeg | pad | JSON file met mapping van ip naar naam. |
| CLIENT_IP_MAP_JSON | leeg | JSON object string | JSON string mapping; override of aanvulling op CLIENT_IP_MAP_PATH. |
| AUDIT_ENTRY_MODE | preview | preview, full, hash | Audit payload mode: preview, full, hash. |
| AUDIT_ENTRY_MAX_LEN | 160 | integer >=40 | Max lengte voor preview mode. |

## UI huisstijl

| Variabele | Standaard | Opties | Beschrijving |
| --- | --- | --- | --- |
| MANAGER_LOGO_URL | leeg | URL of pad | Optionele logo URL of statisch pad voor de UI header. |
| MANAGER_LOGO_ALT | leeg | string | Alt tekst voor het logo. |

## Tinyproxy config generatie (wizard metadata)

Deze variabelen worden door setup-wizard geschreven voor referentie en voor tinyproxy.conf generatie. De draaiende app leest ze niet.

| Variabele | Standaard (wizard) | Opties | Beschrijving |
| --- | --- | --- | --- |
| TINYPROXY_CONFIG | /etc/tinyproxy/tinyproxy.conf | pad | Pad naar tinyproxy.conf gegenereerd door de wizard. |
| TINYPROXY_LISTEN | 0.0.0.0 | ip | Listen adres voor Tinyproxy. |
| TINYPROXY_TIMEOUT | 600 | integer seconden | Tinyproxy timeout in seconden. |
| TINYPROXY_LOG_LEVEL | Info | Critical, Error, Warning, Notice, Connect, Info | Tinyproxy log level. |
| TINYPROXY_PIDFILE | /run/tinyproxy/tinyproxy.pid | pad | Tinyproxy pid file pad. |
| TINYPROXY_ALLOW | 10.0.0.0/8,172.16.0.0/12,192.168.0.0/16 | comma-separated CIDR list | Allowed client networks (comma-separated). |

## Keepalived config metadata (wizard)

Deze variabelen worden in .env opgeslagen voor traceerbaarheid na het genereren van keepalived.conf. De app gebruikt ze niet direct.

| Variabele | Standaard (wizard) | Opties | Beschrijving |
| --- | --- | --- | --- |
| KEEPALIVED_STATE | MASTER of BACKUP | MASTER, BACKUP | Keepalived rol. |
| KEEPALIVED_INTERFACE | detected iface | interface naam | Network interface voor VRRP. |
| KEEPALIVED_VRID | 51 | integer 1-255 | Virtual router ID. |
| KEEPALIVED_PRIORITY | 150 (MASTER) of 100 (BACKUP) | integer 1-255 | VRRP priority. |
| KEEPALIVED_VIP_CIDR | leeg | CIDR | VIP in CIDR vorm (bijv. 10.0.0.10/24). |
| KEEPALIVED_AUTH_PASS | random | string (max 8 chars) | VRRP auth_pass (max 8 chars). |
| KEEPALIVED_CHK_SCRIPT | /etc/keepalived/check_tinyproxy.sh | pad | Health check script pad. |
| KEEPALIVED_NOTIFY_SCRIPT | /etc/keepalived/notify.sh | pad | Notify script pad. |

## Nginx config generatie (optioneel)

Gebruikt door scripts/deploy/render_nginx_config.sh bij het genereren van een Nginx site config uit .env.

| Variabele | Standaard | Opties | Beschrijving |
| --- | --- | --- | --- |
| NGINX_SERVER_NAME | VIP_ADDRESS | hostname of _ | server_name voor Nginx config. |
| NGINX_SSL_CERT | MANAGER_CERT | pad | TLS certificate pad voor Nginx. |
| NGINX_SSL_KEY | MANAGER_KEY | pad | TLS key pad voor Nginx. |

## Demo en lokaal gebruik

Deze worden vaak gebruikt in scripts/dev/run-demo-windows.ps1 of lokale setups.

| Variabele | Standaard | Opties | Beschrijving |
| --- | --- | --- | --- |
| ALLOW_LOCALHOST_SYNC | false | true, false | Sta localhost toe als sync peer (zet true voor demo). |
| DEMO_MODE | false | true, false | Demo UI gedrag (geen tinyproxy down warning). |
