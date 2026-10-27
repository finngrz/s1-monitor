"""Fasst die vielen kleinen Teil-Dateien abgeschlossener Tage zu einer Datei je Tag zusammen.

Während des Tages schreibt jeder Lauf eigene kleine Dateien (so stören sich Läufe nie gegenseitig).
Nachts wird daraus je Tag und Art (abfahrten, meldungen, protokoll) eine einzige Datei
`JJJJ-MM-TT/tag.parquet`. Der heutige Tag wird nie angefasst.

Aufruf:  python sammler/kompaktieren.py --daten <Ordner>
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from speicher import BERLIN, kennungen_kuerzen  # noqa: E402


def kompaktiere(daten: Path, heute) -> list[str]:
    erledigt = []
    for art in ("abfahrten", "meldungen", "protokoll"):
        basis = daten / art
        if not basis.exists():
            continue
        for ordner in sorted(p for p in basis.iterdir() if p.is_dir()):
            if ordner.name >= heute.isoformat():
                continue
            teile = sorted(p for p in ordner.glob("*.parquet") if p.name != "tag.parquet")
            if not teile:
                continue
            ziel = ordner / "tag.parquet"
            alle = ([pd.read_parquet(ziel)] if ziel.exists() else []) + [pd.read_parquet(p) for p in teile]
            df = pd.concat(alle, ignore_index=True)
            spalte = "beobachtet_utc" if "beobachtet_utc" in df else "zeit_utc"
            df = df.sort_values(spalte, kind="stable")
            vorher = sum(len(x) for x in alle)
            df = kennungen_kuerzen(df, art)
            if "stand" in df:  # doppelt gespeicherte gleiche Stände (z. B. überlappende Läufe) entfernen
                df = df[df.stand != df.groupby("schluessel").stand.shift()]
            tmp = ordner / "tag.parquet.tmp"
            df.to_parquet(tmp, index=False, compression="zstd")
            tmp.replace(ziel)
            for p in teile:
                p.unlink()
            erledigt.append(f"{art}/{ordner.name}: {len(teile)} Teile, {vorher} -> {len(df)} Zeilen")
    return erledigt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--daten", required=True)
    args = parser.parse_args()
    for zeile in kompaktiere(Path(args.daten), datetime.now(BERLIN).date()):
        print(zeile)
    return 0


if __name__ == "__main__":
    sys.exit(main())
