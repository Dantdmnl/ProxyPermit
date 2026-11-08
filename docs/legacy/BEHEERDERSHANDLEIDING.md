> Legacy-documentatie: beschrijft de oorspronkelijke twee-node-inrichting. Zie de [actuele README](../../README.md).

# Beheerdershandleiding ProxyPermit

**Versie:** 1.0  
**Doelgroep:** functioneel en technisch beheerders  
**Platform:** Ubuntu Server 24.04 LTS  
**Applicatie:** Tinyproxy 1.11.1 + Flask manager + sync v2

Deze handleiding beschrijft hoe je de oplossing beheert in productie, test en herstel. Het document is bedoeld als centrale beheerdersbron en vervangt de mix van losse aanwijzingen, implementatienotities en technische achtergrond die eerder verspreid stond over de documentatie.

## Inhoudsopgave

1. [Doel en scope](#1-doel-en-scope)
2. [Begrippenlijst](#2-begrippenlijst)
3. [Systeemoverzicht](#3-systeemoverzicht)
4. [Rollen en verantwoordelijkheden](#4-rollen-en-verantwoordelijkheden)
5. [Vereisten](#5-vereisten)
6. [Installatie en eerste uitrol](#6-installatie-en-eerste-uitrol)
7. [Dagelijks beheer](#7-dagelijks-beheer)
8. [Whitelist beheren](#8-whitelist-beheren)
9. [Import, export en herstel](#9-import-export-en-herstel)
10. [Configuratie en secrets](#10-configuratie-en-secrets)
11. [High availability en synchronisatie](#11-high-availability-en-synchronisatie)
12. [Monitoring en logging](#12-monitoring-en-logging)
13. [Beveiliging en privacy](#13-beveiliging-en-privacy)
14. [Toegevoegde firewallregels](#14-toegevoegde-firewallregels)
15. [Troubleshooting](#15-troubleshooting)
16. [Onderhoud en wijzigingsbeheer](#16-onderhoud-en-wijzigingsbeheer)
17. [Belangrijke paden en commando's](#17-belangrijke-paden-en-commandos)
18. [Bijlagen](#18-bijlagen)

## 1. Doel en scope

### 1.1 Doel

Deze oplossing levert centraal beheer voor een Tinyproxy-forward-proxy met domeinwhitelisting. Beheerders gebruiken de webinterface om toegestane domeinen te beheren, synchronisatie tussen nodes te controleren en de proxy-omgeving operationeel te houden.

### 1.2 Scope

De oplossing is bedoeld voor:

- beheer van whitelistregels;
- deployment en configuratie van de manager;
- beheer van High Availability (HA) met Keepalived;
- veilige synchronisatie van whitelistdata tussen twee nodes;
- controle van logs, status en herstelacties.

De oplossing is niet bedoeld voor:

- deep packet inspection;
- HTTPS-interceptie of MITM;
- caching;
- generieke webfiltering buiten de whitelistlogica.

### 1.3 Uitgangspunt

De oorspronkelijke use-case was een vervanging van een FortiGate-proxyfunctie die niet meer beschikbaar is. Deze oplossing houdt dezelfde functionele intentie aan: centraal beheren welke domeinen wel of niet toegankelijk zijn.

## 2. Begrippenlijst

- **Tinyproxy**: forward proxy die verkeer toestaat of blokkeert op basis van ACL en whitelistregels.
- **Manager**: Flask-applicatie met webinterface en API voor beheer van de whitelist en services.
- **Whitelist**: lijst met toegestane domeinen of regexpatronen.
- **Regex**: reguliere expressie. Alleen gebruiken als domeininvoer niet voldoende is.
- **HA**: High Availability; opstelling met een actieve en een standby node.
- **MASTER**: actieve node die de VIP bezit.
- **BACKUP**: standby node die de VIP overneemt bij uitval.
- **VIP**: virtual IP-adres dat tussen nodes verhuist.
- **VRRP**: Virtual Router Redundancy Protocol; protocol dat Keepalived gebruikt om VIP-failover te coördineren.
- **Sync v2**: synchronisatiemodule voor uitwisseling van whitelistdata tussen nodes.
- **HMAC**: mechanisme voor integriteitscontrole en authenticatie van syncverkeer.
- **Nonce**: eenmalige waarde tegen replay-aanvallen.
- **Auditlog**: gestructureerde log voor controle- en wijzigingsacties.
- **RADIUS**: Remote Authentication Dial-In User Service; externe authenticatiebron voor validatie van het beheerwachtwoord.
- **Access-Accept**: RADIUS-response die aangeeft dat authenticatie is toegestaan.
- **Access-Reject**: RADIUS-response die aangeeft dat authenticatie is geweigerd.
- **Shared secret (RADIUS)**: gedeeld geheim tussen RADIUS-client en RADIUS-server.
- **Break-glass**: bewust ingerichte lokale noodtoegang voor beheer, bedoeld voor incidenten/uitval van externe authenticatie.
- **`ENABLE_AUTH`**: schakelt UI-authenticatie in of uit.
- **`AUTH_USERNAME`**: vaste beheer-gebruikersnaam; deze implementatie is single-user.
- **`AUTH_PASSWORD_HASH`**: aanbevolen one-way wachtwoordopslag.
- **`ENABLE_RADIUS`**: schakelt RADIUS-authenticatie in of uit (werkt alleen als `pyrad` beschikbaar is).
- **`RADIUS_ALLOW_LOCAL_FALLBACK`**: staat lokale auth toe als RADIUS onbereikbaar is (break-glass gedrag).
- **`sync-secret.key`**: gedeeld geheim voor sync-authenticatie tussen nodes.
- **`flask-secret.key`**: secret voor sessies en tokens, identiek op beide nodes.

## 3. Systeemoverzicht

### 3.1 Hoofdcomponenten

- **Tinyproxy** draait als forward proxy op poort `3128`.
- **Flask manager** levert de webinterface en API's.
- **Whitelist manager** verwerkt veilige schrijfoperaties, backups en rollback.
- **Keepalived** verzorgt VRRP en VIP-failover.
- **Sync v2** synchroniseert whitelistdata tussen nodes.
- **Nginx** kan worden gebruikt als TLS-terminatie en reverse proxy.

### 3.2 Architectuur in beheertermen

- Gebruikers beheren de whitelist via de webinterface.
- De manager schrijft wijzigingen atomisch naar de whitelistbestanden.
- De sync-service vergelijkt en synchroniseert de whitelist tussen nodes.
- Keepalived zorgt ervoor dat de VIP naar de actieve node wijst.
- Nginx kan de manager en sync-endpoints afschermen of termineren op TLS.

### 3.3 Architectuurdiagram en datapaden

Onderstaand diagram beschrijft de standaard HA-opstelling met twee proxy-nodes en een VIP.

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

Componenten (beheerperspectief):

- **Keepalived**: VRRP voor VIP-management en automatische failover.
- **Tinyproxy**: forward proxy met whitelist filtering op poort `3128`.
- **Nginx**: TLS-terminatie op manager-poort, HTTP->HTTPS redirect, security headers en endpoint filtering.
- **Gunicorn**: applicatieserver achter Nginx (bij voorkeur via Unix socket).
- **Flask Manager**: beheer-UI/API voor whitelist, validatie, service-acties en sync-endpoints.
- **Sync v2 API**: bidirectionele whitelist sync via systemd timer/service.

Nginx endpoint model (belangrijk voor beheer en security):

- UI endpoints (zoals `/api/sync/status`, `/api/sync/peer-status`, en handmatige run-endpoints) lopen via normale app-auth.
- Peer-only endpoints (zoals `/api/sync/download`, `/api/sync/upload`, `/api/sync/info`, `/api/sync/set-conflict`, `/api/sync/clear-conflict`) worden in Nginx extra IP-beperkt tot de peer-node(s).

Opmerking: in productie is het uitgangspunt dat de browser (frontend) niet rechtstreeks met peer-nodes praat. Peer checks en sync-status worden via de backend afgehandeld.

### 3.4 Belangrijkste eigenschappen

- atomische writes;
- automatische backup en rollback;
- sessie-authenticatie voor de UI;
- HMAC-authenticatie voor sync;
- rate limiting en nonce-bescherming;
- logging zonder persoonlijke data;
- optionele logo-ondersteuning in de header.

## 4. Rollen en verantwoordelijkheden

### 4.1 Functioneel beheer

Functioneel beheer gebruikt de webinterface voor:

- toevoegen, wijzigen en verwijderen van whitelistregels;
- import en export;
- statuscontrole;
- eenvoudige operationele controles.

### 4.2 Technisch beheer

Technisch beheer is verantwoordelijk voor:

- installatie en update van de software;
- controle van services, certificates, secrets en permissies;
- HA-configuratie en syncproblemen;
- incidentoplossing en herstel.

### 4.3 Verantwoordelijkheidslijn

- Wijzigingen aan whitelistinhoud horen in de manager, niet direct in het bestand.
- Wijzigingen aan services, secrets of systemd behoren tot technisch beheer.
- Testen van toegestane en geblokkeerde bestemmingen gebeurt na elke relevante wijziging.

## 5. Vereisten

### 5.1 Platform

- Ubuntu Server 24.04 LTS.
- Minimaal 2 nodes voor HA-opstelling.
- Minimaal 2 GB RAM per node.
- Minimaal 20 GB diskruimte per node.

### 5.2 Software

- Python 3.13 met `python3.13-venv`, of een vergelijkbare `python3`-installatie met venv-ondersteuning.
- `python3-pip`.
- `build-essential`.
- Tinyproxy 1.11.1.
- Keepalived.
- Sync v2 systemd unit(s).

### 5.3 Beheervoorwaarden

- SSH-toegang tot beide nodes.
- Rechten om systemd-services te beheren.
- Toegang tot de secrets en configuratiebestanden.
- Beschikbare backupstrategie voor whitelist en secrets.

### 5.4 Netwerk (applicatie-specifiek)

Jullie standaard Ubuntu 24.04 servertemplate (incl. UFW/hardening) is leidend. Hieronder staat alleen wat specifiek is voor deze oplossing: welke stromen je moet toestaan en welke je expliciet beperkt.

Minimaal te borgen stromen:

- **Client -> VIP -> Tinyproxy**: `3128/tcp` inbound naar de VIP, beperkt tot de clientnetwerken die de proxy mogen gebruiken.
- **Beheer -> manager UI (via Nginx)**: `8080/tcp` inbound (HTTPS) beperkt tot beheersegment/VPN/jump host.
- **HTTP redirect (optioneel)**: `80/tcp` inbound als je HTTP->HTTPS redirect gebruikt.
- **Node-to-node**:

  - Keepalived/VRRP tussen de HA-nodes (VRRP gebruikt IP protocol 112).
  - Sync v2 loopt via dezelfde managerpoort (typisch `8080/tcp`), maar peer-only endpoints blijven beperkt tot de peer-node(s) (Nginx `allow/deny` én netwerklaag).

Optioneel:

- **RADIUS**: `1812/udp` outbound van de manager naar de RADIUS-server.

### 5.5 Tijdsynchronisatie

Sync v2 gebruikt timestamps. Zorg daarom dat beide nodes betrouwbare tijdsync hebben (bijvoorbeeld via `chrony`) en dat het tijdverschil tussen nodes binnen de marge blijft.

### 5.6 Operationele randvoorwaarden

- Monitoring en logcollectie volgens jullie standaard (journal/audit/proxy).
- Backups volgens jullie standaard (off-host), passend bij de gewenste RPO/RTO.
- Wijzigingen via jullie change-proces (onderhoudswindow + rollback).

## 6. Installatie en eerste uitrol

### 6.1 Standaardprocedure

1. Plaats de repository op de doelserver, bijvoorbeeld onder `/opt/proxypermit`.
2. Ga naar die map.
3. Start de deployment.

```bash
cd /opt/proxypermit
sudo ./deploy.sh
```

1. Volg de wizard en vul de beheerwaarden in.
1. Controleer de service-status.
1. Open de webinterface en log in.

### 6.2 Wat deployment in hoofdlijnen doet

- installeert dependencies;
- start de setup-wizard als `.env` nog ontbreekt;
- maakt opslag- en state-mappen aan;
- ruimt oude of legacy services op;
- installeert en activeert de manager-service;
- installeert en activeert de sync service of timer;
- genereert sync- en sessiesecrets.

### 6.3 Post-installatiecontrole

Voer na installatie bij voorkeur een pre-productiecontrole uit:

```bash
sudo ./pre-prod-check.sh
```

Gebruik deze check om configuratie, permissies, dependencies en basisstatus te controleren.

Aanvullende acceptatiechecks:

- voer `pre-prod-check.sh` uit op beide nodes;
- verifieer dat de manager UI alleen bereikbaar is vanaf beheersegment;
- verifieer dat de proxy alleen bereikbaar is vanaf de bedoelde clientnetwerken;
- verifieer tijdsync op beide nodes;
- verifieer failover op de VIP (zie 11.5);
- verifieer dat logs zichtbaar zijn in jullie loggingplatform.

## 7. Dagelijks beheer

### 7.1 Standaard werkvolgorde

Gebruik deze volgorde voor reguliere wijzigingen:

1. Log in op de webinterface.
2. Controleer status, synchronisatie en eventuele waarschuwingen.
3. Pas de whitelist aan.
4. Controleer direct de melding of lijstweergave.
5. Bekijk de relevante logs.
6. Test de wijziging met een praktijkvoorbeeld.

### 7.2 Beheerprincipes

- Doe kleine, gerichte wijzigingen.
- Wijzig bij voorkeur buiten piekuren bij risicovolle acties.
- Controleer altijd direct na de wijziging.
- Laat nieuwe bulkimports niet los zonder nacontrole.

### 7.3 Windows-demo

Voor demo of UI-validatie zonder echte proxyfunctionaliteit kun je de webinterface lokaal op Windows starten:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-demo-windows.ps1
```

Optioneel met eigen host en poort:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-demo-windows.ps1 -HostAddress 127.0.0.1 -Port 8080
```

Dit is alleen bedoeld voor demo en invoervalidatie. Het vervangt geen echte Linux-installatie.

## 8. Whitelist beheren

### 8.1 Domeininvoer als standaard

Gebruik domeininvoer als eerste keuze. Dat is de beheerbare en veiligste route voor vrijwel alle gevallen.

Voorbeeld:

- invoer: `github.com`
- resultaat: een regex die exact of volgens de ingestelde wildcardlogica wordt opgeslagen.

### 8.2 Wanneer regex gebruiken

Gebruik regex alleen wanneer domeininvoer niet volstaat, bijvoorbeeld bij uitzonderlijke matchingregels of geavanceerde uitzonderingen.

Beheeradvies:

- controleer regex extra zorgvuldig;
- wijzig complexe regexregio's niet lichtvaardig;
- test altijd met een concrete hostnaam.
- guardrails: alleen ERE (geen PCRE/lookarounds), max 1000 tekens, geen geneste wildcard-repetitie (bijv. (.*)+), geen extreem hoge herhalingen (>999).

### 8.3 Beschrijvingen en comments

Voeg waar mogelijk een korte beschrijving toe:

- waarom de regel bestaat;
- welk systeem of team de regel nodig heeft;
- eventueel een ticket- of wijzigingsreferentie.

Houd comments kort en functioneel. Vermijd privacygevoelige of onnodig gedetailleerde tekst.

### 8.4 Toevoegen

1. Open de whitelistweergave.
2. Kies domein of regex.
3. Vul de regel in.
4. Voeg een beschrijving toe als dat nuttig is.
5. Sla op.
6. Controleer of de regel zichtbaar is.
7. Test de toegang.

### 8.5 Wijzigen

1. Zoek de bestaande regel.
2. Pas alleen aan wat nodig is.
3. Sla op.
4. Controleer direct de uitkomst.
5. Test het effect op verkeer.

### 8.6 Verwijderen

1. Controleer of de regel echt niet meer nodig is.
2. Verwijder de regel.
3. Controleer of de regel weg is.
4. Verifieer dat afhankelijk verkeer nog werkt.

## 9. Import, export en herstel

### 9.1 Import

Import is geschikt voor batchwijzigingen. De server valideert en normaliseert de inhoud voordat die wordt opgeslagen.

Ondersteund gedrag:

- commentregels met `#`;
- inline comments achter een domein of regel;
- validatie en normalisatie van domeinen;
- overslaan van ongeldige regels;
- deduplicatie van dubbele regels.

Werkvolgorde:

1. Maak een bronbestand met een regel per regel.
2. Voeg comments toe waar nuttig.
3. Open import in de webinterface.
4. Controleer de preview.
5. Kies `Toevoegen` of `Vervangen`.
6. Voer de import uit.
7. Controleer resultaat en meldingen.

Gebruik `Vervangen` alleen als je bewust de volledige whitelist wilt overschrijven.

### 9.2 Export

Export gebruik je voor:

- backup van de actuele whitelist;
- review of overdracht;
- vergelijking met een andere omgeving;
- herstel na foutieve wijziging.

### 9.3 Backup en restore

De Makefile bevat directe beheeropties:

```bash
make backup
make restore
```

De backup- en restore-acties zijn bedoeld als snel herstelpad naast de automatische backups van de applicatie.

Aanvullend (backup/restore):

- Richt off-host backups in van minimaal:

  - whitelistbestanden onder `/etc/tinyproxy/`;
  - secrets onder `/etc/proxypermit/` (inclusief `sync-secret.key` en `flask-secret.key`);
  - relevante state onder `/var/lib/proxypermit/`.

- Test periodiek een restore volgens jullie standaard herstelprocedure.

## 10. Configuratie en secrets

### 10.1 Belangrijke `.env`-groepen

De wizard schrijft de belangrijkste variabelen weg in `.env`.

- **Authenticatie**: `ENABLE_AUTH`, `AUTH_USERNAME`, `AUTH_PASSWORD_HASH`, eventueel `AUTH_PASSWORD` als tijdelijke migratiefallback.
- **Loginbeveiliging**: `AUTH_LOGIN_RATE_LIMIT_ENABLED`, `AUTH_LOGIN_MAX_ATTEMPTS`, `AUTH_LOGIN_WINDOW_SECONDS`, `AUTH_LOGIN_LOCKOUT_SECONDS`, `AUTH_LOGIN_DYNAMIC_LOCKOUT_ENABLED`, `AUTH_LOGIN_MAX_LOCKOUT_SECONDS`.
- **Manager runtime**: `MANAGER_SCHEME`, `MANAGER_PORT`, `MANAGER_BIND`, `MANAGER_CERT`, `MANAGER_KEY`.
- **Proxy fix**: `PROXY_FIX_ENABLED`, `PROXY_FIX_X_FOR`, `PROXY_FIX_X_PROTO`, `PROXY_FIX_X_HOST`, `PROXY_FIX_X_PORT`.
- **Gunicorn**: `GUNICORN_BIND`, `GUNICORN_USE_TLS`.
- **HSTS**: `ENABLE_HSTS`, `HSTS_MAX_AGE`, `HSTS_INCLUDE_SUBDOMAINS`, `HSTS_PRELOAD`.
- **Sync**: `SYNC_SCHEME`, `SYNC_PORT`, `SYNC_VERIFY_CERT`, `ALLOW_LOCALHOST_SYNC`, `SYNC_MASTER_HOST`, `SYNC_MASTER_PREFERENCE`, `SYNC_MAX_WHITELIST_SIZE`.
- **Logging**: `AUDIT_LOG_PATH`, `AUDIT_LOG_MAX_BYTES`, `AUDIT_LOG_BACKUP_COUNT`, `LOG_IP_MODE`, `CLIENT_IP_MAP_PATH`, `CLIENT_IP_MAP_JSON`, `AUDIT_ENTRY_MODE`, `AUDIT_ENTRY_MAX_LEN`.
- **Logo**: `MANAGER_LOGO_URL`, `MANAGER_LOGO_ALT`.

Volledige variabelenlijst met defaults en toelichting: [ENVIRONMENT.md](ENVIRONMENT.md).

### 10.2 Certificaten

Voor productie is een geldig certificaat de norm. Gebruik self-signed certificaten alleen in testomgevingen.

TLS-terminatie in productie:

- Aanbevolen: TLS via reverse proxy (Nginx), met de manager-app daarachter.
- Kies één TLS-model:

  - Nginx doet TLS en proxy't naar Gunicorn.
  - Of Gunicorn doet zelf TLS (minder gebruikelijk in DC/ISP omgevingen).

Let op bij Nginx op poort `8080`: configureer Gunicorn niet op dezelfde TCP-poort. Gebruik bij voorkeur een Unix socket, bijvoorbeeld via:

```env
GUNICORN_BIND=unix:/run/proxypermit/gunicorn.sock
```

### 10.3 Secrets

- `sync-secret.key` moet identiek zijn op beide nodes.
- `flask-secret.key` moet identiek zijn op beide nodes.
- Secrets mogen niet in code of documentatie met echte waarden terechtkomen.

Aanvullend:

- File permissies/ownership volgens jullie standaard.
- Rotatie is een geplande wijziging:

  - rotate `sync-secret.key` alleen met een gecontroleerde procedure, omdat nodes identiek moeten blijven;
  - rotate `flask-secret.key` bewust: dit kan bestaande sessies ongeldig maken.
- Bewaar secrets niet in tickets, chatlogs of exports.

### 10.4 Aanmaken van een hash

Genereer een veilige wachtwoordhash met:

```bash
python3 scripts/tools/generate_auth_hash.py
```

### 10.5 RADIUS

RADIUS-ondersteuning is optioneel en experimenteel. Gebruik dit alleen na eigen validatie in een testomgeving voordat je dit in productie inzet.

Belangrijk uitgangspunt in deze implementatie:

- RADIUS is bedoeld om het **wachtwoord** van de beheerlogin centraal te valideren.
- De **gebruikersnaam blijft de lokaal ingestelde** `AUTH_USERNAME` (het is dus geen multi-user IAM in de manager).

#### 10.5.1 Wanneer gebruik je RADIUS

RADIUS is nuttig wanneer je:

- het beheerwachtwoord centraal wilt laten valideren door een bestaand identity platform;
- local credentials als “break-glass” wilt behouden (optioneel via fallback);
- audit en beheerlogica in de manager wilt behouden, maar de password-check extern wilt doen.

#### 10.5.2 Voorwaarden

- De manager draait met `ENABLE_AUTH=true`.
- De Python dependency `pyrad` is geïnstalleerd (staat in [requirements/base.txt](requirements/base.txt)).
- UDP-connectiviteit van de manager naar de RADIUS-server (standaard poort `1812/udp`).
- Een shared secret voor RADIUS (bewaar dit als secret, niet in documentatie).

Let op: RADIUS-auth gebeurt via een RADIUS Access-Request met PAP-achtige password encryptie (via `pyrad`). Zorg daarom dat dit verkeer alleen over een **vertrouwd beheersegment** loopt.

#### 10.5.3 Configuratie (`.env`)

Minimale set:

```env
ENABLE_AUTH=true
AUTH_USERNAME=admin

ENABLE_RADIUS=true
RADIUS_SERVER=auth.example.com
RADIUS_PORT=1812
RADIUS_SECRET=... 
RADIUS_TIMEOUT=5

# Aanbevolen: break-glass mogelijkheid
RADIUS_ALLOW_LOCAL_FALLBACK=true

# Break-glass (aanbevolen) via hash
AUTH_PASSWORD_HASH=...
```

Toelichting:

- `ENABLE_RADIUS` wordt pas actief als `pyrad` ook daadwerkelijk beschikbaar is in de runtime-omgeving.
- Als `ENABLE_RADIUS=true` en `RADIUS_SERVER`/`RADIUS_SECRET` ontbreken, zal de manager bij startup stoppen met een configuratiefout.

#### 10.5.4 Gedrag (beslissingslogica)

Bij een loginpoging:

1. De manager accepteert alleen de gebruikersnaam die exact gelijk is aan `AUTH_USERNAME`.
2. Als RADIUS actief is, wordt eerst RADIUS-auth geprobeerd.
3. Bij **Access-Accept**: login slaagt.
4. Bij **Access-Reject**: login faalt (er is geen fallback op “verkeerd wachtwoord”).
5. Bij **communicatiefout/timeout**:

     - Als `RADIUS_ALLOW_LOCAL_FALLBACK=true`: de manager probeert daarna local auth (`AUTH_PASSWORD_HASH` of legacy `AUTH_PASSWORD`).
     - Als `RADIUS_ALLOW_LOCAL_FALLBACK=false`: login faalt.

#### 10.5.5 Testen (beheercheck)

1. Controleer dat de service draait:

```bash
systemctl status proxypermit --no-pager
```

1. Doe een loginpoging met de beheerusername.
1. Bekijk de logs voor RADIUS-signalen:

```bash
journalctl -u proxypermit -n 200 --no-pager
```

Indicaties die je kunt zien:

- RADIUS success / reject (op basis van response).
- RADIUS communication error (timeout/verbinding).
- “fallback op local auth” wanneer fallback actief is en RADIUS onbereikbaar is.

#### 10.5.6 Veelvoorkomende problemen

- **RADIUS lijkt ingeschakeld maar er gebeurt niets**

  - Check of `pyrad` echt in de gebruikte Python/venv zit (de service draait via de venv uit deploy).
  - Controleer de serviceconfig en `make venv`/deploy.

- **Service start niet met ENABLE_RADIUS=true**

  - Controleer of `RADIUS_SERVER` en `RADIUS_SECRET` gezet zijn.
  - Bekijk `systemctl status proxypermit` en `journalctl -u proxypermit` voor de foutmelding.

- **Timeouts / intermittente loginproblemen**

  - Controleer UDP 1812 routing/firewall.
  - Verhoog `RADIUS_TIMEOUT` voorzichtig als het netwerk traag is.
  - Overweeg tijdelijke fallback (`RADIUS_ALLOW_LOCAL_FALLBACK=true`) als beheerders toegang moeten houden tijdens onderhoud.

#### 10.5.7 Beheeradvies

- Gebruik in productie `AUTH_PASSWORD_HASH` als fallback (geen plaintext).
- Behandel `RADIUS_SECRET` als een echt secret: minimale leesrechten, niet delen in tickets/exports.
- Laat fallback alleen aan staan als je een bewuste break-glass strategie wilt; documenteer intern wanneer je fallback wel/niet mag gebruiken.
- Zet monitoring/alerting op RADIUS errors zodat fallback geen stille “normale modus” wordt.
- RADIUS is voor UI-login; sync gebruikt altijd de sync-secret (dit staat los van RADIUS).

## 11. High availability en synchronisatie

### 11.1 HA-model

De opstelling werkt met een actieve `MASTER` en een standby `BACKUP`.

- Keepalived beheert de VIP.
- De VIP volgt de actieve node.
- Bij uitval verhuist de VIP naar de standby node.

### 11.2 Sync v2

Sync v2 verzorgt veilige uitwisseling van whitelistdata tussen beide nodes. Beveiligingsmaatregelen zijn onder andere:

- HMAC-SHA256-authenticatie;
- timestampcontrole;
- noncebescherming;
- rate limiting;
- IP-allowlist.

### 11.3 Beheerregels voor HA

- Voer wijzigingen bij voorkeur vanuit een centrale beheerplek uit.
- Controleer na elke wijziging de syncstatus.
- Los conflictstatussen eerst op.
- Gebruik de sync-logs als primaire bron bij problemen.

### 11.4 Beheertargets voor sync

```bash
make sync-generate-secret
make sync-verify-secret
make sync-install
make sync-status
make sync-logs
make sync-logs-follow
make sync-test
make sync-state
```

### 11.5 Periodieke failover-test (productie)

Test failover periodiek en na relevante changes (keepalived config, netwerk, kernel updates):

1. Plan een onderhoudsvenster.
1. Verifieer dat sync geen conflictstatus heeft.
1. Forceer failover door Keepalived op de actieve node tijdelijk te stoppen.
1. Controleer dat de VIP verhuist naar de standby node.
1. Test:

     - proxyfunctionaliteit via de VIP;
     - manager UI (alleen vanaf beheersegment);
     - syncstatus en logs.

1. Start Keepalived weer op en verifieer de gewenste eindstatus (MASTER/BACKUP).

Acceptatiecriteria:

- VIP verhuist binnen de afgesproken tijd.
- Tinyproxy blijft bereikbaar via VIP.
- Manager UI blijft bereikbaar vanaf beheersegment.
- Sync v2 blijft functioneren zonder conflicts.

## 12. Monitoring en logging

### 12.1 Gezondheidscontrole

Gebruik minimaal deze checks:

```bash
systemctl is-active tinyproxy
systemctl is-active keepalived
ip addr show | grep <vip>
```

### 12.2 Managerstatus

```bash
systemctl status proxypermit
journalctl -u proxypermit -n 200 --no-pager
```

### 12.3 Proxylog

```bash
sudo tail -f /var/log/tinyproxy/tinyproxy.log
```

### 12.4 Auditlog

```bash
sudo tail -f /var/log/proxypermit/audit.log
```

### 12.5 Synclog

```bash
sudo journalctl -u whitelist-sync-v2 -n 50 --no-pager
```

### 12.6 Waar je op let

- herhaalde authenticatiefouten;
- importfouten;
- syncconflicten;
- onverwachte service-uitval;
- ontbrekende of ongeldige secrets;
- fouten na configwijzigingen of certificaatwissels.

### 12.7 Alerting, retentie en centrale logging (productie)

- Alert op:

  - `tinyproxy`, `keepalived` of `proxypermit` niet actief;
  - VIP ontbreekt op de verwachte node;
  - syncconflict of herhaaldelijke sync errors;
  - spikes in login failures of lockouts.

- Leg retentie vast voor:

  - proxylogs (kunnen operationeel gevoelig zijn);
  - auditlog;
  - journal logs voor manager/sync.

- Gebruik logrotate waar nodig en ship logs naar jullie loggingplatform volgens standaard.

## 13. Beveiliging en privacy

### 13.1 Beveiliging

- Gebruik bij voorkeur `AUTH_PASSWORD_HASH`.
- Laat `ENABLE_AUTH` in productie aan.
- Beperk toegang tot secrets en statebestanden.
- Gebruik HMAC voor syncverkeer.
- Laat de sync-IP-allowlist kloppen met de echte node-adressen.
- Gebruik alleen TLS wanneer certificaten correct zijn ingericht.

Aanvullend (applicatie-specifiek):

- Voorkom open proxy:

  - beperk toegang tot `3128/tcp` strikt tot de bedoelde clientnetwerken;
  - verifieer dit ook op de VIP/firewalllaag.

- Beperk beheeroppervlak:

  - exposeer de manager UI alleen via beheersegment/VPN;
  - bescherm peer-only sync endpoints met IP-allowlists in Nginx én op firewallniveau.
  - bind Gunicorn bij voorkeur aan een Unix socket of localhost, niet aan een publiek bereikbare interface.

### 13.2 Privacy

- Log geen persoonlijke data als dat niet nodig is.
- Houd comments en beschrijvingen functioneel.
- Gebruik auditlogs alleen voor beheer- en technische gebeurtenissen.
- Bescherm sessies en secrets als beheerinformatie.

Aanvullend:

- Beoordeel of proxylogs IP-adressen of URL's bevatten in jullie configuratie en stem retentie en toegang daarop af.

## 14. Toegevoegde firewallregels

Jullie standaard Ubuntu 24.04 servertemplate (incl. UFW/hardening) is leidend. Dit hoofdstuk noemt alleen de regels die deze oplossing extra nodig heeft, met comments zodat direct duidelijk is waarvoor elke regel dient.

### 14.1 Te beheren stromen

De standaard is `deny incoming` en `deny outgoing`. Alles wat nodig is voor deze oplossing wordt daarna expliciet toegestaan of met comments gedocumenteerd in de UFW-regels.

- `3128/tcp` voor Tinyproxy-verkeer vanuit de toegestane clientnetwerken.
- `8080/tcp` voor de beheerinterface en de HTTPS-frontend van de manager.
- `80/tcp` alleen als HTTP-naar-HTTPS-redirect via Nginx gewenst of vereist is.
- protocol 112 (VRRP) voor Keepalived, als jullie firewall dit verkeer filtert.

### 14.2 Voorbeeld met comments

Pas de voorbeeldregels aan op jullie netwerksegmenten en werk met allowlists in plaats van brede openingen:

```bash

# Tinyproxy alleen voor clientnetwerken
sudo ufw allow from <client-netwerk> to any port 3128 proto tcp comment 'Tinyproxy client traffic'

# Manager UI via HTTPS alleen voor beheersegment/VPN
sudo ufw allow from <beheersegment> to any port 8080 proto tcp comment 'Manager UI over HTTPS'

# Alleen gebruiken als HTTP-redirect ook bereikbaar moet zijn
sudo ufw allow from <beheersegment> to any port 80 proto tcp comment 'HTTP to HTTPS redirect'

# Tinyproxy server zelf mag uitgaand webverkeer doen
sudo ufw allow out to any port 80 proto tcp comment 'Tinyproxy outbound HTTP'
sudo ufw allow out to any port 443 proto tcp comment 'Tinyproxy outbound HTTPS'

# Voeg alleen toe als de server deze koppelingen echt nodig heeft
# sudo ufw allow out to any port 53 proto udp comment 'DNS queries'
# sudo ufw allow out to any port 123 proto udp comment 'Time sync (NTP)'

# Keepalived/VRRP tussen de HA-nodes
# Sta protocol 112 (VRRP) toe volgens de syntax van jullie firewall.
```

Controleer na wijziging altijd of de regels in lijn zijn met de Nginx-allowlists en met de poorten die de service daadwerkelijk gebruikt.

## 15. Troubleshooting

### 15.1 Proxy werkt niet

Controleer:

```bash
sudo systemctl status tinyproxy
sudo tail -f /var/log/tinyproxy/tinyproxy.log
curl -x http://<vip>:3128 https://example.com
```

### 15.2 Manager is niet bereikbaar

Controleer:

```bash
systemctl status proxypermit
journalctl -u proxypermit -n 200 --no-pager
```

### 15.3 Failover werkt niet

Controleer:

```bash
sudo systemctl status keepalived
sudo journalctl -u keepalived -n 100 --no-pager
ip addr show | grep <vip>
```

### 15.4 Sync faalt

Controleer:

```bash
sudo systemctl status whitelist-sync-v2.timer --no-pager
sudo journalctl -u whitelist-sync-v2 -n 50 --no-pager
```

Let vooral op:

- mismatch in secrets;
- verkeerde node-adressen;
- certificaatproblemen;
- foutieve `.env`-waarden;
- firewall- of ACL-problemen.

### 15.5 Windows-demo werkt niet

Controleer of:

- PowerShell-uitvoering is toegestaan;
- de demo-scriptpaden bestaan;
- de Python-omgeving beschikbaar is;
- er geen poortconflict is op de gekozen poort.

## 16. Onderhoud en wijzigingsbeheer

### 16.1 Standaard wijzigingsprincipe

Voer kleine wijzigingen door, test direct, en behoud een herstelpad via backup of export.

### 16.2 Waar je nooit lichtzinnig mee omgaat

- secrets vervangen;
- sync-secret opnieuw genereren;
- whitelist vervangen in bulk;
- certificaten wisselen;
- Nginx-migratie activeren;
- ACL's of IP-allowlists wijzigen.

### 16.3 Nuttige beheercommando's

```bash
make help
make setup
make deploy
make venv
make auth-hash
make install-logrotate
make backup
make restore
make prod-check
make start
make stop
make restart
make status
make logs
```

### 16.4 Nginx-migratie

Gebruik onderstaande targets wanneer Nginx als TLS-terminator wordt ingezet:

```bash
make nginx-env-8080
make nginx-render-config
make nginx-install-config
make nginx-reload
make nginx-migrate-8080
make nginx-uninstall-config
```

### 16.5 Logrotate

Als je logrotatie wilt inrichten voor manager- en synclogs:

```bash
make install-logrotate
```

### 16.6 Hotfix/redeploy (indien nodig)

In de praktijk worden geen reguliere feature-updates verwacht. Dit hoofdstuk is bedoeld als korte noodprocedure voor situaties zoals:

- security hotfix;
- herinstallatie na rebuild;
- herstel na een mislukte wijziging;
- gecontroleerde wijziging aan Nginx/certificaten of sync-inrichting.

Volg jullie change-proces en voer de wijziging gecontroleerd uit:

1. Maak een export of backup van de whitelist.
1. Maak een off-host backup van secrets en state.
1. Pas de wijziging eerst toe op de standby node en verifieer minimaal: deploy/herstart wat nodig is; service status en basisfunctionaliteit; sync status.

1. Failover naar de bijgewerkte node.
1. Pas dezelfde wijziging toe op de andere node.

Rollbackprincipe:

- Als de manager niet start of sync errors blijven optreden: rollback naar de vorige release en herstel whitelist/secrets/state vanuit backups.

## 17. Belangrijke paden en commando's

### 17.1 Belangrijke paden

- `/opt/proxypermit/` - applicatie, scripts en servicebestanden.
- `/etc/tinyproxy/` - Tinyproxy-configuratie en whitelist.
- `/etc/proxypermit/` - sync- en Flask-secrets.
- `/var/lib/proxypermit/` - sync state, manual sync state en session state.
- `/var/log/` - proxy-, sync- en auditlogs.

### 17.2 Commandoreferentie

- `make setup` - setup-wizard starten.
- `make deploy` - volledige uitrol.
- `make install` - systemd service installeren.
- `make uninstall` - manager, sync units en logrotate verwijderen.
- `make sync-install` - sync v2 installeren.
- `make sync-status` - syncstatus bekijken.
- `make backup` - whitelist backup maken.
- `make restore` - whitelist backup terugzetten.
- `make prod-check` - pre-productiecontrole.
- `make nginx-migrate-8080` - Nginx-migratie op poort 8080.

## 18. Bijlagen

### 18.1 Wijzigingschecklist

Gebruik deze checklist als compacte samenvatting van het wijzigingsproces uit hoofdstuk 15:

1. Maak een export of backup van de whitelist.
1. Maak een off-host backup van secrets en state.
1. Pas de wijziging eerst toe op de standby node en controleer service status, basisfunctionaliteit en sync.
1. Voer failover uit naar de bijgewerkte node.
1. Pas dezelfde wijziging toe op de andere node.
1. Documenteer de wijziging en bewaak logs na afloop.

## Slot

Deze handleiding is bedoeld als hoofdroute voor beheer. Houd het document actueel wanneer processen, paden, targets of beveiligingsinstellingen veranderen.
