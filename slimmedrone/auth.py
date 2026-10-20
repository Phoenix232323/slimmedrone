"""Gebruikers voor de inlogpagina.

Wachtwoorden worden nooit zelf opgeslagen, alleen een "hash" (een
onomkeerbare versleuteling). Gebruikers beheer je met beheer.py of op de
website (pagina Instellingen).
"""
import hashlib
import json
import re
import threading
import time
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

# Niet beginnen met een punt: namen als ".." zouden in een webadres (/api/gebruikers/..)
# als "map omhoog" worden gelezen en zijn dan niet meer te verwijderen.
USERNAME_PATTERN = re.compile(r"[a-z0-9_-][a-z0-9_.-]{1,31}")
MIN_PASSWORD_LENGTH = 8
# Wordt gebruikt als de gebruikersnaam niet bestaat, zodat een inlogpoging
# even lang duurt en je niet kunt raden welke namen bestaan.
_DUMMY_HASH = generate_password_hash("dummy-wachtwoord")


class UserStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self, users: dict):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(users, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def add(self, username: str, password: str, replace: bool = True):
        """Gebruiker maken. replace=True: bestaat hij al, dan krijgt hij dit nieuwe wachtwoord
        (zo werkt beheer.py). De website gebruikt replace=False: dan is dat een fout."""
        username = username.strip().lower()
        if not USERNAME_PATTERN.fullmatch(username):
            raise ValueError("Gebruikersnaam: 2-32 tekens, alleen kleine letters, cijfers, . _ of - "
                             "(niet beginnen met een punt)")
        check_password(password)
        with self._lock:
            users = self._load()
            if not replace and username in users:
                raise ValueError(f"De gebruiker '{username}' bestaat al.")
            users[username] = generate_password_hash(password)
            self._save(users)

    def remove(self, username: str, keep_last: bool = False) -> bool:
        """Verwijder een gebruiker. Geeft False als hij niet bestond.

        keep_last=True: de laatste gebruiker verwijderen mag niet (ValueError), anders kan
        niemand meer inloggen. Dat controleren we binnen het slot, zodat twee verzoeken
        tegelijk niet samen de laatste twee gebruikers kunnen weghalen.
        """
        with self._lock:
            users = self._load()
            username = username.strip().lower()
            if username not in users:
                return False
            if keep_last and len(users) <= 1:
                raise ValueError("De laatste gebruiker kan niet worden verwijderd.")
            del users[username]
            self._save(users)
            return True

    def change_password(self, username: str, password: str):
        """Nieuw wachtwoord voor een bestaande gebruiker (ValueError als het te kort is)."""
        check_password(password)
        with self._lock:
            users = self._load()
            username = username.strip().lower()
            if username not in users:
                raise ValueError("Deze gebruiker bestaat niet (meer).")
            users[username] = generate_password_hash(password)
            self._save(users)

    def stamp(self, username: str):
        """Een kort "stempel" van het huidige wachtwoord, of None als de gebruiker niet bestaat.

        Staat in het inlogcookie. Verandert het wachtwoord (of wordt de gebruiker
        verwijderd), dan klopt het stempel niet meer en is iedereen die nog met het oude
        wachtwoord was ingelogd automatisch uitgelogd. Het is een hash van de hash: er is
        niets van het wachtwoord uit terug te halen.
        """
        stored = self._load().get(username.strip().lower())
        return hashlib.sha256(stored.encode()).hexdigest()[:16] if stored else None

    def names(self) -> list:
        return sorted(self._load())

    def verify(self, username: str, password: str) -> bool:
        stored = self._load().get(username.strip().lower())
        ok = check_password_hash(stored or _DUMMY_HASH, password)
        return ok and stored is not None


def check_password(password: str):
    """ValueError met uitleg als het wachtwoord te kort (of veel te lang) is."""
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Wachtwoord moet minstens {MIN_PASSWORD_LENGTH} tekens zijn.")
    if len(password) > 200:
        raise ValueError("Wachtwoord is te lang (hooguit 200 tekens).")


class LoginLimiter:
    """Na 5 foute pogingen moet een IP-adres 5 minuten wachten.

    Gebruik: eerst attempt(ip) (False = wachten), dan het wachtwoord controleren, en bij
    een goed wachtwoord succeeded(ip). Een poging telt al als fout zodra hij begint; zo
    kun je de limiet niet omzeilen door heel veel pogingen tegelijk te sturen.
    """

    def __init__(self, max_attempts: int = 5, window: float = 300):
        self.max_attempts = max_attempts
        self.window = window
        self._failures = {}
        self._lock = threading.Lock()

    def attempt(self, ip: str) -> bool:
        """Mag dit IP-adres het nu proberen? Zo ja, dan telt de poging meteen mee (als fout)."""
        with self._lock:
            now = time.time()
            recent = [t for t in self._failures.get(ip, []) if now - t < self.window]
            allowed = len(recent) < self.max_attempts
            if allowed:
                recent.append(now)
            self._failures[ip] = recent
            return allowed

    def succeeded(self, ip: str):
        with self._lock:
            self._failures.pop(ip, None)
