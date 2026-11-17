"""Nastavenia upraviteľné v aplikácii (Admin → Nastavenia).

Hodnoty sa ukladajú do tabuľky settings s predponou "cfg." a majú prednosť pred .env.
Ak hodnota v databáze nie je, platí hodnota z .env (resp. predvolená v config.py).
Pri behu viacerých procesov (gunicorn workery) si každý proces zmenu načíta sám –
pri uložení sa zvýši "cfg_version" a proces pri ďalšej požiadavke nastavenia obnoví.
"""
import re

from .db import connect

# kľúč: (typ, popis)
FIELDS = {
    "APP_NAME":                  ("str",  "Názov aplikácie"),
    "APP_URL":                   ("str",  "Verejná adresa aplikácie"),
    "DUE_SOON_DAYS":             ("int",  "Upozorniť na deadline (dni vopred)"),
    "RISK_MARGIN":               ("int",  "Ohrozený projekt – povolené zaostávanie (%)"),
    "MAIL_ENABLED":              ("bool", "Odosielať e-maily"),
    "MAIL_SERVER":               ("str",  "SMTP server"),
    "MAIL_PORT":                 ("int",  "Port"),
    "MAIL_USE_TLS":              ("bool", "STARTTLS"),
    "MAIL_USE_SSL":              ("bool", "SSL/TLS"),
    "MAIL_USERNAME":             ("str",  "Používateľské meno"),
    "MAIL_PASSWORD":             ("str",  "Heslo"),
    "MAIL_SENDER":               ("str",  "Odosielateľ (From)"),
    "DAILY_REPORT_TIME":         ("str",  "Čas denného reportu"),
    "DAILY_REPORT_SKIP_WEEKEND": ("bool", "Cez víkend neposielať"),
    "SKLAD_URL":                 ("str",  "Adresa Skladu dielov"),
    "SKLAD_API_KEY":             ("str",  "API kľúč Skladu dielov"),
}
PREFIX = "cfg."


def _cast(kind, value):
    if kind == "int":
        return int(value)
    if kind == "bool":
        return value in ("1", "true", "True")
    return value


def _dump(kind, value):
    if kind == "bool":
        return "1" if value else "0"
    return str(value)


def init(app):
    """Zapamätá si hodnoty z .env a aplikuje uložené nastavenia."""
    app.extensions["env_settings"] = {k: app.config.get(k) for k in FIELDS}
    app.extensions["cfg_version"] = None
    refresh(app, force=True)


def refresh(app, force=False):
    """Načíta nastavenia z databázy, ak sa od posledného načítania zmenili."""
    conn = connect(app.config["DATABASE"])
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = 'cfg_version'").fetchone()
        version = row["value"] if row else "0"
        if not force and version == app.extensions.get("cfg_version"):
            return
        stored = {r["key"][len(PREFIX):]: r["value"]
                  for r in conn.execute("SELECT key, value FROM settings WHERE key LIKE 'cfg.%'")}
    finally:
        conn.close()
    env = app.extensions["env_settings"]
    for key, (kind, _) in FIELDS.items():
        if key in stored:
            try:
                app.config[key] = _cast(kind, stored[key])
                continue
            except ValueError:
                pass
        app.config[key] = env.get(key)
    app.extensions["cfg_version"] = version


def overridden(app):
    """Kľúče, ktoré sú nastavené v aplikácii (nie z .env)."""
    conn = connect(app.config["DATABASE"])
    try:
        return {r["key"][len(PREFIX):] for r in conn.execute("SELECT key FROM settings WHERE key LIKE 'cfg.%'")}
    finally:
        conn.close()


def save(app, values, reset=()):
    """values = {kľúč: hodnota (už v správnom type)}; reset = kľúče, ktoré sa vrátia na .env."""
    conn = connect(app.config["DATABASE"])
    try:
        for key, value in values.items():
            kind = FIELDS[key][0]
            conn.execute("INSERT INTO settings(key, value) VALUES(?, ?) "
                         "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (PREFIX + key, _dump(kind, value)))
        for key in reset:
            conn.execute("DELETE FROM settings WHERE key = ?", (PREFIX + key,))
        conn.execute("INSERT INTO settings(key, value) VALUES('cfg_version', '1') "
                     "ON CONFLICT(key) DO UPDATE SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT)")
        conn.commit()
    finally:
        conn.close()
    refresh(app, force=True)


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
