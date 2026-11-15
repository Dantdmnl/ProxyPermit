# Hoge beschikbaarheid instellen

[English](../en/high-availability.md) · [Documentatie](README.md)

**Voor:** een systeembeheerder met twee werkende zelfstandige nodes.
**Resultaat:** apparaten gebruiken één gedeeld proxyadres en B ontvangt automatisch
het beleid dat op A is gepubliceerd. Oefen eerst in het [VM-testlab](vm-lab.md).

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
vervangt geen echte fencingtest. Zie [onderhoud aan een beveiligd cluster](beheer.md#onderhoud-aan-een-door-het-os-beveiligd-cluster)
en de [roadmap](vervolgwerk.md) voor de ondersteunde grenzen en resterende stappen.
