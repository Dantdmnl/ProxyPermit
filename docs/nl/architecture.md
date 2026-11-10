# Architectuur en keuzes

[English](../en/architecture.md) · [Documentatie](README.md)

## Huidige opzet

ProxyPermit draait naast Tinyproxy. De Flask-beheerconsole verzorgt accounts,
filterregels, configuratieconcepten en logs. Tinyproxy verwerkt clientverkeer.
Beide services hebben een eigen luisteradres en toegangsbeleid. Publiceren
activeert een concept. Bij mislukte activatie zet ProxyPermit de vorige
bestanden terug.

Het installatieprogramma ondersteunt een zelfstandige node of twee nodes
met Keepalived. Bij een nieuw HA-cluster publiceer je het beleid op elke node.
Een upgrade kan de bestaande synchronisatie van filterregels behouden.

## Productrichting

ProxyPermit beheert de levenscyclus van een Tinyproxy-installatie. Een beheerder
moet kunnen zien welke configuratie draait, een wijziging voorbereiden, het
effect controleren, de wijziging uitrollen en terugkeren naar een eerdere versie.
Dat moet op één node werken. Een cluster gebruikt later dezelfde werkstroom.

| Werkruimte | Wat de beheerder er kan doen |
| --- | --- |
| Overzicht | Luisteradres, servicestatus, actieve configuratieversie en recente fouten zien. Een HTTP- of CONNECT-proef uitvoeren naar een vooraf ingesteld testdoel. |
| Toegang | Clientnetwerken, proxyauthenticatie, toegestane CONNECT-poorten en bestemmingsfilters afzonderlijk beheren. Allowlist, blocklist en uitgeschakelde filtering zijn expliciete keuzes. |
| Routering | Upstream-proxy's, directe uitzonderingen en geordende bestemmingsregels beheren. Reverse-proxymappings krijgen een eigen werkruimte zodra de geïnstalleerde build ze ondersteunt. |
| Netwerk en verkeer | Luister- en bronadressen, capaciteit, time-outs, HTTP-headers en logging instellen. |
| Wijzigingen | Concept en actieve versie vergelijken, valideren, publiceren en terugzetten. Het native configuratiebestand blijft beschikbaar voor geavanceerd beheer en migratie. |
| Nodes | De actieve versie en gezondheid per node zien, één versie gecontroleerd uitrollen en de status van de virtuele IP-overname volgen. |

Dit zijn productdoelen. De huidige configuratiepagina heeft al native
configuratiebewerking, filterbewerking en validatie. Veel opties vragen nog
kennis van de Tinyproxy-syntaxis. Welke opties beschikbaar zijn, hangt mede af
van de geïnstalleerde versie en build. Bij HTTPS via CONNECT gelden
host- en poortkeuzes voor de tunnel. HTTP-paden en headers binnen de tunnel
zijn voor Tinyproxy niet zichtbaar. Zie de [Tinyproxy-documentatie](https://tinyproxy.github.io/).

### Volgorde van oplevering

1. **Standalone activering.** Houd conceptbestanden apart van de bestanden die
   Tinyproxy leest. Publiceer configuratie en filter als één versie, controleer
   de actieve proxy met een lokale verkeersproef en herstel beide bestanden bij
   een mislukking. Houd rollback van de applicatierelease en rollback van
   proxybeleid apart, zodat een software-rollback nieuwere regels kan behouden.
2. **Toegang en routering.** Bied formulieren voor clientnetwerken,
   bestemmingsbeleid en upstreams. Bewaar bestaande opmerkingen en regelvolgorde,
   laat de native diff zien en scherm referenties met inloggegevens af in
   schermen, logs en exports die bedoeld zijn om te delen.
3. **Meerdere nodes.** Publiceer vanuit één beheerbron, met een versie en
   uitrolstatus per node. Valideer en activeer eerst op één node en ga pas verder
   na een geslaagde verkeersproef. Een node houdt zijn laatst werkende versie als
   de beheerbron wegvalt.
4. **Beheerde failover.** Configureer Keepalived vanuit ProxyPermit, toon de
   eigenaar van het virtuele IP en koppel overname aan proxygezondheid. Test
   procesuitval, hostuitval en netwerkpartitionering op afzonderlijke hosts.
   Beheerfailover en schrijfrechten vormen een afzonderlijke ontwerpbeslissing.

## Server en taal

Gunicorn blijft de standaard voor bestaande en nieuwe installaties. De oude
21.2-pin is vervangen door 26.2.0; compatibiliteit wordt met echte HTTP-verzoeken
gecontroleerd. De applicatie heeft nu één server-entrypoint: `python -m app.runtime.server`.

Granian is een optionele Rust HTTP-server met WSGI-ondersteuning. Hierdoor kan
de bestaande Flask-app behouden blijven. Kies deze met `--server granian` in
de installer of installeer `requirements/granian.txt` en zet `APP_SERVER=granian`.
De integratie gebruikt één blocking thread per worker om het gelijktijdigheidsmodel
van de huidige applicatie niet ongemerkt uit te breiden. Dit bewijst geen
prestatievoordeel; daarvoor is een representatieve benchmark nodig.

Een volledige Rust-rewrite is nu niet gekozen. De grootste verbeterpunten zitten
in transacties, configuratieverdeling, autorisatie en deployment. Een taalwissel
lost die ontwerpvragen niet op.

Granian gebruikt in deze integratie HTTP achter TLS-terminatie door Nginx.
Bestaande directe TLS-configuraties blijven via Gunicorn beschikbaar. Voor een
Granian Unix-socket geldt mode 0660: geef de Nginx-worker toegang via de juiste
groep. Wissel bestaande productiebackends pas na een stagingtest.

Bronnen: [Gunicorn](https://gunicorn.org/design/),
[Granian](https://github.com/emmett-framework/granian),
[Gunicorn-release](https://pypi.org/project/gunicorn/26.2.0/).

## High availability

Beschikbaarheid bestaat hier uit afzonderlijke verantwoordelijkheden:

| Laag | Keuze | Betekenis |
| --- | --- | --- |
| Procesherstel | systemd | Herstart een gestopte manager/proxy op dezelfde host. |
| VIP-failover | Keepalived / VRRP | Verplaatst het bereikbare proxyadres tussen nodes. |
| TCP-balancing, indien nodig | HAProxy | Alleen toevoegen wanneer meerdere actieve proxybackends nodig zijn. |
| VM-/hostherstel | Proxmox HA | Kan de VM na hostuitval elders starten; dit vervangt geen applicatie-healthcheck. |
| Complex clusterresourcebeheer | Pacemaker / Corosync | Pas inzetten als resources, quorum en fencing dat vereisen. |
| Configuratieverdeling | Lokale publicatie of bestaande synchronisatie | Nieuwe clusters publiceren per node. Bestaande synchronisatie omvat filterregels. |

Keepalived wordt behouden. De huidige zelfgeschreven bidirectionele sync is
geen consensusprotocol. VRRP alleen garandeert bij een netwerkpartitionering ook
geen exclusieve schrijver. Pacemaker wordt niet automatisch toegevoegd: een
betrouwbare configuratie vergt een expliciete keuze voor quorum en fencing.

De volgende ontwerpstap is één beheerbron met versiegebonden configuratiereleases
en gecontroleerde uitrol naar proxy-nodes. Bij onbereikbaar beheer blijven nodes
de laatst geactiveerde configuratie gebruiken. Wie wijzigingen mag publiceren,
hoe beheerfailover werkt en waar de bron wordt opgeslagen moeten eerst worden
uitgewerkt; dit is nog geen geïmplementeerde vervanging voor legacy sync.

Bronnen: [Keepalived](https://keepalived.org/),
[Pacemaker](https://clusterlabs.org/projects/pacemaker/),
[HAProxy](https://www.haproxy.org/),
[Proxmox HA](https://github.com/proxmox/pve-docs/blob/master/ha-manager.adoc).

## Verificatie

`python scripts/dev/test.py` gebruikt tijdelijke bestanden en aparte environmentwaarden.
`python scripts/dev/check_server.py --backend gunicorn` (of `granian`) start een echte
server op een tijdelijke loopbackpoort en controleert login, configuratie en assets.
Beide scripts laten productieconfiguratie ongemoeid.

WSL met systemd is geschikt voor package-, service- en rechtencontroles. VRRP,
VIP-overname en netwerkpartitioneringen moeten op twee afzonderlijke Linux-VM's
worden getest. Gebruik daarvoor bijvoorbeeld een geïsoleerd Proxmox-testnetwerk.
