"""Activiteit: wat verscheen en verdween er in beeld? Plus "wachters".

Een achtergrond-thread kijkt twee keer per seconde naar de laatste uitkomst van
de herkenning (pipeline.snapshot()). Zo houden we bij:

  * gebeurtenissen: "hond verschenen", "Johan verdwenen", ... Voor het dashboard
    en voor J.A.R.V.I.S. ("wat gebeurde er de laatste 10 minuten?").
  * wachters: "waarschuw me als er een hond komt". Verschijnt dat, dan komt er
    een melding (soort "wacht") met een fotootje.
  * de camera: valt de verbinding weg (of komt hij terug), dan komt er een
    melding (soort "systeem").

De herkenning flikkert soms: het ene beeld wel een hond, het volgende niet.
Daarom is iets pas "verschenen" als het APPEAR_AFTER seconden achter elkaar te
zien is, en pas "verdwenen" als het GONE_AFTER seconden niet meer te zien is.
"""
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass

from .alerts import thumbnail

log = logging.getLogger(__name__)

INTERVAL = 0.5          # zo vaak kijken we (seconden)
APPEAR_AFTER = 1.0      # zo lang onafgebroken te zien voordat het "verschenen" is
GONE_AFTER = 5.0        # zo lang weg voordat het "verdwenen" is
GAP = 1.5               # nog niet bevestigd en zo lang weg? Dan opnieuw beginnen met tellen
STALE_AFTER = 3.0       # is het laatste beeld ouder, dan veranderen we niets (camera hapert)
CAMERA_DEBOUNCE = 5.0   # zo lang moet de camera los (of weer verbonden) zijn voor een melding
WATCH_COOLDOWN = 30.0   # dezelfde wachter gaat hooguit één keer per zoveel seconden af
MAX_WATCHES = 10
UNKNOWN = "onbekend"    # sleutel voor onbekende gezichten


@dataclass
class _Track:
    """Iets dat in beeld is (een soort object, een bekend persoon of onbekende gezichten)."""
    naam: str
    first_seen: float
    last_seen: float
    aantal: int
    present: bool = False   # al gemeld als "verschenen"?


@dataclass
class Watch:
    """Een wachter: "waarschuw me als ... in beeld komt"."""
    id: int
    soort: str          # "object", "persoon" of "onbekend"
    doel: str | None    # bij object de Engelse naam ("dog"), bij persoon de naam ("Johan")
    omschrijving: str   # om te tonen: "hond", "Johan", "onbekend gezicht"
    tot: float | None   # unix-tijd waarop hij vervalt (None = blijft aan)
    gebruiker: str
    laatst: float = 0.0  # wanneer hij voor het laatst afging

    @property
    def key(self) -> str:
        if self.soort == "object":
            return f"object:{self.doel}"
        if self.soort == "persoon":
            return f"persoon:{self.doel}"
        return UNKNOWN

    def public(self) -> dict:
        return {"id": self.id, "omschrijving": self.omschrijving, "tot": self.tot,
                "soort": self.soort, "gebruiker": self.gebruiker}


class ActivityTracker:
    def __init__(self, pipeline, alerts):
        self.pipeline = pipeline
        self.alerts = alerts
        self._lock = threading.Lock()
        self._tracks = {}               # sleutel -> _Track
        self._events = deque(maxlen=300)
        self._next_event = 1
        self._watches = {}              # id -> Watch
        self._next_watch = 1
        self._camera_ok = None          # None = nog niet bekend
        self._camera_since = None       # sinds wanneer de camera anders is dan _camera_ok
        self._camera_was_ok = False     # was de camera ooit verbonden?
        self._running = False

    def start(self) -> "ActivityTracker":
        self._running = True
        threading.Thread(target=self._run, name="activiteit", daemon=True).start()
        return self

    def stop(self):
        self._running = False

    def _run(self):
        last_error = 0.0
        while self._running:
            try:
                self.update(self.pipeline.snapshot())
            except Exception:
                if time.time() - last_error > 30:  # niet elke halve seconde dezelfde fout loggen
                    log.exception("Fout bij het bijhouden van de activiteit")
                    last_error = time.time()
            time.sleep(INTERVAL)

    # -- elke halve seconde ----------------------------------------------------

    def update(self, snap, now: float | None = None):
        """Verwerk één snapshot. Los aan te roepen, handig om te testen."""
        now = time.time() if now is None else now
        self._check_camera(now)
        if snap.frame is None or now - snap.time > STALE_AFTER:
            return  # geen (vers) beeld: dan weten we niets nieuws

        seen = _what_is_seen(snap)
        triggered = []  # (wachter, kader) die af moeten gaan
        with self._lock:
            for key, (naam, aantal, box) in seen.items():
                track = self._tracks.get(key)
                if track is None or (not track.present and now - track.last_seen > GAP):
                    track = self._tracks[key] = _Track(naam, now, now, aantal)
                track.last_seen = now
                track.aantal = aantal
                if not track.present and now - track.first_seen >= APPEAR_AFTER:
                    track.present = True
                    self._add_event("verschenen", track.naam, aantal, now)
                    for watch in self._watches.values():
                        if watch.key == key and now - watch.laatst >= WATCH_COOLDOWN:
                            watch.laatst = now
                            triggered.append((watch, box))

            for key, track in list(self._tracks.items()):
                if key in seen:
                    continue
                if now - track.last_seen >= GONE_AFTER:
                    del self._tracks[key]
                    if track.present:
                        self._add_event("verdwenen", track.naam, track.aantal, now)
                elif not track.present and now - track.last_seen > GAP:
                    del self._tracks[key]  # was maar heel even te zien: telt niet

            for watch in list(self._watches.values()):
                if watch.tot is not None and now > watch.tot:
                    del self._watches[watch.id]
                    log.info("Wachter #%d (%s) is verlopen", watch.id, watch.omschrijving)

        # Meldingen buiten het slot maken: alerts heeft een eigen slot.
        for watch, box in triggered:
            log.info("Wachter #%d gaat af: %s", watch.id, watch.omschrijving)
            self.alerts.add("wacht", _watch_message(watch), thumbnail(snap.frame, box))

    def _add_event(self, soort, naam, aantal, now):
        self._events.append({"id": self._next_event, "tijd": now, "soort": soort,
                             "naam": naam, "aantal": aantal})
        self._next_event += 1

    def _check_camera(self, now):
        camera = getattr(self.pipeline, "camera", None)
        if camera is None:
            return
        ok = bool(camera.connected)
        if self._camera_ok is None:
            self._camera_ok = ok
            self._camera_was_ok = ok
            return
        if ok == self._camera_ok:
            self._camera_since = None
            return
        if self._camera_since is None:
            self._camera_since = now
        if now - self._camera_since < CAMERA_DEBOUNCE:
            return
        self._camera_ok, self._camera_since = ok, None
        if ok:
            if self._camera_was_ok:
                self.alerts.add("systeem", "Camera is weer verbonden")
            self._camera_was_ok = True
        elif self._camera_was_ok:
            reason = getattr(camera, "error", "") or "geen beeld"
            self.alerts.add("systeem", f"Camera-verbinding verbroken ({reason})")

    # -- opvragen --------------------------------------------------------------

    def since(self, after_id: int = 0, limit: int = 50) -> list:
        """Gebeurtenissen met een id groter dan after_id (nieuwste laatst)."""
        with self._lock:
            return [dict(e) for e in self._events if e["id"] > after_id][-limit:]

    def recent(self, seconds: float) -> list:
        """Gebeurtenissen van de laatste zoveel seconden (nieuwste laatst)."""
        start = time.time() - seconds
        with self._lock:
            return [dict(e) for e in self._events if e["tijd"] >= start]

    def present(self) -> list:
        """Wat er nu (bevestigd) in beeld is: [(naam, aantal), ...]."""
        with self._lock:
            return [(t.naam, t.aantal) for t in self._tracks.values() if t.present]

    def is_present(self, soort: str, doel: str | None) -> bool:
        key = Watch(0, soort, doel, "", None, "").key
        with self._lock:
            track = self._tracks.get(key)
            return bool(track and track.present)

    # -- wachters --------------------------------------------------------------

    def add_watch(self, soort: str, doel, omschrijving: str, minuten, gebruiker: str) -> Watch:
        """Nieuwe wachter. minuten=None: blijft aan tot je hem stopt."""
        if soort not in ("object", "persoon", "onbekend"):
            raise ValueError(f"Onbekende soort wachter: {soort}")
        with self._lock:
            self._drop_expired(time.time())
            if len(self._watches) >= MAX_WATCHES:
                raise ValueError(f"Er staan al {MAX_WATCHES} wachters aan. Stop er eerst een.")
            tot = time.time() + minuten * 60 if minuten else None
            watch = Watch(self._next_watch, soort, doel, omschrijving, tot, gebruiker)
            self._watches[watch.id] = watch
            self._next_watch += 1
        log.info("Wachter #%d: %s (door %s)", watch.id, omschrijving, gebruiker)
        return watch

    def watches(self) -> list:
        """Alle actieve wachters, zoals de website ze wil hebben."""
        with self._lock:
            self._drop_expired(time.time())
            return [w.public() for w in self._watches.values()]

    def cancel_watch(self, watch_id: int):
        """Stop een wachter. Geeft de gestopte wachter terug, of None als hij niet bestond."""
        with self._lock:
            watch = self._watches.pop(watch_id, None)
        if watch:
            log.info("Wachter #%d (%s) gestopt", watch.id, watch.omschrijving)
        return watch

    def _drop_expired(self, now):
        for watch_id, watch in list(self._watches.items()):
            if watch.tot is not None and now > watch.tot:
                del self._watches[watch_id]


# -- hulpfuncties -------------------------------------------------------------

def _what_is_seen(snap) -> dict:
    """{sleutel: (naam, aantal, kader)} van alles in deze snapshot."""
    seen = {}
    for d in snap.detections:
        key = f"object:{d.label}"
        naam, aantal, box = seen.get(key, (d.naam, 0, d.box))
        seen[key] = (naam, aantal + 1, box)
    for f in snap.faces:
        key = f"persoon:{f.name}" if f.name else UNKNOWN
        naam = f.name or "onbekend gezicht"
        _, aantal, box = seen.get(key, (naam, 0, f.box))
        seen[key] = (naam, aantal + 1, box)
    return seen


def _watch_message(watch: Watch) -> str:
    if watch.soort == "persoon":
        return f"Wachter: {watch.omschrijving} is in beeld"
    if watch.soort == "onbekend":
        return "Wachter: onbekend gezicht in beeld"
    return f"Wachter: {watch.omschrijving} in beeld"
