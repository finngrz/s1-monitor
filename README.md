# S1-Monitor Hamburg

Ein privates, nicht-kommerzielles Projekt, das die Pünktlichkeit der Hamburger S1 und die
Abstimmung der Busanschlüsse an ihren Knotenpunkten (Ohlsdorf, Barmbek, Berliner Tor)
transparent und nachvollziehbar auswertet.

**Status:** Stufe 1 (Fahrplan-Analysen) läuft. Stufe 2 (Echtzeit-Sammler) ist fertig gebaut und
getestet, aber ausgeschaltet bis zur schriftlichen Zustimmung der Hochbahn ([sammler/README.md](sammler/README.md)).
**Aktuelle Ergebnisse:** [analysen/ERGEBNISSE_STUFE1.md](analysen/ERGEBNISSE_STUFE1.md)
Die Echtzeit-Erhebung (Stufe 2) beginnt erst nach schriftlicher Zustimmung der Hochbahn
zur Nutzung der Geofox-Schnittstelle.

## Was das Projekt beantworten soll

| Nr. | Frage | Stufe |
| --- | --- | --- |
| F1 | Warum dauert Stadthausbrücke → Poppenbüttel laut Plan mal 30, mal 35 Minuten? | 1 |
| F2 | Wie viel Puffer steckt im Fahrplan, und wo? | 1 + 3 |
| F3–F6 | Pünktlichkeit, Pendelzeiten, Verspätungs-Hotspots, Flügelung in Ohlsdorf | 3 |
| F7 | Wie gut sind die Busse an Ohlsdorf, Barmbek und Berliner Tor auf die S1 abgestimmt? | 1 + 3 |
| F8–F12 | Optimierungspotenzial, Prognosegüte, Einflussfaktoren, Vergleiche, Extremereignisse | 4–5 |

Alle Definitionen stehen in [docs/METHODIK.md](docs/METHODIK.md).

## So funktioniert es

```
Datenquelle ──► Sammeln ──► Rohdaten (unverändert) ──► Aufbereiten ──► Auswerten ──► Dashboard
```

1. **Sammeln:** GitHub Actions lädt die Daten automatisch herunter.
2. **Rohdaten:** werden unverändert archiviert (Fahrplan-Versionen als GitHub Release).
3. **Aufbereiten:** DuckDB (SQL) erzeugt kleine, übersichtliche Tabellen in `ergebnisse/`.
4. **Auswerten:** Analysen in `analysen/`.
5. **Dashboard:** folgt in Stufe 5.

Jede Zahl lässt sich jederzeit aus den Rohdaten neu berechnen.

## Aufbau

```
.github/workflows/   automatische Läufe
fahrplan/            Download und Archiv des Soll-Fahrplans
aufbereitung/        Aufbereitung mit DuckDB
analysen/            Auswertungen zu den Fragen F1–F12
ergebnisse/          aufbereitete Tabellen (klein, versioniert)
sammler/             Echtzeit-Sammler (ab Stufe 2)
dashboard/           Website (ab Stufe 5)
tests/               automatische Prüfungen
docs/METHODIK.md     Definitionen und Änderungsprotokoll
```

## Datenquellen und Lizenzen

- **Soll-Fahrplan:** Hamburger Verkehrsverbund GmbH (hvv), veröffentlicht im
  Transparenzportal Hamburg, Lizenz: [Datenlizenz Deutschland – Namensnennung – Version 2.0](https://www.govdata.de/dl-de/by-2-0).
- **Echtzeitdaten (ab Stufe 2):** Geofox-Schnittstelle der Hamburger Hochbahn AG, nur mit
  schriftlicher Zustimmung; Rohdaten der Schnittstelle werden nicht veröffentlicht.

Alle Angaben ohne Gewähr. Dieses Projekt steht in keiner Verbindung zu hvv, Hochbahn oder
S-Bahn Hamburg.
