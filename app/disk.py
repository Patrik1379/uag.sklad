"""Sledovanie voľného miesta na disku servera."""
import os
import shutil
import time

WARN_PCT, CRIT_PCT = 80, 90                 # obsadenosť v %
WARN_FREE, CRIT_FREE = 5 * 1024**3, 2 * 1024**3   # voľné miesto v bajtoch

_cache = {"at": 0.0, "data": None}


def usage(path, max_age=300):
    """Obsadenosť disku, na ktorom leží `path` (v Dockeri = disk servera). Výsledok sa pamätá 5 minút."""
    if _cache["data"] and time.time() - _cache["at"] < max_age:
        return _cache["data"]
    try:
        total, _used, free = shutil.disk_usage(path)
    except OSError:
        return None
    used = total - free                      # vrátane rezervy systému – to, čo aplikácie naozaj nemôžu použiť
    pct = round(used / total * 100) if total else 0
    level = "ok"
    if pct >= CRIT_PCT or free < CRIT_FREE:
        level = "crit"
    elif pct >= WARN_PCT or free < WARN_FREE:
        level = "warn"
    data = {"total": total, "used": used, "free": free, "pct": pct, "level": level}
    _cache.update(at=time.time(), data=data)
    return data


def folder_size(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def human(n):
    n = float(n or 0)
    for unit in ("B", "kB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "kB") else f"{n:.1f} {unit}".replace(".", ",")
        n /= 1024
