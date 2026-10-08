"""Video opnemen: de weergave van de website (met kaders en tijdregel) als .avi-bestand.

Hoe het werkt:
  * De verwerkingslus (pipeline.py) maakt van elk beeld voor de livestream al een JPEG.
    Tijdens een opname geeft hij die JPEG ook aan de recorder (frame()).
  * frame() zet het beeld alleen in een kleine wachtrij en komt meteen terug: de
    livestream wacht nooit op de schijf. Is de wachtrij vol (de schijf is even te
    traag), dan slaan we dat beeld gewoon over.
  * Een eigen thread ("opname") haalt de beelden uit de wachtrij en schrijft ze in het
    bestand. Een .avi met MJPG is een rij JPEG-beelden achter elkaar met een kop en een
    inhoudsopgave. We hoeven de beelden dus niet opnieuw in te pakken: dat scheelt veel
    rekenwerk op de Raspberry Pi.
  * Bij het stoppen schrijven we de echte snelheid in het bestand (beelden per seconde),
    zodat de opname even snel afspeelt als het in het echt ging, ook als er beelden zijn
    overgeslagen. Plus een miniatuur (.jpg) ernaast voor de pagina Media.

Een opname stopt vanzelf na OPNAME_MAX_MINUTEN, als er minder dan 500 MB vrij is op de
schijf, of als het bestand 2 GB wordt (grotere .avi-bestanden kunnen sommige spelers niet
aan). Opnames komen in data/opnames/ en spelen af op de pagina Media, in VLC en in de
meeste videospelers.
"""
import atexit
import logging
import queue
import shutil
import struct
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .media import RECORDING_NAME, new_file, read_avi, thumbnail, write_atomic

log = logging.getLogger(__name__)

MIN_FREE_MB = 500           # minder vrij op de schijf? Dan niet (verder) opnemen
MAX_FILE_BYTES = 2_000_000_000  # bij 2 GB stoppen (grens van oudere .avi-spelers)
QUEUE_SIZE = 8              # zoveel beelden mogen op de schijf wachten (daarna overslaan)
HEADER_EVERY = 5.0          # seconden: zo vaak de kop bijwerken (dan is een half bestand nog af te spelen)
DISK_CHECK_EVERY = 5.0      # seconden: zo vaak kijken hoeveel ruimte er nog vrij is
MAX_GAP = 1.0               # langer geen beeld (camera weg)? Dan telt dat gat niet mee voor de snelheid
THUMB_AFTER = 1.0           # miniatuur van het beeld na ongeveer 1 seconde
_WAKE = object()            # "wakker worden": stop() stuurt dit, zodat de schrijf-thread meteen stopt


class RecorderError(Exception):
    """Opnemen kan nu niet. De tekst is een uitleg voor de gebruiker."""


# -- .avi schrijven ---------------------------------------------------------------
# Opbouw van het bestand (zie ook read_avi in media.py):
#   RIFF 'AVI '
#     LIST 'hdrl'  avih (algemeen) + LIST 'strl' met strh (de beeldstroom) en strf (beeldformaat)
#     LIST 'movi'  per beeld een blok '00dc' met de JPEG
#     idx1         inhoudsopgave: per beeld waar het staat (pas bij het afsluiten)
# De kop is altijd precies HEADER_SIZE bytes; daarna beginnen de beelden.

MOVI_POS = 220      # positie van het woord 'movi' (de inhoudsopgave rekent vanaf hier)
HEADER_SIZE = 224
AVIF_HASINDEX = 0x10
AVIIF_KEYFRAME = 0x10


def _chunk(cid: bytes, data: bytes) -> bytes:
    return cid + struct.pack("<I", len(data)) + data


def _header(width, height, fps, frames, biggest, movi_size, riff_size, has_index) -> bytes:
    rate = max(1, round(fps * 1000))  # snelheid als breuk: rate / 1000 beelden per seconde
    avih = struct.pack("<10I16x", round(1_000_000 / fps), round(biggest * fps), 0,
                       AVIF_HASINDEX if has_index else 0, frames, 0, 1, biggest, width, height)
    strh = struct.pack("<4s4sIHHIIIIIIII4h", b"vids", b"MJPG", 0, 0, 0, 0, 1000, rate, 0, frames,
                       biggest, 0xFFFFFFFF, 0, 0, 0, width, height)
    strf = struct.pack("<IiiHH4sIiiII", 40, width, height, 1, 24, b"MJPG", width * height * 3, 0, 0, 0, 0)
    strl = b"strl" + _chunk(b"strh", strh) + _chunk(b"strf", strf)
    hdrl = b"hdrl" + _chunk(b"avih", avih) + _chunk(b"LIST", strl)
    head = (b"RIFF" + struct.pack("<I", riff_size) + b"AVI " + _chunk(b"LIST", hdrl)
            + b"LIST" + struct.pack("<I", movi_size) + b"movi")
    assert len(head) == HEADER_SIZE
    return head


class AviWriter:
    """Schrijft JPEG-beelden als MJPG-video in een .avi-bestand."""

    def __init__(self, file, width: int, height: int, fps: float):
        self._f = file
        self.width, self.height, self.fps = width, height, fps
        self.frames = 0
        self.size = HEADER_SIZE         # hoe groot het bestand nu is
        self._biggest = 0               # grootste beeld (voor de kop)
        self._index = bytearray()       # inhoudsopgave: 16 bytes per beeld
        self._f.write(self._header(has_index=False))

    def add(self, jpeg: bytes):
        n = len(jpeg)
        self._f.write(b"00dc" + struct.pack("<I", n))
        self._f.write(jpeg)
        if n & 1:
            self._f.write(b"\0")  # blokken beginnen altijd op een even positie
        self._index += struct.pack("<4sIII", b"00dc", AVIIF_KEYFRAME, self.size - MOVI_POS, n)
        self.size += 8 + n + (n & 1)
        self.frames += 1
        self._biggest = max(self._biggest, n)

    def update_header(self, fps: float):
        """Kop bijwerken zonder af te sluiten: valt de stroom uit, dan is het bestand toch af te spelen."""
        self.fps = fps
        self._f.seek(0)
        self._f.write(self._header(has_index=False))
        self._f.seek(self.size)
        self._f.flush()

    def close(self, fps: float):
        """Inhoudsopgave erachter, de echte snelheid in de kop, en klaar."""
        self.fps = fps
        try:
            self._f.seek(self.size)
            self._f.write(_chunk(b"idx1", bytes(self._index)))
            self._f.seek(0)
            self._f.write(self._header(has_index=True, end=self.size + 8 + len(self._index)))
        finally:
            self._f.close()

    def _header(self, has_index: bool, end: int | None = None) -> bytes:
        end = self.size if end is None else end
        return _header(self.width, self.height, self.fps, self.frames, self._biggest,
                       movi_size=self.size - MOVI_POS, riff_size=end - 8, has_index=has_index)


def repair_avi(path: Path) -> bool:
    """Sluit een opname alsnog netjes af (bijv. na stroomuitval). Geeft True als er iets hersteld is.

    Zonder inhoudsopgave spelen veel spelers het bestand niet (goed) af. We zoeken de
    beelden op, knippen een half geschreven laatste beeld eraf en schrijven de
    inhoudsopgave en een kloppende kop.
    """
    info = read_avi(path)
    if info.indexed or info.movi != MOVI_POS:
        return False  # al in orde, of niet door ons gemaakt
    if not info.frames:
        path.unlink()  # geen enkel beeld: niets om te bewaren
        return True
    pos, size = info.frames[-1]
    end = pos + size + (size & 1)
    index = b"".join(struct.pack("<4sIII", b"00dc", AVIIF_KEYFRAME, p - 8 - MOVI_POS, n) for p, n in info.frames)
    biggest = max(n for _, n in info.frames)
    with open(path, "r+b") as f:
        f.truncate(end)
        f.seek(end)
        f.write(_chunk(b"idx1", index))
        f.seek(0)
        f.write(_header(info.width, info.height, info.fps, len(info.frames), biggest,
                        movi_size=end - MOVI_POS, riff_size=end + 8 + len(index) - 8, has_index=True))
    return True


# -- de recorder ----------------------------------------------------------------

@dataclass
class _Session:
    """Eén opname."""
    path: Path
    file: object
    max_minutes: int
    user: str
    started: float = field(default_factory=time.monotonic)
    started_wall: float = field(default_factory=time.time)
    inbox: queue.Queue = field(default_factory=lambda: queue.Queue(QUEUE_SIZE))  # wachtrij met beelden
    stop: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    writer: AviWriter | None = None
    reason: str | None = None       # waarom hij vanzelf stopte (None = gestopt door de gebruiker)
    next_due: float = 0.0           # wanneer het volgende beeld mag (vast schema)
    dropped: int = 0                # overgeslagen omdat de wachtrij vol was
    last_time: float | None = None  # wanneer het vorige geschreven beeld gemaakt is
    timeline: float = 0.0           # opgenomen tijd (zonder lange gaten)
    thumb_view: object = None       # beeld voor de miniatuur
    last_view: object = None


class Recorder:
    def __init__(self, folder: Path, alerts=None, fps: float = 15, max_minutes: int = 10, pipeline=None):
        self.folder = Path(folder)
        self.alerts = alerts
        self.fps = max(1.0, float(fps))
        self.max_minutes = max(1, int(max_minutes))
        self.pipeline = pipeline
        self._session = None
        self._last = {}                 # de vorige opname: {"bestand", "duur"}
        self._lock = threading.Lock()
        self._repair_old()
        if pipeline is not None:
            pipeline.add_frame_listener(self)
        atexit.register(self._shutdown)  # Ctrl+C tijdens een opname: bestand netjes afsluiten

    def _repair_old(self):
        """Opnames die niet netjes zijn afgesloten (stroom uitgevallen?) alsnog afmaken."""
        try:
            paths = [p for p in self.folder.iterdir() if RECORDING_NAME.fullmatch(p.name) and p.is_file()]
        except OSError:
            return  # map bestaat nog niet
        for path in paths:
            try:
                if repair_avi(path):
                    log.warning("Opname %s was niet netjes afgesloten (stroom uitgevallen?); hersteld.", path.name)
            except (OSError, ValueError, struct.error) as exc:
                log.warning("Opname %s kon niet worden hersteld: %s", path.name, exc)

    # -- voor de verwerkingslus (moet razendsnel zijn) -------------------------

    def wanted_fps(self) -> float:
        """Hoeveel beelden per seconde we nu willen hebben (0 = we nemen niet op)."""
        s = self._session
        return self.fps if s is not None and not s.stop.is_set() else 0

    def frame(self, view, jpeg):
        """Nieuw beeld van de website. Alleen in de wachtrij zetten; nooit wachten."""
        s = self._session
        if s is None or jpeg is None or s.stop.is_set():
            return
        now = time.monotonic()
        interval = 1.0 / self.fps
        if now < s.next_due - interval / 4:
            return  # te vroeg: de livestream kan sneller zijn dan de opname
        s.next_due = max(s.next_due, now - interval / 2) + interval
        s.last_view = view
        if s.thumb_view is None and now - s.started >= THUMB_AFTER:
            s.thumb_view = view
        try:
            s.inbox.put_nowait((now, view.shape[1], view.shape[0], jpeg))
        except queue.Full:
            s.dropped += 1  # de schijf loopt achter: dit beeld overslaan

    # -- starten en stoppen ----------------------------------------------------

    @property
    def active(self) -> bool:
        s = self._session
        return s is not None and not s.stop.is_set()

    def busy_file(self, name: str) -> bool:
        """Is dit het bestand waar nu in geschreven wordt?"""
        s = self._session
        return s is not None and s.path.name == name

    def start(self, max_minutes: int | None = None, user: str = "") -> dict:
        """Begin een opname. RecorderError (met uitleg) als het niet kan."""
        minutes = self.max_minutes if not max_minutes else max(1, min(int(max_minutes), self.max_minutes))
        with self._lock:
            if self._session is not None:
                raise RecorderError("Er loopt al een opname." if self.active else
                                    "De vorige opname wordt nog opgeslagen. Probeer het zo nog eens.")
            if self.pipeline is not None and self.pipeline.snapshot().frame is None:
                raise RecorderError("Er is geen camerabeeld om op te nemen. Controleer de camera.")
            free = disk_free_mb(self.folder)
            if free is not None and free < MIN_FREE_MB:
                raise RecorderError(f"Te weinig ruimte op de schijf: nog {free} MB vrij, en er is minstens "
                                    f"{MIN_FREE_MB} MB nodig. Verwijder oude opnames op de pagina Media.")
            try:
                path, file = new_file(self.folder, "opname", ".avi")
            except OSError as exc:
                log.error("Kan geen opname maken in %s: %s", self.folder, exc)
                raise RecorderError("Het opnamebestand kon niet worden gemaakt.") from exc
            s = _Session(path, file, minutes, user)
            s.thread = threading.Thread(target=self._write, args=(s,), name="opname", daemon=True)
            s.thread.start()  # eerst starten, dan pas zichtbaar maken (stop() mag er meteen op wachten)
            self._session = s
        who = f" door {user}" if user else ""
        log.info("Opname gestart%s: %s (hooguit %d minuten, %g beelden/s)", who, path, minutes, self.fps)
        self._alert(f"Opname gestart{who} (stopt vanzelf na {minutes} minuten)")
        return self.status()

    def stop(self, reason: str | None = None, wait: float = 10.0) -> dict:
        """Stop de opname (als die loopt) en wacht tot het bestand af is."""
        s = self._session
        if s is not None:
            if reason and not s.reason:
                s.reason = reason
            s.stop.set()
            try:
                s.inbox.put_nowait(_WAKE)  # niet wachten tot de schrijf-thread zelf weer kijkt
            except queue.Full:
                pass  # vol: dan is hij toch bezig
            if s.thread is not threading.current_thread():
                s.thread.join(wait)
        return self.status()

    def status(self) -> dict:
        """{"actief", "sinds", "bestand", "duur", "max_minuten"} voor de website en J.A.R.V.I.S."""
        s = self._session
        if s is not None and not s.stop.is_set():
            return {"actief": True, "sinds": round(s.started_wall, 1), "bestand": s.path.name,
                    "duur": round(time.monotonic() - s.started, 1), "max_minuten": s.max_minutes}
        last = self._last
        return {"actief": False, "sinds": None, "bestand": last.get("bestand"),
                "duur": last.get("duur", 0.0), "max_minuten": self.max_minutes}

    def _shutdown(self):
        if self._session is not None:
            self.stop("het grondstation stopte", wait=5.0)

    # -- de schrijf-thread -----------------------------------------------------

    def _write(self, s: _Session):
        last_header = last_disk = time.monotonic()
        try:
            while True:
                stopping = s.stop.is_set()
                try:  # na het stoppen alleen nog de wachtrij leegmaken
                    item = s.inbox.get(timeout=0.5) if not stopping else s.inbox.get_nowait()
                except queue.Empty:
                    if stopping:
                        break
                    item = None
                if item is not None and item is not _WAKE:
                    self._write_frame(s, *item)
                now = time.monotonic()
                if now - s.started >= s.max_minutes * 60:
                    self._auto_stop(s, f"de maximale duur van {s.max_minutes} minuten is bereikt")
                if now - last_disk >= DISK_CHECK_EVERY:
                    last_disk = now
                    free = disk_free_mb(self.folder)
                    if free is not None and free < MIN_FREE_MB:
                        self._auto_stop(s, f"er is nog maar {free} MB vrij op de schijf")
                if s.writer is not None and now - last_header >= HEADER_EVERY:
                    last_header = now
                    s.writer.update_header(self._real_fps(s))
        except Exception as exc:
            log.exception("Fout bij het schrijven van de opname %s", s.path.name)
            self._auto_stop(s, f"er ging iets mis bij het schrijven ({exc})")
        finally:
            self._finish(s)

    def _write_frame(self, s: _Session, made: float, width: int, height: int, jpeg: bytes):
        if s.writer is None:
            s.writer = AviWriter(s.file, width, height, self.fps)
        elif (width, height) != (s.writer.width, s.writer.height):
            self._auto_stop(s, "het beeldformaat van de camera veranderde")
            return
        if s.last_time is not None:
            s.timeline += min(made - s.last_time, MAX_GAP)
        s.last_time = made
        s.writer.add(jpeg)
        if s.writer.size >= MAX_FILE_BYTES:
            self._auto_stop(s, "het bestand is 2 GB geworden")

    def _auto_stop(self, s: _Session, reason: str):
        if not s.reason:
            s.reason = reason
        s.stop.set()

    def _real_fps(self, s: _Session) -> float:
        """Beelden per seconde zoals het echt ging (nodig om op de goede snelheid af te spelen)."""
        frames = s.writer.frames if s.writer else 0
        if frames < 2:
            return self.fps
        return max(0.5, min(frames / (s.timeline + 1.0 / self.fps), self.fps * 2))

    def _finish(self, s: _Session):
        """Bestand afsluiten, miniatuur maken, melden."""
        name, duration, saved, failed = s.path.name, 0.0, False, False
        try:
            if s.writer is not None and s.writer.frames:
                fps = self._real_fps(s)
                s.writer.close(fps)
                duration = round(s.writer.frames / fps, 1)
                saved = True
                view = s.thumb_view if s.thumb_view is not None else s.last_view
                if view is not None:
                    try:
                        write_atomic(s.path.with_suffix(".jpg"), thumbnail(view))
                    except (OSError, ValueError) as exc:
                        log.warning("Miniatuur van %s maken lukte niet: %s", name, exc)
            else:  # er kwam geen enkel beeld binnen: dan ook geen leeg bestand bewaren
                s.file.close()
                s.path.unlink(missing_ok=True)
        except Exception:
            failed = True
            log.exception("Fout bij het afsluiten van de opname %s", name)
        with self._lock:
            self._session = None
            self._last = {"bestand": name if saved else None, "duur": duration}
        why = f" ({s.reason})" if s.reason else ""
        if saved:
            dropped = f", {s.dropped} beelden overgeslagen" if s.dropped else ""
            log.info("Opname opgeslagen: %s (%s, %d beelden%s)%s", s.path, _clock(duration),
                     s.writer.frames, dropped, why)
            stopped = f"Opname gestopt{why}" if s.reason else "Opname opgeslagen"
            self._alert(f"{stopped}: {name} ({_clock(duration)})")
        elif failed:
            self._alert(f"Opname gestopt{why}: het bestand kon niet goed worden opgeslagen")
        else:
            log.warning("Opname %s gestopt zonder beelden%s", name, why)
            self._alert(f"Opname gestopt{why}: er kwam geen camerabeeld binnen")

    def _alert(self, message: str):
        if self.alerts is not None:
            try:
                self.alerts.add("systeem", message)
            except Exception:
                log.exception("Melding maken mislukte")


def disk_free_mb(folder: Path):
    """Vrije ruimte op de schijf waar `folder` staat, in MB (None als dat niet lukt)."""
    try:
        folder.mkdir(parents=True, exist_ok=True)
        return shutil.disk_usage(folder).free // (1024 * 1024)
    except OSError:
        return None


def _clock(seconds: float) -> str:
    """133.4 -> '2:13'."""
    seconds = int(round(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"
