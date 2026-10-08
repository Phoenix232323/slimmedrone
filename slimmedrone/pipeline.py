"""De verwerkingslus: camera -> AI -> tekenen -> zoom -> JPEG voor de website.

Er draaien drie threads naast elkaar, zodat de livestream nooit op de AI hoeft te wachten:

    "verwerking"  elk camerabeeld:   verkleinen -> zoom -> laatste kaders tekenen -> JPEG
    "gezichten"   zo vaak als kan:   faces.recognize() + alerts.process_faces()
    "objecten"    zo vaak als kan:   objects.detect() (YOLO op de processor)

Gezichts- en objectherkenning zijn veel trager dan de camera: op een Raspberry Pi
kost YOLO al snel een halve seconde per beeld. Ze werken daarom steeds op het
nieuwste beeld, en de verwerkingslus tekent telkens hun laatste uitkomst. Zo blijft
de livestream vloeiend (zo snel als de camera); de kaders lopen soms iets achter.

Met de Raspberry Pi AI Camera (IMX500) is het nog beter: die herkent de objecten
zelf, in de camera, en levert ze bij elk beeld mee. Dan is de objecten-thread niet
nodig en passen de objectkaders altijd precies bij het beeld.

Niemand kijkt naar de livestream? Dan maken we maar 2 JPEG-beelden per seconde
(voor /api/snapshot): dat scheelt rekenkracht. De herkenning loopt gewoon door.
"""
import logging
import threading
import time
import unicodedata
from dataclasses import dataclass, field, replace

import cv2
import numpy as np

from .camera import FpsMeter
from .zoom import Zoom, crop

log = logging.getLogger(__name__)

COLOR_OBJECT = (255, 200, 40)    # BGR: lichtblauw
COLOR_KNOWN = (90, 220, 90)      # groen
COLOR_UNKNOWN = (70, 70, 240)    # rood
FONT = cv2.FONT_HERSHEY_SIMPLEX

VIEWER_TIMEOUT = 3.0       # zo lang na de laatste vraag om een beeld is er nog iemand aan het kijken
IDLE_JPEG_INTERVAL = 0.5   # niemand kijkt: dan elke halve seconde een JPEG is genoeg
AI_CAMERA_STALE = 1.0      # kaders van de AI Camera die ouder zijn dan dit (s) tonen we niet meer


@dataclass
class Snapshot:
    """Alles wat we op één moment weten. Gebruikt door de assistent en de website."""
    frame: np.ndarray | None = None          # camerabeeld zonder tekeningen
    detections: list = field(default_factory=list)
    faces: list = field(default_factory=list)
    zoom_rect: tuple | None = None
    time: float = 0.0


class _Latest:
    """Brievenbus voor één beeld. De verwerkingslus stopt het nieuwste beeld erin, een
    herkenningsthread haalt het eruit. Een ouder beeld wordt gewoon overschreven."""

    def __init__(self):
        self._cond = threading.Condition()
        self._frame = None
        self.count = 0  # hoeveel beelden er in totaal in zijn gestopt

    def put(self, frame):
        with self._cond:
            self._frame = frame
            self.count += 1
            self._cond.notify()

    def wait(self, after: int, timeout: float = 1.0):
        """Wacht tot er beeld nummer `after + 1` (of nieuwer) is. Geeft (nummer, beeld) of (after, None)."""
        with self._cond:
            if not self._cond.wait_for(lambda: self.count > after, timeout):
                return after, None
            return self.count, self._frame


class Pipeline:
    def __init__(self, cfg, camera, objects, faces, alerts):
        self.cfg = cfg
        self.camera = camera
        self.objects = objects
        self.faces = faces
        self.alerts = alerts
        self.zoom = Zoom()
        self._cond = threading.Condition()
        self._jpeg = None
        self._jpeg_id = 0
        self._snapshot = Snapshot()
        self._size = (0, 0)          # grootte van de beelden naar de website
        self._running = False
        self._options = {"kaders": True, "hud": True}
        self._last_viewer = 0.0      # wanneer iemand voor het laatst om een beeld vroeg
        # Brievenbussen voor de herkenningsthreads, en hun laatste uitkomst.
        self._for_objects = _Latest()
        self._for_faces = _Latest()
        self._detections = []
        self._faces = []
        # Tellers voor stats(): hoe vaak per seconde gebeurt iets?
        self._frames_meter = FpsMeter()
        self._objects_meter = FpsMeter()
        self._faces_meter = FpsMeter()

    def start(self) -> "Pipeline":
        self._running = True
        threading.Thread(target=self._run, name="verwerking", daemon=True).start()
        threading.Thread(target=self._detect_objects, name="objecten", daemon=True).start()
        threading.Thread(target=self._recognize_faces, name="gezichten", daemon=True).start()
        return self

    def stop(self):
        self._running = False

    @property
    def fps(self) -> float:
        """Beelden per seconde naar de website."""
        return self._frames_meter.value

    def snapshot(self) -> Snapshot:
        with self._cond:
            return self._snapshot

    def latest_jpeg(self):
        self._last_viewer = time.monotonic()
        with self._cond:
            return self._jpeg

    def wait_jpeg(self, last_id: int, timeout: float = 1.0):
        """Wacht op een nieuw beeld voor de livestream. Geeft (nummer, jpeg)."""
        self._last_viewer = time.monotonic()
        with self._cond:
            if self._jpeg_id == last_id:
                self._cond.wait(timeout)
            return self._jpeg_id, self._jpeg

    # -- weergave en statistiek -------------------------------------------------

    @property
    def options(self) -> dict:
        """Wat er in het beeld getekend wordt: kaders (objecten/gezichten) en hud (LIVE-regel, zoomtekst)."""
        return dict(self._options)

    def set_options(self, kaders: bool | None = None, hud: bool | None = None) -> dict:
        options = dict(self._options)
        if kaders is not None:
            options["kaders"] = bool(kaders)
        if hud is not None:
            options["hud"] = bool(hud)
        self._options = options  # in één keer vervangen, zodat de lus nooit een half geval ziet
        return dict(options)

    def stats(self) -> dict:
        """Hoe snel loopt alles? (per seconde)"""
        ai = self.camera.ai if self.camera.ai_active else None
        width, height = self._size
        return {
            "beeld_fps": round(self._frames_meter.value, 1),
            "camera_fps": round(self.camera.fps, 1),
            "objecten_fps": round((ai.fps if ai else self._objects_meter).value, 1),
            "gezichten_fps": round(self._faces_meter.value, 1),
            "breedte": int(width),
            "hoogte": int(height),
        }

    # -- de lus ------------------------------------------------------------

    def _run(self):
        last_id = -1
        last_frame_time = time.monotonic()
        last_placeholder = 0.0
        next_jpeg = 0.0
        last_ai_result = 0.0
        objects_from_camera = False
        last_error = 0.0

        while self._running:
            # Wacht (zonder steeds te kijken) tot de camera een nieuw beeld heeft.
            frame_id, frame, camera_detections = self.camera.wait_frame(last_id, timeout=0.5)
            now = time.monotonic()
            if frame is None or frame_id == last_id:
                last_id = frame_id  # (nog) geen beeld: de volgende keer echt wachten
                if now - last_frame_time > 2 and now - last_placeholder > 1:
                    self._publish(_placeholder(self.camera), Snapshot(time=time.time()), count=False)
                    last_placeholder = now
                continue
            last_id = frame_id
            last_frame_time = now

            try:
                frame, scale = self._resize(frame)
                if self.camera.ai_active:
                    # AI Camera: de objecten horen precies bij dit beeld.
                    objects_from_camera = True
                    if camera_detections is not None:
                        self._detections = _scaled(camera_detections, scale)
                        last_ai_result = now
                    elif now - last_ai_result > AI_CAMERA_STALE:
                        self._detections = []
                else:
                    if objects_from_camera:  # AI Camera is net gestopt: oude kaders weg
                        objects_from_camera = False
                        self._detections = []
                    if self.objects.available:
                        self._for_objects.put(frame)  # geef het beeld door aan de objectherkenning
                self._for_faces.put(frame)  # en aan de gezichtsherkenning
                detections, faces = self._detections, self._faces

                h, w = frame.shape[:2]
                rect = self.zoom.update(detections, w, h)
                snapshot = Snapshot(frame=frame, detections=detections, faces=faces,
                                    zoom_rect=self.zoom.target_rect(w, h), time=time.time())
                make_jpeg, next_jpeg = self._jpeg_plan(now, next_jpeg)
                if make_jpeg:
                    view = frame.copy() if rect is None else crop(frame, rect)
                    options = self._options
                    if options["kaders"]:
                        _draw(view, detections, faces, rect, w, h)
                    if options["hud"]:
                        _draw_hud(view, self.zoom.status())
                    self._publish(view, snapshot)
                else:
                    with self._cond:
                        self._snapshot = snapshot
            except Exception:
                if time.time() - last_error > 10:  # niet elke frame dezelfde fout loggen
                    log.exception("Fout bij het verwerken van een beeld")
                    last_error = time.time()
                time.sleep(0.1)

    def _jpeg_plan(self, now, next_jpeg):
        """Moet dit beeld een JPEG worden? Geeft (ja/nee, wanneer de volgende JPEG mag).

        Niet vaker dan STREAM_FPS (meer stuurt de website toch niet door), en als niemand
        kijkt maar 2 per seconde. We rekenen met een vast schema: is één beeld wat later
        klaar, dan wordt het volgende beeld niet overgeslagen (dat zou haperen).
        """
        if now - self._last_viewer > VIEWER_TIMEOUT:
            interval = IDLE_JPEG_INTERVAL  # niemand kijkt: af en toe is genoeg
        else:
            interval = 1.0 / max(1, self.cfg.stream_fps)
            next_jpeg = min(next_jpeg, now + interval)  # net iemand gaan kijken: meteen beginnen
        if now < next_jpeg - interval / 4:
            return False, next_jpeg
        return True, max(next_jpeg, now - interval / 2) + interval

    def _detect_objects(self):
        """Objectherkenning op het nieuwste beeld, telkens als er een klaar is.

        Na elke ronde wachten we tot er OBJECT_EVERY nieuwe beelden zijn, zodat
        er rekenkracht overblijft voor de livestream en de gezichtsherkenning.
        (Met de AI Camera krijgt deze thread geen beelden: die herkent zelf.)
        """
        done_at = 0
        last_error = 0.0
        while self._running:
            _, frame = self._for_objects.wait(done_at + self.cfg.object_every - 1, timeout=1)
            if frame is None:
                continue
            try:
                detections = self.objects.detect(frame)
                if not self.camera.ai_active:
                    self._detections = detections
                    self._objects_meter.tick()
            except Exception:
                if time.time() - last_error > 10:
                    log.exception("Fout bij de objectherkenning")
                    last_error = time.time()
                time.sleep(0.1)
            done_at = self._for_objects.count

    def _recognize_faces(self):
        """Gezichtsherkenning (en meldingen) op het nieuwste beeld, zo vaak als het lukt."""
        done_at = 0
        last_error = 0.0
        while self._running:
            done_at, frame = self._for_faces.wait(done_at, timeout=1)
            if frame is None:
                continue
            try:
                faces = self.faces.recognize(frame)
                self._faces = faces
                self._faces_meter.tick()
                self.alerts.process_faces(faces, frame)
            except Exception:
                if time.time() - last_error > 10:
                    log.exception("Fout bij de gezichtsherkenning")
                    last_error = time.time()
                time.sleep(0.1)

    def _resize(self, frame):
        """Verklein tot PROCESS_WIDTH. Geeft (beeld, schaal)."""
        h, w = frame.shape[:2]
        if w <= self.cfg.process_width:
            return frame, 1.0
        scale = self.cfg.process_width / w
        small = cv2.resize(frame, (self.cfg.process_width, int(h * scale)), interpolation=cv2.INTER_AREA)
        return small, scale

    def _publish(self, view, snapshot, count=True):
        ok, buf = cv2.imencode(".jpg", view, [cv2.IMWRITE_JPEG_QUALITY, self.cfg.jpeg_quality])
        if not ok:
            return
        with self._cond:
            self._jpeg = buf.tobytes()
            self._jpeg_id += 1
            self._snapshot = snapshot
            self._size = (view.shape[1], view.shape[0])
            self._cond.notify_all()
        if count:  # het "Geen camerabeeld"-plaatje tellen we niet mee
            self._frames_meter.tick()


def _scaled(detections, scale):
    """Kaders uit het camerabeeld omrekenen naar het verkleinde beeld."""
    if scale == 1.0:
        return detections
    return [replace(d, box=tuple(int(round(v * scale)) for v in d.box)) for d in detections]


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
