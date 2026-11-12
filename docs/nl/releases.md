# Een release voorbereiden en publiceren

[English](../en/releases.md) · [Documentatie](README.md)

**Voor:** de projectbeheerder die ProxyPermit uitbrengt. Een server installeren
of bijwerken staat bij [Installeren](installatie.md). Bestanden voorbereiden,
code pushen, een release publiceren en een server bijwerken zijn aparte acties.

## 1. Controleer documentatie en upgradegedrag

Voordat je een versienummer kiest:

- Werk beide talen bij voor gewijzigde handelingen, rechten en installeropties.
- Controleer de [rollen](rollen.md): bestaande accounts zonder rol blijven
  systeembeheerder; nieuwe accounts krijgen standaard de monitorrol. Monitors
  mogen geen filterregels exporteren. Rollen, wachtwoorden en 2FA blijven lokaal.
- Controleer [anti-lockout](beheer.md#anti-lockoutprofiel) met de echte beheerbron
  en poorten op de host. Een NAT-bronadres is niet het eigen adres van de node.
- Controleer de zelfstandige installatie en gekoppelde HA-nodes. Vergelijk de
  beleidsrevisies en test echt clientverkeer; alleen een groene status is niet genoeg.
- Lees [Verificatie](verification.md) en [Vervolgwerk](vervolgwerk.md).
  Beschrijf een geplande functie niet als onderdeel van de release.
- Controleer de back-up- en herstelinstructies voordat beheerders gaan bijwerken.

De documentatiewebsite volgt `main`, inclusief werk dat nog niet is uitgebracht.
Voor een stabiele release bepalen de bijbehorende tag en meegeleverde documentatie
welke versie je gebruikt. De website is statische documentatie, geen beheerinterface.

## 2. Bereid het versienummer voor

`VERSION` bepaalt de versie. De versievelden voor Python en de browser worden
hieruit gegenereerd. Bekijk eerst de huidige stand:

```sh
python scripts/release.py show
python scripts/release.py check
```

Beschrijf de wijzigingen onder **Unreleased** in `CHANGELOG.md`. Kies een nieuwe,
nog niet uitgebrachte versie. Hieronder is **1.2.0 alleen een voorbeeld**:

```sh
python scripts/release.py prepare 1.2.0 --dry-run
python scripts/release.py prepare 1.2.0
```

Controleer de wijzigingen. De voorbereiding past `VERSION`, de gegenereerde
bestanden en de gedateerde changelogsectie aan. Bij ongeldige notities of een
ongeldig versienummer stopt de opdracht. Datums zijn UTC. De opdracht maakt geen
commit of tag en pusht, publiceert of installeert niets. Verplaats een bestaande
stabiele tag niet naar nieuwe code; geef gewijzigde code een nieuwe versie.

## 3. Controleer de kandidaat

Vanuit een virtuele Python-omgeving met `requirements/dev.txt` geïnstalleerd:

```sh
python scripts/check.py
python scripts/release.py archive
```

Gebruik Linux/WSL voor shellcontroles en Gunicorn. De Windows-controle gebruikt
Granian en vermeldt platformgebonden overgeslagen tests. Draai bij gewijzigde browserfuncties
`tests/browser/settings.cjs`, met Playwright beschikbaar voor Node. Controleer
NL/EN, beide thema's en kleine schermen.

Bouw de documentatie met de vastgelegde tools uit `requirements/docs.txt` en
`zensical build --clean --strict`. De
[handleiding voor de documentatiewebsite](documentatiewebsite.md#lokaal-bekijken)
legt de voorbereiding uit.

Het archief heet `dist/proxypermit-VERSION.tar.gz` en krijgt een SHA-256-checksum.
Het interne manifest bevat de hashes van de bestanden. Geheimen, lokale
omgevingen, runtimegegevens en de gegenereerde website horen niet in het archief.

## 4. Push, controleer CI en tag de geteste commit

Controleer de voorbereide release en maak een commit. Push via de normale
reviewprocedure van de repository. Wacht tot **ProxyPermit CI** voor precies die
commit slaagt. Geslaagde lokale tests bewijzen niet dat de GitHub-workflow is geslaagd.

Maak de releasetag op die geteste commit. Vervang `TESTED_COMMIT_SHA` hieronder
door de echte SHA en gebruik het gekozen versienummer:

```sh
git tag -a v1.2.0 TESTED_COMMIT_SHA -m "ProxyPermit v1.2.0"
git push origin v1.2.0
```

Een stabiele tag moet exact `vVERSION` heten en een gedateerde changelogsectie
hebben. De tag-build controleert beide voordat het releasearchief wordt gemaakt.

## 5. Publiceer de download en documentatie

Gebruik na een geslaagde tag-build het artefact **proxypermit-source-release**
als bouwresultaat. Publiceer het archief en de checksum bij de bijbehorende
GitHub Release, met releaseopmerkingen en aandachtspunten voor bijwerken.
CI maakt die GitHub Release niet automatisch aan. Actions-artefacten verlopen
na 14 dagen; het zijn tijdelijke bouwresultaten, geen blijvende releasedownloads.

Publicatie via Pages staat hier los van. De workflow **Documentation** controleert
pull requests en kan `main` publiceren zodra Pages op GitHub Actions is ingesteld.
Zie [GitHub Pages inschakelen](documentatiewebsite.md#github-pages-inschakelen).
Controleer daarna navigatie, zoeken, afbeeldingen en taalverwijzingen op het
gepubliceerde projectadres.

## Wat CI controleert

`.github/workflows/ci.yml` controleert workflowsyntaxis, Python-code en installertypes,
versiesynchronisatie, shellsyntaxis, installatieplannen, Linux-/Windows-tests,
JavaScript en vertalingen, Playwright-scenario's, echte HTTP met Gunicorn/Granian,
bekende kwetsbaarheden in dependencies en een strikte documentatiebuild. De
releasejob wacht op al deze controles, inclusief de documentatie.

Actions zijn vastgezet op commits en het token van de hoofdworkflow is alleen-lezen.
Het dekkingsrapport staat in **proxypermit-coverage**. Alleen de aparte
Pages-publicatiejob krijgt schrijfrechten voor Pages en het identiteitstoken.
Dependabot stelt updates van Actions en Python-dependencies voor.

## Oude workflowruns en broncodegeschiedenis

De vervallen `.github/workflows/pylint.yml` hoort bij het eerdere project.
Die historische uitvoeringen staan los van de huidige CI en releasebestanden.
Een oude run verwijderen haalt de Actions-vermelding en bijbehorende logs en
artefacten weg. Het herschrijft geen commits en trekt een eerder gedownloade
kopie niet in.

Ook een force push garandeert niet dat oude commits bij GitHub verdwijnen.
Directe SHA-links, andere verwijzingen, forks of gecachte weergaven kunnen blijven
bestaan. Beloof daarom niet dat Actions opruimen historische broncode wist.
GitHub licht dit toe in de
[uitleg over repositorygeschiedenis](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository).
Houd bewijs voor huidige releases apart van het opruimen van vervallen workflows.
