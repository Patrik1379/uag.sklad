"""Prihlasovanie, role a ochrana formulárov (CSRF)."""
import logging
import secrets
import time
from functools import wraps

from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template,
                   request, session, url_for)
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash

from .db import query

bp = Blueprint("auth", __name__)


def load_user():
    g.user = None
    uid = session.get("uid")
    if uid:
        user = query("SELECT * FROM users WHERE id = ? AND active = 1", (uid,), one=True)
        if user:
            g.user = user
        else:
            session.clear()


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def check_csrf():
    if request.method == "POST":
        sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
        if not sent or not secrets.compare_digest(sent, session.get("csrf", "")):
            abort(400, "Neplatný bezpečnostný token formulára – obnovte stránku a skúste znova.")


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("auth.login", next=request.full_path))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not g.user["is_admin"]:
            abort(403)
        return view(*args, **kwargs)
    return wrapped


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        user = query("SELECT * FROM users WHERE email = ? AND active = 1", (email,), one=True)
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["uid"] = user["id"]
            session.permanent = True
            nxt = request.args.get("next", "")
            if not nxt.startswith("/") or nxt.startswith("//"):
                nxt = url_for("main.dashboard")
            return redirect(nxt)
        flash("Nesprávny e-mail alebo heslo.", "error")
    return render_template("login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


# ---------------------------------------------------- zabudnuté heslo (obnova cez e-mail)

RESET_MAX_AGE = 3600          # odkaz platí 1 hodinu
_last_reset = {}              # user id -> čas posledného e-mailu (ochrana pred zahltením)
log = logging.getLogger(__name__)


def _reset_serializer():
    return URLSafeTimedSerializer(current_app.secret_key, salt="obnova-hesla")


def _mail_ready():
    cfg = current_app.config
    return bool(cfg.get("MAIL_SERVER") and cfg.get("MAIL_SENDER"))


def _user_from_token(token):
    try:
        data = _reset_serializer().loads(token, max_age=RESET_MAX_AGE)
    except (SignatureExpired, BadSignature):
        return None
    user = query("SELECT * FROM users WHERE id = ? AND active = 1", (data.get("u"),), one=True)
    # časť odtlačku hesla v tokene → po zmene hesla odkaz prestane platiť (dá sa použiť len raz)
    if not user or user["password_hash"][-16:] != data.get("h"):
        return None
    return user


@bp.route("/zabudnute-heslo", methods=["GET", "POST"])
def forgot_password():
    if g.user:
        return redirect(url_for("main.dashboard"))
    mail_ok = _mail_ready()
    if request.method == "POST" and mail_ok:
        from . import mailer

        email = request.form.get("email", "").strip()
        user = query("SELECT * FROM users WHERE email = ? AND active = 1", (email,), one=True) if email else None
        if user and time.time() - _last_reset.get(user["id"], 0) > 120:
            _last_reset[user["id"]] = time.time()
            token = _reset_serializer().dumps({"u": user["id"], "h": user["password_hash"][-16:]})
            cfg = current_app.config
            link = cfg["APP_URL"].rstrip("/") + url_for("auth.reset_password", token=token)
            html = render_template("email/reset.html", user=user, link=link,
                                   app_url=cfg["APP_URL"], app_name=cfg["APP_NAME"])
            try:
                mailer._deliver(cfg, mailer._build(cfg, user["email"], f"Obnova hesla – {cfg['APP_NAME']}", html))
            except Exception:  # noqa: BLE001
                log.exception("E-mail na obnovu hesla sa nepodarilo odoslať")
        # rovnaká odpoveď vždy – nech sa nedá zisťovať, ktoré e-maily majú účet
        flash("Ak k tomuto e-mailu existuje účet, poslali sme naň odkaz na nastavenie nového hesla. "
              "Pozrite si schránku (aj nevyžiadanú poštu).", "success")
        return redirect(url_for("auth.login"))
    return render_template("forgot_password.html", mail_ok=mail_ok)


@bp.route("/obnova-hesla/<token>", methods=["GET", "POST"])
def reset_password(token):
    user = _user_from_token(token)
    if not user:
        flash("Odkaz na obnovu hesla je neplatný alebo už vypršal. Požiadajte o nový.", "error")
        return redirect(url_for("auth.forgot_password"))
    if request.method == "POST":
        from .db import execute

        pw = request.form.get("password", "")
        if len(pw) < 8:
            flash("Heslo musí mať aspoň 8 znakov.", "error")
        elif pw != request.form.get("password2", ""):
            flash("Heslá sa nezhodujú.", "error")
        else:
            execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(pw), user["id"]))
            flash("Heslo bolo zmenené – prihláste sa novým heslom.", "success")
            return redirect(url_for("auth.login"))
    return render_template("reset_password.html", reset_user=user)
