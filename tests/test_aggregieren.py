"""Prüft die Aggregation für das Dashboard an einer kleinen, erfundenen Endstand-Tabelle."""

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

WURZEL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WURZEL / "auswertung"))
import aggregieren as ag  # noqa: E402


def zeile(station, soll, verspaetung_min, ziel="Poppenbüttel", ausfall=False, service=1, linie="S1",
          vm="TRAIN", prognose_min=None, gleis="1"):
    return {"datum": soll[:10], "abfrage_station": station, "linie": linie, "verkehrsmittel": vm, "richtung": ziel,
            "service_id": service, "soll_utc": pd.Timestamp(soll, tz="UTC"),
            "verspaetung_s": None if verspaetung_min is None else verspaetung_min * 60, "ausfall": ausfall,
            "gleis": gleis, "prognose_vor_abfahrt_s": None if prognose_min is None else prognose_min * 60}


def beispiel():
    z = [
        # Zug 1 Richtung Poppenbüttel: Verspätung wächst von 1 auf 3 Minuten
        zeile("Barmbek", "2026-11-03T07:00:00", 1, service=1, prognose_min=1),
        zeile("Alte Wöhr (Stadtpark)", "2026-11-03T07:02:00", 2, service=1, prognose_min=1),
        zeile("Rübenkamp (City Nord)", "2026-11-03T07:04:00", 3, service=1, prognose_min=3),
        # geflügelter Zug: zweiter Eintrag am selben Halt zur selben Zeit -> einmal zählen
        zeile("Barmbek", "2026-11-03T07:00:00", 1, ziel="Hamburg Airport (Flughafen)", service=2),
        # Zug Richtung Wedel, ausgefallen
        zeile("Barmbek", "2026-11-03T07:10:00", None, ziel="Wedel", ausfall=True, service=3, gleis="2"),
        # Startbahnhof: zählt nicht in die Pünktlichkeit
        zeile("Poppenbüttel", "2026-11-03T07:20:00", 0, ziel="Wedel", service=4, gleis="2"),
        # Bus wird für die Pünktlichkeit ignoriert
        zeile("Barmbek", "2026-11-03T07:00:00", 9, linie="172", vm="BUS", service=9),
    ]
    return pd.DataFrame(z)


def test_richtung_und_doppelte_zaehlung():
    s = ag.vorbereiten(beispiel(), ag.stationsreihenfolge())
    assert set(s.loc[s.richtung.str.startswith("Poppenbüttel"), "richtung_gruppe"]) == {"ost"}
    assert set(s.loc[s.richtung == "Wedel", "richtung_gruppe"]) == {"west"}
    assert len(s[(s.abfrage_station == "Barmbek") & (s.richtung_gruppe == "ost")]) == 1  # geflügelt einmal
    assert "172" not in set(s.linie)


def test_histogramm_und_abschnitte():
    s = ag.vorbereiten(beispiel(), ag.stationsreihenfolge())
    basis = s[~s.abfrage_station.isin(ag.STARTBAHNHOEFE)]
    h = ag.histogramme(basis, ["datum"])
    assert sum(h["hist"][0]) == 3 and h["ausfall"][0] == 1
    assert h["hist"][0][1] == 1 and h["hist"][0][2] == 1 and h["hist"][0][3] == 1
    ab = pd.DataFrame(ag.abschnitte_entlang(s, ag.stationsreihenfolge()))
    assert len(ab) == 2 and set(ab.summe_s) == {60.0}  # je Abschnitt +1 Minute


def test_kompletter_lauf(tmp_path):
    (tmp_path / "endstand" / "2026-11").mkdir(parents=True)
    beispiel().to_parquet(tmp_path / "endstand" / "2026-11" / "2026-11-03.parquet", index=False)
    subprocess.run([sys.executable, str(WURZEL / "auswertung" / "aggregieren.py"), "--endstand", str(tmp_path / "endstand"),
                    "--ausgabe", str(tmp_path / "aus"), "--qualitaet", str(tmp_path / "fehlt.csv")], check=True)
    d = json.loads((tmp_path / "aus" / "kennzahlen.json").read_text())
    assert d["meta"]["tage"] == 1 and d["meta"]["abfahrten"] == 4
    # keine Einzelfahrten im Ergebnis: keine Fahrt-Kennungen, keine Soll-Zeiten
    text = json.dumps(d)
    assert "service_id" not in text and "07:00:00" not in text
