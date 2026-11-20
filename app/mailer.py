"""Nastavenia aplikácie (tabuľka settings) a odosielanie e-mailov cez SMTP."""
import smtplib
import ssl
import threading
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from .db import DB, _connect, get_db

DEFAULTS = {
    "smtp_host": "",
    "smtp_port": "587",
    "smtp_security": "starttls",      # none | starttls | ssl
    "smtp_user": "",
    "smtp_password": "",
    "smtp_from": "",
    "smtp_from_name": "Sklad dielov",
    "app_url": "",
    "notify_project_enabled": "0",
    "notify_project_all_users": "1",
    "notify_project_extra": "",
    "mail_last_status": "",
    "api_key": "",                    # kľúč pre prepojenie s Projekt manažérom
}


def get_settings():
    rows = get_db().query("SELECT key, value FROM settings")
    data = dict(DEFAULTS)
    data.update({r["key"]: r["value"] or "" for r in rows})
    if not data.get("app_url"):
        # verejná adresa skladu z .env (DOMAIN) – inak by odkazy pri volaní cez API (http://web:8000)
        # smerovali na vnútornú adresu servera, ktorá zvonku neexistuje
        import os
        dom = (os.environ.get("DOMAIN") or "").strip()
        if dom and dom != "localhost":
            data["app_url"] = dom if dom.startswith("http") else "https://" + dom
    return data


def save_settings(values):
    db = get_db()
    for key, value in values.items():
        if key not in DEFAULTS:
            continue
        if db.scalar("SELECT COUNT(*) FROM settings WHERE key = ?", (key,)):
            db.execute("UPDATE settings SET value = ? WHERE key = ?", (value, key))
        else:
            db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, value))
    db.commit()


def _store_status(text):
    """Zapíše výsledok posledného odoslania (volá sa aj z vlákna mimo požiadavky)."""
    db = DB(_connect())
    try:
        stamp = datetime.now().strftime("%d.%m.%Y %H:%M")
        value = f"{stamp} – {text}"[:500]
        if db.scalar("SELECT COUNT(*) FROM settings WHERE key = 'mail_last_status'"):
            db.execute("UPDATE settings SET value = ? WHERE key = 'mail_last_status'", (value,))
        else:
            db.execute("INSERT INTO settings (key, value) VALUES ('mail_last_status', ?)", (value,))
        db.commit()
    finally:
        db.close()


def smtp_ready(cfg):
    return bool(cfg.get("smtp_host") and cfg.get("smtp_from"))


def _login(server, user, password):
    """Prihlásenie na SMTP. smtplib vie len ASCII – heslo s diakritikou (š, č, ž…) posielame v UTF-8 ručne."""
    import base64

    try:
        (user + password).encode("ascii")
        server.login(user, password)
        return
    except UnicodeEncodeError:
        pass
    token = base64.b64encode(b"\0" + user.encode("utf-8") + b"\0" + password.encode("utf-8")).decode("ascii")
    code, resp = server.docmd("AUTH", "PLAIN " + token)
    if code == 235:
        return
    code, resp = server.docmd("AUTH", "LOGIN " + base64.b64encode(user.encode("utf-8")).decode("ascii"))
    if code == 334:
        code, resp = server.docmd(base64.b64encode(password.encode("utf-8")).decode("ascii"))
    if code != 235:
        raise smtplib.SMTPAuthenticationError(code, resp)


def send_mail(cfg, recipients, subject, text, html=None):
    """Odošle e-mail (blokujúco). Pri chybe vyhodí výnimku so zrozumiteľným popisom."""
    if not smtp_ready(cfg):
        raise RuntimeError("SMTP server nie je nastavený (chýba server alebo adresa odosielateľa).")
    recipients = [r for r in dict.fromkeys(recipients) if r]
    if not recipients:
        raise RuntimeError("Nie je komu poslať – žiadny príjemca nemá e-mail.")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((cfg.get("smtp_from_name") or "", cfg["smtp_from"]))
    msg["To"] = ", ".join(recipients)
    msg["Message-ID"] = make_msgid(domain=cfg["smtp_from"].split("@")[-1] or None)
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    port = int(cfg.get("smtp_port") or 0) or (465 if cfg.get("smtp_security") == "ssl" else 587)
    ctx = ssl.create_default_context()
    if cfg.get("smtp_security") == "ssl":
        server = smtplib.SMTP_SSL(cfg["smtp_host"], port, timeout=20, context=ctx)
    else:
        server = smtplib.SMTP(cfg["smtp_host"], port, timeout=20)
    try:
        server.ehlo()
        if cfg.get("smtp_security") == "starttls":
            server.starttls(context=ctx)
            server.ehlo()
        if cfg.get("smtp_user"):
            _login(server, cfg["smtp_user"], cfg.get("smtp_password") or "")
        server.send_message(msg)
    finally:
        try:
            server.quit()
        except Exception:
            pass


def send_mail_async(cfg, recipients, subject, text, html=None):
    """Odošle e-mail na pozadí, aby používateľ nečakal na SMTP server."""
    def run():
        try:
            send_mail(cfg, recipients, subject, text, html)
            _store_status(f"OK: „{subject}“ → {len(recipients)} príjemcov")
        except Exception as exc:
            _store_status(f"CHYBA pri „{subject}“: {exc}")

    threading.Thread(target=run, daemon=True).start()


def project_recipients(cfg):
    out = []
    if cfg.get("notify_project_all_users") == "1":
        out += [u["email"] for u in get_db().query(
            "SELECT email FROM users WHERE active = 1 AND email IS NOT NULL AND email <> ''")]
    for part in (cfg.get("notify_project_extra") or "").replace(";", ",").replace("\n", ",").split(","):
        if "@" in part:
            out.append(part.strip())
    return list(dict.fromkeys(out))


def notify_new_project(project_id, author=None, base_url="", source=None):
    """Pošle oznámenie o novom projekte, ak je zapnuté v nastaveniach."""
    from markupsafe import escape

    cfg = get_settings()
    if cfg.get("notify_project_enabled") != "1" or not smtp_ready(cfg):
        return False
    recipients = project_recipients(cfg)
    if not recipients:
        return False
    db = get_db()
    p = db.query("SELECT * FROM projects WHERE id = ?", (project_id,), one=True)
    if not p:
        return False
    items = db.scalar("SELECT COUNT(*) FROM project_parts WHERE project_id = ?", (project_id,)) or 0
    base = (cfg.get("app_url") or base_url or "").rstrip("/")
    link = f"{base}/projekty/{project_id}" if base else ""

    def fmt(d):
        if not d:
            return "—"
        try:
            return datetime.fromisoformat(str(d)[:10]).strftime("%d.%m.%Y")
        except ValueError:
            return str(d)

    rows = [("Projekt", p["name"]), ("Číslo zakázky", p["order_no"]), ("Zákazník", p.get("customer") or "—"),
            ("Začiatok", fmt(p.get("start_date"))), ("Termín", fmt(p.get("deadline"))),
            ("Počet položiek v kusovníku", str(items)), ("Vytvoril", author or "—")]
    if source:
        rows.append(("Založené z", source))
    subject = f"Nový projekt: {p['name']} ({p['order_no']})"
    text = "V aplikácii Sklad dielov bol vytvorený nový projekt.\n\n" + "\n".join(
        f"{k}: {v}" for k, v in rows) + (f"\n\nOtvoriť projekt: {link}" if link else "")
    if p.get("note"):
        text += f"\n\nPoznámka:\n{p['note']}"
    html = ('<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#1c1c1a">'
            '<p>V aplikácii <b>Sklad dielov</b> bol vytvorený nový projekt.</p>'
            '<table cellpadding="6" style="border-collapse:collapse">'
            + "".join(f'<tr><td style="color:#55554f">{escape(k)}</td><td><b>{escape(v)}</b></td></tr>' for k, v in rows)
            + "</table>"
            + (f'<p><a href="{escape(link)}" style="display:inline-block;background:#2563c9;color:#fff;'
               f'padding:10px 16px;border-radius:8px;text-decoration:none">Otvoriť projekt</a></p>' if link else "")
            + (f'<p style="color:#55554f;white-space:pre-line">{escape(p["note"])}</p>' if p.get("note") else "")
            + "</div>")
    send_mail_async(cfg, recipients, subject, text, html)
    return True
