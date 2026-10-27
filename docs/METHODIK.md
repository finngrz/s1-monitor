# Methodik

Version 0.4 · Stand 26.10.2026

Grundsatz: Rohdaten werden unverändert gespeichert. Alle Definitionen werden erst bei der
Auswertung angewendet und können daher geändert werden, ohne Daten zu verlieren. Jede
Änderung an dieser Datei steht im Änderungsprotokoll unten.

## Grundbegriffe

| Nr. | Begriff | Definition |
| --- | --- | --- |
| D1 | Ist-Verspätung | Letzte Prognose vor der Soll-Abfahrt; Variante: Prognose x Minuten vorher |
| D2 | Messpunkt | Abfahrt. Ankünfte liefert die Schnittstelle für unseren Zugang nicht; an Endstationen gilt die Abfahrt am vorletzten Halt plus Soll-Fahrzeit als Näherung |
| D3 | Ausfall | Fahrt als ausgefallen gekennzeichnet |
| D4 | Teilausfall | Ausfall ab einer bestimmten Station, z. B. Poppenbüttel-Teil ab Ohlsdorf |
| D5 | Fehlende Fahrt | Steht im Soll-Fahrplan, erscheint nie in den Echtzeitdaten; wird separat ausgewiesen |
| D6 | Datenlücke | Mehr als 5 Minuten ohne erfolgreiche Abfrage an einer Station; betroffene Fahrten gelten als „unbekannt“, nie als pünktlich |
| D7 | Anschluss erreicht | Ist-Ankunft S-Bahn + Mindest-Umsteigezeit ≤ Ist-Abfahrt des Busses |

## Standardansicht

Für alle weitergegebenen Zahlen gilt, sofern nicht anders angegeben:

- Pünktlich = weniger als 3:00 Minuten verspätet.
- Ausfälle zählen als unpünktlich.
- Datenlücken werden ausgeschlossen und mit ihrem Anteil ausgewiesen.
- Mindest-Umsteigezeit 3 Minuten.
- Daneben immer der Referenzwert nach S-Bahn-Definition: unter 6 Minuten, ohne Ausfälle.

## Fahrplan-Analysen (Stufe 1)

- **Quelle:** hvv-Soll-Fahrplan im GTFS-Format aus dem Transparenzportal Hamburg. Jede neue
  Version wird unverändert als GitHub Release archiviert (Prüfsumme SHA-256).
- **Fahrzeit:** Differenz der Soll-Zeiten in `stop_times.txt` zwischen zwei Halten derselben Fahrt.
- **Haltezeit:** Soll-Abfahrt minus Soll-Ankunft am selben Halt.
- **Geplante Umsteigezeit (F7):** Für jede planmäßige S1-Ankunft an einem Knoten die Zeit bis
  zur nächsten planmäßigen Abfahrt jeder Buslinie und Richtung an den Haltestellen dieses Knotens.
  Ein geplanter Anschluss gilt als nutzbar, wenn die Umsteigezeit mindestens 3 Minuten beträgt.
  Guter Anschluss: höchstens 6 Minuten; schlechter Anschluss: mehr als 12 Minuten.
- **Knoten-Haltestellen:** Bus-Haltestellen, deren Name mit „U S <Knoten>“ oder „S <Knoten>“ beginnt
  (offizielle hvv-Benennung). Hält eine Busfahrt an mehreren davon, zählt nur der erste Halt.
- **Geflügelte Züge:** Flughafen- und Poppenbüttel-Teil stehen als zwei Fahrten im Plan. Gleiche Richtung
  und gleiche Zeit am Knoten werden als ein Zug gezählt.
- **Zeitraum:** Anschluss-Analyse im Tagesverkehr 06–20 Uhr. Ankünfte in Betriebspausen des Anschlusses
  (über 60 Minuten ohne Abfahrt) zählen nicht.
- **Stichtage:** Je Tagestyp (Dienstag, Samstag, Sonntag) der künftige Tag mit dem häufigsten Fahrtenumfang
  von S1 und Knoten-Bussen. So fallen Feiertage, Ferien- und Baustellenfahrpläne heraus.
- **Zufällige Lage / Verschiebung:** Alle Abfahrten einer Buslinie werden rechnerisch um −10 bis +10 Minuten
  verschoben. Der Mittelwert über eine 20-Minuten-Spanne entspricht der Umsteigezeit ohne jede Abstimmung.
- **Schienenersatzverkehr** (z. B. „S1-SEV“) wird nicht als S-Bahn gezählt.

## Echtzeit-Erhebung (Stufe 2)

- **Quelle:** Geofox-Schnittstelle (`departureList` mit `useRealtime`, `getAnnouncements`), API-Version 63.
- **Umfang:** alle 31 regulären S1-Stationen, alle Linien am jeweiligen Knoten, 60 Minuten Vorausschau;
  Störungsmeldungen alle 5 Minuten.
- **Takt:** jede Station etwa einmal pro Minute, höchstens 0,8 Abfragen pro Sekunde, gleichmäßig verteilt.
- **Soll-Zeit:** Bezugszeit der Antwort plus `timeOffset` (Minuten). Ist-Verspätung aus `delay` (Sekunden).
- **Auflösung:** Die Schnittstelle liefert Verspätungen nur in ganzen Minuten. Schwellen unter 1 Minute sind daher nicht auswertbar.
- **Zu frühe Abfahrten** sind in den Daten nicht sichtbar: Unter den ersten 55.000 Beobachtungen ist keine einzige negative Verspätung. Frühabfahrten lassen sich nur über eigene Stichproben belegen.
- **Geflügelte Züge** erscheinen an Stationen vor Ohlsdorf zweimal (Ziel Flughafen und Ziel Poppenbüttel); in der Auswertung zählen sie als ein Zug.
- **Speicherung:** nur Änderungen je Fahrt und Station, mit vollständigem Rohdatensatz; jede Abfrage im
  Protokoll. Rohdaten in einem privaten Repository, nie veröffentlicht.

## Änderungsprotokoll

| Datum | Version | Änderung |
| --- | --- | --- |
| 27.10.2026 | 0.4 | Hinweis: Frühabfahrten in den Daten nicht sichtbar |
| 26.10.2026 | 0.4 | Erste Echtzeitdaten: Ankünfte nicht verfügbar (D2 angepasst), Minuten-Auflösung, geflügelte Züge |
| 26.10.2026 | 0.3 | Schriftliche Zustimmung der Hochbahn zur statistischen Nutzung liegt vor (keine Methodikänderung) |
| 18.10.2026 | 0.3 | Echtzeit-Erhebung beschrieben (Sammler gebaut, noch ausgeschaltet) |
| 18.10.2026 | 0.2 | Fahrplan-Analysen präzisiert: Tagesverkehr, Betriebspausen, geflügelte Züge, Knoten-Haltestellen, Stichtage, Verschiebungs-Vorschau |
| 18.10.2026 | 0.1 | Erste Fassung: Grundbegriffe, Standardansicht, Fahrplan-Analysen |
