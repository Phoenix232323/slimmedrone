"""De verwerkingslus: camera -> AI -> tekenen -> zoom -> JPEG voor de website.

    camera.read()                      nieuwste camerabeeld
      -> faces.recognize()             wie is er te zien?
      -> alerts.process_faces()        meldingen maken
      -> zoom.update() + tekenen       uitsnede + kaders en namen
      -> JPEG                          naar iedereen die de livestream bekijkt

Objectherkenning (YOLO) is veel trager dan de rest: op een Raspberry Pi kost
één beeld al snel een halve seconde. Daarom draait die in een eigen thread op
het nieuwste beeld, en tekent de lus hierboven steeds de laatste uitkomst.
Zo blijft de livestream vloeiend; de objectkaders lopen soms iets achter.
"""
import logging
import threading
import time
import unicodedata
from dataclasses import dataclass, field

import cv2
import numpy as np

from .zoom import Zoom, crop

log = logging.getLogger(__name__)

COLOR_OBJECT = (255, 200, 40)    # BGR: lichtblauw
COLOR_KNOWN = (90, 220, 90)      # groen
COLOR_UNKNOWN = (70, 70, 240)    # rood
FONT = cv2.FONT_HERSHEY_SIMPLEX


@dataclass
class Snapshot:
    """Alles wat we op één moment weten. Gebruikt door de assistent en de website."""
    frame: np.ndarray | None = None          # camerabeeld zonder tekeningen
    detections: list = field(default_factory=list)
    faces: list = field(default_factory=list)
    zoom_rect: tuple | None = None
    time: float = 0.0


class Pipeline:
    def __init__(self, cfg, camera, objects, faces, alerts):
        self.cfg = cfg
        self.camera = camera
        self.objects = objects
        self.faces = faces
        self.alerts = alerts
        self.zoom = Zoom()
        self.fps = 0.0
        self._cond = threading.Condition()
        self._jpeg = None
        self._jpeg_id = 0
        self._snapshot = Snapshot()
        self._running = False
        # Voor de objectherkenning: het nieuwste beeld en de laatste uitkomst.
        self._work = threading.Condition()
        self._work_frame = None
        self._frame_count = 0
        self._detections = []

    def start(self) -> "Pipeline":
        self._running = True
        threading.Thread(target=self._run, name="verwerking", daemon=True).start()
        if self.objects.available:
            threading.Thread(target=self._detect_objects, name="objecten", daemon=True).start()
        return self

    def stop(self):
        self._running = False

    def snapshot(self) -> Snapshot:
        with self._cond:
            return self._snapshot

    def latest_jpeg(self):
        with self._cond:
            return self._jpeg

    def wait_jpeg(self, last_id: int, timeout: float = 1.0):
        """Wacht op een nieuw beeld voor de livestream. Geeft (nummer, jpeg)."""
        with self._cond:
            if self._jpeg_id == last_id:
                self._cond.wait(timeout)
            return self._jpeg_id, self._jpeg

    # -- de lus ------------------------------------------------------------

    def _run(self):
        last_frame_id = -1
        last_frame_time = time.time()
        last_placeholder = 0.0
        fps_start, fps_frames = time.time(), 0
        last_error = 0.0

        while self._running:
            frame_id, frame = self.camera.read()
            if frame is None or frame_id == last_frame_id:
                now = time.time()
                if now - last_frame_time > 2 and now - last_placeholder > 1:
                    self._publish(_placeholder(self.camera), Snapshot(time=now))
                    last_placeholder = now
                    self.fps = 0.0
                time.sleep(0.005)
                continue
            last_frame_id = frame_id
            last_frame_time = time.time()

            try:
                frame = self._resize(frame)
                with self._work:  # geef het beeld door aan de objectherkenning
                    self._work_frame = frame
                    self._frame_count += 1
                    self._work.notify()
                detections = self._detections
                faces = self.faces.recognize(frame)
                self.alerts.process_faces(faces, frame)

                h, w = frame.shape[:2]
                rect = self.zoom.update(detections, w, h)
                view = frame.copy() if rect is None else crop(frame, rect)
                _draw(view, detections, faces, rect, w, h)
                _draw_hud(view, self.zoom.status())
                self._publish(view, Snapshot(frame=frame, detections=detections, faces=faces,
                                             zoom_rect=self.zoom.target_rect(w, h),
                                             time=time.time()))
            except Exception:
                if time.time() - last_error > 10:  # niet elke frame dezelfde fout loggen
                    log.exception("Fout bij het verwerken van een beeld")
                    last_error = time.time()
                time.sleep(0.1)
                continue

            fps_frames += 1
            elapsed = time.time() - fps_start
            if elapsed >= 1:
                self.fps = fps_frames / elapsed
                fps_start, fps_frames = time.time(), 0

    def _detect_objects(self):
        """Objectherkenning op het nieuwste beeld, telkens als er een klaar is.

        Na elke ronde wachten we tot er OBJECT_EVERY nieuwe beelden zijn, zodat
        er rekenkracht overblijft voor de livestream en de gezichtsherkenning.
        """
        done_at = 0
        last_error = 0.0
        while self._running:
            with self._work:
                if not self._work.wait_for(
                        lambda: self._frame_count >= done_at + self.cfg.object_every, timeout=1):
                    continue
                frame = self._work_frame
            try:
                self._detections = self.objects.detect(frame)
            except Exception:
                if time.time() - last_error > 10:
                    log.exception("Fout bij de objectherkenning")
                    last_error = time.time()
                time.sleep(0.1)
            with self._work:
                done_at = self._frame_count

    def _resize(self, frame):
        h, w = frame.shape[:2]
        if w <= self.cfg.process_width:
            return frame
        scale = self.cfg.process_width / w
        return cv2.resize(frame, (self.cfg.process_width, int(h * scale)), interpolation=cv2.INTER_AREA)

    def _publish(self, view, snapshot):
        ok, buf = cv2.imencode(".jpg", view, [cv2.IMWRITE_JPEG_QUALITY, self.cfg.jpeg_quality])
        if not ok:
            return
        with self._cond:
            self._jpeg = buf.tobytes()
            self._jpeg_id += 1
            self._snapshot = snapshot
            self._cond.notify_all()


# -- tekenen ---------------------------------------------------------------

def _ascii(text: str) -> str:
    """OpenCV kan alleen gewone letters tekenen: 'Zoë' wordt 'Zoe'."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def _label(img, text, x, y, color):
    text = _ascii(text)
    (tw, th), base = cv2.getTextSize(text, FONT, 0.5, 1)
    y = max(th + 6, y)
    cv2.rectangle(img, (x, y - th - 6), (x + tw + 8, y), color, -1)
    cv2.putText(img, text, (x + 4, y - 4), FONT, 0.5, (15, 15, 15), 1, cv2.LINE_AA)


def _draw(img, detections, faces, rect, width, height):
    # Coördinaten van het hele beeld omrekenen naar de (ingezoomde) weergave.
    x0, y0, x1, _ = rect or (0, 0, width, height)
    scale = width / (x1 - x0)

    def tf(box):
        return tuple(int((v - (x0 if i % 2 == 0 else y0)) * scale) for i, v in enumerate(box))

    for d in detections:
        bx1, by1, bx2, by2 = tf(d.box)
        cv2.rectangle(img, (bx1, by1), (bx2, by2), COLOR_OBJECT, 2)
        _label(img, f"{d.naam} {d.confidence:.0%}", bx1, by1, COLOR_OBJECT)

    for f in faces:
        color = COLOR_KNOWN if f.name else COLOR_UNKNOWN
        bx1, by1, bx2, by2 = tf(f.box)
        cv2.rectangle(img, (bx1, by1), (bx2, by2), color, 2)
        _label(img, f.name or "Onbekend", bx1, by2 + 22, color)


def _draw_hud(img, zoom_status):
    h, w = img.shape[:2]
    stamp = time.strftime("%d-%m-%Y %H:%M:%S")
    cv2.circle(img, (18, 20), 6, (60, 60, 235), -1)
    cv2.putText(img, f"LIVE  {stamp}", (32, 26), FONT, 0.55, (255, 255, 255), 2, cv2.LINE_AA)
    if zoom_status.get("actief"):
        text = _ascii(f"ZOOM {zoom_status['factor']}x  {zoom_status['naam']}")
        cv2.putText(img, text, (12, h - 14), FONT, 0.6, (255, 255, 255), 2, cv2.LINE_AA)


def _placeholder(camera):
    img = np.full((540, 960, 3), (28, 20, 14), dtype=np.uint8)
    cv2.putText(img, "Geen camerabeeld", (300, 250), FONT, 1.2, (255, 200, 40), 2, cv2.LINE_AA)
    detail = _ascii(f"Bron: {camera.source}  -  {camera.error or 'verbinden...'}")[:90]
    cv2.putText(img, detail, (40, 310), FONT, 0.55, (200, 200, 200), 1, cv2.LINE_AA)
    return img
