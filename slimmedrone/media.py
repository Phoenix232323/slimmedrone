"""Foto's en video-opnames: opslaan, opsommen, miniaturen, afspelen en verwijderen.

Mappen:
  data/fotos/     foto's (van de knop Foto op het dashboard en van J.A.R.V.I.S.)
  data/opnames/   video-opnames (.avi, zie recorder.py), met een miniatuur (.jpg) ernaast

De website vraagt een bestand op met zijn naam, bijvoorbeeld
/api/media/fotos/foto-20261008-101500.jpg. Zo'n naam controleren we streng: alleen
namen zoals wij ze zelf maken, zonder mappen en zonder "..". Anders zou iemand met een
slim gekozen naam andere bestanden kunnen opvragen of wissen (bijv. data/gebruikers.json).

Afspelen op de website gaat als MJPEG-stroom, net als de livestream: een .avi met MJPG
is gewoon een rij JPEG-beelden achter elkaar. We sturen die beelden één voor één door,
op de snelheid van de opname, zonder ze opnieuw in te pakken (dat spaart de Pi).
"""
import logging
import os
import re
import shutil
import struct
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger(__name__)

# Alleen deze namen zijn "van ons". [0-9] en niet \d: \d laat ook andere cijfertekens toe.
# Foto's van J.A.R.V.I.S. heetten eerst foto_20261008_101500.jpg; die tonen we ook.
PHOTO_NAME = re.compile(r"foto[-_]([0-9]{8})[-_]([0-9]{6})(?:[-_]([0-9]{1,3}))?\.jpg")
RECORDING_NAME = re.compile(r"opname-([0-9]{8})-([0-9]{6})(?:-([0-9]{1,3}))?\.avi")
NAMES = {"fotos": PHOTO_NAME, "opnames": RECORDING_NAME}

MINI_WIDTH = 320       # miniaturen zijn hooguit zo breed (pixels)
MINI_CACHE = 300       # zoveel miniaturen van foto's onthouden we (samen een paar MB)


class MediaBusy(Exception):
    """Het bestand is nog in gebruik (bijv. een opname die nog loopt)."""


# -- bestanden maken ------------------------------------------------------------

def new_file(folder: Path, prefix: str, ext: str):
    """Maak een nieuw, leeg bestand 'prefix-20261008-101500.ext'. Geeft (pad, open bestand).

    Bestaat die naam al (twee keer in dezelfde seconde), dan wordt het -2, -3, ...
    O_EXCL zorgt dat twee verzoeken tegelijk nooit hetzelfde bestand krijgen.
    """
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    for n in range(1, 1000):
        path = folder / f"{prefix}-{stamp}{'' if n == 1 else f'-{n}'}{ext}"
        try:
            fd = os.open(path, flags, 0o644)
        except FileExistsError:
            continue
        return path, os.fdopen(fd, "wb")
    raise OSError(f"Geen vrije bestandsnaam in {folder}")


def save_photo(folder: Path, jpeg: bytes | None = None, image=None) -> Path:
    """Sla een foto op als foto-20261008-101500.jpg. Geef een JPEG (bytes) of een beeld."""
    if jpeg is None:
        ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if not ok:
            raise OSError("De foto kon niet als JPEG worden opgeslagen.")
        jpeg = buf.tobytes()
    path, f = new_file(folder, "foto", ".jpg")
    try:
        with f:
            f.write(jpeg)
    except OSError:
        path.unlink(missing_ok=True)  # geen half bestand laten staan
        raise
    log.info("Foto opgeslagen: %s", path)
    return path


def thumbnail(image, width: int = MINI_WIDTH) -> bytes:
    """Klein JPEG-plaatje (hooguit `width` pixels breed) van een beeld."""
    h, w = image.shape[:2]
    if w > width:
        image = cv2.resize(image, (width, max(1, round(h * width / w))), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 80])
    if not ok:
        raise ValueError("Miniatuur maken mislukte")
    return buf.tobytes()


def thumbnail_from_jpeg(jpeg: bytes, width: int = MINI_WIDTH) -> bytes:
    """Miniatuur van een JPEG. We pakken hem op halve grootte uit: dat is veel sneller."""
    data = np.frombuffer(jpeg, np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_REDUCED_COLOR_2)
    if image is not None and image.shape[1] < width:  # klein plaatje: dan toch op ware grootte
        image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Geen geldige JPEG")
    return thumbnail(image, width)


# -- .avi-bestanden lezen -------------------------------------------------------
# Een .avi is een "RIFF"-bestand: blokken (chunks) van [4 letters][lengte][inhoud].
#   RIFF 'AVI '
#     LIST 'hdrl'   kop: beeldgrootte, beelden per seconde, aantal beelden
#     LIST 'movi'   de beelden: per beeld een blok '00dc' met een JPEG erin
#     idx1          inhoudsopgave: waar elk beeld staat
# Is een opname niet netjes afgesloten (stroom uitgevallen), dan ontbreekt idx1; dan
# zoeken we de beelden zelf op door de blokken één voor één af te lopen.

@dataclass
class AviInfo:
    fps: float
    width: int
    height: int
    count: int        # aantal beelden
    frames: list      # [(positie, lengte), ...] van elke JPEG in het bestand (leeg als niet gevraagd)
    indexed: bool     # heeft het bestand een inhoudsopgave (idx1)? Zo niet, dan is het niet afgesloten.
                      # (alleen betrouwbaar met frames=True)
    movi: int | None  # positie van het woord 'movi'

    @property
    def duration(self) -> float:
        return self.count / self.fps if self.fps > 0 else 0.0


def read_avi(path: Path, frames: bool = True) -> AviInfo:
    """Lees de kop (en als frames=True ook waar elk beeld staat) van een MJPG-.avi."""
    with open(path, "rb") as f:
        end = f.seek(0, os.SEEK_END)
        f.seek(0)
        riff = f.read(12)
        if len(riff) < 12 or riff[:4] != b"RIFF" or riff[8:12] != b"AVI ":
            raise ValueError("Dit is geen .avi-bestand")
        fps, width, height, count = 0.0, 0, 0, 0
        movi = None     # positie van het woord 'movi' (daar rekent de inhoudsopgave vanaf)
        index = None
        pos = 12
        while pos + 8 <= end:
            f.seek(pos)
            cid, size = struct.unpack("<4sI", f.read(8))
            if cid == b"LIST":
                kind = f.read(4)
                if kind == b"hdrl":
                    fps, width, height, count = _parse_hdrl(f.read(min(max(size - 4, 0), 65536)))
                elif kind == b"movi":
                    movi = pos + 8
                    if not frames and count:
                        break  # alleen de kop gevraagd en die is compleet
            elif cid == b"idx1":
                index = f.read(size) if frames else b""
            pos += 8 + size + (size & 1)

        found = []
        if frames and movi is not None:
            found = _frames_from_index(f, index, movi, end) if index else []
            if not found:  # geen (bruikbare) inhoudsopgave: zelf zoeken
                found = _scan_frames(f, movi + 4, end)
            count = len(found)
    if fps <= 0:
        raise ValueError("Onbekende snelheid (beelden per seconde)")
    return AviInfo(fps, width, height, count, found, index is not None, movi)


def _chunks(data: bytes):
    """Blokken in een stuk van de kop: (4 letters, inhoud)."""
    pos = 0
    while pos + 8 <= len(data):
        cid, size = struct.unpack_from("<4sI", data, pos)
        yield cid, data[pos + 8:pos + 8 + size]
        pos += 8 + size + (size & 1)


def _parse_hdrl(data: bytes):
    """(fps, breedte, hoogte, aantal beelden) uit de kop van een .avi."""
    fps, width, height, count = 0.0, 0, 0, 0
    for cid, body in _chunks(data):
        if cid == b"avih" and len(body) >= 40:
            us_per_frame, _, _, _, count, _, _, _, width, height = struct.unpack_from("<10I", body)
            if us_per_frame:
                fps = 1_000_000 / us_per_frame
        elif cid == b"LIST" and body[:4] == b"strl":
            for sub, sbody in _chunks(body[4:]):
                if sub == b"strh" and len(sbody) >= 36 and sbody[:4] == b"vids":
                    scale, rate, _, length = struct.unpack_from("<4I", sbody, 20)
                    if scale and rate:
                        fps = rate / scale
                    count = length or count
                elif sub == b"strf" and len(sbody) >= 12:
                    width, height = struct.unpack_from("<ii", sbody, 4)
                    height = abs(height)
            break  # alleen de eerste stroom (het beeld)
    return fps, width, height, count


def _frames_from_index(f, index: bytes, movi: int, end: int) -> list:
    """Beelden volgens de inhoudsopgave (idx1)."""
    entries = [struct.unpack_from("<4sIII", index, i) for i in range(0, len(index) - 15, 16)]
    entries = [(off, size) for cid, _, off, size in entries if cid[:2] == b"00" and cid[2:] in (b"dc", b"db")]
    if not entries:
        return []
    # Meestal telt de positie vanaf 'movi', bij sommige programma's vanaf het begin van het bestand.
    base = movi
    f.seek(movi + entries[0][0])
    if f.read(2) != b"00":
        base = 0
    return [(base + off + 8, size) for off, size in entries if size and base + off + 8 + size <= end]


def _scan_frames(f, pos: int, end: int) -> list:
    """Zoek de beelden zelf op, blok voor blok (als de inhoudsopgave ontbreekt)."""
    found = []
    while pos + 8 <= end:
        f.seek(pos)
        cid, size = struct.unpack("<4sI", f.read(8))
        if cid == b"LIST":  # een groepje blokken ('rec '): erin kijken
            pos += 12
            continue
        if cid == b"idx1" or pos + 8 + size > end:
            break  # einde van de beelden, of een half geschreven laatste beeld
        if cid[:2] == b"00" and cid[2:] in (b"dc", b"db") and size:
            found.append((pos + 8, size))
        pos += 8 + size + (size & 1)
    return found


# -- de galerij -----------------------------------------------------------------

class Media:
    def __init__(self, data_dir: Path, recorder=None):
        self.data_dir = Path(data_dir)
        self.folders = {"fotos": self.data_dir / "fotos", "opnames": self.data_dir / "opnames"}
        self.recorder = recorder
        self._lock = threading.Lock()
        self._minis = OrderedDict()     # (pad, wijzigtijd, grootte) -> miniatuur (JPEG)
        self._durations = {}            # (pad, wijzigtijd, grootte) -> duur in seconden

    @property
    def photos_dir(self) -> Path:
        return self.folders["fotos"]

    def path(self, soort: str, naam: str):
        """Het pad van een eigen foto of opname, of None als de naam niet mag of niet bestaat."""
        pattern = NAMES.get(soort)
        if pattern is None or not isinstance(naam, str) or not pattern.fullmatch(naam):
            return None
        folder = self.folders[soort]
        path = folder / naam
        try:
            if path.is_symlink() or not path.is_file() or path.resolve().parent != folder.resolve():
                return None
        except OSError:
            return None
        return path

    def busy(self, naam: str) -> bool:
        """Is dit de opname die nu loopt? (die is nog niet af)"""
        return bool(self.recorder is not None and self.recorder.busy_file(naam))

    def free_mb(self):
        """Vrije ruimte op de schijf van de data-map, in MB (of None als dat niet lukt)."""
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            return shutil.disk_usage(self.data_dir).free // (1024 * 1024)
        except OSError:
            return None

    # -- opsommen --------------------------------------------------------------

    def listing(self) -> dict:
        """Alle foto's en opnames, nieuwste eerst."""
        return {"fotos": self._list("fotos"), "opnames": self._list("opnames"), "vrij_mb": self.free_mb()}

    def _list(self, soort: str) -> list:
        folder, pattern = self.folders[soort], NAMES[soort]
        items = []
        try:
            entries = list(os.scandir(folder))
        except OSError:  # map bestaat (nog) niet
            return []
        for entry in entries:
            match = pattern.fullmatch(entry.name)
            if not match or entry.is_symlink() or not entry.is_file() or self.busy(entry.name):
                continue
            try:
                st = entry.stat()
            except OSError:
                continue
            item = {"naam": entry.name, "tijd": _name_time(match, st.st_mtime), "grootte": st.st_size,
                    "url": f"/api/media/{soort}/{entry.name}", "mini": f"/api/media/{soort}/{entry.name}/mini"}
            if soort == "opnames":
                item["duur"] = self._duration(Path(entry.path), st)
                item["afspelen"] = f"/api/media/opnames/{entry.name}/afspelen"
            items.append((item["tijd"], int(match.group(3) or 1), item))
        # Nieuwste eerst; in dezelfde seconde komt foto-...-2 na foto-... (dus eerder in de lijst).
        items.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return [item for _, _, item in items]

    def _duration(self, path: Path, st) -> float:
        key = (str(path), st.st_mtime_ns, st.st_size)
        with self._lock:
            if key in self._durations:
                return self._durations[key]
        try:
            info = read_avi(path, frames=False)
            if not info.count:  # niet netjes afgesloten: beelden tellen
                info = read_avi(path)
            duration = round(info.duration, 1)
        except (OSError, ValueError, struct.error):
            duration = 0.0
        with self._lock:
            if len(self._durations) > 2000:
                self._durations.clear()
            self._durations[key] = duration
        return duration

    # -- miniaturen ------------------------------------------------------------

    def mini(self, soort: str, path: Path) -> bytes:
        """Kleine JPEG (hooguit 320 pixels breed). ValueError als het niet lukt."""
        if soort == "opnames":
            return self._recording_mini(path)
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size)
        with self._lock:
            if key in self._minis:
                self._minis.move_to_end(key)
                return self._minis[key]
        jpeg = thumbnail_from_jpeg(path.read_bytes())
        with self._lock:
            self._minis[key] = jpeg
            while len(self._minis) > MINI_CACHE:
                self._minis.popitem(last=False)
        return jpeg

    def _recording_mini(self, path: Path) -> bytes:
        """De miniatuur naast de opname (opname-....jpg); ontbreekt die, dan maken we hem nu."""
        if self.busy(path.name):
            raise MediaBusy("Deze opname loopt nog.")
        side = path.with_suffix(".jpg")
        if side.is_file() and not side.is_symlink():
            return side.read_bytes()
        info = read_avi(path)
        if not info.frames:
            raise ValueError("Deze opname bevat geen beelden")
        pos, size = info.frames[min(len(info.frames) - 1, int(info.fps))]  # beeld na ongeveer 1 seconde
        with open(path, "rb") as f:
            f.seek(pos)
            jpeg = thumbnail_from_jpeg(f.read(size))
        write_atomic(side, jpeg)
        return jpeg

    # -- verwijderen en afspelen -----------------------------------------------

    def delete(self, soort: str, path: Path):
        """Verwijder een foto of opname (met zijn miniatuur). MediaBusy als hij nog loopt."""
        if soort == "opnames" and self.busy(path.name):
            raise MediaBusy("Deze opname loopt nog. Stop eerst de opname.")
        path.unlink()
        if soort == "opnames":
            path.with_suffix(".jpg").unlink(missing_ok=True)
        log.info("Verwijderd: %s", path)

    def play(self, path: Path):
        """Speel een opname één keer af op de oorspronkelijke snelheid.

        Geeft (info, beelden): beelden is een generator met de JPEG's, die zelf wacht
        tot het tijd is voor het volgende beeld. ValueError als het bestand niet klopt.
        """
        if self.busy(path.name):
            raise MediaBusy("Deze opname loopt nog. Stop eerst de opname.")
        info = read_avi(path)
        if not info.frames:
            raise ValueError("Deze opname bevat geen beelden")
        file = open(path, "rb")  # nu al openen: lukt dat niet, dan hoort de website dat meteen

        def frames():
            interval = 1.0 / info.fps
            next_time = time.monotonic()
            with file as f:
                for pos, size in info.frames:
                    f.seek(pos)
                    jpeg = f.read(size)
                    if len(jpeg) != size:
                        return  # bestand is ingekort
                    yield jpeg
                    # Vast schema, zodat de opname niet langzaam achter gaat lopen.
                    next_time += interval
                    rest = next_time - time.monotonic()
                    if rest > 0:
                        time.sleep(rest)
                    elif rest < -1:  # ver achter (computer was druk): niet inhalen
                        next_time = time.monotonic()

        return info, frames()


def write_atomic(path: Path, data: bytes):
    """Schrijf eerst naar een tijdelijk bestand en zet het dan in één keer op zijn plek."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def _name_time(match, fallback: float) -> float:
    """De tijd uit de naam (foto-20261008-101500.jpg -> 8 oktober 2026 10:15:00)."""
    try:
        return time.mktime(time.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S"))
    except (ValueError, OverflowError):
        return fallback
