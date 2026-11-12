# Logging, privacy en bewaartermijnen

[English](../en/logging.md) · [Documentatie](README.md)

## Wat wordt bewaard?

Beheerde installaties gebruiken onderstaande standaardwaarden. De termijn is
een maximale bewaarperiode, geen garantie dat elke regel zo lang beschikbaar
blijft. Een schijflimiet kan oudere gegevens eerder verwijderen.

| Bron | Termijn | Opslagbegrenzing |
| --- | --- | --- |
| Audit van beheer en aanmeldingen | 30 dagen | Actief bestand van 2 MiB en vijf genummerde archieven: maximaal 12 MiB aan loginhoud |
| Diagnostiek van ProxyPermit en beheerde Keepalived | 30 dagen | Eigen journalnamespace `proxypermit`, budget van 100 MiB |
| Tinyproxy-diagnostiek en optionele verkeerslogs | 7 dagen | Eigen journalnamespace `proxypermit-traffic`, budget van 32 MiB |

Auditopruiming controleert de tijdstempels van records in het actieve bestand
en alle genummerde archieven. Dit gebeurt ieder uur en bij schrijven wanneer
opruiming nodig is. Ook een stil actief bestand blijft dus niet onbeperkt
persoonsgegevens bewaren. Beschadigde records zonder betrouwbare tijdstempel
worden bij onderhoud verwijderd; alleen het aantal wordt gemeld. Rotatie,
opruiming en het lezen van archieven gebruiken dezelfde procesveilige lock.
Te grote bestanden en overtollige archieven worden teruggebracht tot de
opslaglimieten, met behoud van de nieuwste volledige records.

Journalopslag is persistent. Segmenten roteren minimaal ieder uur. Een timer
roteert en verwijdert ieder uur uitsluitend gegevens in de twee eigen
namespaces. Verwijdering gebeurt per bestand, waardoor fysieke verwijdering
kan achterlopen tot de volgende rotatie/opruiming. De GUI past de termijn per
record toe. Na uitgeschakeld te zijn geweest haalt de host de opruiming bij
via de persistente timer. Zie de [journalinstellingen](https://manpages.ubuntu.com/manpages/noble/man5/journald%40.conf.5.html).

Nieuwe installaties gebruiken Tinyproxy `LogLevel Warning`: verzoeken en
verbindingen worden standaard niet gelogd. Bestaande native configuratie blijft
bij een upgrade behouden. `Connect`, `Info` en `Debug` kunnen client-IP's,
bestemmingen en andere persoonsgegevens vastleggen. Maskering in de interface
verwijdert deze oorspronkelijke records niet.

## Zoeken, datums en archieven

Open **Logs → Periode**, vul begin en einde in je lokale tijdzone in en pas de
periode toe. De browser verstuurt UTC-tijdstempels. Historisch zoeken pauzeert
automatisch vernieuwen; **Terug naar live** wist de periode. Bron-, niveau- en
tekstfilters worden gecombineerd met de datumfilter. Genummerde auditarchieven
en gearchiveerde journalsegmenten worden samen met actieve logs doorzocht.

Paginering gebruikt een vast boventijdstip, zodat nieuwe records oudere
pagina's niet verschuiven. Auditrotatie en lezen worden geserialiseerd.
Retentie kan tijdens het bladeren wel records verwijderen; dit is geen
blijvende momentopname. Zoekopdrachten hebben limieten voor records, bytes en
duur. Een melding over onvolledige resultaten betekent dat een bron onleesbaar
was, een regel beschadigd was of een scanlimiet is bereikt. Kies een kortere
periode en controleer de rechten op de bronnen. Het begrensde aantal resultaten
is niet het totale aantal records in alle bewaarde geschiedenis.

De lezer ondersteunt ook het standaardbestand
`/var/log/tinyproxy/tinyproxy.log` met genummerde en gzip-archieven. Bestandslogs
hebben hun eigen logrotatebeleid van de beheerder of het pakket; de
namespacepolicy verwijdert deze bestanden niet. Andere aangepaste paden worden
niet via de GUI ontsloten en vereisen eigen lees- en retentievoorzieningen.
Laat niet meerdere rotatietaken hetzelfde bestand beheren. Vermijd
`copytruncate` voor auditlogs: [logrotate beschrijft het mogelijke gegevensverlies](https://man7.org/linux/man-pages/man5/logrotate.conf.5.html).

## Persoonsgegevens en de melding

Auditrecords bewaren de beheerdersnaam, gebeurtenis, tijd en noodzakelijke
wijzigingsgegevens voor verantwoording. Standaard worden IPv4-adressen vóór
auditopslag gemaskeerd naar /24 en IPv6-adressen naar /64. Regelinhoud krijgt
een digest met geheime sleutel; beschrijvingen worden niet overgenomen in
standaard auditrecords. Wachtwoorden, OTP's, tokens, cookies en
Authorization-velden worden uitgesloten. Auditrecords worden niet nog eens
naar het operationele journal gekopieerd. Het ongebruikte, onbeperkte log van
volledige regeltoevoegingen wordt niet meer geschreven. Beheerd onderhoud
verwijdert oude records in het standaard statepad volgens de auditretentie.

Historische logreacties en exports krijgen extra afscherming van credentials
en URL-querystrings. Dat verwijdert bronrecords niet. Downloads vallen buiten
de beheerde retentie: bewaar ze veilig en verwijder ze volgens het beleid van
je organisatie. Alleen ingelogde beheerders kunnen logs opvragen; er is geen
publiek logendpoint.

Bij het inloggen en in het logvenster staat een korte melding. `/privacy` is
publiek en beschrijft categorieën, doelen en ingestelde termijnen, zonder
accounts of netwerktopologie te tonen. Stel het echte contact en beleid in:

```dotenv
PRIVACY_CONTACT=privacy@example.com
PRIVACY_POLICY_URL=https://example.com/privacy
```

De organisatie achter de installatie bepaalt wie verwerkingsverantwoordelijke
is, de rechtsgrond, ontvangers, rechtenprocedure en overige informatie die bij
de daadwerkelijke verwerking hoort. Maak de organisatiemelding compleet vóór
productie. Dit is geen toestemmingsvraag en geen verklaring dat de installatie
automatisch AVG-conform is. De AVG vereist doelgerichte gegevensminimalisatie
en opslagbeperking, maar schrijft geen universele logtermijn voor. Zie
[AVG-artikelen 5 en 13](https://eur-lex.europa.eu/eli/reg/2016/679/oj/nld/)
en de [uitleg van de Data Protection Commission](https://www.dataprotection.ie/en/faqs/responsibilities-data-controllers/how-long-should-personal-data-be-held-meet-obligations-imposed-gdpr).

## Instellen en controleren

Instellingen toont de geconfigureerde termijnen. Pas `/etc/proxypermit/.env`
aan en voer een installerupgrade uit om journalpolicy en timers bij te werken:

```dotenv
AUDIT_LOG_RETENTION_DAYS=30
LOG_RETENTION_DAYS=30
PROXY_TRAFFIC_RETENTION_DAYS=7
AUDIT_LOG_MAX_BYTES=2097152
AUDIT_LOG_BACKUP_COUNT=5
LOG_IP_MODE=masked
AUDIT_ENTRY_MODE=hash
HTTP_ACCESS_LOGS=false
```

`LOG_IP_MODE=full` en `AUDIT_ENTRY_MODE=full` zijn expliciete keuzes die meer
persoonsgegevens vastleggen. `hash` is pseudonimisering, geen anonimisering.
Gunicorn-toegangslogging staat standaard uit; ingeschakeld laat deze client-IP's
en requestquerystrings weg. Externe reverse proxies hebben eigen instellingen
voor toegangslogs en retentie nodig.

```bash
sudo bash install.sh --upgrade --plan
sudo bash install.sh --upgrade --apply
systemctl list-timers 'proxypermit-*-retention.timer'
systemctl show proxypermit-audit-retention.service -p Result -p ExecMainStatus
systemctl show proxypermit-journal-retention.service -p Result -p ExecMainStatus
sudo journalctl --namespace=proxypermit --disk-usage
sudo journalctl --namespace=proxypermit-traffic --disk-usage
```

De upgrade herstart lokaal Tinyproxy en beheerde Keepalived om hun logging aan
de namespace te koppelen. Werk HA-nodes één voor één bij. Een kortere policy
kan logs definitief verwijderen; applicatieherstel haalt verlopen logs niet
terug. Retentietaken verwijzen naar een bewaarde release en blijven na een
normale deïnstallatie werken, die logs, configuratie en releases behoudt.
Verwijder die release niet zolang de audit-retentieservice ernaar verwijst.

**Grens bij upgrades:** eerdere hostjournal-/syslogrecords, eigen logbestanden,
configuratie-/herstelback-ups en downloads worden niet stilzwijgend gewist of
onder deze timers gebracht. Controleer hun bestaande retentie afzonderlijk.
De installer verwijdert niets uit het algemene hostjournal en behoudt zo de
records van andere diensten. Handmatige en legacy-installaties vereisen eigen
opslagretentie; de publieke melding geeft die onbeheerde toestand aan.

## Lokale gezondheidscontroles en verbindingsruis

Beheerde HA gebruikt nu een HTTP-verzoek met een tijdslimiet aan de ingestelde
interne statistiekhost van Tinyproxy. De root-eigen helper draait onder het
bestaande account `proxypermit` om de beschermde configuratie te kunnen lezen.
Hij verstuurt geen proxywachtwoorden. Een proxy met een luisterpoort die niet
antwoordt, wordt afgekeurd. HTTP 401/403/407 bewijst nog steeds dat de proxy
antwoordt wanneer authenticatie of lokale toegangsregels toegang beperken.
Dit bewijst geen bereikbaarheid van upstreamservers of correcte volledige filtering.

Succesvol herkenbaar lokaal controleverkeer is standaard verborgen in de
logquery. Met **Gezondheidscontroles tonen** of expliciet zoeken neem je dit mee.
De records blijven onder de bestaande verkeersretentie van 7 dagen vallen.
Waarschuwingen, fouten, externe clients en niet-herkende historische socketfouten
worden nooit op deze manier verborgen. Herkenning vereist het ongemaskeerde
loopbackadres, proces en bestandsdescriptor; een gemaskeerd netwerkprefix is
onvoldoende bewijs dat het een gezondheidscontrole was.

De installer migreert herkenbare oude helpers samen met de exacte beheerde
Keepalived-configuratie, met behoud van eigenaar, rechten en rollback-back-ups.
Eigen helpers en configuraties blijven behouden en vragen beoordeling door de
beheerder. De oude luistercontrole blijft beschikbaar voor compatibiliteitstests.
Keepalived gebruikt twee mislukte controles voor FAULT en twee geslaagde voor
herstel. De werkelijke overnametijd hangt ook van VRRP en de hostplanning af.
Zie de [Keepalived-referentie](https://keepalived.org/documentation/keepalived-conf/)
en de [statistiekhostdocumentatie van Tinyproxy](https://tinyproxy.github.io/).
