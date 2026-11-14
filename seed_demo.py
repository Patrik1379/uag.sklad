"""Naplní databázu ukážkovými dátami (projekty, diely, príjmy).

Použitie:  docker compose exec web python seed_demo.py
"""
import os
import random

from PIL import Image, ImageDraw
from werkzeug.security import generate_password_hash

from app import create_app
from app.db import get_db, init_db

PARTS = [
    ("6ES7214-1AG40-0XB0", "PLC S7-1200 CPU 1214C", "Siemens", "Elektro", (37, 99, 201)),
    ("6AV2123-2GB03-0AX0", "HMI panel KTP700 Basic", "Siemens", "Elektro", (31, 42, 55)),
    ("3RV2011-1JA10", "Motorový istič 7-10 A", "Siemens", "Elektro", (90, 150, 90)),
    ("LC1D18BD", "Stýkač TeSys D 18 A 24 V DC", "Schneider", "Elektro", (60, 160, 60)),
    ("SDBH-24-5", "Zdroj 24 V DC / 5 A", "Mean Well", "Elektro", (120, 120, 120)),
    ("ADN-25-40-A-P-A", "Kompaktný valec ADN 25x40", "Festo", "Pneumatika", (40, 120, 200)),
    ("VUVG-L14-M52-MT-G18-1R8L", "Elektromagnetický ventil 5/2", "Festo", "Pneumatika", (40, 140, 220)),
    ("QSM-M5-4", "Priama rýchlospojka M5 / 4 mm", "Festo", "Pneumatika", (70, 70, 180)),
    ("6205-2RSH", "Guľkové ložisko 6205-2RSH", "SKF", "Mechanika", (160, 160, 170)),
    ("UCF205", "Prírubové ložiskové teleso UCF205", "SKF", "Mechanika", (110, 110, 120)),
    ("IFM-IG5594", "Indukčný snímač M18", "ifm", "Snímače", (230, 140, 30)),
    ("O5D100", "Optický snímač vzdialenosti", "ifm", "Snímače", (240, 160, 40)),
    ("TB-2.5", "Svorka radová 2,5 mm²", "Phoenix Contact", "Elektro", (200, 200, 60)),
    ("M8x20-DIN912", "Skrutka imbus M8x20 DIN 912", "—", "Spojovací materiál", (150, 150, 150)),
]

PROJECTS = [
    ("Linka balenia L3", "Z-2026-041", "Agro Food s.r.o.", "2026-12-05",
     {0: 1, 1: 1, 2: 4, 3: 4, 4: 2, 6: 6, 7: 40, 10: 8, 12: 120}),
    ("Montážna stanica MS-12", "Z-2026-047", "AutoParts a.s.", "2027-01-20",
     {0: 1, 2: 2, 5: 6, 6: 6, 7: 24, 8: 4, 9: 4, 11: 2, 13: 80}),
    ("Retrofit lisu P-200", "Z-2026-052", "Kovo Trnava", "2027-02-28",
     {1: 1, 3: 2, 4: 1, 10: 3, 12: 60, 13: 30}),
]


START = {"Z-2026-041": "2026-09-15", "Z-2026-047": "2026-10-20", "Z-2026-052": "2026-11-10"}


def make_icon(path_big, path_small, color, label):
    img = Image.new("RGB", (400, 400), (248, 248, 245))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((70, 90, 330, 310), radius=30, fill=color)
    d.rectangle((110, 130, 290, 180), fill=tuple(min(255, c + 70) for c in color))
    for i in range(5):
        d.ellipse((105 + i * 40, 230, 130 + i * 40, 255), fill=(240, 240, 240))
    d.text((80, 40), label[:18], fill=(60, 60, 60))
    img.save(path_big, "JPEG", quality=85)
    img.thumbnail((128, 128))
    img.save(path_small, "JPEG", quality=80)


def main():
    init_db()
    app = create_app()
    random.seed(7)
    with app.app_context():
        db = get_db()
        if db.scalar("SELECT COUNT(*) FROM parts"):
            print("Databáza už obsahuje diely – ukážkové dáta sa nevkladajú.")
            return
        folder = app.config["UPLOAD_FOLDER"]
        if not db.scalar("SELECT COUNT(*) FROM users WHERE username = 'technik'"):
            db.execute("INSERT INTO users (username, full_name, password_hash, role) VALUES (?, ?, ?, 'technik')",
                       ("technik", "Ján Technik", generate_password_hash("technik")))
        uid = db.scalar("SELECT id FROM users WHERE username = 'technik'")
        sklad = db.execute("INSERT INTO locations (name, note) VALUES (?, ?) RETURNING id", ("Hlavný sklad", "hala A"))
        dielna = db.execute("INSERT INTO locations (name) VALUES (?) RETURNING id", ("Dielňa",))
        racks = {}
        for code, rname, loc in (("R1", "Elektro", sklad), ("R2", "Pneumatika a mechanika", sklad), ("R3", None, dielna)):
            racks[code] = db.execute(
                "INSERT INTO racks (code, name, location_id, shelf_count) VALUES (?, ?, ?, 5) RETURNING id",
                (code, rname, loc))
        part_ids = []
        for i, (oc, name, man, cat, color) in enumerate(PARTS):
            img = None
            if i % 4 != 3:
                img = f"demo_{i}.jpg"
                make_icon(os.path.join(folder, img), os.path.join(folder, "t_" + img), color, oc)
            pid = db.execute(
                """INSERT INTO parts (code, order_code, name, manufacturer, category, rack_id, shelf, image)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING id""",
                (f"D-{i + 1:05d}", oc, name, man, cat, racks[f"R{1 + i // 5}"], str(1 + i % 5), img))
            part_ids.append(pid)
        for name, order_no, cust, deadline, bom in PROJECTS:
            prj = db.execute(
                "INSERT INTO projects (name, order_no, customer, start_date, deadline) VALUES (?, ?, ?, ?, ?) RETURNING id",
                (name, order_no, cust, START.get(order_no), deadline))
            for idx, qty in bom.items():
                db.execute("INSERT INTO project_parts (project_id, part_id, qty_required) VALUES (?, ?, ?)",
                           (prj, part_ids[idx], qty))
                r = random.random()
                got = qty if r < 0.45 else (qty // 2 if r < 0.7 else 0)
                if got:
                    db.execute("INSERT INTO receipts (part_id, project_id, qty, user_id, delivery_note) VALUES (?, ?, ?, ?, ?)",
                               (part_ids[idx], prj, got, uid, f"DL-{random.randint(1000, 9999)}"))
        db.execute("INSERT INTO receipts (part_id, project_id, qty, user_id, note) VALUES (?, NULL, ?, ?, ?)",
                   (part_ids[7], 10, uid, "Nadbytok z objednávky"))
        # regál R3 je vyhradený pre prvý projekt – zobrazí sa pri ňom jeho názov
        db.execute("UPDATE racks SET project_id = (SELECT MIN(id) FROM projects) WHERE code = 'R3'")
        db.commit()
        print("Ukážkové dáta boli vložené. Technik: technik / technik")


if __name__ == "__main__":
    main()
