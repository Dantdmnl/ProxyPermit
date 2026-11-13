# Vervolgwerk

[English](../en/planned-work.md) · [Documentatie](README.md)

Bijgewerkt op 13 november 2026. Dit zijn plannen, geen reeds beschikbare functies.

## Prioriteit 1: veilig gedrag bij HA-storingen

- Ontwerp bescherming tegen split-brain. Kies expliciet tussen een quorum-/witness-
  ontwerp met afdwingbare uitschakeling en fencing. Alleen een derde host pingen
  of een extra heartbeat toevoegen geeft geen garantie op exclusief VIP-bezit.
- Leg vast wanneer een node het VIP mag voeren, wanneer hij moet stoppen en hoe
  beheer en herstel werken als de peer of het beslissende mechanisme onbereikbaar is.
- Houd VIP-eigenaarschap en schrijfrechten voor gedeeld beleid gescheiden: een
  failover mag B niet stilzwijgend tot beleidsschrijver maken.
- Maak onzekere clusterstatus, verloren peercontact en afwijkende beleidsrevisies
  duidelijk zichtbaar en leg wijzigingen vast in de auditlog.

**Acceptatie:** test op afzonderlijke VM’s een volledige en een eenzijdige
netwerkonderbreking, verlies van de witness/fencingdienst, een vastgelopen proxy,
herstel van de verbinding en terugkeer van een oude node. Verifieer exclusief
VIP-bezit binnen het gekozen foutmodel, het gedocumenteerde stopgedrag en behoud
van het laatst geldige beleid. Twee actieve nodes mogen niet als gezond worden
weergegeven. Documenteer de grenzen van de garantie.

## Prioriteit 2: wijzigingen toepassen zonder verbindingen te verbreken

- Onderzoek per ondersteunde Tinyproxy-versie welke wijzigingen via `SIGUSR1`
  kunnen worden herladen en welke een volledige herstart vereisen. Controleer
  ook de systemd-unit en de manier waarop Tinyproxy is gestart.
- Gebruik reload voor aantoonbaar geschikte wijzigingen, op A én bij toepassing
  op B. Houd validatie, opslaan, activering en herstel gescheiden.
- Geef expliciet aan wanneer een herstart nodig is. Voer na een mislukte of
  onzekere reload niet ongemerkt alsnog een verstorende herstart uit.
- Controleer dat de nieuwe configuratie daadwerkelijk werkt; alleen het succesvol
  versturen van een signaal is geen bewijs van geslaagde activering.

**Acceptatie:** houd langlopende HTTP-overdrachten en HTTPS-CONNECT-tunnels open
terwijl regels worden toegevoegd, verwijderd en gesynchroniseerd. Bestaande
verbindingen blijven bij ondersteunde reloads werken; nieuwe verbindingen volgen
het nieuwe beleid. Test ongeldige configuratie, een geweigerde reload en herstel.
Leg vast dat bestaande tunnels door het behouden van verbindingen niet met
terugwerkende kracht door nieuwe filterregels worden afgesloten.

## Prioriteit 3: beheer van HA-sleutels en certificaten

- Voeg gecontroleerde rotatie van peersleutels toe, met een begrensde
  overgangsperiode en verificatie voordat de oude sleutel wordt ingetrokken.
- Test intrekking op beide nodes, opnieuw koppelen en het afwijzen van verlopen
  of reeds gebruikte uitnodigingen. Toon synchronisatiefouten zonder geheimen te loggen.
- Ontwerp veilige certificaatvernieuwing met pinwijziging. Een pinmismatch mag
  nooit leiden tot automatisch uitschakelen van certificaatcontrole.
- Audit wie koppelt, roteert of intrekt, op welke node en met welk resultaat.
  Bewaar geen bearer-sleutels, uitnodigingen of 2FA-geheimen in auditregels.

**Acceptatie:** de oude sleutel werkt na intrekking niet meer; onderbroken rotatie
leidt tot een herkenbare, herstelbare toestand. Een onverwacht certificaat wordt
geweigerd. Beleid blijft bij authenticatie- of transportfouten ongewijzigd.

## Verplichte regressietests voor deze verbeteringen

- Onderbreek een overdracht en herstart de node tijdens elke activeringsfase.
- Test een volle schijf, ontbrekende schrijfrechten, ongeldige/onvolledige
  antwoorden, gelijktijdige publicaties en achterlopende revisies.
- Test gewijzigd, onverwacht en verlopen TLS-certificaatmateriaal. Bepaal en
  documenteer daarbij expliciet welke geldigheidscontrole de pinning uitvoert.
- Controleer atomair opslaan, rollback, locks, accountrechten en anti-lockoutregels.
  Accounts en nodespecifieke instellingen blijven lokaal.
- Controleer zelfstandig bedrijf en gekoppelde HA-nodes, plus begrijpelijke
  foutmeldingen in NL/EN. Leg resultaten en resterende beperkingen vast.

## Later beoordelen: extra berichtauthenticatie

Beoordeel of HMAC-SHA256 per verzoek met timestamps en eenmalige nonces bovenop
TLS een concrete extra dreiging afdekt. De huidige automatische HA-sync gebruikt
gepinde TLS, een peersleutel en SHA-256-revisies; de legacy-sync gebruikt al HMAC.
HMAC is geen vervanging voor TLS, sleutelrotatie, quorum of fencing. Met een
buitgemaakte HMAC-sleutel kunnen ook nieuwe geldige verzoeken worden gemaakt.
Implementeer dit pas na een onderbouwde afweging en tests voor replay en klokafwijking.

## Afzonderlijk traject: optionele accountreplicatie

- Optionele accountreplicatie apart van beleidsreplicatie ontwerpen. Bepalen
  welke accounts lokaal blijven, hoe gelijke gebruikersnamen worden afgehandeld,
  of bestaande wachtwoordhashes vervangen mogen worden, hoe het intrekken van toegang en
  wijzigingen van sessieversies worden doorgegeven en wat er met 2FA-geheimen en hersteltoegang gebeurt.
  Bestaande lokale accounts niet stilzwijgend overschrijven. Accounts worden nu
  niet gerepliceerd.
