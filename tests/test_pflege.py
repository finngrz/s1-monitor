"""Prüft die Pflege des Daten-Repositorys an einem echten (lokalen) Git-Repository mit Testdaten."""

import json
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

WURZEL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WURZEL / "sammler"))

import pflege  # noqa: E402
from kompaktieren import kompaktiere  # noqa: E402
from speicher import BERLIN, Speicher, abfahrt_zu_zeile, meldung_zu_zeile  # noqa: E402


def sh(*args, cwd=None):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


def abfahrt(offset, delay=0, cancelled=False, service=4711, gruende=None):
    d = {"line": {"name": "S1", "direction": "Poppenbüttel", "id": "S1", "type": {"simpleType": "TRAIN"}},
         "directionId": 1, "timeOffset": offset, "delay": delay, "cancelled": cancelled,
         "serviceId": service, "station": {"name": "Ohlsdorf", "id": "Master:90950"}}
    if gruende:
        d["attributes"] = [{"value": g, "types": ["REALTIME_REASON"]} for g in gruende]
    return d


def repo_mit_daten(tmp_path):
    fern = tmp_path / "fern.git"
    sh("git", "init", "-q", "--bare", "-b", "main", str(fern))
    daten = tmp_path / "daten"
    sh("git", "clone", "-q", str(fern), str(daten))
    sh("git", "-C", str(daten), "config", "user.name", "t")
    sh("git", "-C", str(daten), "config", "user.email", "t@t")
    sh("git", "-C", str(daten), "checkout", "-q", "-b", "main")
    (daten / "README.md").write_text("Daten\n")
    # Zwei Läufe an drei Tagen: 30.09., 01.10., 02.10.
    for tag in (date(2026, 9, 30), date(2026, 10, 1), date(2026, 10, 2)):
        for lauf, std in (("a", 8), ("b", 23)):
            t = datetime(tag.year, tag.month, tag.day, std, 0, tzinfo=BERLIN)
            sp = Speicher(daten, f"{tag}{lauf}")
            sp.nimm_auf("abfahrten", abfahrt_zu_zeile(abfahrt(5, service=std), t, "Ohlsdorf", "ab", t))
            t2 = t + timedelta(minutes=2)
            sp.nimm_auf("abfahrten", abfahrt_zu_zeile(abfahrt(3, delay=120, service=std, gruende=["Signalstörung"]),
                                                      t2, "Ohlsdorf", "ab", t2))
            t3 = t + timedelta(minutes=10)  # nach der Abfahrt (Rückblick): nachträglich korrigiert
            sp.nimm_auf("abfahrten", abfahrt_zu_zeile(abfahrt(-5, delay=180, service=std), t3, "Ohlsdorf", "ab", t3))
            sp.nimm_auf("meldungen", meldung_zu_zeile({"id": f"m{tag}", "description": "x", "publication": {}}, t))
            sp.protokolliere(zeit_utc=t, methode="departureList", station="Ohlsdorf", art="ab", http_status=200,
                             return_code="OK", anzahl=1, dauer_ms=100, fehler=None, neu=1)
            sp.schreibe()
    sh("git", "-C", str(daten), "add", "-A")
    sh("git", "-C", str(daten), "commit", "-q", "-m", "Daten")
    sh("git", "-C", str(daten), "push", "-q", "-u", "origin", "main")
    return fern, daten


def test_endstand_eines_tages():
    t = datetime(2026, 10, 1, 8, 0, tzinfo=BERLIN)
    zeilen = [abfahrt_zu_zeile(abfahrt(5), t, "Ohlsdorf", "ab", t),
              abfahrt_zu_zeile(abfahrt(3, delay=120, gruende=["Signalstörung"]), t + timedelta(minutes=2), "Ohlsdorf", "ab",
                               t + timedelta(minutes=2)),
              # 8:10 beobachtet: Zug (Soll 8:05, +3 = 8:08) ist schon weg, Verspätung nachträglich korrigiert
              abfahrt_zu_zeile(abfahrt(-5, delay=180), t + timedelta(minutes=10), "Ohlsdorf", "ab", t + timedelta(minutes=10))]
    e = pflege.endstand_tag(pd.DataFrame(zeilen), date(2026, 10, 1))
    assert len(e) == 1
    z = e.iloc[0]
    assert z.verspaetung_s == 180  # letzter Stand (nach der Abfahrt korrigiert)
    assert z.prognose_vor_abfahrt_s == 120  # was Fahrgäste vor der Abfahrt sahen
    assert z.beobachtungen == 3
    assert z.grund == "Signalstörung"


def test_pflege_komplett(tmp_path):
    fern, daten = repo_mit_daten(tmp_path)
    heute = date(2026, 10, 3)
    jetzt = datetime(2026, 10, 3, 6, 0, tzinfo=BERLIN)

    kompaktiere(daten, heute)
    for art in pflege.ARTEN:
        for t in ("2026-09-30", "2026-10-01", "2026-10-02"):
            assert [p.name for p in (daten / art / t).iterdir()] == ["tag.parquet"]

    sh("git", "-C", str(daten), "add", "-A")
    sh("git", "-C", str(daten), "commit", "-q", "-m", "kompaktiert")
    erledigt = pflege.endstaende_bauen(daten, heute, jetzt)
    assert len(erledigt) == 3
    e = pd.read_parquet(daten / "endstand" / "2026-10" / "2026-10-01.parquet")
    assert len(e) == 2 and set(e.verspaetung_s) == {180}
    sh("git", "-C", str(daten), "add", "-A")
    sh("git", "-C", str(daten), "commit", "-q", "-m", "endstand")

    # Archiv: September ist am 3.10. fällig, Oktober nicht.
    assert pflege.monate_zum_archivieren(daten, heute) == ["2026-09"]
    assert pflege.monate_zum_archivieren(daten, date(2026, 10, 2)) == []
    releases = tmp_path / "releases"

    def hochladen(tag, dateien, monat):
        (releases / tag).mkdir(parents=True)
        for d in dateien:
            (releases / tag / d.name).write_bytes(d.read_bytes())

    def herunterladen(tag, ziel):
        for d in (releases / tag).iterdir():
            (ziel / d.name).write_bytes(d.read_bytes())

    text = pflege.archiviere_monat(daten, "2026-09", "x/y", hochladen, herunterladen, lambda t: (releases / t).exists())
    assert "archiviert als Release rohdaten-2026-09" in text
    inhalt = json.loads((releases / "rohdaten-2026-09" / "inhalt.json").read_text())
    assert inhalt["dateien"]["rohdaten-2026-09-abfahrten.parquet"]["zeilen"] == 6
    for art in pflege.ARTEN:
        assert not (daten / art / "2026-09-30").exists()
    assert (daten / "endstand" / "2026-09" / "2026-09-30.parquet").exists()  # Endstand bleibt

    print(pflege.speichern_und_historie_neu(daten, "Test"))
    log = subprocess.run(["git", "--git-dir", str(fern), "log", "--oneline", "main"], capture_output=True, text=True).stdout
    assert len(log.strip().splitlines()) == 1  # nur noch ein Commit
    dateien = subprocess.run(["git", "--git-dir", str(fern), "ls-tree", "-r", "--name-only", "main"],
                             capture_output=True, text=True).stdout.split()
    assert "endstand/2026-09/2026-09-30.parquet" in dateien
    assert not any(d.startswith("abfahrten/2026-09") for d in dateien)
    assert "abfahrten/2026-10-02/tag.parquet" in dateien

    # Ein weiterer Sammel-Abschnitt kann danach ganz normal speichern
    (daten / "neu.txt").write_text("x")
    sh("git", "-C", str(daten), "add", "-A")
    sh("git", "-C", str(daten), "commit", "-q", "-m", "neu")
    sh("git", "-C", str(daten), "pull", "-q", "--rebase")
    sh("git", "-C", str(daten), "push", "-q")


def test_archiv_wird_nicht_geloescht_bei_falscher_pruefsumme(tmp_path):
    fern, daten = repo_mit_daten(tmp_path)
    kompaktiere(daten, date(2026, 10, 3))
    pflege.endstaende_bauen(daten, date(2026, 10, 3), datetime(2026, 10, 3, 6, tzinfo=BERLIN))
    sh("git", "-C", str(daten), "add", "-A")
    sh("git", "-C", str(daten), "commit", "-q", "-m", "x")
    releases = tmp_path / "r"

    def hochladen(tag, dateien, monat):
        (releases / tag).mkdir(parents=True)
        for d in dateien:
            (releases / tag / d.name).write_bytes(d.read_bytes() + (b"kaputt" if d.suffix == ".parquet" else b""))

    def herunterladen(tag, ziel):
        for d in (releases / tag).iterdir():
            (ziel / d.name).write_bytes(d.read_bytes())

    try:
        pflege.archiviere_monat(daten, "2026-09", "x/y", hochladen, herunterladen, lambda t: False)
        assert False, "hätte abbrechen müssen"
    except RuntimeError as f:
        assert "Prüfsumme" in str(f)
    assert (daten / "abfahrten" / "2026-09-30" / "tag.parquet").exists()  # nichts gelöscht
