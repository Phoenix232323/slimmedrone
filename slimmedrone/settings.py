"""Instellingen die je op de website kunt veranderen (pagina Instellingen).

Ze staan in data/instellingen.json en gaan vóór de waarden uit .env. Zo kun je
bijvoorbeeld de Claude-sleutel invullen zonder iets op de Raspberry Pi te typen
en zonder te herstarten.

De sleutel is geheim: hij komt nooit terug op de website en nooit in de logs,
alleen de laatste 4 tekens ("…AbCd"), zodat je kunt zien welke sleutel er staat.
Het bestand mag alleen door de eigenaar gelezen worden (bestandsrechten 0600).
"""
import json
import logging
import os
import re
import threading
from pathlib import Path

log = logging.getLogger(__name__)

EFFORTS = ("low", "medium", "high")  # hoe lang Claude nadenkt: snel ... grondig
DEFAULT_EFFORT = "low"
MODEL_PATTERN = re.compile(r"claude-[a-z0-9][a-z0-9.\-]{1,60}")
KEY_PATTERN = re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,300}")

# Namen in instellingen.json: dezelfde als in .env, dan is het herkenbaar.
KEY, MODEL, EFFORT, WEB = "ANTHROPIC_API_KEY", "CLAUDE_MODEL", "CLAUDE_EFFORT", "JARVIS_WEB"


def mask(key: str):
    """'sk-ant-....AbCd' -> '…AbCd'. Zo zie je welke sleutel er staat, zonder hem te verraden."""
    return "…" + key[-4:] if key else None


class Settings:
    def __init__(self, path: Path, cfg):
        self.path = path
        effort = cfg.claude_effort.strip().lower()
        if effort not in EFFORTS:
            log.warning("CLAUDE_EFFORT=%s ken ik niet (kies low, medium of high); ik gebruik %s",
                        cfg.claude_effort, DEFAULT_EFFORT)
            effort = DEFAULT_EFFORT
        # De waarden uit .env: die gelden als er op de website niets is ingevuld.
        self._env = {KEY: cfg.anthropic_api_key, MODEL: cfg.claude_model,
                     EFFORT: effort, WEB: cfg.jarvis_web}
        self._lock = threading.Lock()
        self._saved = self._load()

    # -- lezen ---------------------------------------------------------------

    def claude(self) -> dict:
        """De instellingen voor J.A.R.V.I.S. die nu gelden (website gaat vóór .env)."""
        with self._lock:
            saved = dict(self._saved)
        merged = {**self._env, **saved}
        source = "website" if saved.get(KEY) else ("env" if self._env[KEY] else None)
        return {"api_key": merged[KEY] or "", "bron_sleutel": source, "model": merged[MODEL],
                "effort": merged[EFFORT], "web": bool(merged[WEB])}

    def public(self) -> dict:
        """Hetzelfde, maar veilig om naar de website te sturen: zonder de sleutel zelf."""
        ai = self.claude()
        return {"sleutel_ingesteld": bool(ai["api_key"]), "sleutel_einde": mask(ai["api_key"]),
                "bron_sleutel": ai["bron_sleutel"], "model": ai["model"], "effort": ai["effort"],
                "web": ai["web"]}

    # -- veranderen ----------------------------------------------------------

    def update(self, data: dict) -> dict:
        """Verwerk wat de website stuurt en sla het op. Geeft claude() terug.

        Mogelijk: {"sleutel": "sk-ant-...", "effort": "low", "web": true, "model": "claude-..."}.
        Een lege tekst ("") betekent: weer de waarde uit .env gebruiken.
        Bij foute invoer komt er een ValueError met een uitleg voor de gebruiker.
        """
        if not isinstance(data, dict):
            raise ValueError("Ongeldige instellingen.")
        changes = {}  # naam -> nieuwe waarde (None = verwijderen)
        if "sleutel" in data:
            changes[KEY] = _check_key(data["sleutel"])
        if "effort" in data:
            changes[EFFORT] = _check_choice(data["effort"], EFFORTS, "Kies low, medium of high.")
        if "model" in data:
            changes[MODEL] = _check_model(data["model"])
        if "web" in data:
            if not isinstance(data["web"], bool):
                raise ValueError("Internet zoeken moet aan of uit zijn.")
            changes[WEB] = data["web"]
        if not changes:
            return self.claude()

        with self._lock:
            saved = dict(self._saved)
            for name, value in changes.items():
                if value is None:
                    saved.pop(name, None)
                else:
                    saved[name] = value
            self._write(saved)
            self._saved = saved

        # Loggen zonder de sleutel zelf.
        if KEY in changes:
            if changes[KEY]:
                log.info("Claude-sleutel ingesteld via de website (%s)", mask(changes[KEY]))
            else:
                log.info("Claude-sleutel van de website verwijderd")
        others = {k: v for k, v in changes.items() if k != KEY}
        if others:
            log.info("Instellingen aangepast: %s", others)
        return self.claude()

    # -- bestand -------------------------------------------------------------

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            log.warning("Kan %s niet lezen (%s); ik gebruik de waarden uit .env", self.path, exc)
            return {}
        _make_private(self.path)
        if not isinstance(data, dict):
            return {}
        # Alleen geldige waarden overnemen: iemand kan het bestand met de hand aangepast hebben.
        clean = {}
        for name, check in ((KEY, _check_key), (MODEL, _check_model),
                            (EFFORT, lambda v: _check_choice(v, EFFORTS, ""))):
            try:
                value = check(data.get(name, ""))
            except ValueError:
                log.warning("Ongeldige %s in %s wordt genegeerd", name, self.path.name)
                continue
            if value:
                clean[name] = value
        if isinstance(data.get(WEB), bool):
            clean[WEB] = data[WEB]
        return clean

    def _write(self, data: dict):
        """Veilig opslaan: eerst naar een tijdelijk bestand, dan in één keer vervangen.

        Zo is het bestand nooit half geschreven, ook niet als de stroom uitvalt.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        _make_private(tmp)  # ook als het tijdelijke bestand al bestond met andere rechten
        os.replace(tmp, self.path)


# -- controles ----------------------------------------------------------------

def _check_key(value):
    if not isinstance(value, str):
        raise ValueError("De sleutel moet tekst zijn.")
    value = value.strip()
    if not value:
        return None
    if not KEY_PATTERN.fullmatch(value):
        raise ValueError("Dit lijkt geen Anthropic-sleutel. Die begint met sk-ant- "
                         "(maak er een op console.anthropic.com).")
    return value


def _check_model(value):
    if not isinstance(value, str):
        raise ValueError("Het model moet tekst zijn.")
    value = value.strip()
    if not value:
        return None
    if not MODEL_PATTERN.fullmatch(value):
        raise ValueError("Ongeldige modelnaam (bijv. claude-opus-5-5).")
    return value


def _check_choice(value, choices, message):
    if not isinstance(value, str):
        raise ValueError(message)
    value = value.strip().lower()
    if not value:
        return None
    if value not in choices:
        raise ValueError(message)
    return value


def _make_private(path: Path):
    """Alleen de eigenaar mag het bestand lezen en schrijven (werkt niet op Windows; geeft niet)."""
    try:
        if os.name != "nt" and (path.stat().st_mode & 0o077):
            os.chmod(path, 0o600)
    except OSError:
        pass
