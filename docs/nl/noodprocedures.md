# Herstel via de commandoregel

[English](../en/recovery.md) · [Beheerdershandleiding](beheer.md)

Open SSH-sessies via de **eigen adressen van de nodes**. Gebruik de
beheerconsole van het platform als SSH niet bereikbaar is. Houd een sessie
open terwijl je een nieuwe verbinding test. Noteer de geïnstalleerde versie
en bewaar de back-up met tijdstempel en de private herstelgegevens voordat
je iets wijzigt.

## Lees eerst de status uit

```bash
cat /opt/proxypermit/current/VERSION
systemctl status proxypermit.service tinyproxy.service --no-pager
sudo journalctl --namespace=proxypermit -u proxypermit.service --since '30 minutes ago' --no-pager
```

Controleer een beschermd cluster vanaf **beide** hosts:

```bash
sudo corosync-quorumtool -s
sudo crm_mon --one-shot --failcounts
ip -4 address show
```

Controleer quorum, fencing en welke node het VIP werkelijk voert. Test de
proxy ook vanaf een client. Kun je quorum of fencing niet bevestigen, laat
dan Pacemaker het VIP beheren en herstel de toegang via het OS of het
fencingplatform.

## Kies de herstelactie die bij de situatie past

| Situatie | Commando of procedure |
| --- | --- |
| Ingebouwde Proxmox-migratie onderbroken | Voer op beide nodes vanuit de geïnstalleerde applicatiemap `sudo .venv/bin/python -m scripts.deploy.cluster_native --phase status` uit. Vergelijk het operatie-ID en de planvingerafdruk. Deze opdracht leest alleen. Volg de [tabel met migratiefasen](hoge-beschikbaarheid.md#een-onderbroken-ha-activering-beoordelen). |
| Beschermde upgrade of rollback onderbroken | Voer vanuit de geïnstalleerde applicatiemap `sudo .venv/bin/python -m scripts.deploy.cluster_maintenance --status` uit om de lokale herstelgegevens te vergelijken met de actieve reservering. Herstel eerst de lokale beheerinterface en proxy. Gebruik daarna dezelfde opdracht met `--recover`. Dit controleert beide nodes voordat de reservering wordt vrijgegeven. |
| Een afgeronde beschermde applicatie-upgrade terugdraaien | Voer vanuit de uitgepakte release waarmee je bijwerkte `sudo bash install.sh --rollback BACKUP_NAME --cluster-maintenance --plan` uit. Controleer de gekozen release en voer dezelfde opdracht daarna met `--apply` uit. De andere node moet eerst het beschermde VIP bedienen. |
| Een al losgekoppelde node uit gebruik nemen | Controleer of de andere node quorum kan houden en het VIP kan bedienen. Voer op de vertrekkende host vanuit de uitgepakte release `sudo bash install.sh --retire-ha --detached-ha --plan` uit. Lees [welke diensten je eerst moet loskoppelen en maskeren](beheer.md#een-losgekoppelde-beschermde-node-verwijderen). |
| Beheerconsole verwijderen na geslaagde uitbedrijfname | Test eerst een nieuwe SSH-login. Gebruik `sudo bash install.sh --uninstall --detached-ha --plan` en na controle `--apply`. De lokale webconsole stopt. SSH en de vaste beheerregels in de firewall blijven behouden. |
| Fencingtoken verlopen | Gebruik het eigen adres van de getroffen host en volg [de herstelprocedure voor het token](hoge-beschikbaarheid.md#herstellen-na-het-verlopen-van-een-lokaal-proxmox-fencingtoken). |

De voorbeelden gaan uit van `/opt/proxypermit`. Ga bij een aangepaste
installatie naar de eigen `current`-map en geef `--install-dir` op bij opdrachten
die deze optie accepteren. Vervang `BACKUP_NAME` door de werkelijke back-upnaam
met tijdstempel. Gebruik het terugzetten van een volledige installatieback-up
voor zelfstandig beheerde nodes. Een beschermde applicatierollback en herstel
van het OS-cluster hebben elk een eigen procedure.

Meldt de status `reservation-mismatch` of een herstelbestand van een andere
node? Vergelijk beide hosts, beoordeel de reservering op OS-niveau en bewaar
het herstelbestand. Bij `peerServingProtectedVIP: false` kon de lokale
controle niet bevestigen dat de andere node het beschermde VIP bedient.
