"""Pripomienky – poznámky pre seba alebo pre ostatných (aj k projektu, s fotkou).

Pripomenutie: v zadaný čas (alebo hneď pri najbližšom otvorení aplikácie) sa zobrazí okno
všetkým adresátom a ak je zapnuté, odíde im aj e-mail (posiela plánovač raz za minútu).
"""
import logging
from datetime import datetime, timedelta

from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template, request,
                   send_from_directory, url_for)

from . import images
from .auth import login_required
from .db import connect, execute, query

log = logging.getLogger(__name__)
bp = Blueprint("rem", __name__)

VISIBILITY = {"ja": "Len ja", "projekt": "Členovia projektu", "vsetci": "Všetci"}
WHEN = {"nie": "Nepripomínať", "hned": "Pri najbližšom otvorení aplikácie", "cas": "V určitý čas"}

SELECT = """
SELECT r.*, a.name AS author_name, p.name AS project_name, d.name AS done_by_name,
       (SELECT GROUP_CONCAT(ru.user_id) FROM reminder_users ru WHERE ru.reminder_id = r.id) AS recipients_raw,
       (SELECT GROUP_CONCAT(u.name, ', ') FROM reminder_users ru JOIN users u ON u.id = ru.user_id
         WHERE ru.reminder_id = r.id AND ru.user_id <> COALESCE(r.author_id, 0)) AS others
FROM reminders r
LEFT JOIN users a ON a.id = r.author_id
LEFT JOIN projects p ON p.id = r.project_id
LEFT JOIN users d ON d.id = r.done_by_id
"""


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _visible_where(user):
    """SQL podmienka: pripomienky, ktoré používateľ smie vidieť."""
    if user["is_admin"]:
        return ("(r.visibility <> 'ja' OR r.author_id = ? OR EXISTS (SELECT 1 FROM reminder_users x "
                "WHERE x.reminder_id = r.id AND x.user_id = ?))"), [user["id"], user["id"]]
    return ("(r.author_id = ? OR r.visibility = 'vsetci'"
            " OR EXISTS (SELECT 1 FROM reminder_users x WHERE x.reminder_id = r.id AND x.user_id = ?)"
            " OR (r.visibility = 'projekt' AND EXISTS (SELECT 1 FROM project_members m"
            "     WHERE m.project_id = r.project_id AND m.user_id = ?)))"), [user["id"]] * 3


def _enrich(r):
    r = dict(r)
    r["recipients"] = [int(x) for x in (r.get("recipients_raw") or "").split(",") if x]
    r["mine"] = r["author_id"] == g.user["id"]
    r["for_me"] = g.user["id"] in r["recipients"]
    r["can_edit"] = r["mine"] or bool(g.user["is_admin"])
    r["due"] = bool(r["remind_at"] and not r["done_at"] and r["remind_at"] <= now_str())
    return r


def list_visible(project_id=None, done=False, scope=None):
    where, args = _visible_where(g.user)
    sql = SELECT + " WHERE " + where
    sql += " AND r.done_at IS NOT NULL" if done else " AND r.done_at IS NULL"
    if project_id:
        sql += " AND r.project_id = ?"
        args.append(project_id)
    if scope == "moje":
        sql += " AND (r.author_id = ? OR EXISTS (SELECT 1 FROM reminder_users y WHERE y.reminder_id = r.id AND y.user_id = ?))"
        args += [g.user["id"], g.user["id"]]
    elif scope == "zdielane":
        sql += " AND r.visibility <> 'ja' AND r.author_id <> ?"
        args.append(g.user["id"])
    sql += (" ORDER BY r.done_at DESC LIMIT 200" if done
            else " ORDER BY r.remind_at IS NULL, r.remind_at, r.id DESC")
    return [_enrich(r) for r in query(sql, args)]


def get_visible(rid):
    where, args = _visible_where(g.user)
    r = query(SELECT + " WHERE r.id = ? AND " + where, [rid] + args, one=True)
    if not r:
        abort(404)
    return _enrich(r)


def due_for_user(user_id):
    """Pripomienky, ktoré sa práve majú používateľovi zobraziť v okne."""
    now = now_str()
    rows = query(SELECT + """ JOIN reminder_users me ON me.reminder_id = r.id AND me.user_id = ?
        WHERE r.done_at IS NULL AND r.remind_at IS NOT NULL AND me.dismissed_at IS NULL
          AND COALESCE(me.snooze_until, r.remind_at) <= ?
        ORDER BY r.remind_at""", (user_id, now))
    return [_enrich(r) for r in rows]


def count_open_for_me():
    """Počet mojich nesplnených pripomienok, ktorým už nastal čas (číslo v menu)."""
    return query("""SELECT COUNT(*) AS n FROM reminders r JOIN reminder_users me ON me.reminder_id = r.id
                    WHERE me.user_id = ? AND r.done_at IS NULL AND r.remind_at IS NOT NULL AND r.remind_at <= ?""",
                 (g.user["id"], now_str()), one=True)["n"]


def _back(default=None):
    back = request.form.get("back", "")
    if back.startswith("/") and not back.startswith("//"):
        return redirect(back)
    return redirect(default or url_for("rem.index"))


def _read_form(existing=None):
    """Spoločné spracovanie formulára (nová aj úprava). Vracia dict alebo None pri chybe."""
    f = request.form
    text = f.get("text", "").strip()
    if not text:
        flash("Napíšte, čo si chcete pripomenúť.", "error")
        return None
    project_id = f.get("project_id", type=int) if existing is None else existing["project_id"]
    if project_id:
        from .views import can_access
        from .models import get_project
        p = get_project(project_id)
        if not p or not can_access(p):
            abort(403)
    vis = f.get("visibility") if f.get("visibility") in VISIBILITY else "ja"
    if vis == "projekt" and not project_id:
        vis = "ja"
    when = f.get("when") if f.get("when") in WHEN else "nie"
    remind_at = None
    if when == "hned":
        remind_at = now_str()
    elif when == "cas":
        raw = (f.get("remind_at") or "").replace("T", " ").strip()[:16]
        try:
            remind_at = datetime.strptime(raw, "%Y-%m-%d %H:%M").strftime("%Y-%m-%d %H:%M")
        except ValueError:
            flash("Zadajte dátum a čas pripomenutia.", "error")
            return None
    elif when == "ponechat" and existing:
        remind_at = existing["remind_at"]
    active = {u["id"] for u in query("SELECT id FROM users WHERE active = 1")}
    recipients = {int(x) for x in f.getlist("users") if x.isdigit() and int(x) in active}
    author = existing["author_id"] if existing else g.user["id"]
    if author:
        recipients.add(author)
    return {"text": text, "project_id": project_id, "visibility": vis, "remind_at": remind_at,
            "email": 1 if f.get("email") and remind_at else 0, "recipients": recipients}


def _save_image():
    file = request.files.get("image")
    if not file or not file.filename:
        return None
    try:
        return images.save(file.stream)
    except Exception:  # noqa: BLE001
        flash("Obrázok sa nepodarilo uložiť (podporované sú JPG, PNG, GIF, WEBP).", "error")
        return None


def _set_recipients(rid, recipients, reset):
    current = {r["user_id"] for r in query("SELECT user_id FROM reminder_users WHERE reminder_id = ?", (rid,))}
    for uid in recipients - current:
        execute("INSERT OR IGNORE INTO reminder_users(reminder_id, user_id) VALUES (?, ?)", (rid, uid))
    for uid in current - recipients:
        execute("DELETE FROM reminder_users WHERE reminder_id = ? AND user_id = ?", (rid, uid))
    if reset:   # zmenil sa čas → znova zobraziť aj poslať
        execute("UPDATE reminder_users SET snooze_until = NULL, dismissed_at = NULL, emailed_at = NULL "
                "WHERE reminder_id = ?", (rid,))


# --- Stránky --------------------------------------------------------------------

@bp.route("/pripomienky")
@login_required
def index():
    from .models import list_projects
    from .views import active_users

    scope = request.args.get("zobrazit", "moje")
    if scope not in ("moje", "zdielane", "vsetky", "splnene"):
        scope = "moje"
    items = list_visible(done=scope == "splnene", scope=None if scope in ("vsetky", "splnene") else scope)
    projects = [p for p in list_projects(assignee_id=None if g.user["is_admin"] else g.user["id"], include_done=False)]
    return render_template("reminders.html", items=items, scope=scope, users=active_users(),
                           projects=projects, visibility=VISIBILITY, when=WHEN,
                           preset_project=request.args.get("projekt", type=int))


@bp.route("/pripomienky", methods=["POST"])
@login_required
def create():
    data = _read_form()
    if data is None:
        return _back()
    image = _save_image()
    cur = execute("INSERT INTO reminders(project_id, author_id, text, image, visibility, remind_at, email) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?)",
                  (data["project_id"], g.user["id"], data["text"], image, data["visibility"],
                   data["remind_at"], data["email"]))
    _set_recipients(cur.lastrowid, data["recipients"], reset=False)
    if data["remind_at"] and data["remind_at"] > now_str():
        when = current_app.jinja_env.filters["rem_dt"](data["remind_at"]).replace(" ", " o ", 1)
        flash(f"Pripomienka uložená – pripomenie sa {when}.", "success")
    else:
        flash("Pripomienka bola uložená.", "success")
    return _back()


@bp.route("/pripomienky/<int:rid>/upravit", methods=["POST"])
@login_required
def edit(rid):
    r = get_visible(rid)
    if not r["can_edit"]:
        abort(403)
    data = _read_form(existing=r)
    if data is None:
        return _back()
    image = _save_image()
    if image or request.form.get("remove_image"):
        images.delete(r["image"])
    else:
        image = r["image"]
    execute("UPDATE reminders SET text = ?, image = ?, visibility = ?, remind_at = ?, email = ? WHERE id = ?",
            (data["text"], image, data["visibility"], data["remind_at"], data["email"], rid))
    _set_recipients(rid, data["recipients"], reset=data["remind_at"] != r["remind_at"])
    flash("Pripomienka bola upravená.", "success")
    return _back()


def _require_participant(r):
    if not (r["can_edit"] or r["for_me"]):
        abort(403)


@bp.route("/pripomienky/<int:rid>/hotovo", methods=["POST"])
@login_required
def done(rid):
    r = get_visible(rid)
    _require_participant(r)
    if r["done_at"]:
        execute("UPDATE reminders SET done_at = NULL, done_by_id = NULL WHERE id = ?", (rid,))
        flash("Pripomienka je znova aktívna.", "success")
    else:
        execute("UPDATE reminders SET done_at = ?, done_by_id = ? WHERE id = ?", (now_str(), g.user["id"], rid))
        flash("Hotovo – pripomienka je splnená.", "success")
    return _back()


@bp.route("/pripomienky/<int:rid>/odlozit", methods=["POST"])
@login_required
def snooze(rid):
    r = get_visible(rid)
    if not r["for_me"]:
        abort(403)
    how = request.form.get("na")
    now = datetime.now()
    if how == "zajtra":
        until = (now + timedelta(days=1)).replace(hour=8, minute=0)
        label = "zajtra o 8:00"
    else:
        until = now + timedelta(hours=1)
        label = "o hodinu"
    execute("UPDATE reminder_users SET snooze_until = ?, dismissed_at = NULL WHERE reminder_id = ? AND user_id = ?",
            (until.strftime("%Y-%m-%d %H:%M"), rid, g.user["id"]))
    flash(f"Pripomeniem znova {label}.", "success")
    return _back()


@bp.route("/pripomienky/<int:rid>/zavriet", methods=["POST"])
@login_required
def dismiss(rid):
    execute("UPDATE reminder_users SET dismissed_at = ? WHERE reminder_id = ? AND user_id = ?",
            (now_str(), rid, g.user["id"]))
    return _back()


@bp.route("/pripomienky/<int:rid>/zmazat", methods=["POST"])
@login_required
def delete(rid):
    r = get_visible(rid)
    if not r["can_edit"]:
        abort(403)
    execute("DELETE FROM reminders WHERE id = ?", (rid,))
    images.delete(r["image"])
    flash("Pripomienka bola zmazaná.", "success")
    return _back()


@bp.route("/pripomienky/<int:rid>/obrazok")
@login_required
def image(rid):
    r = get_visible(rid)
    if not r["image"]:
        abort(404)
    name = ("t_" if request.args.get("nahlad") else "") + r["image"]
    return send_from_directory(current_app.config["IMAGE_FOLDER"], name, max_age=3600)


# --- E-maily (plánovač) -----------------------------------------------------------

def send_due_emails(app):
    """Raz za minútu: pošle e-maily k pripomienkam, ktorým nastal čas. Každému adresátovi len raz."""
    now = now_str()
    conn = connect(app.config["DATABASE"])
    try:
        rows = conn.execute("""
            SELECT r.id, r.text, r.remind_at, r.project_id, r.image, p.name AS project_name, a.name AS author_name,
                   u.id AS user_id, u.name AS user_name, u.email
            FROM reminders r JOIN reminder_users ru ON ru.reminder_id = r.id
            JOIN users u ON u.id = ru.user_id AND u.active = 1
            LEFT JOIN projects p ON p.id = r.project_id LEFT JOIN users a ON a.id = r.author_id
            WHERE r.email = 1 AND r.done_at IS NULL AND r.remind_at IS NOT NULL
              AND COALESCE(ru.snooze_until, r.remind_at) <= ? AND ru.emailed_at IS NULL""", (now,)).fetchall()
        for row in rows:
            # najprv zarezervujeme, aby pri viacerých procesoch servera e-mail neodišiel dvakrát
            cur = conn.execute("UPDATE reminder_users SET emailed_at = ? WHERE reminder_id = ? AND user_id = ? "
                               "AND emailed_at IS NULL", (now, row["id"], row["user_id"]))
            conn.commit()
            if cur.rowcount != 1 or not row["email"]:
                continue
            from .mailer import send_mail
            with app.app_context():
                subject = "Pripomienka" + (f" – {row['project_name']}" if row["project_name"] else "")
                first = row["text"].strip().splitlines()[0][:60]
                send_mail(row["email"], f"{subject}: {first}", "email/reminder.html", r=dict(row))
    finally:
        conn.close()
