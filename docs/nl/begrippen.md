# Begrippen uitgelegd

[English](../en/glossary.md) · [Documentatie](README.md)

| Begrip | Betekenis in ProxyPermit |
| --- | --- |
| Proxy / Tinyproxy | De dienst die verzoeken van apparaten doorstuurt naar websites. Tinyproxy verwerkt het verkeer; ProxyPermit beheert de instellingen. |
| Beheerinterface | De website waarop beheerders regels en instellingen aanpassen. Deze heeft een eigen adres en login. |
| Client | Een apparaat of programma dat verzoeken via de proxy verstuurt. |
| Clienttoegangsregel / ACL | Bepaalt welke bronapparaten de proxy mogen gebruiken. Dit staat los van bestemmingsfilters. |
| Bestemmingsfilter | Een regel die past op een website of domein dat via de proxy wordt opgevraagd. |
| Allowlist / blocklist | Passende bestemmingen toestaan en de rest blokkeren / passende bestemmingen blokkeren en de rest toestaan. |
| Concept / actieve configuratie | Opgeslagen werk dat nog moet worden gepubliceerd / de configuratie die Tinyproxy nu gebruikt. |
| Publiceren | De gecontroleerde configuratie toepassen. Publiceer gedeeld beleid op A, zodat een gekoppelde B het ontvangt. |
| Node | Eén server waarop ProxyPermit en Tinyproxy draaien. |
| HA / failover | Hoge beschikbaarheid / automatische overname door de andere server wanneer de gezondheidscontrole mislukt. |
| VIP | Virtueel IP: het gedeelde proxyadres dat op één HA-node tegelijk actief is. Apparaten blijven dat adres gebruiken bij overname. |
| Keepalived / VRRP | De dienst en het netwerkprotocol waarmee het VIP tussen de nodes verplaatst. |
| Beleid / revisie | De gedeelde regels en ondersteunde Tinyproxy-instellingen / een kenmerk waarmee je versies van dat beleid vergelijkt. |
| Synchronisatie | B haalt gepubliceerd beleid van A op en past het automatisch toe. Dit kopieert geen lokale accounts. |
| CIDR / prefix | Een netwerkadres met de grootte erbij, zoals `192.0.2.0/24`. Eén IPv4-host gebruikt `/32`. Vul je echte vertrouwde netwerk in, niet een voorbeeld. |
| UFW | De firewall op de host, gebruikt voor beheerde netwerkregels. |
| Anti-lockout | Een profiel dat de OS-beheerder vastzet om ingestelde beheer- en proxynetwerktoegang te behouden. Bestemmingsfilters blijven gelden. |
| 2FA / TOTP | Een extra inlogcode, meestal zes cijfers, uit een authenticatorapp. Per account optioneel. |
| Auditlog / bewaartermijn | Een registratie van wie wat heeft gewijzigd / de maximale bewaartijd, binnen de beschikbare opslagruimte. |
| SSH / SSH-tunnel | Een versleutelde beheerverbinding / het doorsturen van een lokale poort via die verbinding, zodat je browser de afgeschermde interface bereikt. |
| HTTPS / TLS-certificaat | Een versleutelde webverbinding / een certificaat waarmee je de identiteit van de server controleert. Dit vervangt inloggen niet. |
| CONNECT | Het proxyverzoek dat een tunnel maakt, vaak voor HTTPS. Tinyproxy kan de bestemming filteren maar de versleutelde paginapaden niet lezen. |
| root / sudo | Het OS-beheerdersaccount / een manier om een toegestane opdracht met beheerdersrechten uit te voeren. Controleer het plan voordat je wijzigingen toepast. |
| NAT / beheerbron | Adresvertaling / het bronadres dat de server ziet. Verschillende VM-verbindingen kunnen hetzelfde NAT-bronadres tonen. Dat is niet het eigen adres van de node. |
| Sessie | Je actieve aanmelding in de browser. Een rol- of wachtwoordwijziging kan die ongeldig maken, waarna je opnieuw moet inloggen. |

Bij [Hoge beschikbaarheid](hoge-beschikbaarheid.md) staat hoe twee servers samenwerken.
