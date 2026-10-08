"""De Raspberry Pi AI Camera (Sony IMX500): objectherkenning ín de camera zelf.

In de AI Camera zit naast de beeldsensor een kleine AI-chip. Die draait het neurale
netwerk op elk beeld en stuurt de uitkomst mee met dat beeld (in de "metadata").
Voordelen:
  * de processor van de Pi hoeft geen objectherkenning meer te doen, dus er blijft
    veel meer rekenkracht over voor de livestream en de gezichtsherkenning;
  * beeld en kaders horen altijd precies bij elkaar (geen kaders die achterlopen).

Het standaardmodel is SSD MobileNetV2 (320x320). Dat kent dezelfde objectsoorten als
YOLO (COCO) en zit in het pakket imx500-all:   sudo apt install imx500-all

Volgorde is belangrijk: eerst IMX500(model) (zet het netwerk in de camera), pas daarna
Picamera2. Daarom maakt app.py de objectherkenning vóórdat de camera start.

Lukt iets niet (geen AI Camera, model ontbreekt, rare uitvoer), dan schrijven we één
duidelijke regel in de log en gaat de objectherkenning verder op de processor.
"""
import logging
import os
import time

import numpy as np

from .camera import FpsMeter
from .labels import COCO90, coco_key, dutch
from .objects import Detection

log = logging.getLogger(__name__)

DEFAULT_MODEL = "/usr/share/imx500-models/imx500_network_ssd_mobilenetv2_fpnlite_320x320_pp.rpk"
INSTALL_HINT = "sudo apt install -y imx500-all en herstart daarna de Pi één keer"
REINSTALL_HINT = "herstart de Pi; helpt dat niet, dan: sudo apt install --reinstall -y imx500-all"

MAX_DETECTIONS = 30          # meer kaders tegelijk heeft geen zin
FIRST_OUTPUT_TIMEOUT = 180   # seconden: zo lang mag het laden van het netwerk in de camera duren
OUTPUT_TIMEOUT = 20          # seconden: daarna moet er steeds uitvoer blijven komen
MAX_BAD_FRAMES = 90          # zoveel beelden achter elkaar onbruikbare uitvoer (3 s) = opgeven


class AiCameraError(Exception):
    """De AI Camera werkt niet. De tekst legt uit waarom en wat je eraan kunt doen."""

    def __init__(self, reason: str, hint: str = ""):
        super().__init__(reason)
        self.reason = reason
        self.hint = hint

    def __str__(self):
        return f"{self.reason}. Oplossing: {self.hint}" if self.hint else self.reason


def imx500_present() -> bool:
    """Is er een AI Camera (IMX500) aangesloten? Dat vragen we aan picamera2."""
    from picamera2 import Picamera2  # alleen aanwezig op de Raspberry Pi

    return any(info.get("Model") == "imx500" for info in Picamera2.global_camera_info())


class AiCamera:
    """Het neurale netwerk in de AI Camera, plus het uitlezen van de herkenningen."""

    def __init__(self, model_path: str, confidence: float = 0.45):
        name = os.path.basename(model_path)
        if not os.path.isfile(model_path):
            raise AiCameraError(f"het model {model_path} bestaat niet", INSTALL_HINT)
        try:
            from picamera2.devices.imx500 import IMX500, NetworkIntrinsics
        except Exception as exc:  # bijv. een ontbrekend pakket van picamera2
            raise AiCameraError(f"de AI Camera-onderdelen van picamera2 ontbreken ({exc})",
                                "sudo apt update && sudo apt install -y python3-picamera2 imx500-all") from exc
        try:
            # Dit zet het netwerk (de "firmware") in de camera. Moet vóór Picamera2().
            self.imx500 = IMX500(model_path)
        except Exception as exc:
            raise AiCameraError(f"de AI Camera kon het netwerk {name} niet laden ({exc})",
                                REINSTALL_HINT) from exc

        # "Intrinsics": wat het model over zichzelf vertelt (soort taak, labels, kaders...).
        intrinsics = self.imx500.network_intrinsics
        if not intrinsics:
            intrinsics = NetworkIntrinsics()
            intrinsics.task = "object detection"
        if intrinsics.task not in (None, "object detection"):
            raise AiCameraError(f"het model {name} doet geen objectherkenning maar '{intrinsics.task}'",
                                "maak IMX500_MODEL leeg (dan nemen we het standaardmodel)")
        if intrinsics.postprocess:
            # Bijv. "nanodet": dan moet de processor nog veel narekenen. Dat doen we niet.
            raise AiCameraError(f"het model {name} heeft nabewerking '{intrinsics.postprocess}' nodig "
                                "en die ondersteunen we niet",
                                "maak IMX500_MODEL leeg (dan nemen we het standaardmodel)")
        intrinsics.update_with_defaults()

        self.confidence = confidence
        self.camera_num = self.imx500.camera_num
        self.inference_rate = float(intrinsics.inference_rate or 30)
        self.input_size = self.imx500.get_input_size()  # (breedte, hoogte) van het netwerk, bijv. 320x320
        self._bbox_normalization = bool(intrinsics.bbox_normalization)
        self._bbox_order = intrinsics.bbox_order or "yx"
        self._preserve_aspect_ratio = bool(intrinsics.preserve_aspect_ratio)

        # Klassenummer -> onze COCO-naam (None = overslaan, bijv. "-").
        names = intrinsics.labels or COCO90
        if intrinsics.ignore_dash_labels:
            names = [n for n in names if n and n != "-"]
        self._labels = [coco_key(n) for n in names]
        unknown = sorted({n for n in names if n and n != "-" and coco_key(n) is None})
        if unknown:
            log.info("AI Camera: %d labels van dit model kennen we niet en slaan we over (bijv. %s)",
                     len(unknown), ", ".join(unknown[:5]))

        self.active = True    # wordt False als de AI Camera het opgeeft
        self.error = ""
        self.outputs = 0      # aantal beelden met herkenningen (voor tests en statistiek)
        self.fps = FpsMeter()  # herkenningen per seconde
        self._on_fail = []
        self._started_at = None
        self._last_output = None
        self._next_wait_log = 0.0
        self._bad_frames = 0
        self._last_error_log = 0.0
        log.info("AI Camera: model %s geladen (%dx%d, %g herkenningen/s)",
                 name, self.input_size[0], self.input_size[1], self.inference_rate)

    def on_fail(self, callback):
        """callback(reden) wordt aangeroepen als de AI Camera het opgeeft."""
        self._on_fail.append(callback)

    def frame_rate(self, wanted: float) -> float:
        """Sneller filmen dan het netwerk kan rekenen heeft geen zin."""
        return min(wanted, self.inference_rate)

    def started(self, picam2):
        """Roep aan direct na picam2.start()."""
        if self._preserve_aspect_ratio:
            try:
                self.imx500.set_auto_aspect_ratio()  # het netwerk wil een vierkant stuk van het beeld
            except Exception as exc:
                log.warning("AI Camera: beeldverhouding instellen mislukt (%s)", exc)
        self._started_at = time.monotonic()
        self._last_output = None
        self._next_wait_log = self._started_at + 30
        log.info("AI Camera: het netwerk wordt in de camera geladen (de eerste keer kan dat even duren)...")

    def parse(self, metadata: dict, picam2):
        """Lees de herkende objecten uit de metadata van één beeld.

        Geeft een lijst Detections in pixels van het camerabeeld, of None als dit beeld
        geen uitvoer heeft (bijvoorbeeld omdat het netwerk nog geladen wordt).
        """
        if not self.active:
            return None
        now = time.monotonic()
        try:
            outputs = self.imx500.get_outputs(metadata, add_batch=True)
            if outputs is None:
                self._no_output(now)
                return None
            detections = self._detections(outputs, metadata, picam2)
        except Exception as exc:  # rare uitvoer: het beeld moet gewoon doorlopen
            self._bad_output(exc, now)
            return None
        if self._last_output is None:
            log.info("AI Camera: objectherkenning in de camera werkt (na %.0f s)",
                     now - (self._started_at or now))
        self._last_output = now
        self._bad_frames = 0
        self.outputs += 1
        self.fps.tick()
        return detections

    def fail(self, reason: str, hint: str = ""):
        """Geef de AI Camera op: de objectherkenning gaat dan verder op de processor."""
        if not self.active:
            return
        self.active = False
        self.error = reason
        log.warning("AI Camera gestopt: %s. De objectherkenning gaat verder op de processor.%s",
                    reason, f" Oplossing: {hint}" if hint else "")
        for callback in self._on_fail:
            try:
                callback(reason)
            except Exception:
                log.exception("Fout bij het overschakelen naar objectherkenning op de processor")

    # -- intern -----------------------------------------------------------

    def _detections(self, outputs, metadata, picam2) -> list:
        # Het SSD-model geeft: kaders, zekerheden, klassenummers (en het aantal).
        if len(outputs) < 3:
            raise ValueError(f"{len(outputs)} uitvoer-tensors, verwacht minstens 3")
        boxes = np.asarray(outputs[0][0], dtype=np.float32)
        scores = np.asarray(outputs[1][0], dtype=np.float32).reshape(-1)
        classes = np.asarray(outputs[2][0], dtype=np.float32).reshape(-1)
        if boxes.ndim != 2 or boxes.shape[1] != 4 or not len(boxes) == len(scores) == len(classes):
            raise ValueError(f"onverwachte vormen {boxes.shape}, {scores.shape}, {classes.shape}")
        if self._bbox_normalization:  # kaders in pixels van het netwerk -> 0..1
            boxes = boxes / self.input_size[1]
        if self._bbox_order == "xy":  # wij willen (y0, x0, y1, x1)
            boxes = boxes[:, [1, 0, 3, 2]]

        good = (scores >= self.confidence) & np.isfinite(boxes).all(axis=1) & np.isfinite(classes)
        best_first = np.flatnonzero(good)
        best_first = best_first[np.argsort(-scores[best_first])][:MAX_DETECTIONS]

        detections = []
        for i in best_first:
            label = self._label(classes[i])
            if label is None:
                continue
            y0, x0, y1, x1 = (float(v) for v in np.clip(boxes[i], 0.0, 1.0))
            if x1 <= x0 or y1 <= y0:
                continue
            # Van "deel van de hele sensor" naar pixels in ons camerabeeld (picamera2 rekent
            # zelf uit welk stuk van de sensor in beeld is).
            x, y, w, h = self.imx500.convert_inference_coords((y0, x0, y1, x1), metadata, picam2)
            if w <= 0 or h <= 0:
                continue  # valt buiten het beeld
            detections.append(Detection(label=label, naam=dutch(label), confidence=float(scores[i]),
                                        box=(int(x), int(y), int(x + w), int(y + h))))
        return detections

    def _label(self, class_id):
        i = int(class_id)
        return self._labels[i] if 0 <= i < len(self._labels) else None

    def _no_output(self, now):
        started = self._started_at or now
        if self._last_output is None:
            if now - started > FIRST_OUTPUT_TIMEOUT:
                self.fail(f"na {FIRST_OUTPUT_TIMEOUT} seconden nog geen herkenningen uit de camera",
                          REINSTALL_HINT)
            elif now >= self._next_wait_log:
                log.info("AI Camera: het netwerk wordt nog geladen (%.0f s)...", now - started)
                self._next_wait_log = now + 30
        elif now - self._last_output > OUTPUT_TIMEOUT:
            self.fail(f"de camera geeft al {OUTPUT_TIMEOUT} seconden geen herkenningen meer", REINSTALL_HINT)

    def _bad_output(self, exc, now):
        self._bad_frames += 1
        if now - self._last_error_log > 10:  # niet elk beeld dezelfde melding
            log.warning("AI Camera: onverwachte uitvoer van het netwerk (%s). Het beeld loopt gewoon door.", exc)
            self._last_error_log = now
        if self._bad_frames >= MAX_BAD_FRAMES:
            self.fail(f"{MAX_BAD_FRAMES} beelden achter elkaar onbruikbare uitvoer ({exc})",
                      "maak IMX500_MODEL leeg (dan nemen we het standaardmodel)")
