"""Pflege des privaten Daten-Repositorys. Läuft zu Beginn jedes Sammel-Laufs.

Weil der Sammel-Lauf der einzige ist, der ins Daten-Repository schreibt, kann sich hier nichts in
die Quere kommen. Schritte (jeder nur, wenn etwas zu tun ist):

1. Tage zusammenfassen: die vielen Teil-Dateien abgeschlossener Tage -> eine tag.parquet je Tag und Art.
2. Endstand bauen: je abgeschlossenem Tag eine kleine Tabelle mit einer Zeile pro Abfahrt
   (letzter Stand, letzte Prognose vor der Abfahrt, Ausfall, Grund). Grundlage aller Statistiken.
   Liegt dauerhaft unter endstand/JJJJ-MM/JJJJ-MM-TT.parquet (privat, weil nicht aggregiert).
3. Monat archivieren: ab dem 3. des Folgemonats werden die Rohdaten eines Monats als Release
   "rohdaten-JJJJ-MM" gespeichert (je Art eine Parquet-Datei, dazu Prüfsummen und Inhaltsangabe),
   nach dem Hochladen erneut heruntergeladen und verglichen und erst dann aus dem Repository entfernt.
4. Historie neu aufsetzen: Nach Änderungen wird der aktuelle Stand als einziger Commit gespeichert.
   Ohne diesen Schritt würde Git jede jemals gespeicherte Datei für immer behalten. Die Echtheit der
   Rohdaten belegen die Prüfsummen der Monatsarchive.

Aufruf (im Sammel-Workflow):  GH_TOKEN=... python sammler/pflege.py --daten daten --repo finngrz/s1-monitor-daten
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

HIER = Path(__file__).resolve().parent
sys.path.insert(0, str(HIER))

from kompaktieren import kompaktiere  # noqa: E402
from speicher import BERLIN, kennungen_kuerzen  # noqa: E402

ARTEN = ("abfahrten", "meldungen", "protokoll")
ARCHIV_AB_TAG = 3  # Monat M wird ab dem 3. des Folgemonats archiviert


# --------------------------------------------------------------------------- Git-Hilfen
def git(daten: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(daten), *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} fehlgeschlagen: {r.stderr.strip()}")
    return r.stdout.strip()


def ordner_im_repo(daten: Path, pfad: str) -> list[str]:
    """Namen der Unterordner bzw. Dateien unter pfad im letzten Commit (ohne sie herunterzuladen)."""
    try:
        aus = git(daten, "ls-tree", "--name-only", "HEAD", f"{pfad.rstrip('/')}/")
    except RuntimeError:
        return []
    return sorted(Path(z).name for z in aus.splitlines() if z)


def sichtbar_machen(daten: Path, muster: list[str]) -> None:
    """Holt zusätzliche Ordner in die Teil-Arbeitskopie (nur nötig, wenn sie nicht schon da sind)."""
    try:
        aktiv = git(daten, "config", "--get", "core.sparseCheckout")
    except RuntimeError:
        aktiv = ""
    if aktiv == "true" and muster:
        git(daten, "sparse-checkout", "add", *muster)


def sha256(pfad: Path) -> str:
    h = hashlib.sha256()
    with open(pfad, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def lies(dateien: list[Path]) -> pd.DataFrame:
    teile = [pd.read_parquet(p) for p in dateien]
    return pd.concat(teile, ignore_index=True) if teile else pd.DataFrame()


# --------------------------------------------------------------------------- 2. Endstand
def gruende(roh: str) -> list[str]:
    try:
        attribute = json.loads(roh).get("attributes") or []
    except (ValueError, AttributeError):
        return []
    return [a.get("value") for a in attribute if "REALTIME_REASON" in (a.get("types") or []) and a.get("value")]


ENDSTAND_VERSION = 2  # 2: Züge über Station, Linie, Soll-Zeit und Richtung erkannt (serviceId wechselt)


def stationsreihenfolge() -> list[str]:
    cfg = json.loads((HIER / "konfig.json").read_text(encoding="utf-8"))
    return [s["name"] for s in cfg["stationen"]]


def richtung_gruppe(df: pd.DataFrame, reihenfolge: list[str]) -> pd.Series:
    """S-Bahn: 'west'/'ost' aus Lage von Station und Ziel entlang der Strecke. Sonst: das Ziel selbst."""
    idx = {n: i for i, n in enumerate(reihenfolge)}
    ziel = df["richtung"].fillna("").str.split(" / ").str[0]
    zi, hi = ziel.map(idx), df["abfrage_station"].map(idx)
    rg = pd.Series(np.where(zi < hi, "west", np.where(zi > hi, "ost", "")), index=df.index)
    bahn = df["verkehrsmittel"].eq("TRAIN") & zi.notna() & hi.notna() & rg.ne("")
    return rg.where(bahn, "ziel:" + df["richtung"].fillna(""))


def endstand_tag(beobachtungen: pd.DataFrame, tag: date, reihenfolge: list[str] | None = None) -> pd.DataFrame:
    """Eine Zeile pro Abfahrt mit Soll-Datum `tag` (Berliner Zeit).

    Ein Zug wird über Station, Linie, Soll-Zeit und Richtung erkannt, nicht über die serviceId, weil die
    Schnittstelle für denselben Zug wechselnde serviceIds liefert. Geflügelte Züge (gleiche Zeit, gleiche
    Richtung) zählen dadurch als ein Zug.
    """
    df = kennungen_kuerzen(beobachtungen.copy(), "abfahrten")
    if df.empty:
        return pd.DataFrame()
    df["beobachtet_utc"] = pd.to_datetime(df["beobachtet_utc"], utc=True)
    df["soll_utc"] = pd.to_datetime(df["soll_utc"], utc=True)
    df = df[df["soll_utc"].dt.tz_convert(BERLIN).dt.date == tag].copy()
    if df.empty:
        return pd.DataFrame()
    df["richtung_gruppe"] = richtung_gruppe(df, reihenfolge or stationsreihenfolge())
    df["fahrt"] = pd.util.hash_pandas_object(
        df[["abfrage_station", "linie", "soll_utc", "richtung_gruppe"]], index=False).astype("uint64").map("{:016x}".format)
    df = df.sort_values("beobachtet_utc", kind="stable")
    df["ausfall"] = df["ausfall"].fillna(False).astype(bool)

    letzte = df.drop_duplicates("fahrt", keep="last").set_index("fahrt")
    mit_wert = df[df["verspaetung_s"].notna()]
    letzter_wert = mit_wert.drop_duplicates("fahrt", keep="last").set_index("fahrt")
    vor = mit_wert[mit_wert["beobachtet_utc"] <= mit_wert["soll_utc"] + pd.to_timedelta(mit_wert["verspaetung_s"], unit="s")]
    vor = vor.drop_duplicates("fahrt", keep="last").set_index("fahrt")
    zaehl = df.groupby("fahrt").agg(beobachtungen=("stand", "size"), erste_beobachtung_utc=("beobachtet_utc", "min"),
                                    kennungen=("service_id", "nunique"))
    ziele = df.groupby("fahrt")["richtung"].agg(lambda x: " | ".join(dict.fromkeys(x.dropna())))
    g = df[["fahrt", "roh"]].copy()
    g["g"] = g["roh"].map(gruende)
    g = g.explode("g").dropna(subset=["g"]).drop_duplicates(["fahrt", "g"])
    grund = g.groupby("fahrt")["g"].agg("; ".join)

    spalten = ["abfrage_station", "haltestelle_id", "haltestelle", "linie", "linie_id", "richtung_gruppe", "richtung",
               "verkehrsmittel", "service_id", "soll_utc", "ausfall", "zusatzfahrt", "gleis", "gleis_echtzeit"]
    erg = letzte[[s for s in spalten if s in letzte]].copy()
    erg["ziele"] = ziele.reindex(erg.index)
    erg["verspaetung_s"] = letzter_wert["verspaetung_s"].reindex(erg.index)  # letzter bekannter Wert
    erg["letzte_beobachtung_utc"] = letzte["beobachtet_utc"]
    erg["prognose_vor_abfahrt_s"] = vor["verspaetung_s"].reindex(erg.index)
    erg["prognose_vor_abfahrt_utc"] = vor["beobachtet_utc"].reindex(erg.index)
    erg = erg.join(zaehl)
    erg["grund"] = grund.reindex(erg.index)
    erg.insert(0, "datum", tag.isoformat())
    erg["endstand_version"] = ENDSTAND_VERSION
    return erg.reset_index().rename(columns={"fahrt": "schluessel"}).sort_values(
        ["soll_utc", "abfrage_station", "linie"], kind="stable")


def endstand_pfad(daten: Path, tag: str) -> Path:
    return daten / "endstand" / tag[:7] / f"{tag}.parquet"


def endstand_aktuell(daten: Path, tag: str) -> bool:
    pfad = endstand_pfad(daten, tag)
    sichtbar_machen(daten, [f"/endstand/{tag[:7]}/{tag}.parquet"])
    if not pfad.exists():
        return False
    try:
        v = pd.read_parquet(pfad, columns=["endstand_version"])["endstand_version"]
        return bool(len(v)) and int(v.iloc[0]) >= ENDSTAND_VERSION
    except Exception:  # ältere Datei ohne Versionsspalte
        return False


def endstaende_bauen(daten: Path, heute: date, jetzt_lokal: datetime) -> list[str]:
    """Baut fehlende und veraltete Endstände für alle Tage, deren Rohdaten noch im Repository liegen."""
    tage = [date.fromisoformat(t) for t in ordner_im_repo(daten, "abfahrten")]
    vorhanden = set()
    for monat in ordner_im_repo(daten, "endstand"):
        vorhanden |= {Path(n).stem for n in ordner_im_repo(daten, f"endstand/{monat}")}
    reihenfolge = stationsreihenfolge()
    erledigt = []
    for tag in tage:
        # Ein Tag ist abgeschlossen, wenn auch die Fahrten kurz vor Mitternacht beobachtet wurden.
        abgeschlossen = tag <= heute - timedelta(days=2) or (tag == heute - timedelta(days=1) and jetzt_lokal.hour >= 1)
        if not abgeschlossen:
            continue
        if tag.isoformat() in vorhanden and endstand_aktuell(daten, tag.isoformat()):
            continue
        nachbarn = [(tag + timedelta(days=k)).isoformat() for k in (-1, 0, 1)]
        sichtbar_machen(daten, [f"/abfahrten/{n}/" for n in nachbarn])
        dateien = [p for n in nachbarn for p in sorted((daten / "abfahrten" / n).glob("*.parquet"))]
        e = endstand_tag(lies(dateien), tag, reihenfolge)
        if e.empty:
            continue
        ziel = endstand_pfad(daten, tag.isoformat())
        ziel.parent.mkdir(parents=True, exist_ok=True)
        e.to_parquet(ziel, index=False, compression="zstd")
        art = "neu berechnet" if tag.isoformat() in vorhanden else "gebaut"
        erledigt.append(f"endstand/{tag}: {len(e)} Abfahrten ({art}, Version {ENDSTAND_VERSION})")
    return erledigt


# --------------------------------------------------------------------------- 3. Archiv
def monate_zum_archivieren(daten: Path, heute: date) -> list[str]:
    if heute.day < ARCHIV_AB_TAG:
        aktuell_grenze = (heute.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")  # Vormonat noch nicht
    else:
        aktuell_grenze = heute.strftime("%Y-%m")
    monate = {t[:7] for art in ARTEN for t in ordner_im_repo(daten, art)}
    return sorted(m for m in monate if m < aktuell_grenze)


def archiviere_monat(daten: Path, monat: str, repo: str, hochladen, herunterladen, release_vorhanden) -> str:
    tage = ordner_im_repo(daten, "abfahrten")
    tage_monat = [t for t in tage if t.startswith(monat)]
    fehlend = [t for t in tage_monat if not endstand_aktuell(daten, t)]
    if fehlend:
        return f"{monat}: noch nicht archiviert, aktueller Endstand fehlt für {', '.join(fehlend)}"

    sichtbar_machen(daten, [f"/{art}/{monat}-*/" for art in ARTEN])
    tag_name = f"rohdaten-{monat}"
    arbeit = Path(tempfile.mkdtemp(prefix="archiv_"))
    try:
        inhalt = {"monat": monat, "erstellt_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  "tage": tage_monat, "dateien": {}}
        for art in ARTEN:
            dateien = sorted((daten / art).glob(f"{monat}-*/*.parquet"))
            if not dateien:
                continue
            df = lies(dateien)
            df = kennungen_kuerzen(df, art)
            spalte = "beobachtet_utc" if "beobachtet_utc" in df else "zeit_utc"
            df = df.sort_values(spalte, kind="stable")
            pfad = arbeit / f"{tag_name}-{art}.parquet"
            df.to_parquet(pfad, index=False, compression="zstd")
            inhalt["dateien"][pfad.name] = {"zeilen": len(df), "sha256": sha256(pfad), "bytes": pfad.stat().st_size}
        (arbeit / "inhalt.json").write_text(json.dumps(inhalt, ensure_ascii=False, indent=2), encoding="utf-8")
        (arbeit / "pruefsummen.sha256").write_text(
            "".join(f"{v['sha256']}  {k}\n" for k, v in inhalt["dateien"].items()), encoding="utf-8")

        if release_vorhanden(tag_name):
            # Frühere Archivierung ist hochgeladen, das Löschen aber nicht erfolgt: Inhalt vergleichen.
            zurueck = Path(tempfile.mkdtemp(prefix="pruef_"))
            herunterladen(tag_name, zurueck)
            alt = json.loads((zurueck / "inhalt.json").read_text(encoding="utf-8"))
            for name, info in inhalt["dateien"].items():
                if alt["dateien"].get(name, {}).get("zeilen") != info["zeilen"]:
                    raise RuntimeError(f"Archiv {tag_name} vorhanden, aber Zeilenzahl von {name} weicht ab")
        else:
            hochladen(tag_name, sorted(arbeit.iterdir()), monat)
            zurueck = Path(tempfile.mkdtemp(prefix="pruef_"))
            herunterladen(tag_name, zurueck)
            for name, info in inhalt["dateien"].items():
                if sha256(zurueck / name) != info["sha256"]:
                    raise RuntimeError(f"Prüfsumme von {name} nach dem Hochladen falsch, nichts gelöscht")

        for art in ARTEN:
            for t in ordner_im_repo(daten, art):
                if t.startswith(monat):
                    git(daten, "rm", "-r", "-q", "--sparse", f"{art}/{t}")
        anzahl = sum(v["zeilen"] for v in inhalt["dateien"].values())
        return f"{monat}: archiviert als Release {tag_name} ({anzahl} Zeilen, Prüfsummen geprüft) und aus dem Repository entfernt"
    finally:
        shutil.rmtree(arbeit, ignore_errors=True)


def gh_funktionen(repo: str):
    def gh(*args):
        r = subprocess.run(["gh", *args], capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"gh {args[0]} {args[1]} fehlgeschlagen: {r.stderr.strip()}")
        return r.stdout

    def hochladen(tag, dateien, monat):
        gh("release", "create", tag, *map(str, dateien), "--repo", repo, "--title", f"Rohdaten {monat}",
           "--notes", "Unveränderte Rohdaten der Echtzeit-Erhebung (privat). Prüfsummen: pruefsummen.sha256, "
           "Inhalt: inhalt.json. Datenquelle: Geofox-Schnittstelle der Hamburger Hochbahn AG.")

    def herunterladen(tag, ziel):
        gh("release", "download", tag, "--repo", repo, "--dir", str(ziel), "--clobber")

    def vorhanden(tag):
        return subprocess.run(["gh", "release", "view", tag, "--repo", repo], capture_output=True).returncode == 0

    return hochladen, herunterladen, vorhanden


# --------------------------------------------------------------------------- 4. Historie
def speichern_und_historie_neu(daten: Path, nachricht: str) -> str:
    """Speichert alle Änderungen und ersetzt die Historie durch einen einzigen Commit mit gleichem Inhalt."""
    git(daten, "add", "--sparse", "-A")
    if git(daten, "status", "--porcelain"):
        git(daten, "commit", "-q", "-m", nachricht)
    git(daten, "pull", "-q", "--rebase")
    entfernt = git(daten, "ls-remote", "origin", "refs/heads/main").split()[0]
    baum = git(daten, "rev-parse", "HEAD^{tree}")
    stand = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    neu = git(daten, "commit-tree", baum, "-m", f"Datenstand {stand} (Historie neu aufgesetzt; {nachricht})")
    git(daten, "push", "-q", f"--force-with-lease=refs/heads/main:{entfernt}", "origin", f"{neu}:refs/heads/main")
    git(daten, "reset", "-q", "--soft", neu)
    return f"Historie neu aufgesetzt ({neu[:7]})"


# --------------------------------------------------------------------------- Ablauf
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--daten", required=True)
    parser.add_argument("--repo", required=True, help="owner/name des privaten Daten-Repositorys")
    parser.add_argument("--ohne-historie", action="store_true", help="Historie nicht neu aufsetzen (für Tests)")
    args = parser.parse_args()
    daten = Path(args.daten)
    jetzt_lokal = datetime.now(BERLIN)
    heute = jetzt_lokal.date()

    meldungen = []
    zusammengefasst = kompaktiere(daten, heute)
    meldungen += zusammengefasst
    meldungen += endstaende_bauen(daten, heute, jetzt_lokal)
    hochladen, herunterladen, vorhanden = gh_funktionen(args.repo)
    archiviert = False
    for monat in monate_zum_archivieren(daten, heute):
        text = archiviere_monat(daten, monat, args.repo, hochladen, herunterladen, vorhanden)
        meldungen.append(text)
        archiviert |= "archiviert als" in text

    for m in meldungen:
        print(m)
    git(daten, "add", "--sparse", "-A")
    geaendert = bool(git(daten, "status", "--porcelain"))
    if geaendert or archiviert or zusammengefasst:
        if args.ohne_historie:
            git(daten, "commit", "-q", "-m", "Pflege")
        else:
            print(speichern_und_historie_neu(daten, "Pflege: " + ("Monat archiviert" if archiviert else "Tage zusammengefasst")))
    print(f"::notice title=Pflege::{len(meldungen)} Pflegeschritte" + (" (inkl. Monatsarchiv)" if archiviert else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
