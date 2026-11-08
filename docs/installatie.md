# ProxyPermit installeren

[English](en/installation.md) · [Documentatie](README.md)

## Nieuwe installatie op Debian of Ubuntu

Gebruik systemd en Python 3.10 of hoger. Voer de opdrachten uit vanuit de
hoofdmap van de broncode.

```bash
bash install.sh                            # Open de interactieve wizard
sudo bash install.sh                       # Wizard met installatierechten
bash install.sh --plan                     # Bekijk het standaardplan als JSON
sudo bash install.sh --apply
```

Bij een nieuwe installatie vraagt het installatieprogramma om een
beheerderswachtwoord. Het installeert de benodigde pakketten, bouwt een release
in een virtuele Python-omgeving en voert de tests uit. Een zelfstandige node
gebruikt een eigen serviceaccount met een sudoers-regel voor het herstarten
van Tinyproxy.

Bij een nieuwe zelfstandige installatie luisteren de beheerconsole en proxy
op loopback. Open de beheerconsole via een SSH-tunnel.

```bash
ssh -L 8080:127.0.0.1:8080 gebruiker@server
# Open http://127.0.0.1:8080 en log in.
```

Stel `Listen` en `Allow`/`Deny` in voor LAN-clients. Gebruik voor gedeeld
webbeheer een reverse proxy met TLS. De Nginx-voorbeelden staan in
`deploy/nginx/`. Zet `PROXY_FIX_ENABLED=true` alleen achter een vertrouwde proxy.
Een bestaande, door de beheerder ingerichte Tinyproxy-configuratie wordt behouden.

Voor installatie zonder interactie kan `--admin-password-file /pad/naar/bestand` worden
gebruikt. Beperk de rechten van dat bestand; wachtwoorden worden niet als
commandoregelargument doorgegeven. Het standaard releasepad is
`/opt/proxypermit/releases/`, met `/opt/proxypermit/current` als actieve verwijzing.

## Persoonlijke beheerdersaccounts en optionele 2FA

Een nieuwe installatie maakt een persoonlijk beheeraccount aan. Onder
**Instellingen** kun je accounts toevoegen of verwijderen, je wachtwoord wijzigen
en optionele 2FA voor je eigen account instellen. Alle lokale accounts zijn
beheerders. Voor wijzigingen bevestig je je wachtwoord en, als die actief is,
een nieuwe authenticatorcode. De terminal blijft beschikbaar voor herstel.
Gebruik HTTPS voor beheer op afstand. Lokaal beheer via loopback kan via HTTP.

```bash
cd /opt/proxypermit/current
sudo .venv/bin/python scripts/tools/accounts.py set alice --totp
sudo .venv/bin/python scripts/tools/accounts.py set bob
sudo .venv/bin/python scripts/tools/accounts.py list
```

Bij verlies van de authenticator kan de serverbeheerder een nieuwe sleutel
registreren met `set GEBRUIKER --totp`. Gebruik `--no-totp` om 2FA voor dat
account uit te schakelen. Een accountwijziging trekt de bestaande sessies voor
dat account in. Bij een bestaande installatie kun je eerst accounts aanmaken en
daarna `AUTH_ACCOUNTS_FILE=/var/lib/proxypermit/accounts.json` instellen en ProxyPermit
herstarten. Dit kiest lokale accounts in plaats van de oude enkele login of RADIUS.

Op HA-nodes registreer je accounts en authenticatoren per node. Log op elke node
afzonderlijk in. De lokale JSON-auditlog legt bij wijzigingen de accountnaam en
node vast. Gebruik een besloten terminal en deel de authenticatorsleutel alleen
met de eigenaar van het account.

## Upgrade, migratie en herstel

```bash
bash install.sh --plan                  # detectie en migratieplan, zonder wijzigingen
sudo bash install.sh --apply            # upgrade met back-ups
bash install.sh --rollback BACKUPNAAM   # herstelplan
sudo bash install.sh --rollback BACKUPNAAM --apply
```

De installer zoekt eerst `/etc/proxypermit/.env`, daarna de `.env` in het
installatiepad en de bronmap. Bestaande instellingen, wachtwoordhashes, secrets,
filterregels en Tinyproxy-configuratie worden behouden. Een bestaande
Nginx/Unix-socket- of TLS-binding wordt bij upgrade niet overschreven.
De installer voegt expliciete migratie-instellingen toe aan de bestaande `.env`.

Backups bevatten een herstelmanifest onder `/opt/proxypermit/backups/`.
Bij een mislukte installatie worden gevolgde bestanden en de vorige release
hersteld. Pakketten, nieuw aangemaakte systeemaccounts en lege directories blijven
bestaan. Onderbreking door stroomuitval vereist handmatig herstel met het manifest.
De herstelback-ups bevatten geheime sleutels en zijn alleen voor root toegankelijk.

## Nieuw HA-cluster met twee nodes

Gebruik twee schone Debian- of Ubuntu-hosts met systemd, vaste IPv4-adressen op
hetzelfde netwerk en een vrij virtueel IP-adres. De hosts moeten unicast VRRP
(IP-protocol 112) onderling kunnen uitwisselen. Sta TCP 3128 toe vanaf het
gekozen clientnetwerk en kies een VRRP-ID die op dat netwerk vrij is.

Kies in `sudo bash install.sh` voor **Two-node HA**. De wizard vraagt de
clustergegevens uit, toont een samenvatting en wacht op bevestiging. Gebruik
de pijltjestoetsen of cijfers in een geschikte terminal. In eenvoudige
terminals werkt de genummerde invoer. Stoppen is de standaard bij het
bevestigingsscherm.

Voer dit op beide hosts uit. Gebruik op de tweede host `node=b` en vul per host
de eigen netwerkinterface in. Vervang de voorbeeldadressen door je eigen adressen.

```bash
node=a
iface=ens18
cluster=(
  --mode ha --ha-node "$node" --ha-interface "$iface"
  --ha-node-a 192.0.2.11 --ha-node-b 192.0.2.12
  --ha-vip 192.0.2.10/24 --ha-client-cidr 192.0.2.0/24 --ha-vrid 51
)
bash install.sh "${cluster[@]}"               # Alleen het plan
sudo bash install.sh "${cluster[@]}" --apply  # Installeer deze node
```

Clients gebruiken het virtuele IP op poort 3128. Keepalived bewaakt de lokale
Tinyproxy-poort. Node A heeft voorrang en node B neemt over wanneer A uitvalt.
De beheerconsole luistert per node op localhost. Gebruik per host een SSH-tunnel
zoals bij een zelfstandige installatie. Voeg vóór ingebruikname op beide nodes dezelfde
bestemmingen toe. De installer begint met een lege allowlist en ProxyPermit
publiceert wijzigingen lokaal per node.

Bij upgrades van bestaande clusters blijven de bestaande Keepalived- en
sync-instellingen behouden. De installer weigert een wijziging van topologie
tijdens een upgrade.

Voor bestaande clusters met legacy-whitelistsync gebruiken de peers standaard
HTTPS met certificaatcontrole. Gebruik peeradressen die op het certificaat
staan en stel `SYNC_SCHEME=https`, `SYNC_VERIFY_CERT=true` en zo nodig
`SYNC_PORT` in. Bij TLS-terminatie
moet ProxyFix uitsluitend via de vertrouwde reverse proxy bereikbaar zijn.
`SYNC_ALLOW_INSECURE_TRANSPORT=true` maakt een tijdelijke uitzondering mogelijk
voor oude omgevingen. Nieuw ingerichte HA-clusters beheren beleid per node.

## Opslaan en activeren

Een opgeslagen concept staat apart van de bestanden die Tinyproxy leest.
Publiceren maakt een herstelpunt en past proxyconfiguratie en eventuele
filterwijzigingen samen toe. Bij mislukte
activatie worden die configuratie en eventuele native filterwijzigingen
teruggezet, waarna Tinyproxy opnieuw wordt gestart. Als ook herstel faalt,
meldt de interface dat de service op de server moet worden gecontroleerd.

Een herstart kan verbindingen onderbreken. Gelijktijdig gewijzigde bestanden geven
een conflict in plaats van stilzwijgend overschrijven. Tijdens een openstaande
configuratiewijziging wordt schrijven via de oude domein-editor geblokkeerd.
De bestaande HA-sync en oudere bewerkingsroutes gebruiken nog niet overal
hetzelfde versie- en transactiemodel; coördineer HA-onderhoud per node.

## Webserver kiezen

Gunicorn blijft de standaard en is bijgewerkt naar 26.2.0. Granian 2.8.7 is
beschikbaar als optionele Rust HTTP-server voor dezelfde Python/WSGI-app:

```bash
bash install.sh --server granian        # bekijk eerst het plan
sudo bash install.sh --server granian --apply
```

Voor lokaal gebruik: installeer `requirements/granian.txt` en start
`python -m app.runtime.server --backend granian`. Met `--check` toon je alleen de opdracht.
`APP_BIND` en `APP_WORKERS` regelen luisteradres en workers. Upgrades behouden
de eerdere `APP_SERVER` tenzij je expliciet een andere server kiest.
Directe TLS via Gunicorn blijft beschikbaar; Granian gebruikt in deze integratie
een TLS-reverse-proxy. Zie [architectuur en HA-keuzes](architecture.md).

Een echte HTTP-test met tijdelijke configuratie kan met
`python scripts/dev/check_server.py --backend granian` of `--backend gunicorn`.
