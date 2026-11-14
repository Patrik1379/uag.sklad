"""Výpočty stavu naskladnenia, práca s obrázkami a import kusovníka."""
import csv
import io
import os
import uuid

from flask import current_app

from .db import get_db

LINES_SQL = """
SELECT pp.id AS pp_id, pp.project_id, pp.qty_required, pp.note AS pp_note,
       pa.id AS part_id, pa.code, pa.order_code, pa.name, pa.image, pa.unit,
       pa.manufacturer, pa.supplier, pa.location, pa.location_id, pa.rack_id, pa.shelf,
       COALESCE(r.qty, 0) AS received
FROM project_parts pp
JOIN parts pa ON pa.id = pp.part_id
LEFT JOIN (
    SELECT project_id, part_id, SUM(qty) AS qty
    FROM receipts WHERE project_id IS NOT NULL
    GROUP BY project_id, part_id
) r ON r.project_id = pp.project_id AND r.part_id = pp.part_id
"""


def enrich(line):
    req = int(line["qty_required"] or 0)
    rec = int(line["received"] or 0)
    line["received"] = rec
    line["covered"] = min(rec, req)
    line["missing"] = max(req - rec, 0)
    line["surplus"] = max(rec - req, 0)
    if req and rec >= req:
        line["state"] = "ok"
    elif rec > 0:
        line["state"] = "partial"
    else:
        line["state"] = "missing"
    return line


def project_lines(project_id):
    rows = get_db().query(LINES_SQL + " WHERE pp.project_id = ? ORDER BY pa.code", (project_id,))
    return [enrich(r) for r in rows]


def summarize(lines):
    req = sum(l["qty_required"] for l in lines)
    cov = sum(l["covered"] for l in lines)
    return {
        "n_items": len(lines),
        "items_ok": sum(1 for l in lines if l["state"] == "ok"),
        "items_partial": sum(1 for l in lines if l["state"] == "partial"),
        "items_missing": sum(1 for l in lines if l["state"] == "missing"),
        "required": req,
        "covered": cov,
        "missing": req - cov,
        "pct": round(100 * cov / req) if req else 0,
    }


def projects_with_stats(status=None):
    db = get_db()
    if status:
        projects = db.query("SELECT * FROM projects WHERE status = ? ORDER BY deadline IS NULL, deadline, id", (status,))
    else:
        projects = db.query("SELECT * FROM projects ORDER BY status, deadline IS NULL, deadline, id")
    all_lines = [enrich(r) for r in db.query(LINES_SQL)]
    by_project = {}
    for l in all_lines:
        by_project.setdefault(l["project_id"], []).append(l)
    for p in projects:
        p["stats"] = summarize(by_project.get(p["id"], []))
    return projects


def free_stock(part_id):
    return int(get_db().scalar(
        "SELECT COALESCE(SUM(qty), 0) FROM receipts WHERE part_id = ? AND project_id IS NULL", (part_id,)) or 0)


def free_stock_map():
    rows = get_db().query(
        "SELECT part_id, SUM(qty) AS qty FROM receipts WHERE project_id IS NULL GROUP BY part_id")
    return {r["part_id"]: int(r["qty"]) for r in rows if int(r["qty"] or 0) > 0}


def demand_for_part(part_id):
    """Riadky projektov, do ktorých je diel naplánovaný, zoradené podľa termínu."""
    db = get_db()
    rows = db.query(
        LINES_SQL + """ JOIN projects pr ON pr.id = pp.project_id
        WHERE pp.part_id = ? AND pr.status = 'aktivny'""", (part_id,))
    projects = {p["id"]: p for p in db.query("SELECT * FROM projects")}
    out = []
    for r in rows:
        r = enrich(r)
        r["project"] = projects[r["project_id"]]
        out.append(r)
    out.sort(key=lambda r: (r["project"]["deadline"] is None, str(r["project"]["deadline"] or ""), r["project_id"]))
    return out


# --- Obrázky -------------------------------------------------------------------

ALLOWED_IMG = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


def save_image(file_storage):
    """Uloží obrázok zmenšený (max 900 px) + miniatúru 128 px. Vracia názov súboru."""
    from PIL import Image, ImageOps

    ext = os.path.splitext(file_storage.filename or "")[1].lower()
    if ext not in ALLOWED_IMG:
        raise ValueError("Nepodporovaný formát obrázka.")
    img = Image.open(file_storage.stream)
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        img = bg
    else:
        img = img.convert("RGB")
    name = uuid.uuid4().hex + ".jpg"
    folder = current_app.config["UPLOAD_FOLDER"]
    big = img.copy()
    big.thumbnail((900, 900))
    big.save(os.path.join(folder, name), "JPEG", quality=85)
    thumb = img.copy()
    thumb.thumbnail((128, 128))
    thumb.save(os.path.join(folder, "t_" + name), "JPEG", quality=80)
    return name


def delete_image(name):
    if not name:
        return
    folder = current_app.config["UPLOAD_FOLDER"]
    for n in (name, "t_" + name):
        try:
            os.remove(os.path.join(folder, n))
        except OSError:
            pass


# --- Kódy dielov ---------------------------------------------------------------

def next_part_code():
    db = get_db()
    n = int(db.scalar("SELECT COALESCE(MAX(id), 0) FROM parts") or 0) + 1
    while True:
        code = f"D-{n:05d}"
        if not db.scalar("SELECT COUNT(*) FROM parts WHERE code = ?", (code,)):
            return code
        n += 1


# --- Import kusovníka (CSV / XLSX) ---------------------------------------------

HEADER_MAP = {
    "code": ["oznacenie", "označenie", "kod", "kód", "code", "id", "interne cislo", "interné číslo"],
    "order_code": ["objednavacie cislo", "objednávacie číslo", "obj. cislo", "obj. číslo", "obj cislo",
                   "order code", "order number", "part number", "pn", "objednavacie_cislo"],
    "name": ["nazov", "názov", "popis", "name", "description"],
    "manufacturer": ["vyrobca", "výrobca", "manufacturer"],
    "supplier": ["dodavatel", "dodávateľ", "supplier"],
    "qty": ["mnozstvo", "množstvo", "pocet", "počet", "ks", "qty", "quantity", "pocet ks", "počet ks"],
}


def _norm(h):
    return " ".join(str(h or "").strip().lower().replace("_", " ").split())


def read_bom(file_storage):
    filename = (file_storage.filename or "").lower()
    if filename.endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook

        wb = load_workbook(file_storage.stream, read_only=True, data_only=True)
        ws = wb.active
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
    else:
        raw = file_storage.stream.read()
        for enc in ("utf-8-sig", "cp1250", "latin-1"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        dialect = csv.Sniffer().sniff(text[:2048], delimiters=";,\t") if text.strip() else csv.excel
        rows = list(csv.reader(io.StringIO(text), dialect))
    rows = [r for r in rows if any(c not in (None, "") for c in r)]
    if not rows:
        return []
    header = [_norm(h) for h in rows[0]]
    idx = {}
    for key, names in HEADER_MAP.items():
        for i, h in enumerate(header):
            if h in names:
                idx[key] = i
                break
    if "order_code" not in idx and "code" not in idx:
        raise ValueError("Súbor musí obsahovať stĺpec 'Objednávacie číslo' alebo 'Označenie'.")
    items = []
    for r in rows[1:]:
        def val(k):
            i = idx.get(k)
            if i is None or i >= len(r) or r[i] is None:
                return ""
            v = r[i]
            if isinstance(v, float) and v.is_integer():
                v = int(v)
            return str(v).strip()

        q = val("qty")
        try:
            qty = int(float(q.replace(",", "."))) if q else 1
        except ValueError:
            qty = 1
        item = {k: val(k) for k in ("code", "order_code", "name", "manufacturer", "supplier")}
        item["qty"] = max(qty, 0)
        if item["code"] or item["order_code"]:
            items.append(item)
    return items


# --- Fulltextové hľadanie dielov -----------------------------------------------

SEARCH_FIELDS = [
    ("order_code", "Objednávacie číslo"), ("code", "Označenie"), ("name", "Názov"),
    ("manufacturer", "Výrobca"), ("supplier", "Dodávateľ"), ("category", "Kategória"),
    ("location_full", "Umiestnenie"), ("params", "Parametre"), ("note", "Poznámka"),
]


def _fold(text):
    """Malé písmená bez diakritiky: 'Stýkač' -> 'stykac'."""
    import unicodedata
    text = unicodedata.normalize("NFKD", str(text or "").lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def _compact(text):
    """Bez medzier, pomlčiek, bodiek a lomiek: '6ES7 214-1AG40' -> '6es72141ag40'."""
    return "".join(c for c in _fold(text) if c.isalnum())


def search_parts(q, limit=50):
    """Hľadá vo všetkých textových poliach skladovej karty.

    - nerozlišuje veľké/malé písmená ani diakritiku,
    - ignoruje medzery, pomlčky a bodky v číslach (6ES7214-1AG40 = 6es7 214 1ag40),
    - viac slov = každé slovo sa musí niekde vyskytnúť (napr. "festo ventil").
    Vracia zoznam dielov zoradený podľa relevancie, s kľúčom 'matched' (kde sa našlo).
    """
    q = (q or "").strip()
    if not q:
        return []
    tokens = [t for t in _fold(q).split() if t]
    q_compact = _compact(q)
    rows = get_db().query("SELECT * FROM parts")
    results = []
    for p in rows:
        p["location_full"] = location_label(p)
        folded = {f: _fold(p.get(f)) for f, _ in SEARCH_FIELDS}
        compact = {f: _compact(p.get(f)) for f, _ in SEARCH_FIELDS}
        haystack = " ".join(folded.values())
        hay_compact = "".join(compact.values())
        whole = bool(q_compact) and any(q_compact in compact[f] for f, _ in SEARCH_FIELDS)
        if not whole and not all(t in haystack or (_compact(t) and _compact(t) in hay_compact) for t in tokens):
            continue
        matched = []
        for f, label in SEARCH_FIELDS:
            if (q_compact and q_compact in compact[f]) or any(t in folded[f] for t in tokens):
                matched.append(label)
        score = 0
        if q_compact and q_compact in (compact["order_code"], compact["code"]):
            score += 1000
        elif q_compact and (q_compact in compact["order_code"] or q_compact in compact["code"]):
            score += 500
        if q_compact and q_compact in compact["name"]:
            score += 200
        score += 10 * sum(1 for t in tokens if t in folded["name"])
        score -= len(matched) == 0
        p["matched"] = matched
        p["_score"] = score
        results.append(p)
    results.sort(key=lambda p: (-p["_score"], p["code"]))
    return results[:limit]


# --- Lokácie, regály a umiestnenie dielov -----------------------------------------

def locations_map():
    """Všetky lokácie {id: lokácia} (napr. Hlavný sklad, Dielňa) – načítané raz za požiadavku."""
    from flask import g

    if "locations" not in g:
        rows = get_db().query("SELECT * FROM locations ORDER BY name")
        g.locations = {r["id"]: r for r in rows}
    return g.locations


def racks_map():
    """Všetky regály {id: regál} – v rámci jednej požiadavky sa načítajú iba raz."""
    from flask import g

    if "racks" not in g:
        rows = get_db().query("""
            SELECT r.*, p.name AS project_name, p.order_no AS project_order_no
            FROM racks r LEFT JOIN projects p ON p.id = r.project_id ORDER BY r.code""")
        locs = locations_map()
        for r in rows:
            r["title"] = r["name"] or r["project_name"] or ""
            r["label"] = r["code"] + (" – " + r["title"] if r["title"] else "")
            loc = locs.get(r["location_id"])
            r["location_name"] = loc["name"] if loc else ""
            r["full_label"] = (r["location_name"] + " › " if loc else "") + r["label"]
            r["shelves"] = [str(i) for i in range(1, int(r["shelf_count"] or 0) + 1)]
        rows.sort(key=lambda r: (r["location_name"] == "", r["location_name"].lower(), r["code"]))
        g.racks = {r["id"]: r for r in rows}
    return g.racks


def location_parts(part):
    """(lokácia, regál, polica, vlastné pomenovanie) pre zobrazenie umiestnenia dielu."""
    rack = racks_map().get(part.get("rack_id")) if part.get("rack_id") else None
    loc_id = (rack or {}).get("location_id") or part.get("location_id")
    loc = locations_map().get(loc_id) if loc_id else None
    return loc, rack, (part.get("shelf") or "").strip(), (part.get("location") or "").strip()


def shelf_text(shelf):
    return ("polica " + shelf) if shelf[:1].isdigit() else shelf


def location_label(part):
    """Umiestnenie dielu ako text, napr. 'Hlavný sklad › R1 – Elektro › polica 3'."""
    loc, rack, shelf, other = location_parts(part)
    out = []
    if loc:
        out.append(loc["name"])
    if rack:
        out.append(rack["label"])
    if shelf:
        out.append(shelf_text(shelf))
    if other:
        out.append(other)
    return " › ".join(out)
