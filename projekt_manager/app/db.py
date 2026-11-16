"""Práca s SQLite databázou (bez ďalších knižníc)."""
import sqlite3

from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    active        INTEGER NOT NULL DEFAULT 1,
    daily_report  INTEGER NOT NULL DEFAULT 1,
    font_scale    INTEGER NOT NULL DEFAULT 100,
    created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS projects (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'novy',
    progress      INTEGER NOT NULL DEFAULT 0,
    start_date    TEXT NOT NULL,
    deadline      TEXT NOT NULL,
    auto_progress INTEGER NOT NULL DEFAULT 0,   -- 1 = plnenie sa počíta z úloh
    -- plánovaná inštalácia (jedna na projekt)
    install_date      TEXT,
    install_time_from TEXT,
    install_time_to   TEXT,
    install_place     TEXT NOT NULL DEFAULT '',
    install_status    TEXT NOT NULL DEFAULT 'planovana',
    install_note      TEXT NOT NULL DEFAULT '',
    install_reminded  TEXT,                       -- dátum inštalácie, ku ktorému už odišla pripomienka
    created_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- riešitelia projektu (projekt môže mať viac riešiteľov)
CREATE TABLE IF NOT EXISTS project_members (
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    added_at   TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    PRIMARY KEY (project_id, user_id)
);

-- úlohy v rámci projektu, každá má jedného riešiteľa
CREATE TABLE IF NOT EXISTS tasks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id    INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title         TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    assignee_id   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    due_date      TEXT,
    status        TEXT NOT NULL DEFAULT 'otvorena',
    weight        INTEGER NOT NULL DEFAULT 2,      -- dôležitosť: 1 nízka, 2 normálna, 3 vysoká, 5 kritická
    created_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    completed_at  TEXT
);

-- technici idúci na inštaláciu (podmnožina riešiteľov projektu)
CREATE TABLE IF NOT EXISTS project_installers (
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    PRIMARY KEY (project_id, user_id)
);

-- podúlohy (checklist v rámci úlohy)
CREATE TABLE IF NOT EXISTS subtasks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id       INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    title         TEXT NOT NULL,
    done          INTEGER NOT NULL DEFAULT 0,
    done_at       TEXT,
    done_by_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS task_comments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    user_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    body       TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS documents (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id     INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    filename       TEXT NOT NULL,
    stored_name    TEXT NOT NULL,
    size           INTEGER NOT NULL DEFAULT 0,
    uploaded_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    uploaded_at    TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS project_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    message    TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_members_user ON project_members(user_id);
CREATE INDEX IF NOT EXISTS ix_tasks_project ON tasks(project_id);
CREATE INDEX IF NOT EXISTS ix_tasks_assignee ON tasks(assignee_id);
CREATE INDEX IF NOT EXISTS ix_subtasks_task ON subtasks(task_id);
CREATE INDEX IF NOT EXISTS ix_comments_task ON task_comments(task_id);
CREATE INDEX IF NOT EXISTS ix_documents_project ON documents(project_id);
CREATE INDEX IF NOT EXISTS ix_log_project ON project_log(project_id);
"""


def connect(path):
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def get_db():
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def _migrate(conn):
    """Prevod zo staršej verzie (jeden riešiteľ v stĺpci projects.assignee_id)."""
    user_cols = [r[1] for r in conn.execute("PRAGMA table_info(users)")]
    if "font_scale" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN font_scale INTEGER NOT NULL DEFAULT 100")
    cols = [r[1] for r in conn.execute("PRAGMA table_info(projects)")]
    if "auto_progress" not in cols:
        conn.execute("ALTER TABLE projects ADD COLUMN auto_progress INTEGER NOT NULL DEFAULT 0")
    task_cols = [r[1] for r in conn.execute("PRAGMA table_info(tasks)")]
    if task_cols and "weight" not in task_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN weight INTEGER NOT NULL DEFAULT 2")
    for col, ddl in (("install_date", "TEXT"), ("install_time_from", "TEXT"), ("install_time_to", "TEXT"),
                     ("install_place", "TEXT NOT NULL DEFAULT ''"),
                     ("install_status", "TEXT NOT NULL DEFAULT 'planovana'"),
                     ("install_note", "TEXT NOT NULL DEFAULT ''"), ("install_reminded", "TEXT")):
        if col not in cols:
            conn.execute(f"ALTER TABLE projects ADD COLUMN {col} {ddl}")
    for col, ddl in (("order_no", "TEXT NOT NULL DEFAULT ''"), ("customer", "TEXT NOT NULL DEFAULT ''"),
                     ("sklad_id", "INTEGER"), ("sklad_status", "TEXT")):
        if col not in cols:
            conn.execute(f"ALTER TABLE projects ADD COLUMN {col} {ddl}")
    if "assignee_id" in cols:
        conn.execute("INSERT OR IGNORE INTO project_members(project_id, user_id) "
                     "SELECT id, assignee_id FROM projects WHERE assignee_id IS NOT NULL")
        conn.execute("UPDATE projects SET assignee_id = NULL")


def init_db(app):
    conn = connect(app.config["DATABASE"])
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()
    finally:
        conn.close()
    app.teardown_appcontext(close_db)


def query(sql, args=(), one=False):
    cur = get_db().execute(sql, args)
    rows = cur.fetchall()
    cur.close()
    return (rows[0] if rows else None) if one else rows


def execute(sql, args=()):
    db = get_db()
    cur = db.execute(sql, args)
    db.commit()
    return cur
