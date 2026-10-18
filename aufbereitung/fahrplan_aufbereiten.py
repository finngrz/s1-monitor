"""Bereitet den hvv-Fahrplan (GTFS) für die S1-Analysen auf.

Aus dem großen Gesamtfahrplan (über 100 MB) werden nur die Teile herausgezogen, die wir
brauchen, und als kleine Dateien in ergebnisse/fahrplan/ gespeichert:

- s1_halte.parquet      jeder planmäßige Halt jeder S1-Fahrt (Ankunft, Abfahrt in Sekunden)
- knoten_bus.parquet    jede planmäßige Bus-Abfahrt/-Ankunft an den Knoten (Ohlsdorf, Barmbek, Berliner Tor)
- verkehrstage.parquet  an welchen Tagen welche Fahrt verkehrt (Dienst-ID je Datum)
- tage.csv              Übersicht je Tag: Anzahl S1-Fahrten (zeigt Sonderfahrpläne)
- quelle.json           aus welcher Fahrplan-Version die Dateien stammen

Zeiten werden als Sekunden seit Mitternacht des Betriebstags gespeichert. Werte über
86.400 (z. B. 24:19 Uhr) gehören noch zum Betriebstag davor, wie im GTFS-Standard üblich.

Aufruf:
  python aufbereitung/fahrplan_aufbereiten.py --zip <GTFS.zip> --meta <fahrplan_meta.json> --ziel ergebnisse/fahrplan
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

import duckdb

HIER = Path(__file__).resolve().parent


def sql_liste(werte: list[str]) -> str:
    return ", ".join("'" + w.replace("'", "''") + "'" for w in werte)


def aufbereiten(gtfs: Path, ziel: Path, cfg: dict) -> dict:
    con = duckdb.connect()
    for tabelle in ["routes", "trips", "stops", "stop_times", "calendar", "calendar_dates"]:
        pfad = gtfs / f"{tabelle}.txt"
        if pfad.exists():
            con.execute(
                f"create view {tabelle} as select * from read_csv('{pfad.as_posix()}', header=true, all_varchar=true)"
            )
        elif tabelle in ("calendar", "calendar_dates"):
            spalten = (
                "service_id, monday, tuesday, wednesday, thursday, friday, saturday, sunday, start_date, end_date"
                if tabelle == "calendar"
                else "service_id, date, exception_type"
            )
            leer = ", ".join(f"null::varchar as {s.strip()}" for s in spalten.split(","))
            con.execute(f"create view {tabelle} as select {leer} where false")
        else:
            raise FileNotFoundError(pfad)

    con.execute(
        "create macro sek(t) as split_part(t, ':', 1)::int * 3600 + split_part(t, ':', 2)::int * 60"
        " + split_part(t, ':', 3)::int"
    )

    linien = sql_liste(cfg["schnellbahn_linien"])
    praefix_bedingung = " or ".join(
        f"s.stop_name like '{p.replace(chr(39), chr(39) * 2)}%'"
        for k in cfg["knoten"].values()
        for p in k["bus_praefixe"]
    )
    knoten_fall = " ".join(
        f"when s.stop_name like '{p.replace(chr(39), chr(39) * 2)}%' then '{name}'"
        for name, k in cfg["knoten"].items()
        for p in k["bus_praefixe"]
    )

    # 1. Alle Halte der S-Bahn-Linie(n). Schienenersatzverkehr (z. B. "S1-SEV") ist ein Bus und
    #    wird hier bewusst nicht mitgezählt; Linien-Typ 100–199 = Bahn.
    con.execute(f"""
        create table s1_halte as
        select r.route_short_name as linie, t.trip_id, t.service_id, t.direction_id::int as richtung,
               t.trip_headsign as ziel, st.stop_sequence::int as folge, st.stop_id,
               s.stop_name as halt, s.platform_code as gleis,
               sek(st.arrival_time) as an_s, sek(st.departure_time) as ab_s
        from stop_times st
        join trips t using (trip_id)
        join routes r using (route_id)
        join stops s using (stop_id)
        where r.route_short_name in ({linien})
          and r.route_type::int between 100 and 199
        order by t.trip_id, folge
    """)

    # 2. Alle Busfahrten an den Knoten-Haltestellen (Linien-Typ 3 = Bus, 700–799 = Bus-Sonderformen).
    con.execute(f"""
        create table knoten_bus as
        select case {knoten_fall} end as knoten, r.route_short_name as linie, t.trip_id, t.service_id,
               t.direction_id::int as richtung, t.trip_headsign as ziel, st.stop_sequence::int as folge,
               st.stop_id, s.stop_name as haltestelle,
               sek(st.arrival_time) as an_s, sek(st.departure_time) as ab_s,
               coalesce(nullif(st.pickup_type, ''), '0')::int as einstieg_typ,
               coalesce(nullif(st.drop_off_type, ''), '0')::int as ausstieg_typ,
               st.stop_sequence::int = min(st.stop_sequence::int) over (partition by t.trip_id) as ist_start,
               st.stop_sequence::int = max(st.stop_sequence::int) over (partition by t.trip_id) as ist_ende
        from stop_times st
        join trips t using (trip_id)
        join routes r using (route_id)
        join stops s using (stop_id)
        where (r.route_type::int = 3 or r.route_type::int between 700 and 799)
          and ({praefix_bedingung})
    """)
    # Fenster-Funktion oben sieht nur Knoten-Halte; Start/Ende der ganzen Fahrt neu bestimmen.
    con.execute("""
        create table fahrt_grenzen as
        select trip_id, min(stop_sequence::int) as erste, max(stop_sequence::int) as letzte
        from stop_times where trip_id in (select distinct trip_id from knoten_bus) group by trip_id
    """)
    con.execute("""
        create or replace table knoten_bus as
        select b.* exclude (ist_start, ist_ende), b.folge = g.erste as ist_start, b.folge = g.letzte as ist_ende
        from knoten_bus b join fahrt_grenzen g using (trip_id)
        order by knoten, linie, ab_s
    """)

    # 3. Verkehrstage: Regel-Kalender plus/minus Ausnahmen, nur für benötigte Dienst-IDs.
    con.execute("""
        create table dienste as
        select distinct service_id from s1_halte union select distinct service_id from knoten_bus
    """)
    con.execute("""
        create table verkehrstage as
        with grenzen as (
            select min(d) as von, max(d) as bis from (
                select strptime(start_date, '%Y%m%d')::date as d from calendar
                union all select strptime(end_date, '%Y%m%d')::date from calendar
                union all select strptime(date, '%Y%m%d')::date from calendar_dates)
        ),
        tage as (select unnest(generate_series(von, bis, interval 1 day))::date as datum from grenzen),
        regel as (
            select c.service_id, tage.datum from calendar c, tage
            where tage.datum between strptime(c.start_date, '%Y%m%d') and strptime(c.end_date, '%Y%m%d')
              and [c.monday, c.tuesday, c.wednesday, c.thursday, c.friday, c.saturday, c.sunday][isodow(tage.datum)] = '1'
        ),
        plus as (select service_id, strptime(date, '%Y%m%d')::date as datum from calendar_dates where exception_type = '1'),
        minus as (select service_id, strptime(date, '%Y%m%d')::date as datum from calendar_dates where exception_type = '2')
        select * from ((select * from regel union select * from plus) except select * from minus)
        where service_id in (select service_id from dienste)
        order by datum, service_id
    """)

    # 4. Tagesübersicht.
    con.execute("""
        create table tage as
        select v.datum, dayname(v.datum) as wochentag, count(distinct h.trip_id) as s1_fahrten
        from verkehrstage v join (select distinct trip_id, service_id from s1_halte) h using (service_id)
        group by v.datum order by v.datum
    """)

    ziel.mkdir(parents=True, exist_ok=True)
    for tabelle in ["s1_halte", "knoten_bus", "verkehrstage"]:
        con.execute(f"copy {tabelle} to '{(ziel / (tabelle + '.parquet')).as_posix()}' (format parquet, compression zstd)")
    con.execute(f"copy tage to '{(ziel / 'tage.csv').as_posix()}' (header, delimiter ',')")

    zaehler = {
        t: con.execute(f"select count(*) from {t}").fetchone()[0]
        for t in ["s1_halte", "knoten_bus", "verkehrstage", "tage"]
    }
    zaehler["s1_fahrten"] = con.execute("select count(distinct trip_id) from s1_halte").fetchone()[0]
    return zaehler


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--zip", required=True, help="GTFS-ZIP-Datei")
    parser.add_argument("--meta", help="fahrplan_meta.json aus fahrplan/quelle.py")
    parser.add_argument("--ziel", default="ergebnisse/fahrplan")
    parser.add_argument("--konfig", default=str(HIER / "knoten.json"))
    args = parser.parse_args()

    cfg = json.loads(Path(args.konfig).read_text(encoding="utf-8"))
    ziel = Path(args.ziel)
    tmp = Path(tempfile.mkdtemp(prefix="gtfs_"))
    try:
        with zipfile.ZipFile(args.zip) as z:
            for name in z.namelist():
                basis = Path(name).name
                if basis.endswith(".txt") and basis != "shapes.txt":  # Streckenverläufe brauchen wir nicht
                    with z.open(name) as quelle, open(tmp / basis, "wb") as ziel_datei:
                        shutil.copyfileobj(quelle, ziel_datei)
        zaehler = aufbereiten(tmp, ziel, cfg)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    herkunft = {"zeilen": zaehler}
    if args.meta:
        meta = json.loads(Path(args.meta).read_text(encoding="utf-8"))
        herkunft = {k: meta.get(k) for k in ("dateiname", "sha256", "gueltig_von", "gueltig_bis", "quellenvermerk", "lizenz")} | herkunft
    (ziel / "quelle.json").write_text(json.dumps(herkunft, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(herkunft, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
