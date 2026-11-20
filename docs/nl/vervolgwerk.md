# Roadmap

[English](../en/planned-work.md) · [Documentatie](README.md)

Welke verbeteringen komen eraan en wat heb je daar als beheerder aan? De
belangrijkste ontwikkelprioriteit is HA met bescherming tegen onveilige overname.
Ook gebruiksgemak, HTTPS en logging krijgen aandacht.

Bijgewerkt op 17 november 2026, met **v1.3.0** als uitgangspunt voor uitgebrachte
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

## Beschikbaar om te testen in v1.4.0-dev.1

- **Veiliger HA-beheer:** de koppeling intrekken vraagt opnieuw om het beheerderswachtwoord en, als 2FA aanstaat, een verse authenticatorcode. De beschermingsstatus controleert strenger of de clustergegevens betrouwbaar zijn.
- **Voorbereiding van beschermde HA:** systeembeheerders kunnen voorwaarden laten controleren. De experimentele clusterintegratie is uitgebreid, maar de volledige inrichting via de webinterface ontbreekt nog.
- **HTTPS instellen via de webinterface:** systeembeheerders kunnen certificaten voorbereiden en vernieuwen en HTTPS activeren. HTTPS is nog niet standaard bij installatie. Zie [HTTPS voor beheer](beheer-https.md).

Deze functies zitten in de [prerelease v1.4.0-dev.1](https://github.com/Dantdmnl/ProxyPermit/releases/tag/v1.4.0-dev.1),
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
- [ ] De eerste inrichting en het toevoegen van een node begeleiden via de webinterface, met duidelijke uitleg over benodigde hosts, netwerktoegang en rechten.
- [ ] Bestaande HA-installaties gecontroleerd laten overstappen, met een begrijpelijke herstelprocedure als de omzetting wordt onderbroken.
- [ ] Terugdraaien, een node verwijderen en de-installatie afronden voor beschermde clusters, met behoud van beheertoegang.
- [ ] Aanvullende virtualisatieplatformen en fysieke hosts ondersteunen, met per platform de mogelijkheden en beperkingen.
- [ ] Herstel na stroomuitval, netwerkonderbrekingen en een lang offline gebleven node verder controleren voordat de volledige inrichting als productieklaar wordt aangeboden.

B blijft na verkeersovername alleen-lezen voor gedeeld proxybeleid. Een witness
moet niet afhankelijk zijn van hetzelfde storingspunt als de proxynodes.
Zie de [HA-handleiding](hoge-beschikbaarheid.md) voor de voorwaarden en het
[verificatierapport](verification.md) voor de huidige testdekking.

## Snellere en duidelijkere logs

**Wat je eraan hebt:** problemen snel onderzoeken, zonder lang te wachten of
steeds dezelfde meldingen door te moeten lezen.

- [ ] Het logvenster sneller openen en laten reageren, ook bij een lange bewaarde geschiedenis.
- [ ] Nieuwe logregels bijladen zonder steeds de hele lijst op te halen of je leespositie onnodig te veranderen.
- [ ] Zoeken en filteren op bron, niveau en datum vlot en betrouwbaar laten werken, ook voor archieven binnen de bewaartermijn.
- [ ] Herhaalde certificaatvertrouwensmeldingen compact tonen, met een teller, tijdvak en uitleg over wat je kunt doen. De ruwe records blijven beschikbaar voor onderzoek.
- [ ] Belangrijke TLS-fouten duidelijk onderscheiden van herhaalde browsermeldingen, zodat een verlopen certificaat of onveilige verbinding opvalt.
- [ ] Duidelijk aangeven wanneer de verbinding is verbroken of de getoonde logs niet meer actueel zijn.

Rolcontroles, afscherming van gegevens en bewaartermijnen blijven behouden:
standaard 30 dagen voor beheer- en auditlogs en 7 dagen voor Tinyproxy- en
verkeerslogs. Certificaatcontrole wordt niet uitgeschakeld om meldingen te
verminderen. Zie [Logging](logging.md) voor de huidige mogelijkheden.

## HTTPS als standaard voor beheer

**Wat je eraan hebt:** een beveiligd beheeradres bij nieuwe installaties en
certificaatbeheer zonder voor elke handeling de terminal te gebruiken.

- [x] In de ontwikkelversie HTTPS met een eigen certificaatautoriteit (CA) per node aanbieden, inclusief gecontroleerde activering, vervalwaarschuwingen, vernieuwing en herstel.
- [x] Systeembeheerders deze certificaathandelingen via de webinterface laten uitvoeren, met wachtwoordbevestiging en optionele 2FA.
- [ ] HTTPS standaard maken bij nieuwe installaties en bestaande HTTP-installaties begeleid laten overstappen.
- [ ] Eigen certificaten en interne CA’s ondersteunen; automatische uitgifte via ACME onderzoeken waar dat past.
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

- [ ] Vóór het inloggen een herkenbare nodeaanduiding tonen zonder interne netwerkgegevens of accounts openbaar te maken.
- [ ] Na het inloggen de vaste node-identiteit en de actuele VIP-eigenaar duidelijk van elkaar onderscheiden.
- [ ] Een nodewissel uitleggen en onopgeslagen invoer behouden waar dat veilig kan, zonder wachtwoorden of 2FA-codes over te dragen.
- [ ] De inlog- en sessie-ervaring bij overname afronden voor verschillende lokale accounts, optionele 2FA en mobiel gebruik.

Accounts en sessies zijn nu lokaal. Verkeersovername maakt een account op de
andere node niet automatisch beschikbaar.

## Later te onderzoeken

Dit zijn ontwerponderwerpen; de uiteindelijke uitvoering staat nog niet vast.

- [ ] **Optionele accountsynchronisatie:** gebruikers op meerdere nodes beheren, met expliciete keuzes bij bestaande gebruikersnamen en behoud van lokale herstelaccounts. Wachtwoorden en 2FA-instellingen mogen niet ongemerkt worden overschreven.
- [ ] **Aanvullende berichtondertekening:** beoordelen of dit de bestaande beveiligde synchronisatie aantoonbaar versterkt en wat dit betekent voor sleutelbeheer en herstel.

## Meedenken of bijdragen

Meld een reproduceerbaar probleem of bespreek een voorstel via
[GitHub-issues](https://github.com/Dantdmnl/ProxyPermit/issues). Beschrijf wat je
probeert te doen, de gebruikte versie en of het om een zelfstandige node of HA
gaat. Deel geen wachtwoorden, koppelcodes, sleutels of onbewerkte privatelogs.
