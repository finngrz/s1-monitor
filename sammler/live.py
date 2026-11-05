"""Live-Stand für die private Live-Seite: entsteht nebenbei aus den Daten, die der Sammler ohnehin abfragt.

Keine zusätzliche Abfrage bei der Hochbahn. Alle `intervall_s` Sekunden:
1. Lage der Linie je Richtung (Regeln in lage.py) und die nächsten Abfahrten der eingestellten Ansichten,
   jeweils mit Anzeige der Schnittstelle und erwarteter Verspätung aus der Prognose-Tabelle.
2. Verschlüsseln (AES-256-GCM, Schlüssel aus dem Passwort per PBKDF2): Einzelne Züge mit Live-Verspätung
   sind Rohdaten der Schnittstelle und werden nicht veröffentlicht. Nur wer das Passwort kennt, kann lesen.
3. Als einzige Datei `live.json` in den Zweig `live` dieses Repositorys schreiben (ohne Verlauf).

Das Hochladen läuft in einem eigenen Thread und bremst die Abfragen nie.
"""

from __future__ import annotations

import base64
import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import lage as lg
from speicher import BERLIN

PBKDF2_RUNDEN = 210_000


def verschluesseln(klartext: bytes, passwort: str, salz: bytes, schluessel: bytes | None = None) -> tuple[dict, bytes]:
    from cryptography.hazmat.primitives import hashes  # noqa: PLC0415
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: PLC0415
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC  # noqa: PLC0415

    if schluessel is None:
        schluessel = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salz,
                                iterations=PBKDF2_RUNDEN).derive(passwort.encode("utf-8"))
    iv = os.urandom(12)
    ct = AESGCM(schluessel).encrypt(iv, klartext, None)
    b64 = lambda b: base64.b64encode(b).decode("ascii")  # noqa: E731
    return {"v": 1, "kdf": "PBKDF2-SHA256", "runden": PBKDF2_RUNDEN, "salz": b64(salz), "iv": b64(iv),
            "daten": b64(ct)}, schluessel


def entschluesseln(paket: dict, passwort: str) -> bytes:
    """Gegenstück (für Tests); die Live-Seite macht dasselbe im Browser mit WebCrypto."""
    from cryptography.hazmat.primitives import hashes  # noqa: PLC0415
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: PLC0415
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC  # noqa: PLC0415

    salz = base64.b64decode(paket["salz"])
    k = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salz, iterations=paket["runden"]).derive(passwort.encode())
    return AESGCM(k).decrypt(base64.b64decode(paket["iv"]), base64.b64decode(paket["daten"]), None)


class LiveStand:
    def __init__(self, cfg: dict, reihenfolge: list[str], tabelle: dict | None):
        self.cfg = cfg
        self.idx = {n: i for i, n in enumerate(reihenfolge)}
        self.tabelle = tabelle or {}
        self.werte: dict[tuple, dict] = {}  # (Station, Soll, Richtung) -> letzter Stand
        self.meldungen: list[str] = []

    # ------------------------------------------------------------------ Aufnehmen
    def gruppe(self, station: str, richtung: str | None) -> str | None:
        ziel = (richtung or "").split(" / ")[0]
        zi, hi = self.idx.get(ziel), self.idx.get(station)
        if zi is None or hi is None or zi == hi:
            return None
        return "west" if zi < hi else "ost"

    def aufnehmen(self, zeile: dict) -> None:
        if zeile.get("linie") != "S1" or zeile.get("verkehrsmittel") != "TRAIN":
            return
        g = self.gruppe(zeile["abfrage_station"], zeile.get("richtung"))
        if g is None:
            return
        schl = (zeile["abfrage_station"], zeile["soll_utc"], g)
        d = self.werte.setdefault(schl, {"v": None, "ausfall": False, "ziele": set()})
        if zeile.get("verspaetung_s") is not None:
            d["v"] = zeile["verspaetung_s"]  # zuletzt bekannter Wert, wie in der Endstand-Tabelle
        d["ausfall"] = bool(zeile.get("ausfall"))
        if zeile.get("richtung"):
            d["ziele"].add(zeile["richtung"])

    def meldungen_aufnehmen(self, meldungen: list[dict], jetzt_utc: datetime) -> None:
        aktiv = []
        for m in meldungen:
            if m.get("planned"):
                continue
            text = json.dumps([m.get("summary"), m.get("locations")], ensure_ascii=False)
            if '"S1"' not in text and " S1" not in text and "S1 " not in text:
                continue
            for v in m.get("validities") or []:
                try:
                    a = datetime.strptime(v["begin"], "%Y-%m-%dT%H:%M:%S.%f%z")
                    b = datetime.strptime(v["end"], "%Y-%m-%dT%H:%M:%S.%f%z")
                except (KeyError, ValueError):
                    continue
                if a <= jetzt_utc <= b:
                    aktiv.append((m.get("summary") or "Störungsmeldung").strip())
                    break
        self.meldungen = list(dict.fromkeys(aktiv))[:3]

    def aufraeumen(self, jetzt_utc: datetime) -> None:
        grenze = jetzt_utc - timedelta(minutes=90)
        self.werte = {k: d for k, d in self.werte.items() if k[1] >= grenze}

    # ------------------------------------------------------------------ Auswerten
    def lage(self, g: str, bis: datetime) -> dict:
        von = bis - timedelta(minutes=lg.LAGE_FENSTER_MIN)
        v = [d["v"] for (st, s, gg), d in self.werte.items()
             if gg == g and st not in lg.STARTBAHNHOEFE and von <= s <= bis and d["v"] is not None]
        if len(v) < lg.LAGE_MIN_ABFAHRTEN:
            return {"mittel_min": None, "n": len(v)}
        return {"mittel_min": round(sum(v) / len(v) / 60, 1), "n": len(v)}

    def abfahrten(self, ansicht: dict, jetzt_utc: datetime) -> list[dict]:
        st, g, filt = ansicht["station"], ansicht["richtung"], ansicht.get("ziel_enthaelt")
        lage_jetzt = self.lage(lg.zulauf(st, g), jetzt_utc)["mittel_min"]
        bis = jetzt_utc + timedelta(minutes=self.cfg.get("vorschau_min", 45))
        liste = []
        for (s_st, soll, gg), d in sorted(self.werte.items(), key=lambda kv: kv[0][1]):
            if s_st != st or gg != g or soll > bis:
                continue
            if filt and not any(filt in z for z in d["ziele"]):
                continue
            app_min = None if d["v"] is None else d["v"] / 60
            if soll + timedelta(minutes=app_min or 0) < jetzt_utc - timedelta(minutes=1):
                continue  # schon abgefahren
            vorlauf = (soll - jetzt_utc).total_seconds() / 60
            z = lg.nachschlagen(self.tabelle, st, vorlauf, app_min, lage_jetzt)
            erwartet = z["erwartet"] if z else (app_min or 0)
            if app_min is not None:
                erwartet = max(erwartet, app_min)  # nie weniger als die Anzeige
            ampel = "ausfall" if d["ausfall"] else "gruen" if erwartet < 3 else "gelb" if erwartet < 6 else "rot"
            liste.append({
                "soll": soll.astimezone(BERLIN).strftime("%H:%M"), "soll_utc": soll.isoformat(),
                "app_min": None if app_min is None else round(app_min), "erwartet_min": round(erwartet),
                "von_min": None if not z else round(max(z["von"], 0)), "bis_min": None if not z else round(z["bis"]),
                "anteil_ab5": None if not z else z["anteil_ab5"], "faelle": None if not z else z["n"],
                "zelle": None if not z else z["zelle"], "vorlauf_min": round(max(vorlauf, 0)), "lage_min": lage_jetzt,
                "ampel": ampel, "ausfall": d["ausfall"], "ziel": " | ".join(sorted(d["ziele"]))})
            if len(liste) >= self.cfg.get("max_abfahrten", 6):
                break
        return liste

    def schnappschuss(self, jetzt_utc: datetime) -> dict:
        lage = {}
        for g in ("ost", "west"):
            jetzt_l, vorher = self.lage(g, jetzt_utc), self.lage(g, jetzt_utc - timedelta(minutes=10))
            a, b = jetzt_l["mittel_min"], vorher["mittel_min"]
            tendenz = None if a is None or b is None else "steigend" if a - b >= 1 else "fallend" if b - a >= 1 else "gleich"
            lage[g] = {**jetzt_l, "vor_10_min": b, "tendenz": tendenz, "klasse": lg.lage_klasse(a)}
        return {
            "erzeugt_utc": jetzt_utc.isoformat(timespec="seconds"),
            "lage": lage, "meldungen": self.meldungen,
            "ansichten": [{"id": a["id"], "titel": a["titel"], "station": a["station"],
                           "startbahnhof": a["station"] in lg.STARTBAHNHOEFE, "abfahrten": self.abfahrten(a, jetzt_utc)}
                          for a in self.cfg.get("ansichten", [])],
            "tabelle": {k: self.tabelle.get(k) for k in ("tage", "erster_tag", "letzter_tag", "abfahrten")},
        }


class LiveVeroeffentlichung:
    """Verschlüsselt den Schnappschuss und schreibt ihn als einzigen Commit in den Zweig `live`."""

    def __init__(self, passwort: str, repo: str, token: str, zweig: str = "live"):
        import requests  # noqa: PLC0415

        self.passwort, self.repo, self.zweig = passwort, repo, zweig
        self.sitzung = requests.Session()
        self.sitzung.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                     "X-GitHub-Api-Version": "2022-11-28"})
        self.salz, self.schluessel = os.urandom(16), None
        self.faden: threading.Thread | None = None
        self.erfolge = self.fehler = 0
        self.letzter_fehler = ""

    def _api(self, methode: str, pfad: str, **json_):
        r = self.sitzung.request(methode, f"https://api.github.com/repos/{self.repo}/{pfad}", json=json_ or None, timeout=20)
        return r

    def _hochladen(self, inhalt: str) -> None:
        try:
            blob = self._api("POST", "git/blobs", content=inhalt, encoding="utf-8")
            blob.raise_for_status()
            baum = self._api("POST", "git/trees", tree=[{"path": "live.json", "mode": "100644", "type": "blob",
                                                          "sha": blob.json()["sha"]}])
            baum.raise_for_status()
            commit = self._api("POST", "git/commits", message="Live-Stand", tree=baum.json()["sha"], parents=[])
            commit.raise_for_status()
            sha = commit.json()["sha"]
            ref = self._api("PATCH", f"git/refs/heads/{self.zweig}", sha=sha, force=True)
            if ref.status_code in (404, 422):
                ref = self._api("POST", "git/refs", ref=f"refs/heads/{self.zweig}", sha=sha)
            ref.raise_for_status()
            self.erfolge += 1
        except Exception as f:  # noqa: BLE001 — Live-Seite darf die Sammlung nie stören
            self.fehler += 1
            self.letzter_fehler = f"{type(f).__name__}: {str(f)[:120]}"

    def senden(self, schnappschuss: dict) -> bool:
        if self.faden and self.faden.is_alive():
            return False  # vorheriges Hochladen läuft noch: diesen Stand überspringen
        klartext = json.dumps(schnappschuss, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        paket, self.schluessel = verschluesseln(klartext, self.passwort, self.salz, self.schluessel)
        self.faden = threading.Thread(target=self._hochladen, args=(json.dumps(paket),), daemon=True)
        self.faden.start()
        return True

    def beenden(self) -> None:
        if self.faden:
            self.faden.join(timeout=20)


def tabelle_laden(pfad: Path) -> dict | None:
    try:
        return json.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def jetzt() -> datetime:
    return datetime.now(timezone.utc)
