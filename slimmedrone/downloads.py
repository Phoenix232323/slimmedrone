"""Modellen downloaden (eenmalig) naar data/models."""
import logging
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

OPENCV_ZOO = "https://github.com/opencv/opencv_zoo/raw/main/models"


def ensure_file(path: Path, url: str, min_size: int = 100_000) -> Path:
    if path.exists() and path.stat().st_size >= min_size:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    log.info("Downloaden: %s", path.name)
    tmp = path.with_name(path.name + ".part")
    urllib.request.urlretrieve(url, tmp)
    tmp.replace(path)
    return path
