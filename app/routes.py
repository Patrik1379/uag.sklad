import io
import os
from datetime import datetime

from flask import (Blueprint, abort, current_app, flash, jsonify, redirect,
                   render_template, request, send_file, send_from_directory,
                   url_for)

from .auth import admin_required, current_user, login_required
from .db import get_db
from .services import (location_label, location_parts, locations_map, racks_map, delete_image, demand_for_part, enrich, free_stock,
                       free_stock_map, next_part_code, project_lines,
                       projects_with_stats, read_bom, import_bom_items, save_image, search_parts, summarize,
                       LINES_SQL)

bp = Blueprint("main", __name__)

PART_FIELDS = ["code", "order_code", "name", "manufacturer", "supplier", "category",
               "unit", "location", "shelf", "params", "note"]
PROJECT_FIELDS = ["name", "order_no", "customer", "start_date", "deadline", "note", "status"]


def to_int(value, default=0):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


# =============================================================================
# Dashboard
# =============================================================================

@bp.route("/")
@login_required
def dashboard():
    db = get_db()
    projects = projects_with_stats("aktivny")
    total = {
        "required": sum(p["stats"]["required"] for p in projects),
        "covered": sum(p["stats"]["covered"] for p in projects),
        "n_items": sum(p["stats"]["n_items"] for p in projects),
        "items_ok": sum(p["stats"]["items_ok"] for p in projects),
        "items_partial": sum(p["stats"]["items_partial"] for p in projects),
        "items_missing": sum(p["stats"]["items_missing"] for p in projects),
    }
    total["missing"] = total["required"] - total["covered"]
    total["pct"] = round(100 * total["covered"] / total["required"]) if total["required"] else 0

    # Najviac chýbajúce diely naprieč aktívnymi projektmi
    lines = [enrich(l) for l in db.query(
        LINES_SQL + " JOIN projects pr ON pr.id = pp.project_id WHERE pr.status = 'aktivny'")]
    agg = {}
    for l in lines:
        if l["missing"] <= 0:
            continue
        a = agg.setdefault(l["part_id"], {"part_id": l["part_id"], "code": l["code"], "name": l["name"],
                                          "order_code": l["order_code"], "image": l["image"],
                                          "missing": 0, "projects": 0})
        a["missing"] += l["missing"]
        a["projects"] += 1
    top_missing = sorted(agg.values(), key=lambda a: -a["missing"])[:8]

    recent = db.query("""
        SELECT r.*, pa.code, pa.name AS part_name, pa.image, pr.name AS project_name, u.username
        FROM receipts r JOIN parts pa ON pa.id = r.part_id
        LEFT JOIN projects pr ON pr.id = r.project_id
        LEFT JOIN users u ON u.id = r.user_id
        WHERE r.qty > 0
        ORDER BY r.created_at DESC, r.id DESC LIMIT 8""")
    free_total = sum(free_stock_map().values())
    return render_template("dashboard.html", projects=projects, total=total,
                           top_missing=top_missing, recent=recent, free_total=free_total,
                           timeline=build_timeline(projects))


MONTHS_SK = ["jan", "feb", "mar", "apr", "máj", "jún", "júl", "aug", "sep", "okt", "nov", "dec"]
WARN_DAYS = 14


def _as_date(value):
    from datetime import date as _date, datetime as _dt

    if not value:
        return None
    if isinstance(value, _dt):
        return value.date()
    if isinstance(value, _date):
        return value
    try:
        return _dt.fromisoformat(str(value)[:10]).date()
    except ValueError:
        return None


def build_timeline(projects):
    """Údaje pre časovú os projektov na dashboarde (pozície v % šírky)."""
    from datetime import date as _date, timedelta

    today = _date.today()
    rows = []
    for p in projects:
        start = _as_date(p.get("start_date")) or _as_date(p.get("created_at")) or today
        end = _as_date(p.get("deadline"))
        if end and end < start:
            start = end
        rows.append({"p": p, "start": start, "end": end})
    if not rows:
        return None
    lo = min([r["start"] for r in rows] + [today])
    hi = max([r["end"] for r in rows if r["end"]] + [today + timedelta(days=14)])
    lo = lo.replace(day=1)
    hi = hi + timedelta(days=7)
    span = max((hi - lo).days, 1)

    def pos(d):
        return round(100 * (d - lo).days / span, 2)

    for r in rows:
        p, s = r["p"], r["p"]["stats"]
        complete = s["n_items"] > 0 and s["missing"] == 0
        if r["end"]:
            days = (r["end"] - today).days
            r["days_left"] = days
            if complete:
                r["state"], r["label"] = "ok", "kompletné"
            elif days < 0:
                r["state"], r["label"] = "late", f"po termíne {-days} d · chýba {s['missing']} ks"
            elif days <= WARN_DAYS:
                r["state"], r["label"] = "warn", f"o {days} d · chýba {s['missing']} ks"
            else:
                r["state"], r["label"] = "plan", f"o {days} d"
            r["left"] = pos(r["start"])
            r["width"] = max(pos(r["end"]) - r["left"], 1.2)
        else:
            r["state"], r["label"] = ("ok", "kompletné") if complete else ("none", "bez termínu")
            r["left"] = pos(r["start"])
            r["width"] = None
    rows.sort(key=lambda r: (r["end"] is None, r["end"] or today, r["p"]["name"]))

    # značky mesiacov
    ticks, d = [], lo
    months = (hi.year - lo.year) * 12 + hi.month - lo.month + 1
    step = 1 if months <= 8 else 2 if months <= 16 else 3
    i = 0
    while d <= hi:
        if i % step == 0:
            label = MONTHS_SK[d.month - 1] + (f" {d.year}" if d.month == 1 or not ticks else "")
            ticks.append({"left": pos(d), "label": label})
        d = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
        i += 1
    return {"rows": rows, "ticks": ticks, "today": pos(today), "today_label": today.strftime("%d.%m."),
            "warn_days": WARN_DAYS}


# =============================================================================
# Príjem tovaru
# =============================================================================

@bp.route("/prijem")
@login_required
def receive():
    db = get_db()
    q = request.args.get("q", "").strip()
    part_id = to_int(request.args.get("diel"), None)
    part, candidates, demand = None, [], []
    if part_id:
        part = db.query("SELECT * FROM parts WHERE id = ?", (part_id,), one=True)
    elif q:
        exact = db.query(
            "SELECT * FROM parts WHERE LOWER(order_code) = LOWER(?) OR LOWER(code) = LOWER(?) ORDER BY code",
            (q, q))
        if len(exact) == 1:
            part = exact[0]
        elif exact:
            candidates = exact
        else:
            candidates = search_parts(q, limit=30)
            # jediný nájdený diel otvoríme rovno, iba ak sa zhoduje celé objednávacie číslo/označenie (napr. bez pomlčiek)
            if len(candidates) == 1 and candidates[0]["_score"] >= 1000:
                part, candidates = candidates[0], []
    if part:
        demand = demand_for_part(part["id"])
        part["free"] = free_stock(part["id"])
    return render_template("receive.html", q=q, part=part, candidates=candidates, demand=demand)


@bp.route("/prijem/<int:part_id>", methods=["POST"])
@login_required
def receive_save(part_id):
    db = get_db()
    part = db.query("SELECT * FROM parts WHERE id = ?", (part_id,), one=True) or abort(404)
    uid = current_user()["id"]
    dl = request.form.get("delivery_note", "").strip()[:150] or None
    note = request.form.get("note", "").strip() or None
    allocations = []
    for key, value in request.form.items():
        if key.startswith("alloc_"):
            qty = to_int(value)
            if qty < 0:
                flash("Počet kusov nemôže byť záporný.", "error")
                return redirect(url_for("main.receive", diel=part_id))
            if qty > 0:
                target = key[6:]
                pid = None if target == "free" else to_int(target, None)
                if pid is not None and not db.scalar("SELECT COUNT(*) FROM projects WHERE id = ?", (pid,)):
                    abort(400)
                allocations.append((pid, qty))
    if not allocations:
        flash("Zadajte počet kusov aspoň pre jeden projekt alebo voľný sklad.", "error")
        return redirect(url_for("main.receive", diel=part_id))
    for pid, qty in allocations:
        db.execute(
            "INSERT INTO receipts (part_id, project_id, qty, user_id, delivery_note, note) VALUES (?, ?, ?, ?, ?, ?)",
            (part_id, pid, qty, uid, dl, note))
    db.commit()
    total = sum(q for _, q in allocations)
    flash(f"Naskladnené: {part['code']} – {total} {part['unit']}.", "ok")
    return redirect(url_for("main.receive"))


@bp.route("/projekty/<int:pid>/z-volneho/<int:part_id>", methods=["POST"])
@login_required
def assign_from_free(pid, part_id):
    db = get_db()
    line = next((l for l in project_lines(pid) if l["part_id"] == part_id), None) or abort(404)
    qty = min(free_stock(part_id), line["missing"], max(to_int(request.form.get("qty"), line["missing"]), 0))
    if qty <= 0:
        flash("Vo voľnom sklade nie je čo priradiť.", "error")
    else:
        uid = current_user()["id"]
        db.execute("INSERT INTO receipts (part_id, project_id, qty, user_id, note) VALUES (?, NULL, ?, ?, ?)",
                   (part_id, -qty, uid, "Presun do projektu"))
        db.execute("INSERT INTO receipts (part_id, project_id, qty, user_id, note) VALUES (?, ?, ?, ?, ?)",
                   (part_id, pid, qty, uid, "Presun z voľného skladu"))
        db.commit()
        flash(f"Z voľného skladu priradených {qty} ks.", "ok")
    back = request.form.get("back", "")
    if back.startswith("/") and not back.startswith("//"):
        return redirect(back)
    return redirect(url_for("main.project_detail", pid=pid))


@bp.route("/prijmy/<int:rid>/storno", methods=["POST"])
@admin_required
def receipt_delete(rid):
    db = get_db()
    r = db.query("SELECT * FROM receipts WHERE id = ?", (rid,), one=True) or abort(404)
    db.execute("DELETE FROM receipts WHERE id = ?", (rid,))
    db.commit()
    flash("Záznam o príjme bol stornovaný.", "ok")
    return redirect(request.form.get("back") or url_for("main.part_detail", part_id=r["part_id"]))


# =============================================================================
# Projekty
# =============================================================================

@bp.route("/projekty")
@login_required
def projects():
    from .services import _compact, _fold

    f = {
        "stav": request.args.get("stav", "aktivny"),
        "q": request.args.get("q", "").strip(),
        "zakaznik": request.args.get("zakaznik", "").strip(),
        "kompletnost": request.args.get("kompletnost", ""),
        "zoradit": request.args.get("zoradit", "termin"),
    }
    if f["stav"] not in ("aktivny", "ukonceny", "vsetky"):
        f["stav"] = "aktivny"
    all_rows = projects_with_stats(None if f["stav"] == "vsetky" else f["stav"])
    customers = sorted({p["customer"] for p in all_rows if p["customer"]}, key=_fold)
    rows = all_rows
    if f["q"]:
        tokens = _fold(f["q"]).split()
        qc = _compact(f["q"])
        def hit(p):
            fields = [p["name"], p["order_no"], p["customer"], p["note"]]
            hay = " ".join(_fold(x) for x in fields)
            comp = "".join(_compact(x) for x in fields)
            return (qc and qc in comp) or all(t in hay for t in tokens)
        rows = [p for p in rows if hit(p)]
    if f["zakaznik"]:
        rows = [p for p in rows if _fold(p["customer"]) == _fold(f["zakaznik"])]
    if f["kompletnost"] == "chyba":
        rows = [p for p in rows if p["stats"]["missing"] > 0]
    elif f["kompletnost"] == "hotove":
        rows = [p for p in rows if p["stats"]["n_items"] and p["stats"]["missing"] == 0]
    sorters = {
        "termin": lambda p: (p["deadline"] is None, str(p["deadline"] or "")),
        "zakazka": lambda p: _fold(p["order_no"]),
        "nazov": lambda p: _fold(p["name"]),
        "zakaznik": lambda p: (not p["customer"], _fold(p["customer"])),
        "kompletnost": lambda p: (p["stats"]["pct"], -p["stats"]["missing"]),
        "chyba": lambda p: -p["stats"]["missing"],
    }
    rows = sorted(rows, key=sorters.get(f["zoradit"], sorters["termin"]))
    return render_template("projects.html", projects=rows, f=f, status=f["stav"],
                           customers=customers, total_count=len(all_rows))


def _project_form_data():
    data = {f: request.form.get(f, "").strip() or None for f in PROJECT_FIELDS}
    data["status"] = data["status"] if data["status"] in ("aktivny", "ukonceny") else "aktivny"
    return data


def _project_form_image(db, pid, current):
    image = current
    if request.form.get("remove_image") and image:
        delete_image(image)
        image = None
    f = request.files.get("image")
    if f and f.filename:
        try:
            new = save_image(f)
            delete_image(image)
            image = new
        except Exception as exc:
            flash(f"Obrázok sa nepodarilo uložiť: {exc}", "error")
    if image != current:
        db.execute("UPDATE projects SET image = ? WHERE id = ?", (image, pid))


@bp.route("/projekty/novy", methods=["GET", "POST"])
@admin_required
def project_new():
    if request.method == "POST":
        data = _project_form_data()
        if not data["name"] or not data["order_no"]:
            flash("Názov projektu a číslo zakázky sú povinné.", "error")
            return render_template("project_form.html", project=data)
        db = get_db()
        pid = db.execute(
            f"INSERT INTO projects ({', '.join(PROJECT_FIELDS)}) VALUES ({', '.join('?' * len(PROJECT_FIELDS))}) RETURNING id",
            [data[f] for f in PROJECT_FIELDS])
        _project_form_image(db, pid, None)
        db.commit()
        flash("Projekt bol vytvorený. Teraz doň pridajte diely.", "ok")
        return redirect(url_for("main.project_detail", pid=pid))
    return render_template("project_form.html", project={"status": "aktivny"})


@bp.route("/projekty/<int:pid>/upravit", methods=["GET", "POST"])
@admin_required
def project_edit(pid):
    db = get_db()
    project = db.query("SELECT * FROM projects WHERE id = ?", (pid,), one=True) or abort(404)
    if request.method == "POST":
        data = _project_form_data()
        if not data["name"] or not data["order_no"]:
            flash("Názov projektu a číslo zakázky sú povinné.", "error")
            return render_template("project_form.html", project={**project, **data})
        db.execute(f"UPDATE projects SET {', '.join(f + ' = ?' for f in PROJECT_FIELDS)} WHERE id = ?",
                   [data[f] for f in PROJECT_FIELDS] + [pid])
        _project_form_image(db, pid, project.get("image"))
        db.commit()
        flash("Projekt bol uložený.", "ok")
        return redirect(url_for("main.project_detail", pid=pid))
    return render_template("project_form.html", project=project)


@bp.route("/projekty/<int:pid>/zmazat", methods=["POST"])
@admin_required
def project_delete(pid):
    db = get_db()
    # príjmy projektu presunieme do voľného skladu, aby kusy "nezmizli"
    db.execute("UPDATE receipts SET project_id = NULL WHERE project_id = ?", (pid,))
    db.execute("DELETE FROM project_parts WHERE project_id = ?", (pid,))
    db.execute("UPDATE racks SET project_id = NULL WHERE project_id = ?", (pid,))
    img = db.scalar("SELECT image FROM projects WHERE id = ?", (pid,))
    db.execute("DELETE FROM projects WHERE id = ?", (pid,))
    db.commit()
    delete_image(img)
    flash("Projekt bol zmazaný, naskladnené kusy sa presunuli do voľného skladu.", "ok")
    return redirect(url_for("main.projects"))


@bp.route("/projekty/<int:pid>")
@login_required
def project_detail(pid):
    db = get_db()
    project = db.query("SELECT * FROM projects WHERE id = ?", (pid,), one=True) or abort(404)
    lines = project_lines(pid)
    free = free_stock_map()
    for l in lines:
        l["free"] = free.get(l["part_id"], 0)
    order = {"missing": 0, "partial": 1, "ok": 2}
    lines.sort(key=lambda l: (order[l["state"]], l["code"]))
    return render_template("project_detail.html", project=project, lines=lines, stats=summarize(lines),
                           filt=request.args.get("filter", "vsetky"))


@bp.route("/projekty/<int:pid>/diely", methods=["POST"])
@admin_required
def project_add_part(pid):
    db = get_db()
    db.query("SELECT id FROM projects WHERE id = ?", (pid,), one=True) or abort(404)
    key = request.form.get("part", "").strip()
    qty = max(to_int(request.form.get("qty"), 1), 1)
    part = None
    if key:
        part = db.query("SELECT * FROM parts WHERE LOWER(code) = LOWER(?)", (key.split(" — ")[0],), one=True) \
            or db.query("SELECT * FROM parts WHERE LOWER(order_code) = LOWER(?)", (key,), one=True)
    if not part:
        flash("Diel sa nenašiel. Vyberte existujúcu skladovú kartu alebo vytvorte novú.", "error")
        return redirect(url_for("main.project_detail", pid=pid))
    existing = db.query("SELECT * FROM project_parts WHERE project_id = ? AND part_id = ?", (pid, part["id"]), one=True)
    if existing:
        db.execute("UPDATE project_parts SET qty_required = qty_required + ? WHERE id = ?", (qty, existing["id"]))
        flash(f"{part['code']}: množstvo navýšené o {qty}.", "ok")
    else:
        db.execute("INSERT INTO project_parts (project_id, part_id, qty_required) VALUES (?, ?, ?)",
                   (pid, part["id"], qty))
        flash(f"{part['code']} pridaný do projektu.", "ok")
    db.commit()
    return redirect(url_for("main.project_detail", pid=pid))


@bp.route("/projekty/<int:pid>/diely/<int:pp_id>", methods=["POST"])
@admin_required
def project_line_update(pid, pp_id):
    db = get_db()
    if request.form.get("action") == "remove":
        db.execute("DELETE FROM project_parts WHERE id = ? AND project_id = ?", (pp_id, pid))
        flash("Položka bola odobratá z projektu.", "ok")
    else:
        qty = to_int(request.form.get("qty"), -1)
        if qty < 1:
            flash("Množstvo musí byť aspoň 1.", "error")
        else:
            db.execute("UPDATE project_parts SET qty_required = ? WHERE id = ? AND project_id = ?", (qty, pp_id, pid))
            flash("Množstvo bolo upravené.", "ok")
    db.commit()
    return redirect(url_for("main.project_detail", pid=pid))


@bp.route("/projekty/<int:pid>/import", methods=["POST"])
@admin_required
def project_import(pid):
    db = get_db()
    db.query("SELECT id FROM projects WHERE id = ?", (pid,), one=True) or abort(404)
    f = request.files.get("file")
    if not f or not f.filename:
        flash("Vyberte súbor (XLSX alebo CSV).", "error")
        return redirect(url_for("main.project_detail", pid=pid))
    try:
        bom = read_bom(f)
    except Exception as exc:
        flash(f"Súbor sa nepodarilo načítať: {exc}", "error")
        return redirect(url_for("main.project_detail", pid=pid))
    st = import_bom_items(pid, bom["items"])
    db.commit()
    flash(f"Import hotový: {st['linked']} položiek v projekte, {st['created']} nových skladových kariet"
          + (f", {st['images']} obrázkov" if st["images"] else "") + ".", "ok")
    return redirect(url_for("main.project_detail", pid=pid))


@bp.route("/projekty/import", methods=["POST"])
@admin_required
def project_import_new():
    """Nový projekt priamo z kusovníka – názov a číslo zakázky sa načítajú z hlavičky súboru."""
    db = get_db()
    f = request.files.get("file")
    if not f or not f.filename:
        flash("Vyberte súbor s kusovníkom (XLSX alebo CSV).", "error")
        return redirect(url_for("main.projects"))
    try:
        bom = read_bom(f)
    except Exception as exc:
        flash(f"Súbor sa nepodarilo načítať: {exc}", "error")
        return redirect(url_for("main.projects"))
    meta = bom["meta"]
    base = os.path.splitext(os.path.basename(f.filename))[0]
    name = (request.form.get("name") or "").strip() or meta.get("device") or meta.get("assembly") or base
    order_no = (request.form.get("order_no") or "").strip() or meta.get("serial") or meta.get("assembly") or base
    existing = db.query("SELECT id, name FROM projects WHERE LOWER(order_no) = LOWER(?) AND status = 'aktivny'",
                        (order_no,), one=True)
    if existing:
        pid, name = existing["id"], existing["name"]
        note_new = False
    else:
        note = "\n".join(x for x in [
            f"Importované z kusovníka: {f.filename}",
            f"Zostava: {meta['assembly']}" if meta.get("assembly") else "",
            f"Typ: {meta['type']}" if meta.get("type") else "",
            f"Rok výroby: {meta['year']}" if meta.get("year") else "",
            f"Autor kusovníka: {meta['author']}" if meta.get("author") else ""] if x)
        pid = db.execute("INSERT INTO projects (name, order_no, note, status) VALUES (?, ?, ?, 'aktivny') RETURNING id",
                         (name[:200], order_no[:100], note))
        note_new = True
    st = import_bom_items(pid, bom["items"])
    db.commit()
    flash(("Vytvorený projekt" if note_new else "Kusovník pridaný do existujúceho projektu")
          + f" „{name}“ (zákazka {order_no}): {st['linked']} položiek, {st['created']} nových skladových kariet"
          + (f", {st['images']} obrázkov" if st["images"] else "") + ".", "ok")
    return redirect(url_for("main.project_detail", pid=pid))


@bp.route("/projekty/<int:pid>/export.xlsx")
@login_required
def project_export(pid):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    db = get_db()
    project = db.query("SELECT * FROM projects WHERE id = ?", (pid,), one=True) or abort(404)
    lines = project_lines(pid)
    wb = Workbook()
    ws = wb.active
    ws.title = "Diely"
    ws.append([f"{project['name']} – zákazka {project['order_no']}"])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append([])
    head = ["Označenie", "Objednávacie číslo", "Názov", "Výrobca", "Umiestnenie", "Potrebné", "Naskladnené", "Chýba", "Stav"]
    ws.append(head)
    for c in ws[3]:
        c.font = Font(bold=True)
    fills = {"ok": "D6F2D6", "partial": "FFF0C2", "missing": "F8D7D7"}
    labels = {"ok": "Naskladnené", "partial": "Čiastočne", "missing": "Chýba"}
    for l in lines:
        ws.append([l["code"], l["order_code"], l["name"], l["manufacturer"], location_label(l), l["qty_required"],
                   l["received"], l["missing"], labels[l["state"]]])
        for c in ws[ws.max_row]:
            c.fill = PatternFill("solid", fgColor=fills[l["state"]])
    for col, w in zip("ABCDEFGHI", (14, 22, 40, 18, 30, 10, 12, 10, 14)):
        ws.column_dimensions[col].width = w
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    fname = f"zakazka_{project['order_no']}_{datetime.now():%Y%m%d}.xlsx".replace("/", "-")
    return send_file(buf, as_attachment=True, download_name=fname,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@bp.route("/sablona-kusovnika.xlsx")
@login_required
def bom_template():
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.append(["Označenie", "Objednávacie číslo", "Názov", "Výrobca", "Dodávateľ", "Množstvo"])
    for c in ws[1]:
        c.font = Font(bold=True)
    ws.append(["", "6ES7214-1AG40-0XB0", "PLC S7-1200 CPU 1214C", "Siemens", "", 2])
    for col, w in zip("ABCDEF", (14, 24, 40, 18, 18, 10)):
        ws.column_dimensions[col].width = w
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name="sablona_kusovnika.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# =============================================================================
# Skladové karty (diely)
# =============================================================================

@bp.route("/diely")
@login_required
def parts():
    db = get_db()
    q = request.args.get("q", "").strip()
    sql = """SELECT pa.*,
               (SELECT COALESCE(SUM(qty), 0) FROM receipts r WHERE r.part_id = pa.id) AS stock,
               (SELECT COALESCE(SUM(qty), 0) FROM receipts r WHERE r.part_id = pa.id AND r.project_id IS NULL) AS free,
               (SELECT COALESCE(SUM(pp.qty_required), 0) FROM project_parts pp
                  JOIN projects pr ON pr.id = pp.project_id
                  WHERE pp.part_id = pa.id AND pr.status = 'aktivny') AS planned
             FROM parts pa"""
    only_free = request.args.get("volne") == "1"
    rack_filter = to_int(request.args.get("regal"), None)
    loc_filter = to_int(request.args.get("lokacia"), None)
    rows = db.query(sql + " ORDER BY pa.code")
    if rack_filter:
        rows = [r for r in rows if r["rack_id"] == rack_filter]
    if loc_filter:
        rows = [r for r in rows if (location_parts(r)[0] or {}).get("id") == loc_filter]
    if q:
        found = search_parts(q, limit=500)
        order = {p["id"]: i for i, p in enumerate(found)}
        matched = {p["id"]: p["matched"] for p in found}
        rows = [r for r in rows if r["id"] in order]
        for r in rows:
            r["matched"] = matched[r["id"]]
        rows.sort(key=lambda r: order[r["id"]])
    if only_free:
        rows = [r for r in rows if int(r["free"] or 0) > 0]
    return render_template("parts.html", parts=rows[:500], q=q, only_free=only_free,
                           rack=racks_map().get(rack_filter), loc=locations_map().get(loc_filter))


# =============================================================================
# Regály
# =============================================================================

@bp.route("/regaly", methods=["GET", "POST"])
@login_required
def racks():
    db = get_db()
    if request.method == "POST":
        if request.form.get("kind") == "location":
            name = request.form.get("name", "").strip()[:100]
            if not name:
                flash("Zadajte názov lokácie (napr. Hlavný sklad).", "error")
            elif db.scalar("SELECT COUNT(*) FROM locations WHERE LOWER(name) = LOWER(?)", (name,)):
                flash(f"Lokácia {name} už existuje.", "error")
            else:
                db.execute("INSERT INTO locations (name, note) VALUES (?, ?)",
                           (name, request.form.get("note", "").strip() or None))
                db.commit()
                flash(f"Lokácia {name} bola pridaná.", "ok")
            return redirect(url_for("main.racks"))
        code = request.form.get("code", "").strip().upper()[:30]
        loc_id = _valid_location(request.form.get("location_id"))
        if not code:
            flash("Zadajte označenie regálu (napr. R1).", "error")
        elif _rack_exists(code, loc_id):
            flash(f"Regál {code} v tejto lokácii už existuje.", "error")
        else:
            db.execute(
                "INSERT INTO racks (code, name, location_id, shelf_count, project_id, note) VALUES (?, ?, ?, ?, ?, ?)",
                (code, request.form.get("name", "").strip() or None, loc_id, _shelf_count(),
                 to_int(request.form.get("project_id"), None), request.form.get("note", "").strip() or None))
            db.commit()
            flash(f"Regál {code} bol pridaný.", "ok")
        return redirect(url_for("main.racks"))
    counts = {r["rack_id"]: r["n"] for r in db.query(
        "SELECT rack_id, COUNT(*) AS n FROM parts WHERE rack_id IS NOT NULL GROUP BY rack_id")}
    rows = list(racks_map().values())
    for r in rows:
        r["parts"] = counts.get(r["id"], 0)
    locations = list(locations_map().values())
    for l in locations:
        l["racks"] = sum(1 for r in rows if r["location_id"] == l["id"])
        l["parts"] = sum(r["parts"] for r in rows if r["location_id"] == l["id"]) + int(db.scalar(
            "SELECT COUNT(*) FROM parts WHERE rack_id IS NULL AND location_id = ?", (l["id"],)) or 0)
    projects = db.query("SELECT id, name, order_no FROM projects ORDER BY status, name")
    return render_template("racks.html", racks=rows, locations=locations, projects=projects)


def _valid_location(value):
    lid = to_int(value, None)
    return lid if lid in locations_map() else None


def _shelf_count():
    n = to_int(request.form.get("shelf_count"), 0)
    return max(0, min(n, 50)) or None


def _rack_exists(code, location_id, exclude_id=None):
    db = get_db()
    sql = "SELECT COUNT(*) FROM racks WHERE UPPER(code) = ? AND " + (
        "location_id = ?" if location_id else "location_id IS NULL")
    params = [code] + ([location_id] if location_id else [])
    if exclude_id:
        sql += " AND id <> ?"
        params.append(exclude_id)
    return bool(db.scalar(sql, params))


@bp.route("/regaly/<int:rack_id>", methods=["POST"])
@login_required
def rack_update(rack_id):
    db = get_db()
    rack = db.query("SELECT * FROM racks WHERE id = ?", (rack_id,), one=True) or abort(404)
    if request.form.get("action") == "delete":
        if current_user()["role"] != "admin":
            abort(403)
        db.execute("UPDATE parts SET location_id = ? WHERE rack_id = ? AND location_id IS NULL",
                   (rack["location_id"], rack_id))
        db.execute("UPDATE parts SET rack_id = NULL WHERE rack_id = ?", (rack_id,))
        db.execute("DELETE FROM racks WHERE id = ?", (rack_id,))
        db.commit()
        flash(f"Regál {rack['code']} bol zmazaný. Diely v ňom zostali bez regálu.", "ok")
        return redirect(url_for("main.racks"))
    code = request.form.get("code", "").strip().upper()[:30] or rack["code"]
    loc_id = _valid_location(request.form.get("location_id"))
    if _rack_exists(code, loc_id, exclude_id=rack_id):
        flash(f"Regál {code} v tejto lokácii už existuje.", "error")
        return redirect(url_for("main.racks"))
    db.execute("UPDATE racks SET code = ?, name = ?, location_id = ?, shelf_count = ?, project_id = ?, note = ? WHERE id = ?",
               (code, request.form.get("name", "").strip() or None, loc_id, _shelf_count(),
                to_int(request.form.get("project_id"), None),
                request.form.get("note", "").strip() or None, rack_id))
    db.commit()
    flash(f"Regál {code} bol uložený.", "ok")
    return redirect(url_for("main.racks"))


@bp.route("/lokacie/<int:loc_id>", methods=["POST"])
@login_required
def location_update(loc_id):
    db = get_db()
    loc = db.query("SELECT * FROM locations WHERE id = ?", (loc_id,), one=True) or abort(404)
    if request.form.get("action") == "delete":
        if current_user()["role"] != "admin":
            abort(403)
        db.execute("UPDATE racks SET location_id = NULL WHERE location_id = ?", (loc_id,))
        db.execute("UPDATE parts SET location_id = NULL WHERE location_id = ?", (loc_id,))
        db.execute("DELETE FROM locations WHERE id = ?", (loc_id,))
        db.commit()
        flash(f"Lokácia {loc['name']} bola zmazaná. Jej regály zostali bez lokácie.", "ok")
        return redirect(url_for("main.racks"))
    name = request.form.get("name", "").strip()[:100] or loc["name"]
    if db.scalar("SELECT COUNT(*) FROM locations WHERE LOWER(name) = LOWER(?) AND id <> ?", (name, loc_id)):
        flash(f"Lokácia {name} už existuje.", "error")
        return redirect(url_for("main.racks"))
    db.execute("UPDATE locations SET name = ?, note = ? WHERE id = ?",
               (name, request.form.get("note", "").strip() or None, loc_id))
    db.commit()
    flash(f"Lokácia {name} bola uložená.", "ok")
    return redirect(url_for("main.racks"))


@bp.route("/volny-sklad")
@login_required
def free_stock_view():
    """Diely na voľnom sklade + projekty, v ktorých tieto diely ešte chýbajú."""
    db = get_db()
    free = free_stock_map()
    parts = []
    if free:
        all_parts = {p["id"]: p for p in db.query("SELECT * FROM parts")}
        projects = {p["id"]: p for p in db.query("SELECT * FROM projects WHERE status = 'aktivny'")}
        lines = [enrich(l) for l in db.query(LINES_SQL)]
        need = {}
        for l in lines:
            if l["missing"] > 0 and l["project_id"] in projects and l["part_id"] in free:
                l["project"] = projects[l["project_id"]]
                need.setdefault(l["part_id"], []).append(l)
        for pid, qty in free.items():
            p = all_parts.get(pid)
            if not p:
                continue
            p["free"] = qty
            p["needed_in"] = sorted(need.get(pid, []),
                                    key=lambda l: (l["project"]["deadline"] is None, str(l["project"]["deadline"] or "")))
            parts.append(p)
        parts.sort(key=lambda p: (not p["needed_in"], p["code"]))
    return render_template("free_stock.html", parts=parts,
                           total=sum(p["free"] for p in parts),
                           usable=sum(1 for p in parts if p["needed_in"]))


@bp.route("/api/diely")
@login_required
def parts_api():
    q = request.args.get("q", "").strip()
    rows = [{k: p[k] for k in ("id", "code", "order_code", "name")} for p in search_parts(q, limit=20)]
    return jsonify(rows)


def _part_form_data():
    data = {f: (request.form.get(f, "").strip() or None) for f in PART_FIELDS}
    data["unit"] = data["unit"] or "ks"
    # lokácia
    if request.form.get("location_id") == "new":
        data["location_id"] = _create_location_from_form()
    else:
        data["location_id"] = _valid_location(request.form.get("location_id"))
    # regál
    if request.form.get("rack_id") == "new":
        data["rack_id"] = _create_rack_from_form(data["location_id"])
    else:
        rid = to_int(request.form.get("rack_id"), None)
        data["rack_id"] = rid if rid in racks_map() else None
    rack = racks_map().get(data["rack_id"])
    if rack and rack["location_id"]:
        data["location_id"] = rack["location_id"]
    # polica: výber zo zoznamu alebo ručne zadaná
    data["shelf"] = (request.form.get("shelf") or request.form.get("shelf_text") or "").strip()[:50] or None
    return data


def _create_location_from_form():
    db = get_db()
    name = request.form.get("new_location_name", "").strip()[:100]
    if not name:
        return None
    existing = db.query("SELECT id FROM locations WHERE LOWER(name) = LOWER(?)", (name,), one=True)
    if existing:
        return existing["id"]
    return db.execute("INSERT INTO locations (name) VALUES (?) RETURNING id", (name,))


def _create_rack_from_form(location_id):
    """Nový regál zadaný priamo na skladovej karte (ak už v lokácii existuje, použije sa)."""
    db = get_db()
    code = request.form.get("new_rack_code", "").strip().upper()[:30]
    name = request.form.get("new_rack_name", "").strip() or None
    shelves = max(0, min(to_int(request.form.get("new_rack_shelves"), 0), 50)) or None
    if not code:
        n = 1
        while _rack_exists(f"R{n}", location_id):
            n += 1
        code = f"R{n}"
    loc_sql = "location_id = ?" if location_id else "location_id IS NULL"
    existing = db.query(f"SELECT id FROM racks WHERE UPPER(code) = ? AND {loc_sql}",
                        [code] + ([location_id] if location_id else []), one=True)
    if existing:
        return existing["id"]
    rid = db.execute("INSERT INTO racks (code, name, location_id, shelf_count) VALUES (?, ?, ?, ?) RETURNING id",
                     (code, name, location_id, shelves))
    from flask import g
    g.pop("racks", None)  # nový regál musí byť viditeľný v racks_map()
    return rid


@bp.route("/diely/novy", methods=["GET", "POST"])
@login_required
def part_new():
    db = get_db()
    projects = db.query("SELECT id, name, order_no FROM projects WHERE status = 'aktivny' ORDER BY name")
    if request.method == "POST":
        data = _part_form_data()
        data["code"] = data["code"] or next_part_code()
        if not data["name"]:
            flash("Názov dielu je povinný.", "error")
            return render_template("part_form.html", part=data, projects=projects)
        if db.scalar("SELECT COUNT(*) FROM parts WHERE LOWER(code) = LOWER(?)", (data["code"],)):
            flash(f"Označenie {data['code']} už existuje – musí byť unikátne.", "error")
            return render_template("part_form.html", part=data, projects=projects)
        img = request.files.get("image")
        if img and img.filename:
            try:
                data["image"] = save_image(img)
            except Exception as exc:
                flash(f"Obrázok sa nepodarilo uložiť: {exc}", "error")
        cols = PART_FIELDS + ["location_id", "rack_id"] + (["image"] if data.get("image") else [])
        part_id = db.execute(
            f"INSERT INTO parts ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) RETURNING id",
            [data[c] for c in cols])
        pid = to_int(request.form.get("project_id"), None)
        if pid and current_user()["role"] == "admin":
            db.execute("INSERT INTO project_parts (project_id, part_id, qty_required) VALUES (?, ?, ?)",
                       (pid, part_id, max(to_int(request.form.get("project_qty"), 1), 1)))
        db.commit()
        flash(f"Skladová karta {data['code']} bola vytvorená.", "ok")
        if pid:
            return redirect(url_for("main.project_detail", pid=pid))
        if request.form.get("back") == "prijem":
            return redirect(url_for("main.receive", diel=part_id))
        return redirect(url_for("main.part_detail", part_id=part_id))
    part = {"unit": "ks", "order_code": request.args.get("order_code", "")}
    return render_template("part_form.html", part=part, projects=projects,
                           preset_project=to_int(request.args.get("projekt"), None),
                           back=request.args.get("back", ""))


@bp.route("/diely/<int:part_id>")
@login_required
def part_detail(part_id):
    db = get_db()
    part = db.query("SELECT * FROM parts WHERE id = ?", (part_id,), one=True) or abort(404)
    lines = [enrich(l) for l in db.query(
        LINES_SQL + " WHERE pp.part_id = ?", (part_id,))]
    projects = {p["id"]: p for p in db.query("SELECT * FROM projects")}
    for l in lines:
        l["project"] = projects.get(l["project_id"])
    history = db.query("""
        SELECT r.*, pr.name AS project_name, pr.order_no, u.username
        FROM receipts r LEFT JOIN projects pr ON pr.id = r.project_id
        LEFT JOIN users u ON u.id = r.user_id
        WHERE r.part_id = ? ORDER BY r.created_at DESC, r.id DESC""", (part_id,))
    part["free"] = free_stock(part_id)
    part["stock"] = sum(int(h["qty"]) for h in history)
    return render_template("part_detail.html", part=part, lines=lines, history=history)


@bp.route("/diely/<int:part_id>/upravit", methods=["GET", "POST"])
@login_required
def part_edit(part_id):
    db = get_db()
    part = db.query("SELECT * FROM parts WHERE id = ?", (part_id,), one=True) or abort(404)
    if request.method == "POST":
        data = _part_form_data()
        data["code"] = data["code"] or part["code"]
        if not data["name"]:
            flash("Názov dielu je povinný.", "error")
            return render_template("part_form.html", part={**part, **data}, projects=[])
        if db.scalar("SELECT COUNT(*) FROM parts WHERE LOWER(code) = LOWER(?) AND id <> ?", (data["code"], part_id)):
            flash(f"Označenie {data['code']} už používa iný diel.", "error")
            return render_template("part_form.html", part={**part, **data}, projects=[])
        image = part["image"]
        img = request.files.get("image")
        if request.form.get("remove_image"):
            delete_image(image)
            image = None
        if img and img.filename:
            try:
                new = save_image(img)
                delete_image(image)
                image = new
            except Exception as exc:
                flash(f"Obrázok sa nepodarilo uložiť: {exc}", "error")
        data["image"] = image
        cols = PART_FIELDS + ["location_id", "rack_id", "image"]
        db.execute(f"UPDATE parts SET {', '.join(c + ' = ?' for c in cols)} WHERE id = ?",
                   [data[c] for c in cols] + [part_id])
        db.commit()
        flash("Skladová karta bola uložená.", "ok")
        return redirect(url_for("main.part_detail", part_id=part_id))
    return render_template("part_form.html", part=part, projects=[])


@bp.route("/diely/<int:part_id>/zmazat", methods=["POST"])
@admin_required
def part_delete(part_id):
    db = get_db()
    part = db.query("SELECT * FROM parts WHERE id = ?", (part_id,), one=True) or abort(404)
    if db.scalar("SELECT COUNT(*) FROM receipts WHERE part_id = ?", (part_id,)):
        flash("Diel má záznamy o príjme – nedá sa zmazať. Najprv stornujte príjmy.", "error")
        return redirect(url_for("main.part_detail", part_id=part_id))
    db.execute("DELETE FROM project_parts WHERE part_id = ?", (part_id,))
    db.execute("DELETE FROM parts WHERE id = ?", (part_id,))
    db.commit()
    delete_image(part["image"])
    flash(f"Skladová karta {part['code']} bola zmazaná.", "ok")
    return redirect(url_for("main.parts"))


def _store_image(table, row_id):
    """Uloží obrázok nahratý ťahaním (drag & drop) alebo vložením zo schránky. Vracia JSON."""
    db = get_db()
    row = db.query(f"SELECT id, image FROM {table} WHERE id = ?", (row_id,), one=True)
    if not row:
        return jsonify(error="Záznam neexistuje."), 404
    f = request.files.get("image")
    if not f or not f.filename:
        return jsonify(error="Chýba súbor s obrázkom."), 400
    try:
        name = save_image(f)
    except Exception as exc:
        return jsonify(error=f"Obrázok sa nepodarilo uložiť: {exc}"), 400
    db.execute(f"UPDATE {table} SET image = ? WHERE id = ?", (name, row_id))
    db.commit()
    delete_image(row["image"])
    return jsonify(thumb=url_for("main.media", name="t_" + name), full=url_for("main.media", name=name))


@bp.route("/projekty/<int:pid>/obrazok", methods=["POST"])
@admin_required
def project_image(pid):
    return _store_image("projects", pid)


@bp.route("/diely/<int:part_id>/obrazok", methods=["POST"])
@login_required
def part_image(part_id):
    return _store_image("parts", part_id)


# =============================================================================
# Pripomienky (návrhy na vylepšenie, chyby, otázky)
# =============================================================================

FEEDBACK_KINDS = {"navrh": "Návrh na vylepšenie", "chyba": "Chyba", "otazka": "Otázka"}
FEEDBACK_STATUS = {"nova": "Nová", "riesi_sa": "Rieši sa", "hotovo": "Hotovo", "zamietnute": "Zamietnuté"}


@bp.route("/pripomienky", methods=["GET", "POST"])
@login_required
def feedback():
    db = get_db()
    if request.method == "POST":
        text = request.form.get("text", "").strip()
        kind = request.form.get("kind") if request.form.get("kind") in FEEDBACK_KINDS else "navrh"
        if not text:
            flash("Napíšte, čo by ste chceli zlepšiť alebo čo nefunguje.", "error")
            return redirect(url_for("main.feedback"))
        image = None
        img = request.files.get("image")
        if img and img.filename:
            try:
                image = save_image(img)
            except Exception as exc:
                flash(f"Obrázok sa nepodarilo uložiť: {exc}", "error")
        page = request.form.get("page", "").strip()[:255] or None
        db.execute("INSERT INTO feedback (user_id, kind, text, page, image) VALUES (?, ?, ?, ?, ?)",
                   (current_user()["id"], kind, text, page, image))
        db.commit()
        flash("Ďakujeme, pripomienka bola odoslaná.", "ok")
        return redirect(url_for("main.feedback"))
    status = request.args.get("stav", "otvorene")
    rows = db.query("""SELECT f.*, u.username, u.full_name FROM feedback f
                       LEFT JOIN users u ON u.id = f.user_id ORDER BY f.created_at DESC, f.id DESC""")
    counts = {k: sum(1 for r in rows if r["status"] == k) for k in FEEDBACK_STATUS}
    if status == "otvorene":
        rows = [r for r in rows if r["status"] in ("nova", "riesi_sa")]
    elif status in FEEDBACK_STATUS:
        rows = [r for r in rows if r["status"] == status]
    ref = request.args.get("z", "")
    return render_template("feedback.html", items=rows, kinds=FEEDBACK_KINDS, statuses=FEEDBACK_STATUS,
                           status=status, counts=counts, page=ref if ref.startswith("/") else "")


@bp.route("/pripomienky/<int:fid>", methods=["POST"])
@login_required
def feedback_update(fid):
    db = get_db()
    item = db.query("SELECT * FROM feedback WHERE id = ?", (fid,), one=True) or abort(404)
    me = current_user()
    if request.form.get("action") == "delete":
        if me["role"] != "admin" and item["user_id"] != me["id"]:
            abort(403)
        db.execute("DELETE FROM feedback WHERE id = ?", (fid,))
        db.commit()
        delete_image(item["image"])
        flash("Pripomienka bola zmazaná.", "ok")
        return redirect(url_for("main.feedback"))
    if me["role"] != "admin":
        abort(403)
    status = request.form.get("status")
    status = status if status in FEEDBACK_STATUS else item["status"]
    db.execute("UPDATE feedback SET status = ?, reply = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
               (status, request.form.get("reply", "").strip() or None, fid))
    db.commit()
    flash("Pripomienka bola uložená.", "ok")
    return redirect(request.form.get("back") or url_for("main.feedback"))


@bp.route("/media/<path:name>")
@login_required
def media(name):
    return send_from_directory(current_app.config["UPLOAD_FOLDER"], name, max_age=86400)


@bp.app_errorhandler(403)
def forbidden(_e):
    return render_template("error.html", code=403, msg="Na túto akciu nemáte oprávnenie."), 403


@bp.app_errorhandler(404)
def not_found(_e):
    return render_template("error.html", code=404, msg="Stránka neexistuje."), 404


@bp.app_errorhandler(400)
def bad_request(e):
    return render_template("error.html", code=400, msg=getattr(e, "description", "Chybná požiadavka.")), 400
