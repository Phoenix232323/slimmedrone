"""Digitale zoom: een stuk van het camerabeeld uitsnijden en vergroten.

Zoom je in op een object ("de hond"), dan volgt het beeld dat object: elk
beeld zoeken we het object van dezelfde soort dat het dichtst bij de vorige
plek is. De uitsnede schuift soepel mee, zodat het beeld niet schokt.
"""
import threading

import cv2

MAX_ZOOM = 5.0     # verder inzoomen heeft weinig zin, het beeld wordt dan te wazig
MARGIN = 1.4       # ruimte rondom het object
SMOOTHING = 0.25   # 0..1, hoe snel de uitsnede naar het doel beweegt


def fit_rect(box, width, height, margin=None):
    """Maak van een kader een uitsnede met dezelfde verhouding als het camerabeeld."""
    x1, y1, x2, y2 = box
    if margin is None:
        # Kleine objecten krijgen veel ruimte eromheen, grote bijna niets
        # (anders past een persoon die half het beeld vult er nooit in).
        size = max((x2 - x1) / width, (y2 - y1) / height)
        margin = 1.05 + (MARGIN - 1.05) * max(0.0, 1 - size * 2)
    bw, bh = max(2.0, (x2 - x1) * margin), max(2.0, (y2 - y1) * margin)
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    aspect = width / height
    if bw / bh > aspect:
        bh = bw / aspect
    else:
        bw = bh * aspect
    if bw < width / MAX_ZOOM:
        bw, bh = width / MAX_ZOOM, height / MAX_ZOOM
    if bw >= width:
        return (0.0, 0.0, float(width), float(height))
    left = min(max(cx - bw / 2, 0), width - bw)
    top = min(max(cy - bh / 2, 0), height - bh)
    return (left, top, left + bw, top + bh)


def crop(image, rect):
    """Snij de uitsnede uit en vergroot hem weer tot de grootte van het hele beeld."""
    h, w = image.shape[:2]
    x1, y1, x2, y2 = (int(round(v)) for v in rect)
    part = image[max(0, y1):min(h, y2), max(0, x1):min(w, x2)]
    if part.size == 0:
        return image.copy()
    return cv2.resize(part, (w, h), interpolation=cv2.INTER_LINEAR)


def _center_distance(a, b):
    return ((a[0] + a[2] - b[0] - b[2]) ** 2 + (a[1] + a[3] - b[1] - b[3]) ** 2) ** 0.5


class Zoom:
    def __init__(self):
        self._lock = threading.Lock()
        self._mode = None     # None, "object" of "region"
        self._label = None    # bij "object": welke soort we volgen
        self._naam = None     # om te tonen, bijv. "tafel" of "Johan"
        self._box = None      # het doel (kader in pixels)
        self._margin = None   # None = automatische ruimte rondom het kader
        self._current = None  # de uitsnede die nu getoond wordt
        self._size = None     # (breedte, hoogte) van het camerabeeld

    def follow(self, label, box, naam):
        """Zoom in op een object en blijf het volgen."""
        with self._lock:
            self._mode, self._label, self._naam, self._box = "object", label, naam, tuple(box)
            self._margin = None

    def region(self, box, naam="gebied"):
        """Zoom in op een vast stuk van het beeld."""
        with self._lock:
            self._mode, self._label, self._naam, self._box = "region", None, naam, tuple(box)
            self._margin = None

    def zoom_at(self, nx, ny, factor=2.0):
        """Zoom verder in rond een punt in de huidige weergave (0..1), bijv. waar je klikt."""
        with self._lock:
            if not self._size:
                return
            w, h = self._size
            x1, y1, x2, y2 = self._current or (0, 0, w, h)
            cx, cy = x1 + nx * (x2 - x1), y1 + ny * (y2 - y1)
            half_w, half_h = (x2 - x1) / factor / 2, (y2 - y1) / factor / 2
            self._mode, self._label, self._naam = "region", None, "gebied"
            self._box = (cx - half_w, cy - half_h, cx + half_w, cy + half_h)
            self._margin = 1.0

    def reset(self):
        with self._lock:
            self._mode = self._label = self._naam = self._box = self._margin = None

    def _target(self, width, height):
        return fit_rect(self._box, width, height, self._margin) if self._mode else None

    def update(self, detections, width, height):
        """Bereken de uitsnede voor dit beeld. Geeft None als er niet is ingezoomd."""
        with self._lock:
            self._size = (width, height)
            if self._mode == "object":
                same = [d for d in detections if d.label == self._label]
                if same:
                    nearest = min(same, key=lambda d: _center_distance(d.box, self._box))
                    self._box = nearest.box
            full = (0.0, 0.0, float(width), float(height))
            target = self._target(width, height) or full
            current = self._current or full
            current = tuple(c + (t - c) * SMOOTHING for c, t in zip(current, target))
            if not self._mode and all(abs(c - f) < 2 for c, f in zip(current, full)):
                self._current = None
                return None
            self._current = current
            return current

    def target_rect(self, width, height):
        """De uitsnede waar de zoom naartoe gaat (zonder het soepele bewegen)."""
        with self._lock:
            return self._target(width, height)

    def status(self):
        with self._lock:
            if not self._mode or not self._size:
                return {"actief": False}
            x1, _, x2, _ = self._target(*self._size)
            return {"actief": True, "naam": self._naam,
                    "factor": round(self._size[0] / max(1.0, x2 - x1), 1)}
