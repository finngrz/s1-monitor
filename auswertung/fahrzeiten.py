"""Plan-Fahrzeiten der S1 für die Live-Seite (Ankunft am Ziel schätzen).

Aus dem öffentlichen Soll-Fahrplan (hvv-GTFS, ergebnisse/fahrplan/s1_halte.parquet): für jedes Paar
aufeinanderfolgender Halte die typische Zeit von Abfahrt zu Abfahrt und von Abfahrt zu Ankunft (Median)
sowie je Halt die typische Haltezeit. Dazu Streckenreihenfolge und Äste (Poppenbüttel, Flughafen) aus sammler/konfig.json.
Ergebnis ist klein und öffentlich (keine Echtzeitdaten).

Aufruf:  python auswertung/fahrzeiten.py --ausgabe site/fahrzeiten.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

WURZEL = Path(__file__).resolve().parent.parent
AST_POPPENBUETTEL = ["Kornweg (Klein Borstel)", "Hoheneichen", "Wellingsbüttel", "Poppenbüttel"]
AST_FLUGHAFEN = ["Hamburg Airport (Flughafen)"]


def berechnen(halte: pd.DataFrame, stationen: list[str]) -> dict:
    h = halte[halte["linie"] == "S1"].sort_values(["trip_id", "folge"])
    h = h.assign(nach=h.groupby("trip_id")["halt"].shift(-1), ab_nach=h.groupby("trip_id")["ab_s"].shift(-1),
                 an_nach=h.groupby("trip_id")["an_s"].shift(-1))
    seg = h.dropna(subset=["nach"])
    an = (seg["an_nach"] - seg["ab_s"]).groupby([seg["halt"], seg["nach"]]).median()
    seg = (seg["ab_nach"] - seg["ab_s"]).groupby([seg["halt"], seg["nach"]]).median()
    halt = (h["ab_s"] - h["an_s"]).groupby(h["halt"]).median()
    stamm = [s for s in stationen if s not in AST_POPPENBUETTEL + AST_FLUGHAFEN]
    return {
        "stamm": stamm, "ast_poppenbuettel": AST_POPPENBUETTEL, "ast_flughafen": AST_FLUGHAFEN,
        "segment_s": {f"{a}|{b}": int(v) for (a, b), v in seg.items() if a in stationen and b in stationen},
        "segment_an_s": {f"{a}|{b}": int(v) for (a, b), v in an.items() if a in stationen and b in stationen},
        "haltezeit_s": {k: int(v) for k, v in halt.items() if k in stationen},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--halte", default=str(WURZEL / "ergebnisse" / "fahrplan" / "s1_halte.parquet"))
    parser.add_argument("--ausgabe", required=True)
    args = parser.parse_args()
    stationen = [s["name"] for s in json.loads((WURZEL / "sammler" / "konfig.json").read_text(encoding="utf-8"))["stationen"]]
    daten = berechnen(pd.read_parquet(args.halte), stationen)
    Path(args.ausgabe).parent.mkdir(parents=True, exist_ok=True)
    Path(args.ausgabe).write_text(json.dumps(daten, ensure_ascii=False), encoding="utf-8")
    print(f"fahrzeiten.json: {len(daten['segment_s'])} Abschnitte")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
