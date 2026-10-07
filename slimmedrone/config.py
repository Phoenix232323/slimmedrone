"""Instellingen van de SlimmeDrone.

Alles staat in het bestand .env (kopieer .env.example). Zo hoef je de code
niet aan te passen om bijvoorbeeld een andere camera te kiezen.
"""
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Config:
    camera_source: str
    camera_width: int
    camera_height: int
    process_width: int

    object_backend: str
    yolo_model: str
    object_confidence: float
    object_every: int

    face_detect_threshold: float
    face_match_threshold: float

    alert_cooldown: float

    host: str
    port: int
    stream_fps: int
    jpeg_quality: int

    ai_enabled: bool
    claude_model: str
    claude_effort: str

    data_dir: Path

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def faces_dir(self) -> Path:
        return self.data_dir / "gezichten"

    @property
    def users_file(self) -> Path:
        return self.data_dir / "gebruikers.json"

    @property
    def yolo_path(self) -> str:
        # Een losse naam zoals "yolo11n.pt" komt in data/models terecht;
        # een pad (bijv. een geëxporteerd NCNN-model) gebruiken we zoals het is.
        if os.path.dirname(self.yolo_model):
            return self.yolo_model
        return str(self.models_dir / self.yolo_model)

    def secret_key(self) -> str:
        """Sleutel waarmee de inlogcookies ondertekend worden."""
        key = os.getenv("SECRET_KEY")
        if key:
            return key
        path = self.data_dir / "secret_key"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(secrets.token_hex(32))
        return path.read_text().strip()


def _bool(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "ja", "aan", "yes", "on")


def load_config() -> Config:
    load_dotenv(ROOT / ".env")
    env = os.getenv

    ai_setting = env("JARVIS_AI", "auto").strip().lower()
    if ai_setting == "auto":
        ai_enabled = bool(env("ANTHROPIC_API_KEY"))
    else:
        ai_enabled = _bool(ai_setting)

    return Config(
        camera_source=env("CAMERA_SOURCE", "0"),
        camera_width=int(env("CAMERA_WIDTH", "1280")),
        camera_height=int(env("CAMERA_HEIGHT", "720")),
        process_width=int(env("PROCESS_WIDTH", "960")),
        object_backend=env("OBJECT_BACKEND", "auto"),
        yolo_model=env("YOLO_MODEL", "yolo11n.pt"),
        object_confidence=float(env("OBJECT_CONFIDENCE", "0.45")),
        object_every=max(1, int(env("OBJECT_EVERY", "1"))),
        face_detect_threshold=float(env("FACE_DETECT_THRESHOLD", "0.8")),
        face_match_threshold=float(env("FACE_MATCH_THRESHOLD", "0.363")),
        alert_cooldown=float(env("ALERT_COOLDOWN", "30")),
        host=env("HOST", "0.0.0.0"),
        port=int(env("PORT", "5000")),
        stream_fps=int(env("STREAM_FPS", "20")),
        jpeg_quality=int(env("JPEG_QUALITY", "80")),
        ai_enabled=ai_enabled,
        claude_model=env("CLAUDE_MODEL", "claude-opus-5-5"),
        claude_effort=env("CLAUDE_EFFORT", "low"),
        data_dir=Path(env("DATA_DIR", str(ROOT / "data"))).resolve(),
    )
