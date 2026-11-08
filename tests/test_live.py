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


def test_flattern_wird_verworfen():
    assert lg.plausibel(300, False, 0, False) == (None, False, True)     # +5 -> 0: veraltet
    assert lg.plausibel(60, False, 0, False) == (0, False, False)        # +1 -> 0: echtes Aufholen
    assert lg.plausibel(None, True, 0, False) == (None, True, True)      # Ausfall -> „fährt, 0“: veraltet
    assert lg.plausibel(None, True, 240, False) == (240, False, False)   # Ausfall -> +4: fährt doch
    L = LiveStand(CFG["live"], REIHE, {})
    for v in (5, 0, 6, 0):
        L.aufnehmen(zeile("Barmbek", 3, v))
    assert next(iter(L.werte.values()))["v"] == 360


def test_bewertung_kennzahlen():
    import prognose_bewertung as pb
    g = pd.DataFrame({"y": [0, 8, 10, 1], "anzeige_min": [0, 2, 9, 0], "erwartet": [1, 7, 10, 6],
                      "von": [0, 5, 8, 0], "bis": [2, 9, 12, 3]})
    k = pb.kennzahlen(g)
    assert k["n"] == 4 and k["warn_app_erkannt"] == 0.5 and k["warn_prognose_erkannt"] == 1.0
    assert k["warn_prognose_berechtigt"] == round(2 / 3, 3) and k["in_spanne"] == 1.0


def test_meldungen_ohne_nebensachen():
    L = LiveStand(CFG["live"], REIHE, {})
    gueltig = [{"begin": "2026-11-03T00:00:00.000+0100", "end": "2026-11-04T00:00:00.000+0100"}]
    ort = [{"line": {"name": "S1"}}]
    L.meldungen_aufnehmen([
        {"summary": "Kein barrierefreier Zugang in Alte Wöhr (Stadtpark).", "planned": False, "locations": ort, "validities": gueltig},
        {"summary": "Verspätungen auf der S1", "planned": False, "locations": ort, "validities": gueltig},
        {"summary": "Busse statt S-Bahn zw. A u. B", "planned": True, "locations": ort, "validities": gueltig},
        {"summary": "Aufzug Barmbek außer Betrieb", "planned": True, "locations": ort, "validities": gueltig}], JETZT)
    assert [m["text"] for m in L.meldungen] == ["Busse statt S-Bahn zw. A u. B", "Verspätungen auf der S1"]
