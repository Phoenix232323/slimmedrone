"""Meldingen: "Johan herkend", "Onbekend gezicht gedetecteerd", ...

Om valse meldingen te voorkomen moet een gezicht een paar beelden achter
elkaar gezien worden. Daarna komt er voor dezelfde persoon pas weer een
melding na ALERT_COOLDOWN seconden. Alle meldingen komen ook in
data/meldingen.csv, handig voor het verslag.
"""
import base64
import csv
import threading
import time
from collections import deque
from pathlib import Path

import cv2

CONFIRM_FRAMES = 3  # zo vaak achter elkaar zien voordat we melden


class AlertManager:
    def __init__(self, cooldown: float, log_file: Path):
        self.cooldown = cooldown
        self.log_file = log_file
        self._alerts = deque(maxlen=100)
        self._last_sent = {}
        self._streak = {}
        self._next_id = 1
        self._lock = threading.Lock()

    def add(self, kind: str, message: str, thumb=None):
        with self._lock:
            alert = {"id": self._next_id, "tijd": time.time(), "soort": kind,
                     "bericht": message, "foto": thumb}
            self._next_id += 1
            self._alerts.append(alert)
        try:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_file, "a", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow([time.strftime("%Y-%m-%d %H:%M:%S"), kind, message])
        except OSError:
            pass
        return alert

    def process_faces(self, faces, frame):
        seen = set()
        now = time.time()
        for face in faces:
            key = face.name or "?onbekend"
            if key in seen:
                continue
            seen.add(key)
            self._streak[key] = self._streak.get(key, 0) + 1
            if self._streak[key] < CONFIRM_FRAMES:
                continue
            if now - self._last_sent.get(key, 0) < self.cooldown:
                continue
            self._last_sent[key] = now
            if face.name:
                self.add("bekend", f"{face.name} herkend", _thumbnail(frame, face.box))
            else:
                self.add("onbekend", "Onbekend gezicht gedetecteerd", _thumbnail(frame, face.box))
        for key in list(self._streak):
            if key not in seen:
                del self._streak[key]

    def since(self, after_id: int, limit: int = 30):
        with self._lock:
            return [a for a in self._alerts if a["id"] > after_id][-limit:]


def _thumbnail(frame, box, size=96):
    x1, y1, x2, y2 = box
    pad_x, pad_y = (x2 - x1) // 4, (y2 - y1) // 4
    h, w = frame.shape[:2]
    part = frame[max(0, y1 - pad_y):min(h, y2 + pad_y), max(0, x1 - pad_x):min(w, x2 + pad_x)]
    if part.size == 0:
        return None
    scale = size / max(part.shape[:2])
    part = cv2.resize(part, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", part, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return "data:image/jpeg;base64," + base64.b64encode(buf).decode() if ok else None
