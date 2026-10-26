# Echtzeit-Sammler (Stufe 2)

**Status: fertig gebaut und getestet.** Die Hamburger Hochbahn AG hat am 26.10.2026 per E-Mail
schriftlich bestätigt, dass sie keine Einwände hat gegen: das Speichern der Echtzeitdaten für die
statistische Auswertung, das Veröffentlichen aggregierter Ergebnisse (ohne Rohdaten), das geplante
Abfragevolumen (höchstens 0,8 Abfragen pro Sekunde) und den Fortbestand veröffentlichter Auswertungen
auch nach einem etwaigen Widerruf des Zugangs.

## Was er tut

- Fragt alle 31 regulären S1-Stationen reihum ab (Abfahrten), etwa einmal pro Minute je Station.
  Ankunfts-Abfragen sind für unseren Zugang nicht freigegeben (HTTP 403, festgestellt am 26.10.2026);
  die Ankunft an Endstationen wird deshalb über die Abfahrt am vorletzten Halt abgeschätzt.
- Speichert alle Linien an diesen Stationen, auch Busse und U-Bahn am selben Knoten.
- Holt alle 5 Minuten die Störungsmeldungen zur S1.
- Speichert **nur Änderungen**: Eine neue Zeile entsteht, wenn eine Fahrt zum ersten Mal auftaucht
  oder sich ein Feld ändert (Verspätung, Ausfall, Gleis ...). Der vollständige Rohdatensatz jeder
  Änderung bleibt erhalten.
- Protokolliert **jede einzelne Abfrage** mit Zeitpunkt, Ergebnis und Fehlertext, auch fehlgeschlagene.
  Daraus lässt sich später lückenlos zeigen, wie vollständig die Daten sind.
- Hält sich an das Limit der Schnittstelle: höchstens 0,8 Abfragen pro Sekunde, gleichmäßig verteilt
  (die Hochbahn sperrt ab durchschnittlich 1 pro Sekunde). Bei Fehlern pausiert er mit wachsender
  Wartezeit (30 s bis 10 min), bei falschen Zugangsdaten hört er sofort auf.

## Wo die Daten liegen

Die Rohdaten der Schnittstelle dürfen laut Nutzungsbedingungen nicht veröffentlicht werden. Sie
landen deshalb in einem **privaten** Repository (`s1-monitor-daten`), nie in diesem öffentlichen.
Im öffentlichen Protokoll der GitHub-Läufe erscheinen nur Zähler. Veröffentlicht werden später
ausschließlich Auswertungen.

```
s1-monitor-daten/
├── abfahrten/JJJJ-MM-TT/…parquet    Änderungen je Fahrt und Station
├── meldungen/JJJJ-MM-TT/…parquet    Störungsmeldungen
├── protokoll/JJJJ-MM-TT/…parquet    jede einzelne Abfrage
└── stationen_geprueft.json          überprüfte Stations-IDs
```

Jede Nacht werden die vielen kleinen Dateien des Vortags zu einer `tag.parquet` zusammengefasst.

## Einmalig vorbereiten (kann schon jetzt passieren)

1. **Privates Daten-Repository anlegen:** Auf GitHub „New repository“, Name `s1-monitor-daten`,
   **Private**, Haken bei „Add a README file“ setzen (ein leeres Repository kann nicht gelesen werden).
2. **Zugangsschlüssel für das Daten-Repository erstellen:** GitHub → Settings → Developer settings →
   Personal access tokens → Fine-grained tokens → Generate new token.
   - Repository access: „Only select repositories“ → `s1-monitor-daten`
   - Permissions → Repository permissions → Contents: „Read and write“
   - Ablaufdatum: z. B. 1 Jahr (rechtzeitig erneuern, sonst stoppt die Sammlung)
3. **Im Repository `s1-monitor`** unter Settings → Secrets and variables → Actions eintragen:
   - Secret `DATEN_TOKEN` = der Schlüssel aus Schritt 2
   - Variable `DATEN_REPO` = `finngrz/s1-monitor-daten`

## Einschalten

1. Secrets `GTI_USER` und `GTI_PASS` = Zugangsdaten der Hochbahn.
2. Variable `SAMMLER_AKTIV` = `ja`.
3. Unter Actions → „Echtzeit sammeln“ → „Run workflow“ einen ersten Lauf von Hand starten.

Ausschalten: Variable `SAMMLER_AKTIV` löschen oder auf `nein` setzen.

## Nach dem ersten Tag prüfen

- [x] Alle 31 Stationen in `stationen_geprueft.json` richtig zugeordnet (alle über die globale ID).
- [ ] Bedeutet `timeOffset` die Soll-Zeit (Verspätung separat in `delay`)? Stichprobe gegen die hvv-App.
- [x] Ankunfts-Abfragen: für unseren Zugang nicht freigegeben (Methodik D2 angepasst).
- [ ] Werden Teilausfälle (Flügelung in Ohlsdorf) als Ausfall gemeldet (Methodik D4)?
- [ ] Wie viele Abfragen pro Tag, wie viele Fehler, wie groß die Tagesdateien?

## Hinweis zu GitHub Actions

GitHub hält kurze Zeitpläne nicht zuverlässig ein (statt alle 20 Minuten starteten Läufe nur alle 1–2,5
Stunden). Der Sammler läuft deshalb als Kette: Jeder Lauf sammelt bis zu 5,5 Stunden in Abschnitten von
28 Minuten, speichert nach jedem Abschnitt und startet am Ende selbst seinen Nachfolger. Ein stündlicher
Zeitplan fängt den Fall ab, dass die Kette reißt. Verbleibende Lücken werden im Abfrageprotokoll sichtbar
und in der Auswertung ausgewiesen. Geplante Läufe werden in öffentlichen Repositorys nach 60 Tagen ohne
Änderung abgeschaltet; der wöchentliche Fahrplan-Lauf aktualisiert den Bericht und verhindert das.
Falls GitHub einen dauerhaften Sammelbetrieb künftig nicht mehr zulässt, läuft derselbe Code auch auf
einem eigenen Rechner (z. B. Raspberry Pi).
