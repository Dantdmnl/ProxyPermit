# Eerste stappen

[English](../en/getting-started.md) · [Documentatie](README.md)

**Voor:** iemand met een adres voor de beheerinterface en een gebruikersaccount.
**Resultaat:** je kunt inloggen en de juiste node herkennen. Met beheerrechten kun je ook een eerste regel aanpassen.
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

**Overzicht** toont de node en servicestatus. Een node is één server. Bij een
gekoppelde opstelling verander je gedeelde regels op **node A**. **Node B**
ontvangt die wijzigingen automatisch en toont een melding dat je er niet kunt wijzigen.

Bij een storing kan een andere node het verkeer overnemen. Dat maakt B niet de
node waarop je gedeelde regels aanpast. Lees [Hoge beschikbaarheid](hoge-beschikbaarheid.md).

## 4. Voeg één regel toe

Hiervoor heb je de rol **Proxybeheerder** of **Systeembeheerder** nodig.
Met de rol **Monitor** kun je regels bekijken. Lees meer over [rollen](rollen.md).

Controleer eerst of de installatie een **allowlist** of **blocklist** gebruikt:

- Een allowlist laat passende bestemmingen toe en blokkeert de rest.
- Een blocklist blokkeert passende bestemmingen en laat de rest toe.

Het effect van een regel hangt dus af van de actieve filtermodus.

1. Kies op de zelfstandige node of op A voor **Nieuwe regel**.
2. Kies **Domeinnaam** en vul een echte testbestemming in die je beheert.
3. Beschrijf waarom de regel nodig is.
4. Wacht op de validatie. Laat **Tinyproxy herstarten na opslaan** aanstaan als je
   de wijziging nu wilt toepassen en kies **Regel opslaan**.
5. Test vanaf een apparaat dat de proxy daadwerkelijk gebruikt. Controleer een
   toegestane én een geblokkeerde bestemming. De beheerinterface openen is geen
   test van het proxyverkeer.
6. Controleer bij HA onder **Instellingen** of B dezelfde beleidsrevisie heeft toegepast.

Een domein zoals `example.com` past op dat domein. `*.example.com` neemt ook de
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
