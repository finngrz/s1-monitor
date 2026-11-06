"""Wie gut lag die Live-Prognose? Tägliche Bewertung gegen die tatsächlichen Verspätungen.

Für jeden Tag mit Endstand-Tabelle (Version ≥ 3) wird nachgerechnet, was die Live-Seite zu jedem Vorlauf
(0, 5, 10, 15, 20, 30 Minuten vor Abfahrt) erwartet hätte, und zwar mit der Prognose-Tabelle, die an
diesem Tag galt (aus dem Git-Verlauf). Die Tabelle eines Tages wird immer nur aus früheren Tagen
berechnet, die Bewertung ist also ehrlich (kein Tag bewertet sich selbst). Dieselben Regeln wie live
(sammler/lage.py): gröbere Zelle bei weniger als 30 Fällen, Erwartung nie unter der App-Anzeige.

Kennzahlen je Tag, Art des Halts und Vorlauf, jeweils für die App-Anzeige und für unsere Erwartung:
- abw_*: mittlere absolute Abweichung in Minuten
- bias_*: mittlere Abweichung (tatsächlich − angezeigt/erwartet); positiv = zu optimistisch
- treffer2_*: Anteil mit höchstens 2 Minuten Abweichung
- in_spanne: Anteil innerhalb der angezeigten Spanne „meist X–Y“ (Soll: etwa 60 %)
- warn_*: Warnungen (ab 6 Minuten erwartet/angezeigt): erkannt = Anteil der tatsächlich ≥ 6 min
  verspäteten Züge mit Warnung, berechtigt = Anteil der Warnungen, die sich bestätigt haben

Ergebnis: ergebnisse/prognose_bewertung.csv (nur Zählungen und Mittelwerte, keine Einzelfahrten).

Aufruf:  python auswertung/prognose_bewertung.py --endstand <Ordner>
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

WURZEL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WURZEL / "sammler"))
sys.path.insert(0, str(WURZEL / "auswertung"))
import lage as lg  # noqa: E402
import prognose_tabelle as pt  # noqa: E402

TABELLE = "ergebnisse/prognose_tabelle.json"
WARN_AB_MIN = 6


def tabelle_fuer_tag(tag: str) -> dict | None:
    """Prognose-Tabelle, die am Tag galt: letzter Stand im Git-Verlauf bis 12 Uhr UTC dieses Tages."""
    try:
        sha = subprocess.run(["git", "log", "-1", f"--before={tag}T12:00:00Z", "--format=%H", "--", TABELLE],
                             cwd=WURZEL, capture_output=True, text=True, check=True).stdout.strip()
        if not sha:
            return None
        inhalt = subprocess.run(["git", "show", f"{sha}:{TABELLE}"], cwd=WURZEL, capture_output=True, text=True,
                                check=True).stdout
        return json.loads(inhalt)
    except (subprocess.CalledProcessError, ValueError):
        return None


def zelle(tab: dict, art: str, vorlauf: int, anzeige: str, lage: str) -> dict | None:
    for schl in (lg.zelle(art, vorlauf, anzeige, lage), lg.zelle(art, vorlauf, anzeige, "*"),
                 lg.zelle(art, vorlauf, "*", lage), lg.zelle(art, vorlauf, "*", "*")):
        z = tab["zellen"].get(schl)
        if z and z["n"] >= lg.MIN_FAELLE:
            return z
    return None


def vorhersagen(f: pd.DataFrame, tab: dict) -> pd.DataFrame:
    cache: dict = {}
    erw, von, bis = [], [], []
    for a, v, an, la, am in zip(f["art"], f["vorlauf"], f["anzeige"], f["lage"], f["anzeige_min"]):
        k = (a, v, an, la)
        if k not in cache:
            cache[k] = zelle(tab, a, int(v), an, la)
        z = cache[k]
        erw.append(max(z["erwartet"], am) if z else am)
        von.append(z["von"] if z else np.nan)
        bis.append(z["bis"] if z else np.nan)
    return f.assign(erwartet=erw, von=von, bis=bis)


def kennzahlen(g: pd.DataFrame) -> dict:
    y, app, prog = g["y"].clip(lower=0).to_numpy(), g["anzeige_min"].to_numpy(), g["erwartet"].to_numpy()
    gross = y >= WARN_AB_MIN

    def warn(w):
        return (round(float((w & gross).sum() / max(gross.sum(), 1)), 3),
                round(float((w & gross).sum() / w.sum()), 3) if w.sum() else None)

    mit_spanne = g["von"].notna().to_numpy()
    wa, wp = warn(app >= WARN_AB_MIN), warn(prog >= WARN_AB_MIN)
    return {
        "n": len(g), "anteil_ab6": round(float(gross.mean()), 3),
        "abw_app": round(float(np.abs(y - app).mean()), 2), "abw_prognose": round(float(np.abs(y - prog).mean()), 2),
        "bias_app": round(float((y - app).mean()), 2), "bias_prognose": round(float((y - prog).mean()), 2),
        "treffer2_app": round(float((np.abs(y - app) <= 2).mean()), 3),
        "treffer2_prognose": round(float((np.abs(y - prog) <= 2).mean()), 3),
        "in_spanne": round(float(((y >= g["von"]) & (y <= g["bis"])).to_numpy()[mit_spanne].mean()), 3)
        if mit_spanne.any() else None,
        "warn_app_erkannt": wa[0], "warn_app_berechtigt": wa[1],
        "warn_prognose_erkannt": wp[0], "warn_prognose_berechtigt": wp[1],
    }


def bewerten(endstand: pd.DataFrame, tab: dict) -> list[dict]:
    f = pt.faelle(endstand)
    if f.empty:
        return []
    f = vorhersagen(f, tab)
    zeilen = []
    for (datum, art, vorlauf), g in f.groupby(["datum", "art", "vorlauf"]):
        zeilen.append({"datum": datum, "art": art, "vorlauf": int(vorlauf),
                       "tabelle_tage": tab.get("tage"), "tabelle_bis": tab.get("letzter_tag"), **kennzahlen(g)})
    return zeilen


def zusammenfassung(df: pd.DataFrame, tage: int = 7) -> dict | None:
    """Kurzfassung der letzten Tage für die Live-Seite (Halte unterwegs, 10 Minuten Vorlauf)."""
    d = df[(df["art"] == "unterwegs") & (df["vorlauf"] == 10)].sort_values("datum").tail(tage)
    if d.empty:
        return None
    w = d["n"] / d["n"].sum()
    mittel = lambda sp: round(float((d[sp] * w).sum()), 3)  # noqa: E731
    return {"tage": len(d), "von": d["datum"].min(), "bis": d["datum"].max(), "abfahrten": int(d["n"].sum()),
            **{sp: mittel(sp) for sp in ("abw_app", "abw_prognose", "bias_app", "bias_prognose", "treffer2_app",
                                         "treffer2_prognose", "in_spanne", "warn_app_erkannt",
                                         "warn_prognose_erkannt", "warn_prognose_berechtigt")}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--endstand", required=True)
    parser.add_argument("--ausgabe", default=str(WURZEL / "ergebnisse" / "prognose_bewertung.csv"))
    parser.add_argument("--neu", action="store_true", help="alle Tage neu bewerten")
    args = parser.parse_args()
    aus = Path(args.ausgabe)
    alt = pd.read_csv(aus, dtype={"datum": str}) if aus.exists() and not args.neu else pd.DataFrame(columns=["datum"])
    erledigt = set(alt["datum"])
    neu = []
    for datei in sorted(Path(args.endstand).rglob("*.parquet")):
        tag = datei.stem
        if tag in erledigt:
            continue
        tab = tabelle_fuer_tag(tag)
        if not tab or tab.get("letzter_tag", "9999") >= tag:
            continue  # keine Tabelle oder Tabelle kennt den Tag schon: keine ehrliche Bewertung möglich
        zeilen = bewerten(pd.read_parquet(datei), tab)
        neu += zeilen
        if zeilen:
            print(f"{tag}: bewertet mit Tabelle bis {tab.get('letzter_tag')}")
    if not neu:
        print("Nichts Neues zu bewerten.")
        return 0
    erg = pd.concat([alt, pd.DataFrame(neu)], ignore_index=True).sort_values(["datum", "art", "vorlauf"])
    aus.parent.mkdir(parents=True, exist_ok=True)
    erg.to_csv(aus, index=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
