"""Prüft Lage der Linie, Prognose-Nachschlagen, Live-Schnappschuss und Verschlüsselung."""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

WURZEL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WURZEL / "sammler"))
sys.path.insert(0, str(WURZEL / "auswertung"))
import lage as lg  # noqa: E402
import prognose_tabelle as pt  # noqa: E402
from live import LiveStand, entschluesseln, verschluesseln  # noqa: E402

CFG = json.loads((WURZEL / "sammler" / "konfig.json").read_text(encoding="utf-8"))
REIHE = [s["name"] for s in CFG["stationen"]]
JETZT = datetime(2026, 11, 3, 16, 0, tzinfo=timezone.utc)


def zeile(station, soll_min, v_min, ziel="Poppenbüttel", ausfall=False):
    return {"abfrage_station": station, "linie": "S1", "verkehrsmittel": "TRAIN", "richtung": ziel,
            "soll_utc": JETZT + timedelta(minutes=soll_min), "verspaetung_s": None if v_min is None else v_min * 60,
            "ausfall": ausfall}


def test_klassen_und_zulauf():
    assert lg.anzeige_klasse(None) == "0" and lg.anzeige_klasse(3.4) == "3-4" and lg.anzeige_klasse(12) == ">7"
    assert lg.lage_klasse(None) == "*" and lg.lage_klasse(0.5) == "<1" and lg.lage_klasse(6) == ">6"
    assert lg.vorlauf_klasse(-3) == 0 and lg.vorlauf_klasse(12) == 10 and lg.vorlauf_klasse(90) == 30
    assert lg.zulauf("Poppenbüttel", "west") == "ost" and lg.zulauf("Barmbek", "west") == "west"


def test_nachschlagen_weicht_bei_wenigen_faellen_aus():
    tab = {"zellen": {"unterwegs|10|0|>6": {"n": 5, "erwartet": 9}, "unterwegs|10|0|*": {"n": 100, "erwartet": 2}}}
    z = lg.nachschlagen(tab, "Barmbek", 11, 0, 7)
    assert z["erwartet"] == 2 and z["zelle"] == "unterwegs|10|0|*"
    assert lg.nachschlagen({}, "Barmbek", 11, 0, 7) is None


def test_live_lage_und_abfahrten():
    L = LiveStand(CFG["live"], REIHE, {"zellen": {"unterwegs|10|5-7|>6": {"n": 50, "erwartet": 9.0, "von": 7, "bis": 12,
                                                                        "anteil_ab5": 1.0}}})
    for i, st in enumerate(["Landungsbrücken", "Jungfernstieg", "Hamburg Hbf", "Berliner Tor"]):
        L.aufnehmen(zeile(st, -5 - i, 8))                       # Linie Richtung Ost gestört: Ø 8 min
    L.aufnehmen(zeile("Stadthausbrücke", 10, 6))                 # App zeigt +6
    L.aufnehmen(zeile("Stadthausbrücke", 12, 1, ziel="Hamburg Airport (Flughafen)"))  # nur Flughafen: nicht anzeigen
    L.aufnehmen(zeile("Stadthausbrücke", -25, 2))                # längst abgefahren, außerhalb des Lage-Fensters
    s = L.schnappschuss(JETZT)
    assert s["lage"]["ost"]["mittel_min"] == 8.0
    ab = next(a for a in s["ansichten"] if a["id"] == "stadthausbruecke")["abfahrten"]
    assert len(ab) == 1 and ab[0]["app_min"] == 6 and ab[0]["erwartet_min"] == 9 and ab[0]["ampel"] == "rot"


def test_verschluesselung():
    paket, _ = verschluesseln(b'{"a":1}', "geheim", os.urandom(16))
    assert entschluesseln(paket, "geheim") == b'{"a":1}' and "geheim" not in json.dumps(paket)


def test_prognose_tabelle_aus_endstand():
    zeilen = []
    for i in range(40):
        z = {"datum": "2026-11-03", "abfrage_station": "Barmbek", "linie": "S1", "verkehrsmittel": "TRAIN",
             "richtung_gruppe": "ost", "ausfall": False, "verspaetung_s": 300.0, "verspaetung_folgehalt_s": None}
        for L in lg.VORLAEUFE_MIN:
            z |= {f"anzeige_{L}_s": 120.0, f"lage_{L}_s": 400.0, f"lage_{L}_n": 10}
        zeilen.append(z)
    f = pt.faelle(pd.DataFrame(zeilen))
    zellen = pt.zellen_berechnen(f)
    assert zellen["unterwegs|10|1-2|>6"] == {"n": 40, "erwartet": 5.0, "von": 5.0, "bis": 5.0, "anteil_ab5": 1.0}
