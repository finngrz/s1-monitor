"""Speichern der Rohbeobachtungen: nur Änderungen, als kleine Parquet-Dateien je Lauf.

Ordnerstruktur im (privaten) Daten-Repository:

    abfahrten/JJJJ-MM-TT/<lauf>_<teil>.parquet   jede geänderte Prognose einer Fahrt
    meldungen/JJJJ-MM-TT/<lauf>_<teil>.parquet   jede neue oder geänderte Störungsmeldung
    protokoll/JJJJ-MM-TT/<lauf>_<teil>.parquet   jede einzelne Abfrage, auch fehlgeschlagene
    stationen_geprueft.json                      überprüfte Stations-IDs

"Nur Änderungen" heißt: Für jede Fahrt an jeder Station wird eine neue Zeile geschrieben, wenn
sie zum ersten Mal auftaucht oder sich irgendein Feld (Verspätung, Ausfall, Gleis, ...) ändert.
Es geht keine Information verloren, aber die Datenmenge schrumpft stark.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

BERLIN = ZoneInfo("Europe/Berlin")
UTC = timezone.utc


def fingerabdruck(obj) -> str:
    return hashlib.sha1(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def gti_zeit(zeit: dict) -> datetime:
    """GTI-Zeitangabe {"date": "TT.MM.JJJJ", "time": "HH:MM"} -> Zeitpunkt in Berliner Zeit."""
    return datetime.strptime(f"{zeit['date']} {zeit['time']}", "%d.%m.%Y %H:%M").replace(tzinfo=BERLIN)


def abfahrt_zu_zeile(dep: dict, bezug: datetime, abfrage_station: str, art: str, beobachtet: datetime) -> dict:
    """Wandelt eine Abfahrt (oder Ankunft) aus departureList in eine flache Zeile um.

    `timeOffset` ist relativ zum Abfragezeitpunkt und ändert sich jede Minute. Daraus wird die
    absolute Soll-Zeit berechnet und timeOffset aus dem Rohdatensatz entfernt, damit nicht jede
    Abfrage als "Änderung" zählt.
    """
    linie = dep.get("line") or {}
    soll = bezug + timedelta(minutes=dep.get("timeOffset") or 0)
    roh = {k: v for k, v in dep.items() if k != "timeOffset"}
    station = dep.get("station") or {}
    schluessel = fingerabdruck([abfrage_station, art, station.get("id"), linie.get("id"), linie.get("name"),
                                dep.get("directionId"), dep.get("serviceId"), soll.isoformat()])
    return {
        "beobachtet_utc": beobachtet.astimezone(UTC),
        "abfrage_station": abfrage_station,
        "art": art,  # "ab" = Abfahrt, "an" = Ankunft
        "haltestelle_id": station.get("id"),
        "haltestelle": station.get("name"),
        "linie": linie.get("name"),
        "linie_id": linie.get("id"),
        "richtung_id": dep.get("directionId"),
        "richtung": linie.get("direction"),
        "verkehrsmittel": (linie.get("type") or {}).get("simpleType"),
        "service_id": dep.get("serviceId"),
        "soll_utc": soll.astimezone(UTC),
        "verspaetung_s": dep.get("delay"),
        "ausfall": bool(dep.get("cancelled", False)),
        "zusatzfahrt": bool(dep.get("extra", False)),
        "gleis": dep.get("platform"),
        "gleis_echtzeit": dep.get("realtimePlatform"),
        "fahrzeuge": json.dumps(dep.get("vehicles"), ensure_ascii=False) if dep.get("vehicles") else None,
        "schluessel": schluessel,
        "stand": fingerabdruck(roh),
        "roh": json.dumps(roh, ensure_ascii=False, sort_keys=True),
    }


def meldung_zu_zeile(m: dict, beobachtet: datetime) -> dict:
    return {
        "beobachtet_utc": beobachtet.astimezone(UTC),
        "meldung_id": m.get("id"),
        "version": m.get("version"),
        "zuletzt_geaendert": m.get("lastModified"),
        "geplant": m.get("planned"),
        "grund": m.get("reason"),
        "zusammenfassung": m.get("summary"),
        "beschreibung": m.get("description"),
        "orte": json.dumps(m.get("locations"), ensure_ascii=False) if m.get("locations") else None,
        "gueltigkeit": json.dumps(m.get("validities"), ensure_ascii=False) if m.get("validities") else None,
        "schluessel": str(m.get("id")),
        "stand": fingerabdruck(m),
        "roh": json.dumps(m, ensure_ascii=False, sort_keys=True),
    }


class Speicher:
    """Merkt sich den letzten bekannten Stand und sammelt nur neue/geänderte Zeilen."""

    def __init__(self, wurzel: Path, lauf_id: str):
        self.wurzel = Path(wurzel)
        self.lauf_id = lauf_id
        self.teil = 0
        self.puffer: dict[str, list[dict]] = {"abfahrten": [], "meldungen": [], "protokoll": []}
        self.letzter_stand: dict[str, dict[str, str]] = {"abfahrten": {}, "meldungen": {}}
        self.zaehler = {"abfahrten_neu": 0, "abfahrten_gesehen": 0, "meldungen_neu": 0, "abfragen": 0, "fehler": 0}

    # ---------------------------------------------------------------- Zustand laden
    def lade_zustand(self, heute: date) -> None:
        """Liest die Beobachtungen von gestern und heute, damit ein neuer Lauf nahtlos weitermacht."""
        for art in ("abfahrten", "meldungen"):
            dateien = []
            for tag in (heute - timedelta(days=1), heute):
                ordner = self.wurzel / art / tag.isoformat()
                if ordner.exists():
                    dateien += sorted(ordner.glob("*.parquet"))
            if not dateien:
                continue
            df = pd.concat([pd.read_parquet(d, columns=["beobachtet_utc", "schluessel", "stand"]) for d in dateien])
            df = df.sort_values("beobachtet_utc").drop_duplicates("schluessel", keep="last")
            self.letzter_stand[art] = dict(zip(df.schluessel, df.stand))

    # ---------------------------------------------------------------- Aufnehmen
    def nimm_auf(self, art: str, zeile: dict) -> bool:
        self.zaehler[f"{art}_gesehen"] = self.zaehler.get(f"{art}_gesehen", 0) + 1
        if self.letzter_stand[art].get(zeile["schluessel"]) == zeile["stand"]:
            return False
        self.letzter_stand[art][zeile["schluessel"]] = zeile["stand"]
        self.puffer[art].append(zeile)
        self.zaehler[f"{art}_neu"] = self.zaehler.get(f"{art}_neu", 0) + 1
        return True

    def protokolliere(self, **eintrag) -> None:
        self.zaehler["abfragen"] += 1
        if eintrag.get("fehler"):
            self.zaehler["fehler"] += 1
        self.puffer["protokoll"].append(eintrag)

    # ---------------------------------------------------------------- Schreiben
    def schreibe(self) -> list[Path]:
        """Schreibt alles Gepufferte als neue Teil-Dateien (nach Berliner Datum der Beobachtung)."""
        geschrieben = []
        for art, zeilen in self.puffer.items():
            if not zeilen:
                continue
            df = pd.DataFrame(zeilen)
            spalte = "beobachtet_utc" if "beobachtet_utc" in df else "zeit_utc"
            df[spalte] = pd.to_datetime(df[spalte], utc=True)
            tage = df[spalte].dt.tz_convert(BERLIN).dt.date
            for tag, teil in df.groupby(tage):
                ordner = self.wurzel / art / tag.isoformat()
                ordner.mkdir(parents=True, exist_ok=True)
                pfad = ordner / f"{self.lauf_id}_{self.teil:03d}.parquet"
                teil.to_parquet(pfad, index=False, compression="zstd")
                geschrieben.append(pfad)
            self.puffer[art] = []
        self.teil += 1
        return geschrieben
