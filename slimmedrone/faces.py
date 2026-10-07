"""Gezichtsherkenning met OpenCV.

Twee kleine neurale netwerken uit de OpenCV Model Zoo:
  * YuNet  - vindt gezichten in het beeld (detectie)
  * SFace  - maakt van elk gezicht een "vingerafdruk" van 128 getallen (embedding)

Twee foto's van dezelfde persoon geven vingerafdrukken die op elkaar lijken.
We vergelijken elk gezicht in het beeld met de gezichten die jullie zelf in
de database hebben gezet (map data/gezichten/<naam>/).
"""
import logging
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .downloads import OPENCV_ZOO, ensure_file

log = logging.getLogger(__name__)

YUNET_FILE = "face_detection_yunet_2023mar.onnx"
SFACE_FILE = "face_recognition_sface_2021dec.onnx"

NAME_PATTERN = re.compile(r"[\w\- ]{1,40}")


def clean_name(name: str) -> str:
    name = " ".join((name or "").split())
    if not NAME_PATTERN.fullmatch(name):
        raise ValueError("Gebruik een naam van 1-40 letters, cijfers, spaties of streepjes.")
    return name


@dataclass
class Face:
    box: tuple            # (x1, y1, x2, y2) in pixels
    score: float          # hoe zeker YuNet is dat het een gezicht is
    name: str | None      # None = onbekend gezicht
    similarity: float     # hoe goed het lijkt op de beste match (cosinus, -1..1)


class FaceEngine:
    def __init__(self, models_dir: Path, faces_dir: Path,
                 detect_threshold: float = 0.8, match_threshold: float = 0.363):
        yunet = ensure_file(models_dir / YUNET_FILE,
                            f"{OPENCV_ZOO}/face_detection_yunet/{YUNET_FILE}")
        sface = ensure_file(models_dir / SFACE_FILE,
                            f"{OPENCV_ZOO}/face_recognition_sface/{SFACE_FILE}")
        self.match_threshold = match_threshold
        self.faces_dir = faces_dir
        self.detector = cv2.FaceDetectorYN.create(str(yunet), "", (320, 320),
                                                  detect_threshold, 0.3, 5000)
        self.recognizer = cv2.FaceRecognizerSF.create(str(sface), "")
        # De netwerken zijn niet thread-safe: de live-verwerking en het
        # toevoegen van gezichten via de website wisselen elkaar af.
        self._lock = threading.Lock()
        # (namen, vingerafdrukken) - altijd samen vervangen, zodat ze bij elkaar passen.
        self._db = ([], np.zeros((0, 128), dtype=np.float32))
        self.reload()

    # -- herkenning --------------------------------------------------------

    def detect(self, image) -> np.ndarray:
        """Ruwe YuNet-resultaten: per gezicht 15 getallen (kader, ogen, neus, mond, score)."""
        h, w = image.shape[:2]
        with self._lock:
            self.detector.setInputSize((w, h))
            _, rows = self.detector.detect(image)
        return rows if rows is not None else np.zeros((0, 15), dtype=np.float32)

    def embed(self, image, row):
        """Lijn het gezicht recht (112x112) en bereken de vingerafdruk."""
        with self._lock:
            aligned = self.recognizer.alignCrop(image, row)
            feature = self.recognizer.feature(aligned).flatten()
        return aligned, feature / (np.linalg.norm(feature) + 1e-9)

    def recognize(self, image) -> list:
        h, w = image.shape[:2]
        faces = []
        for row in self.detect(image):
            x, y, fw, fh = row[:4]
            box = (max(0, int(x)), max(0, int(y)), min(w, int(x + fw)), min(h, int(y + fh)))
            _, feature = self.embed(image, row)
            name, similarity = self._match(feature)
            faces.append(Face(box=box, score=float(row[14]), name=name, similarity=similarity))
        return faces

    def _match(self, feature):
        names, embeddings = self._db
        if not names:
            return None, 0.0
        similarities = embeddings @ feature
        best = int(np.argmax(similarities))
        score = float(similarities[best])
        if score >= self.match_threshold:
            return names[best], score
        return None, score

    # -- database ----------------------------------------------------------

    def reload(self):
        """Lees alle opgeslagen gezichten in en bereken hun vingerafdrukken."""
        names, features = [], []
        if self.faces_dir.exists():
            for person in sorted(p for p in self.faces_dir.iterdir() if p.is_dir()):
                for photo in sorted(person.glob("*.jpg")):
                    aligned = cv2.imread(str(photo))
                    if aligned is None:
                        continue
                    with self._lock:
                        feature = self.recognizer.feature(aligned).flatten()
                    names.append(person.name)
                    features.append(feature / (np.linalg.norm(feature) + 1e-9))
        embeddings = (np.array(features, dtype=np.float32)
                      if features else np.zeros((0, 128), dtype=np.float32))
        self._db = (names, embeddings)
        log.info("Gezichtsdatabase: %d foto's van %d personen", len(names), len(set(names)))

    def people(self) -> list:
        result = []
        if self.faces_dir.exists():
            for person in sorted(p for p in self.faces_dir.iterdir() if p.is_dir()):
                count = len(list(person.glob("*.jpg")))
                if count:
                    result.append({"naam": person.name, "fotos": count})
        return result

    def photo_path(self, name: str):
        folder = self.faces_dir / clean_name(name)
        photos = sorted(folder.glob("*.jpg")) if folder.is_dir() else []
        return photos[-1] if photos else None

    def enroll(self, name: str, image, single: bool = False) -> np.ndarray:
        """Sla het gezicht in de foto op onder deze naam.

        Bij single=True moet er precies één gezicht te zien zijn (live camera),
        anders nemen we het grootste gezicht (geüploade foto's).
        """
        name = clean_name(name)
        if image is None:
            raise ValueError("Dit bestand is geen geldige foto.")
        scale = 1280 / max(image.shape[:2])
        if scale < 1:  # grote telefoonfoto's eerst verkleinen
            image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        rows = self.detect(image)
        if len(rows) == 0:
            raise ValueError("Geen gezicht gevonden. Kijk recht in de camera, met goed licht.")
        if single and len(rows) > 1:
            raise ValueError(f"Er zijn {len(rows)} gezichten in beeld. Zorg dat alleen deze persoon "
                             "in beeld is, of zoom eerst in op het juiste gezicht.")
        row = max(rows, key=lambda r: r[2] * r[3])
        aligned, _ = self.embed(image, row)
        folder = self.faces_dir / name
        folder.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(folder / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}.jpg"), aligned)
        self.reload()
        return aligned

    def remove(self, name: str):
        folder = self.faces_dir / clean_name(name)
        if folder.is_dir():
            shutil.rmtree(folder)
        self.reload()
