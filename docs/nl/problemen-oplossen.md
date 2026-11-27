# Problemen oplossen

[English](../en/troubleshooting.md) · [Documentatie](README.md)

Begin bij het verschijnsel. Herhaal een wijziging niet meteen wanneer het antwoord
wegvalt: de server kan de wijziging al hebben opgeslagen. Herstel de verbinding en
controleer eerst het daadwerkelijke resultaat.
Voor herstel van de host of het cluster via SSH gebruik je de [herstelcommando’s](noodprocedures.md).

## De beheerinterface opent niet

1. Controleer het adres van je beheerder. De beheerinterface en proxypoort zijn
   verschillende diensten.
2. Controleer je VPN, verbinding met het beheernetwerk of SSH-tunnel.
3. Stond de pagina al open? Wacht tot de verbindingsmelding verdwijnt.
   Niet-opgeslagen tekst blijft in dat tabblad staan; herladen kan die verliezen.
4. Blijft de pagina onbereikbaar? Laat de hostbeheerder de webdienst controleren.
   Maak de interface niet publiek bereikbaar als tijdelijke oplossing.

## Inloggen is geblokkeerd of een 2FA-code wordt geweigerd

Wacht tot het getoonde tijdstip. Van taal wisselen reset de blokkade op de server
niet. Controleer gebruikersnaam en toetsenbordindeling voordat je opnieuw probeert.

Gebruik voor 2FA een actuele code en controleer of de tijd op het apparaat met je
authenticator klopt. Ben je de authenticator kwijt? Neem contact op met de
beheerder die herstel kan uitvoeren. Plaats geen wachtwoord, 2FA-geheim of QR-code
voor het instellen in een ticket.

## Een website wordt onverwacht geblokkeerd

Controleer deze onderdelen afzonderlijk:

1. **Clienttoegang:** mag het bronnetwerk van dit apparaat de proxy gebruiken?
2. **Filtermodus:** staat de lijst passende bestemmingen toe of blokkeert zij die?
3. **Bestemmingsregel:** staat het juiste domein erin, zo nodig inclusief subdomeinen?
4. **Activering:** heb je gepubliceerd of opgeslagen met herstart ingeschakeld?
5. **Clientinstelling:** gebruikt het apparaat het bedoelde proxyadres en de juiste poort?

HTTP 403 betekent dat een verzoek is geweigerd. Het vertelt op zichzelf niet
welke regel de oorzaak was. Open **Live logs**, filter op Tinyproxy en een kleine
periode, en vergelijk met een verzoek dat wel is toegestaan. De
[werkinstructie](werkinstructie.md) legt domeinregels en moduswissels uit.

## Een opgeslagen wijziging is niet actief

Open **Proxyconfiguratie → Jouw concept**. Een opgeslagen concept staat bewust los
van de actieve configuratie. Controleer het verschil en kies **Publiceren en activeren**
als de wijziging klaar is. Lees bij een mislukte activering de foutmelding; neem
niet aan dat het concept actief is geworden.

Heeft iemand anders het bestand gewijzigd? Laad de nieuwste versie en controleer
je wijziging opnieuw. Overschrijf een nieuwere versie niet alleen om een
conflictmelding weg te krijgen.

### Tinyproxy start niet na een onderbroken publicatie

Bij een beheerde installatie probeert ProxyPermit het laatst bevestigde
beleid terug te zetten voordat Tinyproxy start. Bij een normale publicatie
mag Tinyproxy herstarten zodra beide beleidsbestanden zijn geschreven.
Raakt de publicatie onderbroken, dan gebruikt het herstel de opgeslagen
kopieën van de configuratie en de filterregels.

Staat er **Beleidsherstel nodig** bij **Proxyconfiguratie**? Concepten blijven
bewaard. Je kunt weer wijzigingen aanbrengen nadat een systeembeheerder de
node heeft hersteld.
Op gekoppelde nodes meldt **Beleidssynchronisatie** ook wanneer de synchronisatie
op lokaal herstel wacht. Open voor diagnose het beheeradres van die node.
De uninstaller wacht ook op herstel, zodat de controle bij het opstarten
beschikbaar blijft.
Een beheerde upgrade of het terugzetten van een release op een beschermd
cluster stopt vóór de back-up van de actieve bestanden. Rond het herstel op
de getroffen node af en probeer de handeling opnieuw.
Ook het terugzetten van een installatieback-up wacht op beleidsherstel.
Komt tijdens het pauzeren van de console of synchronisatietimer een
onderbroken publicatie aan het licht, dan starten de eerder actieve diensten
weer en wordt de installatieback-up niet teruggezet.

Voer deze opdrachten uit via directe SSH naar de getroffen node.

```bash
sudo systemctl status tinyproxy.service --no-pager
sudo journalctl --namespace=proxypermit-traffic -u tinyproxy.service -b --no-pager
sudo df -h /etc/tinyproxy
sudo df -i /etc/tinyproxy
```

Meldt het herstel een fout? Bewaar dan de bestanden in
`/etc/tinyproxy/.proxypermit-config-backups` en lees de melding in het
logboek. Controleer bij een vol bestandssysteem de vrije ruimte en inodes.
Controleer bij een rechtenfout of de ProxyPermit-servicegebruiker toegang heeft
tot de beleidsmap en de back-upmap. Bekijk de bewaarde bestanden vóór een
herstart als een herstelbestand ongeldig is of een herstelkopie ontbreekt.
Volg op een beschermd cluster eerst
de [noodprocedures](noodprocedures.md) voordat je een dienst herstart. Test
na herstel een toegestane en een geblokkeerde bestemming voordat je opnieuw
publiceert.

## Ik kan op B geen regels aanpassen

Dat is normaal bij een gekoppelde back-upnode. Pas gedeeld beleid op A aan, ook wanneer
B het VIP heeft. Herstel A als deze niet beschikbaar is. Trek de koppeling niet in
om de beperking te omzeilen. Lees [Hoge beschikbaarheid](hoge-beschikbaarheid.md).

## De nodes tonen verschillende revisies of het VIP verhuist niet

Een revisie is een kenmerk waarmee je een versie van het beleid herkent. Geef B tijd voor de volgende ophaalronde.
Controleer daarna het laatste succesvolle contact en foutmeldingen op beide
nodes. Controleer of de wijziging echt op A is gepubliceerd en de nodes nog gekoppeld zijn.

Controleer bij overnameproblemen welke host het VIP daadwerkelijk heeft en test
verkeer via dat adres. Een verouderde of onbekende status bewijst niet dat een
node gezond is. Laat de hostbeheerder Keepalived en netwerk-/firewallinstellingen
controleren met de [beheerdershandleiding](beheer.md).

Open op een beschermd cluster een rechtstreekse beheersessie naar elke
bereikbare node. Controleer het cluster met `sudo corosync-quorumtool -s` en
`sudo crm_mon --one-shot --failcounts`. Meldt Corosync `WaitForAll` met
`Activity blocked` terwijl de witness verbonden is? Volg de
[noodprocedures](noodprocedures.md) om het clusterlidmaatschap te herstellen.
Draait Tinyproxy terwijl het VIP gestopt is? Noteer het tijdstip en de
foutreden van de VIP-monitor voor het onderzoek.

## Oudere logs ontbreken

Kies een kortere periode en controleer de gekozen bron. Standaard wordt audit-/beheerlogging
maximaal 30 dagen bewaard en Tinyproxy-/verkeerslogging 7 dagen. Schijflimieten
kunnen records eerder verwijderen. Exports, oude systeemlogs en eigen bestanden
hebben afzonderlijk beleid. Lees een melding over onvolledige resultaten voordat
je aanneemt dat er geen passende gebeurtenissen zijn. Zie [Logging](logging.md).

## Wat vermeld je in een hulpvraag?

Open als systeembeheerder **Instellingen → Diagnose** en download het
JSON-rapport. Het bevat een momentopname van de lokale proxyservice en de
laatste bevestigde clusterstatus. Het rapport bevat geen logs, proxyregels,
accountgegevens of sleutels. Onderzoek je een overname? Download dan een
rapport via het rechtstreekse beheeradres van elke node.

Vermeld de geïnstalleerde versie, node, het tijdstip met tijdzone, de pagina/actie,
de exacte fout en of echt proxyverkeer nog werkt. Beschrijf wat je verwachtte en
wat er gebeurde. Verwijder wachtwoorden, koppelcodes, 2FA-gegevens en onnodige
clientadressen of bestemmingsgegevens uit bijlagen.
