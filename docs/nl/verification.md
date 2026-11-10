# Verificatie op 6 november 2026

[English](../en/verification.md) · [Documentatie](README.md)

Omgeving: Ubuntu 26.04.1 LTS onder WSL 2, systemd als PID 1, Python 3.14.4,
Tinyproxy 1.11.3. De Python-dependencies staan in een aparte Linux-virtualenv
onder `/home/ruben/.venvs/proxypermit`.

| Controle | Resultaat |
| --- | --- |
| Unit-tests op Windows/Python 3.11 en Linux/Python 3.14 | 44 geslaagd |
| Gunicorn 26.2.0, echte HTTP-verzoeken, twee workers | Geslaagd |
| Granian 2.8.7, echte HTTP-verzoeken, twee workers | Geslaagd |
| Login, configuratie opslaan, CSRF-token, versieconflict en statische assets | Geslaagd |
| Nieuwe installatie met Granian via de installer | Geslaagd |
| Bestanden schrijven en Tinyproxy herstarten als serviceaccount | Geslaagd |
| Upgrade naar Gunicorn met behoud van configuratie en managerpoort | Geslaagd |
| Rollback naar de vorige release, met HTTP-controle | Geslaagd |
| Echt Tinyproxy-verkeer naar een lokale HTTP-server | Niet toegestane host: 403; toegestane host: 200 |
| Pylint-fouten, JavaScript-syntax en shellsyntax | Geslaagd |

De installatietest gebruikte `/opt/proxypermit-integration` en localhost:8098.
Testservices worden na de controle gestopt en uitgeschakeld; packages,
testreleases en backups blijven beschikbaar voor inspectie.

De integratietest vond een poortmigratiefout: een luisteradres dat alleen in
de systemd-unit stond, ging verloren bij een upgrade. De installer bewaart die
instelling nu in de environmentconfiguratie en controleert het ingestelde
HTTP-adres, inclusief Unix-sockets en lokale TLS, in plaats van alleen de
processtatus. De TLS-readinesscheck controleert bereikbaarheid; certificaatvertrouwen
wordt daardoor niet bewezen.

Herhalen van de geïsoleerde controles vanuit de projectroot:

```bash
~/.venvs/proxypermit/bin/python scripts/dev/test.py
~/.venvs/proxypermit/bin/python scripts/dev/check_server.py --backend gunicorn
~/.venvs/proxypermit/bin/python scripts/dev/check_server.py --backend granian
```

Nog te controleren: Ubuntu 24.04, upgrades van een echte legacy-HA-installatie,
Nginx/TLS-toegang vanaf clients, en failover/netwerkpartitionering tussen twee
afzonderlijke hosts. WSL op één machine bewijst deze eigenschappen niet.

## Na de herindeling

De 58 Python-tests en de JavaScript-contracttests voor locales en regelkaarten
worden met de nieuwe mappen uitgevoerd. Beide echte HTTP-servers zijn opnieuw
gecontroleerd. De workflow is lokaal met actionlint gevalideerd; de productie-
dependencies zijn met pip-audit gecontroleerd (geen bekende kwetsbaarheden).
De GitHub Actions-run zelf volgt pas nadat de wijzigingen naar GitHub gaan.

De screenshotsignalen zijn als rendercontract opgenomen: eigen inhoudskolom,
aparte actiekolom, vertaald Bewerken-label, behouden iconen en ongewijzigde
gebruikersinhoud. Dit vervangt geen visuele browsercontrole.

## Installatie en failover op 8 november 2026

- De huidige installer heeft de bestaande standalone-testinstallatie in WSL
  bijgewerkt naar een nieuwe release met Granian. De beheerpagina op
  `http://localhost:8098/` gaf ook vanaf Windows HTTP 200. De configuratie-API
  gaf zonder login HTTP 401. Via Tinyproxy op loopback gaf een lokaal testdoel
  HTTP 403 zonder toestemming en HTTP 200 na het toevoegen van een filterregel.
  De oorspronkelijke regels zijn teruggezet. Beide services draaien voor de
  demonstratie, met automatisch opstarten uitgeschakeld.
- Twee tijdelijke WSL-netwerk-namespaces draaiden elk ProxyPermit, Tinyproxy en
  Keepalived. Beide beheerpagina's gaven HTTP 200 en beide proxies leverden het
  lokale testantwoord. Na het stoppen van Tinyproxy op de eerste node nam de
  tweede node het virtuele IP over en werkte een nieuwe HTTP-aanvraag na 2,1
  seconden. De testnodes zijn na afloop opgeruimd.
- De failoverproef gebruikte handmatig ingerichte nodes op één WSL-host.
  Installatie van een nieuw HA-cluster, beleidssynchronisatie en uitval van een
  fysieke host zijn afzonderlijke, nog openstaande tests.

### Gegenereerde configuratie voor nieuwe HA-clusters

Beide configuraties uit de nieuwe installer zijn met Keepalived `--config-test`
gecontroleerd in afzonderlijke WSL-netwerk-namespaces. Beide Tinyproxy-processen
werkten met de gegenereerde configuratie en een lokale testregel. Nadat Tinyproxy
op node A werd gestopt, verplaatste het virtuele IP zich naar node B en gaf een
nieuwe aanvraag via datzelfde adres na 3,2 seconden HTTP 200. Deze proef gebruikte
de gegenereerde configuratie en healthcheck. De volledige `--apply`-route op
twee schone systemd-hosts moet nog afzonderlijk worden gevalideerd.

### Herhaalbaar HA-lab met lokale beheerinterfaces

Gebruik een Linux-testhost met Tinyproxy, Keepalived, iproute2 en de Python-
dependencies uit `requirements/granian.txt`. Start vanuit de projectmap:

```bash
sudo unshare --mount --propagation private .venv/bin/python scripts/dev/ha_lab.py --hold
```

Het lab gebruikt twee geïsoleerde netwerk-namespaces met een eigen bridge.
De beheerpagina's luisteren via loopback op poort 8101 en 8102. De gedeelde
proxy is via loopback bereikbaar op 8130. De startproef controleert inloggen
met 2FA, afwijzing van hergebruik en nieuw HTTP-verkeer na failover. De
inloggegevens staan in bestanden die alleen root kan lezen. Het lab print
de paden. De beheerinterface draait in demomodus zodat de nodes geen services
van de testhost herstarten. De twee Tinyproxy- en Keepalived-processen zijn
echte processen. Automatische beleidssynchronisatie maakt geen deel uit van
dit lab.
