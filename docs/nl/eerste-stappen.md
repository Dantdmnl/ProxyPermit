# Eerste stappen

[English](../en/getting-started.md) · [Documentatie](README.md)

**Voor wie:** je hebt een adres voor de beheerinterface en een gebruikersaccount.

**Na deze uitleg:** je kunt inloggen en zien welke node je gebruikt. Als je
proxybeheerder of systeembeheerder bent, kun je ook een regel toevoegen.
Is ProxyPermit nog niet geïnstalleerd? Begin dan bij [Installeren](installatie.md).

## 1. Gebruik het juiste adres

Vraag je systeembeheerder om het **adres van de beheerinterface**, je gebruikersnaam
en je eerste wachtwoord. Open dat adres in je browser. Het proxyadres, dat vaak
op `:3128` eindigt, is voor verkeer van apparaten; het is niet de beheerinterface.

Gebruik het HTTPS-adres van je organisatie of de lokale SSH-tunnel die je
beheerder heeft ingericht. Ga niet verder bij onverwachte certificaatwaarschuwingen.

## 2. Log in

Kies **NL** of **EN** en vul je gegevens in. Met het oogje controleer je het
wachtwoord. Staat tweefactorauthenticatie (2FA) aan voor je account? Vul dan de
actuele zescijferige code uit je authenticatorapp in.

2FA is optioneel. Je kunt het later instellen onder **Instellingen → Mijn account**.
Stuur wachtwoorden, QR-codes voor 2FA en koppelcodes nooit mee in een supportticket.

## 3. Controleer op welke node je werkt

**Overzicht** toont de node en de status van Tinyproxy. Een node is één server.
Heeft jouw installatie twee gekoppelde nodes? Dan pas je gedeelde regels aan op
**node A**. **Node B** ontvangt de wijzigingen automatisch en toont dat de
regels daar alleen-lezen zijn. Bij één zelfstandige node werk je op die node.

Bij een storing kan een andere node het verkeer overnemen. Dat maakt B niet de
node waarop je gedeelde regels aanpast. Lees [Hoge beschikbaarheid](hoge-beschikbaarheid.md).

## 4. Voeg één regel toe

Hiervoor heb je de rol **Proxybeheerder** of **Systeembeheerder** nodig.
Met de rol **Monitor** kun je regels bekijken. Lees meer over [rollen](rollen.md).

Controleer eerst onder **Proxyconfiguratie → Allowlist of blocklist** welke
filtermodus actief is. Een monitor kan deze pagina niet openen; vraag bij
twijfel een proxybeheerder. De gekozen modus bepaalt wat een regel doet:

- Een allowlist laat overeenkomende bestemmingen toe en blokkeert de rest.
- Een blocklist blokkeert overeenkomende bestemmingen en laat de rest toe.

Het effect van een regel hangt dus af van de actieve filtermodus.

1. Kies op de zelfstandige node of op A voor **Nieuwe regel**.
2. Kies **Domeinnaam** en vul een echte testbestemming in die je beheert.
3. Beschrijf waarom de regel nodig is.
4. Controleer de melding onder het invoerveld. Laat **Tinyproxy na het opslaan
   herstarten** aangevinkt als je de wijziging nu wilt toepassen en kies
   **Regel opslaan**. Wacht op de melding dat de regel is opgeslagen en de
   herstart is voltooid. Een herstart kan actieve verbindingen onderbreken.
5. Test vanaf een apparaat dat de proxy daadwerkelijk gebruikt. Controleer een
   toegestane én een geblokkeerde bestemming. De beheerinterface openen is geen
   test van het proxyverkeer.
6. Controleer bij gekoppelde HA-nodes onder **Instellingen → Hoge beschikbaarheid
   (HA) → Beleid synchroniseren** of de gepubliceerde revisie op A overeenkomt
   met de toegepaste revisie op B. Synchronisatie kan even duren.

De regel `example.com` geldt voor dat domein. `*.example.com` neemt ook de
subdomeinen mee. Dit zijn voorbeelden, geen websites die je moet toestaan.

## 5. Weet wat opslaan doet

| Actie | Wat gebeurt er? |
| --- | --- |
| Tekst in een formulier aanpassen | Alleen je huidige browserweergave verandert |
| Een configuratieconcept opslaan | De server bewaart het, maar Tinyproxy gebruikt nog de actieve versie |
| Publiceren en activeren | De gecontroleerde configuratie wordt toegepast en Tinyproxy herstart |

Regelvensters hebben ook een herstartkeuze. Een regel opslaan zonder herstart
activeert het nieuwe beleid niet. Let op het resultaat dat de interface toont.

Lees daarna de [werkinstructie](werkinstructie.md) voor importeren, filtermodi,
logs en accountinstellingen. Is het resultaat anders dan verwacht? Begin bij
[Problemen oplossen](problemen-oplossen.md) voordat je de wijziging herhaalt.
