"""Gemeinsame Regeln für „Lage der Linie“ und die Kurzfrist-Prognose.

Dieselben Definitionen gelten an drei Stellen, damit Live-Anzeige und Statistik zusammenpassen:
- im Sammler (Live-Stand alle 2 Minuten, sammler/live.py),
- in der Pflege (Endstand-Tabelle: was war zu jedem Vorlauf bekannt, sammler/pflege.py),
- in der Prognose-Tabelle (auswertung/prognose_tabelle.py).

Lage der Linie zum Zeitpunkt t, je Fahrtrichtung: Mittelwert der zuletzt bekannten Verspätung aller
S1-Abfahrten dieser Richtung mit Soll-Zeit in [t − 20 min, t], ohne Startbahnhöfe.

Prognose: Nachschlagetabelle nach (Art des Halts, Vorlauf, Anzeige der Schnittstelle, Lage der Linie).
An Startbahnhöfen zeigt die Schnittstelle vor der Abfahrt fast immer „pünktlich“; dort zählt die Lage
der Gegenrichtung (der ankommende Zug wird zum abfahrenden), und als tatsächliche Verspätung gilt die
Verspätung am nächsten Halt, weil die Abfahrt am Startbahnhof selbst meist nicht korrigiert wird.
"""

from __future__ import annotations

VORLAEUFE_MIN = [0, 5, 10, 15, 20, 30]
LAGE_FENSTER_MIN = 20
LAGE_MIN_ABFAHRTEN = 3
STARTBAHNHOEFE = {"Wedel", "Poppenbüttel", "Hamburg Airport (Flughafen)"}
FOLGEHALT = {("Poppenbüttel", "west"): "Wellingsbüttel", ("Hamburg Airport (Flughafen)", "west"): "Ohlsdorf",
             ("Wedel", "ost"): "Rissen"}
ANZEIGE_KLASSEN = [("0", 0, 0), ("1-2", 1, 2), ("3-4", 3, 4), ("5-7", 5, 7), (">7", 8, 10**6)]
LAGE_KLASSEN = [("<1", -10**6, 1), ("1-3", 1, 3), ("3-6", 3, 6), (">6", 6, 10**6)]  # untere Grenze inklusive
MIN_FAELLE = 30  # weniger Fälle in einer Zelle: auf gröbere Zelle ausweichen


# Flattern (beobachtet ab 05.11.2026): Die Schnittstelle antwortet abwechselnd aus einem aktuellen und
# einem veralteten Stand. Dann springt dieselbe Abfahrt minütlich z. B. zwischen +5 und 0 oder zwischen
# „fällt aus“ und „fährt“. Regel: Ein Sprung von mindestens 2 Minuten Verspätung direkt auf 0 gilt als
# veraltete Antwort und wird ignoriert (echtes Aufholen geschieht schrittweise). Nach einer
# Ausfallmeldung gilt „fährt“ erst, wenn wieder eine Verspätung über 0 gemeldet wird.
FLATTERN_AB_S = 120


def plausibel(vorher_v: float | None, vorher_ausfall: bool, v: float | None, ausfall: bool) -> tuple[float | None, bool, bool]:
    """Bereinigter Stand (Verspätung, Ausfall) und ob die neue Beobachtung als veraltet verworfen wurde."""
    if ausfall:
        return v, True, False
    if vorher_ausfall and (v is None or v == 0):
        return None, True, True
    if v == 0 and vorher_v is not None and vorher_v >= FLATTERN_AB_S:
        return None, False, True
    return v, False, False


def anzeige_klasse(minuten: float | None) -> str:
    m = 0 if minuten is None or minuten != minuten else max(0, round(minuten))
    for name, a, b in ANZEIGE_KLASSEN:
        if a <= m <= b:
            return name
    return ANZEIGE_KLASSEN[-1][0]


def lage_klasse(minuten: float | None) -> str:
    if minuten is None or minuten != minuten:
        return "*"
    for name, a, b in LAGE_KLASSEN:
        if a <= minuten < b:
            return name
    return LAGE_KLASSEN[-1][0]


def vorlauf_klasse(minuten: float) -> int:
    """Nächstgelegener ausgewerteter Vorlauf (über 30 Minuten: 30)."""
    m = max(0.0, minuten)
    return min(VORLAEUFE_MIN, key=lambda v: abs(v - m))


def art(station: str) -> str:
    return "start" if station in STARTBAHNHOEFE else "unterwegs"


def zulauf(station: str, gruppe: str) -> str:
    """Richtung, deren Lage für eine Abfahrt zählt (am Startbahnhof die des ankommenden Zuges)."""
    if station in STARTBAHNHOEFE:
        return "ost" if gruppe == "west" else "west"
    return gruppe


def zelle(art_: str, vorlauf: int, anzeige: str, lage: str) -> str:
    return f"{art_}|{vorlauf}|{anzeige}|{lage}"


def nachschlagen(tabelle: dict, station: str, vorlauf_min: float, anzeige_min: float | None,
                 lage_min: float | None) -> dict | None:
    """Erwartete Verspätung aus der Prognose-Tabelle; weicht bei zu wenigen Fällen auf gröbere Zellen aus."""
    zellen = (tabelle or {}).get("zellen", {})
    a, v = art(station), vorlauf_klasse(vorlauf_min)
    k_an, k_la = anzeige_klasse(anzeige_min), lage_klasse(lage_min)
    for schl in (zelle(a, v, k_an, k_la), zelle(a, v, k_an, "*"), zelle(a, v, "*", k_la), zelle(a, v, "*", "*")):
        z = zellen.get(schl)
        if z and z["n"] >= MIN_FAELLE:
            return {**z, "zelle": schl}
    return None
