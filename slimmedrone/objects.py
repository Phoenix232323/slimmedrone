"""Objectherkenning: weet wat een hond, mens, tafel, auto... is.

Er zijn drie "motoren" die allemaal de 80 objectsoorten van de COCO-dataset kennen:

  * imx500 - de Raspberry Pi AI Camera herkent de objecten zelf, in de camera
             (zie imx500.py). Kost de processor van de Pi bijna niets.
  * yolo   - YOLO11 van Ultralytics (pip install ultralytics, gebruikt PyTorch).
             Het nauwkeurigst en snelst als PyTorch op je computer werkt.
  * opencv - YOLOX uit de OpenCV Model Zoo, draait op OpenCV zelf.
             Geen PyTorch nodig: lichter om te installeren (handig op de Pi).

OBJECT_BACKEND=auto kiest de AI Camera als die er is (met CAMERA_SOURCE=picamera),
anders yolo als dat werkt, en anders opencv. Stopt de AI Camera onderweg, dan
schakelt hij vanzelf over op yolo/opencv.
Het YOLO-model wordt de eerste keer automatisch gedownload naar data/models.
"""
import logging
import threading
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .downloads import OPENCV_ZOO, ensure_file
from .labels import LABELS, dutch

log = logging.getLogger(__name__)

COCO_CLASSES = list(LABELS)  # LABELS staat in dezelfde volgorde als COCO
YOLOX_FILE = "object_detection_yolox_2022nov.onnx"


@dataclass
class Detection:
    label: str           # Engelse COCO-naam, bijv. "dining table"
    naam: str            # Nederlandse naam, bijv. "tafel"
    confidence: float    # 0..1
    box: tuple           # (x1, y1, x2, y2) in pixels


class ObjectDetector:
    def __init__(self, backend: str, models_dir: Path, yolo_path: str, confidence: float = 0.45,
                 camera=None, imx500_model: str = ""):
        self.confidence = confidence
        self.models_dir = models_dir
        self.yolo_path = yolo_path
        self.backend = None
        self.error = ""
        self.ai_camera = None  # de AI Camera (IMX500), als die het werk doet
        self._impl = None
        backend = backend.lower()
        if backend in ("uit", "off", "none"):
            self.error = "uitgezet in de instellingen"
            return
        if backend in ("auto", "imx500"):
            self._start_ai_camera(backend, camera, imx500_model)
        if self.backend is None:
            self._load(backend)

    @property
    def available(self) -> bool:
        return self.backend is not None

    @property
    def description(self) -> str:
        return {"imx500": "AI Camera (IMX500, in de camera)", "yolo": "YOLO11 (Ultralytics)",
                "opencv": "YOLOX (OpenCV)"}.get(self.backend, "uit")

    def detect(self, frame) -> list:
        if self.backend in (None, "imx500"):
            return []  # bij de AI Camera komen de objecten met het camerabeeld mee
        detections = [
            Detection(label=label, naam=dutch(label), confidence=float(conf),
                      box=tuple(int(v) for v in box))
            for label, conf, box in self._impl.detect(frame)
        ]
        detections.sort(key=lambda d: d.confidence, reverse=True)
        return detections

    # -- motor kiezen --------------------------------------------------------

    def _start_ai_camera(self, backend, camera, model_path):
        """Probeer de AI Camera (IMX500). Lukt het niet, dan blijft self.backend None."""
        explicit = backend == "imx500"  # zelf gekozen: dan altijd laten weten waarom het niet lukt
        if camera is None or camera.source.lower() != "picamera":
            if explicit:
                log.warning("OBJECT_BACKEND=imx500 werkt alleen met CAMERA_SOURCE=picamera. "
                            "De objectherkenning draait nu op de processor.")
            return
        if camera.started:
            log.warning("De AI Camera moet klaar zijn vóórdat de camera start; we slaan hem over.")
            return
        from . import imx500  # pas hier: picamera2 bestaat alleen op de Raspberry Pi

        try:
            if not imx500.imx500_present():
                log.log(logging.WARNING if explicit else logging.INFO,
                        "Geen AI Camera (IMX500) gevonden; de objectherkenning draait op de processor.")
                return
            ai = imx500.AiCamera(model_path or imx500.DEFAULT_MODEL, self.confidence)
        except Exception as exc:
            self.error = f"AI Camera werkt niet: {exc}"
            log.warning("AI Camera werkt niet: %s. De objectherkenning draait nu op de processor.", exc)
            return
        ai.on_fail(self._ai_camera_failed)
        camera.use_ai_camera(ai)
        self.ai_camera = ai
        self.backend = "imx500"
        log.info("Objectherkenning: %s", self.description)

    def _ai_camera_failed(self, reason):
        """De AI Camera is gestopt: laad YOLO/YOLOX (in een aparte thread, de camera loopt door)."""
        self.backend = None
        self.error = f"AI Camera gestopt: {reason}"
        threading.Thread(target=self._load, args=("auto",), name="objecten-laden", daemon=True).start()

    def _load(self, backend):
        """Laad een motor die op de processor draait (YOLO11 of YOLOX)."""
        if backend == "imx500":
            backend = "auto"  # de AI Camera werkt niet: dan maar op de processor
        impl, name = None, None
        if backend in ("auto", "yolo"):
            try:
                impl, name = _UltralyticsBackend(self.yolo_path, self.confidence), "yolo"
            except Exception as exc:  # niet geïnstalleerd, of PyTorch wil niet laden
                self.error = f"YOLO/PyTorch werkt niet: {exc}"
                level = logging.INFO if backend == "auto" else logging.WARNING
                log.log(level, "%s", self.error)
        if impl is None and backend in ("auto", "opencv"):
            try:
                impl, name = _OpenCvYoloxBackend(self.models_dir, self.confidence), "opencv"
            except Exception as exc:
                self.error = f"YOLOX laden mislukt: {exc}"
        if impl is None and backend not in ("auto", "yolo", "opencv"):
            self.error = f"onbekende OBJECT_BACKEND '{backend}'"
        if impl is not None:
            self._impl = impl  # eerst de motor, dan pas zeggen dat hij klaar is
            self.backend = name
            self.error = ""
            log.info("Objectherkenning: %s", self.description)
        else:
            log.warning("Objectherkenning staat uit: %s", self.error)


class _UltralyticsBackend:
    def __init__(self, model_path: str, confidence: float):
        from ultralytics import YOLO

        Path(model_path).parent.mkdir(parents=True, exist_ok=True)
        self.model = YOLO(model_path)
        self.confidence = confidence

    def detect(self, frame):
        result = self.model(frame, conf=self.confidence, verbose=False)[0]
        for box, cls, conf in zip(result.boxes.xyxy.tolist(),
                                  result.boxes.cls.tolist(),
                                  result.boxes.conf.tolist()):
            yield result.names[int(cls)], conf, box


class _OpenCvYoloxBackend:
    """YOLOX-S (640x640) via cv2.dnn, naar het voorbeeld uit de OpenCV Model Zoo."""

    SIZE = 640
    STRIDES = (8, 16, 32)

    def __init__(self, models_dir: Path, confidence: float):
        path = ensure_file(models_dir / YOLOX_FILE, f"{OPENCV_ZOO}/object_detection_yolox/{YOLOX_FILE}")
        self.net = cv2.dnn.readNet(str(path))
        self.confidence = confidence
        grids, strides = [], []
        for stride in self.STRIDES:
            n = self.SIZE // stride
            xv, yv = np.meshgrid(np.arange(n), np.arange(n))
            grids.append(np.stack((xv, yv), 2).reshape(-1, 2))
            strides.append(np.full((n * n, 1), stride))
        self.grids = np.concatenate(grids).astype(np.float32)
        self.strides = np.concatenate(strides).astype(np.float32)

    def detect(self, frame):
        # Letterbox: verkleinen tot 640 breed/hoog en aanvullen met grijs (114).
        h, w = frame.shape[:2]
        ratio = min(self.SIZE / h, self.SIZE / w)
        resized = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB),
                             (int(w * ratio), int(h * ratio)), interpolation=cv2.INTER_LINEAR)
        padded = np.full((self.SIZE, self.SIZE, 3), 114, dtype=np.float32)
        padded[:resized.shape[0], :resized.shape[1]] = resized
        self.net.setInput(np.transpose(padded, (2, 0, 1))[np.newaxis])
        out = self.net.forward(self.net.getUnconnectedOutLayersNames())[0][0]

        # Uitvoer per vakje: middelpunt, breedte, hoogte, "is er iets", 80 klasse-kansen.
        centers = (out[:, :2] + self.grids) * self.strides
        sizes = np.exp(out[:, 2:4]) * self.strides
        scores_all = out[:, 4:5] * out[:, 5:]
        classes = np.argmax(scores_all, axis=1)
        scores = scores_all[np.arange(len(classes)), classes]
        keep = scores >= self.confidence
        if not np.any(keep):
            return
        centers, sizes, scores, classes = centers[keep], sizes[keep], scores[keep], classes[keep]
        boxes_xywh = np.concatenate([centers - sizes / 2, sizes], axis=1) / ratio
        indices = cv2.dnn.NMSBoxesBatched(boxes_xywh.tolist(), scores.tolist(), classes.tolist(),
                                          self.confidence, 0.5)
        for i in np.array(indices).flatten():
            x, y, bw, bh = boxes_xywh[i]
            box = (max(0, x), max(0, y), min(w, x + bw), min(h, y + bh))
            yield COCO_CLASSES[int(classes[i])], float(scores[i]), box
