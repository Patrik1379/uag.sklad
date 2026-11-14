import os
import secrets
from datetime import date, datetime

from flask import Flask, abort, request, session
from markupsafe import Markup, escape

from . import db as dbmod


def create_app():
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", "zmen-ma-v-produkcii"),
        UPLOAD_FOLDER=os.environ.get("UPLOAD_FOLDER", os.path.abspath("data/uploads")),
        MAX_CONTENT_LENGTH=20 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=60 * 60 * 12,
    )
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    # Za HTTPS proxy (Caddy) – správna schéma/IP z hlavičiek a bezpečné cookies
    if os.environ.get("BEHIND_PROXY") == "1":
        from werkzeug.middleware.proxy_fix import ProxyFix

        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    if os.environ.get("COOKIE_SECURE") == "1":
        app.config["SESSION_COOKIE_SECURE"] = True

    app.teardown_appcontext(dbmod.close_db)

    # --- CSRF ochrana formulárov -------------------------------------------
    def csrf_token():
        if "_csrf" not in session:
            session["_csrf"] = secrets.token_hex(16)
        return session["_csrf"]

    @app.before_request
    def check_csrf():
        if request.method == "POST":
            token = request.form.get("_csrf") or request.headers.get("X-CSRF")
            if not token or token != session.get("_csrf"):
                abort(400, "Neplatný formulár (CSRF). Obnovte stránku a skúste znova.")

    # --- Jinja filtre --------------------------------------------------------
    def fmt_date(value, with_time=False):
        if not value:
            return ""
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value)
            except ValueError:
                return value
        if isinstance(value, datetime):
            return value.strftime("%d.%m.%Y %H:%M" if with_time else "%d.%m.%Y")
        if isinstance(value, date):
            return value.strftime("%d.%m.%Y")
        return str(value)

    def iso_date(value):
        if not value:
            return ""
        if isinstance(value, (date, datetime)):
            return value.strftime("%Y-%m-%d")
        return str(value)[:10]

    def tip_escape(value):
        """Dvojité escapovanie textu pre atribút data-tip (vkladá sa cez innerHTML)."""
        return Markup(escape(str(escape("" if value is None else value))))

    app.jinja_env.filters["d"] = fmt_date
    app.jinja_env.filters["t"] = tip_escape
    app.jinja_env.filters["iso"] = iso_date
    app.jinja_env.globals["csrf_token"] = csrf_token

    from .services import location_label, location_parts, locations_map, racks_map, shelf_text
    app.jinja_env.filters["loc"] = location_label
    app.jinja_env.filters["shelf"] = shelf_text
    app.jinja_env.globals["loc_parts"] = location_parts
    app.jinja_env.globals["racks_map"] = racks_map
    app.jinja_env.globals["locations_map"] = locations_map

    from .auth import bp as auth_bp, current_user
    from .routes import bp as main_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)

    @app.context_processor
    def inject_user():
        user = current_user()
        new_feedback = 0
        if user and user["role"] == "admin":
            from .db import get_db
            new_feedback = int(get_db().scalar("SELECT COUNT(*) FROM feedback WHERE status = 'nova'") or 0)
        return {"current_user": user, "new_feedback": new_feedback}

    return app
