"""Hoe gaat het met de computer? CPU-temperatuur, belasting, geheugen en uptime.

Op de Raspberry Pi (Linux) lezen we dit uit speciale bestanden:
  /sys/class/thermal/thermal_zone0/temp   temperatuur van de processor (in duizendsten graden)
  /proc/stat                              hoeveel tijd de processor bezig was
  /proc/meminfo                           hoeveel geheugen er vrij is

Op Windows of macOS bestaan die bestanden niet. Dan geven we None ("onbekend")
terug, behalve als het pakket psutil geïnstalleerd is.
"""
import threading
import time
from pathlib import Path

THERMAL_FILE = Path("/sys/class/thermal/thermal_zone0/temp")
STAT_FILE = Path("/proc/stat")
MEMINFO_FILE = Path("/proc/meminfo")
MODEL_FILE = Path("/proc/device-tree/model")

STARTED = time.time()  # wanneer het grondstation gestart is (voor de uptime)

try:  # optioneel, handig op Windows en macOS
    import psutil
except ImportError:
    psutil = None

_lock = threading.Lock()
_last_cpu = None    # (moment, totale tijd, rusttijd) van de vorige meting
_last_load = None   # laatst berekende belasting in procent


def status() -> dict:
    """Alles in één keer, zoals de website het wil hebben."""
    return {"cpu_temp": cpu_temp(), "cpu_belasting": cpu_load(),
            "geheugen": memory_percent(), "uptime": uptime()}


def cpu_temp():
    """Temperatuur van de processor in graden Celsius, of None."""
    try:
        return round(int(THERMAL_FILE.read_text().strip()) / 1000, 1)
    except (OSError, ValueError):
        pass
    if psutil and hasattr(psutil, "sensors_temperatures"):
        try:
            for sensors in psutil.sensors_temperatures().values():
                if sensors:
                    return round(sensors[0].current, 1)
        except Exception:
            pass
    return None


def cpu_load():
    """Hoe druk de processor het heeft, in procent (0-100), of None.

    We vergelijken /proc/stat met de vorige meting: van alle tijd sinds toen,
    welk deel was de processor bezig? De eerste keer meten we twee keer kort na
    elkaar.
    """
    global _last_cpu, _last_load
    with _lock:
        sample = _read_cpu()
        if sample is None:
            if psutil:
                return round(psutil.cpu_percent(interval=None), 1)
            return None
        if _last_cpu is None:
            time.sleep(0.1)
            _last_cpu, sample = sample, _read_cpu()
        # Niet vaker dan eens per halve seconde rekenen; anders zijn de verschillen te klein.
        if sample[0] - _last_cpu[0] < 0.5 and _last_load is not None:
            return _last_load
        total = sample[1] - _last_cpu[1]
        idle = sample[2] - _last_cpu[2]
        _last_cpu = sample
        if total > 0:
            _last_load = round(max(0.0, min(100.0, 100.0 * (1 - idle / total))), 1)
        return _last_load


def _read_cpu():
    """(moment, totale tijd, rusttijd) uit de eerste regel van /proc/stat, of None."""
    try:
        fields = STAT_FILE.read_text().splitlines()[0].split()
    except (OSError, IndexError):
        return None
    if not fields or fields[0] != "cpu":
        return None
    values = [int(v) for v in fields[1:9]]  # user nice system idle iowait irq softirq steal
    return time.time(), sum(values), values[3] + values[4]


def memory_percent():
    """Hoeveel procent van het geheugen in gebruik is, of None."""
    try:
        info = {}
        for line in MEMINFO_FILE.read_text().splitlines():
            name, value = line.split(":", 1)
            info[name] = int(value.split()[0])
        return round(100.0 * (1 - info["MemAvailable"] / info["MemTotal"]), 1)
    except (OSError, ValueError, KeyError, ZeroDivisionError):
        pass
    if psutil:
        return round(psutil.virtual_memory().percent, 1)
    return None


def uptime() -> int:
    """Hoeveel seconden het grondstation al draait."""
    return int(time.time() - STARTED)


def device_model():
    """Bijv. 'Raspberry Pi 5 Model B Rev 1.0', of None op een gewone computer."""
    try:
        return MODEL_FILE.read_text().strip("\x00 \n") or None
    except OSError:
        return None

