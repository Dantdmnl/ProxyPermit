# Vereisten en geteste platformen

[English](../en/requirements.md) · [Installatie](installatie.md) · [Verificatie](verification.md)

## Wat je nodig hebt

| Opstelling | Vereisten voor hosts en netwerk |
| --- | --- |
| Zelfstandig | Eén Debian- of Ubuntu-host met systemd, Bash, Python 3.10 of hoger en root- of sudo-toegang. Zorg dat de host de OS-pakketbronnen en de gebruikte Python-pakketbron kan bereiken. |
| Beheerde HA met twee nodes | Twee Linux-hosts met vaste IPv4-adressen, een vrij virtueel IPv4-adres (VIP) buiten DHCP, directe SSH- en beheertoegang tot beide nodes en een clientnetwerk dat het VIP kan bereiken. Gebruik de [HA-netwerkchecklist](hoge-beschikbaarheid.md). |
| Beschermde HA in ontwikkeling | Het beheerde HA-paar, gesynchroniseerde tijd, een onafhankelijke witness en een fencingpad dat ook werkt als een node onbereikbaar is. Gebruik de [HA-handleiding](hoge-beschikbaarheid.md) om het netwerk en gedeelde uitvalpunten te controleren. |

## Wat wordt geïnstalleerd

| Onderdeel | Afhankelijkheden en functie |
| --- | --- |
| OS-pakketten | De gewone installer gebruikt APT voor `python3-venv`, `tinyproxy`, `sudo`, `logrotate`, `ufw` en `acl`. Tinyproxy verwerkt proxyverkeer. Systemd beheert diensten en journals. Bij een nieuw HA-paar installeert de installer ook `keepalived`. |
| Beheerinterface | De installer maakt een virtuele Python-omgeving. Flask en Werkzeug leveren de webapplicatie. Gunicorn is de standaardserver, Granian is optioneel. Requests en python-dotenv verzorgen HTTP en configuratie. PyOTP en Segno ondersteunen optionele 2FA met een authenticator en pyrad ondersteunt optionele RADIUS. De precieze versies staan in de [requirements-bestanden](https://github.com/Dantdmnl/ProxyPermit/tree/main/requirements). |
| Beschermde HA-nodes | De voorbereiding installeert `pacemaker`, `pacemaker-cli-utils`, `corosync`, `corosync-qdevice`, `pcs` en `libnss3-tools`. De fencingagent en zijn afhankelijkheden verschillen per platform. Voor Proxmox levert de ingebouwde procedure een vastgezette agent op basis van ClusterLabs die de OS-bibliotheek voor fencing en PycURL gebruikt. |
| Witness | `corosync-qnetd` en `libnss3-tools` op een eigen Linux-host. Gebruik wederzijdse TLS tussen qdevice en QNetd. De witness heeft geen proxy, beheerinterface of fencingtoken nodig. |
| Ontwikkeling en documentatie | `requirements/dev.txt` bevat de ontwikkelafhankelijkheden. De browsertests gebruiken Node.js, Playwright en Chromium. Voor het [bouwen van de docs](documentatiewebsite.md) gebruik je Python 3.11 of hoger, `requirements/docs.txt`, Node.js 24 en het npm-lockbestand in `scripts/docs`. Deze hulpmiddelen draaien op ontwikkelsystemen en in CI. |

Voor sommige opties gelden extra versie-eisen. Het [herladen van filterregels
met behoud van verbindingen](beheer.md) is getest met Tinyproxy **1.11.1 en 1.11.3**.

## Netwerkverkeer voor beheerde HA

| Bron en bestemming | Verkeer |
| --- | --- |
| Toegestane clients → VIP | TCP 3128 voor het beheerde Tinyproxy-luisteradres. |
| A ↔ B | VRRP voor gewone HA. Sta voor het beschermde profiel UDP 5405 toe voor Corosync. |
| B → A | TCP 8443 voor de beheerde beleidssynchronisatie. |
| A en B → witness | TCP 5403 voor QNetd met wederzijdse TLS. |
| A en B → Proxmox-API | TCP 8006 voor de ingebouwde Proxmox-fencingagent, met certificaatcontrole en rechten voor alleen de andere VM. |
| Beheerders → elke node | Directe SSH en het ingestelde adres van de beheerinterface. Gebruik bij onderhoud het eigen adres van elke node. |

Controleer in het installatieplan de precieze netwerkinterfaces en adressen.
Zorg dat zowel de witness als het fencingpad beschikbaar blijft bij de
storingen die het cluster moet opvangen.

## Waarop is getest?

| Omgeving | Wat is gecontroleerd? |
| --- | --- |
| Linux- en Windows-CI | De Python-matrix gebruikt Linux met 3.10, 3.12 en 3.14 en Windows met 3.14. De root-tests draaien in een Ubuntu 24.04-container. Windows dient voor applicatie- en browsercontroles. De installer richt zich op Linux. |
| Ubuntu in WSL 2 en VirtualBox | Zelfstandige integratie op Ubuntu 26.04.1 in WSL 2. Installatie, koppeling, synchronisatie en gewone HA op Ubuntu 24.04 in VirtualBox. |
| Proxmox VE met Ubuntu 24.04-VM’s | Zelfstandige en beschermde HA-proeven, waaronder een onderbroken migratie, fencing van een VM, herstel van een netwerkscheiding en het terugdraaien van een release per node. De nodes en witness deelden in deze proeven één hypervisor. |
| KVM/libvirt met Ubuntu 24.04-VM’s | OS-beheerde beschermde HA met `fence_xvm` en `fence_virtd`. De proeven dekken fencing, VIP-eigendom en herstel na uitval van de fencer. De OS-beheerder richt dit cluster in en onderhoudt het. |
| VMware vSphere en fysieke servers met IPMIv2 | ProxyPermit beoordeelt ondersteunde fencinggegevens uit OS-beheerde clusters. Geautomatiseerde tests dekken onveilige configuraties. Live proeven met fencing en de volledige beheerprocedure staan nog open. |

In het [verificatierapport](verification.md) vind je datums, scenario’s en de
grenzen van elke proef. De [roadmap](vervolgwerk.md) houdt het resterende werk
bij. Gebruik bij een oudere installatie de documentatie uit die release.
