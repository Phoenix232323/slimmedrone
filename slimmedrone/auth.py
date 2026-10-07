"""Gebruikers voor de inlogpagina.

Wachtwoorden worden nooit zelf opgeslagen, alleen een "hash" (een
onomkeerbare versleuteling). Gebruikers beheer je met beheer.py.
"""
import json
import re
import threading
import time
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

USERNAME_PATTERN = re.compile(r"[a-z0-9_.-]{2,32}")
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

    def add(self, username: str, password: str):
        username = username.strip().lower()
        if not USERNAME_PATTERN.fullmatch(username):
            raise ValueError("Gebruikersnaam: 2-32 tekens, alleen kleine letters, cijfers, . _ of -")
        if len(password) < MIN_PASSWORD_LENGTH:
            raise ValueError(f"Wachtwoord moet minstens {MIN_PASSWORD_LENGTH} tekens zijn.")
        with self._lock:
            users = self._load()
            users[username] = generate_password_hash(password)
            self._save(users)

    def remove(self, username: str) -> bool:
        with self._lock:
            users = self._load()
            if users.pop(username.strip().lower(), None) is None:
                return False
            self._save(users)
            return True

    def names(self) -> list:
        return sorted(self._load())

    def verify(self, username: str, password: str) -> bool:
        stored = self._load().get(username.strip().lower())
        ok = check_password_hash(stored or _DUMMY_HASH, password)
        return ok and stored is not None


class LoginLimiter:
    """Na 5 foute pogingen moet een IP-adres 5 minuten wachten."""

    def __init__(self, max_attempts: int = 5, window: float = 300):
        self.max_attempts = max_attempts
        self.window = window
        self._failures = {}
        self._lock = threading.Lock()

    def blocked(self, ip: str) -> bool:
        with self._lock:
            recent = [t for t in self._failures.get(ip, []) if time.time() - t < self.window]
            self._failures[ip] = recent
            return len(recent) >= self.max_attempts

    def failed(self, ip: str):
        with self._lock:
            self._failures.setdefault(ip, []).append(time.time())

    def succeeded(self, ip: str):
        with self._lock:
            self._failures.pop(ip, None)
