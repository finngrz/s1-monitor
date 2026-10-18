"""Prüft den Echtzeit-Sammler mit nachgebauten Antworten der Schnittstelle (ohne echte Abfragen)."""

import base64
import hashlib
import hmac
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

WURZEL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WURZEL / "sammler"))

import gti as gti_modul  # noqa: E402
import sammler as sammler_modul  # noqa: E402
from speicher import BERLIN, Speicher, abfahrt_zu_zeile  # noqa: E402


def abfahrt(offset, delay=0, cancelled=False, service=4711, linie="S1"):
    return {"line": {"name": linie, "direction": "Poppenbüttel", "id": "SBH:S1_SBH_SBAHNS",
                     "type": {"simpleType": "TRAIN"}},
            "directionId": 1, "timeOffset": offset, "delay": delay, "cancelled": cancelled,
            "serviceId": service, "station": {"name": "Ohlsdorf", "id": "Master:90950"}, "platform": "Gl. 2"}


def test_signatur_wie_im_handbuch():
    koerper = b'{"version":63}'
    erwartet = base64.b64encode(hmac.new(b"geheim", koerper, hashlib.sha1).digest()).decode()
    assert gti_modul.signatur("geheim", koerper) == erwartet


def test_nur_aenderungen_werden_gespeichert(tmp_path):
    sp = Speicher(tmp_path, "lauf")
    t = datetime(2026, 11, 3, 8, 0, tzinfo=BERLIN)
    # 08:00 abgefragt, Abfahrt in 5 min (08:05), pünktlich
    assert sp.nimm_auf("abfahrten", abfahrt_zu_zeile(abfahrt(5), t, "Ohlsdorf", "ab", t))
    # 08:01 erneut abgefragt: gleiche Abfahrt, nur timeOffset 4 -> keine Änderung
    t2 = datetime(2026, 11, 3, 8, 1, tzinfo=BERLIN)
    assert not sp.nimm_auf("abfahrten", abfahrt_zu_zeile(abfahrt(4), t2, "Ohlsdorf", "ab", t2))
    # 08:02: jetzt 120 s Verspätung -> neue Zeile
    t3 = datetime(2026, 11, 3, 8, 2, tzinfo=BERLIN)
    assert sp.nimm_auf("abfahrten", abfahrt_zu_zeile(abfahrt(3, delay=120), t3, "Ohlsdorf", "ab", t3))
    # 08:03: fällt aus -> neue Zeile
    t4 = datetime(2026, 11, 3, 8, 3, tzinfo=BERLIN)
    assert sp.nimm_auf("abfahrten", abfahrt_zu_zeile(abfahrt(2, delay=120, cancelled=True), t4, "Ohlsdorf", "ab", t4))
    sp.schreibe()
    df = pd.read_parquet(next((tmp_path / "abfahrten" / "2026-11-03").glob("*.parquet")))
    assert len(df) == 3
    assert df.soll_utc.nunique() == 1  # immer dieselbe Fahrt (08:05)
    assert list(df.verspaetung_s) == [0, 120, 120]
    assert list(df.ausfall) == [False, False, True]


def test_neuer_lauf_setzt_nahtlos_fort(tmp_path):
    t = datetime(2026, 11, 3, 8, 0, tzinfo=BERLIN)
    sp = Speicher(tmp_path, "lauf1")
    sp.nimm_auf("abfahrten", abfahrt_zu_zeile(abfahrt(5), t, "Ohlsdorf", "ab", t))
    sp.schreibe()
    sp2 = Speicher(tmp_path, "lauf2")
    sp2.lade_zustand(t.date())
    t2 = datetime(2026, 11, 3, 8, 1, tzinfo=BERLIN)
    assert not sp2.nimm_auf("abfahrten", abfahrt_zu_zeile(abfahrt(4), t2, "Ohlsdorf", "ab", t2))


class FalscheAntwort:
    def __init__(self, status, daten):
        self.status_code = status
        self._daten = daten

    def json(self):
        return self._daten


class FalscheSitzung:
    """Spielt die Schnittstelle nach und merkt sich jede Anfrage."""

    def __init__(self):
        self.anfragen = []

    def post(self, url, data, headers, timeout):
        methode = url.rsplit("/", 1)[-1]
        nutzlast = json.loads(data)
        self.anfragen.append((methode, nutzlast, headers))
        erwartet = gti_modul.signatur("pw", data)
        if headers["geofox-auth-signature"] != erwartet:
            return FalscheAntwort(401, {})
        if methode == "checkName":
            name = nutzlast["theName"]["name"]
            return FalscheAntwort(200, {"returnCode": "OK", "results": [
                {"name": name, "id": "Master:90950", "globalId": "de:02000:90950", "type": "STATION"}]})
        if methode == "departureList":
            return FalscheAntwort(200, {"returnCode": "OK", "time": nutzlast["time"],
                                        "departures": [abfahrt(5), abfahrt(9, service=4712)]})
        if methode == "getAnnouncements":
            return FalscheAntwort(200, {"returnCode": "OK", "announcements": [
                {"id": "a1", "version": 1, "description": "Bauarbeiten", "publication": {}}]})
        return FalscheAntwort(404, {})


def test_lauf_von_anfang_bis_ende(tmp_path, monkeypatch):
    sitzung = FalscheSitzung()
    monkeypatch.setattr(gti_modul.requests, "Session", lambda: sitzung)
    monkeypatch.setattr(sammler_modul, "GTI", lambda *a, **k: gti_modul.GTI(*a, **k, sitzung=sitzung))
    cfg = json.loads((WURZEL / "sammler" / "konfig.json").read_text(encoding="utf-8"))
    cfg["stationen"] = [{"name": "Ohlsdorf", "ifopt": "de:02000:90950"},
                        {"name": "Poppenbüttel", "ifopt": "de:02000:71953", "ankuenfte": True}]
    cfg["zyklus_s"], cfg["max_abfragen_pro_s"] = 0.2, 50
    konfig = tmp_path / "konfig.json"
    konfig.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setenv("GTI_USER", "nutzer")
    monkeypatch.setenv("GTI_PASS", "pw")
    monkeypatch.setattr(sammler_modul.time, "sleep", lambda s: None)
    daten = tmp_path / "daten"
    monkeypatch.setattr(sys, "argv", ["sammler", "--daten", str(daten), "--dauer-min", "0.02", "--konfig", str(konfig)])

    assert sammler_modul.main() == 0
    methoden = [a[0] for a in sitzung.anfragen]
    assert methoden.count("checkName") == 2
    assert "getAnnouncements" in methoden
    assert any(a[1].get("departure") is False for a in sitzung.anfragen if a[0] == "departureList")  # Ankünfte
    assert all(a[1]["version"] == cfg["api_version"] for a in sitzung.anfragen)
    assert (daten / "stationen_geprueft.json").exists()
    abfahrten = pd.concat(pd.read_parquet(p) for p in (daten / "abfahrten").rglob("*.parquet"))
    # 2 Fahrten x (Ohlsdorf ab, Poppenbüttel ab, Poppenbüttel an) = 6 verschiedene, egal wie oft abgefragt
    assert abfahrten.schluessel.nunique() == 6
    assert len(abfahrten) == 6
    protokoll = pd.concat(pd.read_parquet(p) for p in (daten / "protokoll").rglob("*.parquet"))
    assert protokoll.fehler.isna().all()


def test_falsches_passwort_bricht_ab(tmp_path, monkeypatch):
    sitzung = FalscheSitzung()
    monkeypatch.setattr(sammler_modul, "GTI", lambda *a, **k: gti_modul.GTI(*a, **k, sitzung=sitzung))
    monkeypatch.setenv("GTI_USER", "nutzer")
    monkeypatch.setenv("GTI_PASS", "falsch")
    monkeypatch.setattr(sammler_modul.time, "sleep", lambda s: None)
    monkeypatch.setattr(sys, "argv", ["sammler", "--daten", str(tmp_path), "--dauer-min", "0.02"])
    assert sammler_modul.main() == 2
    assert len(sitzung.anfragen) == 1  # sofort aufgehört, keine weiteren Versuche


def test_kompaktieren(tmp_path):
    import kompaktieren
    t = datetime(2026, 11, 3, 8, 0, tzinfo=BERLIN)
    for lauf, delay in [("l1", 0), ("l2", 0), ("l3", 60)]:
        sp = Speicher(tmp_path, lauf)  # absichtlich ohne Zustand: simuliert überlappende Läufe
        sp.nimm_auf("abfahrten", abfahrt_zu_zeile(abfahrt(5, delay=delay), t, "Ohlsdorf", "ab", t))
        sp.schreibe()
    kompaktieren.kompaktiere(tmp_path, datetime(2026, 11, 4).date())
    ordner = tmp_path / "abfahrten" / "2026-11-03"
    assert [p.name for p in ordner.iterdir()] == ["tag.parquet"]
    df = pd.read_parquet(ordner / "tag.parquet")
    assert list(df.verspaetung_s) == [0, 60]  # doppelter Stand entfernt, Änderung bleibt
