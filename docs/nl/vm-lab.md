# Installatie testen met VM’s

[English](../en/vm-lab.md) · [Documentatie](README.md)

Gebruik wegwerp-VM’s met Debian of Ubuntu, systemd en Python 3.10 of hoger.
De test gebruikt dezelfde `install.sh` als een gewone installatie. Maak een
snapshot vóór de eerste installatie. Gebruik de console van de hypervisor om
toegang te houden tijdens firewalltests.

| Wat wil je testen? | Opstelling |
| --- | --- |
| Installatie, aanmelden, proxyregels, upgrade, uninstall en herstel | Eén zelfstandige VM |
| Virtueel IP, VRRP, UFW en het uit bedrijf nemen van een node | Twee VM’s en een client in het toegestane labnetwerk |
| Snel proxyverkeer en failover oefenen | [Het bestaande WSL-lab](verification.md#herhaalbaar-ha-lab-met-lokale-beheerinterfaces) |

## VM’s voorbereiden

1. Maak in Proxmox een schone VM of kloon een schone cloud-init-template.
   Installeer Tinyproxy en Keepalived pas via ProxyPermit.
2. Zet beide HA-VM’s op hetzelfde afgeschermde virtuele netwerk of VLAN. Sta
   onderling unicast VRRP (IP-protocol 112) toe en zorg dat de VM’s de
   pakketbronnen kunnen bereiken. Reserveer een vrij virtueel IP.
3. Zet dezelfde ProxyPermit-broncode op de VM’s. Start vanuit de hoofdmap
   van die broncode. Markeer alleen wegwerp-VM’s als labhost:

```bash
hostname | sudo tee /etc/proxypermit-disposable-vm >/dev/null
sudo chmod 0600 /etc/proxypermit-disposable-vm
```

De testsoftware gebruikt `/opt/proxypermit-vm-lab` als releasepad. De echte
installer beheert de normale hostpaden en systemd-units, dus gebruik een schone
VM voor elke opstelling. [Proxmox-netwerkconfiguratie](https://pve.proxmox.com/pve-docs/pve-admin-guide.html#sysadmin_network_configuration)

### Lokaal op Windows met VirtualBox

Maak een schone Debian- of Ubuntu-VM met een NAT-adapter voor internettoegang.
Maak na de OS-installatie een snapshot en gebruik die VM voor de standalonetest.
Maak voor HA twee klonen van de schone toestand en geef ze unieke hostnamen.
Voeg aan beide klonen een tweede adapter toe van het type **Intern netwerk**.
Gebruik op beide de naam `proxypermit-ha` en stel op die adapters de statische
adressen uit het HA-voorbeeld hieronder in, zonder extra standaardgateway.
Geef een testclient op dat interne netwerk bijvoorbeeld `10.77.0.20/24`.

Voor beheer via SSH kun je op de NAT-adapter hostpoort 2221 voor A en 2222
voor B naar gastpoort 22 doorsturen. Bind beide doorstuurregels op Windows
aan `127.0.0.1` en gebruik die hostpoort met `ssh -p`.
[VirtualBox-netwerkmodi](https://docs.oracle.com/en/virtualization/virtualbox/7.2/user/networkingdetails.html)

## Eén zelfstandige node

```bash
sudo python3 scripts/dev/vm_lab.py install
sudo python3 scripts/dev/vm_lab.py check
sudo python3 scripts/dev/vm_lab.py lifecycle
```

`install` draait de installer, maakt het testaccount `lab-admin` aan en
controleert een toegestane en geblokkeerde bestemming via Tinyproxy. Het
wachtwoord staat in `/var/lib/proxypermit-vm-lab/password` en is alleen leesbaar
voor root. Voeg `--server granian` toe om Granian te testen.

`lifecycle` test achtereenvolgens upgrade, rollback, uninstall en herstel.
Na elke herstelstap controleert het script de login en het proxyverkeer.
Gebruik de bronmap van de gewenste versie om ook een versiewissel te testen.

Open de GUI vanaf je werkstation via SSH. Vervang gebruiker en adres.

```bash
ssh -N -L 8100:127.0.0.1:8080 beheerder@10.77.0.11
```

Open daarna `http://127.0.0.1:8100/`. Stel onder **Instellingen** desgewenst
2FA in nadat de automatische controles klaar zijn. Die gebruiken een
testaccount zonder 2FA.

## Twee HA-nodes

Dit voorbeeld gebruikt A op `10.77.0.11`, B op `10.77.0.12`, het virtuele IP
`10.77.0.10/24` en clientnetwerk `10.77.0.0/24`. Pas de waarden aan je
lab aan. Voer het commando op beide VM’s uit, met `node=b` op B en de
juiste interface per VM.

```bash
node=a
iface=ens18
sudo python3 scripts/dev/vm_lab.py install --mode ha \
  --ha-node "$node" --ha-interface "$iface" \
  --ha-node-a 10.77.0.11 --ha-node-b 10.77.0.12 \
  --ha-vip 10.77.0.10/24 --ha-client-cidr 10.77.0.0/24 --ha-vrid 51
```

De test controleert per node of Keepalived draait en of de drie UFW-regels
zijn geregistreerd. Voor een test met **actieve** UFW open je eerst de
hypervisorconsole. Sta zo nodig SSH toe vanaf het beheeradres voordat je UFW
inschakelt. Controleer daarna op beide VM’s `sudo ufw status verbose` en
`sudo ufw show added`.

Open het netwerkplan vanuit **Instellingen → Hoge beschikbaarheid →
HA-instellingen controleren** op een zelfstandige node. Zodra HA is
ingesteld, toont die sectie de synchronisatie en status van het virtuele IP.

### Automatische beleidssynchronisatie

Configureer op A de aparte TLS-listener uit
`deploy/nginx/proxypermit-ha.conf.example`. Gebruik de fysieke HA-adressen,
bind poort 8443 alleen aan de HA-interface en sta TCP 8443 in UFW alleen toe
vanaf B. Geef de TLS-server toegang tot zijn certificaat en privésleutel.
Zet op A `HA_POLICY_TLS_PIN` in `/etc/proxypermit/.env` op de
SHA-256-vingerafdruk van het certificaat in kleine letters zonder leestekens.
Herstart ProxyPermit op A.

Installeer op B `proxypermit-ha-policy.service` en
`proxypermit-ha-policy.timer` uit `deploy/systemd` in
`/etc/systemd/system`. Als de installer een aangepaste map gebruikte,
pas dan `WorkingDirectory` en `ExecStart` in de service aan. Voer
`sudo systemctl daemon-reload` en
`sudo systemctl enable --now proxypermit-ha-policy.timer` uit.

Open **Instellingen → Hoge beschikbaarheid → Beleid synchroniseren**. Kies
op A **Node toevoegen**. Plak de code op B en kies **Deelnemen aan cluster**.
Publiceer wijzigingen op A. B activeert de filterregels en gedeelde
Tinyproxy-instellingen automatisch. Als een node onbereikbaar is, blijft de
timer proberen en blijft B zijn laatst werkende configuratie gebruiken.
Trek de koppeling op beide nodes in voor je een peer of TLS-certificaat vervangt.

1. Start op beide nodes in een eigen terminal
   `python3 scripts/dev/vm_lab.py origin`. Die HTTP-testserver luistert alleen
   op loopback en retourneert de hostnaam van de node.
2. Test vanaf een client in het labnetwerk via het virtuele IP. Vervang
   `proxy-a` en `proxy-b` door de uitvoer van `hostname` op de twee VM’s.

   ```bash
   python3 scripts/dev/vm_lab.py check-vip 10.77.0.10 proxy-a
   ```

3. Stop Keepalived op A via `sudo systemctl stop keepalived.service` en
   herhaal de clienttest met `proxy-b`. Gebruik de hypervisorconsole om
   desgewenst een volledige uitval van A te testen.
4. Test het uit bedrijf nemen apart. Laat A aanstaan met Keepalived gestopt
   en laat B het verkeer afhandelen. Open de GUI van A via een SSH-tunnel
   naar zijn fysieke IP. Gebruik **Instellingen → Hoge beschikbaarheid
   (HA)** om A uit bedrijf te nemen.
5. Controleer op A dat Keepalived en Tinyproxy gestopt zijn en dat de
   geregistreerde HA-regels zijn verdwenen uit `sudo ufw show added`.
   Herhaal de clienttest met `proxy-b`. Herstel A via de back-upnaam in het
   servicelogboek of zet de VM-snapshots terug.

Gebruik afzonderlijke SSH-tunnels naar beide beheerconsoles, bijvoorbeeld
lokale poorten 8101 en 8102. Test clientverkeer via het virtuele IP op
poort 3128.
