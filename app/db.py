"""Tenká databázová vrstva – PostgreSQL (produkcia) alebo SQLite (vývoj/testy).

SQL sa píše s placeholdermi `?`, pre PostgreSQL sa automaticky prevedú na `%s`.
"""
import os
import sqlite3
import time

from flask import g

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///data/sklad.db")


def is_pg() -> bool:
    return DATABASE_URL.startswith("postgres")


def _connect():
    if is_pg():
        import psycopg
        from psycopg.rows import dict_row

        return psycopg.connect(DATABASE_URL, row_factory=dict_row)
    path = DATABASE_URL.replace("sqlite:///", "", 1)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path, detect_types=0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


class DB:
    def __init__(self, conn):
        self.conn = conn

    def _sql(self, sql):
        return sql.replace("?", "%s") if is_pg() else sql

    def query(self, sql, params=(), one=False):
        cur = self.conn.cursor()
        cur.execute(self._sql(sql), tuple(params))
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        if one:
            return rows[0] if rows else None
        return rows

    def scalar(self, sql, params=()):
        row = self.query(sql, params, one=True)
        if not row:
            return None
        return list(row.values())[0]

    def execute(self, sql, params=()):
        """Vykoná príkaz; ak obsahuje RETURNING, vráti prvý stĺpec prvého riadku."""
        cur = self.conn.cursor()
        cur.execute(self._sql(sql), tuple(params))
        result = None
        if "RETURNING" in sql.upper():
            row = cur.fetchone()
            if row is not None:
                result = list(dict(row).values())[0]
        cur.close()
        return result

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        self.conn.close()


def get_db() -> DB:
    if "db" not in g:
        g.db = DB(_connect())
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        try:
            db.rollback()
        except Exception:
            pass
        db.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id {PK},
    username VARCHAR(80) NOT NULL UNIQUE,
    full_name VARCHAR(160),
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(20) NOT NULL DEFAULT 'technik',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS projects (
    id {PK},
    name VARCHAR(200) NOT NULL,
    order_no VARCHAR(100) NOT NULL,
    customer VARCHAR(200),
    deadline DATE,
    note TEXT,
    status VARCHAR(20) NOT NULL DEFAULT 'aktivny',
    image VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS parts (
    id {PK},
    code VARCHAR(100) NOT NULL UNIQUE,
    order_code VARCHAR(150),
    name VARCHAR(255) NOT NULL,
    manufacturer VARCHAR(150),
    supplier VARCHAR(150),
    category VARCHAR(100),
    unit VARCHAR(20) NOT NULL DEFAULT 'ks',
    location VARCHAR(100),
    rack_id INTEGER,
    shelf VARCHAR(50),
    params TEXT,
    note TEXT,
    image VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_parts_order_code ON parts (order_code);
CREATE TABLE IF NOT EXISTS racks (
    id {PK},
    code VARCHAR(30) NOT NULL UNIQUE,
    name VARCHAR(150),
    project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    note TEXT
);
CREATE TABLE IF NOT EXISTS project_parts (
    id {PK},
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    part_id INTEGER NOT NULL REFERENCES parts(id) ON DELETE CASCADE,
    qty_required INTEGER NOT NULL DEFAULT 1,
    note TEXT,
    UNIQUE (project_id, part_id)
);
CREATE TABLE IF NOT EXISTS receipts (
    id {PK},
    part_id INTEGER NOT NULL REFERENCES parts(id) ON DELETE CASCADE,
    project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    qty INTEGER NOT NULL,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    delivery_note VARCHAR(150),
    note TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_receipts_part ON receipts (part_id, project_id);
"""


def _columns(db, table):
    if is_pg():
        return {r["column_name"] for r in db.query(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ?", (table,))}
    return {r["name"] for r in db.query(f"PRAGMA table_info({table})")}


def _add_column(db, table, column, coltype):
    """Doplní stĺpec do existujúcej tabuľky. Vracia True, ak bol pridaný."""
    if column in _columns(db, table):
        return False
    db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
    return True


def _migrate_locations(db):
    """Jednorazovo prevedie textové umiestnenie typu 'R1-P3' na regál R1 + policu P3."""
    import re

    pattern = re.compile(r"^\s*([A-Za-z]{1,3}\d{1,3})\s*[-/ ,]\s*(\S.{0,40})$")
    for part in db.query("SELECT id, location FROM parts WHERE location IS NOT NULL AND rack_id IS NULL"):
        m = pattern.match(part["location"] or "")
        if not m:
            continue
        code, shelf = m.group(1).upper(), m.group(2).strip()
        shelf = re.sub(r"^[Pp]\.?\s*(\d+)$", r"\1", shelf)  # P3 -> 3
        rack = db.query("SELECT id FROM racks WHERE code = ?", (code,), one=True)
        rack_id = rack["id"] if rack else db.execute(
            "INSERT INTO racks (code) VALUES (?) RETURNING id", (code,))
        db.execute("UPDATE parts SET rack_id = ?, shelf = ?, location = NULL WHERE id = ?",
                   (rack_id, shelf, part["id"]))


def init_db(retries: int = 30):
    """Vytvorí tabuľky a predvoleného admina. Čaká, kým databáza nebude dostupná."""
    from werkzeug.security import generate_password_hash

    last = None
    for _ in range(retries):
        try:
            conn = _connect()
            break
        except Exception as exc:  # databáza ešte štartuje
            last = exc
            print("Čakám na databázu…", exc)
            time.sleep(2)
    else:
        raise RuntimeError(f"Databáza nie je dostupná: {last}")

    db = DB(conn)
    pk = "SERIAL PRIMARY KEY" if is_pg() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    for stmt in SCHEMA.format(PK=pk).split(";"):
        if stmt.strip():
            db.execute(stmt)
    db.commit()

    # --- migrácie existujúcich databáz (doplnenie nových stĺpcov) ---
    _add_column(db, "projects", "image", "VARCHAR(255)")
    _add_column(db, "parts", "shelf", "VARCHAR(50)")
    if _add_column(db, "parts", "rack_id", "INTEGER"):
        _migrate_locations(db)
    db.commit()

    if not db.scalar("SELECT COUNT(*) FROM users"):
        username = os.environ.get("ADMIN_USER", "admin")
        password = os.environ.get("ADMIN_PASSWORD", "admin")
        db.execute(
            "INSERT INTO users (username, full_name, password_hash, role) VALUES (?, ?, ?, 'admin')",
            (username, "Administrátor", generate_password_hash(password)),
        )
        db.commit()
        print(f"Vytvorený používateľ '{username}' (admin).")
    db.close()
