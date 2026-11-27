# Roadmap

[English](../en/planned-work.md) · [Documentatie](README.md)

Welke verbeteringen komen eraan en wat heb je daar als beheerder aan? De
belangrijkste ontwikkelprioriteit is HA met bescherming tegen onveilige overname.
Ook gebruiksgemak, HTTPS en logging krijgen aandacht.

Bijgewerkt op 24 november 2026, met **v1.3.0** als uitgangspunt voor uitgebrachte
functies. Er zijn geen toegezegde opleverdatums; de volgorde kan veranderen.

**Zo lees je deze pagina:** een vinkje betekent dat een ontwikkelstap is afgerond
of experimenteel gecontroleerd. Dat onderdeel kan dus nog onuitgebracht zijn.
Open vakjes beschrijven resterend werk. De
[release notes](https://github.com/Dantdmnl/ProxyPermit/releases) vermelden wat
is uitgebracht; het [verificatierapport](verification.md) bevat technische
proefresultaten en beperkingen.

## Wat je nu kunt gebruiken

De volgende mogelijkheden zitten in v1.3.0:

| Mogelijkheid | Wat betekent dit voor jou? |
| --- | --- |
| HA met twee nodes | Keepalived kan het gedeelde virtuele IP-adres (VIP) laten overnemen door de andere node. Node A publiceert filterregels en gedeelde proxyinstellingen; node B ontvangt die automatisch. Accounts en hostinstellingen blijven lokaal. |
| Peersleutel vernieuwen | Je kunt de sleutel voor de verbinding tussen de nodes via A vernieuwen, met wachtwoordbevestiging en een authenticatorcode als 2FA aanstaat. Beide nodes moeten de functie ondersteunen. |
| Intern HA-certificaat vernieuwen | Een OS-beheerder kan het certificaat voor de synchronisatieverbinding gecontroleerd vernieuwen. Dit staat los van HTTPS voor de beheerwebsite. |
| Filters herladen met behoud van verbindingen | Optioneel beschikbaar voor Tinyproxy 1.11.1 en 1.11.3. Andere instellingen vereisen nog een herstart; herladen staat standaard uit. |

Er is ook een **experimentele integratie met een vooraf op OS-niveau ingericht
cluster**, inclusief beschermingsstatus en onderhoud per node. De gewone
HA-wizard richt dit cluster nog niet voor je in.

!!! warning "Gewone HA sluit split-brain nog niet uit"
    Bij een netwerkonderbreking kunnen twee nodes allebei denken dat ze actief
    moeten zijn. Dit heet split-brain. Keepalived met twee nodes alleen kan dat
    niet uitsluiten. Een upgrade schakelt aanvullende bescherming niet automatisch
    in. Lees [HA instellen en de beperkingen](hoge-beschikbaarheid.md#begrijp-de-bescherming-bij-overname)
    voordat je een productieopstelling kiest.

## Beschikbaar om te testen in v1.4.0-dev.5

- **Veiliger HA-beheer:** de koppeling intrekken vraagt opnieuw om het beheerderswachtwoord en, als 2FA aanstaat, een verse authenticatorcode. De beschermingsstatus controleert strenger of de clustergegevens betrouwbaar zijn.
- **Voorbereiding van beschermde HA:** systeembeheerders kunnen in Instellingen een Proxmox-plan controleren en opslaan en op elke node de pakketten voorbereiden. De OS-beheerder regelt de witness, fencing en activering.
- **Migratie en herstel:** controleer het clusterverkeer vóór de VIP-overdracht en zet een applicatieversie terug terwijl de andere node het verkeer bedient. Bekijk de resultaten van de Proxmox-proeven in het [verificatierapport](verification.md).
- **HTTPS voor beheer:** de wizard stelt bij een nieuwe installatie HTTPS met een lokale CA voor. Systeembeheerders kunnen HTTPS inschakelen en certificaten vernieuwen via Instellingen. Zie [HTTPS voor beheer](beheer-https.md).
- **Beleidsherstel en diagnose:** ProxyPermit controleert de herstelkopieën voordat Tinyproxy start. De console meldt wanneer lokaal herstel de beleidssynchronisatie pauzeert en wanneer Corosync op beide proxynodes wacht voordat quorum kan ontstaan.

Deze functies zitten in de [prerelease v1.4.0-dev.5](https://github.com/Dantdmnl/ProxyPermit/releases/tag/v1.4.0-dev.5),
naast de stabiele v1.3.0. Gebruik voor de prerelease een tijdelijke of herstelbare
testomgeving en de handleiding die bij de geïnstalleerde versie hoort.

## HA met bescherming tegen onveilige overname

**Wat je eraan hebt:** de andere node neemt het verkeer pas over wanneer dat
veilig kan. De inrichting helpt je ontbrekende onderdelen en benodigde rechten
te herkennen.

Een onafhankelijke **witness** helpt bepalen welke node mag doorgaan. Samen met
de nodes maakt die een meerderheidsbesluit mogelijk: **quorum**. **Fencing**
kan een onbereikbare of vastgelopen node uitschakelen voordat de andere node het
VIP krijgt. Dit vereist passende infrastructuur en beperkte toegangsrechten op
het gebruikte platform.

- [x] Een experimentele clusterintegratie met quorum, beveiligde witnessverbinding en fencing aanbieden voor inrichting door de OS-beheerder.
- [x] Een beschermingsstatus tonen die bij verouderde gegevens geen gezonde toestand blijft melden.
- [x] Voorwaarden vóór activering controleren en onderhoud per node ondersteunen op een vooraf ingericht cluster.
- [x] In Instellingen een gevalideerd Proxmox-plan zonder geheimen maken en downloaden voor een bestaand beheerd HA-paar.
- [x] In Instellingen de benodigde toegang uitleggen en op de tweede node hetzelfde plan en dezelfde vingerafdruk kunnen controleren.
- [x] Vanuit Instellingen een gecontroleerd Proxmox-plan opslaan en de vaste clusterpakketten op die node voorbereiden, na herauthenticatie.
- [x] De lokale node of witness controleren bij het klaarzetten en het plan behouden zodra de voorbereiding van afhankelijkheden is gestart.
- [x] Onderbroken Proxmox-activering kunnen beoordelen en pas opnieuw proberen als de beoordeelde VIP-resource nog stilstaat.
- [x] Onderbroken migratiereserveringen hervatten met een recente controle van de stand-bynode en afwijkende reserveringsbestanden behouden.
- [x] Witnesscertificaten en bevestigde stroomstatus van de peer controleren, en vóór de VIP-overdracht de vingerafdrukken voor het clusterverkeer vergelijken.
- [x] In een Proxmox-lab een bestaand Keepalived-paar migreren naar beschermde HA en het herstel na een onderbreking en tijdelijke uitval van de witness verifiëren.
- [x] In het beschermde Proxmox-lab controleren dat het VIP één eigenaar houdt en het cluster herstelt nadat de actieve VM abrupt is uitgeschakeld.
- [x] In Instellingen uitleggen hoe je gewone HA opzet en koppelt, en op beschermde nodes de actuele migratiefase tonen.
- [x] Op Proxmox releaserollback en automatische fencing van een geïsoleerde VIP-eigenaar verifiëren.
- [x] Controleren dat bij een netwerkscheiding zonder bereikbare witness geen node het VIP voert en dat daarna één eigenaar terugkeert.
- [x] Een node die al via OS-clusterbeheer is losgekoppeld lokaal uit bedrijf nemen en de-installeren, met behoud van vaste beheerregels.
- [x] Controleren dat B na enkele minuten uitval terugkomt en in een aparte proef een gemiste beleidsrevisie alsnog toepast.
- [x] Uitleggen hoe de inrichting verschilt voor vSphere, libvirt en fysieke servers, en onveilige VMware TLS- en IPMI-configuraties bij de statuscontrole weigeren.
- [x] Per node aanwijzingen geven voor aanmaken en koppelen, en actuele migratiegegevens tonen die je in Instellingen kunt vergelijken.
- [x] Het uit gebruik nemen van een node na een onderbreking hervatten en een herhaalde aanvraag veilig herkennen.
- [x] De inrichting van beschermde HA op Proxmox begeleiden in Instellingen. Toon de witness, netwerktoegang, rechten en lokale controles. De OS-beheerder rondt de inschrijving en activering af.
- [x] Bij de voorbereiding op Proxmox de eerstvolgende stap op de lokale node tonen en voor andere platforms de mogelijkheden uitleggen voordat specifieke velden verschijnen.
- [x] Een bestaand Proxmox HA-paar gecontroleerd laten overstappen en herstel na onderbreking beschrijven. Beide zijn in het migratielab getest.
- [x] Bevestigen dat ProxyPermit een OS-beheerd KVM/libvirt-cluster herkent en dat het cluster een bevroren VM uitschakelt voordat het VIP verhuist.
- [x] Op een live stand-bynode geïsoleerd verifiëren dat verlopen Proxmox-fencingtokens en witnessclientcertificaten worden geweigerd.
- [x] Op Proxmox controleren dat een losgekoppelde beschermde node uit bedrijf kan en de console kan worden verwijderd terwijl de andere node het VIP bedient en SSH beschikbaar blijft.
- [ ] Maak een procedure om een lid van een beschermd cluster definitief te vervangen. Controleer dat de overblijvende node quorum behoudt, dat de vervanger het beleid synchroniseert en het VIP kan overnemen, en dat de oude node niet meer kan deelnemen of een van beide nodes kan uitschakelen.
- [ ] Aanvullende virtualisatieplatformen en fysieke hosts ondersteunen, met per platform de mogelijkheden en beperkingen.
- [ ] Herstel na stroomuitval, netwerkonderbrekingen en een lang offline gebleven node verder controleren voordat de volledige inrichting als productieklaar wordt aangeboden.
- [x] Gecontroleerd VIP-herstel testen wanneer bewaarde monitorfouten beide nodes uitsluiten. Zie de [Proxmox-proef](verification.md#vip-herstellen-en-een-losgekoppelde-node-verwijderen-op-proxmox).
- [ ] De oorzaak van incidentele time-outs van de VIP-monitor onderzoeken. Zie de [eerste observatie](verification.md#vip-van-b-naar-a-na-een-monitortime-out).

B blijft na verkeersovername alleen-lezen voor gedeeld proxybeleid. Een witness
moet niet afhankelijk zijn van hetzelfde storingspunt als de proxynodes.
Zie de [HA-handleiding](hoge-beschikbaarheid.md) voor de voorwaarden en de
[acceptatieproeven](verification.md#acceptatieproeven-voor-beschermde-ha-in-v140)
voor wat er nog bewezen moet worden.

## Snellere en duidelijkere logs

**Wat je eraan hebt:** problemen snel onderzoeken, zonder lang te wachten of
steeds dezelfde meldingen door te moeten lezen.

- [x] Het logvenster sneller openen met kleinere pagina’s en een begrensde scan van recente regels.
- [ ] Nieuwe logregels bijladen zonder steeds de hele lijst op te halen of je leespositie onnodig te veranderen.
- [ ] Zoeken en filteren op bron, niveau en datum vlot en betrouwbaar laten werken, ook voor archieven binnen de bewaartermijn.
- [x] Herhaalde certificaatvertrouwensmeldingen per minuut bundelen met een teller en korte uitleg. De ruwe records blijven beschikbaar voor onderzoek.
- [ ] Belangrijke TLS-fouten duidelijk onderscheiden van herhaalde browsermeldingen, zodat een verlopen certificaat of onveilige verbinding opvalt.
- [x] Tonen wanneer logs voor het laatst zijn opgehaald, waarschuwen bij mogelijk verouderde regels en live verversen na herstel van de verbinding.

Rolcontroles, afscherming van gegevens en bewaartermijnen blijven behouden:
standaard 30 dagen voor beheer- en auditlogs en 7 dagen voor Tinyproxy- en
verkeerslogs. Certificaatcontrole wordt niet uitgeschakeld om meldingen te
verminderen. Zie [Logging](logging.md) voor de huidige mogelijkheden.

## HTTPS als standaard voor beheer

**Wat je eraan hebt:** een beveiligd beheeradres bij nieuwe installaties en
certificaatbeheer zonder voor elke handeling de terminal te gebruiken.

- [x] In de ontwikkelversie HTTPS met een eigen certificaatautoriteit (CA) per node aanbieden, inclusief gecontroleerde activering, vervalwaarschuwingen, vernieuwing en herstel.
- [x] Systeembeheerders deze certificaathandelingen via de webinterface laten uitvoeren, met wachtwoordbevestiging en optionele 2FA.
- [x] Bij nieuwe installaties via de wizard HTTPS met een lokale CA voorstellen.
- [ ] De standaard voor installaties zonder wizard beoordelen en bestaande HTTP-installaties begeleiden bij de overstap.
- [x] Tijdens installatie een lokale CA of een eigen PEM-certificaat aanbieden en beide opties testen op Ubuntu-VM’s in VMware.
- [x] Controleren of een Keepalived-paar zijn HTTPS-instellingen behoudt bij een upgrade en bij het terugzetten van de stand-bynode. Zie de [VMware-proef](verification.md#upgrade-en-rollback-op-een-keepalived-paar-met-https).
- [ ] Automatische vernieuwing voor extern uitgegeven certificaten onderzoeken, waaronder ACME waar dat past.
- [ ] HTTPS-beveiligingsheaders, waaronder HSTS, zorgvuldig toepassen en cookies en doorverwijzingen daarop afstemmen.
- [ ] De volledige installatie-, upgrade- en herstelprocedure afronden voor zelfstandige nodes en HA-opstellingen.

Een lokaal uitgegeven certificaat moet eerst worden vertrouwd op de computers
waarmee je de beheerinterface gebruikt. Zie [HTTPS voor beheer](beheer-https.md).

## Betrouwbare beleidswijzigingen en sleutelvernieuwing

**Wat je eraan hebt:** filterregels en gedeelde instellingen komen betrouwbaar
op de andere node aan. Sleutel- en certificaatwijzigingen hebben een duidelijke
uitkomst, ook als de verbinding tussentijds wegvalt.

- [x] Automatische beleidssynchronisatie en optioneel herladen van filters met behoud van bestaande verbindingen ondersteunen.
- [x] Peersleutels en interne HA-certificaten gecontroleerd vernieuwen en daarna het vertrouwen in de oude sleutels of certificaten intrekken.
- [x] In de ontwikkelversie het intrekken en opnieuw koppelen verder beveiligen, inclusief bescherming tegen hergebruik van uitnodigingen.
- [ ] Herstel bij stroomuitval, volle schijven, onderbroken overdrachten en gelijktijdige wijzigingen verder controleren.
- [ ] Voor iedere beheerhandeling duidelijk registreren wie die uitvoerde en of ze is voltooid of herstel nodig heeft.
- [ ] Herladen van filters pas standaard inschakelen zodra de resterende betrouwbaarheidscontroles zijn afgerond.

Bij herladen blijven bestaande verbindingen open. Nieuwe filterregels sluiten
bestaande tunnels dus niet achteraf. Zie [Filters herladen](beheer.md#optioneel-filters-herladen).

## Duidelijker beheer via het gedeelde adres

**Wat je eraan hebt:** na een HA-overname weten welke node je beheert en waarom
je eventueel opnieuw moet inloggen.

- [x] In de ontwikkelversie A of B en een optioneel openbaar label tonen op het inlogscherm van beheerde HA.
- [x] In Instellingen de vaste nodeaanduiding naast de lokale VIP-status tonen.
- [x] Tijdens het inloggen bij een vastgestelde nodewissel om een nieuwe aanmelding vragen.
- [x] Een vastgestelde nodewissel op een geopende beheerpagina uitleggen en onopgeslagen invoer zichtbaar houden totdat de beheerder de console opnieuw opent.
- [x] Lokale accounts, optionele 2FA en een geopend formulier in een mobiele browser testen tijdens een gecontroleerde VIP-overname.

Accounts en sessies zijn nu lokaal. Verkeersovername maakt een account op de
andere node niet automatisch beschikbaar.

### Beleid beheren als alleen het VIP bereikbaar is

Sommige beheernetwerken geven alleen toegang tot het VIP. In de huidige
beschermde opstelling kan B het verkeer blijven bedienen nadat A hersteld is.
Gedeeld beleid wijzigen kan alleen op A. Een beheerder die via het VIP op B
uitkomt, kan daardoor geen regel toevoegen.

- [ ] A na herstel weer de voorkeursnode voor beheer maken. Ontwerp automatische terugkeer van het VIP naar A nadat de node een tijd stabiel is en quorum, fencing en actueel beleid zijn bevestigd. Test dat het VIP niet heen en weer verhuist als A steeds kort uitvalt.
- [ ] Beleidsbeheer via het VIP mogelijk houden zolang A onbereikbaar is. Zorg dat maar één geauthenticeerde node tegelijk mag publiceren. Geef B pas schrijfrecht als quorum en fencing bevestigen dat A niet meer kan schrijven. Trek beleidsrevisies gelijk voordat A het schrijfrecht terugkrijgt.
- [ ] Testen vanuit een beheernetwerk dat alleen het VIP toestaat. Controleer of een systeembeheerder na overname en na terugkeer van A een regel kan toevoegen, met lokale aanmelding, rolcontrole, auditregistratie en bescherming tegen dubbele publicatie.

Pacemaker combineert de voorkeur voor een node met de score voor het behouden
van de huidige eigenaar. Met de huidige instellingen weegt behoud op een
gezonde B zwaarder dan de voorkeur voor A. Terugkeer van het VIP en overdracht
van schrijfrechten zijn aparte handelingen. [Plaatsing in Pacemaker](https://clusterlabs.org/projects/pacemaker/doc/3.0/Pacemaker_Explained/html/constraints.html) · [Fencing](https://clusterlabs.org/projects/pacemaker/doc/3.0/Pacemaker_Explained/html/fencing.html)

## Later te onderzoeken

Dit zijn ontwerponderwerpen; de uiteindelijke uitvoering staat nog niet vast.

- [ ] **Optionele accountsynchronisatie:** gebruikers op meerdere nodes beheren, met expliciete keuzes bij bestaande gebruikersnamen en behoud van lokale herstelaccounts. Wachtwoorden en 2FA-instellingen mogen niet ongemerkt worden overschreven.
- [ ] **Aanvullende berichtondertekening:** beoordelen of dit de bestaande beveiligde synchronisatie aantoonbaar versterkt en wat dit betekent voor sleutelbeheer en herstel.

## Meedenken of bijdragen

Meld een reproduceerbaar probleem of bespreek een voorstel via
[GitHub-issues](https://github.com/Dantdmnl/ProxyPermit/issues). Beschrijf wat je
probeert te doen, de gebruikte versie en of het om een zelfstandige node of HA
gaat. Deel geen wachtwoorden, koppelcodes, sleutels of onbewerkte privatelogs.
