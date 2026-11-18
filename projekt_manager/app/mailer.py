"""Odosielanie e-mailov cez SMTP a jednotlivé typy notifikácií."""
import logging
import smtplib
import ssl
from datetime import date
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from flask import current_app, render_template

from .db import query
from .models import list_installations, list_projects, list_tasks, project_stats

log = logging.getLogger(__name__)


def send_mail(to, subject, template, **ctx):
    """Vyrenderuje HTML šablónu a odošle ju. Vráti True/False."""
    cfg = current_app.config
    ctx.setdefault("app_url", cfg["APP_URL"])
    ctx.setdefault("app_name", cfg["APP_NAME"])
    html = render_template(template, **ctx)

    if not cfg["MAIL_ENABLED"]:
        log.info("[MAIL vypnutý] %s -> %s", subject, to)
        return False

    try:
        _deliver(cfg, _build(cfg, to, subject, html))
        log.info("E-mail odoslaný: %s -> %s", subject, to)
        return True
    except Exception:  # noqa: BLE001 – chyba mailu nesmie zhodiť aplikáciu
        log.exception("Nepodarilo sa odoslať e-mail '%s' na %s", subject, to)
        return False


def send_test_mail(to):
    """Testovací e-mail z Nastavení – pošle sa aj pri vypnutom odosielaní. Vráti (ok, správa)."""
    cfg = current_app.config
    html = render_template("email/test.html", app_url=cfg["APP_URL"], app_name=cfg["APP_NAME"])
    try:
        _deliver(cfg, _build(cfg, to, f"Test – {cfg['APP_NAME']}", html))
        return True, f"Testovací e-mail bol odoslaný na {to}."
    except smtplib.SMTPAuthenticationError:
        return False, "Server odmietol prihlásenie – skontrolujte meno a heslo (pri Gmaile treba „heslo aplikácie“)."
    except (OSError, smtplib.SMTPException) as e:
        return False, f"Nepodarilo sa odoslať: {e.__class__.__name__}: {e}"


def _build(cfg, to, subject, html):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((cfg["APP_NAME"], cfg["MAIL_SENDER"]))
    msg["To"] = to
    msg["Message-ID"] = make_msgid()
    msg.set_content("Tento e-mail je vo formáte HTML. Otvorte ho v poštovom klientovi s podporou HTML.")
    msg.add_alternative(html, subtype="html")
    return msg


def _deliver(cfg, msg):
    context = ssl.create_default_context()
    if cfg["MAIL_USE_SSL"]:
        server = smtplib.SMTP_SSL(cfg["MAIL_SERVER"], cfg["MAIL_PORT"], context=context, timeout=20)
    else:
        server = smtplib.SMTP(cfg["MAIL_SERVER"], cfg["MAIL_PORT"], timeout=20)
    with server:
        if cfg["MAIL_USE_TLS"] and not cfg["MAIL_USE_SSL"]:
            server.starttls(context=context)
        if cfg["MAIL_USERNAME"]:
            server.login(cfg["MAIL_USERNAME"], cfg["MAIL_PASSWORD"])
        server.send_message(msg)


# ---------------------------------------------------------------- notifikácie

def notify_assigned(project, user_ids):
    """E-mail novým riešiteľom projektu (vrátane mien kolegov v tíme)."""
    sent = 0
    for member in project.members:
        if member["id"] not in user_ids:
            continue
        colleagues = [m["name"] for m in project.members if m["id"] != member["id"]]
        sent += send_mail(
            member["email"],
            f"Bol vám pridelený projekt: {project.name}",
            "email/assigned.html",
            project=project, member=member, colleagues=colleagues,
        )
    return sent


def notify_status_change(project, changed_by, note):
    """Zmenu stavu dostane zakladateľ projektu a ostatní riešitelia (nie ten, kto zmenu urobil)."""
    recipients = {}
    if project.creator_email and project.created_by_id != changed_by["id"]:
        recipients[project.creator_email.lower()] = project.creator_email
    for m in project.members:
        if m["id"] != changed_by["id"]:
            recipients[m["email"].lower()] = m["email"]
    sent = 0
    for email in recipients.values():
        sent += send_mail(
            email,
            f"Zmena v projekte {project.name}: {project.status_label}, {project.progress} %",
            "email/status_changed.html",
            project=project, changed_by=changed_by, note=note,
        )
    return sent


def notify_task_assigned(task, user_ids=None):
    """E-mail novým riešiteľom úlohy (user_ids = komu; None = všetkým riešiteľom)."""
    sent = 0
    for a in task.assignees:
        if user_ids is not None and a["id"] not in user_ids:
            continue
        if a.get("email"):
            sent += send_mail(a["email"], f"Nová úloha: {task.title} ({task.project_name})",
                              "email/task_assigned.html", task=task, recipient=a)
    return sent


def _done_recipients(task, done_by):
    """Zadávateľ projektu a autor úlohy – okrem toho, kto úlohu/podúlohu splnil."""
    from .models import get_project
    project = get_project(task.project_id)
    recipients = {}
    for uid, email in ((project.created_by_id if project else None, project.creator_email if project else None),
                       (task.created_by_id, task.creator_email)):
        if uid and email and uid != done_by["id"]:
            recipients[email.lower()] = email
    return list(recipients.values())


def notify_task_done(task, done_by, last_subtask=None):
    """Úloha je hotová (ručne alebo splnením poslednej podúlohy)."""
    sent = 0
    for email in _done_recipients(task, done_by):
        sent += send_mail(email, f"Úloha splnená: {task.title} ({task.project_name})", "email/task_done.html",
                          task=task, done_by=done_by, last_subtask=last_subtask)
    return sent


def notify_subtask_done(task, subtask_title, done_by):
    """Splnená podúloha (úloha ešte nie je celá hotová)."""
    sent = 0
    for email in _done_recipients(task, done_by):
        sent += send_mail(email, f"Podúloha splnená: {subtask_title} ({task.title})", "email/subtask_done.html",
                          task=task, subtask_title=subtask_title, done_by=done_by)
    return sent


def notify_task_comment(task, author, body):
    """Komentár dostane riešiteľ úlohy, jej autor a ostatní, ktorí ju komentovali (okrem autora komentára)."""
    recipients = {}
    people = [(a["id"], a["email"]) for a in task.assignees] + [(task.created_by_id, task.creator_email)]
    for uid, email in people:
        if uid and email and uid != author["id"]:
            recipients[email.lower()] = email
    for row in query("SELECT DISTINCT u.id, u.email FROM task_comments k JOIN users u ON u.id = k.user_id "
                     "WHERE k.task_id = ? AND u.active = 1", (task.id,)):
        if row["id"] != author["id"]:
            recipients[row["email"].lower()] = row["email"]
    sent = 0
    for email in recipients.values():
        sent += send_mail(email, f"Nový komentár k úlohe: {task.title}", "email/task_comment.html",
                          task=task, author=author, body=body)
    return sent


def notify_installation(project, user_ids, changed_by, kind):
    """Technikom príde termín inštalácie (kind: new / changed)."""
    sent = 0
    for m in project.installers:
        if m["id"] in user_ids:
            subject = ("Naplánovaná inštalácia" if kind == "new" else "Zmena termínu inštalácie") + \
                f": {project.name} – {project.install_date:%d.%m.%Y}"
            sent += send_mail(m["email"], subject, "email/install.html",
                              project=project, member=m, changed_by=changed_by, kind=kind)
    return sent


def send_install_reminders(skip_weekend=True):
    """Pripomienka deň vopred. V piatok (pri vynechaní víkendu) pokryje aj sobotu, nedeľu a pondelok.
    Každú inštaláciu pripomenie len raz (stĺpec install_reminded)."""
    from .db import execute
    today = date.today()
    horizon = 1
    if skip_weekend and today.weekday() == 4:
        horizon = 3
    sent = 0
    for p in list_installations(days_back=0, days_ahead=horizon):
        if p.install_status != "planovana" or p.install_reminded == p.install_date.isoformat():
            continue
        people = p.installers or p.members
        for m in people:
            sent += send_mail(m["email"], f"Pripomienka: inštalácia {p.install_when_text} – {p.name}",
                              "email/install_reminder.html", project=p, member=m)
        execute("UPDATE projects SET install_reminded = ? WHERE id = ?", (p.install_date.isoformat(), p.id))
    log.info("Pripomienky inštalácií: odoslaných %s e-mailov", sent)
    return sent


def send_daily_reports():
    """Denný súhrn: používateľ dostane svoje otvorené projekty, admin prehľad všetkých."""
    sent = 0
    users = query("SELECT * FROM users WHERE active = 1 AND daily_report = 1")
    for user in users:
        if user["is_admin"]:
            projects = list_projects(include_done=False)
            subject = f"Denný prehľad projektov – {date.today():%d.%m.%Y}"
        else:
            projects = list_projects(assignee_id=user["id"], include_done=False)
            subject = f"Vaše projekty – stav k {date.today():%d.%m.%Y}"
        tasks = list_tasks(assignee_id=user["id"], include_done=False)
        installs = list_installations(user_id=None if user["is_admin"] else user["id"], days_back=0, days_ahead=7)
        if not projects and not tasks and not installs:
            continue
        ok = send_mail(
            user["email"], subject, "email/daily.html",
            user=user, projects=projects, stats=project_stats(projects), tasks=tasks, installs=installs,
        )
        sent += int(ok)
    log.info("Denný report: odoslaných %s e-mailov", sent)
    return sent
