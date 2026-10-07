"""Camerabronnen: webcam, Raspberry Pi-camera, netwerkstream of testbestand.

De camera leest in een eigen thread, zodat de rest van het programma altijd
het nieuwste beeld krijgt en nooit hoeft te wachten.

CAMERA_SOURCE kan zijn:
  0, 1, ...                    een webcam (USB of ingebouwd)
  picamera                     de Raspberry Pi Camera Module (picamera2)
  http://.../stream.mjpg       een netwerkstream, bijv. van pi_stream.py
  rtsp://...                   een IP-camera of drone met RTSP
  pad/naar/video.mp4           een video (speelt in een lus, handig om te testen)
  pad/naar/foto.jpg            een foto (handig om te testen zonder camera)
"""
import logging
import os
import threading
import time

import cv2

log = logging.getLogger(__name__)

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp", ".webp")


class Camera:
    def __init__(self, source: str, width: int = 1280, height: int = 720):
        self.source = str(source).strip()
        self.width = width
        self.height = height
        self.connected = False
        self.error = ""
        self._frame = None
        self._frame_id = 0
        self._lock = threading.Lock()
        self._running = False

    def start(self) -> "Camera":
        self._running = True
        threading.Thread(target=self._run, name="camera", daemon=True).start()
        return self

    def stop(self):
        self._running = False

    def read(self):
        """Geeft (beeldnummer, beeld). Het beeld is None zolang er nog niets binnen is."""
        with self._lock:
            return self._frame_id, self._frame

    # -- intern -----------------------------------------------------------

    def _run(self):
        while self._running:
            try:
                frames = self._open()
                for frame in frames:
                    if not self._running:
                        break
                    if not self.connected:
                        log.info("Camera verbonden: %s", self.source)
                    self.connected = True
                    self.error = ""
                    with self._lock:
                        self._frame = frame
                        self._frame_id += 1
                if self._running:
                    raise RuntimeError("camera gaf geen beelden meer")
            except Exception as exc:  # camera los, stream weg, enz.
                if self.connected or self.error != str(exc):
                    log.warning("Camera %s: %s (opnieuw proberen...)", self.source, exc)
                self.connected = False
                self.error = str(exc)
                time.sleep(2)

    def _open(self):
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
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        delay = 0 if live else 1.0 / (cap.get(cv2.CAP_PROP_FPS) or 25)
        try:
            while True:
                ok, frame = cap.read()
                if not ok:
                    if live:
                        return
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)  # video opnieuw afspelen
                    continue
                yield frame
                if delay:
                    time.sleep(delay)
        finally:
            cap.release()

    def _image_frames(self, path):
        image = cv2.imread(path)
        if image is None:
            raise RuntimeError(f"kan foto '{path}' niet lezen")
        while True:
            yield image
            time.sleep(1 / 15)

    def _picamera_frames(self):
        from picamera2 import Picamera2  # alleen aanwezig op de Raspberry Pi

        cam = Picamera2()
        # "RGB888" levert in picamera2 de kleuren in BGR-volgorde: precies wat OpenCV wil.
        cam.configure(cam.create_video_configuration(
            main={"format": "RGB888", "size": (self.width, self.height)}))
        cam.start()
        try:
            while True:
                yield cam.capture_array()
        finally:
            cam.stop()
            cam.close()
