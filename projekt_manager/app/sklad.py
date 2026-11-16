"""Prepojenie s aplikáciou Sklad dielov.

Projekt založený alebo upravený tu sa cez API založí / aktualizuje aj v sklade.
Adresa skladu a API kľúč sa nastavujú v Nastaveniach (kľúč sa vygeneruje v sklade).
"""
import json
import logging
import time
import urllib.error
import urllib.request

from flask import current_app

from .db import connect

log = logging.getLogger(__name__)


def enabled(cfg=None):
    cfg = cfg or current_app.config
    return bool(cfg.get("SKLAD_URL") and cfg.get("SKLAD_API_KEY"))


def _call(method, path, payload=None, timeout=10, cfg=None):
    """Zavolá API skladu. Vracia (ok, dáta alebo text chyby)."""
    cfg = cfg or current_app.config
    url = cfg["SKLAD_URL"].rstrip("/") + "/api/v1" + path
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=body, method=method, headers={
        "Authorization": "Bearer " + cfg["SKLAD_API_KEY"],
        "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            msg = json.loads(exc.read().decode("utf-8")).get("error")
        except Exception:
            msg = None
        return False, msg or f"Sklad odpovedal chybou {exc.code}."
    except urllib.error.URLError as exc:
        return False, f"Sklad nie je dostupný ({exc.reason})."
    except Exception as exc:  # timeout, neplatná odpoveď…
        return False, f"Chyba spojenia so skladom: {exc}"


def ping():
    if not enabled():
        return False, "Vyplňte adresu skladu aj API kľúč."
    ok, data = _call("GET", "/ping", timeout=8)
    return ok, ("Spojenie so skladom funguje." if ok else data)


def _store(project_id, sklad_id, status_text):
    conn = connect(current_app.config["DATABASE"])
    try:
        if sklad_id:
            conn.execute("UPDATE projects SET sklad_id = ?, sklad_status = ? WHERE id = ?",
                         (sklad_id, status_text, project_id))
        else:
            conn.execute("UPDATE projects SET sklad_status = ? WHERE id = ?", (status_text, project_id))
        if status_text:
            conn.execute("INSERT INTO project_log(project_id, user_id, message) VALUES (?, NULL, ?)",
                         (project_id, "Sklad: " + status_text))
        conn.commit()
    finally:
        conn.close()


def push(project_id, author=None):
    """Pošle projekt do skladu (založí alebo aktualizuje). Vracia (ok, správa)."""
    from .models import get_project

    if not enabled():
        return False, "Prepojenie so skladom nie je nastavené."
    p = get_project(project_id)
    if not p:
        return False, "Projekt neexistuje."
    payload = {
        "ref": f"pm:{p.id}", "name": p.name, "order_no": p.order_no or "", "customer": p.customer or "",
        "start_date": p.start_date.isoformat() if p.start_date else None,
        "deadline": p.deadline.isoformat() if p.deadline else None,
        "status": p.status, "description": p.description or "",
        "url": f"{current_app.config['APP_URL'].rstrip('/')}/projekty/{p.id}", "author": author or "",
    }
    ok, data = _call("POST", "/projekty", payload)
    if ok:
        created = data.get("created")
        forget()
        _store(project_id, data.get("id"), "projekt založený v sklade" if created else "")
        if p.image and not p.image_sync:          # obrázok pridaný pred prepojením
            from .images import push_to_sklad
            push_to_sklad(project_id)
        return True, "Projekt bol založený v sklade." if created else "Údaje v sklade boli aktualizované."
    log.warning("Sklad – projekt %s sa nepodarilo odoslať: %s", project_id, data)
    _store(project_id, None, "chyba – " + str(data))
    return False, str(data)


def status(project):
    """Stav dielov projektu v sklade pre detail projektu (None, ak nie je prepojený / dostupný)."""
    if not enabled() or not project.sklad_id:
        return None
    ok, data = _call("GET", f"/projekty/pm:{project.id}", timeout=3)
    return data if ok else {"error": data}


_cache = {"at": 0.0, "key": None, "data": None}


def stats_map(max_age=60):
    """Stav materiálu všetkých prepojených projektov {"pm:<id>": {...}} – na dashboard.

    Výsledok sa krátko pamätá, aby dashboard nečakal na sklad pri každom načítaní.
    Ak sklad nie je dostupný, vráti None (dashboard sa zobrazí bez údajov o materiáli).
    """
    if not enabled():
        return None
    key = (current_app.config["SKLAD_URL"], current_app.config["SKLAD_API_KEY"])
    if _cache["key"] == key and time.time() - _cache["at"] < max_age:
        return _cache["data"]
    ok, data = _call("GET", "/projekty", timeout=3)
    result = data.get("projects", {}) if ok and isinstance(data, dict) else None
    _cache.update(at=time.time(), key=key, data=result)
    return result


def forget():
    """Po zmene projektu načítame stav zo skladu znova."""
    _cache["at"] = 0.0


def upload_image(project_id, data, timeout=20):
    """Nahrá obrázok projektu do skladu (multipart/form-data)."""
    import uuid as _uuid

    cfg = current_app.config
    boundary = "----pm" + _uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"projekt.jpg\"\r\n"
            f"Content-Type: image/jpeg\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(cfg["SKLAD_URL"].rstrip("/") + f"/api/v1/projekty/pm:{project_id}/obrazok",
                                 data=body, method="POST", headers={
                                     "Authorization": "Bearer " + cfg["SKLAD_API_KEY"],
                                     "Content-Type": "multipart/form-data; boundary=" + boundary})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, json.loads(resp.read().decode("utf-8") or "{}")
    except Exception as exc:
        log.warning("Sklad – obrázok projektu %s sa nepodarilo odoslať: %s", project_id, exc)
        return False, {}


def download_image(project_id, timeout=20):
    cfg = current_app.config
    req = urllib.request.Request(cfg["SKLAD_URL"].rstrip("/") + f"/api/v1/projekty/pm:{project_id}/obrazok",
                                 headers={"Authorization": "Bearer " + cfg["SKLAD_API_KEY"]})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, resp.read()
    except Exception as exc:
        log.warning("Sklad – obrázok projektu %s sa nepodarilo stiahnuť: %s", project_id, exc)
        return False, None
