# Projectstructuur

[English](en/layout.md) · [Documentatie](README.md)

```text
app/
  core/              Bestandsvergrendeling, validatie, filter- en configuratielogica
  web/               Flask-manager, API-routes en webintegratie
  runtime/           Gunicorn/Granian-entrypoint
  i18n/catalogs/     Nederlandse en Engelse teksten
  templates/         Pagina's en gedeelde navigatie
  static/css/        Vormgeving
  static/js/         Browsercode; rules.js rendert regelkaarten
deploy/
  systemd/           Service- en timerbestanden
  nginx/             Reverse-proxyvoorbeelden
  logrotate/         Logrotatie
  gunicorn.conf.py   Gunicorn-configuratie
scripts/
  deploy/            Installer en infrastructuurhulpmiddelen
  dev/               Geïsoleerde tests, echte HTTP-checks en Windows-demo
  tools/             Kleine beheerhulpmiddelen
  check.py           Gedeelde lokale/CI-checks
  release.py         Versiebeheer en lokale releasevoorbereiding
requirements/        Productie-, Granian- en ontwikkeldependencies
tests/
  python/            Backend-, installer-, locale- en releasetests
  js/                Browseronafhankelijke locale- en rendercontracten
  browser/           Playwright-tests voor de webinterface
  fixtures/          Testgegevens
legacy/              Synchronisatie en herstelhulpmiddelen voor bestaande HA-clusters
docs/                Huidige documentatie; historische handleidingen in legacy/
```

De root bevat alleen documentatie/metadata, versie/changelog, de Makefile en de
twee startpunten `install.sh` en `wsgi.py`. Voer commando's uit vanuit deze root.
`bash install.sh` opent de interactieve installer. Gebruik `--plan` voor JSON.
Gebruik `sudo bash install.sh --plan` voor een plan van een bestaande installatie als
de configuratiemap alleen voor het serviceaccount/root toegankelijk is.

Gebruik [het huidige installatieprogramma](installatie.md) voor nieuwe
installaties, upgrades en herstel. Voor bestaande HA-clusters met
filtersynchronisatie bevat `legacy/` de synchronisatiecode en de
hulpmiddelen voor herstel. De bijbehorende units staan in `deploy/systemd/`.

## Ontwikkelen

Maak een virtuele omgeving met Python 3.11 of hoger en activeer die voordat
je de ontwikkelafhankelijkheden installeert.

```bash
python -m venv .venv
python -m pip install -r requirements/dev.txt
python scripts/check.py
```

Voor een rechtstreekse import van de webapp zijn `FLASK_SECRET_KEY`,
`WHITELIST_PATH`, `AUDIT_LOG_PATH` en `SESSION_STATE_FILE` nodig.
`TINYPROXY_CONFIG_PATH` kiest het configuratiebestand. Met
`DEPLOYMENT_MODE=standalone` staan peerstatus en synchronisatie-API's uit.
Zonder die instelling blijven bestaande installaties met twee node-adressen
in HA-modus werken.

Start de Windows-demo vanuit PowerShell. De demo gebruikt lokale bestanden en
start geen Tinyproxy-service.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\dev\run-demo-windows.ps1
```

ProxyPermit gebruikt bij het eerste bezoek de browsertaal, met Nederlands
als standaard. De keuze via **NL / EN** wordt één jaar in een cookie
onthouden. Ingevoerde beschrijvingen, configuratiebestanden en originele
logberichten behouden hun oorspronkelijke taal.
