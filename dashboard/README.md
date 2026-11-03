# Dashboard

Statische Website (GitHub Pages), alle 6 Stunden neu erzeugt von `.github/workflows/dashboard.yml`.

- `index.html`: die Seite; rechnet alle Filter (Schwelle, Ausfälle, Zeitraum, Tageszeit, Richtung) im Browser.
- `daten/kennzahlen.json`: wird beim Veröffentlichen aus der privaten Endstand-Tabelle erzeugt
  (`auswertung/aggregieren.py`) und enthält nur Zählungen, keine Einzelfahrten oder Rohdaten.

Die Seite ist für Suchmaschinen gesperrt (`noindex`) und nirgends verlinkt. Abschalten:
Settings → Pages → „Unpublish site“ bzw. Source auf „None“.

## Live-Seite (`live.html`, privat)

Zeigt die Lage der S1 je Richtung und die nächsten Abfahrten ab Poppenbüttel und Ohlsdorf (Richtung
Innenstadt) sowie ab Stadthausbrücke (Richtung Poppenbüttel), jeweils mit Anzeige der Schnittstelle und
erwarteter Verspätung. Der Sammler schreibt den Stand alle 2 Minuten verschlüsselt in den Zweig `live`;
die Seite entschlüsselt ihn im Browser. Einmalig nötig: Repository-Secret `LIVE_PASSWORT` anlegen.
Ansichten und Intervall: `sammler/konfig.json`, Abschnitt `live`. Regeln: `docs/METHODIK.md`.
