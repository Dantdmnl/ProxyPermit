# Roadmap

[English](../en/planned-work.md) · [Documentatie](README.md)

Waar ProxyPermit naartoe werkt, wat je nu kunt gebruiken en wat nog moet gebeuren.
Bijgewerkt op 15 november 2026 voor **v1.3.0**. Prioriteiten kunnen veranderen;
er zijn geen toegezegde opleverdatums. Een vinkje betekent dat die specifieke
stap is gecontroleerd, niet dat het hele onderdeel klaar is voor productie.

## Beschikbaar in v1.3.0

| Onderdeel | Status | Wat betekent dit voor jou? |
| --- | --- | --- |
| HA met twee nodes | Beschikbaar | Keepalived verzorgt VIP-overname; B ontvangt automatisch het beleid dat A publiceert. Accounts en hostinstellingen blijven lokaal. |
| Peersleutel vernieuwen | Beschikbaar na bijwerken van beide nodes | Vernieuw de sleutel via A’s interface met wachtwoordbevestiging en optionele 2FA; B bevestigt de nieuwe sleutel automatisch. |
| Intern HA-certificaat vernieuwen | Handeling voor de OS-beheerder | Vernieuw het gepinde HA-certificaat via een voorbereide overgang met herstelregistratie. Dit schakelt HTTPS voor de beheerwebsite niet in. |
| Filter herladen met behoud van verbindingen | Optioneel inschakelen | Getest met Tinyproxy 1.11.1 en 1.11.3. Andere instellingen vereisen nog een herstart. Reload staat standaard uit. |
| Quorum, witness en fencing | Experimentele integratie | Getest op een afzonderlijk KVM-cluster. De eerste inrichting vereist nog OS-clusterbeheer; de gewone HA-wizard regelt dit niet. |
| Onderhoud aan een beveiligd cluster | Experimentele integratie | Applicatie-updates per node en herstel zijn gecontroleerd op een door het OS voorbereid cluster. Volledige installatie/verwijdering en andere platformen staan nog open. |

**Waarom toont de interface ‘Quorum en fencing zijn niet ingesteld’?**
De gewone HA-inrichting stelt Keepalived en beleidsynchronisatie in. Een witness
en fencing worden nog niet ingericht. De melding is bij zulke installaties dus
te verwachten en betekent niet dat de synchronisatie stuk is. VRRP met twee
nodes alleen kan bij een netwerkonderbreking geen exclusief VIP-bezit garanderen.

Lees vóór productiegebruik [HA instellen en de beperkingen](hoge-beschikbaarheid.md#begrijp-de-bescherming-bij-overname).
Een upgrade zet een bestaande HA-installatie niet ongemerkt om naar een
cluster dat quorum gebruikt.

## Als volgende: begeleide split-brainbescherming

**Doel:** een beheerder helpen een cluster in te richten en te onderhouden dat
een node kan stoppen of uitschakelen voordat een onveilige overname plaatsvindt.

- [x] Het Pacemaker/Corosync-ontwerp testen met een TLS-witness en afgeschermde libvirt-fencing.
- [x] Vastgelopen proxy’s, achtergebleven VIP’s, eenzijdige/volledige peeronderbrekingen, witnessverlies en onbereikbare fencing op afzonderlijke VM’s controleren.
- [x] Automatisch verouderende beschermingsstatus tonen zonder clustergeheimen toegankelijk te maken voor de webaccount.
- [x] Exclusieve onderhoudsreservering, VIP-overname en expliciet herstel na fouten controleren.
- [ ] Begeleide eerste inrichting en toetreding toevoegen, met duidelijke voorwaarden voor witness en fencing.
- [ ] Een veilige migratie vanuit bestaande Keepalived-installaties en een gecontroleerde herstelroute toevoegen.
- [ ] Clusterbewust terugdraaien, uit bedrijf nemen en verwijderen afronden; beheertoegang behouden en alleen eigen firewallregels opruimen.
- [ ] Platformspecifieke fencing en beperkt bevoegde accounts voor andere hypervisors en fysieke hosts controleren.
- [ ] Volledige herstelproeven afronden voor verouderde nodes, onderbroken inrichting en onbereikbare beslissende infrastructuur.

**Voordat dit klaar is:** exclusief VIP-bezit aantonen binnen het beschreven
foutmodel, overname tegenhouden zolang noodzakelijke fencing niet is bevestigd
en B na verkeersovername alleen-lezen houden voor beleid. Een witness op dezelfde
fysieke host biedt geen onafhankelijk storingsdomein.

## Als volgende: herstel van reload en sleutels verder testen

**Doel:** normale beleidswijzigingen minder verstorend maken, met betrouwbaar
herstel en behoud van veilige authenticatie tussen de nodes.

- [x] Doorlopende HTTP-overdrachten en CONNECT-tunnels behouden tijdens ondersteunde filterreloads.
- [x] Automatische toepassing van A naar B, publicatievolgorde en native leesrechten controleren zonder beheergeheimen bloot te stellen.
- [x] Echte peersleutelrotatie en gepinde certificaatvernieuwing op het HA-paar controleren, inclusief intrekking van oude sleutels/pins.
- [x] Verkeerde of verlopen certificaten en certificaatwissels tussen handshakes weigeren voordat de peersleutel wordt verstuurd.
- [x] Opslagfouten en verloren bevestigingsantwoorden geïsoleerd testen.
- [ ] Stroomuitval en herstarten tijdens elke sleutel-, certificaat- en activeringsfase op VM’s controleren.
- [ ] Regressietests voor volle schijven, rechten, onderbroken overdrachten en gelijktijdige publicaties afronden.
- [ ] Intrekking, opnieuw koppelen en eenmalige uitnodigingen op beide nodes volledig controleren.
- [ ] Registratie van de uitvoerende beheerder en foutmeldingen voor alle sleutelhandelingen nalopen.
- [ ] Reload pas standaard inschakelen nadat de overige acceptatiecontroles slagen.

Bestaande tunnels blijven bij een ondersteunde reload open: een nieuwe filterregel
sluit ze niet achteraf af. Zie [Reload instellen](beheer.md#optioneel-filters-herladen)
en het [verificatierapport](verification.md) voor gecontroleerde situaties en beperkingen.

## Daarna: standaard HTTPS voor beheer

**Doel:** bij nieuwe installaties een beveiligd beheeradres aanbieden en bestaande
HTTP-installaties via een bewuste, herstelbare wijziging laten overstappen.

- [ ] Per node een uniek zelfondertekend certificaat en afgeschermde privésleutel maken; de vingerafdruk via de vertrouwde installer/console tonen.
- [ ] Eigen certificaten en interne CA’s ondersteunen; optioneel ACME beoordelen waar dat past.
- [ ] Waarschuwingen vóór verlopen en gecontroleerde vernieuwing toevoegen, los van HA-replicatiepins.
- [ ] Anti-lockouttoegang bij migratie behouden, TLS vóór activering controleren en terugdraaien mogelijk maken.
- [ ] HSTS alleen bij gecontroleerde HTTPS-antwoorden toepassen; veilige cookies, redirects en herkenbare serverheaders nalopen.
- [ ] Installatie, upgrade, vertrouwen, vernieuwing en herstel op zelfstandige en HA-nodes testen in NL/EN.

## Later: accountsynchronisatie en extra authenticatie

- [ ] Optionele accountsynchronisatie ontwerpen met expliciete afspraken voor bestaande gebruikersnamen, wachtwoordwijzigingen, 2FA, sessie-intrekking en lokale herstelaccounts. Nooit ongemerkt een lokaal account overschrijven.
- [ ] Beoordelen of HMAC-SHA256 per verzoek iets toevoegt aan gepinde TLS. Eerst tests voor herhaalde verzoeken en klokafwijkingen eisen.

Dit zijn ontwerponderwerpen, geen ondersteunde functies. HA-beleidsreplicatie
repliceert geen accounts. HMAC zou TLS, sleutelrotatie, quorum en fencing niet vervangen.

## Voortgang volgen of bijdragen

Bekijk de [release notes](https://github.com/Dantdmnl/ProxyPermit/releases) voor
uitgebrachte wijzigingen en [verificatie](verification.md) voor testresultaten en
beperkingen. Meld reproduceerbare problemen of bespreek voorstellen via
[GitHub-issues](https://github.com/Dantdmnl/ProxyPermit/issues). Vermeld de versie en
het type installatie; deel geen koppelcodes, wachtwoorden, sleutels of privatelogs.
