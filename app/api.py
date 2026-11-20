"""API pre prepojenie s inými aplikáciami (Projekt manažér).

Overenie: hlavička  Authorization: Bearer <kľúč>  – kľúč sa vygeneruje v Nastaveniach.

POST /api/v1/projekty          založí alebo aktualizuje projekt podľa "ref" (napr. "pm:12")
GET  /api/v1/projekty          stav všetkých prepojených projektov naraz (pre dashboard)
GET  /api/v1/projekty/<ref>    stav projektu v sklade (kompletnosť dielov)
GET  /api/v1/projekty/<ref>/obrazok   obrázok projektu (súbor JPEG)
POST /api/v1/projekty/<ref>/obrazok   nahratie nového obrázka projektu (pole "image")
DELETE /api/v1/projekty/<ref>         projekt bol zmazaný v Projektoch → zmaže sa aj tu (ak nemá diely),
                                      inak sa len ukončí a odpojí, aby sa kusovník a príjmy nestratili
GET  /api/v1/ping              overenie spojenia a kľúča
GET  /api/v1/pripomienky       spoločný zoznam pripomienok (zlepšovákov) pre všetky aplikácie
POST /api/v1/pripomienky       nová pripomienka (multipart: kind, text, page, source, author, ext_user, image)
POST /api/v1/pripomienky/<id>  zmena stavu / odpoveď (JSON: status, reply)
DELETE /api/v1/pripomienky/<id>
GET  /api/v1/pripomienky/<id>/obrazok
"""
import secrets
from datetime import date

from flask import Blueprint, abort, current_app, jsonify, request, send_from_directory, url_for

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
            "name": p["name"], "order_no": p["order_no"], "status": p["status"], "image": p.get("image"),
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


@bp.route("/projekty/<ref>")
def project_status(ref):
    _check_key()
    p = get_db().query("SELECT * FROM projects WHERE external_ref = ?", (ref,), one=True)
    if not p:
        return _error(404, "Projekt v sklade neexistuje.")
    return jsonify(_project_info(p))


def _by_ref(ref):
    return get_db().query("SELECT * FROM projects WHERE external_ref = ?", (ref,), one=True)


@bp.route("/projekty/<ref>/obrazok", methods=["GET"])
def project_image_get(ref):
    _check_key()
    p = _by_ref(ref)
    if not p or not p.get("image"):
        return _error(404, "Projekt nemá obrázok.")
    return send_from_directory(current_app.config["UPLOAD_FOLDER"], p["image"], max_age=0)


@bp.route("/projekty/<ref>/obrazok", methods=["POST"])
def project_image_set(ref):
    from .services import delete_image, save_image

    _check_key()
    p = _by_ref(ref)
    if not p:
        return _error(404, "Projekt v sklade neexistuje.")
    f = request.files.get("image")
    if not f or not f.filename:
        return _error(400, "Chýba súbor s obrázkom.")
    try:
        name = save_image(f)
    except Exception as exc:
        return _error(400, f"Obrázok sa nepodarilo uložiť: {exc}")
    db = get_db()
    db.execute("UPDATE projects SET image = ? WHERE id = ?", (name, p["id"]))
    db.commit()
    delete_image(p.get("image"))
    return jsonify(ok=True, image=name)


@bp.route("/projekty/<ref>", methods=["DELETE"])
def project_remove(ref):
    from datetime import datetime

    from .services import delete_image

    _check_key()
    p = _by_ref(ref)
    if not p:
        return jsonify(ok=True, result="missing")
    db = get_db()
    items = db.scalar("SELECT COUNT(*) FROM project_parts WHERE project_id = ?", (p["id"],)) or 0
    received = db.scalar("SELECT COUNT(*) FROM receipts WHERE project_id = ?", (p["id"],)) or 0
    if not items and not received:
        db.execute("UPDATE racks SET project_id = NULL WHERE project_id = ?", (p["id"],))
        db.execute("DELETE FROM projects WHERE id = ?", (p["id"],))
        db.commit()
        delete_image(p.get("image"))
        return jsonify(ok=True, result="deleted")
    note = (p.get("note") or "").rstrip()
    note += ("\n\n" if note else "") + (f"Projekt bol {datetime.now():%d.%m.%Y} zmazaný v Projektoch – v sklade zostal "
                                         f"ukončený kvôli kusovníku a príjmom. Ak už nie je potrebný, zmažte ho tu.")
    db.execute("UPDATE projects SET status = 'ukonceny', external_ref = NULL, external_url = NULL, note = ? WHERE id = ?",
               (note, p["id"]))
    db.commit()
    return jsonify(ok=True, result="closed", items=items, receipts=received)


# --- Pripomienky (spoločné pre Sklad aj Projekty) ------------------------------

def _feedback_json(f):
    page = f.get("page") or ""
    if page.startswith("/"):
        page = _base() + page                         # stránka v sklade – úplná adresa
    return {
        "id": f["id"], "kind": f["kind"], "status": f["status"], "text": f["text"], "reply": f.get("reply"),
        "page": page or None, "has_image": bool(f.get("image")),
        "source": f.get("source") or "sklad",
        "author": f.get("author") or f.get("full_name") or f.get("username") or "",
        "ext_user": f.get("ext_user"),
        "created_at": str(f.get("created_at") or "")[:19],
    }


@bp.route("/pripomienky", methods=["GET"])
def feedback_list():
    _check_key()
    rows = get_db().query("""SELECT f.*, u.username, u.full_name FROM feedback f
                             LEFT JOIN users u ON u.id = f.user_id ORDER BY f.created_at DESC, f.id DESC""")
    return jsonify(ok=True, items=[_feedback_json(r) for r in rows])


@bp.route("/pripomienky", methods=["POST"])
def feedback_create():
    from .routes import FEEDBACK_KINDS
    from .services import save_image

    _check_key()
    f = request.form
    text = (f.get("text") or "").strip()
    if not text:
        abort(_error(400, "Chýba text pripomienky."))
    kind = f.get("kind") if f.get("kind") in FEEDBACK_KINDS else "navrh"
    image = None
    img = request.files.get("image")
    if img and img.filename:
        try:
            image = save_image(img)
        except Exception as exc:
            abort(_error(400, f"Obrázok sa nepodarilo uložiť: {exc}"))
    db = get_db()
    db.execute("""INSERT INTO feedback (user_id, kind, text, page, image, source, author, ext_user)
                  VALUES (NULL, ?, ?, ?, ?, ?, ?, ?)""",
               (kind, text, (f.get("page") or "")[:255] or None, image, (f.get("source") or "projekty")[:20],
                (f.get("author") or "")[:150] or None, (f.get("ext_user") or "")[:100] or None))
    db.commit()
    return jsonify(ok=True)


def _feedback_or_404(fid):
    f = get_db().query("SELECT * FROM feedback WHERE id = ?", (fid,), one=True)
    if not f:
        abort(_error(404, "Pripomienka neexistuje."))
    return f


@bp.route("/pripomienky/<int:fid>", methods=["POST"])
def feedback_set(fid):
    from .routes import FEEDBACK_STATUS

    _check_key()
    f = _feedback_or_404(fid)
    data = request.get_json(silent=True) or {}
    status = data.get("status") if data.get("status") in FEEDBACK_STATUS else f["status"]
    db = get_db()
    db.execute("UPDATE feedback SET status = ?, reply = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
               (status, (data.get("reply") or "").strip() or None, fid))
    db.commit()
    return jsonify(ok=True)


@bp.route("/pripomienky/<int:fid>", methods=["DELETE"])
def feedback_delete(fid):
    from .services import delete_image

    _check_key()
    f = _feedback_or_404(fid)
    db = get_db()
    db.execute("DELETE FROM feedback WHERE id = ?", (fid,))
    db.commit()
    delete_image(f.get("image"))
    return jsonify(ok=True)


@bp.route("/pripomienky/<int:fid>/obrazok")
def feedback_image(fid):
    _check_key()
    f = _feedback_or_404(fid)
    if not f.get("image"):
        abort(404)
    name = ("t_" if request.args.get("nahlad") else "") + f["image"]
    return send_from_directory(current_app.config["UPLOAD_FOLDER"], name, max_age=3600)
