"""Berechnet die Nachschlagetabelle für die Kurzfrist-Prognose aus der privaten Endstand-Tabelle.

Für jede Kombination aus Art des Halts (unterwegs / Startbahnhof), Vorlauf (0–30 Minuten), Anzeige der
Schnittstelle und Lage der Linie: wie spät waren die Züge tatsächlich? (Mittelwert, 20 %- und
80 %-Quantil, Anteil ab 5 Minuten). Regeln in sammler/lage.py. Veröffentlicht werden nur diese
Zählungen, keine Einzelfahrten.

Außerdem eine ehrliche Gütemessung: Tabelle ohne die letzten 2 Tage berechnen und an diesen 2 Tagen
prüfen, ob sie besser liegt als die Anzeige der Schnittstelle allein.

Aufruf:  python auswertung/prognose_tabelle.py --endstand <Ordner> --ausgabe ergebnisse/prognose_tabelle.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

WURZEL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WURZEL / "sammler"))
sys.path.insert(0, str(WURZEL / "auswertung"))
import lage as lg  # noqa: E402

PRUEF_TAGE = 2
WARN_AB_MIN = 4    # Warnung, wenn mindestens so viel erwartet wird
GROSS_AB_MIN = 5   # „deutlich verspätet“


def faelle(endstand: pd.DataFrame) -> pd.DataFrame:
    """Eine Zeile je S1-Abfahrt und Vorlauf mit Klassen und tatsächlicher Verspätung (Minuten)."""
    if "anzeige_0_s" not in endstand:
        return pd.DataFrame()
    s = endstand[(endstand["linie"] == "S1") & (endstand["verkehrsmittel"] == "TRAIN")
                 & endstand["richtung_gruppe"].isin(["west", "ost"]) & ~endstand["ausfall"].fillna(False).astype(bool)]
    s = s[s["anzeige_0_s"].notna() | s["lage_0_s"].notna()]  # nur Tage mit Prognose-Merkmalen (Version 3)
    start = s["abfrage_station"].isin(lg.STARTBAHNHOEFE)
    y = np.where(start, s["verspaetung_folgehalt_s"], s["verspaetung_s"]) / 60
    teile = []
    for L in lg.VORLAEUFE_MIN:
        lage_min = (s[f"lage_{L}_s"] / 60).where(s[f"lage_{L}_n"] >= lg.LAGE_MIN_ABFAHRTEN)
        teile.append(pd.DataFrame({
            "datum": s["datum"].to_numpy(), "art": np.where(start, "start", "unterwegs"), "vorlauf": L,
            "anzeige_min": (s[f"anzeige_{L}_s"].fillna(0) / 60).to_numpy(),
            "anzeige": [lg.anzeige_klasse(v / 60 if v == v else None) for v in s[f"anzeige_{L}_s"]],
            "lage": [lg.lage_klasse(v) for v in lage_min], "y": y}))
    f = pd.concat(teile, ignore_index=True)
    return f[f["y"].notna()]


def zellen_berechnen(f: pd.DataFrame) -> dict:
    zellen = {}
    for an_alle, la_alle in ((False, False), (False, True), (True, False), (True, True)):
        g = f.assign(anzeige="*" if an_alle else f["anzeige"], lage="*" if la_alle else f["lage"])
        for (a, v, an, la), x in g.groupby(["art", "vorlauf", "anzeige", "lage"]):
            y = x["y"].clip(lower=0)
            zellen[lg.zelle(a, int(v), an, la)] = {
                "n": int(len(y)), "erwartet": round(float(y.mean()), 1),
                "von": round(float(y.quantile(0.2)), 1), "bis": round(float(y.quantile(0.8)), 1),
                "anteil_ab5": round(float((y >= GROSS_AB_MIN).mean()), 3)}
    return zellen


def guete(f: pd.DataFrame) -> dict | None:
    tage = sorted(f["datum"].unique())
    if len(tage) < PRUEF_TAGE + 2:
        return None
    lern, pruef = f[f["datum"] < tage[-PRUEF_TAGE]], f[f["datum"] >= tage[-PRUEF_TAGE]]
    tab = {"zellen": zellen_berechnen(lern)}
    erg = {"lern_tage": len(tage) - PRUEF_TAGE, "pruef_tage": PRUEF_TAGE, "je_vorlauf": []}
    for L, p in pruef[pruef["art"] == "unterwegs"].groupby("vorlauf"):
        prog = np.array([(_zelle(tab, L, an, la) or {}).get("erwartet", am)
                         for an, la, am in zip(p["anzeige"], p["lage"], p["anzeige_min"])])
        y, app = p["y"].clip(lower=0).to_numpy(), p["anzeige_min"].to_numpy()
        gross = y >= GROSS_AB_MIN

        def warnung(w):
            return {"erkannt": round(float((w & gross).sum() / max(gross.sum(), 1)), 3),
                    "berechtigt": round(float((w & gross).sum() / max(w.sum(), 1)), 3)}

        erg["je_vorlauf"].append({
            "vorlauf": int(L), "n": int(len(p)), "abw_app_min": round(float(np.abs(y - app).mean()), 2),
            "abw_prognose_min": round(float(np.abs(y - prog).mean()), 2),
            "warnung_app": warnung(app >= WARN_AB_MIN), "warnung_prognose": warnung(prog >= WARN_AB_MIN)})
    return erg


def _zelle(tab: dict, L: int, an: str, la: str) -> dict | None:
    for schl in (lg.zelle("unterwegs", L, an, la), lg.zelle("unterwegs", L, an, "*"),
                 lg.zelle("unterwegs", L, "*", la), lg.zelle("unterwegs", L, "*", "*")):
        z = tab["zellen"].get(schl)
        if z and z["n"] >= lg.MIN_FAELLE:
            return z
    return None


def bewertung_lesen() -> dict | None:
    """Kurzfassung der täglichen Bewertung (auswertung/prognose_bewertung.py) für die Live-Seite."""
    pfad = WURZEL / "ergebnisse" / "prognose_bewertung.csv"
    if not pfad.exists():
        return None
    import prognose_bewertung as pb  # noqa: PLC0415

    return pb.zusammenfassung(pd.read_csv(pfad, dtype={"datum": str}))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--endstand", required=True)
    parser.add_argument("--ausgabe", default=str(WURZEL / "ergebnisse" / "prognose_tabelle.json"))
    args = parser.parse_args()
    dateien = sorted(Path(args.endstand).rglob("*.parquet"))
    teile = [faelle(pd.read_parquet(d)) for d in dateien]
    teile = [t for t in teile if len(t)]
    if not teile:
        print("Noch keine Endstände mit Prognose-Merkmalen (Version 3).")
        return 0
    f = pd.concat(teile, ignore_index=True)
    daten = {
        "erzeugt_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tage": int(f["datum"].nunique()), "erster_tag": f["datum"].min(), "letzter_tag": f["datum"].max(),
        "abfahrten": int(len(f) / len(lg.VORLAEUFE_MIN)),
        "regeln": {"vorlaeufe_min": lg.VORLAEUFE_MIN, "lage_fenster_min": lg.LAGE_FENSTER_MIN,
                   "anzeige_klassen": [k[0] for k in lg.ANZEIGE_KLASSEN], "lage_klassen": [k[0] for k in lg.LAGE_KLASSEN],
                   "min_faelle": lg.MIN_FAELLE},
        "guete": guete(f),
        "bewertung": bewertung_lesen(),
        "zellen": zellen_berechnen(f),
    }
    aus = Path(args.ausgabe)
    aus.parent.mkdir(parents=True, exist_ok=True)
    aus.write_text(json.dumps(daten, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Prognose-Tabelle: {daten['tage']} Tage, {daten['abfahrten']} Abfahrten, {len(daten['zellen'])} Zellen")
    return 0


if __name__ == "__main__":
    sys.exit(main())
