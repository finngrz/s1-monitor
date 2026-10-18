"""Lädt die aktuelle Version des hvv-Fahrplans (GTFS) herunter und prüft sie.

Ablauf:
1. Im Transparenzportal Hamburg nach dem neuesten GTFS-Datensatz suchen.
2. Falls das scheitert: die Ersatz-Adressen aus quelle.json probieren.
3. Datei herunterladen, auf Vollständigkeit prüfen, Prüfsumme berechnen.
4. Metadaten (Quelle, Prüfsumme, Gültigkeit) als JSON neben die Datei schreiben.

Aufruf:  python fahrplan/quelle.py --ziel data/fahrplan
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

HIER = Path(__file__).resolve().parent
PFLICHTDATEIEN = {"agency.txt", "stops.txt", "routes.txt", "trips.txt", "stop_times.txt"}
TIMEOUT = 120


def lade_konfiguration() -> dict:
    return json.loads((HIER / "quelle.json").read_text(encoding="utf-8"))


def suche_im_transparenzportal(cfg: dict) -> str | None:
    """Gibt die URL der neuesten GTFS-ZIP-Datei zurück oder None."""
    try:
        antwort = requests.get(
            cfg["ckan_api"],
            params={"q": cfg["ckan_suche"], "rows": 50, "sort": "metadata_modified desc"},
            timeout=60,
        )
        antwort.raise_for_status()
        pakete = antwort.json()["result"]["results"]
    except Exception as fehler:  # Portal nicht erreichbar oder anderes Format
        print(f"Transparenzportal-Suche fehlgeschlagen: {fehler}")
        return None

    kandidaten = []
    for paket in pakete:
        titel = (paket.get("title") or "").lower()
        if "hvv" not in titel or "gtfs" not in titel:
            continue
        for res in paket.get("resources", []):
            url = res.get("url") or ""
            name = (res.get("name") or "") + " " + url
            if url.lower().endswith(".zip") and "gtfs" in name.lower():
                datum = res.get("last_modified") or res.get("created") or paket.get("metadata_modified") or ""
                kandidaten.append((datum, url))

    if not kandidaten:
        print("Im Transparenzportal keinen GTFS-Datensatz gefunden.")
        return None
    kandidaten.sort(reverse=True)
    print(f"Neuester Datensatz im Transparenzportal: {kandidaten[0][1]}")
    return kandidaten[0][1]


def lade_herunter(url: str, ziel: Path) -> Path:
    dateiname = Path(unquote(urlparse(url).path)).name or "fahrplan.zip"
    pfad = ziel / dateiname
    with requests.get(url, stream=True, timeout=TIMEOUT) as antwort:
        antwort.raise_for_status()
        with open(pfad, "wb") as datei:
            for block in antwort.iter_content(chunk_size=1 << 20):
                datei.write(block)
    return pfad


def pruefe_zip(pfad: Path) -> dict:
    """Prüft, ob die Datei ein vollständiger GTFS-Datensatz ist, und liest Kerndaten aus."""
    with zipfile.ZipFile(pfad) as z:
        namen = {Path(n).name for n in z.namelist()}
        fehlend = PFLICHTDATEIEN - namen
        if fehlend:
            raise ValueError(f"Kein vollständiger GTFS-Datensatz, es fehlen: {sorted(fehlend)}")
        if not ({"calendar.txt", "calendar_dates.txt"} & namen):
            raise ValueError("Weder calendar.txt noch calendar_dates.txt vorhanden.")

        def lies(name: str) -> list[dict]:
            treffer = [n for n in z.namelist() if Path(n).name == name]
            if not treffer:
                return []
            with z.open(treffer[0]) as roh:
                text = io.TextIOWrapper(roh, encoding="utf-8-sig")
                return list(csv.DictReader(text))

        feed_info = lies("feed_info.txt")
        daten = []
        for zeile in lies("calendar.txt"):
            daten += [zeile.get("start_date", ""), zeile.get("end_date", "")]
        for zeile in lies("calendar_dates.txt"):
            if zeile.get("exception_type") == "1":
                daten.append(zeile.get("date", ""))
        daten = sorted(d for d in daten if d)
        return {
            "dateien": sorted(namen),
            "feed_info": feed_info[0] if feed_info else None,
            "gueltig_von": daten[0] if daten else None,
            "gueltig_bis": daten[-1] if daten else None,
        }


def sha256(pfad: Path) -> str:
    h = hashlib.sha256()
    with open(pfad, "rb") as datei:
        for block in iter(lambda: datei.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ziel", default="data/fahrplan", help="Ordner für die heruntergeladene Datei")
    args = parser.parse_args()

    cfg = lade_konfiguration()
    ziel = Path(args.ziel)
    ziel.mkdir(parents=True, exist_ok=True)

    urls = []
    gefunden = suche_im_transparenzportal(cfg)
    if gefunden:
        urls.append(("transparenzportal", gefunden))
    urls += [("ersatz", u) for u in cfg["ersatz_urls"]]

    for methode, url in urls:
        try:
            print(f"Lade herunter ({methode}): {url}")
            pfad = lade_herunter(url, ziel)
            info = pruefe_zip(pfad)
        except Exception as fehler:
            print(f"  fehlgeschlagen: {fehler}")
            continue

        pruefsumme = sha256(pfad)
        meta = {
            "quelle_url": url,
            "methode": methode,
            "dateiname": pfad.name,
            "groesse_bytes": pfad.stat().st_size,
            "sha256": pruefsumme,
            "heruntergeladen_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "lizenz": cfg["lizenz"],
            "quellenvermerk": cfg["quellenvermerk"],
            **info,
        }
        meta_pfad = ziel / "fahrplan_meta.json"
        meta_pfad.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"OK: {pfad.name}, gültig {info['gueltig_von']}–{info['gueltig_bis']}, SHA-256 {pruefsumme[:12]}…")

        ausgabe = os.environ.get("GITHUB_OUTPUT")
        if ausgabe:
            with open(ausgabe, "a", encoding="utf-8") as f:
                f.write(f"zip={pfad}\nmeta={meta_pfad}\ntag=fahrplan-{pruefsumme[:12]}\n")
                f.write(f"gueltig_von={info['gueltig_von']}\ngueltig_bis={info['gueltig_bis']}\n")
        return 0

    print("Kein gültiger Fahrplan gefunden.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
