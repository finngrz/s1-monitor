"""Prüft den Gesundheits-Check mit erfundenen Abfrageprotokollen."""

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sammler"))
from gesundheit import pruefe  # noqa: E402
from speicher import BERLIN  # noqa: E402


def protokoll(tag, stationen=("A", "B"), luecke=None, fehler_jede=None):
    start = datetime(tag.year, tag.month, tag.day, tzinfo=BERLIN).astimezone(timezone.utc)
    zeilen = []
    for m in range(24 * 60):
        t = start + timedelta(minutes=m, seconds=5)
        for s in stationen:
            if luecke and s == luecke[0] and luecke[1] <= m < luecke[2]:
                continue
            fehler = "HTTP 500" if fehler_jede and m % fehler_jede == 0 else None
            zeilen.append({"zeit_utc": t, "methode": "departureList", "station": s, "fehler": fehler})
    return pd.DataFrame(zeilen)


TAG = date(2026, 11, 3)
JETZT = datetime(2026, 11, 4, 7, 13, tzinfo=timezone.utc)


def test_alles_gut():
    p = pd.concat([protokoll(TAG), protokoll(TAG + timedelta(days=1)).iloc[: 2 * 60 * 8]])
    k, probleme = pruefe(p, TAG, JETZT, stationen=2)
    assert probleme == []
    assert k["abdeckung_min"] == 1.0 and k["luecken_ueber_5_min"] == 0


def test_luecke_und_geringe_abdeckung():
    p = pd.concat([protokoll(TAG, luecke=("A", 600, 700)), protokoll(TAG + timedelta(days=1)).iloc[: 2 * 60 * 8]])
    k, probleme = pruefe(p, TAG, JETZT, stationen=2)
    assert k["luecken_ueber_5_min"] == 1
    assert any("Abdeckung" in x for x in probleme)  # 100 von 1440 Minuten fehlen -> 93 %


def test_sammlung_steht():
    p = protokoll(TAG)  # heute nichts mehr
    _, probleme = pruefe(p, TAG, JETZT, stationen=2)
    assert any("Sammlung steht" in x for x in probleme)


def test_fehlende_station_und_fehlerquote():
    p = pd.concat([protokoll(TAG, stationen=("A",), fehler_jede=10), protokoll(TAG + timedelta(days=1)).iloc[: 60 * 8]])
    _, probleme = pruefe(p, TAG, JETZT, stationen=2)
    assert any("ohne erfolgreiche Abfrage" in x for x in probleme)
    assert any("Fehlerquote" in x for x in probleme)
