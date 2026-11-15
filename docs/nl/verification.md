# Verificatie op 6 november 2026

[English](../en/verification.md) · [Documentatie](README.md)

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

## 12 november 2026: bediening en HA-responsiviteit

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

## 12 november 2026: anti-lockout en zichtbare concepten

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

## Documentatiewebsite

De Zensical-site bouwt met `--clean --strict`, inclusief controle op links en
ankers. Dezelfde build is vanuit een gegenereerd releasearchief gecontroleerd.
Playwright controleert Engelse/Nederlandse handleidingen, light/dark mode,
desktop/mobiele opmaak, taalverwijzingen, echte zoekresultaten en sluiten via het
toetsenbord. De lokale preview gebruikt `/ProxyPermit/` om relatieve Pages-paden
te controleren. Actionlint controleert de Pages-workflow. Dit bereidt publicatie
voor; het betekent niet dat GitHub Pages al is ingeschakeld of gepubliceerd.

## 12 november 2026: lokale gebruikersrollen

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


## Reload gecontroleerd — 15 november 2026

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

## Sleutelrotatie en TLS gecontroleerd — 15 november 2026

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

## Aanvullende HA-controles — 15 november 2026

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

### Controle van clusterstatus en onderhoud per node

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
