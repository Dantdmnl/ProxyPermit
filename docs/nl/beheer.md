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
3. Kies **Publiceren en activeren** tijdens het onderhoudsvenster. Tinyproxy
   herstart en bestaande verbindingen kunnen worden onderbroken.
4. Test het clientverkeer opnieuw en controleer de wijziging in **Live logs**.

Publiceer gedeeld beleid bij gekoppelde beheerde HA-nodes op A. B haalt het
automatisch op en activeert het. Controleer gelijke revisies vóór een
failovertest. Niet-gekoppelde nodes houden zelfstandig beleid. Houd account- en
softwareonderhoud gescheiden van beleidspublicatie.

## Een storing onderzoeken

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
- De HA-proef bevestigt VIP-failover in het WSL-lab. Valideer de volledige
  installatie, hostuitval, netwerkpartitionering en herstel op de beoogde
  afzonderlijke hosts. [Uitgevoerde tests](verification.md)

## Verbindingsverlies

Elke geopende pagina controleert of de beheerconsole bereikbaar is. De verbindingsmelding maakt onderscheid tussen een browser zonder netwerkverbinding en een onbereikbare console. De melding verschijnt ook boven open dialoogvensters, ondersteunt beide thema’s en probeert tijdens een storing iedere vijf seconden opnieuw verbinding te maken. Deze controle zegt niets over Tinyproxy of de HA-peer.

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
de SSH-peer als enkele beheerbron gebruiken. Lokale onbeheerde installaties
zonder beide blijven zichtbaar ongeconfigureerd. Gebruik nooit `0.0.0.0/0` of
`::/0`. De interactieve installer vraagt het vertrouwde netwerk en de echte webpoort.

Het profiel `/etc/proxypermit/access-safety.json` is eigendom van root. Het
serviceaccount kan het lezen, maar de GUI kan het niet herschrijven. Toegevoegde
UFW-regels heten `ProxyPermit SAFETY IN - ...` en worden voor gewone regels
geplaatst. Eerst worden alle regels droog getest. Activering en standaardbeleid
van UFW blijven behouden. Bestaande toestandsafhankelijke retourregels verzorgen
antwoorden; er komt geen brede OUT-uitzondering bij. Configuratie, profiel en
firewallbestanden worden voor herstel geback-upt.

Het lokale Allow-blok valt buiten gedeeld HA-beleid en de bijbehorende
conflictbaseline. Revisies voor native bewerkingen bewaken wel het hele bestand.
Een backup behoudt eigen beheertoegang wanneer A clientregels publiceert.
Herstart, rollback en conceptopslag blijven afzonderlijke stappen. De server
controleert de vaste uitzonderingen, poorten en luisterbindingen, ook bij
inkomend gedeeld beleid.

De GUI kan een bestaand profiel niet veranderen. Installerargumenten voegen
beheerbronnen en poorten toe zonder bestaande bescherming te verwijderen.
Verwijder oude uitzonderingen bewust via onafhankelijke OS-beheertoegang, met gezamenlijke controle van profiel, native Allow-blok en
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
