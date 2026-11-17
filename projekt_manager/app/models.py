"""Doménová logika: projekty, stavy, časová os."""
from datetime import date, datetime, timedelta

from flask import current_app

from .db import query

STATUSES = {
    "novy": "Nový",
    "prebieha": "Prebieha",
    "pozastaveny": "Pozastavený",
    "hotovy": "Hotový",
}

WEEKDAYS_SK = ["po", "ut", "st", "št", "pi", "so", "ne"]


def sk_days(n):
    """Správny tvar slova deň: 1 deň, 2 dni, 5 dní."""
    n = abs(n)
    return "deň" if n == 1 else "dni" if 2 <= n <= 4 else "dní"


INSTALL_STATUSES = {
    "planovana": "Naplánovaná",
    "presunuta": "Presunutá",
    "hotova": "Hotová",
}

MONTHS_SK = ["jan", "feb", "mar", "apr", "máj", "jún", "júl", "aug", "sep", "okt", "nov", "dec"]

PROJECT_SELECT = """
SELECT p.*,
       c.name  AS creator_name,   c.email AS creator_email,
       (SELECT GROUP_CONCAT(u.id || char(31) || u.name || char(31) || u.email, char(30))
          FROM project_members m JOIN users u ON u.id = m.user_id
         WHERE m.project_id = p.id) AS members_raw,
       (SELECT GROUP_CONCAT(u.id || char(31) || u.name || char(31) || u.email, char(30))
          FROM project_installers i JOIN users u ON u.id = i.user_id
         WHERE i.project_id = p.id) AS installers_raw,
       (SELECT COUNT(*) FROM documents d WHERE d.project_id = p.id) AS doc_count,
       (SELECT COUNT(*) FROM tasks t WHERE t.project_id = p.id) AS task_count,
       (SELECT COUNT(*) FROM tasks t WHERE t.project_id = p.id AND t.status = 'hotova') AS task_done
FROM projects p
LEFT JOIN users c ON c.id = p.created_by_id
"""


def _parse_people(raw):
    people = []
    for item in (raw or "").split("\x1e"):
        if item:
            uid, name, email = item.split("\x1f", 2)
            people.append({"id": int(uid), "name": name, "email": email})
    people.sort(key=lambda m: m["name"])
    return people


def parse_date(value):
    if not value:
        return None
    if isinstance(value, date):
        return value
    return datetime.strptime(value[:10], "%Y-%m-%d").date()


class Project:
    """Obal nad riadkom z DB s vypočítanými vlastnosťami."""

    def __init__(self, row):
        self.__dict__.update(dict(row))
        self.start_date = parse_date(self.start_date)
        self.deadline = parse_date(self.deadline)
        self.install_date = parse_date(self.install_date)
        self.members = _parse_people(self.members_raw)
        self.installers = _parse_people(self.installers_raw)

    # ---- plánovaná inštalácia
    @property
    def has_install(self):
        return self.install_date is not None

    @property
    def install_open(self):
        """Inštalácia, ktorá sa ešte len má uskutočniť."""
        return self.has_install and self.install_status != "hotova"

    @property
    def install_status_label(self):
        return INSTALL_STATUSES.get(self.install_status, self.install_status)

    @property
    def install_days_left(self):
        return (self.install_date - date.today()).days if self.install_date else None

    @property
    def install_time_text(self):
        f, t = self.install_time_from or "", self.install_time_to or ""
        return f"{f} – {t}" if f and t else f or (f"do {t}" if t else "")

    @property
    def installer_ids(self):
        return [m["id"] for m in self.installers]

    @property
    def installers_text(self):
        return ", ".join(m["name"] for m in self.installers)

    @property
    def install_when_text(self):
        """Napr. „zajtra“, „o 5 dní“, „pred 2 dňami“."""
        if not self.install_date:
            return ""
        d = self.install_days_left
        if d == 0:
            return "dnes"
        if d == 1:
            return "zajtra"
        if d < 0:
            return f"pred {abs(d)} {'dňom' if d == -1 else 'dňami'}"
        return f"o {d} {sk_days(d)}"

    @property
    def install_health(self):
        """Farba značky: install (naplánovaná), risk (dnes/zajtra), overdue (prešla a nie je hotová), done, paused (presunutá)."""
        if not self.has_install:
            return None
        if self.install_status == "hotova":
            return "done"
        if self.install_status == "presunuta":
            return "paused"
        d = self.install_days_left
        if d < 0:
            return "overdue"
        if d <= 1:
            return "risk"
        return "install"

    @property
    def member_ids(self):
        return [m["id"] for m in self.members]

    @property
    def members_text(self):
        return ", ".join(m["name"] for m in self.members)

    @property
    def status_label(self):
        return STATUSES.get(self.status, self.status)

    @property
    def is_done(self):
        return self.status == "hotovy"

    @property
    def days_left(self):
        return (self.deadline - date.today()).days

    @property
    def is_overdue(self):
        return not self.is_done and self.days_left < 0

    @property
    def expected_progress(self):
        """Koľko % by malo byť hotové podľa uplynutého času."""
        total = (self.deadline - self.start_date).days or 1
        elapsed = (date.today() - self.start_date).days
        return max(0, min(100, round(elapsed / total * 100)))

    @property
    def health(self):
        """(kód, popis) – použije sa na farby v dashboarde a v e-mailoch."""
        if self.is_done:
            return "done", "Hotový"
        if self.is_overdue:
            return "overdue", f"Po termíne {abs(self.days_left)} d"
        soon = current_app.config.get("DUE_SOON_DAYS", 7)
        if self.status == "pozastaveny":
            return "paused", "Pozastavený"
        margin = current_app.config.get("RISK_MARGIN", 20)
        behind = self.progress + margin < self.expected_progress
        if self.days_left <= soon or behind:
            return "risk", "Ohrozený" if behind else f"Termín o {self.days_left} d"
        if not getattr(self, "task_count", 0):
            return "paused", "Bez úloh"           # bez úloh sa nedá posúdiť, či je projekt v poriadku
        return "ok", "Podľa plánu"

    @property
    def deadline_text(self):
        if self.is_done:
            return "dokončený"
        d = self.days_left
        if d < 0:
            return f"{abs(d)} {sk_days(d)} po termíne"
        if d == 0:
            return "dnes"
        if d == 1:
            return "zajtra"
        return f"o {d} {sk_days(d)}"

    @property
    def countdown(self):
        """(veľké číslo/text, popis) pre kartu termínu."""
        d = self.days_left
        if d < 0:
            return str(abs(d)), f"{sk_days(d)} po termíne"
        if d == 0:
            return "Dnes", "je deadline"
        if d == 1:
            return "Zajtra", "je deadline"
        return str(d), f"{sk_days(d)} do termínu"

    @property
    def countdown_short(self):
        if self.is_done:
            return "hotový"
        d = self.days_left
        return "dnes" if d == 0 else f"−{abs(d)} d" if d < 0 else f"{d} d"


def get_project(project_id):
    row = query(PROJECT_SELECT + " WHERE p.id = ?", (project_id,), one=True)
    return Project(row) if row else None


def list_projects(assignee_id=None, include_done=True, status=None):
    sql, args, where = PROJECT_SELECT, [], []
    if assignee_id:
        where.append("EXISTS (SELECT 1 FROM project_members m WHERE m.project_id = p.id AND m.user_id = ?)")
        args.append(assignee_id)
    if not include_done:
        where.append("p.status <> 'hotovy'")
    if status:
        where.append("p.status = ?")
        args.append(status)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY (p.status = 'hotovy'), p.deadline, p.name"
    return [Project(r) for r in query(sql, args)]


def set_members(project_id, user_ids):
    """Uloží riešiteľov projektu. Vráti (pridaní, odobratí) ako množiny id."""
    from .db import execute
    current = {r["user_id"] for r in query("SELECT user_id FROM project_members WHERE project_id = ?", (project_id,))}
    wanted = set(user_ids)
    for uid in wanted - current:
        execute("INSERT OR IGNORE INTO project_members(project_id, user_id) VALUES (?, ?)", (project_id, uid))
    for uid in current - wanted:
        execute("DELETE FROM project_members WHERE project_id = ? AND user_id = ?", (project_id, uid))
    return wanted - current, current - wanted


def set_installers(project_id, user_ids):
    """Uloží technikov inštalácie. Vráti množinu novo pridaných."""
    from .db import execute
    current = {r["user_id"] for r in query("SELECT user_id FROM project_installers WHERE project_id = ?", (project_id,))}
    wanted = set(user_ids)
    for uid in wanted - current:
        execute("INSERT OR IGNORE INTO project_installers(project_id, user_id) VALUES (?, ?)", (project_id, uid))
    for uid in current - wanted:
        execute("DELETE FROM project_installers WHERE project_id = ? AND user_id = ?", (project_id, uid))
    return wanted - current


def list_installations(user_id=None, days_back=1, days_ahead=None, include_done=False):
    """Projekty s plánovanou inštaláciou zoradené podľa dátumu.
    user_id – len projekty, kde je používateľ technikom alebo riešiteľom."""
    sql = PROJECT_SELECT + " WHERE p.install_date IS NOT NULL AND p.install_date >= ?"
    args = [(date.today() - timedelta(days=days_back)).isoformat()]
    if days_ahead is not None:
        sql += " AND p.install_date <= ?"
        args.append((date.today() + timedelta(days=days_ahead)).isoformat())
    if not include_done:
        sql += " AND p.install_status <> 'hotova'"
    if user_id:
        sql += (" AND (EXISTS (SELECT 1 FROM project_installers i WHERE i.project_id = p.id AND i.user_id = ?)"
                " OR EXISTS (SELECT 1 FROM project_members m WHERE m.project_id = p.id AND m.user_id = ?))")
        args += [user_id, user_id]
    sql += " ORDER BY p.install_date, p.install_time_from"
    return [Project(r) for r in query(sql, args)]


def project_stats(projects):
    soon = current_app.config.get("DUE_SOON_DAYS", 7)
    open_ = [p for p in projects if not p.is_done]
    return {
        "open": len(open_),
        "overdue": sum(1 for p in open_ if p.is_overdue),
        "due_soon": sum(1 for p in open_ if 0 <= p.days_left <= soon),
        "done": len(projects) - len(open_),
        "avg_progress": round(sum(p.progress for p in open_) / len(open_)) if open_ else 0,
    }


def build_timeline(projects, tasks=()):
    """Pripraví dáta pre Ganttovu os (pozície v % šírky). tasks = úlohy s termínom (bodky na pruhoch)."""
    if not projects:
        return None
    today = date.today()
    ids = {p.id for p in projects}
    tasks = [t for t in tasks if t.due_date and t.project_id in ids]
    installs = [p.install_date for p in projects if p.install_date]
    lo = min([p.start_date for p in projects] + [t.due_date for t in tasks] + installs + [today]) - timedelta(days=3)
    hi = max([p.deadline for p in projects] + [t.due_date for t in tasks] + installs + [today]) + timedelta(days=7)
    total = (hi - lo).days or 1

    def pct(d):
        return round((d - lo).days / total * 100, 3)

    half_day = 50 / total
    by_project = {}
    for t in tasks:
        by_project.setdefault(t.project_id, []).append({"t": t, "left": round(pct(t.due_date) + half_day, 3)})

    rows = []
    for p in projects:
        left = pct(p.start_date)
        width = max(pct(p.deadline + timedelta(days=1)) - left, 0.8)
        end = left + width
        install = round(pct(p.install_date) + half_day, 3) if p.install_date else None
        # popis deadlinu dať doľava, ak by ho prekryla značka inštalácie alebo okraj osi
        flip = end > 82 or (install is not None and 0 <= install - end <= 10)
        rows.append({"p": p, "left": left, "width": width, "end": round(end, 3), "flip": flip,
                     "tasks": by_project.get(p.id, []), "install": install})

    # značky mesiacov
    months, d = [], date(lo.year, lo.month, 1)
    while d <= hi:
        months.append(d)
        d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    step = 1 if len(months) <= 12 else 2 if len(months) <= 24 else 3
    ticks = []
    for i, m in enumerate(months):
        if m < lo or i % step:
            continue
        label = MONTHS_SK[m.month - 1] + (f" {m.year}" if m.month == 1 or not ticks else "")
        ticks.append({"left": pct(m), "label": label})

    return {"rows": rows, "ticks": ticks, "today": pct(today)}


# ---------------------------------------------------------------------- úlohy

TASK_STATUSES = {
    "otvorena": "Na riešenie",
    "prebieha": "Rieši sa",
    "hotova": "Hotová",
}

# dôležitosť úlohy = jej váha pri výpočte % projektu
TASK_WEIGHTS = {1: "Nízka", 2: "Normálna", 3: "Vysoká", 5: "Kritická"}

TASK_SELECT = """
SELECT t.*, p.name AS project_name,
       u.name AS assignee_name, u.email AS assignee_email,
       c.name AS creator_name,  c.email AS creator_email,
       (SELECT COUNT(*) FROM task_comments k WHERE k.task_id = t.id) AS comment_count,
       (SELECT COUNT(*) FROM subtasks s WHERE s.task_id = t.id) AS sub_total,
       (SELECT COUNT(*) FROM subtasks s WHERE s.task_id = t.id AND s.done = 1) AS sub_done,
       (SELECT SUM(x.weight) FROM tasks x WHERE x.project_id = t.project_id) AS project_weight
FROM tasks t
JOIN projects p ON p.id = t.project_id
LEFT JOIN users u ON u.id = t.assignee_id
LEFT JOIN users c ON c.id = t.created_by_id
"""


class Task:
    def __init__(self, row):
        self.__dict__.update(dict(row))
        self.due_date = parse_date(self.due_date)

    @property
    def fraction(self):
        """Podiel hotovej práce na úlohe 0–1: hotová = 1, inak podiel hotových podúloh."""
        if self.status == "hotova":
            return 1.0
        if self.sub_total:
            return self.sub_done / self.sub_total
        return 0.0

    @property
    def percent(self):
        return round(self.fraction * 100)

    @property
    def weight_label(self):
        return TASK_WEIGHTS.get(self.weight, str(self.weight))

    @property
    def share(self):
        """Koľko % projektu táto úloha tvorí (podľa váhy)."""
        return round(self.weight / self.project_weight * 100) if self.project_weight else 0

    @property
    def status_label(self):
        return TASK_STATUSES.get(self.status, self.status)

    @property
    def is_done(self):
        return self.status == "hotova"

    @property
    def days_left(self):
        return (self.due_date - date.today()).days if self.due_date else None

    @property
    def is_overdue(self):
        return not self.is_done and self.due_date is not None and self.days_left < 0

    @property
    def health(self):
        if self.is_done:
            return "done", "Hotová"
        if self.due_date is None:
            return "paused", "Bez termínu"
        d = self.days_left
        if d < 0:
            return "overdue", f"Po termíne {abs(d)} d"
        if d == 0:
            return "risk", "Dnes"
        if d == 1:
            return "risk", "Zajtra"
        if d <= 3:
            return "risk", f"O {d} {sk_days(d)}"
        return "ok", f"O {d} {sk_days(d)}"


def get_task(task_id):
    row = query(TASK_SELECT + " WHERE t.id = ?", (task_id,), one=True)
    return Task(row) if row else None


def list_tasks(project_id=None, assignee_id=None, include_done=True):
    sql, args, where = TASK_SELECT, [], []
    if project_id:
        where.append("t.project_id = ?")
        args.append(project_id)
    if assignee_id:
        where.append("t.assignee_id = ?")
        args.append(assignee_id)
    if not include_done:
        where.append("t.status <> 'hotova'")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY (t.status = 'hotova'), (t.due_date IS NULL), t.due_date, t.id"
    return [Task(r) for r in query(sql, args)]


def recalc_progress(project_id):
    """Ak má projekt zapnuté automatické plnenie, prepočíta % z hotových úloh."""
    from .db import execute
    row = query("SELECT auto_progress, status FROM projects WHERE id = ?", (project_id,), one=True)
    if not row or not row["auto_progress"] or row["status"] == "hotovy":
        return
    execute("UPDATE projects SET progress = ? WHERE id = ?", (tasks_progress(project_id), project_id))


def tasks_progress(project_id):
    """% plnenia z úloh a podúloh – vážený priemer podľa dôležitosti úloh; úloha s podúlohami
    sa započíta pomerom hotových podúloh, hotová úloha celá. 0, ak projekt nemá úlohy."""
    rows = query(
        "SELECT t.status, t.weight, (SELECT COUNT(*) FROM subtasks s WHERE s.task_id = t.id) AS n, "
        "(SELECT COUNT(*) FROM subtasks s WHERE s.task_id = t.id AND s.done = 1) AS d "
        "FROM tasks t WHERE t.project_id = ?", (project_id,))
    weights = sum(max(r["weight"], 1) for r in rows)
    if not weights:
        return 0
    done = sum(max(r["weight"], 1) * (1.0 if r["status"] == "hotova" else (r["d"] / r["n"] if r["n"] else 0.0))
               for r in rows)
    return round(done / weights * 100)


def list_subtasks(project_id=None, task_ids=None):
    """Podúlohy zoskupené podľa task_id."""
    if project_id:
        rows = query("SELECT s.*, u.name AS done_by FROM subtasks s JOIN tasks t ON t.id = s.task_id "
                     "LEFT JOIN users u ON u.id = s.done_by_id WHERE t.project_id = ? ORDER BY s.id", (project_id,))
    else:
        rows = []
    out = {}
    for r in rows:
        out.setdefault(r["task_id"], []).append(r)
    return out


MONTHS_SK_FULL = ["január", "február", "marec", "apríl", "máj", "jún", "júl", "august",
                  "september", "október", "november", "december"]


def build_calendar(year, month, projects, tasks):
    """Mesačná mriežka (pondelok–nedeľa) s deadlinami projektov a termínmi úloh."""
    import calendar as _cal
    today = date.today()
    events = {}
    for p in projects:
        events.setdefault(p.deadline, []).append({"kind": "project", "obj": p})
    for t in tasks:
        if t.due_date:
            events.setdefault(t.due_date, []).append({"kind": "task", "obj": t})
    for p in projects:
        if p.install_date:
            events.setdefault(p.install_date, []).insert(0, {"kind": "install", "obj": p})
    weeks = []
    for week in _cal.Calendar(firstweekday=0).monthdatescalendar(year, month):
        weeks.append([{"date": d, "in_month": d.month == month, "today": d == today,
                       "weekend": d.weekday() >= 5, "events": events.get(d, [])} for d in week])
    agenda = [(d, ev) for d, ev in sorted(events.items()) if d.year == year and d.month == month]
    return {"weeks": weeks, "agenda": agenda, "title": f"{MONTHS_SK_FULL[month - 1]} {year}"}
