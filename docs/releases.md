# Versies en releases

[English](en/releases.md) · [Documentatie](README.md)

`VERSION` is de enige handmatig beheerde versiebron. De huidige versie blijft
1.0.0 totdat je bewust een release voorbereidt. `app/_version.py` en
`app/static/js/version.js` zijn gegenereerd; de interface en asset-queryversies
gebruiken dezelfde versie. Dit volgt dezelfde werkwijze als Website/V2.

```bash
python scripts/release.py show
python scripts/release.py sync
python scripts/release.py check
```

Verzamel wijzigingen onder `## [Unreleased]` in `CHANGELOG.md`. Bereid daarna
een nieuwere release voor; bij incompatibele wijzigingen is een major-bump logisch:

```bash
python scripts/release.py prepare 2.0.0 --dry-run
python scripts/release.py prepare 2.0.0
python scripts/check.py
python scripts/release.py archive
```

Voorbereiden controleert eerst versie en release notes en werkt daarna VERSION,
beide gegenereerde bestanden en de gedateerde changelogsectie bij. Zonder concrete
release notes stopt het commando voordat het bestanden verandert. Datums zijn UTC.
Een ontwikkelversie mag `MAJOR.MINOR.PATCH-dev.N` gebruiken.

De commando's maken geen commits, Git-tags of pushes en publiceren niets.
Controleer eerst de diff. Een eventuele release-tag moet exact `vVERSION` zijn;
CI controleert dit. Alle branches/PR's krijgen dezelfde controles; de broncode-
artifact wordt pas gebouwd nadat die slagen.
Een stabiele release-tag vereist ook een gedateerde sectie voor die versie in
de changelog; onvoorbereide tags worden geweigerd.

De archiefopdracht schrijft `dist/proxypermit-VERSION.tar.gz`, een SHA-256-checksum
en een intern bestandsmanifest. Alleen de distributiemappen worden meegenomen;
Git-data, lokale environments, sleutels en runtime-logs horen niet in het archief.
Automatisch publiceren of deployen maakt geen onderdeel uit van de workflow.

## CI

`.github/workflows/ci.yml` heeft afzonderlijke jobs voor:

- Python-foutcontrole, versiesynchronisatie, shellsyntax en installatieplannen;
- typecontrole van de installer met Windows- en Linux-platformdefinities;
- workflow-validatie met actionlint;
- unit-tests op Linux/Python 3.10, 3.12, 3.14 en Windows/Python 3.14;
- coverage als artifact en job summary, zonder een ongefundeerde dekkingsclaim;
- JavaScript-syntax, localecontracten en regelweergave;
- echte HTTP-tests met Gunicorn en Granian;
- bekende dependencykwetsbaarheden via pip-audit;
- een broncodearchief en tagcontrole na geslaagde checks.

Acties zijn op geverifieerde commit-ID's vastgezet, tokens zijn alleen-lezen,
verouderde runs worden geannuleerd, en dependencies worden gecachet.
Dependabot stelt updates van Actions en Python-dependencies voor.

Dezelfde checks lokaal, vanuit de projectroot (ontwikkelomgeving: Python 3.11+):

```bash
python -m pip install -r requirements/dev.txt
python scripts/check.py
python scripts/check.py --only frontend version
```

Gebruik Linux/WSL voor shellchecks en Gunicorn. Op Windows voert de volledige
check Granian uit en meldt hij dat shellchecks zijn overgeslagen.
