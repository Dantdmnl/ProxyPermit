# De documentatiewebsite onderhouden

[English](../en/documentation-site.md) · [Documentatie](README.md)

De website wordt gebouwd met [Zensical](https://zensical.org/). De bron blijft
gewone Markdown in `docs/en/` en `docs/nl/` en is daardoor ook op GitHub leesbaar.
De gegenereerde map `site/` wordt niet ingecheckt of op proxynodes geïnstalleerd.

## Lokaal bekijken

Gebruik Python 3.11 of hoger in een aparte virtuele omgeving, plus Node.js 24 of hoger. Vanuit de projectmap:

```sh
python -m venv .venv/docs-tools
```

Op Linux/macOS:

```sh
.venv/docs-tools/bin/python -m pip install -r requirements/docs.txt
npm ci --prefix scripts/docs --ignore-scripts
.venv/docs-tools/bin/python scripts/docs/site.py serve
```

In Windows PowerShell:

```powershell
.venv/docs-tools/Scripts/python.exe -m pip install -r requirements/docs.txt
npm ci --prefix scripts/docs --ignore-scripts
.venv/docs-tools/Scripts/python.exe scripts/docs/site.py serve
```

Open het lokale previewadres dat het script toont. Stop met Ctrl+C. Vervang `serve` door
`build` om dezelfde gecontroleerde build als CI uit te voeren.

## GitHub Pages inschakelen

De workflow staat klaar in `.github/workflows/docs.yml`. Zo publiceer je de site:

1. Controleer de documentatiewijzigingen en push ze naar `main`.
2. Open in de repository **Settings → Pages → Build and deployment**.
3. Kies **GitHub Actions** als bron.
4. Geef openbare publicatie van de documentatie vrij: zet de repositoryvariabele
   `DOCS_PUBLISH_ENABLED` op `true` onder **Settings → Secrets and variables →
   Actions → Variables**. Een privébronrepository maakt een gewone Pages-site
   niet automatisch privé.
5. Open **Actions → Documentation → Run workflow** op `main`.
6. Open na een geslaagde uitvoering de gepubliceerde URL uit het resultaat.

Daarna bouwen en publiceren documentatiepushes naar `main` automatisch.
Pull requests bouwen en controleren alleen; ze kunnen niet publiceren. Alleen
de publicatiestap krijgt schrijfrechten voor Pages en het identiteitstoken.

Het ingestelde adres is `https://dantdmnl.github.io/ProxyPermit/`. Pas `site_url`
in `zensical.toml` aan als de repositorynaam of het publieke domein verandert.
Pages toont statische documentatie, geen ProxyPermit-beheerinterface of demoserver.
Inhoud op publieke Pages is openbaar: gebruik voorbeeldgegevens en opgeschoonde beelden.

## Schrijf voor degene die de taak uitvoert

- Vertel voor wie de pagina is, wat nodig is en wat het resultaat moet zijn.
- Gebruik de echte knopnamen. Leg nieuwe begrippen uit voordat je ze vaker gebruikt.
- Zet één handeling per genummerde stap en vertel hoe je het resultaat controleert.
- Leg herstarten, vervangen en verwijderen uit vóór de bevestigingsstap.
- Houd normale instructies apart van architectuur en implementatiedetails.
- Werk de bijbehorende NL-/EN-pagina, taalverwijzing en navigatie samen bij.
- Gebruik geen echte accounts, clientgegevens of geheimen in voorbeelden.

De site heeft één navigatieboom met een Engelse en Nederlandse sectie. Algemene
interfaceknoppen zijn Engels; de pagina's en navigatie binnen elke taalsectie
zijn vertaald. De taallink bovenaan een handleiding opent de bijbehorende vertaling.
Er zijn geen analytics of externe Google Fonts ingesteld. Historische documenten
blijven op GitHub en staan niet in de navigatie of zoekresultaten van de gebouwde site.

## Vóór het samenvoegen

Draai de strikte build, controleer de gewijzigde pagina's in beide thema's en op
een klein scherm, en test navigatie, zoeken en de taalverwijzingen. De
[officiële publicatiehandleiding van Zensical](https://zensical.org/docs/publish-your-site/)
beschrijft de koppeling met GitHub Pages.

## Schermafbeeldingen en bediening

Bewaar schermafbeeldingen zonder gevoelige gegevens in `docs/assets/nl/` en
`docs/assets/en/`. Gebruik samenhangende voorbeeldgegevens en leg het relevante
paneel of dialoogvenster vast. Vermijd lege schermen, onnodige bureaubladranden
en afbeeldingen van terminaltekst.

Plaats afbeeldingen bij de handeling die ze uitleggen. Gebruik een `figure` met
de klasse `docs-figure`, een kort bijschrift in `figcaption` en beschrijvende
alternatieve tekst. Voeg `docs-figure--dialog`, `docs-figure--signin` of
`docs-figure--mobile` toe voor smallere beelden. Zet `loading=lazy` en de
originele breedte/hoogte in pixels op de Markdown-afbeelding, zodat de pagina
ruimte reserveert tijdens het laden. Rek kleine screenshots niet uit.

De ingebouwde GLightbox van Zensical verzorgt de galerij en zoom. Lokale
JavaScript voegt vertaalde labels en focusbeheer toe; lokale CSS verzorgt de
opmaak. Zonder JavaScript opent de afbeeldingslink nog steeds het originele
bestand. Er zijn geen externe viewerscripts, analytics of afbeeldingsdiensten
nodig. De uitnodiging om een GitHub-ster te geven staat kort onder de paginatekst.

Deze aanpak volgt de [afbeeldingsrichtlijnen van Zensical](https://zensical.org/docs/authoring/images/)
en [Googles richtlijnen voor screenshots](https://developers.google.com/style/images).

Maak de voorbeeldafbeeldingen opnieuw met `tests/browser/settings.cjs`, met
`PP_DOC_SCREENSHOTS=1`, Playwright op `NODE_PATH` en de ontwikkelomgeving gereed.
De test gebruikt afzonderlijke accounts en verzonnen loggegevens. Controleer de
afbeeldingen voor je ze commit; publiceer nooit inloggegevens of een 2FA-QR-code.

Draait de gebouwde site lokaal? Voer dan `tests/browser/docs.cjs` uit om de
viewer in beide talen, thema’s en schermformaten te controleren. Stel
`PP_DOCS_URL` in op het previewadres en zo nodig `PLAYWRIGHT_CHANNEL`.

De privacyfunctie kopieert de viewerbestanden tijdens de build naar de gegenereerde
site. Een eerste build heeft netwerktoegang nodig; volgende builds kunnen de
downloadcache gebruiken. Bezoekers laden de viewer vanaf de documentatiehost.
De browserjob in CI test de galerij met externe verzoeken geblokkeerd, in beide
talen en thema’s en op mobiel-, tablet- en desktopbreedtes. Ook de afbeeldingslinks
zonder JavaScript worden gecontroleerd. Gebruik voor deze tests het previewadres
inclusief het `/ProxyPermit/`-voorvoegsel.

Codeblokken krijgen tijdens de build syntaxkleuring met Shiki en de VS Code-thema’s
Light+ en Dark+. De browser heeft daarvoor geen highlighter of CDN nodig. De
kopieerknop kopieert de originele tekst, zonder het taallabel. Het buildscript
voert eerst de strikte Zensical-build uit en kleurt daarna shell-, PowerShell- en
`.env`-voorbeelden. Gewone tekst blijft ongewijzigd. Gebruik dit script voor de
definitieve preview en CI. Bouw opnieuw nadat je de bronbestanden hebt aangepast.
