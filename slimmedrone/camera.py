"""Camerabronnen: webcam, Raspberry Pi-camera, netwerkstream of testbestand.

De camera leest in een eigen thread, zodat de rest van het programma altijd
het nieuwste beeld krijgt en nooit hoeft te wachten. Komt er een nieuw beeld
binnen, dan krijgen de wachtende threads een seintje (wait_frame), zodat niemand
steeds hoeft te kijken of er al iets nieuws is.

CAMERA_SOURCE kan zijn:
  0, 1, ...                    een webcam (USB of ingebouwd)
  picamera                     de Raspberry Pi Camera Module of AI Camera (picamera2)
  http://.../stream.mjpg       een netwerkstream, bijv. van pi_stream.py
  rtsp://...                   een IP-camera of drone met RTSP
  pad/naar/video.mp4           een video (speelt in een lus, handig om te testen)
  pad/naar/foto.jpg            een foto (handig om te testen zonder camera)

CAMERA_FPS (standaard 30) is hoeveel beelden per seconde we aan de camera vragen.
60 kan alleen als de camera het kan; de AI Camera (IMX500) haalt maximaal 30.
"""
import logging
import os
import threading
import time

import cv2

log = logging.getLogger(__name__)

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp", ".webp")


class FpsMeter:
    """Telt hoe vaak iets per seconde gebeurt (gemiddeld over ongeveer een seconde)."""

    def __init__(self):
        self._start = time.monotonic()
        self._count = 0
        self._last = 0.0
        self._value = 0.0

    def tick(self):
        now = time.monotonic()
        if now - self._last > 10:  # lang niets gebeurd: opnieuw beginnen met tellen
            self._start, self._count = now, 0
        self._count += 1
        self._last = now
        if now - self._start >= 1.0:
            self._value = self._count / (now - self._start)
            self._start, self._count = now, 0

    @property
    def value(self) -> float:
        # Al een tijd niets gebeurd? Dan is het nu 0 per seconde.
        quiet = time.monotonic() - self._last
        if quiet > max(2.0, 3.0 / self._value if self._value else 2.0):
            return 0.0
        return self._value


class Camera:
    def __init__(self, source: str, width: int = 1280, height: int = 720, fps: int = 30):
        self.source = str(source).strip()
        self.width = width
        self.height = height
        self.wanted_fps = max(1, int(fps))
        self.connected = False
        self.error = ""
        self.ai = None  # de AI Camera (IMX500), als die de objectherkenning doet
        self._frame = None
        self._detections = None
        self._frame_id = 0
        self._cond = threading.Condition()
        self._running = False
        self._meter = FpsMeter()

    def use_ai_camera(self, ai):
        """Laat de AI Camera (IMX500) bij elk beeld de herkende objecten meeleveren.

        Moet vóór start(): het netwerk moet in de camera staan voordat picamera2 begint.
        """
        if self._running:
            raise RuntimeError("use_ai_camera() moet vóór camera.start()")
        self.ai = ai

    @property
    def ai_active(self) -> bool:
        """True als de objecten op dit moment uit de AI Camera komen."""
        return self.ai is not None and self.ai.active

    @property
    def started(self) -> bool:
        return self._running

    @property
    def fps(self) -> float:
        """Gemeten aantal beelden per seconde dat de camera echt levert."""
        return self._meter.value

    def start(self) -> "Camera":
        self._running = True
        threading.Thread(target=self._run, name="camera", daemon=True).start()
        return self

    def stop(self):
        self._running = False

    def read(self):
        """Geeft (beeldnummer, beeld). Het beeld is None zolang er nog niets binnen is."""
        with self._cond:
            return self._frame_id, self._frame

    def wait_frame(self, last_id: int, timeout: float = 1.0):
        """Wacht op een beeld dat nieuwer is dan last_id. Geeft (nummer, beeld, objecten).

        objecten is alleen bij de AI Camera gevuld: de herkenningen die precies bij dit
        beeld horen (in pixels van dit beeld). Anders is het None.
        """
        with self._cond:
            self._cond.wait_for(lambda: self._frame_id != last_id, timeout)
            return self._frame_id, self._frame, self._detections

    # -- intern -----------------------------------------------------------

    def _run(self):
        while self._running:
            try:
                for frame, detections in self._open():
                    if not self._running:
                        break
                    if not self.connected:
                        log.info("Camera verbonden: %s", self.source)
                    self.connected = True
                    self.error = ""
                    with self._cond:
                        self._frame = frame
                        self._detections = detections
                        self._frame_id += 1
                        self._cond.notify_all()  # seintje aan iedereen die op een beeld wacht
                    self._meter.tick()
                if self._running:
                    raise RuntimeError("camera gaf geen beelden meer")
            except Exception as exc:  # camera los, stream weg, enz.
                if self.connected or self.error != str(exc):
                    log.warning("Camera %s: %s (opnieuw proberen...)", self.source, exc)
                self.connected = False
                self.error = str(exc)
                time.sleep(2)

    def _open(self):
        """Geeft een reeks (beeld, objecten). objecten is None, behalve bij de AI Camera."""
        src = self.source
        if src.lower() == "picamera":
            return self._picamera_frames()
        if src.isdigit():
            return self._opencv_frames(int(src), live=True)
        if src.lower().endswith(IMAGE_EXTENSIONS) and os.path.isfile(src):
            return self._image_frames(src)
        is_file = os.path.isfile(src)
        if not is_file and "://" not in src:
            raise FileNotFoundError(f"bestand of camera '{src}' niet gevonden")
        return self._opencv_frames(src, live=not is_file)

    def _opencv_frames(self, src, live: bool):
        if isinstance(src, int) and os.name == "nt":
            cap = cv2.VideoCapture(src, cv2.CAP_DSHOW)  # opent sneller op Windows
        else:
            cap = cv2.VideoCapture(src)
        if not cap.isOpened():
            raise RuntimeError("kan camera/stream niet openen")
        if isinstance(src, int):
            if os.name != "nt":
                # Op Linux (bijv. de Pi) halen USB-webcams 30 of 60 beelden per seconde op
                # 720p vaak alleen als ze MJPG sturen (anders te veel data voor USB).
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            cap.set(cv2.CAP_PROP_FPS, self.wanted_fps)
            log.info("Webcam: %dx%d, %.0f beelden/s (gevraagd: %d)",
                     cap.get(cv2.CAP_PROP_FRAME_WIDTH), cap.get(cv2.CAP_PROP_FRAME_HEIGHT),
                     cap.get(cv2.CAP_PROP_FPS), self.wanted_fps)
        # Een video speelt af op zijn eigen snelheid. We rekenen met een vaste klok,
        # zodat de video niet langzaam achter gaat lopen.
        interval = 0 if live else 1.0 / (cap.get(cv2.CAP_PROP_FPS) or 25)
        next_time = time.monotonic()
        try:
            while True:
                ok, frame = cap.read()
                if not ok:
                    if live:
                        return
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)  # video opnieuw afspelen
                    continue
                yield frame, None
                if interval:
                    next_time += interval
                    rest = next_time - time.monotonic()
                    if rest > 0:
                        time.sleep(rest)
                    elif rest < -0.5:  # ver achter (computer was even druk): niet inhalen
                        next_time = time.monotonic()
        finally:
            cap.release()

    def _image_frames(self, path):
        image = cv2.imread(path)
        if image is None:
            raise RuntimeError(f"kan foto '{path}' niet lezen")
        while True:
            yield image, None
            time.sleep(1 / 15)

    def _picamera_frames(self):
        from picamera2 import Picamera2  # alleen aanwezig op de Raspberry Pi

        ai = self.ai if self.ai_active else None
        # Met de AI Camera: eerst is (in objects.py) het netwerk in de camera gezet,
        # nu pas mogen we Picamera2 openen.
        cam = Picamera2(ai.camera_num if ai else 0)
        try:
            fps = self.wanted_fps
            if ai and ai.frame_rate(fps) < fps:  # sneller filmen dan het netwerk rekent heeft geen zin
                fps = ai.frame_rate(fps)
                log.info("De AI Camera herkent maximaal %g beelden per seconde, dus de camera filmt op %g "
                         "(CAMERA_FPS=%d).", fps, fps, self.wanted_fps)
            sensor = {}
            if fps > 30:
                sensor, fps = _fast_sensor_mode(cam, fps)
            # "RGB888" levert in picamera2 de kleuren in BGR-volgorde: precies wat OpenCV wil.
            config = cam.create_video_configuration(
                main={"format": "RGB888", "size": (self.width, self.height)},
                controls={"FrameRate": fps}, sensor=sensor)
            cam.configure(config)
            cam.start()
            log.info("Pi-camera gestart: %dx%d, %g beelden/s gevraagd%s", self.width, self.height, fps,
                     ", objectherkenning in de AI Camera" if ai else "")
            if ai:
                ai.started(cam)
            while True:
                # Eén "request" = één beeld plus de metadata die erbij hoort. Bij de AI Camera
                # zitten daar de herkende objecten van precies dit beeld in.
                request = cam.capture_request()
                try:
                    frame = request.make_array("main")
                    metadata = request.get_metadata() if ai is not None and ai.active else None
                finally:
                    request.release()  # buffer meteen terug naar de camera
                yield frame, (ai.parse(metadata, cam) if metadata is not None else None)
        finally:
            cam.stop()
            cam.close()


def _fast_sensor_mode(cam, fps):
    """Kies een stand van de sensor die zo snel kan (alleen nodig boven 30 beelden/s).

    Geeft (sensor-instelling voor picamera2, haalbaar aantal beelden/s).
    """
    try:
        modes = [m for m in cam.sensor_modes if m.get("fps") and m.get("size")]
    except Exception as exc:
        log.warning("Kan de standen van de camera niet opvragen (%s); we vragen gewoon %d beelden/s.", exc, fps)
        return {}, fps
    if not modes:
        return {}, fps
    fast = [m for m in modes if m["fps"] >= fps - 0.5]
    if not fast:
        best = max(m["fps"] for m in modes)
        log.warning("Deze camera kan maximaal %.0f beelden per seconde (CAMERA_FPS=%d); we gebruiken %.0f.",
                    best, fps, best)
        return {}, round(best)
    mode = max(fast, key=lambda m: m["size"][0] * m["size"][1])  # de snelle stand met het meeste beeld
    return {"output_size": mode["size"], "bit_depth": mode["bit_depth"]}, fps
