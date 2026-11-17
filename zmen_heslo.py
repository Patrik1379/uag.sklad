"""Nastavenie nového hesla používateľovi skladu (keď ho zabudne).

Spustenie (v priečinku aplikácie):
    docker compose cp zmen_heslo.py web:/tmp/zmen_heslo.py
    docker compose exec web python /tmp/zmen_heslo.py
"""
import getpass
import os
import sys

sys.path.insert(0, os.getcwd())          # priečinok aplikácie (/app v kontajneri)
sys.path.insert(0, "/app")

from werkzeug.security import generate_password_hash  # noqa: E402

from app import create_app  # noqa: E402
from app.db import get_db  # noqa: E402

app = create_app()
with app.app_context():
    db = get_db()
    users = db.query("SELECT username, full_name, email, role, active FROM users ORDER BY username")
    print("\nPoužívatelia:")
    for u in users:
        print(f"  {u['username']:<15} {u['full_name'] or '':<25} {u['email'] or '':<30} "
              f"{'admin' if u['role'] == 'admin' else 'technik'}{'' if u['active'] else '  (zablokovaný)'}")
    name = input("\nPrihlasovacie meno: ").strip()
    if not any(u["username"].lower() == name.lower() for u in users):
        sys.exit(f"Používateľ '{name}' neexistuje.")
    pw = getpass.getpass("Nové heslo (min. 4 znaky, pri písaní sa nezobrazuje): ")
    if len(pw) < 4:
        sys.exit("Heslo je príliš krátke.")
    if getpass.getpass("Zopakujte heslo: ") != pw:
        sys.exit("Heslá sa nezhodujú, nič sa nezmenilo.")
    db.execute("UPDATE users SET password_hash = ?, active = 1 WHERE LOWER(username) = LOWER(?)",
               (generate_password_hash(pw), name))
    db.commit()
    print(f"\nHotovo – používateľ {name} sa môže prihlásiť novým heslom.")
