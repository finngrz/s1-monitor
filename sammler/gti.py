"""Schlanker Client für die Geofox-Schnittstelle (GTI) der Hochbahn.

Jede Anfrage wird mit HMAC-SHA1 signiert: Schlüssel = Passwort, Nachricht = der exakte
Anfrage-Text (UTF-8), Ergebnis Base64-kodiert im Kopf "geofox-auth-signature".
Das Passwort selbst wird nie übertragen.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass

import requests


class GTIFehler(Exception):
    """Fehler der Schnittstelle. `dauerhaft` = Weiterfragen ist sinnlos (z. B. falsche Zugangsdaten)."""

    def __init__(self, text: str, http_status: int | None = None, return_code: str | None = None,
                 dauerhaft: bool = False):
        super().__init__(text)
        self.http_status = http_status
        self.return_code = return_code
        self.dauerhaft = dauerhaft


@dataclass
class Antwort:
    daten: dict
    http_status: int
    dauer_ms: int


def signatur(passwort: str, koerper: bytes) -> str:
    return base64.b64encode(hmac.new(passwort.encode("utf-8"), koerper, hashlib.sha1).digest()).decode("ascii")


class GTI:
    def __init__(self, benutzer: str, passwort: str, basis: str, version: int, timeout_s: float = 20.0,
                 sitzung: requests.Session | None = None):
        self.benutzer = benutzer
        self.passwort = passwort
        self.basis = basis.rstrip("/")
        self.version = version
        self.timeout_s = timeout_s
        self.sitzung = sitzung or requests.Session()

    def anfrage(self, methode: str, nutzlast: dict) -> Antwort:
        koerper = json.dumps({**nutzlast, "version": self.version, "language": "de"},
                             ensure_ascii=False).encode("utf-8")
        kopf = {
            "Content-Type": "application/json;charset=UTF-8",
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "geofox-auth-type": "HmacSHA1",
            "geofox-auth-user": self.benutzer,
            "geofox-auth-signature": signatur(self.passwort, koerper),
            "X-Platform": "web",
            "X-TraceId": str(uuid.uuid4()),
        }
        start = time.monotonic()
        try:
            r = self.sitzung.post(f"{self.basis}/{methode}", data=koerper, headers=kopf, timeout=self.timeout_s)
        except requests.RequestException as fehler:
            raise GTIFehler(f"Netzwerkfehler: {type(fehler).__name__}") from None
        dauer_ms = int((time.monotonic() - start) * 1000)

        if r.status_code in (401, 403):
            raise GTIFehler("Zugang verweigert (Zugangsdaten oder Freischaltung prüfen)", r.status_code, dauerhaft=True)
        if r.status_code != 200:
            raise GTIFehler(f"HTTP {r.status_code}", r.status_code)
        try:
            daten = r.json()
        except ValueError:
            raise GTIFehler("Antwort ist kein JSON", r.status_code) from None

        code = daten.get("returnCode")
        if code != "OK":
            text = daten.get("errorText") or "unbekannter Fehler"
            dauerhaft = code in ("ERROR_COMM",) and "auth" in text.lower()
            raise GTIFehler(f"{code}: {text}", r.status_code, code, dauerhaft=dauerhaft)
        return Antwort(daten, r.status_code, dauer_ms)
