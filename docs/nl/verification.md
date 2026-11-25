# Verificatierapport

[English](../en/verification.md) · [Documentatie](README.md)

## Acceptatieproeven voor beschermde HA in v1.4.0

Gebruik een testopstelling die je kunt herstellen, met directe toegang tot
beide proxynodes, onafhankelijke toegang tot de witness en een manier om
uitgeschakelde hosts weer op te starten. Noteer per proef de versie, het
platform, de begin- en eindtijd in UTC, de tijd tot clientverkeer herstelt,
het resultaat van fencing en welke node het VIP had volgens beide hosts.

| Scenario | Wanneer slaagt de proef? | Tot nu toe getest |
| --- | --- | --- |
| Uitval van de actieve host of voeding | De andere host houdt quorum, schakelt zo nodig precies de juiste host uit en bedient het VIP. Test vanaf een client zowel een toegestane als een geblokkeerde bestemming. | Abrupte uitval van een VM op Proxmox is getest. Uitval van een onafhankelijke fysieke host of voeding staat nog open. |
| Netwerkscheiding tussen nodes en witness | Hooguit één node voert het VIP. Heeft geen van beide quorum, dan voert geen van beide het VIP. Valt ook het fencingpad weg terwijl fencing nodig is, controleer dan of de andere node wacht. | Netwerkscheiding en verlies van quorum zijn op Proxmox getest. Op libvirt is een bevroren VIP-eigenaar met een onbereikbare fencer getest. |
| Stand-bynode keert terug na lange afwezigheid | Houd directe beheertoegang en controleer tijd, witnesscertificaat en fencingauthenticatie voordat de node mag overnemen. Vergelijk het actieve beleid en controleer of B de nieuwste revisie toepast. Test ook een afwezigheid waarbij de geldigheid van de inloggegevens verloopt. | Terugkeer, het inhalen van beleid en het weigeren van verlopen fencingtokens en witnesscertificaten zijn afzonderlijk getest. Herstel en overname na een afwezigheid waarin die gegevens zijn verlopen staan nog open. |
| Uit gebruik nemen, verwijderen en herstellen | Controleer of de andere node het VIP blijft bedienen en directe SSH-toegang behouden blijft. Vergelijk de firewallregels. Onderbreek de actie, hervat die en controleer of verwijderen pas kan na afronding. | Lokaal loskoppelen, uit bedrijf nemen en de console verwijderen zijn getest op Proxmox. Geïsoleerde regressietests dekken onderbreken en hervatten. Een lid uit de clusterconfiguratie verwijderen en vervangen staat nog open. |

Herhaal de relevante proeven per platform en per gedeeld uitvalpunt. Op één
hypervisor kun je uitval van een VM en netwerkscheiding testen. Voor uitval
van een hele host is onafhankelijke infrastructuur nodig.

## 25 november 2026

### Onderbroken beleidspublicatie gevolgd door een herstart van Tinyproxy

Op de Ubuntu QEMU-VM is het publicatieproces in twee afzonderlijke proeven
afgebroken met `SIGKILL`. Na afbreken tussen het vervangen van de configuratie
en de filter stonden de nieuwe configuratie en de oude filter samen op schijf.
Na afbreken direct na het vervangen van beide bestanden gaf het draaiende
Tinyproxy-proces nog HTTP 200 voor de eerder toegestane bestemming. Na een
herstart las Tinyproxy de onbevestigde filter van schijf en werd dat HTTP 403.
Expliciet terugzetten vanuit de herstelkopieën herstelde in beide gevallen
het oorspronkelijke beleid. Deze eerste proef liet zien dat automatisch
herstel vóór de start van Tinyproxy ontbrak.

### Herstel vóór de start van Tinyproxy

De zelfstandige QEMU-node is met de normale installer bijgewerkt. Het
publicatieproces is afgebroken vóór het vervangen van de filter, erna en
nadat het een herstart van Tinyproxy had uitgevoerd. Bij de volgende
systemd-start kwamen de oorspronkelijke configuratie en filter in alle
drie de gevallen terug voordat Tinyproxy startte. Beide bestanden kwamen
overeen met de beginsituatie. De toegestane bestemming gaf weer HTTP 200
en de geblokkeerde bestemming HTTP 403.

De disposable QEMU-VM is ook hard uitgezet nadat beide onbevestigde
bestanden waren geschreven. Na het opstarten kwamen beide bestanden overeen
met de checksums die vooraf op de host waren vastgelegd. Tinyproxy gaf weer
HTTP 200 en 403 voor dezelfde bestemmingen. Het logboek liet zien dat het
herstel vóór de start van Tinyproxy draaide.

### Status van het beschermde Proxmox-lab

Node A had geen quorum en voerde het VIP niet. De witness was verbonden
en leverde een stem, maar Corosync meldde `WaitForAll` en blokkeerde
clusteractiviteit. De nieuwste geregistreerde VIP-monitortime-out viel
samen met IPC-time-outs in Pacemaker. VM 8107 weigerde nog SSH, zodat
inschrijving en VIP-overname op die vervanger niet konden worden getest.
Het clusterbeleid en de fencinginstellingen bleven behouden.

### Vol bestandssysteem tijdens beleidspublicatie

Een afzonderlijk Tinyproxy-proces op de Ubuntu QEMU-VM gebruikte
beleidsbestanden op een tmpfs van 160 KiB. Na het vervangen van de
configuratie is het bestandssysteem gevuld. Het schrijven van de filter
mislukte met `ENOSPC`. ProxyPermit zette beide oorspronkelijke bestanden terug
uit de vooraf klaargezette kopieën. Hetzelfde Tinyproxy-proces bleef draaien
en gaf HTTP 200 voor de toegestane en HTTP 403 voor de geblokkeerde bestemming.
De API-regressietest bevestigde ook dat het filterconcept beschikbaar bleef
en na het vrijmaken van ruimte alsnog kon worden gepubliceerd.

### Herstel na een verlopen beheercertificaat

De zelfstandige QEMU-kandidaat serveerde tijdelijk een verlopen
servercertificaat dat was uitgegeven door de bestaande node-CA. Een client
die die CA vertrouwde, weigerde het verlopen certificaat. De normale
vernieuwingsopdracht zag dat vernieuwing nodig was, gaf een nieuw
servercertificaat uit en herstelde de HTTPS-bereikbaarheidscontrole.
Dezelfde client accepteerde het nieuwe certificaat met de bestaande CA.

### Herstart met actieve UFW en HTTPS-beheer

Op dezelfde kandidaat is gecontroleerd of de opgeslagen firewallregel het
werkelijke SSH-bronadres toeliet. Daarna is UFW ingeschakeld met een
standaardblokkade voor inkomend verkeer. Na een normale herstart werkte een
nieuwe SSH-sessie terwijl UFW actief was. De beheerinterface antwoordde via
HTTPS met dezelfde CA, de vernieuwingstimer was actief en Tinyproxy gaf nog
steeds HTTP 200 voor de toegestane en HTTP 403 voor de geblokkeerde bestemming.

## 24 november 2026 om 17:39–17:53 UTC

### Releasearchief op een zelfstandige Ubuntu 24.04.5-node

De proef draaide op twee schone QEMU-VM’s. De checksum van het gepubliceerde
v1.3.0-archief klopte. Van het lokaal gebouwde kandidaatarchief voor
`1.4.0-dev.2` klopten zowel de checksum als de hashes van de afzonderlijke
bestanden. SHA-256 van het kandidaatarchief:

```text
e18430bba5ca7d5455ad3be8eff441519944aacabab057c0c23357a1db709cf1
```

- Op beide schone installaties werkte aanmelden. Tinyproxy gaf HTTP 200 voor
  de toegestane bestemming en HTTP 403 voor de geblokkeerde bestemming.
- Na de upgrade vanaf v1.3.0, rollback naar v1.3.0 en een tweede upgrade
  werkten aanmelden en beide proxyverzoeken. De checksum van de filterregels
  bleef gelijk.
- Na het verwijderen van de kandidaat stopte de beheerconsole. Tinyproxy en
  een nieuwe SSH-sessie bleven beschikbaar. De filterregels en geregistreerde
  UFW-beheerregels kwamen overeen met die van vóór de upgrade.
- Op de andere VM is de beheerconsole op dezelfde poort overgezet naar HTTPS.
  Aanmelden werkte met de lokale CA en een beveiligde sessiecookie. Geforceerde
  vernieuwing verving het servercertificaat en behield dezelfde CA.

De kandidaatinstaller draaide alle 394 Python-tests als root, zonder
overgeslagen tests. Herhaal deze controles met het definitieve stabiele archief.

## 24 november 2026 om 17:08–17:16 UTC

### Vervanger uitschakelen en het VIP herstellen op Proxmox

De oude, losgekoppelde B is regulier afgesloten. De schone vervanger startte
op het adres van B en was via SSH bereikbaar met de gecontroleerde
hostsleutel. Hij draaide zonder clusterdiensten of VIP. Het testtoken
schakelde de VM uit via de Proxmox-API. Het aparte labaccount startte hem
weer en Proxmox meldde dat de VM draaide. De VM antwoordde met het
verwachte MAC-adres, maar de SSH-poort weigerde verbindingen en de
netwerkcontrole via de guestagent mislukte. Het herstellen van toegang
tot de VM staat nog open.

A behield quorum met de witness. Tijdens de proef gaf de VIP-monitor een
fout zonder nadere reden en stopte Pacemaker het VIP. De lokale proxytest
slaagde en fencing bevestigde dat de oude peer uit stond. Nadat alleen de
fout van A's vijfsecondenmonitor was gewist, herstelde Pacemaker het VIP.
Een proxyverzoek vanaf de witness gaf daarna de verwachte HTTP 403. De
oorzaak van de monitorfout staat nog open. De aangepaste agent rapporteerde
op A een gesimuleerde fout in het formaat van Pacemaker. De actieve
Pacemaker-configuratie en de geïnstalleerde agent zijn behouden.
Clusterinschrijving en VIP-overname op de vervanger zijn nog niet getest.

## 24 november 2026 om 17:04–17:06 UTC

### Voorcontrole voor vervanging van node B op Proxmox

A en de witness hadden quorum met twee van de drie stemmen. Het VIP stond
nog op A. Er is een schone vervangende VM aangemaakt. Die bleef uit omdat
hij dezelfde netwerkinstellingen heeft als B. De losgekoppelde oude VM is
bewaard voor herstel. Toen in een lokaal proefplan alleen het VM-ID van B
werd aangepast, veranderde de gegenereerde Corosync-clusternaam. Die naam
wordt ook gebruikt voor inschrijving bij de witness. De procedure voor
eerste installatie kan daarom niet worden hergebruikt voor deze wijziging
op een actief beschermd cluster. Elk nieuw fencingtoken kon via de
Proxmox-API de status van de andere VM opvragen. De API weigerde
statusaanvragen voor de eigen VM, de oude B en de witness. Het
clusterlidmaatschap, de inschrijving bij de witness en de actieve fencing
zijn ongemoeid gelaten. Uitschakelen via fencing, vervanging en overname
moeten nog worden getest.

## 24 november 2026 om 16:46–16:51 UTC

### VIP herstellen en een losgekoppelde node verwijderen op Proxmox

Beide nodes waren online met drie stemmen, terwijl bewaarde monitorfouten
het VIP gestopt hielden. De Tinyproxy-controle en de fencingstatus voor de
andere VM slaagden op beide nodes. Nadat alleen de fout van de VIP-monitor met
een interval van vijf seconden op A was gewist, herstelde Pacemaker het VIP
op A. Een proxyverzoek vanaf de witness kreeg de verwachte blokkade. De
oorzaak van de oorspronkelijke time-outs staat nog open.

Rond de VIP-time-out op B meldde Pacemaker dat zijn scheduler, controller,
executor en andere lokale processen niet op IPC reageerden. Op A miste de
scheduler een IPC-controle vóór de VIP-time-out en deed de controller dat
rond de fencingtime-out op 24 november. De time-outs vielen dus samen met
vertraging in meerdere clusterprocessen. De logs geven geen oorzaak voor
die vertraging.

B is in standby gezet en de lokale clusterdiensten zijn gestopt en permanent
gemaskeerd. De installer weigerde verwijdering totdat de lokale uitbedrijfname
was voltooid. Die ruimde zeven geregistreerde HA-regels op en behield de
beheerconsole en de vaste SSH- en beheerregels. Herhalen gaf geen wijzigingen.
De uninstaller liet daarna het beleid, de dienstmaskers en de OS-fencingagent
staan. Bij elke controle hield A quorum met de witness en bediende het VIP.
Na verwijdering werkte een nieuwe SSH-verbinding met B. Het clusterlidmaatschap
is voor herstel behouden.

## 24 november 2026 om 16:28–16:35 UTC

### Upgrade en rollback op een Keepalived-paar met HTTPS

Op twee Ubuntu 24.04-VM’s in VMware is eerst B bijgewerkt, teruggezet vanuit
de back-up van de installer en opnieuw bijgewerkt. Daarna volgde A. A draaide
met Granian en de lokale CA. B draaide met Gunicorn en een eigen PEM-certificaat.
Bij de eerste upgrade las een zelftest de systemd-unit van de actieve host.
De installer hield de vorige release in bedrijf. Na het isoleren van die
test slaagden alle 388 installatietests op de VM’s.

Beide consoles antwoordden na de upgrade via HTTPS. Het certificaat of de
vertrouwde CA en de accountbestanden, filterregels en firewallregels bleven
behouden. B bevestigde de volledige beleidsrevisie van A. A voerde het VIP
voor en na de proef. Deze proef betrof gewone HA met Keepalived.

## 23 november 2026 om 12:38–16:44 UTC

### VIP van B naar A na een monitortime-out

| Tijd (UTC) | Gebeurtenis in Pacemaker |
| --- | --- |
| 12:38:35 | De VIP-monitor op B overschreed zijn time-out van tien seconden. Pacemaker zette de foutteller op `1`, sloot B uit en plande herstel op A. |
| 12:38:42 | Na het stoppen van de resource op B bevestigde Pacemaker dat het VIP op A draaide. |
| 12:53:44–12:53:46 | De monitor op A liep ook tegen zijn time-out aan. B was nog uitgesloten, waarna Pacemaker het VIP op A stopte. |
| 16:44 | Beide hosts waren online. Pacemaker meldde nog steeds dat het VIP gestopt was en beide fouttellers bleven staan. |

In het lab had A een voorkeurswaarde van `100`, kreeg de huidige eigenaar
een behoudscore van `1000` en gold een migratiedrempel van `1`. De wissel
naar A was herstel na de monitorfout op B. De oorzaak waardoor het
monitorproces zijn time-out overschreed, staat nog open.

## 23 november 2026 om 12:15–12:18 UTC

### Herstelcommando’s en een stilgevallen Proxmox-VIP

De nieuwe opdracht `cluster_maintenance --status` werkte op beide toegewezen
Proxmox-nodes en meldde dat er geen reservering actief was. De controles voor
uit gebruik nemen en de-installatie weigerden de nog aangesloten stand-bynode.
Het lab draaide een oudere ontwikkelversie en het proxy-VIP was al gestopt.

Pacemaker toonde eerdere fouten van de VIP-monitor op beide nodes. Beide
lokale Tinyproxy-controles slaagden, quorum en fencing waren bevestigd en er
was geen onderhoudsreservering. Alleen de fouttellers van de VIP-resource
zijn gewist. Pacemaker bracht het VIP terug. Om 12:17:58 UTC bevestigden beide
nodes dat alleen B het VIP voerde en dat de proxypoort bereikbaar was. De
oorzaak van de eerdere monitorfouten is nog open. In deze proef zijn beide
nodes aangesloten gebleven en is er niets gede-installeerd.

## 23 november 2026 om 11:56–12:04 UTC

### Verlopen Proxmox-fencingtoken

De echte fencingagent op de stand-bynode bevestigde met het bestaande token
dat de andere VM draaide. Met een verlopen kopie in een private mount-namespace
weigerde dezelfde uitleesactie. Het actieve profiel bleef byte voor byte gelijk
en de andere node bleef het VIP bedienen. Geautomatiseerde tests controleren
ook dat de beheerinterface een verlopen lokaal token meldt wanneer de gedeelde
fencer nog gereed lijkt. Met actuele gegevens van Corosync en Pacemaker
meldde de nieuwe statuscontrole `protected` bij het echte profiel,
`degraded` bij de verlopen kopie en weer `protected` bij het echte profiel.
Bij alle drie de controles had het VIP één eigenaar.

Op dezelfde stand-bynode accepteerde OpenSSL de publieke witness-clientketen
op de huidige tijd. Met een verificatietijd na afloop van het clientcertificaat
werd de keten geweigerd. De proxy op het VIP bleef bereikbaar. Beide proeven
testen het verlopen van gegevens zonder het actieve token, certificaat of de
klok te wijzigen. De hersteltijd na een echte offlineperiode is hier niet gemeten.

## 23 november 2026 om 11:28–11:35 UTC

### Fencing en terugkeer op KVM/libvirt

In het tijdelijke OS-beheerde libvirt-lab voerde A aanvankelijk het VIP. Nadat
A om 11:29:17 UTC was gepauzeerd, bevestigde de host dat fencing de VM had
uitgeschakeld voordat B het VIP overnam. De TCP-proxypoort was 19,1 seconden
na de pauze weer bereikbaar. A kwam terug zonder het VIP over te nemen.
Beide nodes zagen één VIP-eigenaar en konden de proxypoort bereiken.

Na terugkeer meldde qdevice op A nog twee verwachte stemmen, terwijl votequorum
er drie toonde en TLS met de witness actief was. Met de aangepaste statuscontrole
bevestigden beide live nodes hun bescherming op basis van de actuele stemtelling.
Regressietests dekken ook ontbrekend quorum en ontbrekende TLS-clientauthenticatie.

Om 11:34:36 UTC is de labfencer gestopt en is de nieuwe VIP-eigenaar B
gepauzeerd. Tijdens 24 seconden controle nam A het VIP niet over. Na herstel
van de fencer was B uitgeschakeld en was de proxypoort binnen zeven seconden
via A bereikbaar. Na terugkeer van B meldden beide nodes één eigenaar en een
bereikbare proxy. De witness en de VM’s stonden tijdens deze tests op dezelfde
hypervisor.

## 23 november 2026 om 11:12–11:17 UTC

### Een ontbrekende Proxmox-node terugbrengen

In het bestaande beschermde lab met `v1.4.0-dev.1` draaide B en was de witness
bereikbaar terwijl A uit stond. B meldde geen quorum en geen VIP-eigenaar.
Na het starten van A om 11:12:21 UTC waren quorum en beide Pacemaker-resources
om 11:16:37 weer beschikbaar. Om 11:17 voerde alleen A het VIP. Beide nodes
meldden beschikbare fencing, konden de proxy-TCP-poort op het VIP bereiken en
hadden dezelfde hash van het filterbestand. De meetmomenten geven een
bovengrens voor de hersteltijd. De duur van de eerdere uitval en terugkeer
na het verlopen van certificaten of tokens vielen buiten deze proef.

## 22 november 2026

### Live controle van de HA-voorbereiding

Op het Proxmox-proefpaar controleerde de voorbereidingsopdracht beide
NSS-certificaatopslagen tegen de CA van de witness. Beide nodes gaven
dezelfde vingerafdruk voor het clientcertificaat en weigerden een
afwijkende CA-vingerafdruk. In een aparte proef schakelde de peer-gebonden
fencing-agent de stand-by-VM uit. De nieuwe opdracht legde de geverifieerde
stroomstatus vóór en na de onafhankelijke herstart vast. De actieve node
behield het virtuele IP.

Regressietests dekken ontbrekend of afwijkend bewijs vóór reservering,
gewijzigd of verlopen fencingbewijs en uitsluiting van de bewijsbestanden
uit releasearchieven.

### Netwerkscheiding zonder quorum

In het lab is alleen het verkeer naar de witness en tussen de Corosync-peers
geblokkeerd. SSH bleef bereikbaar. Toen beide nodes hun quorum verloren,
voerde geen van beide het virtuele IP. Na het verwijderen van de tijdelijke
regels meldden beide nodes weer bescherming. Het VIP had één eigenaar en
de configuratie, filterregels en accounts waren ongewijzigd. De proef draaide
op het bestaande beschermde Proxmox-cluster met één gedeelde host.

### Beschermde HA voorbereiden via Instellingen

De browsertests controleren het plan en de firewallvoorvertoning, bevestigen
de lokale opslag, bereiden de pakketten voor en laden de opgeslagen velden
opnieuw. De geïsoleerde Linux-helper slaat een plan op dat alleen root kan
lezen en leest het terug. De tests dekken verlopen of gewijzigde plannen,
de node-identiteit, gebruikersrollen, CSRF, herauthenticatie en dubbele
verzoeken. De systemd-unit en het ene toegestane sudo-commando zijn op Linux
gecontroleerd. Bij deze proef is de installatie van pakketten gesimuleerd.

### Een bestaand HA-paar migreren op Proxmox

Een afzonderlijk lab met twee proxynodes en een witness-VM is gemigreerd van
Keepalived naar beschermde HA met Pacemaker. Het lab begon met een gekoppeld
HA-paar waarop de actieve filterregels al waren gesynchroniseerd. Elke node
heeft zijn peer via Proxmox uitgeschakeld. Na elke proef is de uitgeschakelde
VM via de onafhankelijke labtoegang weer gestart.

De overstap is op B onderbroken vlak vóór Keepalived zou stoppen en daarna
hervat met dezelfde migratie-ID. A behield het VIP zolang B nog niet klaar
was voor de overstap. A weigerde verder te gaan toen zijn klok na een
herstart nog niet gesynchroniseerd was. Na kloksynchronisatie weigerde A
ook een verlopen bevestiging van B. Met een verse bevestiging konden beide
nodes de overstap afronden. Beide meldden quorum, een verbonden witness en
beschikbare fencing. Precies één node voerde het VIP. Vanaf beide nodes
was de proxypoort op het VIP bereikbaar. De filterregels, Tinyproxy-configuratie
en lokale accounts waren op beide nodes ongewijzigd.

Bij het tijdelijk stoppen van de witnessdienst behielden beide nodes quorum
en bleef het VIP op dezelfde node. Na het herstarten van de dienst meldden
beide nodes weer volledige bescherming. Alle drie de VM’s draaiden op één
Proxmox-host. Volledig hostverlies, netwerkpartities en het terugkeren van
een node na langdurige uitval vielen buiten deze proef.

### Uitval van de actieve node na migratie

In het beschermde lab schakelde de fencing-agent op B de actieve Proxmox-VM
A abrupt uit. De onafhankelijke Proxmox-API bevestigde dat A uit stond.
B nam het VIP over met quorum en accepteerde een TCP-verbinding op de
proxypoort. Binnen 17 seconden na het uitschakelcommando was de overname
waargenomen. Na de herstart van A meldden beide nodes weer bescherming en
precies één VIP-eigenaar. De lokale accounts, Tinyproxy-configuratie en
filterregels waren ongewijzigd.

De bijgewerkte resource-agent is op beide draaiende VM’s in alleen-lezen
monitormodus uitgevoerd. Hij herkende het werkende VIP op A en de inactieve
dienst op B. De uitvalproef draaide met de eerder geïnstalleerde clusteragent.

### Netwerkscheiding en releaserollback

In het Proxmox-lab vielen alleen het Corosync-verkeer van de actieve node naar
zijn peer en de TLS-verbinding met de witness weg. Rechtstreekse SSH en de
Proxmox-API bleven bereikbaar. Pacemaker liet de geïsoleerde node uitschakelen.
De onafhankelijke Proxmox-API bevestigde dat die uit stond. De andere node
hield het VIP met quorum en accepteerde een TCP-verbinding op de proxypoort.
Na herstel meldden beide nodes weer bescherming. Configuratie, filterregels
en lokale accounts waren ongewijzigd.

In hetzelfde lab is B via een clusterreservering bijgewerkt en daarna
teruggezet naar de applicatiebron uit het bewijs van die upgrade. A behield
het VIP. Beide nodes meldden bescherming en de hashes van de Tinyproxy-
configuratie, filterregels en lokale accounts waren ongewijzigd.

### Terugkeer van B en beleidssynchronisatie

B keerde terug in het beschermde lab na ruim vijf minuten offline te zijn
geweest. In een aparte proef stond B opnieuw uit, ditmaal langer dan de
geldigheid van de statusgegevens. A behield het VIP en publiceerde een
nieuwe revisie met een tijdelijke opmerking in de filterlijst. Na terugkeer
nam B die revisie over zonder het VIP op te eisen. A publiceerde daarna
het oorspronkelijke beleid opnieuw. De Tinyproxy-configuratie, filterregels
en lokale accounts kwamen op beide nodes weer overeen met de beginsituatie.

De VM’s draaiden op dezelfde Proxmox-host. Langere uitval waarbij een
certificaat of fencingtoken verloopt, verlies van de hele host en live
fencing op andere platformen moeten nog worden getest.

### Beschermde HA voorbereiden en het oorspronkelijke lab herstellen

De huidige ontwikkelversie is lokaal getest. De planner in Instellingen
gebruikte de ingestelde adressen van A en B, leverde een download in het
JSON-formaat van de OS-validator en weigerde een witness op het VIP.
De browsertests dekten beide talen, thema’s en schermgroottes. De
rechtentests bevestigden dat alleen een systeembeheerder met een geldig
CSRF-token een plan kan laten valideren.

In het bestaande Proxmox-lab stond het VIP stil nadat time-outs van de
resource-monitor op beide nodes een foutteller hadden achtergelaten. Beide
nodes meldden quorum, een verbonden witness, beschikbare fencing en geen
VIP-eigenaar. Na een controle van Tinyproxy op beide nodes en van de
onderhoudsreservering zijn de geregistreerde fouten voor de VIP-resource
gewist. Pacemaker herstelde het VIP op A. Beide nodes meldden daarna
bescherming en precies één eigenaar. De zelfstandige lab-VM kon Tinyproxy
op het VIP bereiken. De oorzaak van de oorspronkelijke time-outs is nog
niet bevestigd. Bij een latere controle waren er nieuwe monitorfouten op
beide nodes en stond het oorspronkelijke VIP opnieuw stil. De migratie-
en uitvalproeven draaiden in het aparte lab.

## 18 november 2026 — beheer tijdens een VIP-overname

Tijdens onderhoud in het ontwikkellab verhuisde het gedeelde beheeradres van A
naar B. HTTPS werd bij de geslaagde verzoeken gecontroleerd met de CA-bundel van
het lab. B weigerde verzoeken vanuit een oude pagina van A en accepteerde de
sessiecookie van A niet. Aanmelden met het lokale beheerdersaccount van B gaf
weer toegang via hetzelfde adres. De omgekeerde overname is ook getest in een
Nederlandstalige mobiele browser. Het geopende formulier behield de invoer
totdat de beheerder de console opnieuw opende. Daarna toonde de browser A en
slaagde de aanmelding met een tijdelijk lokaal account en 2FA. Het testaccount
is na de proef verwijderd.

Het ontwikkelwerk hieronder zit in **v1.4.0-dev.1**, een prerelease voor
testgebruik. Historische vermeldingen beschrijven de stand tijdens die proef.
De laatste kandidaatcontroles slaagden met 342 Python-tests op Linux,
Windows-tests met verwachte platformskips, beide browserproeven voor instellingen
en HA, workflowlint, installerplannen en de strikte documentatiebuild. Zie de
[release notes](https://github.com/Dantdmnl/ProxyPermit/releases/tag/v1.4.0-dev.1)
voor de inhoud en bekende beperkingen; v1.3.0 blijft de stabiele release.

## 17 november 2026 — beheerbeveiliging en clustervoorbereiding

### Clustervoorbereiding en Proxmox-fencing

De volgende build voegt verlopende, door root verzamelde OS-controles toe en
een afzonderlijk voorbereid node-/witnessplan zonder geheimen. Browserproeven
controleren NL/EN, beide thema’s, mobiel/desktop, statusverval en toegang alleen
voor systeembeheerders. Dit stelt Pacemaker, een witness of quorum nog niet in.
Een gekopieerd plan telt nooit als bewijs dat fencing werkt. De volledige
Linux-rootproef slaagt met 324 tests.

In het Proxmox-lab maakte de host-roothelper twee tokens met gescheiden rechten.
De effectieve bevoegdheden zijn gecontroleerd: ieder token mag alleen de andere
HA-VM beheren, zonder hostbeheer of toegang tot andere guests. Na expliciete
toestemming zijn de tokens alleen in lokale root-only bestanden geplaatst.
De afzonderlijk benoemde, via de bronhash vastgepinde ClusterLabs-agent
controleerde de Proxmox-CA en certificaathostnaam. Geïsoleerde tests weigeren
andere doelen en uitgeschakelde TLS-controle.
Praktijkproeven weigerden ook een verkeerde CA en een verkeerd doel. De juiste
CA is hersteld; foutuitvoer bevatte geen token.

De echte agent op A schakelde B uit. In een afzonderlijke proef schakelde B
vervolgens A uit. De hypervisor bevestigde beide gestopte VM’s, de overblijvende
node voerde het VIP en herstel via de onafhankelijke lab-API bracht alle
applicatieservices terug. De hashes van filterregels, native configuratie en
accounts bleven gelijk. De webaccount kon geen van beide tokens lezen.
Dit zijn agentproeven, geen afgeronde onderbrekingsmatrix voor een beschermd
cluster. Begeleide quorum-/witnessactivering staat nog open. Deze externe labscripts vinken de
voorwaarde voor de fencing-proef niet automatisch af in de interface.

De experimentele variant behoudt de distributie-agent en bevat de GPL-broncode,
licentie en exacte upstream-revisie. Labtokens verlopen na 14 dagen.
Productievernieuwing van credentials en de volledige clusterlevenscyclus staan
nog open.

### Native quorumactivering in het tijdelijke Proxmox-lab

Alle vier de labguests gebruiken nu een bereikbare NTP-bron via de bestaande
OS-tijdservice. Vóór het instellen is de bron vergeleken met onafhankelijk via
HTTPS gecontroleerde Date-antwoorden. Het guest-OS meldt gesynchroniseerde
netwerktijd. De tijdinstellingen van de Proxmox-host en gateway zijn niet gewijzigd.

Root-only hulpmiddelen bereidden de pakketten voor terwijl de clusterservices
eerst gemaskeerd en uitgeschakeld bleven. De bestaande witness-CA ondertekende
het clientcertificaat voor het cluster. Privésleutelbundels en een verse Corosync-
sleutel van 1024 bytes zijn uitsluitend via vertrouwde SSH-verbindingen naar
root-only opslag overgedragen. Qnetd eist TLS en een clientcertificaat, luistert
op het witnessadres en laat via de firewall alleen de twee nodes toe. Het eigen
beheerjournal van de witness heeft een limiet van 30 dagen/100 MiB. Er is geen
PCSD-beheerservice opengesteld of clusterwachtwoord ingesteld.

Eerst is de standby gestopt, daarna de actieve VIP-eigenaar. Blijvende reserveringen
voorkomen dat Keepalived zelfstandig start zolang de inrichting niet is afgerond.
Beide nodes bevestigden vervolgens versleutelde Corosync-verbindingen, twee leden,
drie verwachte stemmen en een geauthenticeerde witnessverbinding. Pacemaker kreeg
uitsluitend een nieuwe, lege configuratie, met verplichte fencing en een aanvankelijk
gestopt VIP. Activering was een afzonderlijke stap met revisiecontrole, nadat beide
nodes veilig beleid en gereedstaande fencing hadden bevestigd. Daarna meldden
beide bescherming met precies één VIP-eigenaar. Keepalived start bij het opstarten
van de VM niet meer zelfstandig.

De native fencing-agent van de peer schakelde een gepauzeerde actieve VM uit.
Onafhankelijke hypervisorcontroles bevestigden dat die uit stond voordat de peer
het gezonde VIP overnam. Onafhankelijk herstel bracht de VM terug; beide nodes
meldden weer bescherming en de hashes van filterregels, native configuratie en
accounts bleven gelijk. Na het stoppen van de witness behielden de twee verbonden
leden quorum en exclusief VIP-bezit, terwijl de beschermingsstatus verslechterde.
Het opnieuw starten van de witness herstelde de beschermingsstatus.

Een aanvullende proef controleerde onbereikbare fencing. Eerst is bewezen dat
een tijdelijke regel op de guest een nieuwe verbinding naar de fencing-API
blokkeerde. Na het pauzeren van de actieve VM hield de peer de VIP-overname
45 seconden tegen. Na het verwijderen van uitsluitend de testregel kon fencing
voltooien. Ook nu bevestigde de hypervisor het uitschakelen voordat VIP-bezit op
de peer werd waargenomen. Beide nodes herstelden met één eigenaar en ongewijzigd
beleid en accounts. Een eerdere poging waarbij de conflicterende testregel niet
daadwerkelijk werd geïnstalleerd, is afgewezen als ongeldig testbewijs.

Dit zijn experimentele labresultaten met coördinatie door de OS-beheerder. De
GUI-inrichting, alle herstelpaden bij onderbroken inrichting en de Proxmox-
onderbrekingsmatrix zijn hiermee nog niet afgerond. Alle labguests delen één
fysieke host; in productie blijven onafhankelijke storingsdomeinen nodig voor
witness en fencing.

De laatste regressieproef slaagde met 340 Python-tests zonder overgeslagen tests,
lint en installertypecontroles. Frontendcontroles draaiden op Windows omdat de
WSL-image geen Node bevat; de Linux-versie- en shellcontroles slaagden afzonderlijk.
De Playwright-proef voor instellingen en de strikte NL/EN-documentatiebuild
slaagden ook. Daarna zijn A en B achtereenvolgens bijgewerkt via ondersteund
clusteronderhoud; de zelfstandige node kreeg een gewone upgrade. Browserproeven
bevestigden HTTPS met controle van de juiste CA, gesynchroniseerd beleid en een
actuele beschermingsstatus op beide HA-nodes. Er bleven geen testfirewallregels,
onderhoudscontrolepunten of fencing-tokens in de CIB/auditrecords achter.

De laatste tijdcontrole vond na een herstart ook een onbereikbaar adres in de
DNS-tijdpool van het lab. De configuratie op de guests probeert nu eerst een
opnieuw gecontroleerd, bereikbaar adres en bevat terugvalopties. Alle vier de
guests meldden daarna weer gesynchroniseerde tijd. Dit is een labinstelling,
geen vast ingebouwde tijdserver in de installer.

### HTTPS-beheer en browserbetrouwbaarheid

De zelfstandige Proxmox-node doorliep de volledige browserprocedure:
certificaat voorbereiden, de openbare CA downloaden, vertrouwen bevestigen,
HTTPS activeren en het beveiligde adres openen. De sessiecookie kreeg Secure
en HttpOnly. Alle drie de applicatienodes vernieuwden hun certificaat ook via
Instellingen, na wachtwoordbevestiging voor deze handeling. Afzonderlijke CA-controle
slaagde, de CA bleef gelijk en het auditlog vermeldde de uitvoerende beheerder.
Tijdens TLS-onderhoud bleven het proxyproces, de native configuratie, filterregels
en accounts ongewijzigd.

De praktijkproef vond een verschil tussen de stoptermijnen. De beheerde webservice
gebruikt nu gerichte beëindiging via systemd en een begrensde Gunicorn-wachttijd.
Rechtstreekse Gunicorn-TLS gebruikt workers met meerdere threads. Beide ondersteunde
servers beantwoordden gelijktijdige, via de CA gecontroleerde bestandsverzoeken
terwijl zes ongebruikte TCP-verbindingen openstonden. Ook de bestaande HTTP/HTTPS-
proeven voor accounts, optionele 2FA, auditregistratie en configuratie slaagden.

Browserproeven blokkeerden bewust `rules.js`: beide talen toonden een actie om de
pagina opnieuw te laden, zonder `RuleView`-fout. Daarna herstelde de regelweergave.
De HTTPS-dialogen slaagden voor beide talen en thema’s, mobiel en desktop, focus,
bevestiging bij niet-opgeslagen invoer en rolbeperkingen. Bij een verloren antwoord
op activering bleef de HTTPS-link beschikbaar, zonder de aanvraag te herhalen. Logtests controleren
behoud van ruwe geschiedenis en afscherming van gegevens bij het bundelen van
herhaalde vertrouwensmeldingen; andere fouten blijven zichtbaar. Op Linux slagen
324 tests zonder overgeslagen tests, plus lint, installertypes, frontendcontroles
en de strikte documentatiebuild. Deze wijzigingen zijn nog niet uitgebracht.

## 16 november 2026 — nieuw Proxmox-lab

In een aparte Proxmox-pool zijn vier tijdelijke Ubuntu 24.04-VM’s ingericht:
een zelfstandige node, twee HA-nodes en een voorbereide witness-host. De
ondersteunde installer installeerde v1.3.0 op de drie applicatienodes. Bestaande
VM’s, het hostnetwerk en de bovenliggende firewall zijn niet gewijzigd. De
beheerinterfaces luisteren alleen op loopback binnen de VM en zijn bereikbaar
via SSH-tunnels.

Via de browserinterface is A omgezet van zelfstandig naar beheerde HA en
is B met een eenmalige uitnodigingscode gekoppeld. Daarbij zijn het netwerkplan
en het beheerderswachtwoord gecontroleerd. De UFW-regels zijn in de VM’s
toegepast. De afzonderlijke beheerderswachtwoorden bleven werken. Toevoegen en
verwijderen van filterregels en het publiceren van een gedeelde Tinyproxy-instelling
werden automatisch van A naar B gesynchroniseerd. Rechtstreeks wijzigen van
regels op B werd geweigerd. Alle tijdelijke beleidswijzigingen zijn teruggedraaid.

Na het stoppen van Tinyproxy op A nam B het virtuele IP over en beantwoordde B
een echte proxy-aanvraag. Na het starten van Tinyproxy werd A weer de voorkeursnode;
precies één node had het virtuele IP en de services waren op beide nodes gezond.
Dit controleert service-uitval, niet een netwerkscheiding of uitval van de fysieke
host. De witness is alleen voorbereid: quorum en fencing zijn in dit lab
**niet ingesteld**.

De schone HA-omzetting vond een rechtenprobleem in v1.3.0: door de afgeschermde
umask van de root-helper was een openbare systemd-map voor Keepalived niet
toegankelijk voor de applicatie. Daardoor gaf de instellingenpagina HTTP 500.
Een lokale, nog niet uitgebrachte correctie maakt nieuwe openbare configuratiemappen
toegankelijk en herstelt uitsluitend de exact herkende installermap met dit probleem.
Onleesbare clusterreserveringen blokkeren nu zelfstandig servicebeheer zonder de
instellingenpagina te laten vastlopen. De ondersteunde updater heeft deze correctie
op alle drie de applicatienodes toegepast voordat bovenstaande proeven slaagden.
De gepubliceerde tag v1.3.0 is niet gewijzigd.

Op Linux slaagden alle 286 Python-tests, zonder overgeslagen tests, plus de lint-
en installertypecontroles. De Playwright-proeven voor instellingen en HA slaagden
voor beide talen, beide thema’s, toetsenbordbediening en kleine schermen.

### Koppelen, intrekken en beschermingsstatus

De volgende lokale build vraagt bij intrekken op beide nodes om bevestiging en
het beheerderswachtwoord, plus een verse TOTP-code als 2FA aanstaat. Geïsoleerde
tests controleren ontbrekende CSRF/bevestiging, verkeerde wachtwoorden en
hergebruik van TOTP-codes. Playwright controleert beide talen en thema’s,
mobiel/desktop, focus, behoud van invoer na fouten, bevestiging bij verwerpen,
succesvol intrekken en auditregistratie zonder geheimen.

Op het Proxmox-paar werkte de oude peersleutel na intrekken niet meer. Beide
nodes zijn daarna opnieuw gekoppeld. Verlopen, vervangen en hergebruikte
uitnodigingen werden geweigerd; van twee gelijktijdige koppelpogingen slaagde
er precies één. De native filter- en configuratiebestanden en afzonderlijke
inloggegevens bleven behouden. Automatische synchronisatie werd hervat. De
praktijkproef gebruikt een ondubbelzinnig verlopen tijdstip; geïsoleerde tests
zetten ook de klok van de uitgever voorbij de oorspronkelijke geldigheidsduur.

Regressietests voor de beschermingsstatus weigeren dubbele doelen tussen
afzonderlijke fencing-apparaten, afwijkende draaiende agents, uitgeschakelde
of regelgestuurde apparaten en Proxmox-toewijzingen zonder gecontroleerde TLS
of expliciete numerieke VM-ID’s. Dit controleert statusgegevens en bewijst
niet dat uitschakelen via Proxmox al is getest. Op Linux slagen nu 290
Python-tests, zonder overgeslagen tests.

De in dit lab beschikbare Ubuntu 24.04-versie van `fence_pve` ondersteunt geen
API-tokenauthenticatie. Het automatiseringstoken voor de labpool is niet naar de
proxynodes gekopieerd. Proxmox-fencing, activering van de witness en begeleide
inrichting van een beschermd cluster staan nog open.

Bij de tijdcontrole reageerde externe NTP niet; de gateway antwoordde wel, maar
had een vergelijkbare klokafwijking. De vier VM-klokken zijn eenmalig vooruitgezet
naar de tijd van de vertrouwde werk-pc, na vergelijking met een datumheader via
gecontroleerde HTTPS. De Proxmox-host en NTP-instellingen zijn niet gewijzigd.
Dit is een tijdelijke labcorrectie, geen doorlopende tijdsynchronisatie. Een
betrouwbare tijdbron blijft een beheervoorwaarde, vooral voor 2FA en
certificaatvernieuwing.

### HTTPS voor beheer

De volgende lokale build biedt rechtstreeks beheer-HTTPS aan, zonder reverse
proxy, op de zelfstandige node en beide Proxmox-HA-nodes. Iedere node heeft een
andere CA. De applicatieaccount kan de CA-sleutel niet lezen. Inschakelen en
vernieuwen behielden het Tinyproxy-proces-ID. Een daaropvolgende ondersteunde
applicatie-update behield de HTTPS-interface en certificaatkoppeling op de
zelfstandige node.

De foutproeven op de zelfstandige node vernieuwden voortijdig, boden bewust een
verlopen servercertificaat aan en controleerden dat een client dit weigerde.
Daarna herstelde de normale vernieuwingsservice geldige HTTPS. De CA bleef
gelijk. De echte systemd-opstartcontrole herstelde een voorbereide onderbroken
activering. Bij een opzettelijk mislukte bereikbaarheidscontrole werd de vorige
geldige HTTPS-configuratie teruggezet. Native proxybestanden en accounts bleven
behouden. Dit waren gecontroleerde certificaat- en servicefouten, geen fysieke
stroomuitvalproeven.

De browserproeven controleerden op alle drie de nodes afzonderlijk CA-vertrouwen,
HTTPS-inloggen, Secure/HttpOnly/SameSite-sessiecookies, NL/EN, beide thema’s en een
mobiel scherm. Een tijdelijke regel via HTTPS kwam op B aan en is daarna verwijderd.
De oorspronkelijke filterrevisie en gezonde synchronisatie zijn hersteld.
Het browservertrouwen voor de test was beperkt tot de verwachte publieke
certificaatsleutels; de vertrouwde certificaten van de werk-pc zijn niet gewijzigd.
De openbare node-CA’s zijn via vertrouwde SSH opgehaald.

Op Linux slagen 302 Python-tests, lint, installertypecontroles en shellcontroles.
Beide WSGI-servers met twee workers slaagden voor echte HTTP- en CA-gecontroleerde
HTTPS-proeven voor accounts, 2FA, audit en configuratie, inclusief HSTS en
beveiligde cookies. De strikte documentatiebuild en vertaalde responsieve
browserproeven slagen. Installerzelftests isoleren TLS-detectie nu van het echte
hostprofiel.

HTTPS blijft optioneel. Standaardinstallatie, begeleide CA-import/vervanging en
ACME staan nog open. HSTS wordt bij de lokale node-CA niet automatisch aangezet.

## 6 november 2026

Omgeving: Ubuntu 26.04.1 LTS onder WSL 2, systemd als PID 1, Python 3.14.4,
Tinyproxy 1.11.3. De Python-dependencies staan in een aparte Linux-virtualenv
onder `/home/ruben/.venvs/proxypermit`.

| Controle | Resultaat |
| --- | --- |
| Unit-tests op Windows/Python 3.11 en Linux/Python 3.14 | 44 geslaagd |
| Gunicorn 26.2.0, echte HTTP-verzoeken, twee workers | Geslaagd |
| Granian 2.8.7, echte HTTP-verzoeken, twee workers | Geslaagd |
| Login, configuratie opslaan, CSRF-token, versieconflict en statische assets | Geslaagd |
| Nieuwe installatie met Granian via de installer | Geslaagd |
| Bestanden schrijven en Tinyproxy herstarten als serviceaccount | Geslaagd |
| Upgrade naar Gunicorn met behoud van configuratie en managerpoort | Geslaagd |
| Rollback naar de vorige release, met HTTP-controle | Geslaagd |
| Echt Tinyproxy-verkeer naar een lokale HTTP-server | Niet toegestane host: 403; toegestane host: 200 |
| Pylint-fouten, JavaScript-syntax en shellsyntax | Geslaagd |

De installatietest gebruikte `/opt/proxypermit-integration` en localhost:8098.
Testservices worden na de controle gestopt en uitgeschakeld; packages,
testreleases en backups blijven beschikbaar voor inspectie.

De integratietest vond een poortmigratiefout: een luisteradres dat alleen in
de systemd-unit stond, ging verloren bij een upgrade. De installer bewaart die
instelling nu in de environmentconfiguratie en controleert het ingestelde
HTTP-adres, inclusief Unix-sockets en lokale TLS, in plaats van alleen de
processtatus. De TLS-readinesscheck controleert bereikbaarheid; certificaatvertrouwen
wordt daardoor niet bewezen.

Herhalen van de geïsoleerde controles vanuit de projectroot:

```bash
~/.venvs/proxypermit/bin/python scripts/dev/test.py
~/.venvs/proxypermit/bin/python scripts/dev/check_server.py --backend gunicorn
~/.venvs/proxypermit/bin/python scripts/dev/check_server.py --backend granian
```

Nog te controleren: Ubuntu 24.04, upgrades van een echte legacy-HA-installatie,
Nginx/TLS-toegang vanaf clients, en failover/netwerkpartitionering tussen twee
afzonderlijke hosts. WSL op één machine bewijst deze eigenschappen niet.

## Na de herindeling

De 58 Python-tests en de JavaScript-contracttests voor locales en regelkaarten
worden met de nieuwe mappen uitgevoerd. Beide echte HTTP-servers zijn opnieuw
gecontroleerd. De workflow is lokaal met actionlint gevalideerd; de productie-
dependencies zijn met pip-audit gecontroleerd (geen bekende kwetsbaarheden).
De GitHub Actions-run zelf volgt pas nadat de wijzigingen naar GitHub gaan.

De screenshotsignalen zijn als rendercontract opgenomen: eigen inhoudskolom,
aparte actiekolom, vertaald Bewerken-label, behouden iconen en ongewijzigde
gebruikersinhoud. Dit vervangt geen visuele browsercontrole.

## Installatie en failover op 8 november 2026

- De huidige installer heeft de bestaande standalone-testinstallatie in WSL
  bijgewerkt naar een nieuwe release met Granian. De beheerpagina op
  `http://localhost:8098/` gaf ook vanaf Windows HTTP 200. De configuratie-API
  gaf zonder login HTTP 401. Via Tinyproxy op loopback gaf een lokaal testdoel
  HTTP 403 zonder toestemming en HTTP 200 na het toevoegen van een filterregel.
  De oorspronkelijke regels zijn teruggezet. Beide services draaien voor de
  demonstratie, met automatisch opstarten uitgeschakeld.
- Twee tijdelijke WSL-netwerk-namespaces draaiden elk ProxyPermit, Tinyproxy en
  Keepalived. Beide beheerpagina's gaven HTTP 200 en beide proxies leverden het
  lokale testantwoord. Na het stoppen van Tinyproxy op de eerste node nam de
  tweede node het virtuele IP over en werkte een nieuwe HTTP-aanvraag na 2,1
  seconden. De testnodes zijn na afloop opgeruimd.
- De failoverproef gebruikte handmatig ingerichte nodes op één WSL-host.
  Installatie van een nieuw HA-cluster, beleidssynchronisatie en uitval van een
  fysieke host zijn afzonderlijke, nog openstaande tests.

### Gegenereerde configuratie voor nieuwe HA-clusters

Beide configuraties uit de nieuwe installer zijn met Keepalived `--config-test`
gecontroleerd in afzonderlijke WSL-netwerk-namespaces. Beide Tinyproxy-processen
werkten met de gegenereerde configuratie en een lokale testregel. Nadat Tinyproxy
op node A werd gestopt, verplaatste het virtuele IP zich naar node B en gaf een
nieuwe aanvraag via datzelfde adres na 3,2 seconden HTTP 200. Deze proef gebruikte
de gegenereerde configuratie en healthcheck. De volledige `--apply`-route op
twee schone systemd-hosts moet nog afzonderlijk worden gevalideerd.

### Herhaalbaar HA-lab met lokale beheerinterfaces

Gebruik een Linux-testhost met Tinyproxy, Keepalived, iproute2 en de Python-
dependencies uit `requirements/granian.txt`. Start vanuit de projectmap:

```bash
sudo unshare --mount --propagation private .venv/bin/python scripts/dev/ha_lab.py --hold
```

Het lab gebruikt twee geïsoleerde netwerk-namespaces met een eigen bridge.
De beheerpagina's luisteren via loopback op poort 8101 en 8102. De gedeelde
proxy is via loopback bereikbaar op 8130. De startproef controleert inloggen
met 2FA, afwijzing van hergebruik en nieuw HTTP-verkeer na failover. De
inloggegevens staan in bestanden die alleen root kan lezen. Het lab print
de paden. De beheerinterface draait in demomodus zodat de nodes geen services
van de testhost herstarten. De twee Tinyproxy- en Keepalived-processen zijn
echte processen. Automatische beleidssynchronisatie maakt geen deel uit van
dit lab.

## 11 november 2026: beheerde HA en logretentie

Twee disposable Ubuntu 24.04-VM's in VirtualBox zijn vanuit standalone via
gecontroleerde GUI-plannen en een eenmalige koppelcode omgezet naar HA. UFW
bleef inkomend en uitgaand standaard weigeren. Filterregels en gedeelde
Tinyproxy-instellingen kwamen automatisch op B terecht. Echte proxyverzoeken
werkten via het virtuele IP op A en na failover op B. Automatisch foutherstel en
handmatig herstel naar standalone zijn gecontroleerd. De VM's draaien op één
fysieke host; dit bewijst geen fysieke hostfailover, fencing of partitiegedrag.

De nieuwe audit- en journalretentieservices zijn op een echte guest uitgevoerd.
Synthetische records van 31 dagen oud zijn uit het actieve auditbestand en een
archief verwijderd. Records van twee dagen oud bleven via een UTC-datumbereik
doorzoekbaar. Het operationele journal en het Tinyproxy-journal waren via de
ingelogde API leesbaar. Opruiming in deze namespaces liet een testrecord in het
algemene hostjournal intact. Ook schijfbudgetten en succesvolle onderhoudstaken
zijn gecontroleerd.

Regressietests behandelen parallel schrijven/roteren, leeftijds- en grootte-
opruiming, lezen tijdens rotatie, paginering met boventijdstip, beschadigde
records, IPv4/IPv6- en credentialafscherming, veilige logpaden, toegangscontrole
en de publieke melding. Playwright controleert NL/EN, beide thema's, mobiele
logs, datumfilters, wisselen tussen historie/live en contrast van gevaarlijke
acties. De organisatie moet haar eigen privacymelding, externe logs,
back-up-/exportretentie, netwerkplan en belasting blijven valideren.

## 12 november 2026

### Bediening en HA-responsiviteit

De werkversie is gecontroleerd met de volledige projectchecks en afzonderlijke
browserfixtures. De Windows-suite draait 144 Python-tests met 7 platformgebonden
skips. De Linux-installer draait de volledige suite bij iedere labupgrade.
Playwright controleert NL/EN, beide thema's, kleine schermen, toetsenbordbediening,
schrijfblokkades op de back-upnode en de bevestigde conceptworkflow voor filtermodi.
Documentatiescreenshots worden uit fixtures gemaakt met `PP_DOC_SCREENSHOTS=1`.

In het draaiende VirtualBox-lab is de volgorde op A gewijzigd en zonder handmatig
herladen op de geopende pagina van B gecontroleerd en daarna teruggezet. Tinyproxy
op A is tijdelijk met SIGSTOP stilgezet terwijl Keepalived bleef draaien. B nam
het VIP over, toonde de actieve rol en beantwoordde een clientverzoek via het VIP.
Na hervatten kreeg A het voorkeurs-VIP terug en keerde de status van B terug naar
standby. Een afzonderlijke hersteltimer verstuurde eveneens SIGCONT. De
webinterface van de zelfstandige node activeerde de andere filtermodus en herstelde de oorspronkelijke
configuratie. De HA-koppeling en beleidssynchronisatie bleven werken.

Dit bewijst geen productie-beschikbaarheid bij een fysieke netwerkonderbreking,
upstreamstoring of uitval van de gedeelde hypervisor. Accountreplicatie en
afzonderlijke rollen waren op dat moment nog vervolgwerk. De latere [rolimplementatie](rollen.md) voegt beperkte lokale toegang toe.

### Anti-lockout en zichtbare concepten

De standalone en beide HA-labnodes zijn bijgewerkt met een gecontroleerd
beheerbronadres, de echte SSH-/webpoorten en een veiligheidsprofiel van root
(modus 0640, leesbaar voor de servicegroep). De bestaande activatiestatus van UFW
bleef behouden. De proef met een lege firewall ontdekte een ongeldige numerieke
invoegpositie. De installer draaide terug; de aangepaste prepend-opdracht werkte
daarna op dezelfde host.

Ingelogde API-verzoeken om het vaste Allow-blok te verwijderen, de vaste proxypoort
te wijzigen of een Deny voor de beheeruitzonderingen te plaatsen werden geweigerd.
Het actieve bestand bleef gelijk en er ontstond geen concept. Daarna is een
Timeout-wijziging op A gepubliceerd, op B verwerkt met behoud van B's eigen
beheerblok en op beide nodes teruggezet. De volledige overname-/herstelproef en
regelvolgordecontrole slaagden opnieuw na de updates. De aparte UFW-beheerregels
vallen buiten het opruimen van HA-regels.

De browserchecks controleren niet-opgeslagen, opgeslagen en actieve conceptstatus,
de afgeschermde verschillen, opslaan/verwerpen en de bestaande controles voor
beide thema's, talen en mobiele schermen. Het installeroverzicht en de opdracht
voor niet-rootgebruikers behouden de expliciete beschermde adressen en echte
hostpoorten. De Windows-suite draait 144 Python-tests met 7 platformskips.
Bestemmingsfilters en proxy-authenticatie blijven gelden: dit beschermt de
vastgelegde netwerktoegang, niet willekeurige routerings- of upstreamwijzigingen.

De definitieve Linux-run slaagde voor alle 144 Python-tests zonder skips, de
shellchecks en afzonderlijke HTTP-/account-/2FA-/audit-/opslaantests met Gunicorn
en Granian (elk twee workers). De extra backend is in een aparte test-venv
geïnstalleerd; de dependencies van de draaiende installatie bleven intact.

### Lokale gebruikersrollen

De Windows-suite slaagt voor 153 Python-tests (7 platformgebonden skips). De
Linux-installer voert alle 153 tests zonder skips uit. Ook typecontroles,
Python-codecontrole, frontendcontroles, shellsyntaxis en afzonderlijke HTTP-/
account-/2FA-tests met Gunicorn en Granian slagen. De browsertests controleren
monitor- en proxybeheerdersschermen, NL/EN, beide thema's, mobiele opmaak, rolkeuze
en het ongeldig maken van sessies. Bestaande accounts zonder rol behouden hun
beheertoegang.

De drie VirtualBox-nodes zijn bijgewerkt. Via de beveiligde API zijn tijdelijke
monitoraccounts aangemaakt, van rol veranderd en weer verwijderd. Oude sessies
werden ongeldig, terwijl het wachtwoord bruikbaar bleef. Monitors konden geen
de oorspronkelijke Tinyproxy-configuratie lezen of Tinyproxy herstarten. Proxybeheerders konden de
standalone-proxy herstarten, maar geen HA of accounts beheren. Gedeeld beleid
bleef op de gekoppelde back-upnode alleen-lezen. Beide HA-statuspanelen bleven
synchronisatie tonen. Een andere servicegebruiker instellen werd geweigerd zonder
concept of wijziging van actieve bestanden. Afzonderlijke tests controleren ook
beschermde systeemconcepten, optionele 2FA voor monitors en bescherming van de
laatste systeembeheerder in de CLI.

Alle drie de SSH-verbindingen tonen door VirtualBox-NAT `10.0.2.2` als bronadres.
De gelijke beheerbron is in dit lab dus te verwachten en is niet het eigen IP-adres
van elke node. Bronbeperkingen vervangen inloggen niet.

De regressiecontrole voor monitorregels controleert de volle tekstbreedte op
320, 390 en 1440 pixels in beide talen en thema's. Alleen proxy- en systeembeheerders
kunnen filterregels exporteren; een rechtstreeks exportverzoek van een monitor
krijgt 403. Dit is op alle drie de labnodes gecontroleerd met tijdelijke accounts
die na afloop zijn verwijderd.

## Documentatiewebsite

De Zensical-site bouwt met `--clean --strict`, inclusief controle op links en
ankers. Dezelfde build is vanuit een gegenereerd releasearchief gecontroleerd.
Playwright controleert Engelse/Nederlandse handleidingen, light/dark mode,
desktop/mobiele opmaak, taalverwijzingen, echte zoekresultaten en sluiten via het
toetsenbord. De lokale preview gebruikt `/ProxyPermit/` om relatieve Pages-paden
te controleren. Actionlint controleert de Pages-workflow. Dit bereidt publicatie
voor; het betekent niet dat GitHub Pages al is ingeschakeld of gepubliceerd.

## Releasedocumentatie en oude Actions-runs

De release-instructies maken nu onderscheid tussen voorbereiden, CI, taggen,
GitHub Release-bestanden en Pages-publicatie. De voorbeelden voor accountmigratie
maken expliciet een admin aan voordat lokale accounts worden ingeschakeld. Een
strikte documentatiebuild is een verplichte voorwaarde voor het releasearchief.
Actionlint, versiecontrole en schone/strikte Zensical-builds slaagden, ook bij
bouwen vanuit het gegenereerde broncodearchief.

Op 12 november 2026 zijn 39 afgeronde runs van de vervallen Pylint-workflow
verwijderd. Een vergelijking vóór en na de actie bevestigde dat alle 46 huidige
workflowruns en 61 huidige artefacten behouden bleven. Geen van die artefacten
hoorde bij de oude runs. Een oude commit bleef daarna rechtstreeks via SHA
bereikbaar: Actions opruimen wist de broncodegeschiedenis bij GitHub niet. Deze
wijzigingen maken geen release, verplaatsen geen stabiele tag en bewijzen geen
nieuwe geslaagde CI-run op GitHub.

## 13 november 2026 — regressiecontroles voor 1.2.1

Op Windows slaagden 159 Python-tests; zeven platformafhankelijke tests zijn
overgeslagen. Ook de frontendcontroles, Pylint, typecontrole van de installer voor
Windows/Linux en de HTTP-integratiecontrole met Granian slaagden. Afzonderlijke
browsertests bevestigden dat een verouderde bewerking of verwijdering de nieuwere
regel intact laat, ook na een achtergrondverversing terwijl het bewerk- of
bevestigingsvenster openstaat. Dit is in het Nederlands en Engels gecontroleerd.

Regressietests controleren ook gelijktijdige bewerkingen vanuit dezelfde revisie,
een concept of gewijzigd filtertype voordat een wachtend schrijfverzoek het slot
krijgt, ongeldige verzoeken en de telling van overgeslagen importregels. Bestaande
HA-tests controleren publicatie op A, schrijfbeperkingen op B en geauthenticeerde
beleidsoverdracht. Dit is geen nieuwe overnametest op afzonderlijke VM’s.

De strikte documentatiebuild en viewercontroles omvatten beide talen, beide
thema’s en schermbreedtes van 390 en 1440 pixels. De schermafbeeldingen gebruiken
afzonderlijke testgegevens.

In een afzonderlijke Linux-virtualenv met de huidige requirements slaagden ook
159 Python-tests; één test die rootrechten vereist is overgeslagen. De oude
WSL-serviceomgeving gebruikte nog pyrad 2.4. De RADIUS-testfouten verdwenen met
de vereiste 2.6.0 in de afzonderlijke testomgeving. De serviceomgeving is niet
aangepast.


## 15 november 2026

### Reload

De afzonderlijke Linux-controle slaagde voor 191 tests. Op Windows slaagde dezelfde
suite, met 15 overgeslagen tests voor POSIX-rechten of root. Ook de kwaliteits-,
type- en frontendcontroles slaagden. Een upgrade vanuit een afgeschermde bronmap
is daarnaast op een echte VM gecontroleerd.

De zelfstandige node en beide VirtualBox-HA-nodes doorstonden een echte reload als
serviceaccount: verse start- en eindmeldingen in het private journal, dezelfde
systemd-invocatie en hetzelfde proces-ID, plus een geslaagde lokale HTTP-controle.
Oude opstartmeldingen tellen niet als bevestiging. De lees-ACL’s op de native
bestanden blijven bij atomair opslaan en herstel behouden. Tinyproxy krijgt
hiervoor geen toegang tot beheergeheimen via lidmaatschap van de beheergroep.

Een afzonderlijke Tinyproxy-proef met versies 1.11.1 en 1.11.3 hield een
HTTP-overdracht en een CONNECT-tunnel open tijdens drie reloads en wisselingen
tussen allowlist en blocklist. Nieuwe verzoeken volgden het gewijzigde beleid:

```bash
python3 scripts/dev/check_reload.py
```

Deze proef gebruikt eigen tijdelijke bestanden, poorten en een eigen proces.
De geïnstalleerde service wordt niet herladen. Gebruik voor de beheerde HA-proef
wegwerp-nodes: noteer op beide nodes
`systemctl show tinyproxy.service -p MainPID -p InvocationID`. Voeg via A een
tijdelijk `.invalid`-domein toe met automatisch toepassen ingeschakeld. Wacht op
B’s gelijke revisie en verwijder daarna dezelfde regel. Beide proces-ID’s moeten
gelijk blijven; na elke bewerking moeten de revisies overeenkomen.


De geauthenticeerde A → B-proef slaagde voor toevoegen én verwijderen. Beide
Tinyproxy-proces-ID’s bleven gelijk, de oorspronkelijke filterinhoud is exact
hersteld en beide nodes keerden terug naar de oorspronkelijke gedeelde revisie.
OS-controles op alle drie de VM’s bevestigden dat Tinyproxy de native bestanden
alleen kan lezen en de beheeromgeving, accountfile en bestaande peergegevens niet kan lezen.

Deze reloadcontroles bewijzen geen bescherming tegen split-brain. Quorum/fencing
en tests met netwerkonderbrekingen blijven open op de [roadmap](vervolgwerk.md).

### Sleutelrotatie en TLS

De geïsoleerde Linux-suite slaagde voor alle 208 tests, zonder overgeslagen tests.
De Windows-suite slaagde met 19 platform- of OpenSSL-afhankelijke tests overgeslagen.
De rotatietests controleren intrekking van de oude sleutel, het aflopen van de
overgang, gelijktijdige verzoeken en herstel na een verloren bevestigingsantwoord.
Dit is nog geen volledige controle van sleutelrotatie via de browser op twee VM’s.

De TLS-tests gebruiken echte lokale verbindingen met tijdelijk gegenereerde
certificaten. Een eerste handshake controleert de vooraf vertrouwde SHA-256-pin
zonder HTTP-verzoek of peersleutel. Een tweede handshake controleert het gepinde
certificaat met OpenSSL, inclusief geldigheid en handtekening; daarna wordt de pin
opnieuw gecontroleerd. Pas daarna mag de peersleutel worden verstuurd. De pin
identificeert de node; de hostnaam en systeem-CA’s worden hiervoor niet gebruikt.
Een verkeerde pin, een verlopen certificaat en een certificaatwissel tussen beide
handshakes worden geweigerd. Gecontroleerde certificaatvernieuwing en pinmigratie
blijven open, evenals de aanvullende opslag- en herstarttests voor sleutelrotatie.

### Aanvullende HA-controles

De Linux-suite slaagt nu voor 231 tests zonder overgeslagen tests, inclusief
opslagfouten, een nieuwe optionele TOTP-code, de VIP-resource en de volgorde van
publicaties. De bestaande Playwright-instellingensuite en de nieuwe afzonderlijke
HA-beveiligingssuite slagen. De HA-suite controleert NL/EN, beide thema’s,
mobiel/desktop, focus, sluiten met onopgeslagen invoer, verkeerde wachtwoorden
en de beperkingen van de monitorrol.

Een echte sleutelrotatie via de browser slaagde op het VirtualBox-HA-paar.
B bevestigde de nieuwe sleutel automatisch; de oude sleutel kreeg 403 en de
nieuwe 200. De filters en het Tinyproxy-proces-ID op A bleven behouden. De eerste
proef vond een verouderde nginx-padbeperking. Na aanpassing van uitsluitend het
interne commitpad slaagde de volledige proef. Zie de upgradevoorwaarde in
[Beheer](beheer.md#de-ha-peersleutel-vernieuwen).

Een afzonderlijk KVM/libvirt-prototype gebruikte twee Ubuntu 24.04-nodes,
Pacemaker/Corosync, een TLS-qdevice-witness en de standaardagent `fence_xvm`.
De hostfencer was beperkt tot de UUID’s en bronadressen van de twee proxynodes;
de witness had geen fencing-sleutel. De proeven slaagden voor een vastgelopen proxy,
herstel zonder automatische terugkeer van het VIP, een achtergebleven VIP na het
doden van Keepalived, witnessverlies met verbonden peers, eenzijdige/volledige
peeronderbreking en een bevroren node met een onbereikbare fencer. Overname bleef
geblokkeerd tot fencing weer beschikbaar was. Daarna werd de bevroren VM eerst
uitgeschakeld voordat het VIP gezond op de andere node verscheen.

Dit bewijst de geteste foutscenario’s van het prototype, niet de productie-integratie.
De gewone HA-wizard en bestaande ProxyPermit-nodes zetten dit niet automatisch aan.
Platformadapters, integratie met beheerhandelingen, bredere storingstests,
certificaatvernieuwing en HTTPS voor de beheerinterface blijven open. Zet een volledig
oud OS-image bij herstel eerst via het platform in quarantaine totdat de clusterstatus
is beoordeeld. Gastsoftware kan geen herstel tegenhouden dat ook haar eigen
beveiligingsmaatregelen verwijdert.

De laatste Linux-controle slaagde voor 247 tests zonder overgeslagen tests, plus
shellcontroles en echte HTTP-controles met Gunicorn en Granian met twee workers.
Het echte HA-paar doorstond ook de volgordecontrole: B weigerde een tijdelijk
teruggezet lager publicatienummer, behield zijn filter en Tinyproxy-proces en
synchroniseerde weer nadat de oorspronkelijke publicatie was hersteld.

Daarna slaagde de interne certificaatvernieuwing op dat paar. B bevestigde de
nieuwe pin en verwijderde de oude uit zijn toegestane pins. Op beide nodes bleven
de Tinyproxy- en Keepalived-proces-ID’s en de filterbestanden behouden.
Peersleutelrotatie over de vernieuwde TLS-verbinding slaagde ook. De OS-UID in de
auditregistratie en het ontbreken van de peersleutel in die logregels zijn
gecontroleerd. De afgeronde herstelroute kon zonder wijzigingen opnieuw draaien.

De drie VM’s van het afzonderlijke KVM-prototype delen één werkstation. Uitval
van die gedeelde fysieke host valt buiten deze proeven. De VM’s zijn opnieuw
gestart voor onderstaande onderhoudsproeven. De bestaande standalone- en twee VirtualBox-HA-nodes blijven
draaien. Standaard HTTPS voor de beheerwebsite blijft gepland: interne HA-TLS-
vernieuwing schakelt HTTPS op die website niet in.

#### Controle van clusterstatus en onderhoud per node

Het afzonderlijke KVM-paar draait nu echte, door de installer beheerde
ProxyPermit-services boven op het door het OS voorbereide cluster. Beide nodes
doorliepen de ondersteunde route `--upgrade --cluster-maintenance` met succes.
Voor en na iedere update zijn pakketversies, Keepalived-configuratie en
gezondheidscontrole, clusterconfiguratie en -profiel, native proxyconfiguratie,
filterbestand en accounts vergeleken. Die bleven allemaal behouden; de
applicatierelease veranderde op beide nodes.

De actieve node droeg de VIP over voordat lokale wijzigingen begonnen. Een tweede
onderhoudsaanvraag werd geweigerd zolang de eerste reservering bestond. Na
procesafsluiting bleven het herstelbestand en de bijbehorende reservering intact;
expliciet herstel zette standby terug. Ook een afzonderlijke mislukte
gezondheidscontrole liet de reservering staan. De echte root-herstelopdracht
controleerde vervolgens de lokale beheerinterface en proxy en de bescherming op
de andere node voordat hij de reservering vrijgaf.

De root-statusexport is op beide nodes gecontroleerd. De applicatieaccount kon
alleen de beperkte status lezen en kreeg geen toegang tot de CIB. Witnessverlies
leverde een verminderde beschermingsstatus op. Na het stoppen van de exporter
verviel de gezonde status automatisch naar onbekend. Geïsoleerde tests weigeren
ook fencingtoewijzingen zonder verschillende doelen voor beide nodes,
tegenstrijdig VIP-bezit, ongeldige gegevens en onveilige bestandsrechten.
Playwright slaagde voor NL/EN, beide thema’s, kleine en grote schermen,
toetsenbord/focus, onopgeslagen wijzigingen, herauthenticatie en de monitorrol.

Hiermee wordt de productieclusterinrichting nog niet automatisch ingeschakeld.
Andere platformadapters, clusterbewust verwijderen/terugdraaien en de overige
onderbrekingsproeven staan nog open. Zie [Vervolgwerk](vervolgwerk.md).

De laatste Linux-controle slaagde voor alle 280 Python-tests, zonder overgeslagen
tests. Lint- en installertypecontroles, shellcontroles en de echte HTTP-proeven
met Gunicorn en Granian met twee workers slaagden ook. De strikte documentatiebuild
slaagde eveneens. De schone installatietest vond een fout in de voorbereiding van
de back-upmap vóór de import als serviceaccount; die is opgelost. Installerzelftests
isoleren nu zowel de clusterreservering als certificaatvernieuwing van de echte
host. Beschrijfbare native conceptback-ups worden gecontroleerd zonder recursieve
rechtenmigratie als root.
