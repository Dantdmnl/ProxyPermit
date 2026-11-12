# Gebruikers en rollen

[English](../en/roles.md) · [Documentatie](README.md)

Kies een rol die past bij het werk van de gebruiker. Een rol geldt voor één node.
Accounts, wachtwoorden, 2FA en rollen worden niet naar de andere HA-node gekopieerd.

| Taak | Monitor | Proxybeheerder | Systeembeheerder |
| --- | --- | --- | --- |
| Status, filterregels en logs bekijken | Ja | Ja | Ja |
| Filterregels exporteren | Nee | Ja | Ja |
| Eigen wachtwoord en optionele 2FA beheren | Ja | Ja | Ja |
| Regels toevoegen, aanpassen, importeren, verwijderen of verplaatsen | Nee | Ja | Ja |
| Tinyproxy-configuratie bekijken en proxybeleid aanpassen en publiceren | Nee | Ja | Ja |
| Tinyproxy herstarten | Nee | Ja | Ja |
| Servicegebruiker/-groep, luisterbindingen of bestandspaden wijzigen | Nee | Nee | Ja |
| HA aanmaken, een node koppelen of uit bedrijf nemen, of de koppeling intrekken | Nee | Nee | Ja |
| Gebruikers toevoegen, verwijderen of een andere rol geven | Nee | Nee | Ja |
| Alle sessies afmelden | Nee | Nee | Ja |

Op een gekoppelde back-upnode blijft gedeeld beleid voor elke rol alleen-lezen.
Publiceer wijzigingen op A. Een monitor kan bewaarde logs lezen; daarin kunnen
bedrijfsgegevens en persoonsgegevens staan. Geef die rol dus alleen aan mensen
die deze gegevens mogen inzien. Het Tinyproxy-configuratiebestand kan proxywachtwoorden
bevatten en is daarom alleen beschikbaar voor proxy- en systeembeheerders.
Een proxybeheerder kan geen servicegebruiker, luisteradressen of bestandspaden wijzigen. Dit zijn rollen in de beheerinterface, geen Linux-gebruikersaccounts.

## Een account toevoegen

1. Log in als **Systeembeheerder** en open **Instellingen → Gebruikers en rollen**.
2. Kies **Account toevoegen**, vul een persoonlijke gebruikersnaam in en kies
   de gewenste rol. Standaard is dat **Monitor**.
3. Stel het eerste wachtwoord voor de nieuwe gebruiker in. Bevestig met je eigen
   huidige wachtwoord en, als je 2FA gebruikt, een nieuwe authenticatorcode.
4. Geef het beheeradres en de inloggegevens door via het afgesproken veilige kanaal.
   Vraag de gebruiker het eerste wachtwoord te wijzigen. Die kan zelf 2FA inschakelen.

## Een rol wijzigen

Kies **Rol wijzigen** bij het account, selecteer de gewenste rol en bevestig met
je eigen gegevens. Bestaande sessies van die gebruiker worden ongeldig. Het
wachtwoord en de ingestelde 2FA blijven behouden. De gebruiker moet opnieuw inloggen.

Via de interface kun je je eigen rol niet wijzigen of je eigen account verwijderen.
Ook de accounttools voorkomen dat de laatste geldige systeembeheerder wordt
verwijderd of een lagere rol krijgt. Bestaande accounts zonder rol behouden bij
een upgrade hun systeembeheerdersrechten. Loop ze na en beperk rechten waar nodig.

## Herstellen vanaf de host

Een bevoegde OS-beheerder kan accounts bekijken en rollen wijzigen:

```sh
cd /opt/proxypermit/current
sudo .venv/bin/python scripts/tools/accounts.py list
sudo .venv/bin/python scripts/tools/accounts.py role alice manager
```

Gebruik het echte installatiepad. Met `set USER --role ROLE` wijzig je ook het
wachtwoord. Met `role USER ROLE` wijzig je alleen de rol en maak je bestaande
sessies ongeldig. De rolwaarden zijn `admin`, `manager` en `monitor`. Nieuwe
CLI-accounts krijgen standaard de monitorrol. Het eerste installatieaccount
blijft een systeembeheerder.

Rollen gelden voor lokale accounts. Bestaande installaties met één login of
RADIUS behouden hun beheertoegang totdat ze naar lokale accounts zijn gemigreerd.
