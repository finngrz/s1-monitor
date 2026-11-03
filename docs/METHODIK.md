# Methodik

Version 0.7 · Stand 03.11.2026

Grundsatz: Rohdaten werden unverändert gespeichert. Alle Definitionen werden erst bei der
Auswertung angewendet und können daher geändert werden, ohne Daten zu verlieren. Jede
Änderung an dieser Datei steht im Änderungsprotokoll unten.

## Grundbegriffe

| Nr. | Begriff | Definition |
| --- | --- | --- |
| D1 | Ist-Verspätung | Letzter Stand der Schnittstelle einschließlich nachträglicher Korrekturen bis 10 Minuten (ab 27.10.2026) bzw. 30 Minuten (ab 02.11.2026) nach der Abfahrt. Daneben gespeichert: die letzte Prognose vor der Abfahrt (was Fahrgäste sahen) |
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
- **Startbahnhöfe** (Wedel, Poppenbüttel, Flughafen): Vor der Abfahrt steht dort praktisch immer „pünktlich“; Abweichungen werden erst nach der Abfahrt bekannt (Beispiel: S1 7:28 ab Poppenbüttel am 27.10., +5 min erst ab Hoheneichen gemeldet). Ab 27.10. fragt der Sammler deshalb auch die letzten 10 Minuten ab (ab 02.11.: 30 Minuten), um nachträglich korrigierte Abfahrtszeiten zu erfassen.
- **Zu frühe Abfahrten** sind in den Daten nicht sichtbar: Unter den ersten 55.000 Beobachtungen ist keine einzige negative Verspätung. Frühabfahrten lassen sich nur über eigene Stichproben belegen.
- **Geflügelte Züge** erscheinen an Stationen vor Ohlsdorf zweimal (Ziel Flughafen und Ziel Poppenbüttel); in der Auswertung zählen sie als ein Zug.
- **Speicherung:** nur Änderungen je Fahrt und Station, mit vollständigem Rohdatensatz; jede Abfrage im
  Protokoll. Rohdaten in einem privaten Repository, nie veröffentlicht.

## Endstand-Tabelle

Für jeden abgeschlossenen Tag entsteht eine Tabelle mit einer Zeile pro beobachteter Abfahrt (Station,
Linie, Richtung, Soll-Zeit): letzter Stand (D1), letzte Prognose vor der Abfahrt, Ausfall, Gründe aus
`REALTIME_REASON`, Zahl der Beobachtungen. Sie ist die Grundlage aller Statistiken und bleibt dauerhaft
im privaten Repository; die Rohdaten jedes Monats liegen als geprüftes Archiv (SHA-256) in den Releases.

**Befund 27.10.2026:** Bei 116 von 196 abgefahrenen S1-Abfahrten änderte sich die Verspätung nach der
Abfahrt noch, meist um +1 bis +2 Minuten (Mittel +81 Sekunden, maximal +4 Minuten). Die letzte Prognose
vor der Abfahrt unterschätzt die tatsächliche Verspätung also systematisch. Negative Verspätungen
(Frühabfahrten) liefert die Schnittstelle weiterhin nicht.

**Fahrt-Identität (ab 02.11.2026, Endstand-Version 2):** Die Kennung `serviceId` der Schnittstelle
ist nicht stabil: Derselbe Zug erscheint unter wechselnden Kennungen. Eine Abfahrt ist deshalb definiert
über Station, Linie, Soll-Zeit und Fahrtrichtung (West/Ost entlang der Linie). Geflügelte Züge zählen
damit automatisch einmal. Alle Endstand-Tabellen ab 26.10. wurden aus den unveränderten Rohdaten neu
berechnet; Version 1 enthielt Doppelzählungen.

**Abfahrten ohne Echtzeitangabe:** Für einen Teil der S1-Abfahrten liefert die Schnittstelle bis zur
Abfahrt und danach keinen Verspätungswert (27.10.: 11 %, 29.10.–01.11.: 36–45 %). Diese Abfahrten zählen
als „ohne Echtzeit“ und gehen weder als pünktlich noch als verspätet in die Quoten ein; ihr Anteil wird
mit ausgewiesen. Ursache offen (fehlende Echtzeit im Datenfeed oder nicht verkehrende Fahrten). Ab
02.11. zählt der Sammler je Abfrage, wie viele S1-Züge der nächsten 20 Minuten ohne Wert geliefert werden
(Tageswert in `ergebnisse/datenqualitaet.csv`, Spalte `s1_ohne_echtzeit`).

**Analyse 03.11.2026 (ersetzt die erste Einschätzung vom 02.11.):** Am Sammler liegt es nicht: Jede
Station wurde lückenlos alle 60 Sekunden abgefragt, Protokoll und Rohdaten stimmen zeilengenau überein,
und der Einbruch liegt mitten in einem Sammel-Lauf ohne Codeänderung. Entscheidend ist, *wann* die
Schnittstelle den ersten Verspätungswert eines Zuges liefert (S1 ohne Startbahnhöfe):

| Zeitraum | Wert ≥ 20 min vor Abfahrt | erst nach Abfahrt | nie |
| --- | --- | --- | --- |
| 26.10. abends, 27.10. bis 14 Uhr | ~90 % | ~1 % | 0–9 % |
| ab 27.10. ca. 15 Uhr bis 01.11. | 16–37 % | 12–19 % | 12–46 % |
| 02.11. vormittags | 43 % | 16 % | 13 % |
| ab 02.11. nachmittags | 45–56 % | 15–19 % | 7 % |

Bis 27.10. mittags lieferte die Schnittstelle für fast jeden Zug schon eine Stunde vorher einen Wert.
Ab 27.10. gegen 15 Uhr kommen Werte offenbar erst, wenn der Zug unterwegs ist, und für viele Züge gar
nicht. Ursache liegt bei der Datenlieferung an die Schnittstelle (von außen nicht feststellbar). Ab
02.11. wurde es wieder deutlich besser, schon vor unserer Änderung. Der längere Rückblick (30 statt
10 Minuten) bringt zusätzlich rund 5 Prozentpunkte. Am schwächsten bleiben die ersten Halte nach dem
Startbahnhof Poppenbüttel (Wellingsbüttel, Kornweg, Hoheneichen: 11–13 % ohne Wert).

**Regel ab 03.11.2026:** Stunden, in denen für mehr als 25 % der S1-Abfahrten (mindestens 30 Abfahrten)
kein Wert vorliegt, gehen in keine Statistik ein; das Dashboard weist sie aus. Die Rohdaten bleiben
vollständig erhalten, die Regel lässt sich also jederzeit ändern. Die Erhebung wird bewusst nicht
pausiert: Auch in schwachen Stunden liefert sie die vorhandenen Werte und belegt die Datenlage.
Der Gesundheits-Check schlägt Alarm (E-Mail), wenn an einem Tag mehr als 15 % ohne Wert sind oder
mindestens 3 Stunden ausgeschlossen werden.

**Offener Punkt:** Rund 13 % der S1-Soll-Zeiten aus der Schnittstelle finden sich nicht im
Open-Data-Fahrplan (z. B. zusätzliche Abfahrten Richtung Wedel um :05/:15). Das bestand schon vor dem
Rückblick und wird in Stufe 3 geklärt (abweichende Fahrplanversion, Zusatzfahrten oder Baufahrplan).

## Änderungsprotokoll

| Datum | Version | Änderung |
| --- | --- | --- |
| 03.11.2026 | 0.7 | Stunden mit > 25 % ohne Echtzeit ausgeschlossen; Alarm im Gesundheits-Check; Ursachenanalyse |
| 02.11.2026 | 0.6 | Rückblick 30 statt 10 Minuten (D1); Analyse fehlender Echtzeit |
| 02.11.2026 | 0.6 | Fahrt-Identität ohne `serviceId` (Endstand v2, neu berechnet); Abfahrten ohne Echtzeit getrennt ausgewiesen |
| 27.10.2026 | 0.5 | D1 neu: letzter Stand inkl. Korrekturen nach der Abfahrt; Endstand-Tabelle; Monatsarchive |
| 27.10.2026 | 0.4 | Hinweis: Frühabfahrten in den Daten nicht sichtbar |
| 26.10.2026 | 0.4 | Erste Echtzeitdaten: Ankünfte nicht verfügbar (D2 angepasst), Minuten-Auflösung, geflügelte Züge |
| 26.10.2026 | 0.3 | Schriftliche Zustimmung der Hochbahn zur statistischen Nutzung liegt vor (keine Methodikänderung) |
| 18.10.2026 | 0.3 | Echtzeit-Erhebung beschrieben (Sammler gebaut, noch ausgeschaltet) |
| 18.10.2026 | 0.2 | Fahrplan-Analysen präzisiert: Tagesverkehr, Betriebspausen, geflügelte Züge, Knoten-Haltestellen, Stichtage, Verschiebungs-Vorschau |
| 18.10.2026 | 0.1 | Erste Fassung: Grundbegriffe, Standardansicht, Fahrplan-Analysen |
