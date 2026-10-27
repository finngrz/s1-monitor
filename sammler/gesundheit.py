"""Täglicher Gesundheits-Check der Echtzeit-Erhebung.

Prüft anhand des Abfrageprotokolls:
- Abdeckung gestern: Anteil der Minuten mit mindestens einer erfolgreichen Abfrage, je Station
- Lücken über 5 Minuten (Methodik D6)
- Fehlerquote
- Läuft die Sammlung gerade? (letzte erfolgreiche Abfrage höchstens 90 Minuten alt)

Schreibt eine Zeile in ergebnisse/datenqualitaet.csv (nur Kennzahlen, keine Rohdaten; öffentlich) und
endet mit Fehlercode 1, wenn etwas nicht stimmt. GitHub schickt dann eine E-Mail.

Aufruf:  python sammler/gesundheit.py --daten daten --ausgabe ergebnisse/datenqualitaet.csv
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

HIER = Path(__file__).resolve().parent
sys.path.insert(0, str(HIER))
from speicher import BERLIN  # noqa: E402

MIN_ABDECKUNG = 0.95
MAX_FEHLERQUOTE = 0.05
MAX_ALTER_MIN = 90
LUECKE_S = 300


def pruefe(protokoll: pd.DataFrame, tag, jetzt_utc: datetime, stationen: int) -> tuple[dict, list[str]]:
    p = protokoll.copy()
    p["zeit_utc"] = pd.to_datetime(p["zeit_utc"], utc=True)
    probleme = []

    ok = p[(p["methode"] == "departureList") & p["fehler"].isna()]
    if ok.empty:
        return {"datum": tag.isoformat()}, ["Keine einzige erfolgreiche Abfrage gefunden"]
    alter = (jetzt_utc - ok["zeit_utc"].max()).total_seconds() / 60
    if alter > MAX_ALTER_MIN:
        probleme.append(f"Sammlung steht: letzte erfolgreiche Abfrage vor {alter:.0f} Minuten")

    lokal = p["zeit_utc"].dt.tz_convert(BERLIN)
    gestern = p[lokal.dt.date == tag]
    g_ok = gestern[(gestern["methode"] == "departureList") & gestern["fehler"].isna()]
    minuten_am_tag = int((datetime(tag.year, tag.month, tag.day, tzinfo=BERLIN) + timedelta(days=1)
                          - datetime(tag.year, tag.month, tag.day, tzinfo=BERLIN)).total_seconds() // 60)  # Zeitumstellung
    abdeckung = (g_ok.assign(m=g_ok["zeit_utc"].dt.floor("min")).groupby("station")["m"].nunique() / minuten_am_tag)
    fehlende = max(0, stationen - abdeckung.size)
    luecken = 0
    for _, g in g_ok.sort_values("zeit_utc").groupby("station"):
        luecken += int((g["zeit_utc"].diff().dt.total_seconds() > LUECKE_S).sum())
    fehlerquote = gestern["fehler"].notna().mean() if len(gestern) else 1.0

    kennzahlen = {
        "datum": tag.isoformat(),
        "abfragen": len(gestern),
        "fehlerquote": round(float(fehlerquote), 4),
        "stationen": int(abdeckung.size),
        "abdeckung_min": round(float(abdeckung.min()), 4) if abdeckung.size else 0.0,
        "abdeckung_mittel": round(float(abdeckung.mean()), 4) if abdeckung.size else 0.0,
        "luecken_ueber_5_min": luecken,
        "geprueft_utc": jetzt_utc.isoformat(timespec="seconds"),
    }
    if fehlende:
        probleme.append(f"{fehlende} Station(en) gestern ohne erfolgreiche Abfrage")
    if abdeckung.size and abdeckung.min() < MIN_ABDECKUNG:
        schwach = abdeckung[abdeckung < MIN_ABDECKUNG].round(3).to_dict()
        probleme.append(f"Abdeckung gestern unter {MIN_ABDECKUNG:.0%}: {schwach}")
    if fehlerquote > MAX_FEHLERQUOTE:
        probleme.append(f"Fehlerquote gestern {fehlerquote:.1%}")
    return kennzahlen, probleme


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--daten", required=True)
    parser.add_argument("--ausgabe", required=True)
    parser.add_argument("--stationen", type=int, default=31)
    parser.add_argument("--test-fehler", action="store_true", help="Absichtlich fehlschlagen (E-Mail testen)")
    args = parser.parse_args()

    jetzt = datetime.now(timezone.utc)
    tag = jetzt.astimezone(BERLIN).date() - timedelta(days=1)
    dateien = sorted(Path(args.daten, "protokoll").glob("*/*.parquet"))
    protokoll = pd.concat([pd.read_parquet(d) for d in dateien], ignore_index=True) if dateien else pd.DataFrame(
        columns=["zeit_utc", "methode", "station", "fehler"])
    kennzahlen, probleme = pruefe(protokoll, tag, jetzt, args.stationen)
    # Läuft die Pflege? Der Endstand von vorgestern muss inzwischen gebaut sein.
    vor2 = tag - timedelta(days=1)
    endstand = Path(args.daten, "endstand", vor2.isoformat()[:7], f"{vor2.isoformat()}.parquet")
    if Path(args.daten, "protokoll", vor2.isoformat()).exists() and not endstand.exists():
        probleme.append(f"Pflege läuft nicht: Endstand für {vor2} fehlt")
    if args.test_fehler:
        probleme.append("Test: absichtlich ausgelöster Fehler, um die E-Mail-Benachrichtigung zu prüfen")
    kennzahlen["probleme"] = " | ".join(probleme)

    ausgabe = Path(args.ausgabe)
    ausgabe.parent.mkdir(parents=True, exist_ok=True)
    alt = pd.read_csv(ausgabe) if ausgabe.exists() else pd.DataFrame()
    if not args.test_fehler:
        neu = pd.concat([alt[alt.get("datum", pd.Series(dtype=str)) != kennzahlen["datum"]] if len(alt) else alt,
                         pd.DataFrame([kennzahlen])], ignore_index=True)
        neu.to_csv(ausgabe, index=False)

    print(f"::notice title=Gesundheit {kennzahlen['datum']}::Abdeckung min {kennzahlen.get('abdeckung_min')}, "
          f"Mittel {kennzahlen.get('abdeckung_mittel')}, Lücken {kennzahlen.get('luecken_ueber_5_min')}, "
          f"Fehlerquote {kennzahlen.get('fehlerquote')}")
    for p in probleme:
        print(f"::error title=Problem::{p}")
    return 1 if probleme else 0


if __name__ == "__main__":
    sys.exit(main())
