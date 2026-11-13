from functools import wraps

from flask import (Blueprint, abort, flash, g, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db

bp = Blueprint("auth", __name__)

ROLES = {"admin": "Administrátor", "technik": "Technik"}


def current_user():
    if "user" in g:
        return g.user
    uid = session.get("uid")
    g.user = None
    if uid:
        g.user = get_db().query("SELECT * FROM users WHERE id = ? AND active = 1", (uid,), one=True)
    return g.user


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if current_user()["role"] != "admin":
            abort(403)
        return view(*args, **kwargs)

    return wrapped


@bp.route("/prihlasenie", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = get_db().query(
            "SELECT * FROM users WHERE LOWER(username) = LOWER(?) AND active = 1",
            (request.form.get("username", "").strip(),), one=True,
        )
        if user and check_password_hash(user["password_hash"], request.form.get("password", "")):
            session.clear()
            session.permanent = True
            session["uid"] = user["id"]
            nxt = request.args.get("next") or ""
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("main.dashboard"))
        flash("Nesprávne meno alebo heslo.", "error")
    return render_template("login.html")


@bp.route("/odhlasenie")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


# --- Správa používateľov -------------------------------------------------------

@bp.route("/pouzivatelia", methods=["GET", "POST"])
@admin_required
def users():
    db = get_db()
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        role = request.form.get("role", "technik")
        if not username or len(password) < 4 or role not in ROLES:
            flash("Vyplňte meno a heslo (min. 4 znaky).", "error")
        elif db.scalar("SELECT COUNT(*) FROM users WHERE LOWER(username) = LOWER(?)", (username,)):
            flash("Používateľ s týmto menom už existuje.", "error")
        else:
            db.execute(
                "INSERT INTO users (username, full_name, password_hash, role) VALUES (?, ?, ?, ?)",
                (username, request.form.get("full_name", "").strip(), generate_password_hash(password), role),
            )
            db.commit()
            flash(f"Používateľ {username} bol vytvorený.", "ok")
        return redirect(url_for("auth.users"))
    rows = db.query("SELECT * FROM users ORDER BY active DESC, username")
    return render_template("users.html", users=rows, roles=ROLES)


@bp.route("/pouzivatelia/<int:uid>", methods=["POST"])
@admin_required
def user_update(uid):
    db = get_db()
    user = db.query("SELECT * FROM users WHERE id = ?", (uid,), one=True) or abort(404)
    action = request.form.get("action")
    me = current_user()
    if action == "password":
        pw = request.form.get("password", "")
        if len(pw) < 4:
            flash("Heslo musí mať aspoň 4 znaky.", "error")
        else:
            db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(pw), uid))
            flash(f"Heslo pre {user['username']} bolo zmenené.", "ok")
    elif action == "role" and uid != me["id"]:
        role = request.form.get("role")
        if role in ROLES:
            db.execute("UPDATE users SET role = ? WHERE id = ?", (role, uid))
            flash("Rola bola zmenená.", "ok")
    elif action == "toggle" and uid != me["id"]:
        db.execute("UPDATE users SET active = ? WHERE id = ?", (0 if user["active"] else 1, uid))
        flash("Stav účtu bol zmenený.", "ok")
    db.commit()
    return redirect(url_for("auth.users"))


@bp.route("/moje-heslo", methods=["GET", "POST"])
@login_required
def my_password():
    if request.method == "POST":
        me = current_user()
        if not check_password_hash(me["password_hash"], request.form.get("old", "")):
            flash("Pôvodné heslo nie je správne.", "error")
        elif len(request.form.get("new", "")) < 4:
            flash("Nové heslo musí mať aspoň 4 znaky.", "error")
        else:
            db = get_db()
            db.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                       (generate_password_hash(request.form["new"]), me["id"]))
            db.commit()
            flash("Heslo bolo zmenené.", "ok")
            return redirect(url_for("main.dashboard"))
    return render_template("my_password.html")
