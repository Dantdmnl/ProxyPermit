# Hoge beschikbaarheid instellen

[English](../en/high-availability.md) · [Documentatie](README.md)

**Voor:** een systeembeheerder met twee werkende zelfstandige nodes.
**Resultaat:** apparaten gebruiken één gedeeld proxyadres en B ontvangt automatisch
het beleid dat op A is gepubliceerd. Oefen eerst in het [VM-testlab](vm-lab.md).

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
2. Selecteer de gevonden HA-verbinding. Controleer alle ingevulde gegevens, vul
   het vaste adres van B in en controleer het VIP en clientnetwerk.
3. Bekijk het plan en de firewallregels. Bevestig met je huidige wachtwoord en,
   indien ingesteld, je authenticatorcode. Wacht op afronding en log opnieuw in
   nadat de beheerinterface is herstart.
4. Kies op A **Node toevoegen** en kopieer de koppelcode. Deze verloopt na tien
   minuten. Behandel de code als een wachtwoord; plaats hem niet in logs of tickets.
5. Kies op **B**: **HA instellen → Aansluiten met koppelcode**. Plak de code,
   selecteer de eigen HA-verbinding van B, controleer de wijzigingen en bevestig.
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
lokale HA- en proxydiensten. Dat zijn verschillende acties. Controleer vóór het
uit HA nemen of de andere node al verkeer via het VIP afhandelt. Volg
[Een HA-node uit gebruik nemen](installatie.md). De beheerinterface blijft
beschikbaar en de aparte UFW-regels voor anti-lockout blijven behouden.
