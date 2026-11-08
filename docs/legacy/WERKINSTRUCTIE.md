> Legacy-documentatie: beschrijft de oorspronkelijke twee-node-inrichting. Zie de [actuele README](../../README.md).

# Werkinstructie ProxyPermit

## 1. Doel en doelgroep

Deze werkinstructie is bedoeld voor beheerders en operationele gebruikers die de whitelist beheren.

De instructie helpt je om:

- dagelijks veilig wijzigingen door te voeren;
- overzicht te houden op status en synchronisatie;
- fouten snel te herkennen en op te lossen.

Deze instructie is bewust praktisch opgesteld. Je hebt geen diepgaande technische kennis nodig om de stappen te volgen.

## 2. Wat je met de manager doet

In de manager voer je vooral deze taken uit:

- huidige whitelistregels bekijken;
- regels toevoegen, wijzigen en verwijderen;
- whitelistinhoud importeren en exporteren;
- status, meldingen en logs controleren.

## 3. Dagelijkse werkvolgorde

Gebruik bij reguliere werkzaamheden deze volgorde:

1. Log in op de webinterface.
2. Controleer de statusbalk (services en synchronisatie).
3. Voer de whitelistwijziging uit.
4. Controleer direct de uitkomst (melding, lijstweergave en eventuele waarschuwingen).
5. Controleer kort de logs op fouten.
6. Test indien nodig met een praktijkvoorbeeld (toegestane bestemming werkt, niet-toegestane bestemming blijft geblokkeerd).

Let op:

- Voer bij voorkeur kleine wijzigingen per keer door.
- Doe risicovolle wijzigingen buiten piekuren.

## 4. Whitelistregels beheren

### 4.1 Handmatig toevoegen

Bij toevoegen kun je kiezen tussen:

- domeininvoer;
- regex-invoer.

Je kunt optioneel een korte beschrijving toevoegen.
Deze beschrijving wordt als inline comment achter de regel opgeslagen.

Praktisch voorbeeld:

- Invoer: portal-team1.example.net
- Beschrijving: toegang leveranciersportaal
- Opgeslagen regel: ^portal\-team1\.example\.net(:[0-9]+)?$  # toegang leveranciersportaal

Werkstappen (toevoegen):

1. Open de webinterface en ga naar het whitelistoverzicht.
2. Kies het invoertype (domein of regex).
3. Vul de regel in.
4. Voeg optioneel een korte beschrijving toe (waarom de regel bestaat).
5. Klik op "Entry Toevoegen".
6. Controleer of de regel zichtbaar is in de lijst.
7. Voer een korte functionele check uit.

Let op:

- Gebruik regex alleen als domeininvoer niet voldoende is.
- Controleer regex extra zorgvuldig op typefouten.
- Guardrails: alleen ERE (geen PCRE/lookarounds), max 1000 tekens, geen geneste wildcard-repetitie (bijv. (.*)+), geen extreem hoge herhalingen (>999).

### 4.2 Regel bewerken

Bij bewerken gelden dezelfde principes als bij toevoegen:

- regex blijft regex;
- domeininvoer wordt netjes verwerkt;
- comment/description blijft gekoppeld aan de regel.

Werkstappen (bewerken):

1. Zoek de bestaande regel in de lijst.
2. Klik op bewerken.
3. Pas alleen aan wat nodig is.
4. Werk de beschrijving bij als de context is veranderd.
5. Sla op.
6. Controleer of de gewijzigde waarde direct zichtbaar is.
7. Test het effect op verkeer.

Let op:

- Bewerk liever niet meerdere complexe regexregels tegelijk.
- Controleer na bewerken altijd minimaal een relevante bestemming.

### 4.3 Regel verwijderen

Verwijder alleen regels die aantoonbaar niet meer nodig zijn.

Werkstappen (verwijderen):

1. Zoek de regel op inhoud en beschrijving.
2. Controleer of de regel echt niet meer nodig is.
3. Verwijder de regel.
4. Controleer direct of de regel uit de lijst verdwenen is.
5. Doe een korte nacontrole op afhankelijk verkeer.

Let op:

- Verwijder bij voorkeur in een rustige periode.
- Controleer na verwijderen of belangrijke applicaties blijven werken.

## 5. Import en export

### 5.1 Import

Import ondersteunt:

- commentregels die beginnen met `#`;
- inline comments, bijvoorbeeld `domein.tld # toelichting`.

Praktisch voorbeeld:

Invoerbestand:

```text
portal-team1.example.net # toegang leveranciersportaal
# tijdelijke projecttoegang
api-datafeed.example.org # koppeling externe datastroom
```

Na import (opgeslagen vorm):

```text
^portal\-team1\.example\.net(:[0-9]+)?$  # toegang leveranciersportaal
# tijdelijke projecttoegang
^api\-datafeed\.example\.org(:[0-9]+)?$  # koppeling externe datastroom
```

Tijdens import gebeurt automatisch:

- validatie van regels;
- normalisatie van domeinregels;
- overslaan van ongeldige regels;
- deduplicatie van dubbele regels.

Werkstappen (import):

1. Maak een werkbestand met een regel per regel.
2. Voeg comments toe waar nodig.
3. Open import in de webinterface.
4. Plak of laad de inhoud.
5. Controleer de preview.
6. Kies de juiste modus (Toevoegen of Vervangen).
7. Voer de import uit.
8. Controleer aantal geïmporteerde regels en eventuele foutmeldingen.
9. Voer minimaal een korte functionele check uit.

Keuzehulp voor importmodus:

- Toevoegen: bestaande whitelist blijft behouden, nieuwe regels worden toegevoegd.
- Vervangen: de volledige whitelist wordt vervangen.

Let op:

- Gebruik `Vervangen` alleen als je bewust een complete set wilt plaatsen.
- Bewaar vooraf altijd een export of backup.

### 5.2 Export

Gebruik export voor:

- backup van de actuele whitelist;
- review met collega's;
- overdracht naar een andere omgeving.

Werkstappen (export):

1. Open export in de webinterface.
2. Kies het gewenste formaat.
3. Download het bestand.
4. Sla het bestand op met datum/tijd in de bestandsnaam.
5. Gebruik export als referentie voor grotere wijzigingen.

## 6. Werken met comments

Gebruik comments kort en functioneel:

- waarom de regel bestaat;
- welk systeem/team de regel gebruikt;
- eventueel een ticket- of wijzigingsreferentie.

Let op:

- Vermijd lange teksten in comments.
- Vermijd privacygevoelige gegevens in comments.

## 7. Inloggen en accountbeveiliging

### 7.1 Normaal gedrag

Bij herhaalde foutieve inlogpogingen kan tijdelijk een inlogbeperking actief worden. Dat is een normale beveiligingsmaatregel.

### 7.2 Als je tijdelijk geblokkeerd bent

1. Wacht tot de blokkade is verlopen.
2. Controleer gebruikersnaam en wachtwoord.
3. Probeer opnieuw met correcte gegevens.

Escalatie:

- Blijft het probleem terugkomen, schakel beheer/operations in.

## 8. Synchronisatie en samenwerken in HA

In een HA-opstelling kunnen meerdere nodes actief zijn.

Werkrichtlijn:

- voer wijzigingen bij voorkeur vanuit één beheerpunt uit;
- controleer na een wijziging of status/synchronisatie groen blijft;
- los conflictmeldingen op voordat je nieuwe wijzigingen uitvoert.

Let op:

- Doe geen nieuwe bulkimport wanneer er een actief conflict is.

## 9. Logs en signalen

Gebruik logs voor controle, niet als primaire werkplek.

Let vooral op:

- foutmeldingen bij import;
- herhaalde authenticatiefouten;
- synchronisatiefouten of conflictsituaties.

Doel:

- snel vaststellen of een wijziging correct is verwerkt.

## 10. Kwaliteitsregels voor wijzigingen

Hanteer deze basisregels altijd:

1. Voer kleine, gerichte wijzigingen door.
2. Voeg waar mogelijk een duidelijke comment toe.
3. Controleer direct na aanpassen/importeren het resultaat.
4. Doe geen grote bulk-aanpassing zonder nacontrole.
5. Stem risicovolle wijzigingen af met betrokken teams.

## 11. Veelvoorkomende situaties

### Situatie A: Import geeft minder regels dan verwacht

Controleer:

- of regels geldig zijn;
- of regels al bestonden;
- of comments correct zijn geformatteerd.

### Situatie B: Een domein werkt na wijziging niet

Controleer:

- of de regel correct is opgeslagen;
- of er geen conflicterende wijziging is gedaan;
- of synchronisatie is afgerond.

### Situatie C: Veel foutieve logins

Controleer:

- of het juiste account wordt gebruikt;
- of er geen oud wachtwoord in scripts/tools staat;
- of er een tijdelijke lockout actief is.
