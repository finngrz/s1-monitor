"""Stufe 1: Fahrplan-Analysen (nur Soll-Fahrplan, noch ohne Echtzeit).

Beantwortet aus dem aufbereiteten Fahrplan (ergebnisse/fahrplan/):

- F1  Warum dauert Stadthausbrücke -> Poppenbüttel laut Plan unterschiedlich lange?
- F2  Planmäßige Fahr- und Haltezeiten je Abschnitt (Grundlage für die spätere Puffer-Analyse)
- F7  Wie gut sind die Busse an Ohlsdorf, Barmbek und Berliner Tor laut Plan auf die S1 abgestimmt?
      Inklusive Verschiebungs-Vorschau: Was brächte es, eine Buslinie um ein paar Minuten zu verschieben?

Ausgabe:
- ergebnisse/analysen/*.csv und *.png
- analysen/ERGEBNISSE_STUFE1.md (lesbarer Bericht, wird bei jedem Lauf neu erzeugt)

Aufruf:  python analysen/stufe1.py
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

WURZEL = Path(__file__).resolve().parent.parent
FAHRPLAN = WURZEL / "ergebnisse" / "fahrplan"
AUSGABE = WURZEL / "ergebnisse" / "analysen"
BERICHT = WURZEL / "analysen" / "ERGEBNISSE_STUFE1.md"

MIN_UMSTIEG_S = 180  # Mindest-Umsteigezeit laut Methodik (D7 / Standardansicht)
GUT_S = 6 * 60  # guter Anschluss: höchstens 6 Minuten zwischen Ankunft und Abfahrt
SCHLECHT_S = 12 * 60  # schlechter Anschluss: mehr als 12 Minuten
MAX_LUECKE_S = 60 * 60  # längere Lücken zwischen Abfahrten gelten als Betriebspause
TAG_VON_S, TAG_BIS_S = 6 * 3600, 20 * 3600  # Tagesverkehr für die Anschluss-Analyse
MAX_TAKT_RANG_MIN = 20  # Rangliste nur für Linien, die mindestens alle 20 Minuten fahren
START, ZIEL = "Stadthausbrücke", "Poppenbüttel"
WOCHENTAGE_DE = {"Monday": "Montag", "Tuesday": "Dienstag", "Wednesday": "Mittwoch", "Thursday": "Donnerstag",
                 "Friday": "Freitag", "Saturday": "Samstag", "Sunday": "Sonntag"}
RICHTUNG_S1 = {0: "Richtung Poppenbüttel/Flughafen", 1: "Richtung Innenstadt/Wedel"}

# Farben (validierte Standardpalette, Slot 1 und 2) und Textfarben
BLAU, ORANGE, TINTE, LEISE, RASTER = "#2a78d6", "#eb6834", "#0b0b0b", "#52514e", "#e4e3df"


# --------------------------------------------------------------------------- Hilfen
def uhrzeit(sekunden: float) -> str:
    s = int(round(sekunden))
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}"


def minuten(sekunden: float) -> str:
    return f"{sekunden / 60:.1f}".replace(".", ",")


def prozent(anteil: float) -> str:
    return f"{anteil * 100:.0f} %"


def datum_de(d: date) -> str:
    return f"{WOCHENTAGE_DE[d.strftime('%A')]}, {d.strftime('%d.%m.%Y')}"


def stil(ax) -> None:
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color(LEISE)
    ax.tick_params(colors=LEISE, labelsize=9)
    ax.grid(axis="y", color=RASTER, linewidth=0.8)
    ax.set_axisbelow(True)


def md_tabelle(df: pd.DataFrame) -> str:
    kopf = "| " + " | ".join(df.columns) + " |"
    trenner = "| " + " | ".join("---" for _ in df.columns) + " |"
    zeilen = ["| " + " | ".join(str(v) for v in zeile) + " |" for zeile in df.itertuples(index=False)]
    return "\n".join([kopf, trenner, *zeilen])


# --------------------------------------------------------------------------- Daten
def lade():
    con = duckdb.connect()
    halte = con.execute(f"select * from '{(FAHRPLAN / 's1_halte.parquet').as_posix()}'").df()
    bus = con.execute(f"select * from '{(FAHRPLAN / 'knoten_bus.parquet').as_posix()}'").df()
    tage = con.execute(f"select * from '{(FAHRPLAN / 'verkehrstage.parquet').as_posix()}'").df()
    quelle = json.loads((FAHRPLAN / "quelle.json").read_text(encoding="utf-8"))
    tage["datum"] = pd.to_datetime(tage["datum"]).dt.date
    return halte, bus, tage, quelle


def stichtage(halte: pd.DataFrame, bus: pd.DataFrame, tage: pd.DataFrame, heute: date) -> dict[str, date]:
    """Wählt je Tagestyp einen typischen Tag: den mit dem häufigsten Fahrtenumfang (S1 und Busse).

    So landen wir nicht zufällig auf einem Feiertag, Ferientag oder Baustellentag.
    """
    fahrten_s1 = halte[["trip_id", "service_id"]].drop_duplicates().merge(tage, on="service_id")
    fahrten_bus = bus[["trip_id", "service_id"]].drop_duplicates().merge(tage, on="service_id")
    umfang = (
        fahrten_s1.groupby("datum").trip_id.nunique().rename("s1").to_frame()
        .join(fahrten_bus.groupby("datum").trip_id.nunique().rename("bus"))
        .fillna(0).reset_index()
    )
    umfang["wochentag"] = [d.strftime("%A") for d in umfang.datum]
    zukunft = umfang[umfang.datum >= heute]
    basis = zukunft if len(zukunft) >= 21 else umfang
    auswahl = {}
    for typ, tag in [("Werktag", "Tuesday"), ("Samstag", "Saturday"), ("Sonntag", "Sunday")]:
        kandidaten = basis[basis.wochentag == tag]
        if kandidaten.empty:
            continue
        haeufigster = kandidaten.groupby(["s1", "bus"]).size().idxmax()
        treffer = kandidaten[(kandidaten.s1 == haeufigster[0]) & (kandidaten.bus == haeufigster[1])]
        auswahl[typ] = treffer.datum.min()
    return auswahl


def am_tag(df: pd.DataFrame, tage: pd.DataFrame, tag: date) -> pd.DataFrame:
    dienste = set(tage.loc[tage.datum == tag, "service_id"])
    return df[df.service_id.isin(dienste)].copy()


# --------------------------------------------------------------------------- F1 / F2
def f1_fahrten(halte_tag: pd.DataFrame, von: str, nach: str) -> pd.DataFrame:
    a = halte_tag[halte_tag.halt == von][["trip_id", "ziel", "folge", "ab_s"]]
    b = halte_tag[halte_tag.halt == nach][["trip_id", "folge", "an_s"]]
    f = a.merge(b, on="trip_id", suffixes=("_von", "_nach"))
    f = f[f.folge_nach > f.folge_von].copy()
    f["dauer_min"] = (f.an_s - f.ab_s) / 60
    oh = halte_tag[halte_tag.halt == "Ohlsdorf"][["trip_id", "an_s", "ab_s"]].rename(
        columns={"an_s": "oh_an", "ab_s": "oh_ab"})
    f = f.merge(oh, on="trip_id", how="left")
    f["halt_ohlsdorf_min"] = (f.oh_ab - f.oh_an) / 60
    return f.sort_values("ab_s").reset_index(drop=True)


def abschnitte(halte_tag: pd.DataFrame, fahrten: pd.DataFrame) -> pd.DataFrame:
    """Fahr- und Haltezeit je Halt für die Fahrten aus F1, getrennt nach Gesamtdauer."""
    h = halte_tag[halte_tag.trip_id.isin(fahrten.trip_id)].sort_values(["trip_id", "folge"]).copy()
    h = h.merge(fahrten[["trip_id", "dauer_min", "folge_von", "folge_nach"]], on="trip_id")
    h = h[(h.folge >= h.folge_von) & (h.folge <= h.folge_nach)]
    h["fahrt_s"] = h.an_s - h.groupby("trip_id").ab_s.shift()
    h["halt_s"] = np.where(h.folge.between(h.folge_von + 1, h.folge_nach - 1), h.ab_s - h.an_s, np.nan)
    reihenfolge = h.groupby("halt").folge.min().sort_values().index
    t = h.pivot_table(index="halt", columns="dauer_min", values=["fahrt_s", "halt_s"], aggfunc="mean")
    return t.reindex(reihenfolge)


def grafik_f1(f: pd.DataFrame, tag: date, pfad: Path) -> None:
    kurz, lang = f.dauer_min.min(), f.dauer_min.max()
    fig, ax = plt.subplots(figsize=(9, 3.6), dpi=150)
    for wert, farbe, name in [(lang, BLAU, f"{lang:.0f} Minuten"), (kurz, ORANGE, f"{kurz:.0f} Minuten")]:
        teil = f[f.dauer_min == wert]
        ax.scatter(teil.ab_s / 3600, teil.dauer_min, s=22, color=farbe, label=name, zorder=3,
                   edgecolors="white", linewidths=0.6)
    andere = f[~f.dauer_min.isin([kurz, lang])]
    if len(andere):
        ax.scatter(andere.ab_s / 3600, andere.dauer_min, s=22, color=LEISE, label="andere", zorder=3)
    ax.set_xlim(4, 25)
    ax.set_xticks(range(4, 26, 2))
    ax.set_xticklabels([f"{h % 24:02d}:00" for h in range(4, 26, 2)])
    ax.set_ylim(min(30, kurz - 2), max(38, lang + 2))
    ax.set_ylabel("Fahrzeit laut Plan (min)", color=LEISE, fontsize=9)
    ax.set_xlabel("Abfahrt Stadthausbrücke", color=LEISE, fontsize=9)
    ax.set_title(f"S1 Stadthausbrücke → Poppenbüttel: Fahrzeit je Fahrt, {datum_de(tag)}",
                 loc="left", fontsize=11, color=TINTE)
    stil(ax)
    ax.legend(frameon=False, fontsize=9, loc="upper right", ncol=3)
    fig.tight_layout()
    fig.savefig(pfad)
    plt.close(fig)


# --------------------------------------------------------------------------- F7
def naechste(abfahrten: np.ndarray, ankunft: np.ndarray, min_s: int) -> np.ndarray:
    """Für jede Ankunft: Zeit bis zur nächsten Abfahrt, die mindestens min_s später liegt.

    NaN, wenn es keine solche Abfahrt gibt oder die Ankunft in eine Betriebspause der
    Anschlusslinie fällt (Lücke zwischen zwei Abfahrten über 60 Minuten, z. B. nachts).
    """
    abfahrten = np.sort(abfahrten)
    idx = np.searchsorted(abfahrten, ankunft + min_s, side="left")
    ergebnis = np.full(len(ankunft), np.nan)
    ok = idx < len(abfahrten)
    luecke = np.full(len(ankunft), np.inf)
    mit_vorgaenger = ok & (idx > 0)
    luecke[mit_vorgaenger] = abfahrten[idx[mit_vorgaenger]] - abfahrten[idx[mit_vorgaenger] - 1]
    erste_abfahrt = ok & (idx == 0)  # vor der ersten Abfahrt des Tages: Lücke = Wartezeit selbst
    luecke[erste_abfahrt] = abfahrten[0] - ankunft[erste_abfahrt]
    ok &= luecke <= MAX_LUECKE_S
    ergebnis[ok] = abfahrten[idx[ok]] - ankunft[ok]
    return ergebnis


def tagsueber(zeiten: np.ndarray) -> np.ndarray:
    """Nur Ankünfte im Tagesverkehr (06:00–20:00), auf den sich die Analyse konzentriert."""
    return zeiten[(zeiten >= TAG_VON_S) & (zeiten < TAG_BIS_S)]


def kennzahlen(umstieg: np.ndarray) -> dict:
    u = umstieg[~np.isnan(umstieg)]
    if len(u) == 0:
        return {"n": 0, "mittel_s": np.nan, "median_s": np.nan, "gut": np.nan, "schlecht": np.nan}
    return {"n": len(u), "mittel_s": u.mean(), "median_s": np.median(u),
            "gut": (u <= GUT_S).mean(), "schlecht": (u > SCHLECHT_S).mean()}


def takt_s(zeiten: np.ndarray) -> float:
    d = np.diff(np.sort(np.unique(zeiten)))
    return float(np.median(d)) if len(d) else np.nan


def verschiebung(abfahrten: np.ndarray, ankuenfte: np.ndarray) -> pd.DataFrame:
    """Mittlere Umsteigezeit, wenn alle Abfahrten der Zielseite um k Minuten verschoben würden."""
    zeilen = []
    for k in range(-10, 11):
        z = kennzahlen(naechste(abfahrten + k * 60, ankuenfte, MIN_UMSTIEG_S))
        zeilen.append({"verschiebung_min": k, **z})
    return pd.DataFrame(zeilen)


def f7_anschluesse(halte_tag: pd.DataFrame, bus_tag: pd.DataFrame, knoten_cfg: dict) -> pd.DataFrame:
    zeilen = []
    erste = halte_tag.groupby("trip_id").folge.transform("min")
    letzte = halte_tag.groupby("trip_id").folge.transform("max")
    for knoten, k in knoten_cfg.items():
        s1 = halte_tag[(halte_tag.halt == k["bahnsteig_name"])]
        # Geflügelte Züge (Flughafen- und Poppenbüttel-Teil) stehen als zwei Fahrten im Plan, sind aber ein Zug:
        # gleiche Richtung und gleiche Zeit am Knoten werden deshalb nur einmal gezählt.
        s1_an = s1[s1.folge > erste[s1.index]].drop_duplicates(["richtung", "an_s"])  # hier kann man aussteigen
        s1_ab = s1[s1.folge < letzte[s1.index]].drop_duplicates(["richtung", "ab_s"])  # hier kann man einsteigen
        b = bus_tag[bus_tag.knoten == knoten]
        for (linie, richtung), gruppe in b.groupby(["linie", "richtung"]):
            # Hält ein Bus an mehreren Haltestellen desselben Knotens, zählt je Fahrt nur der erste Halt
            # (früheste Einstiegs- bzw. Ausstiegsmöglichkeit), sonst würde dieselbe Fahrt doppelt gezählt.
            ab = gruppe[(~gruppe.ist_ende) & (gruppe.einstieg_typ != 1)].sort_values("folge").drop_duplicates("trip_id")
            an = gruppe[(~gruppe.ist_start) & (gruppe.ausstieg_typ != 1)].sort_values("folge").drop_duplicates("trip_id")
            ziel_ab = ab.ziel.mode().iat[0] if len(ab) else ""
            herkunft = an.ziel.mode().iat[0] if len(an) else ""
            for s1_richtung in (0, 1):
                # S1 -> Bus
                ankunft = s1_an[s1_an.richtung == s1_richtung].an_s.to_numpy(float)
                if len(ab) >= 4 and len(ankunft):
                    abf = ab.ab_s.to_numpy(float)
                    ankunft = tagsueber(ankunft)
                    if len(ankunft):
                        z = kennzahlen(naechste(abf, ankunft, MIN_UMSTIEG_S))
                        if z["n"] == 0:  # Linie fährt tagsüber nicht (z. B. Nachtbus)
                            continue
                        v = verschiebung(abf, ankunft)
                        best = v.loc[v.mittel_s.idxmin()]
                        zufall = v[v.verschiebung_min < 10].mittel_s.mean()
                        zeilen.append({"knoten": knoten, "umstieg": "S1 → Bus", "linie": linie, "richtung_bus": richtung,
                                       "bus_ziel": ziel_ab, "s1_richtung": RICHTUNG_S1[s1_richtung],
                                       "bus_takt_min": takt_s(abf) / 60, **z,
                                       "beste_verschiebung_min": int(best.verschiebung_min),
                                       "mittel_s_bei_bester": best.mittel_s, "gut_bei_bester": best.gut,
                                       "mittel_s_zufaellig": zufall})
                # Bus -> S1
                abfahrt_s1 = s1_ab[s1_ab.richtung == s1_richtung].ab_s.to_numpy(float)
                if len(an) >= 4 and len(abfahrt_s1):
                    ank = tagsueber(an.an_s.to_numpy(float))
                    if len(ank):
                        z = kennzahlen(naechste(abfahrt_s1, ank, MIN_UMSTIEG_S))
                        zeilen.append({"knoten": knoten, "umstieg": "Bus → S1", "linie": linie, "richtung_bus": richtung,
                                       "bus_ziel": herkunft, "s1_richtung": RICHTUNG_S1[s1_richtung],
                                       "bus_takt_min": takt_s(an.an_s.to_numpy(float)) / 60, **z})
    return pd.DataFrame(zeilen)


def grafik_179(abfahrten: np.ndarray, ankuenfte: np.ndarray, tag: date, pfad: Path) -> pd.DataFrame:
    umstieg = naechste(abfahrten, ankuenfte, MIN_UMSTIEG_S) / 60
    v = verschiebung(abfahrten, ankuenfte)
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(9, 3.6), dpi=150, gridspec_kw={"width_ratios": [1.3, 1]})
    u = umstieg[~np.isnan(umstieg)]
    grenzen = np.arange(3, max(24, np.ceil(u.max()) + 2), 1) - 0.5
    a1.hist(u, bins=grenzen, color=BLAU, edgecolor="white", linewidth=1)
    a1.axvspan(3, GUT_S / 60, color=ORANGE, alpha=0.10, lw=0)
    a1.text(GUT_S / 60 + 0.4, a1.get_ylim()[1] * 0.97, "guter Anschluss (≤ 6 min)", ha="left", va="top",
            fontsize=8, color=LEISE)
    a1.set_xlabel("Minuten von Ankunft S1 bis Abfahrt 179", color=LEISE, fontsize=9)
    a1.set_ylabel("Anzahl S1-Ankünfte", color=LEISE, fontsize=9)
    a1.set_title("Umsteigezeit heute (Plan)", loc="left", fontsize=10, color=TINTE)
    stil(a1)
    a2.plot(v.verschiebung_min, v.mittel_s / 60, color=BLAU, lw=2)
    jetzt = v[v.verschiebung_min == 0].iloc[0]
    best = v.loc[v.mittel_s.idxmin()]
    a2.scatter([0], [jetzt.mittel_s / 60], s=36, color=BLAU, zorder=3, edgecolors="white", linewidths=1)
    a2.scatter([best.verschiebung_min], [best.mittel_s / 60], s=40, color=ORANGE, zorder=3,
               edgecolors="white", linewidths=1)
    a2.annotate("heute", (0, jetzt.mittel_s / 60), xytext=(4, 6), textcoords="offset points", fontsize=8, color=LEISE)
    a2.annotate(f"bestes: {int(best.verschiebung_min):+d} min", (best.verschiebung_min, best.mittel_s / 60),
                xytext=(-8, -12), textcoords="offset points", ha="right", fontsize=8, color=LEISE)
    a2.set_xlabel("Bus früher (–) / später (+) in Minuten", color=LEISE, fontsize=9)
    a2.set_ylabel("Mittlere Umsteigezeit (min)", color=LEISE, fontsize=9)
    a2.set_title("Wenn der 179er verschoben würde", loc="left", fontsize=10, color=TINTE)
    stil(a2)
    fig.suptitle(f"Ohlsdorf: S1 aus der Innenstadt → Bus 179 Richtung Poppenbüttel, {datum_de(tag)}",
                 x=0.01, ha="left", fontsize=11, color=TINTE)
    fig.tight_layout()
    fig.savefig(pfad)
    plt.close(fig)
    return v


# --------------------------------------------------------------------------- Bericht
def main() -> None:
    AUSGABE.mkdir(parents=True, exist_ok=True)
    halte, bus, tage, quelle = lade()
    cfg = json.loads((WURZEL / "aufbereitung" / "knoten.json").read_text(encoding="utf-8"))
    tage_wahl = stichtage(halte, bus, tage, date.today())
    werktag = tage_wahl["Werktag"]

    # F1
    f1_tabellen = {}
    for typ, tag in tage_wahl.items():
        f = f1_fahrten(am_tag(halte, tage, tag), START, ZIEL)
        f.assign(abfahrt=f.ab_s.map(uhrzeit), tagestyp=typ, datum=tag)[
            ["tagestyp", "datum", "abfahrt", "ziel", "dauer_min", "halt_ohlsdorf_min"]
        ].to_csv(AUSGABE / f"f1_fahrzeiten_{typ.lower()}.csv", index=False)
        f1_tabellen[typ] = f
    f1 = f1_tabellen["Werktag"]
    grafik_f1(f1, werktag, AUSGABE / "f1_fahrzeiten.png")
    rueck = f1_fahrten(am_tag(halte, tage, werktag), ZIEL, START)
    absch = abschnitte(am_tag(halte, tage, werktag), f1)

    kurz, lang = f1.dauer_min.min(), f1.dauer_min.max()
    f_kurz, f_lang = f1[f1.dauer_min == kurz], f1[f1.dauer_min == lang]
    halt_kurz, halt_lang = f_kurz.halt_ohlsdorf_min.mean(), f_lang.halt_ohlsdorf_min.mean()
    rest_kurz = (f_kurz.an_s - f_kurz.oh_ab).mean() / 60
    rest_lang = (f_lang.an_s - f_lang.oh_ab).mean() / 60
    vor_kurz = (f_kurz.oh_an - f_kurz.ab_s).mean() / 60
    vor_lang = (f_lang.oh_an - f_lang.ab_s).mean() / 60
    stunden_kurz = sorted({int(s // 3600) for s in f_kurz.ab_s})
    anteil_kurz = len(f_kurz) / len(f1)

    def zeitfenster(sek: pd.Series) -> str:
        if sek.empty:
            return "–"
        bloecke, start, vorher = [], None, None
        for s in sorted(sek):
            if start is None:
                start = vorher = s
            elif s - vorher > 15 * 60:
                bloecke.append((start, vorher))
                start = s
            vorher = s
        bloecke.append((start, vorher))
        return ", ".join(f"{uhrzeit(a)}–{uhrzeit(b)}" for a, b in bloecke)

    # F2-Tabelle (Fahrplan-Teil)
    f2 = pd.DataFrame({
        "Halt": absch.index,
        f"Fahrzeit vom vorigen Halt ({lang:.0f}-min-Fahrten)": [minuten(x) if pd.notna(x) else "–" for x in absch[("fahrt_s", lang)]],
        f"Haltezeit ({lang:.0f}-min-Fahrten)": [minuten(x) if pd.notna(x) else "–" for x in absch[("halt_s", lang)]],
        f"Fahrzeit vom vorigen Halt ({kurz:.0f}-min-Fahrten)": [minuten(x) if pd.notna(x) else "–" for x in absch.get(("fahrt_s", kurz), pd.Series(index=absch.index, dtype=float))],
        f"Haltezeit ({kurz:.0f}-min-Fahrten)": [minuten(x) if pd.notna(x) else "–" for x in absch.get(("halt_s", kurz), pd.Series(index=absch.index, dtype=float))],
    })
    f2.to_csv(AUSGABE / "f2_abschnitte_werktag.csv", index=False)

    # F7
    f7 = f7_anschluesse(am_tag(halte, tage, werktag), am_tag(bus, tage, werktag), cfg["knoten"])
    f7.to_csv(AUSGABE / "f7_anschluesse_werktag.csv", index=False)

    h_tag, b_tag = am_tag(halte, tage, werktag), am_tag(bus, tage, werktag)
    erste = h_tag.groupby("trip_id").folge.transform("min")
    oh = h_tag[(h_tag.halt == "Ohlsdorf") & (h_tag.richtung == 0)]
    oh = oh[oh.folge > erste[oh.index]].drop_duplicates(["richtung", "an_s"])
    b179 = b_tag[(b_tag.knoten == "Ohlsdorf") & (b_tag.linie == "179") & (~b_tag.ist_ende)
                 & (b_tag.einstieg_typ != 1) & (b_tag.ziel.str.contains("Poppenbüttel"))
                 ].sort_values("folge").drop_duplicates("trip_id")
    v179 = None
    if len(b179) and len(oh):
        abf = b179.ab_s.to_numpy(float)
        ank = tagsueber(oh.an_s.to_numpy(float))
        v179 = grafik_179(abf, ank, werktag, AUSGABE / "f7_ohlsdorf_179.png")
        v179.to_csv(AUSGABE / "f7_ohlsdorf_179_verschiebung.csv", index=False)
        k179 = kennzahlen(naechste(abf, ank, MIN_UMSTIEG_S))
        takt179 = takt_s(abf) / 60
        s1_takt = takt_s(oh.an_s.to_numpy(float)) / 60

    # Rangliste: wo liegt der Bus ungünstiger als nötig? (dichte Linien, genug S1-Ankünfte)
    rang = f7[(f7.umstieg == "S1 → Bus") & (f7.n >= 20) & (f7.bus_takt_min <= MAX_TAKT_RANG_MIN)].copy()
    rang["ersparnis_s"] = rang.mittel_s - rang.mittel_s_bei_bester
    rang = rang.sort_values("ersparnis_s", ascending=False)

    def rang_tabelle(df: pd.DataFrame) -> pd.DataFrame:
        return pd.DataFrame({
            "Knoten": df.knoten, "Bus": df.linie, "Bus Richtung": df.bus_ziel, "S1 kommend": df.s1_richtung,
            "Bus-Takt": [f"{t:.0f} min" for t in df.bus_takt_min],
            "Ø heute": [f"{minuten(x)} min" for x in df.mittel_s],
            "Ø bei zufälliger Lage": [f"{minuten(x)} min" for x in df.mittel_s_zufaellig],
            "Ø bei bester Lage": [f"{minuten(m)} min ({int(k):+d} min)" for k, m in
                                  zip(df.beste_verschiebung_min, df.mittel_s_bei_bester)],
            "Mögliche Ersparnis": [f"{minuten(x)} min" for x in df.ersparnis_s],
        })

    besser_als_zufall = (rang.mittel_s < rang.mittel_s_zufaellig).mean() if len(rang) else np.nan

    # Bericht
    teile = []
    teile.append("# Ergebnisse Stufe 1: Fahrplan-Analysen\n")
    teile.append(
        f"Automatisch erzeugt am {datetime.now().strftime('%d.%m.%Y')} aus dem hvv-Soll-Fahrplan "
        f"(gültig {quelle.get('gueltig_von', '?')}–{quelle.get('gueltig_bis', '?')}, "
        f"Prüfsumme {str(quelle.get('sha256', ''))[:12]}). "
        "**Alle Zahlen sind Plan-Werte ohne Verspätungen.** Was davon in der Realität ankommt, zeigt erst die "
        "Echtzeit-Erhebung ab Stufe 2.\n"
    )
    teile.append("Untersuchte Stichtage (typischer Fahrplan, kein Feiertag oder Sonderfahrplan): "
                 + ", ".join(f"{typ} = {datum_de(tag)}" for typ, tag in tage_wahl.items()) + ".\n")

    teile.append("## F1: Warum dauert Stadthausbrücke → Poppenbüttel mal länger, mal kürzer?\n")
    teile.append(
        f"**Kurz:** Laut Plan dauert die direkte Fahrt am {datum_de(werktag)} entweder **{kurz:.0f} oder "
        f"{lang:.0f} Minuten**. Der Unterschied entsteht fast vollständig beim **Halt in Ohlsdorf**: "
        f"Die längeren Fahrten stehen dort im Schnitt {minuten(halt_lang * 60)} Minuten, die kürzeren nur "
        f"{minuten(halt_kurz * 60)} Minuten.\n"
    )
    teile.append(
        f"- {len(f_lang)} Fahrten brauchen {lang:.0f} Minuten, {len(f_kurz)} Fahrten ({prozent(anteil_kurz)}) "
        f"brauchen {kurz:.0f} Minuten.\n"
        f"- Die {kurz:.0f}-Minuten-Fahrten starten nur in diesen Zeitfenstern: {zeitfenster(f_kurz.ab_s)}.\n"
        f"- Bis Ohlsdorf sind beide gleich schnell ({minuten(vor_lang * 60)} bzw. {minuten(vor_kurz * 60)} Minuten). "
        f"Von Ohlsdorf bis Poppenbüttel brauchen beide fast gleich lang ({minuten(rest_lang * 60)} bzw. "
        f"{minuten(rest_kurz * 60)} Minuten).\n"
        f"- Gegenrichtung Poppenbüttel → Stadthausbrücke: "
        + ", ".join(f"{n} Fahrten mit {d:.0f} Minuten" for d, n in rueck.dauer_min.value_counts().sort_index().items())
        + ".\n"
    )
    teile.append(
        "**Einordnung:** In Ohlsdorf wird die S1 planmäßig geteilt: Ein Zugteil fährt zum Flughafen, der andere "
        "nach Poppenbüttel. Fahrten mit langem Halt in Ohlsdorf passen zu diesem Teilen. Die kürzeren Fahrten mit "
        "kurzem Halt liegen in den Hauptverkehrszeiten und passen zu zusätzlichen Zügen, die nicht geteilt werden. "
        "Das ist eine Deutung aus den Haltezeiten; der Fahrplan selbst nennt den Grund nicht.\n"
    )
    teile.append(
        "Eine Differenz von 30 statt 35 Minuten in der App lässt sich mit dem Fahrplan allein nicht erklären: "
        "Die App rechnet vermutlich andere Verbindungen mit ein (z. B. mit Umstieg, Fußweg oder Echtzeit). "
        "Das prüfen wir, sobald Echtzeitdaten vorliegen.\n"
    )
    teile.append("![Fahrzeit je Fahrt](../ergebnisse/analysen/f1_fahrzeiten.png)\n")

    teile.append("## F2: Planmäßige Fahr- und Haltezeiten je Abschnitt\n")
    teile.append(
        "Grundlage für die spätere Puffer-Analyse (Stufe 3): Wo der Plan mehr Zeit vorsieht als die Züge "
        "tatsächlich brauchen, steckt Puffer. Werte in Minuten, Durchschnitt über alle Fahrten des Werktags. "
        "Der Fahrplan rechnet in ganzen Minuten; Haltezeiten unter einer Minute erscheinen daher als 0.\n"
    )
    teile.append(md_tabelle(f2) + "\n")

    teile.append("## F7: Wie gut sind die Busse auf die S1 abgestimmt?\n")
    if v179 is not None:
        jetzt = v179[v179.verschiebung_min == 0].iloc[0]
        best = v179.loc[v179.mittel_s.idxmin()]
        teile.append("### Der 179er in Ohlsdorf (Richtung Poppenbüttel)\n")
        teile.append(
            f"Für jede S1-Ankunft aus der Innenstadt in Ohlsdorf zwischen 06 und 20 Uhr: Wie lange dauert es bis zur nächsten Abfahrt des "
            f"179ers Richtung Poppenbüttel, wenn man mindestens {MIN_UMSTIEG_S // 60} Minuten zum Umsteigen braucht?\n"
        )
        teile.append(
            f"- Die S1 kommt etwa alle {s1_takt:.0f} Minuten an, der 179er fährt etwa alle {takt179:.0f} Minuten.\n"
            f"- Mittlere Umsteigezeit heute: **{minuten(k179['mittel_s'])} Minuten**; guter Anschluss (≤ 6 Minuten) bei "
            f"**{prozent(k179['gut'])}** der Ankünfte, mehr als 12 Minuten bei {prozent(k179['schlecht'])}.\n"
            f"- Zum Vergleich: Läge der 179er rein zufällig zur S1, betrüge die mittlere Umsteigezeit etwa "
            f"{minuten(v179[v179.verschiebung_min < 10].mittel_s.mean())} Minuten. Die heutige Lage ist also "
            + ("**besser als Zufall**" if k179['mittel_s'] < v179[v179.verschiebung_min < 10].mittel_s.mean()
               else "**nicht besser als Zufall**") + ".\n"
            f"- Die beste Verschiebung ({int(best.verschiebung_min):+d} Minuten) brächte nur "
            f"{minuten(k179['mittel_s'] - best.mittel_s)} Minuten weniger im Schnitt "
            f"({minuten(best.mittel_s)} Minuten, guter Anschluss bei {prozent(best.gut)}).\n"
        )
        if takt179 >= 1.8 * s1_takt:
            teile.append(
                f"**Wichtig:** Weil der Bus nur halb so oft fährt wie die S1, kann eine Verschiebung höchstens jeden "
                f"zweiten Zug gut anbinden. Die übrigen Ankünfte warten zwangsläufig lange. Eine spürbare Verbesserung "
                f"für alle bringt nur ein dichterer Takt. Das ist eine Frage an den hvv, der laut Hochbahn den Takt "
                f"festlegt.\n"
            )
        teile.append("![Umsteigen S1 → 179 in Ohlsdorf](../ergebnisse/analysen/f7_ohlsdorf_179.png)\n")
    teile.append("### Rangliste: am schlechtesten abgestimmte Bus-Anschlüsse (S1 → Bus)\n")
    teile.append(
        f"Buslinien an Ohlsdorf, Barmbek und Berliner Tor, die mindestens alle {MAX_TAKT_RANG_MIN} Minuten fahren, "
        "im Tagesverkehr 06–20 Uhr. Sortiert nach der möglichen Ersparnis: Wie viel kürzer wäre die mittlere "
        "Umsteigezeit, wenn der Bus um bis zu ±10 Minuten anders läge? „Zufällige Lage“ ist der Durchschnitt über "
        "alle Verschiebungen, also der Wert ohne jede Abstimmung. Unberücksichtigt bleibt, dass ein Bus auch andere "
        "Anschlüsse bedient. Das ist eine Vorschau auf F8, keine fertige Empfehlung.\n"
    )
    if len(rang):
        teile.append(f"Insgesamt liegen {prozent(besser_als_zufall)} dieser Verbindungen besser als bei zufälliger "
                     "Lage.\n")
    teile.append(md_tabelle(rang_tabelle(rang.head(15))) + "\n")
    teile.append("Alle Verbindungen in beide Umsteigerichtungen: "
                 "[f7_anschluesse_werktag.csv](../ergebnisse/analysen/f7_anschluesse_werktag.csv).\n")

    teile.append("## Methodik und Grenzen\n")
    teile.append(
        "- Quelle: hvv-Soll-Fahrplan (GTFS), unverändert archiviert als GitHub Release. "
        f"{quelle.get('quellenvermerk', '')}; Lizenz: {quelle.get('lizenz', '')}.\n"
        "- Umsteigezeit = Abfahrt des Anschlusses minus Ankunft, mindestens 3 Minuten (Methodik D7). "
        "Es zählt die nächste erreichbare Abfahrt.\n"
        "- Bus-Haltestellen gehören zu einem Knoten, wenn ihr Name mit „U S <Knoten>“ bzw. „S <Knoten>“ beginnt "
        "(offizielle hvv-Benennung). Einstellbar in `aufbereitung/knoten.json`.\n"
        "- Anschluss-Analyse im Tagesverkehr 06–20 Uhr; Ankünfte in Betriebspausen des Anschlusses "
        "(über 60 Minuten ohne Abfahrt) zählen nicht. Schienenersatzverkehr wird nicht als S-Bahn gezählt.\n"
        "- Plan-Werte in ganzen Minuten, ohne Verspätungen, ohne Fußwegunterschiede zwischen Haltestellen.\n"
    )
    BERICHT.write_text("\n".join(teile), encoding="utf-8")
    print(f"Bericht geschrieben: {BERICHT}")


if __name__ == "__main__":
    main()
