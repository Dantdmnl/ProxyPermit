# Beheerdershandleiding

[English](../en/administration.md) · [Documentatie](README.md)

Gebruik deze handleiding voor een proefinstallatie en de overdracht naar dagelijks
beheer. ProxyPermit beheert Tinyproxy. Het beheeradres en het proxyadres hebben
elk hun eigen toegangsregels en netwerkfunctie.

## Kies de opstelling

| Opstelling | Beheer | Clientverkeer |
| --- | --- | --- |
| Zelfstandige installatie | Eén beheerconsole en één configuratie | Eén Tinyproxy-adres |
| Nieuwe HA-installatie | Een console en lokaal beleid per node | Eén virtueel IPv4-adres op twee nodes via Keepalived |
| Bestaand cluster met synchronisatie | Bestaande node-instellingen en afzonderlijke whitelistsync | Bestaand virtueel adres |

Begin met een afgeschermde Debian- of Ubuntu-testhost met systemd. Reserveer bij
HA twee vaste node-IP’s en één vrij virtueel IP. Gebruik de
[installatiewizard en voorbeelden](installatie.md#nieuwe-installatie-op-debian-of-ubuntu).
Bewaar de gekozen release, de poorten, het clientnetwerk, het beheeradres en de
naam van de back-up bij het wijzigingsverzoek.

## Controleer vóór ingebruikname

1. Bekijk het installatieplan. Voer de installatie uit als het plan klopt. Open
   de beheerconsole via een SSH-tunnel of via HTTPS achter de reverse proxy.
   Beperk toegang tot het beheernetwerk. Gebruik bij HTTPS een Secure-sessiecookie
   en vertrouw forwarded headers uitsluitend achter je eigen reverse proxy.
   [OWASP-richtlijnen voor sessiebeheer](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)
2. Maak een persoonlijk account aan voor elke beheerder. Lokale accounts krijgen
   een [rol die bij hun werk past](rollen.md). Optionele 2FA stel je in onder **Instellingen**.
   Controleer het inloggen en leg vast wie bij verlies van de authenticator via de
   server toegang kan herstellen.
3. Beperk de clienttoegang in Tinyproxy tot de bedoelde netwerken. Controleer
   vanaf een toegestane client zowel een toegestane als een geblokkeerde
   bestemming. Controleer ook dat een client buiten het toegestane netwerk
   geen verkeer kan doorsturen.
4. Maak een kleine wijziging via de beheerconsole. Controleer na publicatie
   opnieuw het verkeer. Zoek de beheerder en node op in de auditlog onder
   **Live logs**. Publiceer bij gekoppelde HA-nodes op A en controleer de
   overeenkomende toegepaste revisie op B.

Gebruik voor de verkeersproef twee interne testbestemmingen waarvan je de
normale HTTP-respons kent. Vervang de voorbeeldwaarden:

```bash
proxy=http://192.0.2.10:3128
curl --noproxy '' --proxy "$proxy" --max-time 10 -i http://toegestaan.example/
curl --noproxy '' --proxy "$proxy" --max-time 10 -i http://geblokkeerd.example/
```

Bij allowlist-filtering verwacht je het normale antwoord van de eerste
testbestemming en HTTP 403 voor de tweede. Test ook HTTPS via CONNECT als je
clients dat gebruiken. Een actieve systemd-service of HTTP 200 van de
beheerconsole is op zichzelf onvoldoende om clientverkeer te verifiëren.

## Proxybeleid wijzigen

1. Noteer welke bestemmingen na de wijziging bereikbaar moeten zijn.
2. Open **Proxyconfiguratie**. Kies **Controleer concept** en verwerk de fouten
   of aandachtspunten. Kies daarna **Concept opslaan**.
3. Kies **Publiceren en activeren** tijdens het onderhoudsvenster. Lees de
   bevestiging: de standaardherstart kan bestaande verbindingen onderbreken.
   Een gecontroleerde, ingeschakelde reloadroute behoudt die bij geschikte filterwijzigingen.
4. Test het clientverkeer opnieuw en controleer de wijziging in **Live logs**.

Publiceer gedeeld beleid bij gekoppelde beheerde HA-nodes op A. B haalt het
automatisch op en activeert het. Controleer gelijke revisies vóór een
failovertest. Niet-gekoppelde nodes houden zelfstandig beleid. Houd account- en
softwareonderhoud gescheiden van beleidspublicatie.

## Optioneel filters herladen

Publiceren herstart Tinyproxy standaard en kan verbindingen verbreken. v1.3.0
voegt een optionele reloadroute toe voor geschikte filterwijzigingen op
**Tinyproxy 1.11.1 en 1.11.3** met beheerde journallogging. Andere wijzigingen
vereisen nog een herstart; een mislukte reload leidt nooit ongemerkt tot een herstart.

Laat de OS-beheerder voor deze optie `TINYPROXY_RELOAD_ENABLED=true` instellen in
`/etc/proxypermit/.env` en daarna de ondersteunde installerupgrade vanuit de
nieuwe release uitvoeren. Gebruik bij een beveiligd cluster de onderhoudsroute
verderop. De installer geeft Tinyproxy via ACL’s alleen leesrechten op de native
configuratie en het filterbestand. De account wordt geen lid van de beheergroep.
Een aangepast ACL-masker dat lezen blokkeert vereist OS-controle. Controleer de
rechten opnieuw als je de OS-account van Tinyproxy wijzigt.

Controleer toegestane/geblokkeerde bestemmingen, een doorlopende HTTP-overdracht
en een CONNECT-tunnel op je eigen hosts voordat je op deze optie vertrouwt.
Bestaande tunnels blijven open; nieuwe regels sluiten ze niet achteraf af.
Uitgebreide acceptatie bij stroomuitval en opslagfouten staat nog op de
[roadmap](vervolgwerk.md).

## Een storing onderzoeken

Zie [HA-bescherming en de beperkingen](hoge-beschikbaarheid.md#begrijp-de-bescherming-bij-overname)
als de interface meldt dat quorum en fencing niet zijn ingesteld. Dat is geen
melding over een mislukte beleidsynchronisatie.

Open **Live logs** en filter op bron en niveau. Typ om de geladen regels te
filteren of kies **Zoek op server** om ook oudere regels te doorzoeken. Klap
een auditregel uit voor details over de wijziging. Zoek met **Periode** in
bewaarde auditarchieven en journalgeschiedenis. Let op meldingen over
onvolledige resultaten. Zie [logging, privacy en bewaartermijnen](logging.md)
voor opslaglimieten en exports.

Controleer op de host de service en het journal.

```bash
sudo systemctl status proxypermit.service tinyproxy.service --no-pager
sudo journalctl --namespace=proxypermit -u proxypermit.service -n 100 --no-pager
sudo journalctl --namespace=proxypermit-traffic -u tinyproxy.service -n 100 --no-pager
# Alleen op HA-nodes
sudo systemctl status keepalived.service --no-pager
ip -br -4 address
sudo journalctl --namespace=proxypermit -u keepalived.service -n 100 --no-pager
```

| Symptoom | Controle |
| --- | --- |
| Tijdelijke blokkade bij het inloggen | Wacht tot de blokkade is verlopen. Een taalwissel of een nieuw tabblad verandert de wachttijd op de server niet. |
| Beheer opent niet | Controleer de manager-unit, de lokale luisterpoort en de SSH-tunnel of TLS-reverse-proxy. |
| Proxy geeft onverwacht 403 | Controleer de client-ACL, de filtermodus, de doelregel en of het concept is geactiveerd. |
| Failover werkt niet | Controleer welke node het virtuele IP heeft, bekijk de Keepalived-logs en controleer het VRRP-verkeer. Test vervolgens vanaf de client via het virtuele IP. |
| Nodes hebben verschillend beleid | Vergelijk de geactiveerde configuratie en filters per node. Controleer bij legacy-sync ook de TLS-configuratie en sync-logs. |

## Back-up, upgrade en herstel

Neem de installatie-instellingen, Tinyproxy-configuratie, filterregels,
accountbestand, sleutels en relevante servicestatus op in je back-up. Controleer
de ingestelde paden in `/etc/proxypermit/.env`. Bij HA horen ook de
Keepalived-configuratie en de gegevens per node erbij. Beperk toegang tot deze
bestanden, want ze kunnen wachtwoordhashes, 2FA-sleutels en sync-secrets bevatten.
Bewaar een kopie buiten de node en oefen herstel op de testomgeving.

Bekijk vóór een [upgrade](installatie.md#beheerde-installatie-bijwerken) het plan
en leg het huidige beleid vast. De installer toont na afloop de back-upnaam.
Bekijk bij [herstel](installatie.md#terugzetten-vanuit-een-back-up) welke
bestanden worden teruggezet. Bewaar latere beleidswijzigingen als het plan
oudere configuratie en filterregels terugzet. Test na herstel opnieuw of je
kunt inloggen en of het proxyverkeer werkt.

## Grenzen van de huidige versie

- Er zijn drie lokale rollen. Centrale identiteitsvoorzieningen en
  accountreplicatie vallen buiten het huidige lokale accountmodel.
- Auditlogs staan lokaal. Leg voor bedrijfsgebruik vast hoe je ze centraal
  verzamelt en hoe lang je ze bewaart.
- VirtualBox-HA en een afzonderlijk KVM-prototype met quorum/fencing zijn getest.
  De gewone HA-werkwijze richt dat beveiligde cluster nog niet in. Controleer
  installatie, hostuitval, netwerkonderbrekingen en herstel op je beoogde platform.
  [Uitgevoerde tests](verification.md)

## Verbindingsverlies

Elke geopende pagina controleert of de beheerconsole bereikbaar is. De verbindingsmelding maakt onderscheid tussen een browser zonder netwerkverbinding en een onbereikbare console. De melding verschijnt ook boven open dialoogvensters, ondersteunt beide thema’s en probeert tijdens een storing iedere vijf seconden opnieuw verbinding te maken. Deze controle zegt niets over Tinyproxy of de andere HA-node.

Je invoer en open dialoogvensters blijven op de huidige pagina staan. Navigeren en nieuwe schrijfverzoeken worden tegengehouden zolang de console onbereikbaar is. Bij herstel wordt de pagina niet herladen en worden acties niet herhaald. Als het antwoord op een schrijfverzoek verloren ging, controleer dan de actuele regels of instellingen voordat je de actie herhaalt: de server kan die al verwerkt hebben. De bestaande revisiecontroles blijven gelijktijdige wijzigingen bewaken.

Houd het tabblad open om niet-opgeslagen invoer te behouden. Dit herstelt de verbinding voor een al geopende pagina; het biedt geen offline beheer. Een pagina openen of herladen vereist nog steeds een bereikbare server. Inloggegevens en beheergegevens worden niet in een offline cache opgeslagen.

## Anti-lockoutprofiel

Zet de echte vertrouwde beheerbronnen en de SSH-/webpoorten op de host vast.
Gebruik de luisterpoort van de reverse proxy of relay, niet een afwijkende
externe NAT-poort. Houd een onafhankelijke console en een tweede werkende
beheersessie beschikbaar. Vervang voorbeeldadres en installatiepad:

```sh
sudo bash install.sh --upgrade --install-dir /opt/proxypermit \
  --management-cidr 192.0.2.8/32 --management-ssh-port 22 \
  --management-web-port 443 --plan
```

Controleer het plan en herhaal met `--apply`. CIDR- en poortargumenten mogen
herhaald worden. Zonder bestaand profiel of expliciet netwerk kan de installer
het adres van de SSH-verbinding als enige beheerbron gebruiken. Lokale onbeheerde installaties
zonder beide blijven zichtbaar ongeconfigureerd. Gebruik nooit `0.0.0.0/0` of
`::/0`. De interactieve installer vraagt het vertrouwde netwerk en de echte webpoort.

Het profiel `/etc/proxypermit/access-safety.json` is eigendom van root. Het
serviceaccount kan het lezen, maar de webinterface kan het niet herschrijven. Toegevoegde
UFW-regels heten `ProxyPermit SAFETY IN - ...` en worden voor gewone regels
geplaatst. Eerst worden alle regels droog getest. Activering en standaardbeleid
van UFW blijven behouden. Bestaande toestandsafhankelijke retourregels verzorgen
antwoorden; er komt geen brede OUT-uitzondering bij. Configuratie, profiel en
firewallbestanden worden voor herstel geback-upt.

Het lokale Allow-blok valt buiten gedeeld HA-beleid en de bijbehorende
conflictbaseline. Bij het bewerken van de oorspronkelijke configuratie controleert ProxyPermit
wel de revisie van het volledige bestand.
Een back-upnode behoudt zijn eigen beheertoegang wanneer A clientregels publiceert.
Herstart, rollback en conceptopslag blijven afzonderlijke stappen. De server
controleert de vaste uitzonderingen, poorten en luisterbindingen, ook bij
inkomend gedeeld beleid.

De webinterface kan een bestaand profiel niet veranderen. Installeropties voegen
beheerbronnen en poorten toe zonder bestaande bescherming te verwijderen.
Verwijder oude uitzonderingen bewust via onafhankelijke OS-beheertoegang, en controleer daarbij het profiel, het vaste Allow-blok en
bijbehorende UFW-regels. Test een tweede beheersessie voordat je de eerste sluit.
Dit beschermt wijzigingen via de utility, niet tegen root die zelf de firewall
reset of het profiel verwijdert.

De lokale uitzondering staat voor gedeelde ACL-regels omdat Tinyproxy stopt bij
de eerste passende clienttoegangsregel. Upstream-routering heeft een andere
volgorde. Zie de [ACL-implementatie van Tinyproxy](https://github.com/tinyproxy/tinyproxy/blob/master/src/acl.c).

### Waarom meerdere nodes dezelfde beheerbron kunnen tonen

Het profiel toont toegestane **bronadressen**, niet de eigen adressen van de node.
In het VirtualBox-lab zien alle drie de afzonderlijke NAT-verbindingen een
poortdoorgestuurde hostverbinding als afkomstig van `10.0.2.2`. Met `/32` wordt
dat ene waargenomen bronadres toegestaan. Daarom kunnen de standalone en de
HA-nodes terecht dezelfde waarde tonen. Hun HA-adressen en identiteit zijn
verschillend. Moet je achter gedeelde NAT of een reverse proxy onderscheid
maken tussen clients? Beperk de toegang dan ook op dat toegangspunt. De bronregel
vervangt inloggen of HTTPS niet.

## De HA-peersleutel vernieuwen

Werk eerst beide nodes bij en wacht tot de automatische synchronisatie slaagt.
Open op A **Instellingen → Hoge beschikbaarheid → Peersleutel vernieuwen**.
Bevestig met je huidige wachtwoord en, als je account 2FA gebruikt, een nieuwe
authenticatorcode. B ontvangt de nieuwe sleutel automatisch via de bestaande
gepinde TLS-verbinding. B slaat die sleutel op voordat A de oude sleutel intrekt.
De interface toont de voortgang en de afronding, maar nooit de sleutels zelf.

De overgang verloopt na tien minuten. Als B de nieuwe sleutel dan nog niet heeft
bevestigd, worden beide sleutels geweigerd en moet je de nodes opnieuw koppelen.
Een verloren bevestigingsantwoord kan met de op B opgeslagen sleutel opnieuw
worden opgevraagd. Het beleid en bestaande proxyverbindingen blijven tijdens de
sleutelrotatie behouden. Certificaatvernieuwing is een afzonderlijke handeling.

Gebruik je het oudere nginx-voorbeeld voor interne TLS? Sta dan vóór de rotatie
ook `/internal/ha/credential-commit` toe, naast `enroll` en `policy`. Behoud de
beperking tot het bronadres van de peer en weiger alle andere paden. Maak een
back-up, voer `sudo nginx -t` uit en herlaad daarna nginx. Het bijgewerkte voorbeeld
staat in `deploy/nginx/proxypermit-ha.conf.example`. Het beheerde Gunicorn-TLS-endpoint
beperkt de toegang al tot deze drie machine-endpoints. Stel de beheerinterface
niet via deze interne listener beschikbaar.

## Het interne HA-certificaat vernieuwen

Dit doet de OS-beheerder op A. Het staat los van het certificaat voor de
beheerwebsite. Werk eerst beide nodes bij en controleer of de synchronisatie
slaagt. Het huidige interne certificaat moet nog minstens elf minuten geldig
zijn. Deze opdrachten gebruiken de standaardinstallatiemap:

```bash
cd /opt/proxypermit/current
sudo .venv/bin/python -m scripts.deploy.ha_certificates plan
sudo .venv/bin/python -m scripts.deploy.ha_certificates stage
sudo .venv/bin/python -m scripts.deploy.ha_certificates activate
```

Geef B na `stage` tijd om het beleid op te halen. `activate` weigert de definitieve
omschakeling totdat B de nieuwe pin heeft opgeslagen. Probeer de opdracht zo nodig
na de volgende synchronisatie opnieuw. Gebruik je de oudere interne nginx-listener?
Voeg dan bij `stage` de optie `--tls-service nginx.service` toe. Bij een andere
installatiemap geef je ook `--install-dir /jouw/pad` mee. De beheerapp wordt kort
herstart; Tinyproxy en Keepalived blijven draaien.

Vóór de definitieve beslissing kun je het aanbod met `cancel` intrekken zonder
het actieve certificaat te veranderen. Daarna moet herstel het nieuwe certificaat
afmaken: zet nooit alleen het oude certificaat of de oude pin terug. Een
OS-herstelunit maakt een onderbroken omschakeling bij het opstarten af. Met
`recover` kun je dat handmatig opnieuw proberen; voer daarna `activate` uit voor
B’s bevestiging. Bij een verlopen overgang of gewijzigde peer kan herstel via
onafhankelijke beheertoegang en opnieuw koppelen nodig zijn. Certificaatcontrole
wordt nooit uitgeschakeld.

Nieuwe sleutels, back-ups van het oorspronkelijke certificaat, de sleutel en de
omgeving, en het herstelplan blijven alleen voor root toegankelijk onder
`/etc/proxypermit/ha-tls/maintenance`. Openbare overgangsmetadata staat apart in
`/etc/proxypermit/ha-certificate-transition.json`. De webservice hoeft daardoor
niet in een oude afgeschermde TLS-map te kunnen lezen. De bestaande private
auditlog legt de OS-UID en het bewerkingsnummer vast, met dezelfde bewaartermijn.

## Onderhoud aan een door het OS beveiligd cluster

Deze werkwijze is bedoeld voor een OS-beheerder die de geteste
Pacemaker/Corosync-resource, een onafhankelijke TLS-witness en fencing al heeft
ingericht. De gewone HA-wizard installeert deze onderdelen **nog niet**.
Keepalived mag niet zelfstandig starten bij het opstarten of zichzelf automatisch
herstarten: Pacemaker bepaalt waar het draait.

**Bescherming bij overname** in de interface toont recente clustergegevens die door
root zijn gecontroleerd. De status maakt onderscheid tussen niet-ingestelde
beveiliging, onzekerheid, verminderde beveiliging, een gestopte resource en
tegenstrijdig VIP-eigenaarschap. Zonder geslaagde verversing vervalt een gezonde
melding binnen twintig seconden. Fencing moet beide nodes expliciet aan
verschillende doelen koppelen en als beschikbaar worden gemonitord. Dit vervangt
geen echte fencingtest en bewijst niet dat de hypervisor, witness en
stroomvoorziening onafhankelijk van elkaar zijn.

Pak voor een applicatie-update de nieuwe release uit en voer de installer op één
node uit:

```bash
sudo bash install.sh --upgrade --cluster-maintenance --plan
sudo bash install.sh --upgrade --cluster-maintenance --apply
```

Gebruik bij een afwijkend installatiepad ook `--install-dir /jouw/pad`.
De installer vereist gezond quorum, een bereikbare witness en beschikbare
fencing. Hij reserveert onderhoud voor het hele cluster en zet deze node in
standby. Lokale wijzigingen beginnen pas nadat de andere node de beveiligde VIP
heeft overgenomen. OS-pakketten, de Keepalived-configuratie en clusterservices
blijven behouden. Na controle van de beheerinterface en de proxy herstelt hij de
oorspronkelijke standby-instelling en geeft hij de reservering vrij. Wacht op een
stabiele clusterstatus voordat je de tweede node bijwerkt. Gedeeld beleid wordt
nog steeds op A gepubliceerd; VIP-overname geeft B geen schrijfrechten.

Na een afgeronde clusterupgrade kun je de vorige applicatieversie
terugzetten. Gebruik de back-upnaam die de laatste clusterupgrade heeft
getoond. Voer dezelfde installer uit vanuit de uitgepakte bronmap:

```bash
sudo bash install.sh --rollback BACKUPNAAM --cluster-maintenance --plan
sudo bash install.sh --rollback BACKUPNAAM --cluster-maintenance --apply
```

Controleer in het plan welke versie wordt teruggezet. Eerst neemt de andere
node het VIP over. De installer bouwt de vorige applicatieversie opnieuw op
met de huidige configuratie, accounts en filterregels. Fencing, firewallregels
en het beheeradres blijven behouden. Controleer vooraf of de vorige
applicatieversie de huidige gegevens ondersteunt. De installer heeft het
bewijsbestand van de afgeronde clusterupgrade nodig.

Bij een onderbroken of mislukte update blijft de onderhoudsreservering staan.
Deze verloopt niet automatisch, zodat de andere node niet tegelijk in onderhoud
kan gaan. Controleer het back-upmanifest van de installer en herstel eerst de
lokale installatie. Laat daarna de geïnstalleerde release het herstel controleren:

```bash
cd /opt/proxypermit/current
sudo .venv/bin/python -m scripts.deploy.cluster_maintenance --recover
```

Deze opdracht controleert de lokale beheerinterface en proxy, de beveiliging op
de andere node en het eigenaarschap van de reservering voordat hij standby
herstelt. Bij tijdelijk onbevestigde bescherming controleert ProxyPermit tot
20 seconden opnieuw voordat het herstelbestand behouden blijft. Gewijzigde
beheerdersinstellingen of een onduidelijk herstelbestand
worden geweigerd. Verwijder `/etc/proxypermit/cluster-maintenance.json` niet,
wis geen reservering van de andere node en start Keepalived niet rechtstreeks om
een weigering te omzeilen. Onderzoek zulke situaties via OS-clusterbeheer en
onafhankelijke beheertoegang. Voor herstel van een volledige installatieback-up
is op beveiligde nodes een aparte herstelprocedure via het OS nodig.

### Een losgekoppelde beschermde node verwijderen

Houd rechtstreekse SSH- en beheertoegang tot beide nodes open. Zorg ook voor
onafhankelijke hersteltoegang tot het fencingplatform. Regel eerst via het
OS-clusterbeheer dat de andere node het VIP bedient en quorum behoudt
tijdens de wijziging. Haal de vertrekkende node uit het actieve cluster.
Op die node moeten `pacemaker.service`, `corosync.service`,
`corosync-qdevice.service` en `keepalived.service` inactief en permanent
gemaskeerd zijn. De installer wijzigt het clusterlidmaatschap, de witness
en de fencinginstellingen van de overblijvende node niet.

Voer de installer op de losgekoppelde node uit vanuit de uitgepakte bronmap.
Controleer in het plan welke services en firewallregels worden verwijderd:

```bash
sudo bash install.sh --retire-ha --detached-ha --plan
sudo bash install.sh --retire-ha --detached-ha --apply
```

De installer controleert de opgeslagen migratiestatus, het ontbreken van
een lokaal VIP en de bereikbaarheid van de proxy op de andere node en
de lokale beheerconsole. Hij verwijdert alleen de vastgelegde HA- en
clusterregels. De vaste beheerregels blijven behouden en de lokale
beheerconsole blijft na het uit bedrijf nemen beschikbaar.

Wil je de console ook verwijderen? Controleer eerst je SSH-toegang en
bekijk daarna het plan voordat je het uitvoert:

```bash
sudo bash install.sh --uninstall --detached-ha --plan
sudo bash install.sh --uninstall --detached-ha --apply
```

De uninstaller controleert of de vorige stap is afgerond en de
clusterdiensten nog gemaskeerd zijn. OS-pakketten, herstelgegevens van het
cluster, beleid en back-ups blijven beschikbaar voor de OS-beheerder.
Volg de procedure van je cluster om de node te vervangen of de overblijvende
installatie opnieuw in te richten.

De installer controleert ook de afgeschermde back-ups van native concepten. Hij
weigert symlinks, bestanden met meerdere harde koppelingen en oude concepten met
ongeschikte eigenaars of rechten. Hij wijzigt zulke door de applicatie beschrijfbare
paden niet recursief als root. Controleer die bestanden via OS-beheer en gebruik
geen algemene recursieve rechtenopdracht.
