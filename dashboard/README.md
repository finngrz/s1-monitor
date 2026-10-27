# Dashboard

Statische Website (GitHub Pages), alle 6 Stunden neu erzeugt von `.github/workflows/dashboard.yml`.

- `index.html`: die Seite; rechnet alle Filter (Schwelle, Ausfälle, Zeitraum, Tageszeit, Richtung) im Browser.
- `daten/kennzahlen.json`: wird beim Veröffentlichen aus der privaten Endstand-Tabelle erzeugt
  (`auswertung/aggregieren.py`) und enthält nur Zählungen, keine Einzelfahrten oder Rohdaten.

Die Seite ist für Suchmaschinen gesperrt (`noindex`) und nirgends verlinkt. Abschalten:
Settings → Pages → „Unpublish site“ bzw. Source auf „None“.
