"""Statistiek: hoeveel personen en objecten waren er in beeld, en hoeveel meldingen kwamen er?

Een achtergrond-thread kijkt elke 2 seconden naar de laatste uitkomst van de herkenning
(pipeline.snapshot()) en telt:
  personen   hoeveel personen er tegelijk in beeld waren (persoon-kaders of gezichten,
             wat het hoogst is)
  objecten   hoeveel objecten er in beeld waren (alle soorten samen, personen ook)
  bekend     hoeveel bekende gezichten
  onbekend   hoeveel onbekende gezichten
  in beeld   per soort object: hoeveel seconden het in beeld was
  namen      welke bekende personen er waren

Per blok (10 seconden, 1 minuut of 5 minuten) bewaren we het MAXIMUM, dus het drukste
moment, en niet het gemiddelde: bij "hoeveel mensen waren er?" wil je weten hoeveel er
tegelijk waren. De tijd in beeld tellen we wel op.

  * De blokken van 10 seconden blijven een uur in het geheugen (grafiek "afgelopen uur").
  * Per minuut komt er een regel in data/statistiek.csv (handig voor het verslag, opent in
    Excel). Die lezen we bij het starten weer in, zodat een herstart de dag niet wist.
  * De grafiek "afgelopen dag" gebruikt blokken van 5 minuten, gemaakt uit de minuten.
  * Meldingen tellen we per uur (de laatste 24 uur). Bij het starten lezen we de meldingen
    van het afgelopen etmaal uit data/meldingen.csv.

Geen (vers) camerabeeld? Dan tellen we niets: een gat in de grafiek in plaats van een nul.
"""
import atexit
import csv
import io
import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

SAMPLE_INTERVAL = 2.0       # zo vaak kijken we (seconden)
FINE_STEP = 10              # blokken van 10 seconden voor het afgelopen uur
DAY_STEP = 300              # blokken van 5 minuten voor de afgelopen dag
HOUR = 3600
DAY = 24 * HOUR
STALE_AFTER = 3.0           # is het laatste beeld ouder, dan is er (even) geen camerabeeld
CSV_HEADER = ["tijd", "personen", "objecten", "bekend", "onbekend", "in_beeld", "namen"]
READ_TAIL = 1024 * 1024     # bij het starten lezen we alleen het laatste stuk van de csv-bestanden
PERIODS = {"uur": (HOUR, FINE_STEP), "dag": (DAY, DAY_STEP)}
ALERT_KINDS = ("bekend", "onbekend", "wacht", "systeem")


@dataclass
class _Block:
    """Wat er in één tijdsblok (10 s, 1 min of 5 min) in beeld was."""
    tijd: float                 # begin van het blok (unix-tijd)
    personen: int = 0           # maximum binnen het blok
    objecten: int = 0
    bekend: int = 0
    onbekend: int = 0
    seconden: dict = field(default_factory=dict)  # objectnaam -> seconden in beeld
    namen: set = field(default_factory=set)       # bekende personen

    def merge(self, other: "_Block"):
        self.personen = max(self.personen, other.personen)
        self.objecten = max(self.objecten, other.objecten)
        self.bekend = max(self.bekend, other.bekend)
        self.onbekend = max(self.onbekend, other.onbekend)
        for naam, sec in other.seconden.items():
            self.seconden[naam] = self.seconden.get(naam, 0.0) + sec
        self.namen |= other.namen

    def point(self) -> dict:
        return {"tijd": float(self.tijd), "personen": self.personen, "objecten": self.objecten,
                "bekend": self.bekend, "onbekend": self.onbekend}


class Statistics:
    def __init__(self, pipeline, alerts, csv_path: Path, alerts_csv: Path | None = None):
        self.pipeline = pipeline
        self.alerts = alerts
        self.csv_path = Path(csv_path)
        self.alerts_csv = alerts_csv if alerts_csv is not None else getattr(alerts, "log_file", None)
        self._lock = threading.Lock()
        self._fine = deque()        # _Block per 10 s (het laatste uur)
        self._minutes = deque()     # _Block per minuut (de laatste 24 uur), al in de csv
        self._minute = None         # _Block van de lopende minuut (nog niet in de csv)
        self._alerts = deque()      # (tijd, soort) van de meldingen van de laatste 24 uur
        self._last_alert_id = 0
        self._last_sample = None    # wanneer we voor het laatst iets telden
        self._running = False
        self._load()

    def start(self) -> "Statistics":
        self._running = True
        threading.Thread(target=self._run, name="statistiek", daemon=True).start()
        atexit.register(self.flush)  # bij afsluiten de lopende minuut nog opslaan
        return self

    def stop(self):
        self._running = False
        self.flush()

    def _run(self):
        last_error = 0.0
        while self._running:
            try:
                self.sample(self.pipeline.snapshot())
                self.poll_alerts()
            except Exception:
                if time.time() - last_error > 60:
                    log.exception("Fout bij het bijhouden van de statistiek")
                    last_error = time.time()
            time.sleep(SAMPLE_INTERVAL)

    # -- tellen ----------------------------------------------------------------

    def sample(self, snap, now: float | None = None):
        """Tel wat er nu in beeld is. Los aan te roepen (handig om te testen)."""
        now = time.time() if now is None else now
        if snap.frame is None or now - snap.time > STALE_AFTER:
            self._last_sample = None  # geen beeld: niets tellen (en straks niet doortellen)
            self._close_minute(now)
            return
        # Tijd sinds de vorige telling (hooguit 2 rondes, anders telt een hapering te zwaar).
        dt = SAMPLE_INTERVAL if self._last_sample is None else \
            max(0.0, min(now - self._last_sample, 2 * SAMPLE_INTERVAL))
        self._last_sample = now

        person_boxes = sum(1 for d in snap.detections if d.label == "person")
        sample = _Block(now, personen=max(person_boxes, len(snap.faces)), objecten=len(snap.detections),
                        bekend=sum(1 for f in snap.faces if f.name),
                        onbekend=sum(1 for f in snap.faces if not f.name),
                        seconden={_clean(d.naam): dt for d in snap.detections},
                        namen={_clean(f.name) for f in snap.faces if f.name})
        self._close_minute(now)
        with self._lock:
            fine_start = now - now % FINE_STEP
            if not self._fine or self._fine[-1].tijd != fine_start:
                self._fine.append(_Block(fine_start))
            self._fine[-1].merge(sample)
            if self._minute is None:
                self._minute = _Block(now - now % 60)
            self._minute.merge(sample)
            while self._fine and self._fine[0].tijd < now - HOUR - FINE_STEP:
                self._fine.popleft()

    def _close_minute(self, now: float):
        """Is de lopende minuut voorbij? Dan gaat hij naar de lijst en de csv."""
        with self._lock:
            done = self._minute
            if done is None or now - done.tijd < 60:
                return
            self._minute = None
            self._minutes.append(done)
            while self._minutes and self._minutes[0].tijd < now - DAY - 60:
                self._minutes.popleft()
        self._append_csv(done)

    def flush(self):
        """Sla de lopende minuut nu al op (bij afsluiten)."""
        with self._lock:
            done, self._minute = self._minute, None
            if done is not None:
                self._minutes.append(done)
        if done is not None:
            self._append_csv(done)

    def poll_alerts(self):
        """Nieuwe meldingen tellen (de AlertManager bewaart de laatste 100)."""
        if self.alerts is None:
            return
        new = self.alerts.since(self._last_alert_id, limit=100)
        now = time.time()
        with self._lock:
            for a in new:
                self._alerts.append((a["tijd"], a["soort"]))
                self._last_alert_id = max(self._last_alert_id, a["id"])
            while self._alerts and self._alerts[0][0] < now - DAY - HOUR:
                self._alerts.popleft()

    # -- opvragen --------------------------------------------------------------

    def report(self, periode: str = "uur", now: float | None = None) -> dict:
        """Alles voor de pagina Statistieken (zie /api/statistiek)."""
        if periode not in PERIODS:
            raise ValueError("Kies periode=uur of periode=dag.")
        now = time.time() if now is None else now
        span, step = PERIODS[periode]
        blocks = self._blocks(now - span, fine=(periode == "uur"))
        if periode == "dag":  # minuten samenvoegen tot blokken van 5 minuten
            merged = {}
            for b in blocks:
                start = b.tijd - b.tijd % step
                merged.setdefault(start, _Block(start)).merge(b)
            points = [merged[t].point() for t in sorted(merged)]
        else:
            points = [b.point() for b in blocks]
        return {"periode": periode, "stap": step, "punten": points,
                "meldingen_per_uur": self._alerts_per_hour(now),
                "top_objecten": _top_objects(blocks),
                "samenvatting": self._summary(blocks, now - span)}

    def summary(self, seconds: float, now: float | None = None) -> dict:
        """Samenvatting van de laatste `seconds` seconden (voor J.A.R.V.I.S.).

        Geeft de samenvatting van report() plus "top_objecten" en "sinds" (vanaf wanneer er
        gegevens zijn in die periode, of None als er helemaal niets is).
        """
        now = time.time() if now is None else now
        seconds = max(60.0, min(float(seconds), DAY))
        blocks = self._blocks(now - seconds, fine=seconds <= HOUR)
        result = self._summary(blocks, now - seconds)
        result["top_objecten"] = _top_objects(blocks)
        result["sinds"] = min((b.tijd for b in blocks), default=None)
        return result

    def _blocks(self, start: float, fine: bool) -> list:
        """De blokken vanaf `start`, oudste eerst.

        fine=True: blokken van 10 s; minuten van vóór een herstart (uit de csv, zonder
        blokken van 10 s) komen er dan als één blok per minuut bij. fine=False: per minuut.
        """
        with self._lock:
            minutes = [b for b in self._minutes if b.tijd > start - 60]
            if self._minute is not None:
                minutes.append(self._minute)
            minutes = [_copy(b) for b in minutes]
            fine_blocks = [_copy(b) for b in self._fine if b.tijd > start - FINE_STEP] if fine else []
        if not fine:
            merged = {}  # na een herstart binnen dezelfde minuut kan een minuut twee keer voorkomen
            for b in minutes:
                merged.setdefault(b.tijd, _Block(b.tijd)).merge(b)
            return [merged[t] for t in sorted(merged)]
        covered = {b.tijd - b.tijd % 60 for b in fine_blocks}
        old = [b for b in minutes if b.tijd not in covered]
        return sorted(old + fine_blocks, key=lambda b: b.tijd)

    def _summary(self, blocks: list, start: float) -> dict:
        with self._lock:
            alerts = [kind for t, kind in self._alerts if t >= start]
        names = set()
        for b in blocks:
            names |= b.namen
        return {"max_personen": max((b.personen for b in blocks), default=0),
                "meldingen": len(alerts), "onbekend": alerts.count("onbekend"),
                "bekende_personen": sorted(names, key=str.lower)}

    def _alerts_per_hour(self, now: float) -> list:
        """Meldingen per uur, per soort, voor de laatste 24 uur (huidige uur als laatste)."""
        this_hour = datetime.fromtimestamp(now).replace(minute=0, second=0, microsecond=0).timestamp()
        first = this_hour - 23 * HOUR
        hours = [{"uur": first + i * HOUR, **{k: 0 for k in ALERT_KINDS}} for i in range(24)]
        with self._lock:
            alerts = list(self._alerts)
        for t, kind in alerts:
            i = int((t - first) // HOUR)
            if 0 <= i < 24 and kind in ALERT_KINDS:
                hours[i][kind] += 1
        return hours

    # -- opslaan en inlezen ----------------------------------------------------

    def _append_csv(self, b: _Block):
        try:
            new = not self.csv_path.exists() or self.csv_path.stat().st_size == 0
            self.csv_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.csv_path, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                if new:
                    writer.writerow(CSV_HEADER)
                writer.writerow([
                    datetime.fromtimestamp(b.tijd).astimezone().isoformat(sep=" ", timespec="minutes"),
                    b.personen, b.objecten, b.bekend, b.onbekend,
                    ";".join(f"{naam}={round(sec)}" for naam, sec in sorted(b.seconden.items())),
                    ";".join(sorted(b.namen, key=str.lower))])
        except OSError as exc:
            log.warning("Kan de statistiek niet opslaan in %s: %s", self.csv_path, exc)

    def _load(self):
        """Lees de laatste 24 uur terug uit statistiek.csv en meldingen.csv."""
        now = time.time()
        merged = {}
        for row in _csv_tail(self.csv_path):
            try:
                t = datetime.fromisoformat(row[0]).timestamp()
                if t < now - DAY - 60 or t > now + 60:
                    continue
                b = _Block(t, personen=int(row[1]), objecten=int(row[2]), bekend=int(row[3]),
                           onbekend=int(row[4]))
                for part in filter(None, row[5].split(";")):
                    naam, _, sec = part.rpartition("=")
                    b.seconden[naam] = float(sec)
                b.namen = set(filter(None, row[6].split(";")))
            except (ValueError, IndexError):
                continue  # kopregel of kapotte regel
            merged.setdefault(t, _Block(t)).merge(b)
        self._minutes.extend(merged[t] for t in sorted(merged))

        if self.alerts_csv is not None:
            for row in _csv_tail(Path(self.alerts_csv)):
                try:
                    t = time.mktime(time.strptime(row[0], "%Y-%m-%d %H:%M:%S"))
                except (ValueError, IndexError, OverflowError):
                    continue
                if t >= now - DAY - HOUR and len(row) > 1:
                    self._alerts.append((t, row[1]))
        # Meldingen die al in het geheugen staan, staan ook al in meldingen.csv.
        recent = self.alerts.recent(1) if self.alerts is not None else []
        self._last_alert_id = recent[-1]["id"] if recent else 0
        if self._minutes or self._alerts:
            log.info("Statistiek: %d minuten en %d meldingen van het afgelopen etmaal ingelezen",
                     len(self._minutes), len(self._alerts))


def _csv_tail(path: Path) -> list:
    """De regels van (het laatste stuk van) een csv-bestand."""
    try:
        with open(path, "rb") as f:
            size = f.seek(0, os.SEEK_END)
            f.seek(max(0, size - READ_TAIL))
            data = f.read()
    except OSError:
        return []
    if size > READ_TAIL:
        data = data[data.find(b"\n") + 1:]  # de eerste (halve) regel overslaan
    text = data.decode("utf-8", errors="replace")
    return list(csv.reader(io.StringIO(text)))


def _top_objects(blocks: list, limit: int = 10) -> list:
    total = {}
    for b in blocks:
        for naam, sec in b.seconden.items():
            total[naam] = total.get(naam, 0.0) + sec
    best = sorted(total.items(), key=lambda item: (-item[1], item[0]))[:limit]
    return [{"naam": naam, "minuten": round(sec / 60, 1)} for naam, sec in best if sec > 0]


def _copy(b: _Block) -> _Block:
    return _Block(b.tijd, b.personen, b.objecten, b.bekend, b.onbekend, dict(b.seconden), set(b.namen))


def _clean(name: str) -> str:
    """Namen zonder de tekens die we in de csv als scheiding gebruiken."""
    return " ".join(str(name).replace(";", " ").replace("=", " ").split()) or "?"
