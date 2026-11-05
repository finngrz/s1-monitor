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
        # Anteil der S1-Züge der nächsten 20 Minuten, die ohne Echtzeitangabe geliefert wurden (ab 02.11.)
        "s1_ohne_echtzeit": (round(float(g_ok["s1_20_ohne"].sum() / g_ok["s1_20"].sum()), 4)
                             if "s1_20" in g_ok and g_ok["s1_20"].sum() > 0 else None),
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


MAX_OHNE_ECHTZEIT_TAG = 0.15
MAX_SCHWACHE_STUNDEN = 3


def datenlage_pruefen(endstand: pd.DataFrame, probleme: list[str]) -> dict:
    """Gleiche Regel wie im Dashboard: Stunden mit zu vielen Abfahrten ohne Verspätungswert zählen nicht."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "auswertung"))
    import aggregieren as ag  # noqa: PLC0415

    q = ag.echtzeit_qualitaet(ag.vorbereiten(endstand, ag.stationsreihenfolge()))
    if q.empty:
        probleme.append("Endstand von gestern enthält keine S1-Abfahrten")
        return {}
    anteil = float(q["ohne"].sum() / q["n"].sum())
    schwach = [int(h) for h in q.loc[q["ausgeschlossen"], "stunde"]]
    if anteil > MAX_OHNE_ECHTZEIT_TAG or len(schwach) >= MAX_SCHWACHE_STUNDEN:
        probleme.append(f"Echtzeitdaten unvollständig: {anteil:.0%} der S1-Abfahrten ohne Verspätungswert, "
                        f"{len(schwach)} Stunde(n) aus der Statistik ausgeschlossen ({schwach})")
    s1 = endstand[(endstand["linie"] == "S1") & (endstand["verkehrsmittel"] == "TRAIN")]
    flattern = float((s1["verworfen"] > 0).mean()) if "verworfen" in s1 and len(s1) else None
    return {"endstand_ohne_wert": round(anteil, 4), "stunden_ausgeschlossen": len(schwach),
            "anteil_flattern": None if flattern is None else round(flattern, 4)}


LIVE_MAX_ALTER_MIN = 15
FLATTERN_TAGE = 7
FLATTERN_GRENZE = 0.10


def live_pruefen(probleme: list[str]) -> float | None:
    """Wird der verschlüsselte Live-Stand (Zweig „live“) noch laufend erneuert? Ohne Zweig: nichts zu prüfen."""
    import os  # noqa: PLC0415

    import requests  # noqa: PLC0415

    repo, token = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
    if not repo or not token:
        return None
    try:
        r = requests.get(f"https://api.github.com/repos/{repo}/branches/live", timeout=20,
                         headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
        if r.status_code == 404:
            return None  # Live-Seite (noch) nicht eingerichtet
        r.raise_for_status()
        zeit = datetime.fromisoformat(r.json()["commit"]["commit"]["committer"]["date"].replace("Z", "+00:00"))
    except Exception as f:  # noqa: BLE001
        print(f"::warning title=Live-Seite::Prüfung nicht möglich ({type(f).__name__})")
        return None
    alter = (datetime.now(timezone.utc) - zeit).total_seconds() / 60
    if alter > LIVE_MAX_ALTER_MIN:
        probleme.append(f"Live-Seite steht: letzter Live-Stand vor {alter:.0f} Minuten")
    return round(alter, 1)


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
    # Datenlage gestern: Liefert die Schnittstelle für genug S1-Abfahrten Verspätungswerte?
    endstand_gestern = Path(args.daten, "endstand", tag.isoformat()[:7], f"{tag.isoformat()}.parquet")
    if endstand_gestern.exists():
        kennzahlen.update(datenlage_pruefen(pd.read_parquet(endstand_gestern), probleme))
    else:
        print(f"::notice title=Datenlage::Endstand für {tag} noch nicht gebaut, Datenlage wird morgen nachgeholt")
    kennzahlen["live_alter_min"] = live_pruefen(probleme)
    if args.test_fehler:
        probleme.append("Test: absichtlich ausgelöster Fehler, um die E-Mail-Benachrichtigung zu prüfen")
    kennzahlen["probleme"] = " | ".join(probleme)

    ausgabe = Path(args.ausgabe)
    ausgabe.parent.mkdir(parents=True, exist_ok=True)
    alt = pd.read_csv(ausgabe) if ausgabe.exists() else pd.DataFrame()
    # Dauerhaftes Flattern der Schnittstelle (Meldung an die Hochbahn erst, wenn es länger anhält)
    verlauf = pd.concat([alt, pd.DataFrame([kennzahlen])], ignore_index=True).drop_duplicates("datum", keep="last")
    if "anteil_flattern" in verlauf:
        letzte = pd.to_numeric(verlauf.sort_values("datum")["anteil_flattern"], errors="coerce").tail(FLATTERN_TAGE)
        if len(letzte) == FLATTERN_TAGE and (letzte > FLATTERN_GRENZE).all():
            probleme.append(f"Schnittstelle flattert seit {FLATTERN_TAGE} Tagen (täglich über {FLATTERN_GRENZE:.0%} der "
                            "S1-Abfahrten): Zeitpunkt für eine Meldung an die Hochbahn")
            kennzahlen["probleme"] = " | ".join(probleme)
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
