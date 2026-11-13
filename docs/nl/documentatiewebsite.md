# De documentatiewebsite onderhouden

[English](../en/documentation-site.md) · [Documentatie](README.md)

De website wordt gebouwd met [Zensical](https://zensical.org/). De bron blijft
gewone Markdown in `docs/en/` en `docs/nl/` en is daardoor ook op GitHub leesbaar.
De gegenereerde map `site/` wordt niet ingecheckt of op proxynodes geïnstalleerd.

## Lokaal bekijken

Gebruik Python 3.11 of hoger in een aparte virtuele omgeving. Vanuit de projectmap:

```sh
python -m venv .venv/docs-tools
```

Op Linux/macOS:

```sh
.venv/docs-tools/bin/python -m pip install -r requirements/docs.txt
.venv/docs-tools/bin/zensical serve
```

In Windows PowerShell:

```powershell
.venv/docs-tools/Scripts/python.exe -m pip install -r requirements/docs.txt
.venv/docs-tools/Scripts/zensical.exe serve
```

Open het lokale adres dat Zensical toont. Stop met Ctrl+C. Vervang `serve` door
`build --clean --strict` om dezelfde gecontroleerde build als CI uit te voeren.

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
`docs/assets/en/`. Geef elke afbeelding een beschrijvende alternatieve tekst.
De website voegt een viewer toe waarmee je ook met het toetsenbord kunt vergroten
en inzoomen. Op GitHub blijven het gewone Markdown-afbeeldingen. Het logo opent
niet in de viewer. Onder de paginatekst staat een korte, vertaalde uitnodiging
om het project een ster te geven, zonder analytics of scripts van derden.

Maak de voorbeeldafbeeldingen opnieuw met `tests/browser/settings.cjs`, met
`PP_DOC_SCREENSHOTS=1`, Playwright op `NODE_PATH` en de ontwikkelomgeving gereed.
De test gebruikt afzonderlijke accounts en verzonnen loggegevens. Controleer de
afbeeldingen voor je ze commit; publiceer nooit inloggegevens of een 2FA-QR-code.

Draait de gebouwde site lokaal? Voer dan `tests/browser/docs.cjs` uit om de
viewer in beide talen, thema’s en schermformaten te controleren. Stel
`PP_DOCS_URL` in op het previewadres en zo nodig `PLAYWRIGHT_CHANNEL`.
