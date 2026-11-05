# Dashboard

Statische Website (GitHub Pages), alle 6 Stunden neu erzeugt von `.github/workflows/dashboard.yml`.

- `index.html`: die Seite; rechnet alle Filter (Schwelle, Ausfälle, Zeitraum, Tageszeit, Richtung) im Browser.
- `daten/kennzahlen.json`: wird beim Veröffentlichen aus der privaten Endstand-Tabelle erzeugt
  (`auswertung/aggregieren.py`) und enthält nur Zählungen, keine Einzelfahrten oder Rohdaten.

Die Seite ist für Suchmaschinen gesperrt (`noindex`) und nirgends verlinkt. Abschalten:
Settings → Pages → „Unpublish site“ bzw. Source auf „None“.

## Live-Seite (`live.html`, privat)

Zeigt die Lage der S1 je Richtung und für eine frei wählbare Strecke (Start und Ziel, Favoriten auf dem
Gerät gespeichert) die nächsten Abfahrten mit Anzeige der Schnittstelle, erwarteter Verspätung und
geschätzter Ankunft. Jede Angabe hat einen Info-Knopf mit ihrer Grundlage. Der Sammler schreibt den Stand alle 2 Minuten verschlüsselt in den Zweig `live`;
die Seite entschlüsselt ihn im Browser. Einmalig nötig: Repository-Secret `LIVE_PASSWORT` anlegen.
Ansichten und Intervall: `sammler/konfig.json`, Abschnitt `live`. Regeln: `docs/METHODIK.md`.
