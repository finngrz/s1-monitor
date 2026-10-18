"""Prüft die Kernrechnung der Anschluss-Analyse an kleinen, von Hand nachrechenbaren Beispielen."""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "analysen"))
from stufe1 import MIN_UMSTIEG_S, kennzahlen, naechste  # noqa: E402

MIN = 60


def test_naechste_erreichbare_abfahrt():
    # Bus fährt um 10:05, 10:25, 10:45; S1 kommt um 10:00 an.
    abfahrten = np.array([605, 625, 645]) * MIN
    ankunft = np.array([600]) * MIN
    # 10:05 ist erreichbar (5 min >= 3 min Mindest-Umsteigezeit)
    assert naechste(abfahrten, ankunft, MIN_UMSTIEG_S)[0] == 5 * MIN


def test_zu_knapp_heisst_naechster_bus():
    abfahrten = np.array([602, 622, 642]) * MIN  # 10:02 ist nur 2 min nach Ankunft
    ankunft = np.array([600]) * MIN
    assert naechste(abfahrten, ankunft, MIN_UMSTIEG_S)[0] == 22 * MIN


def test_betriebspause_wird_nicht_gezaehlt():
    # Letzter Bus abends 20:00, nächster erst 05:00: Ankunft 22:00 fällt in eine Betriebspause.
    abfahrten = np.array([19 * 60 + 40, 20 * 60, 29 * 60]) * MIN
    ankunft = np.array([22 * 60]) * MIN
    assert np.isnan(naechste(abfahrten, ankunft, MIN_UMSTIEG_S)[0])


def test_kein_bus_mehr():
    abfahrten = np.array([600, 620]) * MIN
    ankunft = np.array([630]) * MIN
    assert np.isnan(naechste(abfahrten, ankunft, MIN_UMSTIEG_S)[0])


def test_kennzahlen():
    z = kennzahlen(np.array([4, 6, 14, np.nan]) * MIN)
    assert z["n"] == 3
    assert abs(z["mittel_s"] - 8 * MIN) < 1e-9
    assert abs(z["gut"] - 2 / 3) < 1e-9  # 4 und 6 min gelten als guter Anschluss
    assert abs(z["schlecht"] - 1 / 3) < 1e-9  # 14 min > 12 min
