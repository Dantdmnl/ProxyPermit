# Hoge beschikbaarheid instellen

[English](../en/high-availability.md) · [Documentatie](README.md)

**Voor:** een systeembeheerder met twee werkende zelfstandige nodes.
**Resultaat:** apparaten gebruiken één gedeeld proxyadres en B ontvangt automatisch
het beleid dat op A is gepubliceerd. Oefen eerst in het [VM-testlab](vm-lab.md).

In de huidige ontwikkelversie tonen het inlogscherm en Instellingen of je
node A of B beheert. Die aanduiding
blijft gelijk als het virtuele IP-adres verhuist. Accounts en sessies zijn
lokaal. Als het inlogformulier een nodewissel vaststelt, opent de browser een
nieuw formulier en vraagt je in te loggen met je account voor die node.

Verhuist het VIP terwijl de beheerconsole openstaat, dan blijft het huidige
formulier zichtbaar en blokkeert de pagina verdere wijzigingen. Bekijk of
kopieer onopgeslagen instellingen voordat je **Beheerconsole opnieuw openen**
kiest. Controleer na het inloggen de actuele instellingen van die node.

Voor een herkenbaar openbaar label stel je `PROXYPERMIT_PUBLIC_NODE_LABEL` in
het afgeschermde bestand `/etc/proxypermit/.env` van de node in. Herstart daarna
`proxypermit.service`. Gebruik maximaal 32 ASCII-letters, cijfers, spaties,
lage streepjes of koppeltekens en begin met een letter of cijfer. Kies een
label dat iedereen die het inlogscherm kan bereiken mag zien.

!!! warning "Quorum en fencing worden niet automatisch ingesteld"
    Deze werkwijze stelt Keepalived en beleidsynchronisatie in. Afdwingbare
    split-brainbescherming bij netwerkonderbrekingen wordt nog niet ingericht.
    Lees vóór productiegebruik [Begrijp de bescherming bij overname](#begrijp-de-bescherming-bij-overname).

## Begrijp de taak van elke node

Een node is één server. In deze handleiding pas je op **A** de gedeelde regels en
instellingen aan. **B** ontvangt ze automatisch. **Keepalived** verplaatst een
gedeeld virtueel IP-adres (het **VIP**) naar de gezonde node. Apparaten gebruiken
het VIP als proxyadres en hebben na overname dus geen ander adres nodig.

Er zijn twee verschillende taken:

| Taak | Zo werkt het |
| --- | --- |
| Beleid aanpassen en kopiëren | Publiceer op A; B haalt het beleid automatisch op en past het toe |
| Verkeer afhandelen | De gezonde node met het VIP bedient de apparaten |

Ook als B het verkeer overneemt, pas je er geen gedeeld beleid aan. Accounts,
wachtwoorden, 2FA en hostgebonden instellingen blijven lokaal. HA is geen back-up
van die bestanden en beschermt niet tegen bijvoorbeeld stroomuitval van beide servers.

## Bereid het netwerk voor

Lees eerst [Begrijp de bescherming bij overname](#begrijp-de-bescherming-bij-overname).

Verzamel deze gegevens voordat je het instelformulier opent:

| Gegeven | Wat vul je in? |
| --- | --- |
| Adressen van A en B | Twee vaste IPv4-adressen op het HA-netwerk |
| Netwerkinterface | De verbinding op elke server die het HA-netwerk draagt; de naam kan per server verschillen |
| VIP met prefix | Een derde, ongebruikt adres dat buiten DHCP is gereserveerd, met de subnetgrootte erbij |
| Toegestaan clientnetwerk | Het netwerk waarvan apparaten de proxy mogen gebruiken |
| VRRP-ID | Op beide nodes hetzelfde ID, afwijkend van andere VRRP-groepen op dat netwerk |

Gebruik je echte netwerkgegevens. Een voorgesteld VIP is geen reservering. De
controle kan een apparaat herkennen dat op dat adres reageert, maar reserveert geen adres en
sluit niet uit dat een stil apparaat het later gebruikt. Vraag bij twijfel je netwerkbeheerder.

Op beide hosts moet UFW actief zijn en inkomend verkeer standaard weigeren.
Controleer of je beheertoegang werkt en of het anti-lockoutprofiel de juiste
bronnen en poorten toont. Houd een tweede SSH-sessie of console open. De omzetting
voegt gerichte HA-regels toe en opent geen onbeperkt uitgaand internetverkeer.

## Maak A aan en laat B deelnemen

1. Open op **A**: **Instellingen → Hoge beschikbaarheid → HA instellen** en kies
   **Nieuw HA-cluster maken**.
2. Selecteer de gedetecteerde HA-verbinding. Controleer alle ingevulde gegevens, vul
   het vaste adres van B in en controleer het VIP en clientnetwerk.
3. Bekijk het plan en de firewallregels. Bevestig met je huidige wachtwoord en,
   indien ingesteld, je authenticatorcode. Wacht op afronding en log opnieuw in
   nadat de beheerinterface is herstart.
4. Kies op A **Node toevoegen** en kopieer de koppelcode. Deze verloopt na tien
   minuten. Behandel de code als een wachtwoord; plaats hem niet in logs of tickets.
5. Kies op **B**: **HA instellen → Aansluiten met koppelcode**. Plak de code,
   selecteer de HA-verbinding van B, controleer de wijzigingen en bevestig.
6. Wacht op afronding en log opnieuw in op B.

Vóór de omzetting wordt een herstelback-up gemaakt. Mislukt de omzetting? Lees
eerst het resultaat voordat je opnieuw begint. De
[installatiehandleiding](installatie.md) beschrijft herstel en servicecontroles.

## Controleer vóór overdracht

- Onder **Instellingen** moeten de gepubliceerde en toegepaste revisies gelijk
  worden. Een revisie is een kenmerk van een versie van het gedeelde beleid.
  Geef B de tijd om dat beleid op te halen.
- Publiceer een kleine testwijziging zonder gevolgen voor gebruikers op A, controleer die op B en draai de wijziging terug.
- Test vanaf een echt apparaat een toegestane en een geblokkeerde website via het **VIP**.
- Stop tijdens een onderhoudsmoment de proxy op A. Controleer of B het VIP overneemt
  en dat apparaat bedient. Herstel A en controleer beide statusweergaven opnieuw.
- Leg de beheeradressen, het VIP, de hersteltoegang en de back-uplocatie vast.

Gelijke revisies bewijzen dat het beleid overeenkomt, niet dat clientverkeer werkt.
Controleer beide. Lees [Problemen oplossen](problemen-oplossen.md) bij een afwijking.

## Een node zorgvuldig verwijderen

!!! note "Vanaf v1.4.0-dev.1 (prerelease)"

    De prerelease vraagt bij het intrekken van de koppeling om je huidige
    beheerderswachtwoord en, als je 2FA hebt ingeschakeld, een verse
    authenticatorcode. Deze aanvullende bevestiging zit niet in de stabiele v1.3.0.

Open **Instellingen → Hoge beschikbaarheid → HA-beheeracties → Koppeling intrekken**
om synchronisatie te stoppen. Op A maakt dit de peersleutel ongeldig; op B wordt
de lokale koppeling verwijderd. Controleer beide nodes en trek de koppeling op
beide in voordat je ze bewust opnieuw koppelt. Het laatst actieve proxybeleid
blijft behouden. Maak op A een nieuwe uitnodiging en laat B daarmee deelnemen.
Een nieuwe uitnodiging vervangt een eerdere, ongebruikte uitnodiging. Gebruikte
en verlopen codes werken niet meer. Houd de nodeklokken gelijk voor uitnodigingen,
2FA en certificaten.

De koppeling intrekken stopt de synchronisatie. Een node uit HA nemen stopt de
lokale HA- en proxydiensten. Dat zijn verschillende acties. Controleer voordat je de node
uit HA neemt of de andere node al verkeer via het VIP afhandelt. Volg
[Een HA-node uit gebruik nemen](installatie.md). De beheerinterface blijft
beschikbaar en de aparte UFW-regels voor anti-lockout blijven behouden.

## Begrijp de bescherming bij overname

De inrichting in deze handleiding stelt **Keepalived-failover en automatische
beleidsynchronisatie** in. Pacemaker/Corosync, een witness en fencing worden niet
automatisch geïnstalleerd. Een upgrade naar v1.3.0 voegt die onderdelen ook niet
toe aan een bestaand cluster.

Toont de interface **Bescherming bij overname → Quorum en fencing zijn niet
ingesteld**? Dat is bij deze inrichting te verwachten. De melding gaat over
clusterbescherming, niet over de synchronisatie van regels. Controleer de
beleidsrevisies afzonderlijk.

**Split brain** betekent dat beide nodes zich als actieve node gedragen. Als ze
elkaar niet meer bereiken maar nog wel clients kunnen bedienen, kan VRRP alleen
niet garanderen dat slechts één node de VIP voert. Veilige beleidsynchronisatie
voorkomt dit niet.

| Bescherming | Doel | Huidige status |
| --- | --- | --- |
| Keepalived/VRRP | De VIP verplaatsen als een node of proxy uitvalt | Ingericht door de gewone HA-werkwijze |
| Quorum met een onafhankelijke witness | Bepalen welke kant mag doorgaan na verbindingsverlies | Experimentele integratie met OS-voorbereiding; begeleide inrichting staat gepland |
| Fencing | Een node stoppen of uitschakelen vóór een onveilige overname | Vereist platformafhankelijke OS-inrichting en tests |

Een witness moet in een onafhankelijk storingsdomein staan. Fencingaccounts
moeten beperkt blijven tot de bedoelde nodes; het daadwerkelijk uitschakelen
moet ook worden getest. ProxyPermit raadt deze instellingen niet en verandert
je platform niet ongemerkt.

Bij een door het OS voorbereid, beveiligd cluster toont de interface recente,
door root gecontroleerde gegevens. Ontbrekende of verouderde gegevens worden
als onzeker getoond; een gezonde melding blijft niet onbeperkt staan. Die status
vervangt geen echte fencingtest. Bij verminderde bescherming zie je in
Instellingen welke controles niet konden worden bevestigd. De verbinding met
de witness kan bijvoorbeeld wegvallen terwijl beide proxynodes samen nog quorum
hebben. Zie [onderhoud aan een beveiligd cluster](beheer.md#onderhoud-aan-een-door-het-os-beveiligd-cluster)
en de [roadmap](vervolgwerk.md) voor de ondersteunde grenzen en resterende stappen.

### Een beschermd cluster voorbereiden (v1.4.0-dev.2)

Systeembeheerders kunnen **Instellingen → Hoge beschikbaarheid →
Split-brainbescherming voorbereiden** openen. De controles komen van een
rootservice, gelden alleen voor deze node en verlopen na 90 seconden.
Ze controleren beheerde HA, de door het OS gemelde tijdsynchronisatie,
clusterpakketten, het voorbereide plan en de fencing-agent. Ze schakelen geen
quorum in en bevestigen niet dat de andere node of witness gereed is.
Authenticatie en daadwerkelijk uitschakelen/herstellen blijven afzonderlijke
voorwaarden. Alleen een plan kopiëren vinkt die proeven nooit af.

Zodra een node voor clusterbeheer is gereserveerd, verdwijnt dit paneel voor de
eerste voorbereiding. Bekijk dan de actuele **Bescherming bij overname**.
Experimentele activering vanuit het OS is in het tijdelijke Proxmox-lab
gecontroleerd; zie het [verificatierapport](verification.md#native-quorumactivering-in-het-tijdelijke-proxmox-lab).
Via de webinterface kun je het plan opslaan en de vaste clusterpakketten op
elke node voorbereiden. De OS-beheerder regelt de witness, fencing en
activering.

1. Open het directe beheeradres van A en kies **Proxmox VE**. Open zo nodig
   **Vereiste netwerkverbindingen** voor de poorten. Voor andere platforms
   verwijst de interface naar de mogelijkheden voor beheer vanuit het OS.
2. Vul de Proxmox-VM-ID’s en de IPv4-adressen van de witness en de Proxmox-API
   in. De node-adressen en de lokale OS-hostnaam komen uit de installatie.
   Vul de OS-hostnaam van de andere node in en klik op **Proxmox-plan
   controleren**.
3. Bekijk **Firewallregels bij activering** als die worden getoond en kies
   **Plan op deze node opslaan**. Download het plan. Kies op B **Proxmox VE**
   en **Importeer het plan van node A**. Vergelijk de
   vingerafdrukken. Sla het plan ook op B op. Gebruik hetzelfde bestand op
   de witness.
4. Wacht tot de opgeslagen vingerafdruk zichtbaar is en kies dan op elke
   node **Clusterpakketten voorbereiden**. Wacht op de bevestiging. Tijdens
   de installatie blijven de nieuwe clusterdiensten inactief en blijft de
   bestaande HA-opstelling in bedrijf. **Volgende stap op deze node** geeft
   aan wanneer je via SSH verdergaat met de witness en de fencingtest.

Bevestig elke wijziging met je wachtwoord en, als 2FA aanstaat, je
authenticatorcode. Met **Opgeslagen plan laden** kun je het plan opnieuw
bekijken. Zodra de pakketvoorbereiding begint, staat het plan op die node vast.

Open in de ontwikkelversie **Witness inschrijven en overstappen op beschermde
HA**. Daar staat per host welke netwerktoegang nodig is en welke controle je
uitvoert. **Lokale witnessinschrijving en Corosync-sleutel vastgelegd** toont
dat de lokale voorbereiding bij het beoordeelde plan hoort. Vóór de migratie
controleert de OS-opdracht het actuele certificaat en vergelijkt deze de
sleutel met de controle op de andere node.

Als de voorbereiding stopt, controleer dan het afgeschermde
servicelog. Je kunt met hetzelfde plan verdergaan.

```bash
sudo journalctl --namespace=proxypermit -u proxypermit-cluster-web.service
```

De OS-beheerder kan hetzelfde plan zonder geheimen ook zelf schrijven:

```json
{
  "version": 1,
  "platform": "proxmox",
  "witness": "192.0.2.13",
  "fenceAddress": "192.0.2.100",
  "nodes": [
    {"name": "proxy-a", "address": "192.0.2.11", "target": "201"},
    {"name": "proxy-b", "address": "192.0.2.12", "target": "202"}
  ]
}
```

Vul de echte gegevens in, met A als eerste en B als tweede. De witness moet
buiten het storingsdomein van beide nodes staan; een ander IP-adres bewijst die
onafhankelijkheid niet. Controleer vanuit de geïnstalleerde applicatiemap eerst
het plan en sla het daarna afzonderlijk op:

```bash
sudo .venv/bin/python -m scripts.deploy.cluster_setup --plan /root/cluster-plan.json --role node
sudo .venv/bin/python -m scripts.deploy.cluster_setup --plan /root/cluster-plan.json --role node --stage
```

Gebruik hetzelfde JSON-bestand op alle drie de hosts. Verwacht
`checkedHost: "a"` op A en `checkedHost: "b"` op B. Gebruik op de witness
`--role witness` in beide opdrachten vanuit de beoordeelde ProxyPermit-applicatiemap.
Daar is het verwachte resultaat `checkedHost: "witness"`. Voor de witness
wordt het eigen geplande adres gecontroleerd. Op A en B worden ook de
ingestelde rol en hostnaam gecontroleerd. Vergelijk `planHash` op alle drie de
hosts voordat je verdergaat. Die moet gelijk zijn, ook als de JSON-opmaak afwijkt.

Voor een gereserveerd cluster geldt de onderhoudsprocedure. Zodra de
voorbereiding van afhankelijkheden is gestart, vereist een ander plan
beoordeling op OS-niveau.

Dit slaat `/etc/proxypermit/cluster-plan.json` op, alleen toegankelijk voor root.
UFW, Keepalived en Pacemaker worden niet gewijzigd. Los een ongesynchroniseerde
klok, ontbrekende pakketten of een ongeschikte authenticatiemethode eerst op.
Begeleide inrichting, toetreding en migratie zijn nog in ontwikkeling.

### Het OS en de witness voorbereiden

Bekijk op beide nodes eerst het plan voor de benodigde pakketten. Voer het
daarna uit. De clusterdiensten blijven tijdens de installatie inactief.

```bash
sudo .venv/bin/python -m scripts.deploy.cluster_bootstrap --role node
sudo .venv/bin/python -m scripts.deploy.cluster_bootstrap --role node --apply
```

Gebruik op de witness `--role witness`. Laat de OS-beheerder vóór het
reserveren van een node de witness met wederzijdse TLS inrichten, beide
Corosync-clients inschrijven en de aparte fencingtoegang controleren.
Leg het uitschakelen en herstellen van beide doelen vast. Houd rechtstreekse
beheertoegang tot elke host open en controleer of je een uitgeschakelde host
via een onafhankelijk kanaal kunt herstellen. De webinterface toont de
lokale controles. De beheerder stemt de stappen op de drie hosts af.

### De witness inschrijven en fencing van beide nodes testen

Initialiseer QNetd op een nieuwe witness met een lege certificaatopslag.
Gebruik op die host de gecontroleerde ProxyPermit-bronmap en houd directe
SSH-toegang beschikbaar. Kopieer de openbare CA via geauthenticeerde SSH naar
A en B.

```bash
sudo corosync-qnetd-certutil -i
sudo python3 -m scripts.deploy.cluster_witness --apply
sudo openssl x509 -in /etc/corosync/qnetd/nssdb/qnetd-cacert.crt -noout -sha256 -fingerprint
```

Maak met `openssl rand -hex 16` één migratie-ID en stel op beide proxynodes
`operation` in op die waarde. Voer de volgende opdracht uit op A en B en
vergelijk de planvingerafdrukken. `cluster` is de naam voor het certificaat.
`directory` geeft de afgeschermde werkmap aan.

```bash
sudo .venv/bin/python -m scripts.deploy.cluster_preflight workspace --operation "$operation"
```

Volg de [Corosync-procedure voor certificaten](https://manpages.debian.org/bookworm/corosync-qdevice/corosync-qdevice-net-certutil.8.en.html).
Initialiseer op elke nieuwe node de NSS-opslag met de witness-CA via
`corosync-qdevice-net-certutil -i -c /root/witness-ca.crt`. Maak op A de
aanvraag met `corosync-qdevice-net-certutil -r -n "$cluster"`. Breng hem
over naar de witness en onderteken hem daar met
`corosync-qnetd-certutil -s -c /root/qdevice-net-node.crq -n "$cluster"`.
Importeer het ondertekende certificaat op A met
`corosync-qdevice-net-certutil -M -c`. Breng de afgeschermde PKCS#12-bundel via
geauthenticeerde SSH over naar B en importeer hem daar met
`corosync-qdevice-net-certutil -m -c`. Geef bij beide imports het bijbehorende
bestand op na `-c`. Gebruik deze stappen voor een nieuw, afzonderlijk
cluster. Laat een bestaande certificaatopslag bij een onderbreking intact
voor controle via het OS. Sommige pakketversies geven op B kort na de
import een fout. Ga pas verder als de lokale ProxyPermit-controle hieronder
het verwachte certificaat en de sleutel bevestigt.

Stel `witness_ca_sha256` in op de waarde na `=` in de uitvoer op de witness.
Voer de certificaatcontrole uit op beide nodes en vergelijk `certificateHash`.
Voer daarna de sleutelopdracht uit op A. Breng het afgeschermde bestand
`authkey` over naar de gemelde werkmap op B en voer de sleutelopdracht ook
daar uit.

```bash
sudo .venv/bin/python -m scripts.deploy.cluster_preflight witness --operation "$operation" --ca-sha256 "$witness_ca_sha256"
sudo .venv/bin/python -m scripts.deploy.cluster_preflight key --operation "$operation"
```

Test tijdens onderhoud de fencing-agent vanaf elke node, één peer tegelijk,
met de ingestelde Proxmox-host en VM-ID. Voer `fence-off` uit vanaf de
node die nog draait zodra de agent de peer heeft uitgeschakeld. Start de
peer via het onafhankelijke Proxmox-beheerkanaal, controleer de lokale
diensten en voer binnen tien minuten `fence-on` uit vanaf dezelfde node.
Deze opdrachten vragen de bevestigde stroomstatus van de geplande peer
op en slaan de uitkomst op. Gebruik bij de migratie een actuele test met
dezelfde agent, toegang en CA.

```bash
sudo .venv/bin/python -m scripts.deploy.cluster_preflight fence-off --operation "$operation"
sudo .venv/bin/python -m scripts.deploy.cluster_preflight fence-on --operation "$operation"
```

### Een onderbroken reservering hervatten

Plan een onderhoudsvenster en houd rechtstreeks toegang tot het
besturingssysteem van beide nodes. Controleer welke node het VIP voert.
Reserveer eerst de andere node, nadat je de afhankelijkheden hebt
voorbereid en fencing hebt getest. Gebruik op beide hosts hetzelfde
operatie-ID. Stel `operation` in op dat gedeelde ID en voer dit uit:

```bash
sudo .venv/bin/python -m scripts.deploy.cluster_native --phase reserve --operation "$operation" --experimental
```

De reserveringsstap geeft een JSON-rapport zodra is bevestigd
dat Keepalived op die node inactief is en het VIP er niet aanwezig is.

Kopieer het JSON-rapport van de stand-bynode via een vertrouwde SSH-verbinding
voordat je de huidige VIP-eigenaar reserveert. Bewaar het op de VIP-eigenaar
als root-only bestand `peer-reserved.json` in
`/var/lib/proxypermit-cluster-bootstrap/<operation>/`. Het rapport is vijf
minuten geldig. Draai zo nodig opnieuw `--phase reserve` op de gereserveerde
stand-bynode voor een actuele controle.
Met het nieuwe bewijs uit de voorbereiding vergelijkt de VIP-eigenaar ook
de vingerafdrukken van de witness-CA, het clientcertificaat en de
Corosync-sleutel voordat Keepalived stopt.

Blijft een node na een onderbreking in de fase `reserving`? Bekijk beide
nodes met `--phase status`, controleer wie het VIP voert en hervat met
hetzelfde operatie-ID. Vanaf het eerste vastgelegde punt blokkeert de
reservering zelfstandige
beheerhandelingen. Is het lokale beschermingsprofiel of de Keepalived-override
gewijzigd, beoordeel dat dan eerst op OS-niveau.
Wacht na een herstart totdat `timedatectl show --property=NTPSynchronized`
`yes` meldt. De reserveringsopdracht controleert dit opnieuw bij het hervatten.

### Een onderbroken HA-activering beoordelen

Voer dit uit op A en B vanuit de geïnstalleerde applicatiemap:

```bash
sudo .venv/bin/python -m scripts.deploy.cluster_native --phase status
```

Vergelijk het operatie-ID, de planhash en de gemelde fase per host. De
opdracht leest de bewaarde voortgang en de actuele clusterstatus zonder die
te wijzigen. Volg de vervolgstap die elke node meldt.

| Vervolgstap | Wat je kunt doen |
| --- | --- |
| `inspect-reservation` | Controleer de VIP-eigenaar, de migratie-ID's en de tijdsynchronisatie. Hervat eerst op de stand-bynode en daarna op de VIP-eigenaar. |
| `transport` | Controleer of Keepalived inactief is en geen node het VIP heeft. Bereid Corosync voor met de gedeelde sleutel en de witnesscertificaten. |
| `start` | Vergelijk het plan op beide nodes. Start vervolgens op elke node het versleutelde clusterverkeer. |
| `inspect-transport-and-pacemaker` | Controleer of beide nodes lid zijn en wederzijdse TLS met de witness werkt. Start Pacemaker en bereid vervolgens op beide nodes de gestopte VIP-resource voor. |
| `activate` | Controleer op beide nodes quorum, fencing en het gestopte VIP. Activeer het VIP één keer en rond de activering af op de andere node. |
| `finalize` | De bedoelde Pacemaker-configuratie is actief en het VIP heeft één gezonde eigenaar. Rond de activering af op de node die deze stap meldt. |
| `retry-activation` | Het VIP staat volgens de bedoelde Pacemaker-configuratie nog stil. De witness en fencing zijn beschikbaar. Controleer beide nodes en probeer het opnieuw op de node die de activering heeft aangevraagd. De opdracht controleert de fencingtest, de onderhoudsreservering en de clusterconfiguratie opnieuw. |
| `verify-fencing-test` | Controleer het bewijs van de fencingtest. Bevestig een actuele test van uitschakelen en herstellen voordat je doorgaat. |
| `inspect-vip-health` of `review` | Onderzoek Pacemaker en de logs op de hosts. Laat Keepalived onder clusterbeheer en bewaar de vastgelegde voortgang. |

Vervang `--phase status` door de gecontroleerde fase (`transport`, `start`,
`pacemaker`, `seed`, `activate`, `finalize` of `retry-activation`) om
die stap uit te voeren. Voeg `--operation "$operation" --experimental` toe
met het migratie-ID dat je op beide nodes hebt gecontroleerd.

Open **Instellingen → Beschermde HA migreren en herstellen** via het eigen
adres van elke node. Je ziet de lokale fase, het operatie-ID en de vingerafdruk
van het plan uit de laatste OS-controle. Vergelijk beide waarden. De fasen
mogen tijdelijk verschillen. Ontbreekt een waarde, wijkt die af of is de
informatie verlopen? Controleer dan eerst beide hosts via SSH. Op een
zelfstandige node begeleidt **HA instellen** je bij het aanmaken op A, het
koppelen van B en het controleren van de synchronisatie.

### Fencingplatformen

| Platform | Inrichting en verificatie |
| --- | --- |
| Proxmox VE met QEMU | De ingebouwde procedure maakt de clusterconfiguratie en beperkte UFW-regels. Gebruik TLS met certificaatcontrole en aparte rechten waarmee elke node alleen de andere VM kan uitschakelen. In het Proxmox-lab zijn migratie, abrupt uitschakelen, fencing bij een netwerkscheiding en releaserollback getest. |
| VMware vSphere | De OS-beheerder configureert `fence_vmware_rest` met gecontroleerde HTTPS en VM-namen die binnen het bereik uniek zijn. Geef het serviceaccount alleen stroombeheer over de toegewezen VM’s. ProxyPermit beoordeelt de clusterconfiguratie. Live fencing op VMware moet nog worden getest. |
| KVM/libvirt | De OS-beheerder configureert `fence_virtd` op de host en `fence_xvm` in het cluster. Beperk toegang tot een apart beheernetwerk en koppel elke node aan de juiste VM-UUID. ProxyPermit herkent de fencingagent op een getest OS-beheerd cluster. De ingebouwde inrichting is voor Proxmox. |
| Fysieke servers | Gebruik per server één rechtstreeks bereikbare BMC met `fence_ipmilan`, IPMIv2 LAN+, ciphersuite 17 en de methode `onoff`. Isoleer het BMC-netwerk. Deelt de BMC de voeding met de server, zorg dan voor een fencingpad met een onafhankelijke voeding en test uitval van de voeding. ProxyPermit beoordeelt de clusterconfiguratie. Hiervoor is nog geen hardwareproef uitgevoerd. |

Richt bij OS-beheerde adapters Corosync, een witness met wederzijdse TLS,
Pacemaker en de ProxyPermit VIP-resource afzonderlijk in. Test of de andere
node precies het bedoelde doel kan uitschakelen en uit kan houden. Controleer
ook of je het via onafhankelijke toegang kunt herstellen. Een beschikbare
agent in de clusterconfiguratie bewijst dit nog niet. Zie ook de
[netwerk- en voedingseisen voor fencing van Pacemaker](https://clusterlabs.org/projects/pacemaker/doc/3.0/Pacemaker_Explained/html/fencing.html#fence-devices).
De uitkomsten van de live KVM/libvirt-proef staan in het [verificatierapport](verification.md).

Zie de upstream [metadata voor VMware REST](https://github.com/ClusterLabs/fence-agents/blob/main/tests/data/metadata/fence_vmware_rest.xml)
en de [IPMI-fencingagent](https://github.com/ClusterLabs/fence-agents/blob/main/agents/ipmilan/fence_ipmilan.py).

### Waarom het VIP terug kan komen op A

Bij beschermde HA kiest Pacemaker bij een nieuwe plaatsing bij voorkeur A.
Als B het VIP na overname gezond bedient, kan B het ook blijven voeren
wanneer A terug is. In het ingebouwde profiel heeft A een plaatsingsscore
van `100` en krijgt de huidige eigenaar een behoudscore van `1000`.

Komt het VIP terug op A? Vergelijk het moment van de wissel met de monitor- en
stopmeldingen op B. In de [Proxmox-proef](verification.md#vip-van-b-naar-a-na-een-monitortime-out)
stopte Pacemaker de resource op B na een monitortime-out en herstelde die op A.
Door de ingestelde migratiedrempel van `1` kwam B na die fout niet meer in
aanmerking voor het VIP. Toen een latere monitorfout ook A uitsloot, voerde
geen van beide nodes het VIP. Controleer de fouttellers op beide nodes,
ook als beide hosts online zijn. Zie [Plaatsing in Pacemaker](https://clusterlabs.org/projects/pacemaker/doc/3.0/Pacemaker_Explained/html/constraints.html)
en [afhandeling van fouten](https://clusterlabs.org/projects/pacemaker/doc/3.0/Pacemaker_Explained/html/operations.html).

### Een gestopt of haperend VIP onderzoeken

Maak via SSH verbinding met het eigen adres van elke node. Vergelijk welke
nodes online zijn, waar het VIP staat en wat er rond de eerste fout is gelogd.

```bash
sudo crm_mon --one-shot --failcounts
sudo corosync-quorumtool -s
sudo journalctl --namespace=proxypermit -u pacemaker.service -u keepalived.service --since "30 minutes ago"
```

Bij een volledige clusterherstart geldt in het ingebouwde Proxmox-profiel
`wait_for_all: 1`. Voordat er voor het eerst quorum is, moeten beide proxynodes
elkaar zien, ook als één node de witness al bereikt. Herstel de ontbrekende
node of volg de herstelprocedure van het OS-cluster. Laat het VIP tijdens het
herstel onder clusterbeheer. Zie [Corosync votequorum](https://manpages.debian.org/bookworm/corosync/votequorum.5.en.html).

De VIP-agent meldt of Keepalived een onverwachte status had, het adres ontbrak
of de lokale Tinyproxy-controle mislukte. Bij een time-out kan Pacemaker de
agent al hebben gestopt voordat die een resultaat kon melden. Los de oorzaak
op en controleer quorum en fencing voordat je Pacemaker het VIP laat
herstellen. Keepalived blijft onder beheer van Pacemaker.

Sluiten oude monitorfouten beide nodes uit, leg die fouten dan eerst vast.
Controleer quorum, de verbinding met de witness, fencing en de lokale proxy.
Laat Pacemaker daarna alleen de mislukte monitor op één geschikte node opnieuw
proberen. Vervang `proxy-node-a` door de clusternaam van die node.

```bash
sudo crm_resource --cleanup --resource proxypermit-vip --node proxy-node-a --operation monitor --interval 5s
```

Controleer op beide nodes of precies één node het VIP voert. Test daarna de
proxy vanaf een client. Deze procedure is getest in het
[Proxmox-lab](verification.md#vip-herstellen-en-een-losgekoppelde-node-verwijderen-op-proxmox).

Een clusterupgrade per node werkt de geïnstalleerde VIP-agent bij als die
overeenkomt met de vorige ProxyPermit-release. De OS-beheerder controleert
en actualiseert een aangepaste agent zelf.

### Herstellen na het verlopen van een lokaal Proxmox-fencingtoken

Meldt Instellingen dat het lokale fencingtoken is verlopen? Gebruik het eigen
beheeradres van die node en laat de node het VIP pas weer overnemen nadat de
bescherming is hersteld. Laat de OS-beheerder het token op het goedgekeurde
Proxmox-eindpunt vernieuwen, met rechten voor alleen de andere VM. Houd het
lokale profiel uitsluitend toegankelijk voor root, behoud de vertrouwde CA en
controleer aan welke VM het token gekoppeld is. Controleer de geauthenticeerde
agentstatus en vergelijk op beide nodes quorum, fencing en VIP-eigendom. Is er
een migratie in voorbereiding, herhaal dan met het nieuwe token de controles
voor uitschakelen en herstellen.

### Fencing-toegang voor een Proxmox-lab (experimenteel)

De optionele helper `scripts/dev/proxmox_fencing.py` draait op de Proxmox-host.
Standaard toont hij alleen de voorgestelde bevoegdheden. Met expliciet `--apply`
maakt hij een nieuwe gebruiker, rol en twee tokens met gescheiden rechten.
Ieder token mag alleen de andere VM beheren, binnen de gecontroleerde bestaande
pool. Bestaande accounts en uitvoermappen worden geweigerd. De helper controleert
de effectieve rechten voordat de bestanden gedeeld kunnen worden. Na een fout
blijft gedeeltelijke toestand behouden voor controle door root. De helper
schakelt zelf geen VM uit.

De rol bevat `VM.Audit` en `VM.PowerMgmt`. Die laatste Proxmox-bevoegdheid staat
ook starten en resetten van die andere VM toe. Er is geen toegang tot hostbeheer
of andere guests. Labtokens verlopen standaard na 14 dagen; regel tijdig
vervanging. Deze helper verzorgt geen automatische tokenrotatie voor productie.

Sommige distributieversies van `fence_pve` ondersteunen geen API-tokens.
De optionele module `scripts.deploy.proxmox_fence` installeert een afzonderlijk
benoemde, vastgepinde ClusterLabs-variant na controle van de bronhash. Zie
[de opgenomen broncode en licentie](https://github.com/Dantdmnl/ProxyPermit/tree/main/deploy/fence-agents).
De applicatieaccount mag `/etc/proxypermit/proxmox-fencing.json` niet lezen.
Dat root-only profiel bevat `version`, `host`, `pveNode`, het fencing-doel van
de andere node (`target`), `expires`, `tokenId` en `token`. Het vaste openbare
CA-bestand is `/etc/proxypermit/proxmox-fencing-ca.pem`. De hostnaam moet
overeenkomen met het certificaat. Schakel certificaatcontrole nooit uit en
zet tokens niet in de clusterconfiguratie of commandoregelargumenten.

Deze variant staat alleen statusopvragen, monitoren en uitschakelen van de
ingestelde andere VM toe. Zonder `--apply` toont de installatie alleen een plan;
installeren vereist expliciet `--apply`. De distributie-agent blijft behouden.
Test daadwerkelijk uitschakelen en herstellen via deze agent voordat een
beschermd cluster wordt geactiveerd. Een kloppende bronhash en metadata bewijzen
nog niet dat fencing werkt.
