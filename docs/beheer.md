# Beheerdershandleiding

[English](en/administration.md) · [Documentatie](README.md)

Gebruik deze handleiding voor een proefinstallatie en de overdracht naar dagelijks
beheer. ProxyPermit beheert Tinyproxy. Het beheeradres en het proxyadres hebben
elk hun eigen toegangsregels en netwerkfunctie.

## Kies de opstelling

| Opstelling | Beheer | Clientverkeer |
| --- | --- | --- |
| Zelfstandige installatie | Eén beheerconsole en één configuratie | Eén Tinyproxy-adres |
| Nieuwe HA-installatie | Een console en lokaal beleid per node | Eén virtueel IPv4-adres op twee nodes via Keepalived |
| Bestaand cluster met synchronisatie | Bestaande node-instellingen en afzonderlijke whitelistsync | Bestaand virtueel adres |

Begin met een afgeschermde Debian- of Ubuntu-testhost met systemd. Reserveer bij
HA twee vaste node-IP’s en één vrij virtueel IP. Gebruik de
[installatiewizard en voorbeelden](installatie.md#nieuwe-installatie-op-debian-of-ubuntu).
Bewaar de gekozen release, de poorten, het clientnetwerk, het beheeradres en de
naam van de back-up bij het wijzigingsverzoek.

## Controleer vóór ingebruikname

1. Bekijk het installatieplan. Voer de installatie uit als het plan klopt. Open
   de beheerconsole via een SSH-tunnel of via HTTPS achter de reverse proxy.
   Beperk toegang tot het beheernetwerk. Gebruik bij HTTPS een Secure-sessiecookie
   en vertrouw forwarded headers uitsluitend achter je eigen reverse proxy.
   [OWASP-richtlijnen voor sessiebeheer](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)
2. Maak een persoonlijk account aan voor elke beheerder. Lokale accounts hebben
   allemaal beheerdersrechten. Optionele 2FA stel je in onder **Instellingen**.
   Controleer het inloggen en leg vast wie bij verlies van de authenticator via de
   server toegang kan herstellen.
3. Beperk de clienttoegang in Tinyproxy tot de bedoelde netwerken. Controleer
   vanaf een toegestane client zowel een toegestane als een geblokkeerde
   bestemming. Controleer ook dat een client buiten het toegestane netwerk
   geen verkeer kan doorsturen.
4. Maak een kleine wijziging via de beheerconsole. Controleer na publicatie
   opnieuw het verkeer. Zoek de beheerder en node op in de auditlog onder
   **Live logs**. Bij nieuwe HA-nodes herhaal je dit op beide nodes.

Gebruik voor de verkeersproef twee interne testbestemmingen waarvan je de
normale HTTP-respons kent. Vervang de voorbeeldwaarden:

```bash
proxy=http://192.0.2.10:3128
curl --noproxy '' --proxy "$proxy" --max-time 10 -i http://toegestaan.example/
curl --noproxy '' --proxy "$proxy" --max-time 10 -i http://geblokkeerd.example/
```

Bij allowlist-filtering verwacht je het normale antwoord van de eerste
testbestemming en HTTP 403 voor de tweede. Test ook HTTPS via CONNECT als je
clients dat gebruiken. Een actieve systemd-service of HTTP 200 van de
beheerconsole is op zichzelf onvoldoende om clientverkeer te verifiëren.

## Dagelijks wijzigen en storingen onderzoeken

Gebruik **Proxyconfiguratie → Controleer concept → Concept opslaan → Publiceren
en activeren**. Noteer welke testbestemmingen na de wijziging moeten werken.
Publiceren kan verbindingen onderbreken. Bij een nieuwe HA-opstelling pas je
hetzelfde beleid op beide nodes toe. Begin met de node die het virtuele IP
momenteel niet bezit. Controleer die node vóór je de actieve node wijzigt.
Nieuwe clusters synchroniseren het beleid voorlopig niet automatisch.

```bash
sudo systemctl status proxypermit.service tinyproxy.service --no-pager
sudo journalctl -u proxypermit.service -u tinyproxy.service -n 100 --no-pager
# Alleen op HA-nodes
sudo systemctl status keepalived.service --no-pager
ip -br -4 address
sudo journalctl -u keepalived.service -n 100 --no-pager
```

| Symptoom | Controle |
| --- | --- |
| Tijdelijke blokkade bij het inloggen | Wacht tot de blokkade is verlopen. Een taalwissel of een nieuw tabblad verandert de wachttijd op de server niet. |
| Beheer opent niet | Controleer de manager-unit, de lokale luisterpoort en de SSH-tunnel of TLS-reverse-proxy. |
| Proxy geeft onverwacht 403 | Controleer de client-ACL, de filtermodus, de doelregel en of het concept is geactiveerd. |
| Failover werkt niet | Controleer welke node het virtuele IP heeft, bekijk de Keepalived-logs en controleer het VRRP-verkeer. Test vervolgens vanaf de client via het virtuele IP. |
| Nodes hebben verschillend beleid | Vergelijk de geactiveerde configuratie en filters per node. Controleer bij legacy-sync ook de TLS-configuratie en sync-logs. |

## Back-up, upgrade en herstel

Neem de installatie-instellingen, Tinyproxy-configuratie, filterregels,
accountbestand, sleutels en relevante servicestatus op in je back-up. Controleer
de ingestelde paden in `/etc/proxypermit/.env`. Bij HA horen ook de
Keepalived-configuratie en de gegevens per node erbij. Beperk toegang tot deze
bestanden, want ze kunnen wachtwoordhashes, 2FA-sleutels en sync-secrets bevatten.
Bewaar een kopie buiten de node en oefen herstel op de testomgeving.

Bekijk vóór een upgrade het plan en leg het huidige beleid vast. De installer
toont na uitvoering de naam van het herstelmanifest. Bekijk bij rollback eerst
welke bestanden worden teruggezet. Een herstelmanifest kan ook de
Tinyproxy-configuratie en filterregels van dat moment bevatten. Bewaar dus
latere beleidswijzigingen voordat je een oude release terugzet. Herhaal na
herstel het inloggen- en verkeersproeven.

## Grenzen van de huidige versie

- Er is één lokaal beheerdersniveau. Een alleen-lezen rol en centrale
  bedrijfsidentiteit zijn nog niet beschikbaar.
- Auditlogs staan lokaal. Leg voor bedrijfsgebruik vast hoe je ze centraal
  verzamelt en hoe lang je ze bewaart.
- De HA-proef bevestigt VIP-failover in het WSL-lab. Valideer de volledige
  installatie, hostuitval, netwerkpartitionering en herstel op de beoogde
  afzonderlijke hosts. [Uitgevoerde tests](verification.md)
