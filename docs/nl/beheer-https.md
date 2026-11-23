# HTTPS voor de beheerinterface

[English](../en/management-https.md) · [Beheerdershandleiding](beheer.md)

!!! warning "Functie in de prerelease"

    Deze optionele werkwijze is beschikbaar om te testen in **v1.4.0-dev.1** en
    zit niet in de stabiele v1.3.0. Gebruik een tijdelijke of herstelbare
    testomgeving. HTTPS is nog niet standaard bij een nieuwe installatie.

Gunicorn en Granian kunnen de beheerinterface rechtstreeks via HTTPS aanbieden.
Een reverse proxy is optioneel. De beheercertificaten staan los van de interne
HA-certificaten. Vernieuwen verandert de HA-vingerafdrukken niet en herstart
Tinyproxy en Keepalived niet.

## Via de webinterface

Open **Instellingen → HTTPS voor beheer** met een account met de rol
**Systeembeheerder**. Proxybeheerders en monitors kunnen geen HTTPS-taken
starten of de CA via de beheer-API downloaden. Een bestaande installatie heeft
eerst één ondersteunde applicatie-update nodig om de beperkte systeemhelper
te installeren.

- Kies op een HTTP-node **HTTPS voorbereiden** en bevestig je huidige wachtwoord
  en, als 2FA aanstaat, een verse authenticatorcode. Voorbereiden herstart de
  interface niet. De eerste inrichting via HTTP mag alleen door een lokale
  SSH-tunnel; een externe HTTP-verbinding kan deze acties niet uitvoeren.
- Download de openbare node-CA, controleer de getoonde vingerafdruk en vertrouw
  de CA volgens het beleid van je organisatie. Vink **Ik heb deze node-CA
  gecontroleerd en vertrouwd** aan, kies **HTTPS activeren** en bevestig opnieuw
  je identiteit. Hetzelfde adres en dezelfde poort schakelen over naar HTTPS.
  Tijdens de herstart toont de pagina een link naar het beveiligde beheeradres.
  Ontvang je geen antwoord op de activering, controleer dan eerst dat beveiligde
  adres voordat je opnieuw indient. De interface herhaalt de aanvraag niet automatisch.
- Kies op een beheerde HTTPS-node **Certificaat vernieuwen** en bevestig je
  identiteit. De bestaande CA blijft behouden. De dagelijkse automatische
  vernieuwing blijft ingeschakeld.

Het paneel toont de doelnode en de door het OS goedgekeurde beheernamen. De
browser kan geen bestandspaden kiezen, rootcommando’s uitvoeren of certificaten
voor willekeurige domeinen laten uitgeven. Extra DNS-namen vereisen goedkeuring
door de OS-beheerder. Een verlopen plan, gewijzigde configuratie of ingetrokken
beheerdersrol wordt geweigerd. Wachtwoorden en authenticatorcodes komen nooit
in de taakbestanden terecht.

Controleer via het VIP eerst de doelnode. HTTPS-beheer blijft lokaal op die
node; certificaten worden niet naar de peer gekopieerd. Na een overname kan
opnieuw inloggen nodig zijn. Certificaten van een externe uitgever blijven de
vernieuwingsprocedure van die uitgever gebruiken.

## OS-herstelroute: HTTPS inschakelen

Houd een SSH-sessie open voor herstel en controleer de nodeklok. Kies de echte
DNS-naam en het beheeradres van deze node:

```bash
cd /opt/proxypermit/current
sudo .venv/bin/python -m scripts.deploy.management_tls enable \
  --dns proxy.example.internal --ip 192.0.2.11
```

Zonder `--apply` verschijnt alleen een plan. Controleer de namen en het
luisteradres en herhaal de opdracht met `--apply`. Gebruik bij een afwijkende
installatiemap ook `--install-dir`. Herhaal dit op iedere node met de eigen
adressen. Het bestaande luisteradres, de poort en de firewall blijven behouden.
Een bestaande TLS-configuratie met een zelf aangeleverd certificaat wordt niet
overschreven.

De opdracht maakt een unieke lokale certificaatautoriteit (CA), met een sleutel
die alleen root kan lezen, en een servercertificaat dat 90 dagen geldig is.
Bij activering en vernieuwing herstart de beheerinterface kort. Instellingen
toont de vervaldatum en vingerafdruk en waarschuwt in de laatste 30 dagen.

## De node-CA vertrouwen

Haal het **openbare** `/etc/proxypermit/management-tls/ca.pem` op via vertrouwde
SSH. Vergelijk de SHA-256-vingerafdruk via dat vertrouwde kanaal met de uitvoer
van de opdracht. Vertrouw de CA vervolgens in je browser of beheerclient volgens
het beleid van je organisatie. Kopieer `ca-key.pem` en bestanden met de naam
`key.pem` nooit naar clients. Iedere node heeft een eigen CA.

Open `https://` met het bestaande adres en de poort. SSH-tunnels blijven werken;
op de lokale tunnelpoort gebruik je nu HTTPS. `localhost`, `127.0.0.1` en `::1`
zijn opgenomen voor lokale controles. Een browserwaarschuwing voordat je de CA
bewust vertrouwt, is te verwachten: dit is geen publiek vertrouwde CA.

## Vernieuwen en herstellen

De dagelijkse timer vernieuwt wanneer er nog 30 dagen of minder over zijn.
Ook een al verlopen servercertificaat kan worden vervangen. De serversleutel
verandert; de vertrouwde CA blijft gelijk.

```bash
sudo .venv/bin/python -m scripts.deploy.management_tls status
sudo systemctl list-timers proxypermit-management-tls-renew.timer
sudo systemctl start proxypermit-management-tls-renew.service
```

Gebruik `renew --force --apply` om eerder te vernieuwen voor een test. De CA is
tien jaar geldig. Kan deze de looptijd van een nieuw servercertificaat niet meer
dekken, dan is een bewuste vertrouwensovergang nodig. De vertrouwde CA wordt
nooit stilzwijgend vervangen.

Activering controleert het certificaat met de sleutel en de echte HTTPS-interface,
met CA-validatie en de verwachte vingerafdruk. Bij een mislukte controle wordt de
vorige beheerconfiguratie teruggezet. Een blijvende herstelregistratie en een
controle bij opstarten herstellen een onderbroken activering. Een al bevestigde
activering wordt afgemaakt. Zijn ondertussen buiten deze procedure wijzigingen
aan het omgevingsbestand gedaan, dan worden die niet overschreven en is
controle door de OS-beheerder nodig.

```bash
sudo .venv/bin/python -m scripts.deploy.management_tls recover --apply
sudo journalctl --namespace=proxypermit \
  -u proxypermit-management-tls-renew.service --since today
```

Applicatie-updates behouden TLS. Terugdraaien behoudt de huidige certificaatkoppeling
en weigert releases van vóór deze werkwijze. Verwijderen ruimt overeenkomende
eigen vernieuwings- en herstelunits op en bewaart certificaten in `/etc/proxypermit`.

## HSTS en eigen certificaten

Deze werkwijze met een lokale CA zet HSTS aanvankelijk uit. Zodra clients de CA
vertrouwen en herstel via HTTPS is gecontroleerd, kan de OS-beheerder bewust
`ENABLE_HSTS=true` en een passende `HSTS_MAX_AGE` instellen en de beheerinterface
herstarten. HSTS wordt alleen via HTTPS meegestuurd en kan browserherstel naar
HTTP op dezelfde hostnaam blokkeren. Subdomein- en preloadopties worden niet aangezet.

Eigen certificaten blijven mogelijk via `MANAGER_CERT` en `MANAGER_KEY`.
De vernieuwingsprocedure van de uitgever blijft daarvoor verantwoordelijk.
Granian vereist een PKCS#8-sleutel. Begeleide import, CA-vervanging en ACME
staan nog op de roadmap.
