"""Hlavné stránky: dashboard, projekty, dokumenty, používatelia, profil."""
import os
import secrets
import threading
import uuid
from datetime import date, datetime

from flask import (Blueprint, abort, current_app, flash, g, jsonify, redirect,
                   render_template, request, send_from_directory, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from . import mailer
from . import sklad
from . import images
from . import settings as app_settings
from .auth import admin_required, login_required
from .db import execute, query
from .models import (STATUSES, TASK_STATUSES, build_timeline, get_project,
                     get_task, list_projects, list_tasks, parse_date,
                     project_stats, set_members, recalc_progress, tasks_progress,
                     build_calendar, INSTALL_STATUSES, set_installers,
                     list_installations, list_subtasks, TASK_WEIGHTS)

bp = Blueprint("main", __name__)


def run_in_background(fn, *args):
    """Mail sa posiela vo vlákne, aby používateľ nečakal na SMTP server."""
    app = current_app._get_current_object()

    def task():
        with app.app_context():
            fn(*args)

    if app.config.get("TESTING"):
        task()
    else:
        threading.Thread(target=task, daemon=True).start()


def log_event(project_id, message):
    execute("INSERT INTO project_log(project_id, user_id, message) VALUES (?, ?, ?)",
            (project_id, g.user["id"], message))


def can_access(project):
    return g.user["is_admin"] or g.user["id"] in project.member_ids


def project_or_404(project_id):
    project = get_project(project_id)
    if not project:
        abort(404)
    if not can_access(project):
        abort(403)
    return project


def active_users():
    return query("SELECT id, name, email FROM users WHERE active = 1 ORDER BY name")


# ------------------------------------------------------------------ dashboard

@bp.route("/")
@login_required
def dashboard():
    show_done = request.args.get("hotove") == "1"
    status = request.args.get("stav") or None
    if status not in STATUSES:
        status = None
    if g.user["is_admin"]:
        user_filter = request.args.get("pouzivatel", type=int)
    else:
        user_filter = g.user["id"]

    deadline_filter = request.args.get("termin")
    soon = current_app.config["DUE_SOON_DAYS"]

    all_projects = list_projects(assignee_id=user_filter)
    projects = [p for p in all_projects if (show_done or not p.is_done or status == "hotovy")
                and (status is None or p.status == status)]
    if deadline_filter == "po":
        projects = [p for p in projects if p.is_overdue]
    elif deadline_filter == "tyzden":
        projects = [p for p in projects if not p.is_done and 0 <= p.days_left <= soon]
    else:
        deadline_filter = None
    upcoming = [p for p in all_projects if not p.is_done][:8]
    smap = sklad.stats_map()
    for p in all_projects:
        p.sklad = smap.get(f"pm:{p.id}") if smap else None
    # obrázky projektov: neodoslané pošleme do skladu, zmenené v sklade stiahneme (na pozadí)
    todo = [(p.id, p.image, p.image_sync, (p.sklad or {}).get("image")) for p in all_projects if p.sklad
            and ((p.image and not p.image_sync) or ((p.sklad or {}).get("image") and p.sklad["image"] != p.image_sync))]
    if todo and not current_app.config.get("TESTING"):
        run_in_background(images.sync_all, todo, None)
    linked = [p.sklad for p in all_projects if not p.is_done and p.sklad and p.sklad.get("required")]
    material = None
    if linked:
        req = sum(s["required"] for s in linked)
        material = {"pct": round(sum(s["covered"] for s in linked) / req * 100) if req else 0,
                    "projects": len(linked), "missing": sum(1 for s in linked if s["missing"])}
    return render_template(
        "dashboard.html",
        my_tasks=list_tasks(assignee_id=g.user["id"], include_done=False),
        task_statuses=TASK_STATUSES,
        projects=projects,
        stats=project_stats(all_projects),
        timeline=build_timeline(projects, list_tasks() if projects else []),
        users=active_users() if g.user["is_admin"] else [],
        user_filter=user_filter,
        show_done=show_done,
        status_filter=status,
        deadline_filter=deadline_filter,
        upcoming=upcoming,
        material=material, sklad_on=sklad.enabled(), sklad_error=sklad.enabled() and smap is None,
        installations=list_installations(user_id=None if g.user["is_admin"] and not user_filter else user_filter,
                                         days_back=7),
    )


# ------------------------------------------------------------------- projekty

def _read_project_form():
    f = request.form
    data = {
        "name": f.get("name", "").strip(),
        "order_no": f.get("order_no", "").strip()[:100],
        "customer": f.get("customer", "").strip()[:200],
        "description": f.get("description", "").strip(),
        "status": f.get("status", "novy"),
        "progress": max(0, min(100, f.get("progress", 0, type=int) or 0)),
        "start_date": f.get("start_date") or date.today().isoformat(),
        "deadline": f.get("deadline", ""),
        "member_ids": sorted({int(x) for x in f.getlist("member_ids") if x.isdigit()}),
        "auto_progress": int("auto_progress" in f),
    }
    if data["status"] == "hotovy":
        data["progress"] = 100
    valid = {u["id"] for u in active_users()}
    data["member_ids"] = [uid for uid in data["member_ids"] if uid in valid]
    errors = []
    if not data["name"]:
        errors.append("Zadajte názov projektu.")
    if data["status"] not in STATUSES:
        errors.append("Neplatný stav.")
    try:
        start, end = parse_date(data["start_date"]), parse_date(data["deadline"])
        if not end:
            errors.append("Zadajte deadline.")
        elif end < start:
            errors.append("Deadline nemôže byť pred začiatkom projektu.")
    except ValueError:
        errors.append("Neplatný dátum.")
    return data, errors


@bp.route("/projekty/novy", methods=["GET", "POST"])
@admin_required
def project_new():
    data = {"start_date": date.today().isoformat(), "status": "novy", "progress": 0, "auto_progress": 1}
    if request.method == "POST":
        data, errors = _read_project_form()
        if not errors:
            cur = execute(
                "INSERT INTO projects(name, order_no, customer, description, status, progress, start_date, deadline, "
                "auto_progress, created_by_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (data["name"], data["order_no"], data["customer"], data["description"], data["status"],
                 data["progress"], data["start_date"], data["deadline"], data["auto_progress"], g.user["id"]),
            )
            pid = cur.lastrowid
            recalc_progress(pid)
            set_members(pid, data["member_ids"])
            log_event(pid, "Projekt založený.")
            project = get_project(pid)
            if project.members:
                run_in_background(mailer.notify_assigned, project, project.member_ids)
                log_event(pid, f"Riešitelia: {project.members_text}")
            if sklad.enabled():
                run_in_background(sklad.push, pid, g.user["name"])
            flash("Projekt bol vytvorený." + (" Riešiteľom odchádza e-mail." if project.members else "")
                  + (" Zakladá sa aj v Sklade dielov." if sklad.enabled() else ""), "success")
            return redirect(url_for("main.project_detail", project_id=pid))
        for e in errors:
            flash(e, "error")
    return render_template("project_form.html", data=data, users=active_users(),
                           statuses=STATUSES, project=None)


@bp.route("/projekty/<int:project_id>")
@login_required
def project_detail(project_id):
    project = project_or_404(project_id)
    folders = _folders(project_id)
    cur = request.args.get("priecinok", type=int)
    if cur not in folders:
        cur = None
    documents = query(
        "SELECT d.*, u.name AS uploader FROM documents d LEFT JOIN users u ON u.id = d.uploaded_by_id "
        "WHERE d.project_id = ? AND d.folder_id IS ? ORDER BY d.filename COLLATE NOCASE", (project_id, cur))
    counts = {r["folder_id"]: r["n"] for r in query(
        "SELECT folder_id, COUNT(*) AS n FROM documents WHERE project_id = ? GROUP BY folder_id", (project_id,))}
    subfolders = sorted((f for f in folders.values() if f["parent_id"] == cur), key=lambda f: f["name"].lower())
    for f in subfolders:
        inside = _folder_tree_ids(folders, f["id"])
        f["files"] = sum(counts.get(i, 0) for i in inside)
        f["dirs"] = len(inside) - 1
    doc_total = sum(counts.values())
    history = query(
        "SELECT l.*, u.name AS user_name FROM project_log l LEFT JOIN users u ON u.id = l.user_id "
        "WHERE l.project_id = ? ORDER BY l.id DESC LIMIT 50", (project_id,))
    comments = {}
    for c in query("SELECT k.*, u.name AS author FROM task_comments k JOIN tasks t ON t.id = k.task_id "
                   "LEFT JOIN users u ON u.id = k.user_id WHERE t.project_id = ? ORDER BY k.id", (project_id,)):
        comments.setdefault(c["task_id"], []).append(c)
    return render_template("project_detail.html", project=project, documents=documents,
                           folders=folders, cur_folder=folders.get(cur), subfolders=subfolders,
                           breadcrumb=_folder_path(folders, cur), doc_total=doc_total,
                           folder_choices=sorted(((fid, "/".join(x["name"] for x in _folder_path(folders, fid)))
                                                  for fid in folders), key=lambda t: t[1].lower()),
                           history=history, statuses=STATUSES,
                           tasks=list_tasks(project_id=project_id), task_statuses=TASK_STATUSES,
                           task_weights=TASK_WEIGHTS,
                           show_done_tasks=request.args.get("hotove_ulohy") == "1",
                           comments=comments, open_task=request.args.get("uloha", type=int),
                           install_statuses=INSTALL_STATUSES,
                           subtasks=list_subtasks(project_id=project_id),
                           edit_install=request.args.get("instalacia") == "upravit",
                           sklad_on=sklad.enabled(), sklad_info=_sklad_info_with_image_sync(project),
                           sklad_url=current_app.config.get("SKLAD_URL"))


def _sklad_info_with_image_sync(project):
    """Stav zo skladu pre detail projektu; ak sa obrázok zmenil v sklade (alebo ešte nebol odoslaný), zosúladí ho."""
    info = sklad.status(project)
    if info and not info.get("error"):
        si = info.get("image")
        if (project.image and not project.image_sync) or (si and si != project.image_sync):
            run_in_background(images.sync_all, [(project.id, project.image, project.image_sync, si)], None)
    return info


@bp.route("/projekty/<int:project_id>/obrazok", methods=["POST"])
@admin_required
def project_image(project_id):
    """Nahratie ilustračného obrázka (ťahaním, kliknutím alebo Ctrl+V). Vracia JSON."""
    project_or_404(project_id)
    f = request.files.get("image")
    if not f or not f.filename:
        return jsonify(error="Chýba súbor s obrázkom."), 400
    if os.path.splitext(f.filename)[1].lower() not in images.ALLOWED:
        return jsonify(error="Nepodporovaný formát obrázka (použite JPG, PNG, GIF alebo WEBP)."), 400
    try:
        name = images.set_project_image(project_id, f.stream)
    except Exception as exc:
        return jsonify(error=f"Obrázok sa nepodarilo uložiť: {exc}"), 400
    log_event(project_id, "Zmenený obrázok projektu.")
    if sklad.enabled():
        run_in_background(images.push_to_sklad, project_id)
    return jsonify(thumb=url_for("main.project_image_file", name="t_" + name),
                   full=url_for("main.project_image_file", name=name))


@bp.route("/obrazky/<name>")
@login_required
def project_image_file(name):
    return send_from_directory(current_app.config["IMAGE_FOLDER"], name, max_age=86400)


@bp.route("/projekty/<int:project_id>/sklad", methods=["POST"])
@admin_required
def project_sklad_push(project_id):
    """Ručné odoslanie projektu do skladu (napr. pre projekty založené pred prepojením)."""
    project_or_404(project_id)
    ok, msg = sklad.push(project_id, g.user["name"])
    flash(msg, "success" if ok else "error")
    return redirect(url_for("main.project_detail", project_id=project_id))


@bp.route("/projekty/<int:project_id>/upravit", methods=["GET", "POST"])
@admin_required
def project_edit(project_id):
    project = project_or_404(project_id)
    data = dict(project.__dict__)
    data["start_date"], data["deadline"] = project.start_date.isoformat(), project.deadline.isoformat()
    data["member_ids"] = project.member_ids
    if request.method == "POST":
        data, errors = _read_project_form()
        if not errors:
            execute(
                "UPDATE projects SET name=?, order_no=?, customer=?, description=?, status=?, progress=?, "
                "start_date=?, deadline=?, auto_progress=?, updated_at=datetime('now','localtime') WHERE id=?",
                (data["name"], data["order_no"], data["customer"], data["description"], data["status"], data["progress"],
                 data["start_date"], data["deadline"], data["auto_progress"], project_id),
            )
            recalc_progress(project_id)
            added, removed = set_members(project_id, data["member_ids"])
            updated = get_project(project_id)
            changes = []
            if updated.deadline != project.deadline:
                changes.append(f"deadline {project.deadline:%d.%m.%Y} → {updated.deadline:%d.%m.%Y}")
            if updated.status != project.status:
                changes.append(f"stav {project.status_label} → {updated.status_label}")
            names = {m["id"]: m["name"] for m in project.members + updated.members}
            if added:
                changes.append("pridaní riešitelia: " + ", ".join(sorted(names[i] for i in added)))
                run_in_background(mailer.notify_assigned, updated, list(added))
            if removed:
                changes.append("odobratí riešitelia: " + ", ".join(sorted(names[i] for i in removed)))
                # nedokončené úlohy odobratých riešiteľov ostanú nepridelené
                marks = ",".join("?" * len(removed))
                execute(f"UPDATE tasks SET assignee_id = NULL WHERE project_id = ? AND status <> 'hotova' "
                        f"AND assignee_id IN ({marks})", (project_id, *removed))
                execute(f"DELETE FROM project_installers WHERE project_id = ? AND user_id IN ({marks})",
                        (project_id, *removed))
            log_event(project_id, "Projekt upravený" + (": " + ", ".join(changes) if changes else "."))
            if sklad.enabled():
                run_in_background(sklad.push, project_id, g.user["name"])
            flash("Zmeny boli uložené.", "success")
            return redirect(url_for("main.project_detail", project_id=project_id))
        for e in errors:
            flash(e, "error")
    return render_template("project_form.html", data=data, users=active_users(),
                           statuses=STATUSES, project=project)


@bp.route("/projekty/<int:project_id>/stav", methods=["POST"])
@login_required
def project_status(project_id):
    """Rýchla aktualizácia stavu/percent – môže riešiteľ aj admin."""
    project = project_or_404(project_id)
    status = request.form.get("status", project.status)
    if status not in STATUSES:
        abort(400)
    progress = max(0, min(100, request.form.get("progress", project.progress, type=int)))
    if project.auto_progress:
        progress = tasks_progress(project_id)
    if status == "hotovy":
        progress = 100
    note = request.form.get("note", "").strip()

    execute("UPDATE projects SET status=?, progress=?, updated_at=datetime('now','localtime') WHERE id=?",
            (status, progress, project_id))
    msg = f"Stav: {STATUSES[status]}, {progress} %"
    if note:
        msg += f" – {note}"
    log_event(project_id, msg)

    updated = get_project(project_id)
    if (status, progress) != (project.status, project.progress) or note:
        run_in_background(mailer.notify_status_change, updated, dict(g.user), note)
    if status != project.status and sklad.enabled() and project.sklad_id:
        run_in_background(sklad.push, project_id, g.user["name"])
    flash("Stav projektu bol aktualizovaný.", "success")
    return redirect(url_for("main.project_detail", project_id=project_id))


@bp.route("/projekty/<int:project_id>/zmazat", methods=["POST"])
@admin_required
def project_delete(project_id):
    project = project_or_404(project_id)
    folder = os.path.join(current_app.config["UPLOAD_FOLDER"], str(project_id))
    for d in query("SELECT stored_name FROM documents WHERE project_id = ?", (project_id,)):
        try:
            os.remove(os.path.join(folder, d["stored_name"]))
        except OSError:
            pass
    try:
        os.rmdir(folder)
    except OSError:
        pass
    sklad_msg = sklad.remove(project_id) if project.sklad_id else None
    images.delete(project.image)
    execute("DELETE FROM projects WHERE id = ?", (project_id,))
    flash(f"Projekt „{project.name}“ bol zmazaný." + (f" {sklad_msg}" if sklad_msg else ""), "success")
    return redirect(url_for("main.dashboard"))


# ------------------------------------------------------------------ dokumenty

@bp.route("/projekty/<int:project_id>/dokumenty", methods=["POST"])
@login_required
def document_upload(project_id):
    project = project_or_404(project_id)
    files = [f for f in request.files.getlist("files") if f and f.filename]
    if not files:
        flash("Nevybrali ste žiadny súbor.", "error")
        return redirect(url_for("main.project_detail", project_id=project_id))

    folder = os.path.join(current_app.config["UPLOAD_FOLDER"], str(project.id))
    os.makedirs(folder, exist_ok=True)
    folder_id = request.form.get("folder_id", type=int)
    if folder_id not in _folders(project.id):
        folder_id = None
    names = []
    for f in files:
        original = os.path.basename(f.filename.replace("\\", "/"))[:200]
        ext = os.path.splitext(original)[1].lower()[:10]
        stored = uuid.uuid4().hex + "".join(c for c in ext if c.isalnum() or c == ".")
        path = os.path.join(folder, stored)
        f.save(path)
        execute("INSERT INTO documents(project_id, filename, stored_name, size, uploaded_by_id, folder_id) "
                "VALUES (?,?,?,?,?,?)", (project.id, original, stored, os.path.getsize(path), g.user["id"], folder_id))
        names.append(original)
    where = _folder_label(project.id, folder_id)
    log_event(project.id, "Nahrané dokumenty" + (f" do priečinka {where}" if folder_id else "") + ": " + ", ".join(names))
    flash(f"Nahraných súborov: {len(names)}.", "success")
    return _docs_redirect(project_id, folder_id)


def _document_or_404(doc_id):
    doc = query("SELECT * FROM documents WHERE id = ?", (doc_id,), one=True)
    if not doc:
        abort(404)
    project_or_404(doc["project_id"])
    return doc


@bp.route("/dokumenty/<int:doc_id>")
@login_required
def document_download(doc_id):
    doc = _document_or_404(doc_id)
    folder = os.path.join(current_app.config["UPLOAD_FOLDER"], str(doc["project_id"]))
    inline = request.args.get("zobrazit") == "1"
    return send_from_directory(folder, doc["stored_name"], download_name=doc["filename"],
                               as_attachment=not inline)


@bp.route("/dokumenty/<int:doc_id>/zmazat", methods=["POST"])
@login_required
def document_delete(doc_id):
    doc = _document_or_404(doc_id)
    if not (g.user["is_admin"] or doc["uploaded_by_id"] == g.user["id"]):
        abort(403)
    try:
        os.remove(os.path.join(current_app.config["UPLOAD_FOLDER"], str(doc["project_id"]), doc["stored_name"]))
    except OSError:
        pass
    execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    log_event(doc["project_id"], f"Zmazaný dokument: {doc['filename']}")
    flash("Dokument bol zmazaný.", "success")
    return _docs_redirect(doc["project_id"], doc["folder_id"])


# ------------------------------------------------------- priečinky dokumentov

def _folders(project_id):
    return {f["id"]: dict(f) for f in query("SELECT * FROM doc_folders WHERE project_id = ?", (project_id,))}


def _folder_path(folders, folder_id):
    """Cesta od hlavného priečinka po zadaný (pre navigáciu „Dokumenty / Výkresy / Elektro“)."""
    path, seen = [], set()
    while folder_id in folders and folder_id not in seen:
        seen.add(folder_id)
        path.append(folders[folder_id])
        folder_id = folders[folder_id]["parent_id"]
    return list(reversed(path))


def _folder_tree_ids(folders, folder_id):
    """Priečinok a všetky jeho podpriečinky."""
    out, todo = [], [folder_id]
    while todo:
        fid = todo.pop()
        out.append(fid)
        todo += [f["id"] for f in folders.values() if f["parent_id"] == fid]
    return out


def _folder_label(project_id, folder_id):
    return "/".join(f["name"] for f in _folder_path(_folders(project_id), folder_id)) or "Dokumenty"


def _docs_redirect(project_id, folder_id=None):
    return redirect(url_for("main.project_detail", project_id=project_id, priecinok=folder_id) + "#dokumenty")


def _clean_folder_name(name):
    name = " ".join((name or "").replace("/", "-").replace("\\", "-").split())[:80]
    return name.strip(". ")


@bp.route("/projekty/<int:project_id>/priecinky", methods=["POST"])
@login_required
def folder_create(project_id):
    project_or_404(project_id)
    folders = _folders(project_id)
    parent = request.form.get("parent_id", type=int)
    parent = parent if parent in folders else None
    name = _clean_folder_name(request.form.get("name"))
    if not name:
        flash("Zadajte názov priečinka.", "error")
    elif any(f["parent_id"] == parent and f["name"].lower() == name.lower() for f in folders.values()):
        flash(f"Priečinok „{name}“ tu už existuje.", "error")
    else:
        execute("INSERT INTO doc_folders(project_id, parent_id, name, created_by_id) VALUES (?,?,?,?)",
                (project_id, parent, name, g.user["id"]))
        log_event(project_id, f"Vytvorený priečinok dokumentov: {_folder_label(project_id, parent) + '/' if parent else ''}{name}")
        flash(f"Priečinok „{name}“ bol vytvorený.", "success")
    return _docs_redirect(project_id, parent)


def _folder_or_404(folder_id):
    f = query("SELECT * FROM doc_folders WHERE id = ?", (folder_id,), one=True) or abort(404)
    project_or_404(f["project_id"])
    return f


@bp.route("/priecinky/<int:folder_id>/premenovat", methods=["POST"])
@login_required
def folder_rename(folder_id):
    f = _folder_or_404(folder_id)
    if not (g.user["is_admin"] or f["created_by_id"] == g.user["id"]):
        abort(403)
    name = _clean_folder_name(request.form.get("name"))
    siblings = [x for x in _folders(f["project_id"]).values() if x["parent_id"] == f["parent_id"] and x["id"] != f["id"]]
    if not name:
        flash("Zadajte názov priečinka.", "error")
    elif any(x["name"].lower() == name.lower() for x in siblings):
        flash(f"Priečinok „{name}“ tu už existuje.", "error")
    else:
        execute("UPDATE doc_folders SET name = ? WHERE id = ?", (name, folder_id))
        log_event(f["project_id"], f"Priečinok „{f['name']}“ premenovaný na „{name}“.")
        flash("Priečinok bol premenovaný.", "success")
    return _docs_redirect(f["project_id"], f["parent_id"])


@bp.route("/priecinky/<int:folder_id>/zmazat", methods=["POST"])
@login_required
def folder_delete(folder_id):
    f = _folder_or_404(folder_id)
    folders = _folders(f["project_id"])
    ids = _folder_tree_ids(folders, folder_id)
    marks = ",".join("?" * len(ids))
    docs = query(f"SELECT * FROM documents WHERE folder_id IN ({marks})", ids)
    if docs and not g.user["is_admin"]:
        flash("Priečinok obsahuje súbory – zmazať ho môže len administrátor (alebo súbory najprv presuňte či zmažte).", "error")
        return _docs_redirect(f["project_id"], f["parent_id"])
    if not (g.user["is_admin"] or f["created_by_id"] == g.user["id"]):
        abort(403)
    base = os.path.join(current_app.config["UPLOAD_FOLDER"], str(f["project_id"]))
    for d in docs:
        try:
            os.remove(os.path.join(base, d["stored_name"]))
        except OSError:
            pass
    execute(f"DELETE FROM documents WHERE folder_id IN ({marks})", ids)
    execute(f"DELETE FROM doc_folders WHERE id IN ({marks})", ids)
    log_event(f["project_id"], f"Zmazaný priečinok „{f['name']}“" + (f" aj s {len(docs)} súbormi." if docs else "."))
    flash(f"Priečinok „{f['name']}“ bol zmazaný.", "success")
    return _docs_redirect(f["project_id"], f["parent_id"])


@bp.route("/dokumenty/<int:doc_id>/presunut", methods=["POST"])
@login_required
def document_move(doc_id):
    doc = _document_or_404(doc_id)
    if not (g.user["is_admin"] or doc["uploaded_by_id"] == g.user["id"]):
        abort(403)
    target = request.form.get("folder_id", type=int)
    if target not in _folders(doc["project_id"]):
        target = None
    if target != doc["folder_id"]:
        execute("UPDATE documents SET folder_id = ? WHERE id = ?", (target, doc_id))
        where = _folder_label(doc["project_id"], target)
        log_event(doc["project_id"], f"Dokument {doc['filename']} presunutý do: {where}")
        flash(f"Súbor bol presunutý do priečinka {where}.", "success")
    return _docs_redirect(doc["project_id"], doc["folder_id"])


# ------------------------------------------------------------ inštalácia

def _valid_time(value):
    value = (value or "").strip()
    if not value:
        return None
    try:
        h, m = (int(x) for x in value.split(":")[:2])
        if 0 <= h < 24 and 0 <= m < 60:
            return f"{h:02d}:{m:02d}"
    except ValueError:
        pass
    raise ValueError(value)


@bp.route("/projekty/<int:project_id>/instalacia", methods=["POST"])
@login_required
def project_install(project_id):
    """Naplánovanie / úprava inštalácie – admin alebo riešiteľ projektu."""
    project = project_or_404(project_id)
    f = request.form
    back = redirect(url_for("main.project_detail", project_id=project_id) + "#instalacia")
    try:
        install_date = parse_date(f.get("install_date"))
        t_from, t_to = _valid_time(f.get("time_from")), _valid_time(f.get("time_to"))
    except ValueError:
        flash("Neplatný dátum alebo čas inštalácie.", "error")
        return back
    if not install_date:
        flash("Zadajte dátum inštalácie.", "error")
        return back
    if t_from and t_to and t_to < t_from:
        flash("Čas „do“ nemôže byť skôr ako čas „od“.", "error")
        return back
    status = f.get("install_status", "planovana")
    if status not in INSTALL_STATUSES:
        status = "planovana"
    place = f.get("install_place", "").strip()[:300]
    note = f.get("install_note", "").strip()[:2000]
    installers = [int(x) for x in f.getlist("installer_ids") if x.isdigit() and int(x) in project.member_ids]

    moved = (install_date != project.install_date or t_from != project.install_time_from
             or t_to != project.install_time_to or place != (project.install_place or ""))
    execute(
        "UPDATE projects SET install_date=?, install_time_from=?, install_time_to=?, install_place=?, "
        "install_status=?, install_note=?, install_reminded = CASE WHEN install_date = ? THEN install_reminded END, "
        "updated_at=datetime('now','localtime') WHERE id=?",
        (install_date.isoformat(), t_from, t_to, place, status, note, install_date.isoformat(), project_id),
    )
    added = set_installers(project_id, installers)
    updated = get_project(project_id)

    when = f"{install_date:%d.%m.%Y}" + (f" {updated.install_time_text}" if updated.install_time_text else "")
    if not project.has_install:
        log_event(project_id, f"Naplánovaná inštalácia: {when}" + (f", {place}" if place else ""))
    elif moved:
        log_event(project_id, f"Inštalácia zmenená na {when}" + (f", {place}" if place else ""))
    if status != project.install_status and project.has_install:
        log_event(project_id, f"Inštalácia: {INSTALL_STATUSES[status]}" + (f" – {note}" if note else ""))

    # e-mail technikom: všetkým pri novom termíne/zmene, inak len novo pridaným
    notify = set(updated.installer_ids) if (moved or not project.has_install) else set(added)
    notify.discard(g.user["id"])
    if notify and status != "hotova":
        kind = "new" if not project.has_install else "changed"
        run_in_background(mailer.notify_installation, updated, notify, dict(g.user), kind)
    flash("Inštalácia bola uložená." + (" Technici dostanú e-mail." if notify and status != "hotova" else ""), "success")
    return back


@bp.route("/projekty/<int:project_id>/instalacia/zrusit", methods=["POST"])
@login_required
def project_install_clear(project_id):
    project = project_or_404(project_id)
    execute("UPDATE projects SET install_date=NULL, install_time_from=NULL, install_time_to=NULL, install_place='', "
            "install_status='planovana', install_note='', install_reminded=NULL WHERE id=?", (project_id,))
    execute("DELETE FROM project_installers WHERE project_id = ?", (project_id,))
    if project.has_install:
        log_event(project_id, f"Inštalácia {project.install_date:%d.%m.%Y} bola zrušená.")
    flash("Plánovaná inštalácia bola odstránená.", "success")
    return redirect(url_for("main.project_detail", project_id=project_id) + "#instalacia")


# ---------------------------------------------------------------------- úlohy

def task_or_404(task_id):
    task = get_task(task_id)
    if not task:
        abort(404)
    project = project_or_404(task.project_id)  # kontrola prístupu k projektu
    return task, project


def can_edit_task(task):
    return g.user["is_admin"] or task.created_by_id == g.user["id"]


def _read_task_form(project):
    f = request.form
    data = {
        "title": f.get("title", "").strip()[:300],
        "description": f.get("description", "").strip(),
        "assignee_id": f.get("assignee_id", type=int) or None,
        "due_date": f.get("due_date") or None,
        "weight": f.get("weight", 2, type=int) if f.get("weight", type=int) in TASK_WEIGHTS else 2,
    }
    errors = []
    if not data["title"]:
        errors.append("Zadajte názov úlohy.")
    if data["assignee_id"] and data["assignee_id"] not in project.member_ids:
        errors.append("Úlohu možno prideliť len riešiteľovi projektu.")
    if data["due_date"]:
        try:
            parse_date(data["due_date"])
        except ValueError:
            errors.append("Neplatný termín úlohy.")
    return data, errors


def _task_redirect(project_id):
    return redirect(url_for("main.project_detail", project_id=project_id) + "#ulohy")


@bp.route("/projekty/<int:project_id>/ulohy", methods=["POST"])
@login_required
def task_new(project_id):
    project = project_or_404(project_id)
    data, errors = _read_task_form(project)
    if errors:
        for e in errors:
            flash(e, "error")
        return _task_redirect(project_id)
    cur = execute(
        "INSERT INTO tasks(project_id, title, description, assignee_id, due_date, weight, created_by_id) "
        "VALUES (?,?,?,?,?,?,?)",
        (project_id, data["title"], data["description"], data["assignee_id"], data["due_date"], data["weight"],
         g.user["id"]),
    )
    task = get_task(cur.lastrowid)
    recalc_progress(project_id)
    log_event(project_id, f"Nová úloha: {task.title}" + (f" → {task.assignee_name}" if task.assignee_name else ""))
    if task.assignee_id and task.assignee_id != g.user["id"]:
        run_in_background(mailer.notify_task_assigned, task)
    flash("Úloha bola pridaná." + (f" {task.assignee_name} dostane e-mail." if task.assignee_id and task.assignee_id != g.user["id"] else ""), "success")
    return _task_redirect(project_id)


@bp.route("/ulohy/<int:task_id>/stav", methods=["POST"])
@login_required
def task_status(task_id):
    task, project = task_or_404(task_id)
    status = request.form.get("status")
    if status not in TASK_STATUSES:
        abort(400)
    if status != task.status:
        execute("UPDATE tasks SET status = ?, completed_at = CASE WHEN ? = 'hotova' "
                "THEN datetime('now','localtime') ELSE NULL END WHERE id = ?", (status, status, task_id))
        if status == "hotova":  # hotová úloha = hotové aj všetky jej podúlohy
            execute("UPDATE subtasks SET done = 1, done_at = datetime('now','localtime'), done_by_id = ? "
                    "WHERE task_id = ? AND done = 0", (g.user["id"], task_id))
        log_event(project.id, f"Úloha „{task.title}“: {TASK_STATUSES[status]}")
        if status == "hotova":
            run_in_background(mailer.notify_task_done, get_task(task_id), dict(g.user))
        recalc_progress(project.id)
    nxt = request.form.get("next", "")
    if nxt.startswith("/") and not nxt.startswith("//"):
        return redirect(nxt)
    return _task_redirect(project.id)


# ------------------------------------------------------------------ podúlohy

def _sync_task_with_subtasks(task_id, last_subtask=None):
    """Zosúladí stav úlohy s podúlohami: všetky hotové → úloha hotová; niečo nehotové → úloha sa rieši.
    last_subtask = názov práve splnenej podúlohy (uvedie sa v e-maile o hotovej úlohe)."""
    task = get_task(task_id)
    if not task.sub_total:
        return task
    if task.sub_done == task.sub_total and task.status != "hotova":
        execute("UPDATE tasks SET status = 'hotova', completed_at = datetime('now','localtime') WHERE id = ?", (task_id,))
        log_event(task.project_id, f"Úloha „{task.title}“: Hotová (všetky podúlohy splnené)")
        run_in_background(mailer.notify_task_done, get_task(task_id), dict(g.user), last_subtask)
    elif task.sub_done < task.sub_total and task.status == "hotova":
        execute("UPDATE tasks SET status = 'prebieha', completed_at = NULL WHERE id = ?", (task_id,))
        log_event(task.project_id, f"Úloha „{task.title}“ znovu otvorená (pribudla alebo sa odškrtla podúloha)")
    elif task.sub_done and task.status == "otvorena":
        execute("UPDATE tasks SET status = 'prebieha' WHERE id = ?", (task_id,))
    recalc_progress(task.project_id)
    return get_task(task_id)


def _subtask_redirect(task):
    args = {"project_id": task.project_id}
    if task.status == "hotova":
        args["hotove_ulohy"] = 1
    return redirect(url_for("main.project_detail", **args) + f"#uloha-{task.id}")


@bp.route("/ulohy/<int:task_id>/podulohy", methods=["POST"])
@login_required
def subtask_new(task_id):
    task, _project = task_or_404(task_id)
    # každý riadok = jedna podúloha (dá sa vložiť celý zoznam naraz)
    titles = [t.strip()[:300] for t in request.form.get("title", "").splitlines() if t.strip()][:50]
    if not titles:
        flash("Zadajte názov podúlohy.", "error")
        return _subtask_redirect(task)
    for t in titles:
        execute("INSERT INTO subtasks(task_id, title, created_by_id) VALUES (?, ?, ?)", (task_id, t, g.user["id"]))
    task = _sync_task_with_subtasks(task_id)
    return _subtask_redirect(task)


def _subtask_or_404(subtask_id):
    s = query("SELECT * FROM subtasks WHERE id = ?", (subtask_id,), one=True)
    if not s:
        abort(404)
    task, project = task_or_404(s["task_id"])
    return s, task, project


@bp.route("/podulohy/<int:subtask_id>/prepnut", methods=["POST"])
@login_required
def subtask_toggle(subtask_id):
    s, task, _project = _subtask_or_404(subtask_id)
    if s["done"]:
        execute("UPDATE subtasks SET done = 0, done_at = NULL, done_by_id = NULL WHERE id = ?", (subtask_id,))
        task = _sync_task_with_subtasks(task.id)
    else:
        execute("UPDATE subtasks SET done = 1, done_at = datetime('now','localtime'), done_by_id = ? WHERE id = ?",
                (g.user["id"], subtask_id))
        was_done = task.status == "hotova"
        task = _sync_task_with_subtasks(task.id, last_subtask=s["title"])
        if task.status != "hotova" or was_done:  # pri poslednej podúlohe ide jeden e-mail „úloha splnená“
            run_in_background(mailer.notify_subtask_done, task, s["title"], dict(g.user))
    nxt = request.form.get("next", "")
    if nxt.startswith("/") and not nxt.startswith("//"):
        return redirect(nxt)
    return _subtask_redirect(task)


@bp.route("/podulohy/<int:subtask_id>/zmazat", methods=["POST"])
@login_required
def subtask_delete(subtask_id):
    s, task, _project = _subtask_or_404(subtask_id)
    if not (g.user["is_admin"] or task.created_by_id == g.user["id"] or s["created_by_id"] == g.user["id"]):
        abort(403)
    execute("DELETE FROM subtasks WHERE id = ?", (subtask_id,))
    task = _sync_task_with_subtasks(task.id)
    recalc_progress(task.project_id)
    return _subtask_redirect(task)


@bp.route("/projekty/<int:project_id>/plnenie-z-uloh", methods=["POST"])
@login_required
def project_enable_auto(project_id):
    project = project_or_404(project_id)
    execute("UPDATE projects SET auto_progress = 1 WHERE id = ?", (project_id,))
    recalc_progress(project_id)
    log_event(project_id, "Plnenie sa odteraz počíta automaticky z úloh a podúloh.")
    flash("Plnenie projektu sa odteraz počíta z úloh a podúloh.", "success")
    return redirect(url_for("main.project_detail", project_id=project.id))


@bp.route("/ulohy/<int:task_id>/vaha", methods=["POST"])
@login_required
def task_weight(task_id):
    """Rýchla zmena dôležitosti (váhy) úlohy – admin alebo zadávateľ úlohy."""
    task, project = task_or_404(task_id)
    if not can_edit_task(task):
        abort(403)
    weight = request.form.get("weight", type=int)
    if weight not in TASK_WEIGHTS:
        abort(400)
    if weight != task.weight:
        execute("UPDATE tasks SET weight = ? WHERE id = ?", (weight, task_id))
        log_event(project.id, f"Úloha „{task.title}“: dôležitosť {TASK_WEIGHTS.get(task.weight, task.weight)} → {TASK_WEIGHTS[weight]}")
        recalc_progress(project.id)
    return _subtask_redirect(get_task(task_id))


@bp.route("/ulohy/<int:task_id>/upravit", methods=["GET", "POST"])
@login_required
def task_edit(task_id):
    task, project = task_or_404(task_id)
    if not can_edit_task(task):
        abort(403)
    data = {"title": task.title, "description": task.description, "assignee_id": task.assignee_id,
            "due_date": task.due_date.isoformat() if task.due_date else "", "status": task.status,
            "weight": task.weight}
    if request.method == "POST":
        data, errors = _read_task_form(project)
        data["status"] = request.form.get("status", task.status)
        if data["status"] not in TASK_STATUSES:
            errors.append("Neplatný stav.")
        if not errors:
            execute(
                "UPDATE tasks SET title=?, description=?, assignee_id=?, due_date=?, status=?, weight=?, "
                "completed_at = CASE WHEN ? = 'hotova' THEN COALESCE(completed_at, datetime('now','localtime')) "
                "ELSE NULL END WHERE id=?",
                (data["title"], data["description"], data["assignee_id"], data["due_date"],
                 data["status"], data["weight"], data["status"], task_id),
            )
            updated = get_task(task_id)
            log_event(project.id, f"Úloha „{updated.title}“ upravená")
            if updated.assignee_id and updated.assignee_id != task.assignee_id and updated.assignee_id != g.user["id"]:
                run_in_background(mailer.notify_task_assigned, updated)
            recalc_progress(project.id)
            flash("Úloha bola uložená.", "success")
            return _task_redirect(project.id)
        for e in errors:
            flash(e, "error")
    return render_template("task_form.html", task=task, project=project, data=data,
                           task_statuses=TASK_STATUSES, task_weights=TASK_WEIGHTS)


@bp.route("/ulohy/<int:task_id>/zmazat", methods=["POST"])
@login_required
def task_delete(task_id):
    task, project = task_or_404(task_id)
    if not can_edit_task(task):
        abort(403)
    execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    log_event(project.id, f"Zmazaná úloha: {task.title}")
    recalc_progress(project.id)
    flash("Úloha bola zmazaná.", "success")
    return _task_redirect(project.id)


@bp.route("/ulohy/<int:task_id>/komentare", methods=["POST"])
@login_required
def task_comment(task_id):
    task, project = task_or_404(task_id)
    body = request.form.get("body", "").strip()[:4000]
    if body:
        execute("INSERT INTO task_comments(task_id, user_id, body) VALUES (?, ?, ?)", (task_id, g.user["id"], body))
        run_in_background(mailer.notify_task_comment, task, dict(g.user), body)
    else:
        flash("Komentár je prázdny.", "error")
    return _comment_redirect(task)


@bp.route("/komentare/<int:comment_id>/zmazat", methods=["POST"])
@login_required
def comment_delete(comment_id):
    c = query("SELECT * FROM task_comments WHERE id = ?", (comment_id,), one=True)
    if not c:
        abort(404)
    task, _project = task_or_404(c["task_id"])
    if not (g.user["is_admin"] or c["user_id"] == g.user["id"]):
        abort(403)
    execute("DELETE FROM task_comments WHERE id = ?", (comment_id,))
    return _comment_redirect(task)


def _comment_redirect(task):
    args = {"project_id": task.project_id, "uloha": task.id}
    if task.is_done:
        args["hotove_ulohy"] = 1
    return redirect(url_for("main.project_detail", **args) + f"#uloha-{task.id}")


# -------------------------------------------------------------------- kalendár

@bp.route("/kalendar")
@login_required
def calendar_view():
    today = date.today()
    try:
        year, month = (int(x) for x in request.args.get("mesiac", "").split("-"))
        date(year, month, 1)
    except ValueError:
        year, month = today.year, today.month
    if g.user["is_admin"]:
        user_filter = request.args.get("pouzivatel", type=int)
    else:
        user_filter = g.user["id"]
    only_mine = request.args.get("moje") == "1"

    projects = list_projects(assignee_id=user_filter)
    ids = {p.id for p in projects}
    tasks = [t for t in list_tasks() if t.project_id in ids and t.due_date]
    if only_mine:
        tasks = [t for t in tasks if t.assignee_id == g.user["id"]]
        projects = [p for p in projects if g.user["id"] in p.member_ids]

    prev_m = date(year - (month == 1), 12 if month == 1 else month - 1, 1)
    next_m = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    return render_template(
        "calendar.html",
        cal=build_calendar(year, month, projects, tasks),
        prev_month=f"{prev_m:%Y-%m}", next_month=f"{next_m:%Y-%m}",
        is_current=(year, month) == (today.year, today.month),
        users=active_users() if g.user["is_admin"] else [],
        user_filter=user_filter, only_mine=only_mine,
    )


# --------------------------------------------------------------- používatelia

@bp.route("/pouzivatelia")
@admin_required
def users():
    rows = query(
        "SELECT u.*, (SELECT COUNT(*) FROM project_members m JOIN projects p ON p.id = m.project_id "
        "WHERE m.user_id = u.id AND p.status <> 'hotovy') "
        "AS open_projects FROM users u ORDER BY u.active DESC, u.name")
    return render_template("users.html", users=rows)


@bp.route("/pouzivatelia/novy", methods=["GET", "POST"])
@bp.route("/pouzivatelia/<int:user_id>", methods=["GET", "POST"])
@admin_required
def user_edit(user_id=None):
    user = query("SELECT * FROM users WHERE id = ?", (user_id,), one=True) if user_id else None
    if user_id and not user:
        abort(404)
    if request.method == "POST":
        f = request.form
        name, email, password = f.get("name", "").strip(), f.get("email", "").strip().lower(), f.get("password", "")
        is_admin, active, daily = int("is_admin" in f), int("active" in f), int("daily_report" in f)
        if user and user["id"] == g.user["id"]:
            is_admin, active = 1, 1  # admin si nemôže sám odobrať práva
        errors = []
        if not name or "@" not in email:
            errors.append("Vyplňte meno a platný e-mail.")
        if not user and len(password) < 8:
            errors.append("Heslo musí mať aspoň 8 znakov.")
        if password and len(password) < 8:
            errors.append("Heslo musí mať aspoň 8 znakov.")
        dup = query("SELECT id FROM users WHERE email = ? AND id <> ?", (email, user_id or 0), one=True)
        if dup:
            errors.append("Používateľ s týmto e-mailom už existuje.")
        if user and user["is_admin"] and user["active"] and (not active or not is_admin) and _admins_left(user_id) == 0:
            errors.append("Toto je posledný aktívny administrátor – nedá sa deaktivovať ani mu odobrať práva.")
        if not errors:
            if user:
                execute("UPDATE users SET name=?, email=?, is_admin=?, active=?, daily_report=? WHERE id=?",
                        (name, email, is_admin, active, daily, user_id))
                if password:
                    execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(password), user_id))
            else:
                execute("INSERT INTO users(name, email, password_hash, is_admin, active, daily_report) "
                        "VALUES (?,?,?,?,?,?)", (name, email, generate_password_hash(password), is_admin, active, daily))
            flash("Používateľ bol uložený.", "success")
            return redirect(url_for("main.users"))
        for e in errors:
            flash(e, "error")
        user = {**(dict(user) if user else {}), "name": name, "email": email, "is_admin": is_admin,
                "active": active, "daily_report": daily, "id": user_id}
    impact = _user_impact(user_id) if user_id else None
    return render_template("user_form.html", user=user, impact=impact)


def _admins_left(except_id):
    return query("SELECT COUNT(*) FROM users WHERE is_admin = 1 AND active = 1 AND id <> ?", (except_id,), one=True)[0]


@bp.route("/pouzivatelia/<int:user_id>/aktivny", methods=["POST"])
@admin_required
def user_toggle(user_id):
    """Rýchle zablokovanie / povolenie účtu priamo zo zoznamu používateľov."""
    user = query("SELECT * FROM users WHERE id = ?", (user_id,), one=True) or abort(404)
    if user_id == g.user["id"]:
        flash("Vlastný účet nemôžete deaktivovať.", "error")
    elif user["active"] and user["is_admin"] and _admins_left(user_id) == 0:
        flash("Toto je posledný aktívny administrátor – nedá sa deaktivovať.", "error")
    else:
        execute("UPDATE users SET active = ? WHERE id = ?", (0 if user["active"] else 1, user_id))
        flash(f"Účet {user['name']} bol {'deaktivovaný' if user['active'] else 'znova aktivovaný'}.", "success")
    return redirect(url_for("main.users"))


def _user_impact(user_id):
    """Čoho sa týka zmazanie používateľa – zobrazí sa pred potvrdením."""
    one = lambda sql: query(sql, (user_id,), one=True)[0]
    return {
        "projects": one("SELECT COUNT(*) FROM project_members m JOIN projects p ON p.id = m.project_id "
                        "WHERE m.user_id = ? AND p.status <> 'hotovy'"),
        "tasks": one("SELECT COUNT(*) FROM tasks WHERE assignee_id = ? AND status <> 'hotova'"),
    }


@bp.route("/pouzivatelia/<int:user_id>/zmazat", methods=["POST"])
@admin_required
def user_delete(user_id):
    user = query("SELECT * FROM users WHERE id = ?", (user_id,), one=True)
    if not user:
        abort(404)
    if user["id"] == g.user["id"]:
        flash("Nemôžete zmazať sám seba.", "error")
        return redirect(url_for("main.user_edit", user_id=user_id))
    if user["is_admin"] and user["active"] and query("SELECT COUNT(*) FROM users WHERE is_admin = 1 AND active = 1", one=True)[0] <= 1:
        flash("Toto je posledný aktívny administrátor – nedá sa zmazať.", "error")
        return redirect(url_for("main.user_edit", user_id=user_id))

    # záznam do histórie projektov, na ktorých pracoval
    for row in query("SELECT project_id FROM project_members WHERE user_id = ?", (user_id,)):
        log_event(row["project_id"], f"Používateľ {user['name']} bol odstránený – jeho otvorené úlohy sú nepridelené.")
    # zmazanie: členstvá v projektoch sa odstránia, úlohy/dokumenty/história ostanú bez priradenej osoby
    # (odkazy upravíme aj ručne – staršie databázy nemusia mať pravidlá ON DELETE)
    for sql in ("DELETE FROM project_members WHERE user_id = ?", "DELETE FROM project_installers WHERE user_id = ?",
                "UPDATE projects SET created_by_id = NULL WHERE created_by_id = ?",
                "UPDATE tasks SET assignee_id = NULL WHERE assignee_id = ?",
                "UPDATE tasks SET created_by_id = NULL WHERE created_by_id = ?",
                "UPDATE subtasks SET done_by_id = NULL WHERE done_by_id = ?",
                "UPDATE subtasks SET created_by_id = NULL WHERE created_by_id = ?",
                "UPDATE task_comments SET user_id = NULL WHERE user_id = ?",
                "UPDATE documents SET uploaded_by_id = NULL WHERE uploaded_by_id = ?",
                "UPDATE project_log SET user_id = NULL WHERE user_id = ?"):
        execute(sql, (user_id,))
    execute("DELETE FROM users WHERE id = ?", (user_id,))
    flash(f"Používateľ {user['name']} bol zmazaný.", "success")
    return redirect(url_for("main.users"))


# ------------------------------------------------------------------ nastavenia

def _read_settings_form(f, cfg):
    """Vráti (hodnoty, chyby) z formulára nastavení."""
    v, errors = {}, []
    v["APP_NAME"] = f.get("APP_NAME", "").strip() or "Prehľad projektov"
    url = f.get("APP_URL", "").strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        errors.append("Verejná adresa musí začínať http:// alebo https://")
    v["APP_URL"] = url
    days = f.get("DUE_SOON_DAYS", type=int)
    if days is None or not 1 <= days <= 90:
        errors.append("Upozornenie na deadline: zadajte počet dní 1 – 90.")
    v["DUE_SOON_DAYS"] = days or 7

    v["MAIL_ENABLED"] = "MAIL_ENABLED" in f
    v["MAIL_SERVER"] = f.get("MAIL_SERVER", "").strip()
    port = f.get("MAIL_PORT", type=int)
    if port is None or not 1 <= port <= 65535:
        errors.append("Port musí byť číslo 1 – 65535.")
    v["MAIL_PORT"] = port or 587
    security = f.get("security", "starttls")
    v["MAIL_USE_TLS"] = security == "starttls"
    v["MAIL_USE_SSL"] = security == "ssl"
    v["MAIL_USERNAME"] = f.get("MAIL_USERNAME", "").strip()
    pwd = f.get("MAIL_PASSWORD", "")
    if "clear_password" in f:
        v["MAIL_PASSWORD"] = ""
    elif pwd:
        v["MAIL_PASSWORD"] = pwd
    # prázdne pole = heslo sa nemení
    v["MAIL_SENDER"] = f.get("MAIL_SENDER", "").strip() or v["MAIL_USERNAME"]
    if v["MAIL_SENDER"] and not app_settings.EMAIL_RE.match(v["MAIL_SENDER"]):
        errors.append("Odosielateľ musí byť platná e-mailová adresa.")
    if v["MAIL_ENABLED"] and not v["MAIL_SERVER"]:
        errors.append("Na odosielanie e-mailov treba vyplniť SMTP server.")
    if v["MAIL_ENABLED"] and not v["MAIL_SENDER"]:
        errors.append("Na odosielanie e-mailov treba vyplniť odosielateľa.")

    t = f.get("DAILY_REPORT_TIME", "").strip()
    if not app_settings.TIME_RE.match(t):
        errors.append("Čas denného reportu zadajte vo formáte HH:MM (napr. 07:30).")
    v["DAILY_REPORT_TIME"] = t
    v["DAILY_REPORT_SKIP_WEEKEND"] = "DAILY_REPORT_SKIP_WEEKEND" in f
    return v, errors


@bp.route("/admin/nastavenia", methods=["GET", "POST"])
@admin_required
def settings_page():
    app = current_app._get_current_object()
    if request.method == "POST":
        action = request.form.get("action", "save")
        if action == "reset":
            app_settings.save(app, {}, reset=list(app_settings.FIELDS))
            flash("Nastavenia boli vrátené na hodnoty zo súboru .env.", "success")
            return redirect(url_for("main.settings_page"))
        if action == "test":
            to = request.form.get("test_to", "").strip()
            if not app_settings.EMAIL_RE.match(to):
                flash("Zadajte platnú adresu pre testovací e-mail.", "error")
            else:
                execute("INSERT INTO settings(key, value) VALUES('test_mail_to', ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (to,))
                ok, msg = mailer.send_test_mail(to)
                flash(msg, "success" if ok else "error")
            return redirect(url_for("main.settings_page") + "#email")
        if action in ("sklad", "sklad_test", "sklad_all"):
            return _settings_sklad(app, action)
        values, errors = _read_settings_form(request.form, app.config)
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("settings.html", form=request.form, **_settings_ctx(app))
        changed = {k: val for k, val in values.items() if app.config.get(k) != val
                   or k in app_settings.overridden(app)}
        app_settings.save(app, changed)
        flash("Nastavenia boli uložené.", "success")
        return redirect(url_for("main.settings_page"))
    return render_template("settings.html", form=None, **_settings_ctx(app))


def _settings_sklad(app, action):
    if action == "sklad":
        url = request.form.get("SKLAD_URL", "").strip().rstrip("/")
        if url and not url.startswith(("http://", "https://")):
            flash("Adresa skladu musí začínať http:// alebo https://", "error")
            return redirect(url_for("main.settings_page") + "#sklad")
        values = {"SKLAD_URL": url}
        key = request.form.get("SKLAD_API_KEY", "").strip()
        if "clear_sklad_key" in request.form or not url:
            values["SKLAD_API_KEY"] = ""
        elif key:
            values["SKLAD_API_KEY"] = key           # prázdne pole = kľúč sa nemení
        app_settings.save(app, values)
        if sklad.enabled():
            ok, msg = sklad.ping()
            flash("Prepojenie so skladom uložené. " + msg, "success" if ok else "error")
        else:
            flash("Prepojenie so skladom je vypnuté." if not url else "Uložené – ešte doplňte API kľúč.", "success")
    elif action == "sklad_test":
        ok, msg = sklad.ping()
        flash(msg, "success" if ok else "error")
    else:
        done, failed = 0, []
        for p in list_projects():
            if p.is_done and not p.sklad_id:
                continue                            # hotové projekty, ktoré v sklade nie sú, nezakladáme
            ok, msg = sklad.push(p.id, g.user["name"])
            if ok:
                done += 1
            else:
                failed.append(f"{p.name}: {msg}")
                if "nie je dostupný" in msg or "API kľúč" in msg:
                    break
        flash(f"Do skladu odoslaných projektov: {done}." + (" Chyby: " + "; ".join(failed[:3]) if failed else ""),
              "error" if failed else "success")
    return redirect(url_for("main.settings_page") + "#sklad")


def _server_info(app):
    """Úložisko servera pre Nastavenia: disk + koľko zaberajú dáta Projektov."""
    from .disk import folder_size, usage

    cfg = app.config
    try:
        db = os.path.getsize(cfg["DATABASE"])
    except OSError:
        db = None
    return {"disk": usage(cfg["DATA_DIR"], max_age=0), "db": db,
            "docs": folder_size(cfg["UPLOAD_FOLDER"]), "images": folder_size(cfg["IMAGE_FOLDER"])}


def _settings_ctx(app):
    cfg = app.config
    env = app.extensions["env_settings"]
    return {
        "cfg": cfg, "env": env, "overridden": app_settings.overridden(app),
        "security": "ssl" if cfg["MAIL_USE_SSL"] else "starttls" if cfg["MAIL_USE_TLS"] else "none",
        "has_password": bool(cfg["MAIL_PASSWORD"]),
        "has_sklad_key": bool(cfg.get("SKLAD_API_KEY")),
        "server": _server_info(app),
        "sklad_on": sklad.enabled(cfg),
        "board_on": bool(_board_token()),
        "test_to": (query("SELECT value FROM settings WHERE key = 'test_mail_to'", one=True) or {"value": g.user["email"]})["value"],
        "user_emails": [u["email"] for u in active_users()],
    }


# ------------------------------------------------- tabuľa (prehľad bez prihlásenia)

def _board_token():
    row = query("SELECT value FROM settings WHERE key = 'board_token'", one=True)
    return row["value"] if row and row["value"] else None


@bp.route("/tabula/<token>")
def board(token):
    """Verejný prehľad len na čítanie – prístup cez tajný odkaz, ktorý spravuje admin."""
    current = _board_token()
    if not current or not secrets.compare_digest(token, current):
        abort(404)
    tv = request.args.get("tv") == "1"
    all_projects = list_projects()
    projects = [p for p in all_projects if not p.is_done]
    installs = list_installations(days_back=0, days_ahead=60)
    resp = current_app.make_response(render_template(
        "board.html", tv=tv, token=token,
        projects=projects,
        stats=project_stats(all_projects),
        installs_week=sum(1 for p in installs if p.install_days_left <= 7),
        timeline=build_timeline(projects, [t for t in list_tasks(include_done=True)] if projects else []),
        installations=installs,
        now=datetime.now(),
    ))
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    resp.headers["Referrer-Policy"] = "same-origin"
    return resp


@bp.route("/admin/tabula", methods=["GET", "POST"])
@admin_required
def board_admin():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "new":
            execute("INSERT INTO settings(key, value) VALUES('board_token', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (secrets.token_urlsafe(18),))
            flash("Vytvoril sa nový odkaz. Starý odkaz (ak existoval) už nefunguje.", "success")
        elif action == "off":
            execute("DELETE FROM settings WHERE key = 'board_token'")
            flash("Prehľad bez prihlásenia je vypnutý.", "success")
        return redirect(url_for("main.board_admin"))
    token = _board_token()
    link = url_for("main.board", token=token, _external=True) if token else None
    return render_template("board_admin.html", link=link)


# veľkosť celého rozhrania v % – tlačidlá A− / A+ menia po 5 % (70 % … 150 %)
FONT_MIN, FONT_MAX, FONT_STEP = 70, 150, 5
FONT_SCALES = {v: ("Normálna (100 %)" if v == 100 else f"{v} %") for v in range(FONT_MIN, FONT_MAX + 1, FONT_STEP)}


@bp.route("/velkost-textu", methods=["POST"])
def set_font_scale():
    """Prepínač veľkosti textu – uloží sa k používateľovi aj do cookie (platí aj na prihlasovacej stránke)."""
    step = request.form.get("step", type=int)
    if step is not None:                      # A− / A+ / kliknutie na hodnotu (návrat na 100 %)
        current = (g.user["font_scale"] if g.user else request.cookies.get("font_scale", type=int)) or 100
        scale = 100 if step == 0 else current + step * FONT_STEP
        scale = max(FONT_MIN, min(FONT_MAX, scale))
    else:
        scale = request.form.get("scale", type=int)
    if scale not in FONT_SCALES:
        scale = 100
    if g.user:
        execute("UPDATE users SET font_scale=? WHERE id=?", (scale, g.user["id"]))
    back = request.referrer or ""
    if not back.startswith(request.host_url):
        back = url_for("main.dashboard")
    resp = redirect(back)
    resp.set_cookie("font_scale", str(scale), max_age=3600 * 24 * 365, samesite="Lax")
    return resp


@bp.route("/profil", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        f = request.form
        scale = f.get("font_scale", type=int)
        execute("UPDATE users SET daily_report=?, font_scale=? WHERE id=?",
                (int("daily_report" in f), scale if scale in FONT_SCALES else 100, g.user["id"]))
        new = f.get("new_password", "")
        if new:
            if not check_password_hash(g.user["password_hash"], f.get("old_password", "")):
                flash("Pôvodné heslo nie je správne.", "error")
                return redirect(url_for("main.profile"))
            if len(new) < 8:
                flash("Nové heslo musí mať aspoň 8 znakov.", "error")
                return redirect(url_for("main.profile"))
            execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(new), g.user["id"]))
        flash("Nastavenia boli uložené.", "success")
        return redirect(url_for("main.profile"))
    return render_template("profile.html")


@bp.route("/admin/denny-report", methods=["POST"])
@admin_required
def send_report_now():
    if not current_app.config["MAIL_ENABLED"]:
        flash("Odosielanie e-mailov je vypnuté – zapnite ho v Nastaveniach.", "error")
    else:
        sent = mailer.send_daily_reports()
        flash(f"Denný report odoslaný ({sent} e-mailov).", "success")
    return redirect(request.referrer or url_for("main.dashboard"))
