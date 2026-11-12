# Begin hier

[English](../en/README.md) · [ProxyPermit op GitHub](https://github.com/Dantdmnl/ProxyPermit)

Met ProxyPermit beheer je een Tinyproxy-server via een webinterface. Je bepaalt
welke apparaten de proxy mogen gebruiken en welke websites zij mogen bereiken.
Je kunt ook logs bekijken en twee servers gebruiken die elkaar bij uitval overnemen.

## Wat wil je doen?

| Jouw situatie | Begin bij |
| --- | --- |
| Ik heb een adres voor de beheerinterface en een account | [Eerste stappen](eerste-stappen.md) |
| Ik wil een website toevoegen, regels importeren of instellingen wijzigen | [De interface gebruiken](werkinstructie.md) |
| Ik wil ProxyPermit installeren, bijwerken of verwijderen | [Installeren](installatie.md) |
| Ik wil twee servers die elkaar kunnen overnemen | [Hoge beschikbaarheid](hoge-beschikbaarheid.md) |
| Iets werkt niet | [Problemen oplossen](problemen-oplossen.md) |
| Ik begrijp een technische term niet | [Begrippen uitgelegd](begrippen.md) |

## Voor de systeembeheerder

Gebruik na installatie de [beheerdershandleiding](beheer.md) om toegang te
controleren, verkeer te testen en back-ups voor te bereiden. De
[logginghandleiding](logging.md) legt uit wat wordt vastgelegd, hoelang dat wordt
bewaard en hoe je het terugvindt.

Het beheeradres en het proxyadres hebben verschillende functies. In de
beheerinterface pas je instellingen aan. Apparaten gebruiken het proxyadres om
websites te bereiken. Een beheeraccount aanmaken stelt de proxy op een apparaat
niet automatisch in.

## Voor ontwikkelaars

- Oefen veranderingen eerst in het [VM-testlab](vm-lab.md).
- [Architectuur](architecture.md) en [projectstructuur](layout.md) leggen de broncode uit.
- [Releases en CI](releases.md) beschrijft controles en het maken van releases.
- [Documentatiewebsite](documentatiewebsite.md) legt lokaal bekijken en publiceren uit.
- [Verificatie](verification.md) beschrijft wat daadwerkelijk is getest.
- [Vervolgwerk](vervolgwerk.md) bevat functies die nog niet beschikbaar zijn.

Deze pagina's volgen `main`, inclusief werk dat nog niet is uitgebracht.
Oude instructies staan in het
[archief](https://github.com/Dantdmnl/ProxyPermit/tree/main/docs/legacy).

[Gebruikers en rollen](rollen.md) legt uit welke rechten bij welk werk passen.
