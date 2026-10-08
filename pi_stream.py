"""Alleen camerabeeld doorsturen vanaf de Raspberry Pi (zonder AI).

Gebruik dit als de Pi te traag is om zelf de AI te draaien. De Pi stuurt dan
alleen het beeld via wifi, en de laptop doet de herkenning:

    op de Pi:      python pi_stream.py
    op de laptop:  CAMERA_SOURCE=http://<ip-van-pi>:8000/stream.mjpg?token=<STREAM_TOKEN>
                   python app.py

Zet in .env op de Pi een STREAM_TOKEN (een lang willekeurig woord), zodat niet
iedereen in het netwerk zomaar kan meekijken.
"""
import logging
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import cv2
from dotenv import load_dotenv

from slimmedrone.camera import Camera
from slimmedrone.config import ROOT

load_dotenv(ROOT / ".env")
PORT = int(os.getenv("STREAM_PORT", "8000"))
TOKEN = os.getenv("STREAM_TOKEN", "")
FPS = int(os.getenv("STREAM_FPS", "30"))
QUALITY = int(os.getenv("JPEG_QUALITY", "80"))

camera = Camera(os.getenv("CAMERA_SOURCE", "picamera"),
                int(os.getenv("CAMERA_WIDTH", "1280")), int(os.getenv("CAMERA_HEIGHT", "720")),
                int(os.getenv("CAMERA_FPS", "30")))


class StreamHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlparse(self.path)
        if url.path != "/stream.mjpg":
            self.send_error(404)
            return
        if TOKEN and parse_qs(url.query).get("token", [""])[0] != TOKEN:
            self.send_error(403, "Verkeerde of ontbrekende token")
            return
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        last_id = -1
        next_time = time.monotonic()
        try:
            while True:
                # Wacht op een nieuw beeld (de camera geeft een seintje).
                frame_id, frame, _ = camera.wait_frame(last_id, timeout=1.0)
                if frame is None or frame_id == last_id:
                    continue
                last_id = frame_id
                ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, QUALITY])
                if not ok:
                    continue
                self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                 + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg.tobytes() + b"\r\n")
                # Niet vaker dan STREAM_FPS beelden per seconde (vast schema, dus geen gehaper).
                next_time = max(next_time + 1 / FPS, time.monotonic() - 0.5 / FPS)
                rest = next_time - time.monotonic() - 0.25 / FPS
                if rest > 0:
                    time.sleep(rest)
        except (BrokenPipeError, ConnectionResetError):
            pass  # kijker heeft de verbinding gesloten

    def log_message(self, fmt, *args):
        logging.info("%s - %s", self.client_address[0], fmt % args)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    if not TOKEN:
        logging.warning("Geen STREAM_TOKEN ingesteld: iedereen in het netwerk kan meekijken!")
    camera.start()
    logging.info("Stream draait op poort %d (pad /stream.mjpg)", PORT)
    ThreadingHTTPServer(("0.0.0.0", PORT), StreamHandler).serve_forever()


if __name__ == "__main__":
    main()
