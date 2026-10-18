# Methodik

Version 0.1 · Stand 18.10.2026

Grundsatz: Rohdaten werden unverändert gespeichert. Alle Definitionen werden erst bei der
Auswertung angewendet und können daher geändert werden, ohne Daten zu verlieren. Jede
Änderung an dieser Datei steht im Änderungsprotokoll unten.

## Grundbegriffe

| Nr. | Begriff | Definition |
| --- | --- | --- |
| D1 | Ist-Verspätung | Letzte Prognose vor der Soll-Abfahrt; Variante: Prognose x Minuten vorher |
| D2 | Messpunkt | Abfahrt; an Endstationen die Ankunft, sofern die Schnittstelle sie liefert (offen) |
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

## Änderungsprotokoll

| Datum | Version | Änderung |
| --- | --- | --- |
| 18.10.2026 | 0.1 | Erste Fassung: Grundbegriffe, Standardansicht, Fahrplan-Analysen |
