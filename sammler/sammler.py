"""Echtzeit-Sammler: fragt reihum alle S1-Stationen ab und speichert jede Änderung.

Ablauf eines Laufs (z. B. 24 Minuten auf GitHub Actions):
1. Stations-IDs prüfen (nur beim allerersten Mal, danach aus stationen_geprueft.json).
2. Letzten bekannten Stand laden, damit nur echte Änderungen gespeichert werden.
3. Bis zum Laufende: Stationen gleichmäßig verteilt abfragen (höchstens 0,8 Abfragen pro Sekunde,
   die Hochbahn sperrt ab durchschnittlich 1 pro Sekunde), alle 5 Minuten Störungsmeldungen holen.
4. Alle 5 Minuten und am Ende: neue Daten als Teil-Dateien schreiben.

Bei Fehlern pausiert der Sammler mit wachsender Wartezeit; bei falschen Zugangsdaten bricht er ab.
Im (öffentlichen) Protokoll des Laufs erscheinen nur Zähler, nie die Daten selbst.

Aufruf:
  GTI_USER=... GTI_PASS=... python sammler/sammler.py --daten <Ordner> --dauer-min 24
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HIER = Path(__file__).resolve().parent
sys.path.insert(0, str(HIER))

from gti import GTI, GTIFehler  # noqa: E402
from speicher import BERLIN, Speicher, abfahrt_zu_zeile, gti_zeit, meldung_zu_zeile  # noqa: E402

SCHREIB_INTERVALL_S = 300


def jetzt() -> datetime:
    return datetime.now(timezone.utc)


def pruefe_stationen(gti: GTI, cfg: dict, datei: Path, speicher: Speicher) -> list[dict]:
    """Ordnet jeder Station die GTI-Stations-ID zu (über checkName) und merkt sich das Ergebnis."""
    bekannt = json.loads(datei.read_text(encoding="utf-8")) if datei.exists() else {}
    ergebnis = []
    for st in cfg["stationen"]:
        if st["name"] in bekannt:
            ergebnis.append({**st, **bekannt[st["name"]]})
            continue
        t0 = jetzt()
        try:
            antw = gti.anfrage("checkName", {"theName": {"name": st["name"], "type": "STATION"}, "maxList": 10,
                                             "coordinateType": "EPSG_4326"})
        except GTIFehler as f:
            speicher.protokolliere(zeit_utc=t0, methode="checkName", station=st["name"], art=None,
                                   http_status=f.http_status, return_code=f.return_code, anzahl=None,
                                   dauer_ms=None, fehler=str(f))
            if f.dauerhaft:
                raise
            continue
        treffer = antw.daten.get("results") or []
        speicher.protokolliere(zeit_utc=t0, methode="checkName", station=st["name"], art=None,
                               http_status=antw.http_status, return_code="OK", anzahl=len(treffer),
                               dauer_ms=antw.dauer_ms, fehler=None)
        stationen = [t for t in treffer if t.get("type") == "STATION"]
        wahl, guete = None, None
        for t in stationen:  # 1. beste Wahl: gleiche globale ID wie im Fahrplan
            if t.get("globalId") == st["ifopt"]:
                wahl, guete = t, "globale ID"
                break
        if wahl is None:
            for t in stationen:  # 2. gleiche Nummer wie die Fahrplan-ID
                if str(t.get("id", "")).split(":")[-1] == st["ifopt"].split(":")[-1]:
                    wahl, guete = t, "ID-Nummer"
                    break
        if wahl is None:
            for t in stationen:  # 3. gleicher Name
                if (t.get("name") or "").lower() == st["name"].lower():
                    wahl, guete = t, "Name"
                    break
        if wahl is None:
            print(f"WARNUNG: Keine eindeutige Station für '{st['name']}' gefunden, wird übersprungen.")
            continue
        eintrag = {"gti": {k: wahl.get(k) for k in ("name", "city", "id", "globalId", "type")}, "zuordnung": guete}
        bekannt[st["name"]] = eintrag
        ergebnis.append({**st, **eintrag})
        time.sleep(1.5)
    datei.write_text(json.dumps(bekannt, ensure_ascii=False, indent=2), encoding="utf-8")
    return ergebnis


def abfrage_stationen(gti: GTI, cfg: dict, station: dict, art: str, speicher: Speicher) -> None:
    """Eine departureList-Abfrage (Abfahrten oder Ankünfte) für eine Station."""
    t0 = jetzt()
    lokal = t0.astimezone(BERLIN)
    nutzlast = {
        "station": {k: v for k, v in station["gti"].items() if v is not None},
        "time": {"date": lokal.strftime("%d.%m.%Y"), "time": lokal.strftime("%H:%M")},
        "maxList": cfg["max_eintraege"],
        "maxTimeOffset": cfg["vorausschau_min"],
        "allStationsInChangingNode": cfg["alle_haltestellen_im_knoten"],
        "useRealtime": True,
        "departure": art == "ab",
    }
    antw = gti.anfrage("departureList", nutzlast)
    bezug = gti_zeit(antw.daten["time"]) if antw.daten.get("time") else lokal.replace(second=0, microsecond=0)
    abfahrten = antw.daten.get("departures") or []
    neu = 0
    for dep in abfahrten:
        neu += speicher.nimm_auf("abfahrten", abfahrt_zu_zeile(dep, bezug, station["name"], art, t0))
    speicher.protokolliere(zeit_utc=t0, methode="departureList", station=station["name"], art=art,
                           http_status=antw.http_status, return_code="OK", anzahl=len(abfahrten),
                           dauer_ms=antw.dauer_ms, fehler=None, neu=neu)


def abfrage_meldungen(gti: GTI, cfg: dict, speicher: Speicher) -> None:
    t0 = jetzt()
    antw = gti.anfrage("getAnnouncements", {"names": cfg["meldungen_linien"], "full": True,
                                            "filterPlanned": "NO_FILTER"})
    meldungen = antw.daten.get("announcements") or []
    neu = sum(speicher.nimm_auf("meldungen", meldung_zu_zeile(m, t0)) for m in meldungen)
    speicher.protokolliere(zeit_utc=t0, methode="getAnnouncements", station=None, art=None,
                           http_status=antw.http_status, return_code="OK", anzahl=len(meldungen),
                           dauer_ms=antw.dauer_ms, fehler=None, neu=neu)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--daten", required=True, help="Ordner des (privaten) Daten-Repositorys")
    parser.add_argument("--dauer-min", type=float, default=24.0)
    parser.add_argument("--konfig", default=str(HIER / "konfig.json"))
    args = parser.parse_args()

    benutzer, passwort = os.environ.get("GTI_USER"), os.environ.get("GTI_PASS")
    if not benutzer or not passwort:
        print("Keine Zugangsdaten (GTI_USER / GTI_PASS) gesetzt. Der Sammler startet nicht.")
        return 1

    cfg = json.loads(Path(args.konfig).read_text(encoding="utf-8"))
    daten = Path(args.daten)
    daten.mkdir(parents=True, exist_ok=True)
    start = jetzt()
    lauf_id = start.strftime("%Y%m%dT%H%M%SZ")
    speicher = Speicher(daten, lauf_id)
    speicher.lade_zustand(start.astimezone(BERLIN).date())
    gti = GTI(benutzer, passwort, cfg["api_basis"], cfg["api_version"])

    try:
        stationen = pruefe_stationen(gti, cfg, daten / "stationen_geprueft.json", speicher)
    except GTIFehler as f:
        print(f"Abbruch beim Prüfen der Stationen: {f}")
        speicher.schreibe()
        return 2

    aufgaben = [(st, "ab") for st in stationen] + [(st, "an") for st in stationen if st.get("ankuenfte")]
    abstand = max(cfg["zyklus_s"] / max(len(aufgaben), 1), 1.0 / cfg["max_abfragen_pro_s"])
    ende = time.monotonic() + args.dauer_min * 60
    naechste_meldung = time.monotonic()
    naechstes_schreiben = time.monotonic() + SCHREIB_INTERVALL_S
    fehler_in_folge = 0
    print(f"Lauf {lauf_id}: {len(stationen)} Stationen, {len(aufgaben)} Abfragen je Runde, "
          f"Abstand {abstand:.1f} s, Dauer {args.dauer_min:.0f} min.")

    takt = time.monotonic()
    i = 0
    while time.monotonic() < ende:
        if time.monotonic() >= naechste_meldung:
            aufgabe = ("meldungen", None)
            naechste_meldung = time.monotonic() + cfg["meldungen_alle_s"]
        else:
            aufgabe = aufgaben[i % len(aufgaben)]
            i += 1
        try:
            if aufgabe[0] == "meldungen":
                abfrage_meldungen(gti, cfg, speicher)
            else:
                abfrage_stationen(gti, cfg, aufgabe[0], aufgabe[1], speicher)
            fehler_in_folge = 0
        except GTIFehler as f:
            fehler_in_folge += 1
            name = None if aufgabe[0] == "meldungen" else aufgabe[0]["name"]
            speicher.protokolliere(zeit_utc=jetzt(), methode="getAnnouncements" if name is None else "departureList",
                                   station=name, art=None if name is None else aufgabe[1],
                                   http_status=f.http_status, return_code=f.return_code, anzahl=None,
                                   dauer_ms=None, fehler=str(f), neu=None)
            if f.dauerhaft or fehler_in_folge >= cfg["max_fehler_in_folge_bis_abbruch"]:
                print(f"Abbruch nach Fehler: {f}")
                break
            pausen = cfg["pause_nach_fehler_s"]
            pause = pausen[min(fehler_in_folge - 1, len(pausen) - 1)]
            print(f"Fehler ({f}); Pause {pause} s.")
            time.sleep(min(pause, max(0.0, ende - time.monotonic())))
            takt = time.monotonic()
            continue

        if time.monotonic() >= naechstes_schreiben:
            speicher.schreibe()
            naechstes_schreiben = time.monotonic() + SCHREIB_INTERVALL_S

        takt += abstand
        time.sleep(max(0.0, takt - time.monotonic()))

    speicher.schreibe()
    z = speicher.zaehler
    print(f"Lauf beendet: {z['abfragen']} Abfragen, {z['fehler']} Fehler, "
          f"{z.get('abfahrten_neu', 0)} neue/geänderte Abfahrten, {z.get('meldungen_neu', 0)} neue Meldungen.")
    return 0 if z["abfragen"] > z["fehler"] else 3


if __name__ == "__main__":
    sys.exit(main())
