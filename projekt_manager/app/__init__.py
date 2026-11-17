import logging
import os
from datetime import timedelta

from flask import Flask, g, render_template

from config import Config


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    app.permanent_session_lifetime = timedelta(days=14)

    os.makedirs(app.config["DATA_DIR"], exist_ok=True)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    os.makedirs(app.config["IMAGE_FOLDER"], exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    from .db import init_db
    init_db(app)

    from . import settings
    settings.init(app)  # nastavenia uložené v aplikácii majú prednosť pred .env

    from . import auth, views, cli
    app.register_blueprint(auth.bp)
    app.register_blueprint(views.bp)
    cli.register(app)

    app.before_request(lambda: settings.refresh(app))
    app.before_request(auth.load_user)
    app.before_request(auth.check_csrf)
    app.jinja_env.globals["csrf_token"] = auth.csrf_token

    @app.template_filter("sk_date")
    def sk_date(value, fmt="%d.%m.%Y"):
        from .models import parse_date
        if not value:
            return "—"
        if isinstance(value, str) and len(value) > 10:
            from datetime import datetime
            return datetime.strptime(value[:16].replace("T", " "), "%Y-%m-%d %H:%M").strftime("%d.%m.%Y %H:%M")
        return parse_date(value).strftime(fmt)

    @app.template_filter("sk_weekday")
    def sk_weekday(value):
        from .models import WEEKDAYS_SK
        return WEEKDAYS_SK[value.weekday()] if value else ""

    @app.template_filter("sk_month")
    def sk_month(value):
        from .models import MONTHS_SK
        return MONTHS_SK[value.month - 1] if value else ""

    from .disk import human as _human
    app.jinja_env.filters["bytes"] = _human

    @app.template_filter("filesize")
    def filesize(n):
        for unit in ("B", "kB", "MB", "GB"):
            if n < 1024:
                return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
            n /= 1024
        return f"{n:.1f} TB"

    @app.context_processor
    def inject():
        from datetime import date
        from .models import STATUSES
        from flask import has_request_context, request
        from .views import FONT_SCALES
        user = g.get("user")
        scale = None
        if user:
            scale = user["font_scale"]
        elif has_request_context():  # e-maily z plánovača sa renderujú mimo požiadavky
            scale = request.cookies.get("font_scale", type=int)
        disk_alert = None
        if user and user["is_admin"]:
            from .disk import usage
            d = usage(app.config["DATA_DIR"])
            disk_alert = d if d and d["level"] != "ok" else None
        return {"app_name": app.config["APP_NAME"], "current_user": user, "disk_alert": disk_alert,
                "font_scale": scale if scale in FONT_SCALES else 100, "font_scales": FONT_SCALES,
                "statuses": STATUSES, "today_str": date.today().strftime("%d.%m.%Y")}

    for code, text in ((400, "Neplatná požiadavka"), (403, "Na túto stránku nemáte prístup"),
                       (404, "Stránka sa nenašla"), (413, "Súbor je príliš veľký")):
        def handler(e, code=code, text=text):
            return render_template("error.html", code=code, text=text,
                                   detail=getattr(e, "description", "")), code
        app.register_error_handler(code, handler)

    return app
