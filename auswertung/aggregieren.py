"""Erzeugt aus der privaten Endstand-Tabelle die aggregierten Kennzahlen für das Dashboard.

Veröffentlicht werden ausschließlich Zählungen (Histogramme der Verspätung in Minuten), keine einzelnen
Fahrten und keine Rohdaten der Schnittstelle. So kann das Dashboard jede Pünktlichkeitsschwelle selbst
berechnen, ohne dass Einzeldaten öffentlich werden.

Eingaben:  --endstand <Ordner mit endstand/JJJJ-MM/*.parquet>  (privat)
           --fahrplan ergebnisse/fahrplan   (öffentlicher Soll-Fahrplan, für die Fahrzeit nach Poppenbüttel)
           --qualitaet ergebnisse/datenqualitaet.csv
Ausgabe:   --ausgabe dashboard/daten   ->  kennzahlen.json
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
from speicher import BERLIN  # noqa: E402

# Verspätungs-Klassen: 0..15 Minuten einzeln, dann 16–20, 21–30, über 30
KLASSEN = list(range(16)) + ["16-20", "21-30", ">30"]
# Tagesabschnitte für Filter (Stunde von, bis ausschließlich)
ABSCHNITTE = {"nachts": (0, 4), "frueh": (4, 6), "morgens": (6, 9), "tagsueber": (9, 16),
              "abends": (16, 19), "spaetabends": (19, 24)}
STARTBAHNHOEFE = {"Wedel", "Poppenbüttel", "Hamburg Airport (Flughafen)"}  # vor der Abfahrt immer „pünktlich“
D1_KORREKTUR_AB = "2026-10-27T10:35:00Z"  # ab hier: Verspätung inkl. Korrekturen nach der Abfahrt (Rückblick)
MIN_UMSTIEG_S = 180


def stationsreihenfolge() -> list[str]:
    cfg = json.loads((WURZEL / "sammler" / "konfig.json").read_text(encoding="utf-8"))
    return [s["name"] for s in cfg["stationen"]]


def klasse(minuten: pd.Series) -> pd.Series:
    m = minuten.clip(lower=0)
    k = m.where(m <= 15)
    k = k.mask(m.between(16, 20, inclusive="both"), 16).mask(m.between(21, 30, inclusive="both"), 17).mask(m > 30, 18)
    return k


def abschnitt(stunde: pd.Series) -> pd.Series:
    erg = pd.Series(index=stunde.index, dtype=object)
    for name, (a, b) in ABSCHNITTE.items():
        erg[(stunde >= a) & (stunde < b)] = name
    return erg


def histogramme(df: pd.DataFrame, schluessel: list[str]) -> dict:
    """Je Gruppe: Anzahl je Verspätungsklasse, Ausfälle, unbekannt. Spaltenweise für kleine Dateien."""
    d = df.copy()
    d["k"] = klasse(d["verspaetung_s"] / 60)
    d.loc[d["ausfall"], "k"] = -1  # Ausfall
    d.loc[d["k"].isna() & ~d["ausfall"], "k"] = -2  # keine Echtzeit
    z = d.groupby(schluessel + ["k"]).size().unstack("k", fill_value=0)
    for k in [-2, -1] + list(range(len(KLASSEN))):
        if k not in z.columns:
            z[k] = 0
    z = z[[*range(len(KLASSEN)), -1, -2]].reset_index()
    out = {s: z[s].astype(str).tolist() if z[s].dtype == object else z[s].tolist() for s in schluessel}
    out["hist"] = z[[*range(len(KLASSEN))]].to_numpy().tolist()
    out["ausfall"] = z[-1].tolist()
    out["unbekannt"] = z[-2].tolist()
    return out


def richtung_bestimmen(s: pd.DataFrame, reihenfolge: list[str]) -> pd.Series:
    idx = {n: i for i, n in enumerate(reihenfolge)}
    ziel = s["richtung"].fillna("").str.split(" / ").str[0].map(idx)
    hier = s["abfrage_station"].map(idx)
    r = pd.Series(np.where(ziel < hier, "west", np.where(ziel > hier, "ost", None)), index=s.index)
    return r


def vorbereiten(endstand: pd.DataFrame, reihenfolge: list[str]) -> pd.DataFrame:
    s = endstand[(endstand["linie"] == "S1") & (endstand["verkehrsmittel"] == "TRAIN")].copy()
    s["soll_utc"] = pd.to_datetime(s["soll_utc"], utc=True)
    lokal = s["soll_utc"].dt.tz_convert(BERLIN)
    s["datum"] = lokal.dt.strftime("%Y-%m-%d")
    s["stunde"] = lokal.dt.hour
    s["abschnitt"] = abschnitt(s["stunde"])
    s["richtung_gruppe"] = richtung_bestimmen(s, reihenfolge)
    s = s[s["richtung_gruppe"].notna()]
    s["ausfall"] = s["ausfall"].fillna(False).astype(bool)
    # Geflügelte Züge (Flughafen- und Poppenbüttel-Teil) stehen doppelt im Datenstrom: einmal zählen
    s = s.sort_values(["soll_utc", "abfrage_station"]).drop_duplicates(["abfrage_station", "soll_utc", "gleis", "richtung_gruppe"])
    return s


def abschnitte_entlang(s: pd.DataFrame, reihenfolge: list[str]) -> dict:
    """Verspätungszuwachs zwischen aufeinanderfolgenden Halten derselben Fahrt."""
    f = s[~s["ausfall"] & s["verspaetung_s"].notna() & s["service_id"].notna()].copy()
    f = f.sort_values(["service_id", "datum", "soll_utc"])
    g = f.groupby(["service_id", "datum", "richtung_gruppe"], sort=False)
    f["von"] = g["abfrage_station"].shift()
    f["delta_s"] = f["verspaetung_s"] - g["verspaetung_s"].shift()
    nachbarn = {frozenset(p) for p in zip(reihenfolge[:-2], reihenfolge[1:-1])}  # Wedel ... Poppenbüttel
    nachbarn.add(frozenset(("Ohlsdorf", "Hamburg Airport (Flughafen)")))  # Flughafen-Ast
    f = f[f["von"].notna()]
    f = f[[frozenset((v, n)) in nachbarn for v, n in zip(f["von"], f["abfrage_station"])]]  # nur benachbarte Halte
    f = f[~f["von"].isin(STARTBAHNHOEFE)]  # ab Startbahnhof ist die Ausgangsverspätung immer 0 (Messartefakt)
    z = (f.groupby(["von", "abfrage_station", "richtung_gruppe", "datum", "abschnitt"])
         .agg(n=("delta_s", "size"), summe_s=("delta_s", "sum"), zuwachs_s=("delta_s", lambda x: x.clip(lower=0).sum()))
         .reset_index().rename(columns={"abfrage_station": "nach"}))
    return {c: z[c].tolist() for c in z.columns}


def prognose_vs_realitaet(s: pd.DataFrame) -> dict:
    f = s[(s["soll_utc"] >= pd.Timestamp(D1_KORREKTUR_AB)) & ~s["ausfall"] & s["verspaetung_s"].notna()
          & s["prognose_vor_abfahrt_s"].notna() & ~s["abfrage_station"].isin(STARTBAHNHOEFE)].copy()
    f["diff_s"] = f["verspaetung_s"] - f["prognose_vor_abfahrt_s"]
    z = (f.groupby(["datum", "richtung_gruppe", "abschnitt"])
         .agg(n=("diff_s", "size"), summe_diff_s=("diff_s", "sum"), n_hoeher=("diff_s", lambda x: int((x > 0).sum())),
              n_hoeher_2=("diff_s", lambda x: int((x >= 120).sum())))
         .reset_index())
    return {c: z[c].tolist() for c in z.columns}


def lauf_wellingsbuettel(fahrplan: Path) -> dict:
    """Soll-Fahrzeit Wellingsbüttel -> Poppenbüttel je (Datum, Soll-Abfahrt in Sekunden)."""
    try:
        h = pd.read_parquet(fahrplan / "s1_halte.parquet")
        v = pd.read_parquet(fahrplan / "verkehrstage.parquet")
    except FileNotFoundError:
        return {}
    w = h[h["halt"] == "Wellingsbüttel"][["trip_id", "service_id", "ab_s"]].merge(
        h[h["halt"] == "Poppenbüttel"][["trip_id", "an_s"]], on="trip_id")
    w["lauf"] = w["an_s"] - w["ab_s"]
    w = w.merge(v, on="service_id")
    w["datum"] = pd.to_datetime(w["datum"]).dt.strftime("%Y-%m-%d")
    return {(d, a): l for d, a, l in zip(w["datum"], w["ab_s"], w["lauf"])}


def pendelweg(endstand: pd.DataFrame, s: pd.DataFrame, fahrplan: Path) -> dict:
    """Ankunft in Poppenbüttel (über Wellingsbüttel geschätzt) und reale Wartezeit auf den 176er."""
    w = s[(s["abfrage_station"] == "Wellingsbüttel") & (s["richtung_gruppe"] == "ost") & ~s["ausfall"]
          & s["verspaetung_s"].notna()].copy()
    lauf = lauf_wellingsbuettel(fahrplan)
    lokal = w["soll_utc"].dt.tz_convert(BERLIN)
    sek = lokal.dt.hour * 3600 + lokal.dt.minute * 60
    w["lauf_s"] = [lauf.get((d, x), 150) for d, x in zip(w["datum"], sek)]
    w["an_ist"] = w["soll_utc"] + pd.to_timedelta(w["verspaetung_s"] + w["lauf_s"], unit="s")
    ankunft = histogramme(w, ["datum", "stunde"])

    b = endstand[(endstand["abfrage_station"] == "Poppenbüttel") & (endstand["linie"] == "176")].copy()
    warte = {"datum": [], "stunde": [], "hist": []}
    if len(b) and len(w):
        b["soll_utc"] = pd.to_datetime(b["soll_utc"], utc=True)
        b = b[~b["ausfall"].fillna(False).astype(bool)]
        b["ist"] = b["soll_utc"] + pd.to_timedelta(b["verspaetung_s"].fillna(0), unit="s")
        sek = lambda x: (x.dt.tz_convert("UTC").dt.tz_localize(None) - pd.Timestamp("1970-01-01")).dt.total_seconds().to_numpy()  # noqa: E731
        abf = np.sort(np.unique(sek(b["ist"])))
        an = sek(w["an_ist"])
        i = np.searchsorted(abf, an + MIN_UMSTIEG_S)
        ok = i < len(abf)
        wart = np.full(len(an), np.nan)
        wart[ok] = (abf[i[ok]] - an[ok]) / 60
        w["warte_min"] = wart
        w = w[w["warte_min"].notna() & (w["warte_min"] <= 60)]  # Betriebspausen ausklammern
        w["wk"] = w["warte_min"].round().clip(upper=30).astype(int)
        z = w.groupby(["datum", "stunde", "wk"]).size().unstack("wk", fill_value=0)
        z = z.reindex(columns=range(31), fill_value=0).reset_index()
        warte = {"datum": z["datum"].tolist(), "stunde": z["stunde"].tolist(), "hist": z[list(range(31))].to_numpy().tolist()}
    return {"ankunft_poppenbuettel": ankunft, "warten_176": warte}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--endstand", required=True)
    parser.add_argument("--fahrplan", default=str(WURZEL / "ergebnisse" / "fahrplan"))
    parser.add_argument("--qualitaet", default=str(WURZEL / "ergebnisse" / "datenqualitaet.csv"))
    parser.add_argument("--ausgabe", default=str(WURZEL / "dashboard" / "daten"))
    args = parser.parse_args()

    dateien = sorted(Path(args.endstand).rglob("*.parquet"))
    if not dateien:
        print("Keine Endstand-Dateien gefunden.")
        return 1
    endstand = pd.concat([pd.read_parquet(d) for d in dateien], ignore_index=True)
    reihenfolge = stationsreihenfolge()
    s = vorbereiten(endstand, reihenfolge)
    puenktlich_basis = s[~s["abfrage_station"].isin(STARTBAHNHOEFE)]

    qualitaet = []
    if Path(args.qualitaet).exists():
        q = pd.read_csv(args.qualitaet).fillna("")
        qualitaet = q.to_dict(orient="records")

    daten = {
        "meta": {
            "erzeugt_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "erster_tag": s["datum"].min(), "letzter_tag": s["datum"].max(), "tage": int(s["datum"].nunique()),
            "abfahrten": int(len(puenktlich_basis)), "klassen": [str(k) for k in KLASSEN],
            "abschnitte": {k: list(v) for k, v in ABSCHNITTE.items()},
            "stationen": [n for n in reihenfolge],
            "startbahnhoefe": sorted(STARTBAHNHOEFE), "d1_korrektur_ab": D1_KORREKTUR_AB,
            "quelle": "Echtzeitdaten: Geofox-Schnittstelle der Hamburger Hochbahn AG; Soll-Fahrplan: hvv (Transparenzportal Hamburg)",
        },
        "tag_stunde": histogramme(puenktlich_basis, ["datum", "stunde", "richtung_gruppe"]),
        "station": histogramme(s, ["abfrage_station", "datum", "abschnitt", "richtung_gruppe"]),
        "abschnitte": abschnitte_entlang(s, reihenfolge),
        "prognose": prognose_vs_realitaet(s),
        "pendel": pendelweg(endstand, s, Path(args.fahrplan)),
        "qualitaet": qualitaet,
    }
    aus = Path(args.ausgabe)
    aus.mkdir(parents=True, exist_ok=True)
    (aus / "kennzahlen.json").write_text(json.dumps(daten, ensure_ascii=False, separators=(",", ":"), default=str),
                                         encoding="utf-8")
    print(f"kennzahlen.json: {daten['meta']['tage']} Tage, {daten['meta']['abfahrten']} S1-Abfahrten, "
          f"{(aus / 'kennzahlen.json').stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
