"""API pre prepojenie s inými aplikáciami (Projekt manažér).

Overenie: hlavička  Authorization: Bearer <kľúč>  – kľúč sa vygeneruje v Nastaveniach.

POST /api/v1/projekty          založí alebo aktualizuje projekt podľa "ref" (napr. "pm:12")
GET  /api/v1/projekty          stav všetkých prepojených projektov naraz (pre dashboard)
GET  /api/v1/projekty/<ref>    stav projektu v sklade (kompletnosť dielov)
GET  /api/v1/ping              overenie spojenia a kľúča
"""
import secrets
from datetime import date

from flask import Blueprint, abort, current_app, jsonify, request, url_for

from .db import get_db

bp = Blueprint("api", __name__, url_prefix="/api/v1")

STATUS_MAP = {"hotovy": "ukonceny", "ukonceny": "ukonceny", "zruseny": "ukonceny"}


def _check_key():
    from .mailer import get_settings

    key = get_settings().get("api_key") or ""
    auth = request.headers.get("Authorization", "")
    sent = auth[7:].strip() if auth.lower().startswith("bearer ") else request.headers.get("X-Api-Key", "")
    if not key:
        abort(_error(503, "V sklade nie je vygenerovaný API kľúč (Nastavenia → Prepojenie s Projekt manažérom)."))
    if not sent or not secrets.compare_digest(sent, key):
        abort(_error(401, "Neplatný API kľúč."))


def _error(code, msg):
    resp = jsonify(ok=False, error=msg)
    resp.status_code = code
    return resp


def _date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError:
        return None


def _base():
    from .mailer import get_settings

    return (get_settings().get("app_url") or request.url_root).rstrip("/")


def _project_info(p, s=None):
    from .services import project_lines, summarize

    s = s or summarize(project_lines(p["id"]))
    return {"ok": True, "id": p["id"], "url": f"{_base()}{url_for('main.project_detail', pid=p['id'])}",
            "name": p["name"], "order_no": p["order_no"], "status": p["status"],
            "items": s["n_items"], "items_ok": s["items_ok"], "items_missing": s["items_missing"],
            "items_partial": s["items_partial"], "required": s["required"], "covered": s["covered"],
            "missing": s["missing"], "pct": s["pct"]}


@bp.route("/ping")
def ping():
    _check_key()
    return jsonify(ok=True, app="Sklad dielov")


@bp.route("/projekty", methods=["POST"])
def project_upsert():
    _check_key()
    data = request.get_json(silent=True) or {}
    ref = str(data.get("ref") or "").strip()[:60]
    name = str(data.get("name") or "").strip()[:200]
    if not ref or not name:
        return _error(400, "Chýba ref alebo názov projektu.")
    order_no = str(data.get("order_no") or "").strip()[:100] or ref.upper().replace(":", "-")
    values = {
        "name": name, "order_no": order_no,
        "customer": str(data.get("customer") or "").strip()[:200] or None,
        "start_date": _date(data.get("start_date")), "deadline": _date(data.get("deadline")),
        "status": STATUS_MAP.get(str(data.get("status") or ""), "aktivny"),
        "external_url": str(data.get("url") or "").strip()[:300] or None,
    }
    db = get_db()
    p = db.query("SELECT * FROM projects WHERE external_ref = ?", (ref,), one=True)
    created = False
    if p:
        if not str(data.get("order_no") or "").strip():
            values.pop("order_no")          # číslo zákazky doplnené v sklade neprepisujeme náhradným
        cols = list(values)
        db.execute(f"UPDATE projects SET {', '.join(c + ' = ?' for c in cols)} WHERE id = ?",
                   [values[c] for c in cols] + [p["id"]])
        pid = p["id"]
    else:
        values.update(external_ref=ref, note=str(data.get("description") or "").strip() or None)
        cols = list(values)
        pid = db.execute(f"INSERT INTO projects ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) RETURNING id",
                         [values[c] for c in cols])
        created = True
    db.commit()
    if created:
        from .mailer import notify_new_project

        try:
            notify_new_project(pid, author=str(data.get("author") or "") or None, base_url=request.url_root,
                               source="Projekt manažér")
        except Exception as exc:  # oznámenie nesmie zablokovať prepojenie
            current_app.logger.warning("Oznámenie o projekte sa nepodarilo odoslať: %s", exc)
    p = db.query("SELECT * FROM projects WHERE id = ?", (pid,), one=True)
    info = _project_info(p)
    info["created"] = created
    return jsonify(info), 201 if created else 200


@bp.route("/projekty", methods=["GET"])
def project_list():
    from .services import projects_with_stats

    _check_key()
    out = {p["external_ref"]: _project_info(p, p["stats"])
           for p in projects_with_stats() if p.get("external_ref")}
    return jsonify(ok=True, projects=out)


@bp.route("/projekty/<path:ref>")
def project_status(ref):
    _check_key()
    p = get_db().query("SELECT * FROM projects WHERE external_ref = ?", (ref,), one=True)
    if not p:
        return _error(404, "Projekt v sklade neexistuje.")
    return jsonify(_project_info(p))
