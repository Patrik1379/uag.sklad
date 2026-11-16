"""Príkazy pre príkazový riadok:  flask --app run <príkaz>"""
import getpass
from datetime import date, timedelta

import click
from werkzeug.security import generate_password_hash

from .db import execute, query
from .models import recalc_progress, set_installers, set_members


def register(app):
    @app.cli.command("create-admin")
    @click.option("--name", prompt="Meno")
    @click.option("--email", prompt="E-mail")
    def create_admin(name, email):
        """Vytvorí administrátora."""
        password = getpass.getpass("Heslo (min. 8 znakov): ")
        if len(password) < 8:
            raise click.ClickException("Heslo musí mať aspoň 8 znakov.")
        if query("SELECT 1 FROM users WHERE email = ?", (email,), one=True):
            execute("UPDATE users SET is_admin=1, active=1, password_hash=? WHERE email=?",
                    (generate_password_hash(password), email))
            click.echo("Existujúci používateľ bol povýšený na admina a heslo zmenené.")
        else:
            execute("INSERT INTO users(name, email, password_hash, is_admin) VALUES (?,?,?,1)",
                    (name, email.lower(), generate_password_hash(password)))
            click.echo(f"Admin {email} vytvorený.")

    @app.cli.command("send-daily-report")
    def send_daily_report():
        """Okamžite odošle denný report (vhodné aj pre cron / Plánovač úloh)."""
        from .mailer import send_daily_reports
        click.echo(f"Odoslaných e-mailov: {send_daily_reports()}")

    @app.cli.command("send-install-reminders")
    def send_install_reminders_cmd():
        """Pošle pripomienky inštalácií na zajtra (na piatok aj víkend + pondelok)."""
        from .mailer import send_install_reminders
        click.echo(f"Odoslaných pripomienok: {send_install_reminders(app.config['DAILY_REPORT_SKIP_WEEKEND'])}")

    @app.cli.command("test-mail")
    @click.argument("to")
    def test_mail(to):
        """Pošle testovací e-mail – overenie SMTP nastavení."""
        from .mailer import send_mail
        ok = send_mail(to, "Test – Prehľad projektov", "email/test.html")
        click.echo("OK – e-mail odoslaný." if ok else "Nepodarilo sa – pozri log vyššie (alebo MAIL_ENABLED=false).")

    @app.cli.command("demo-data")
    def demo_data():
        """Naplní databázu ukážkovými dátami (admin@demo.sk / demo1234)."""
        h = generate_password_hash("demo1234")
        users = [("Admin Demo", "admin@demo.sk", 1), ("Jana Nováková", "jana@demo.sk", 0),
                 ("Peter Horváth", "peter@demo.sk", 0), ("Lucia Kováčová", "lucia@demo.sk", 0)]
        ids = {}
        for name, email, admin in users:
            row = query("SELECT id FROM users WHERE email=?", (email,), one=True)
            if not row:
                execute("INSERT INTO users(name, email, password_hash, is_admin) VALUES (?,?,?,?)",
                        (name, email, h, admin))
                row = query("SELECT id FROM users WHERE email=?", (email,), one=True)
            ids[email] = row["id"]
        t = date.today()
        projects = [
            ("Nový web pre klienta", "Redizajn a migrácia obsahu.", "prebieha", 60, -40, 20, ["jana@demo.sk", "peter@demo.sk"]),
            ("Ročná uzávierka", "Podklady pre účtovníctvo.", "prebieha", 30, -20, 5, ["peter@demo.sk"]),
            ("Výberové konanie – IT", "Dodávateľ serverov.", "novy", 0, 3, 45, ["lucia@demo.sk", "peter@demo.sk", "jana@demo.sk"]),
            ("Migrácia e-mailov", "Presun na Microsoft 365.", "prebieha", 70, -60, -4, ["peter@demo.sk"]),
            ("Školenie BOZP", "", "pozastaveny", 20, -10, 30, ["jana@demo.sk"]),
            ("Inventúra skladu", "", "hotovy", 100, -50, -15, ["lucia@demo.sk"]),
            ("Marketingová kampaň Q1", "Plán kampane na budúci kvartál.", "novy", 5, 10, 80, ["jana@demo.sk", "lucia@demo.sk"]),
        ]
        for name, desc, status, prog, s, d, members in projects:
            if query("SELECT 1 FROM projects WHERE name=?", (name,), one=True):
                continue
            cur = execute("INSERT INTO projects(name, description, status, progress, start_date, deadline, "
                          "created_by_id) VALUES (?,?,?,?,?,?,?)",
                          (name, desc, status, prog, (t + timedelta(days=s)).isoformat(),
                           (t + timedelta(days=d)).isoformat(), ids["admin@demo.sk"]))
            set_members(cur.lastrowid, [ids[m] for m in members])
            execute("INSERT INTO project_log(project_id, user_id, message) VALUES (?,?,?)",
                    (cur.lastrowid, ids["admin@demo.sk"], "Projekt založený."))
        if not query("SELECT 1 FROM tasks LIMIT 1", one=True):
            pid = {r["name"]: r["id"] for r in query("SELECT id, name FROM projects")}
            tasks = [
                ("Nový web pre klienta", "Pripraviť wireframy", "jana@demo.sk", -5, "hotova"),
                ("Nový web pre klienta", "Migrácia obsahu zo starého webu", "peter@demo.sk", 7, "prebieha"),
                ("Nový web pre klienta", "Odsúhlasiť dizajn s klientom", "jana@demo.sk", 2, "otvorena"),
                ("Ročná uzávierka", "Skontrolovať faktúry za december", "peter@demo.sk", -1, "otvorena"),
                ("Výberové konanie – IT", "Pripraviť zadávaciu dokumentáciu", "lucia@demo.sk", 10, "otvorena"),
                ("Výberové konanie – IT", "Prieskum trhu – 3 ponuky", "jana@demo.sk", 14, "otvorena"),
                ("Marketingová kampaň Q1", "Návrh rozpočtu kampane", "lucia@demo.sk", None, "otvorena"),
            ]
            for proj, title, who, due, status in tasks:
                if proj in pid:
                    execute("INSERT INTO tasks(project_id, title, assignee_id, due_date, status, created_by_id) "
                            "VALUES (?,?,?,?,?,?)",
                            (pid[proj], title, ids[who], (t + timedelta(days=due)).isoformat() if due is not None else None,
                             status, ids["admin@demo.sk"]))
        if not query("SELECT 1 FROM task_comments LIMIT 1", one=True):
            task = query("SELECT id FROM tasks WHERE title = 'Odsúhlasiť dizajn s klientom'", one=True)
            if task:
                execute("INSERT INTO task_comments(task_id, user_id, body) VALUES (?,?,?)",
                        (task["id"], ids["peter@demo.sk"], "Klient má čas vo štvrtok poobede, stačí 30 minút."))
                execute("INSERT INTO task_comments(task_id, user_id, body) VALUES (?,?,?)",
                        (task["id"], ids["jana@demo.sk"], "Super, pošlem pozvánku."))
            web = query("SELECT id FROM projects WHERE name = 'Nový web pre klienta'", one=True)
            if web:
                execute("UPDATE projects SET auto_progress = 1 WHERE id = ?", (web["id"],))
                recalc_progress(web["id"])
        if query("SELECT COUNT(*) FROM tasks WHERE weight <> 2", one=True)[0] == 0:
            execute("UPDATE tasks SET weight = 3 WHERE title = 'Migrácia obsahu zo starého webu'")
            execute("UPDATE tasks SET weight = 1 WHERE title = 'Pripraviť wireframy'")
        if not query("SELECT 1 FROM subtasks LIMIT 1", one=True):
            demo_subs = {
                "Migrácia obsahu zo starého webu": [("Export článkov", 1), ("Presun obrázkov", 1), ("Kontrola odkazov", 0),
                                                    ("Presmerovania starých URL", 0)],
                "Odsúhlasiť dizajn s klientom": [("Pripraviť prezentáciu", 1), ("Stretnutie s klientom", 0),
                                                 ("Zapracovať pripomienky", 0)],
                "Pripraviť zadávaciu dokumentáciu": [("Technická špecifikácia", 0), ("Kritériá hodnotenia", 0)],
            }
            for title, subs in demo_subs.items():
                task = query("SELECT id, project_id FROM tasks WHERE title = ?", (title,), one=True)
                if not task:
                    continue
                for st, done in subs:
                    execute("INSERT INTO subtasks(task_id, title, done, done_at, done_by_id, created_by_id) VALUES (?,?,?,?,?,?)",
                            (task["id"], st, done, t.isoformat() if done else None, ids["jana@demo.sk"] if done else None,
                             ids["admin@demo.sk"]))
                if any(d for _, d in subs):
                    execute("UPDATE tasks SET status = 'prebieha' WHERE id = ? AND status = 'otvorena'", (task["id"],))
                recalc_progress(task["project_id"])
        if not query("SELECT 1 FROM projects WHERE install_date IS NOT NULL LIMIT 1", one=True):
            demo_installs = [
                ("Nový web pre klienta", 1, "09:00", "11:00", "Kancelária klienta, Hlavná 12, Bratislava", ["peter@demo.sk"],
                 "Nasadenie webu na produkčný server, zaškolenie redaktorov."),
                ("Výberové konanie – IT", 16, "08:00", "15:00", "Serverovňa, Priemyselná 5, Trnava",
                 ["lucia@demo.sk", "peter@demo.sk"], "Montáž serverov do racku, kabeláž."),
                ("Migrácia e-mailov", 4, "17:00", None, "Online (Teams)", ["peter@demo.sk"], ""),
            ]
            for name, days, t1, t2, place, techs, note in demo_installs:
                row = query("SELECT id FROM projects WHERE name = ?", (name,), one=True)
                if row:
                    execute("UPDATE projects SET install_date=?, install_time_from=?, install_time_to=?, install_place=?, "
                            "install_note=? WHERE id=?",
                            ((t + timedelta(days=days)).isoformat(), t1, t2, place, note, row["id"]))
                    set_installers(row["id"], [ids[e] for e in techs])
        click.echo("Demo dáta pripravené. Prihlásenie: admin@demo.sk / demo1234 (ostatní tiež demo1234)")
