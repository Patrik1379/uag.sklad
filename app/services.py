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
                   "order code", "order number", "part number", "partnumber", "pn", "objednavacie_cislo",
                   "číslo dielu", "cislo dielu", "artikel", "article"],
    "name": ["nazov", "názov", "name", "nazov dielu", "názov dielu"],
    "description": ["popis", "description", "beschreibung"],
    "manufacturer": ["vyrobca", "výrobca", "manufacturer", "vendor", "hersteller"],
    "supplier": ["dodavatel", "dodávateľ", "supplier", "lieferant"],
    "qty": ["mnozstvo", "množstvo", "pocet", "počet", "ks", "qty", "quantity", "pocet ks", "počet ks", "menge",
            "item qty", "total qty"],
    "material": ["material", "materiál"],
    "mass": ["mass", "hmotnost", "hmotnosť", "weight", "gewicht"],
    "filename": ["filename", "file name", "subor", "súbor"],
    "thumbnail": ["thumbnail", "nahlad", "náhľad", "obrazok", "obrázok", "image"],
}


def _norm(h):
    return " ".join(str(h or "").strip().lower().replace("_", " ").split())


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def _find_header(rows):
    """Nájde riadok s hlavičkou (nemusí byť prvý – napr. export z Inventora má nad ňou údaje o zostave)."""
    best, best_idx = None, {}
    for ri, r in enumerate(rows[:40]):
        header = [_norm(h) for h in r]
        idx = {}
        for key, names in HEADER_MAP.items():
            for i, h in enumerate(header):
                if h in names:
                    idx[key] = i
                    break
        if ("order_code" in idx or "code" in idx) and len(idx) > len(best_idx):
            best, best_idx = ri, idx
    return best, best_idx


def _bom_meta(rows, header_row):
    """Údaje o zostave nad hlavičkou (Inventor: 'Part list of Assemblie …', Device name, Serial number…)."""
    meta = {}
    above = rows[:header_row]
    for ri, r in enumerate(above):
        cells = [_cell(c) for c in r]
        for c in cells:
            low = c.lower()
            for prefix in ("part list of assemblie", "part list of assembly", "kusovník zostavy", "kusovnik zostavy"):
                if low.startswith(prefix):
                    meta["assembly"] = c[len(prefix):].strip(" :")
        # štítok v jednom riadku, hodnota pod ním (rovnaký stĺpec)
        if ri + 1 < len(above):
            below = [_cell(c) for c in above[ri + 1]]
            for ci, c in enumerate(cells):
                key = {"device name": "device", "názov zariadenia": "device", "nazov zariadenia": "device",
                       "type": "type", "typ": "type", "serial number": "serial", "výrobné číslo": "serial",
                       "zakazka": "serial", "zákazka": "serial", "manufacturing year": "year",
                       "rok výroby": "year"}.get(c.lower())
                if key and ci < len(below) and below[ci]:
                    meta.setdefault(key, below[ci])
            # štítok a hodnota v jednom riadku vedľa seba
            for ci, c in enumerate(cells[:-1]):
                if c.lower() in ("author", "autor", "date", "dátum") and cells[ci + 1:]:
                    nxt = next((x for x in cells[ci + 1:] if x), "")
                    if nxt and c.lower() in ("author", "autor"):
                        meta.setdefault("author", nxt)
    return meta


def read_bom(file_storage):
    """Načíta kusovník (XLSX/CSV). Vracia {"items": [...], "meta": {...}}.

    Zvláda aj export z Autodesk Inventora: hlavička nemusí byť v prvom riadku,
    údaje o zostave nad ňou sa načítajú do meta a náhľady dielov (stĺpec Thumbnail) sa priradia k riadkom.
    """
    filename = (file_storage.filename or "").lower()
    images = {}
    if filename.endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook

        wb = load_workbook(file_storage.stream, data_only=True)
        ws = wb.active
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        for im in getattr(ws, "_images", []):
            try:
                fr = im.anchor._from
                images[(fr.row, fr.col)] = im._data()
            except Exception:
                pass
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
    if not any(any(c not in (None, "") for c in r) for r in rows):
        return {"items": [], "meta": {}}
    hrow, idx = _find_header(rows)
    if hrow is None:
        raise ValueError("Nenašiel sa stĺpec 'Objednávacie číslo' / 'Part Number' ani 'Označenie'.")
    meta = _bom_meta(rows, hrow)

    items, by_key = [], {}
    for ri in range(hrow + 1, len(rows)):
        r = rows[ri]
        if not any(c not in (None, "") for c in r):
            continue

        def val(k):
            i = idx.get(k)
            return _cell(r[i]) if i is not None and i < len(r) else ""

        code, order_code = val("code"), val("order_code")
        if not code and not order_code:
            continue
        q = val("qty")
        try:
            qty = int(round(float(q.replace(",", ".")))) if q else 1
        except ValueError:
            qty = 1
        desc, name = val("description"), val("name")
        pn = order_code or code
        if not name:
            name = f"{desc} {pn}".strip() if desc and desc.lower() not in pn.lower() else (desc or pn)
        params = []
        material = val("material")
        if material and material.lower() != "generic":
            params.append("Materiál: " + material)
        if val("mass"):
            params.append("Hmotnosť: " + val("mass"))
        if val("filename"):
            params.append("CAD súbor: " + val("filename"))
        up = pn.upper()
        category = ""
        if up.startswith(("DIN ", "DIN-", "ISO ", "ISO-", "EN ", "ČSN ", "CSN ")):
            category = "Spojovací materiál"
        elif "_P_" in up:
            category = "Výrobný diel"
        image = None
        if "thumbnail" in idx:
            image = images.get((ri, idx["thumbnail"]))
        key = (code or "").lower(), (order_code or "").lower()
        if key in by_key:                      # rovnaký diel viackrát v kusovníku → sčítame
            by_key[key]["qty"] += max(qty, 0)
            continue
        item = {"code": code, "order_code": order_code, "name": name[:255],
                "manufacturer": val("manufacturer"), "supplier": val("supplier"),
                "params": "\n".join(params), "category": category, "image": image, "qty": max(qty, 0)}
        by_key[key] = item
        items.append(item)
    return {"items": items, "meta": meta}


class _BytesUpload:
    """Obal, aby sa obrázok z kusovníka dal uložiť funkciou save_image."""
    def __init__(self, data):
        self.filename = "nahlad.png"
        self.stream = io.BytesIO(data)


def import_bom_items(project_id, items):
    """Vloží položky kusovníka do projektu. Chýbajúce skladové karty vytvorí, náhľady doplní."""
    db = get_db()
    stats = {"linked": 0, "created": 0, "images": 0}
    for it in items:
        part = None
        if it["code"]:
            part = db.query("SELECT * FROM parts WHERE LOWER(code) = LOWER(?)", (it["code"],), one=True)
        if not part and it["order_code"]:
            part = db.query("SELECT * FROM parts WHERE LOWER(order_code) = LOWER(?)", (it["order_code"],), one=True)
        image = None
        if it.get("image") and (not part or not part.get("image")):
            try:
                image = save_image(_BytesUpload(it["image"]))
                stats["images"] += 1
            except Exception:
                image = None
        if not part:
            code = it["code"] or next_part_code()
            part_id = db.execute(
                """INSERT INTO parts (code, order_code, name, manufacturer, supplier, category, params, image)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING id""",
                (code, it["order_code"] or None, it["name"] or it["order_code"] or code,
                 it["manufacturer"] or None, it["supplier"] or None, it.get("category") or None,
                 it.get("params") or None, image))
            stats["created"] += 1
        else:
            part_id = part["id"]
            if image:
                db.execute("UPDATE parts SET image = ? WHERE id = ?", (image, part_id))
        if it["qty"] <= 0:
            continue
        ex = db.query("SELECT id FROM project_parts WHERE project_id = ? AND part_id = ?",
                      (project_id, part_id), one=True)
        note = it.get("pp_note") or None
        if ex:
            db.execute("UPDATE project_parts SET qty_required = ?, note = COALESCE(?, note) WHERE id = ?",
                       (it["qty"], note, ex["id"]))
        else:
            db.execute("INSERT INTO project_parts (project_id, part_id, qty_required, note) VALUES (?, ?, ?, ?)",
                       (project_id, part_id, it["qty"], note))
        stats["linked"] += 1
    return stats


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


# --- Import zoznamu dielov z EPLAN (XML export "partsList") ----------------------

EPLAN_MANUFACTURERS = {
    "PXC": "Phoenix Contact", "SE": "Schneider Electric", "SIE": "Siemens", "FES": "Festo", "KEY": "Keyence",
    "HELU": "Helukabel", "SCHR": "Schrack", "WEI": "Weidmüller", "MWP": "Mean Well", "RIT": "Rittal",
    "WAGO": "WAGO", "LAPP": "Lapp", "OMR": "Omron", "IFM": "ifm", "SICK": "SICK", "PIL": "Pilz",
    "BAL": "Balluff", "ABB": "ABB", "EAT": "Eaton", "MUR": "Murrelektronik", "HAR": "Harting",
    "BECK": "Beckhoff", "TUR": "Turck", "LEU": "Leuze", "PEP": "Pepperl+Fuchs", "SMC": "SMC", "FIN": "Finder",
    "WEID": "Weidmüller", "OBO": "OBO Bettermann", "HAG": "Hager", "LEG": "Legrand", "BOS": "Bosch Rexroth",
}


def _eplan_text(value, prefer=("sk_SK", "cs_CZ", "en_US", "de_DE")):
    """EPLAN viacjazyčný text 'de_DE@Text;en_US@Text;' → text v preferovanom jazyku."""
    if not value:
        return ""
    if "@" not in value:
        return value.strip()
    texts = {}
    for chunk in value.split(";"):
        if "@" in chunk:
            lang, text = chunk.split("@", 1)
            if text.strip():
                texts[lang.strip()] = " ".join(text.split())
    for lang in prefer:
        if texts.get(lang):
            return texts[lang]
    return next(iter(texts.values()), "")


def parse_eplan_xml(stream):
    """Načíta EPLAN export zoznamu dielov (XML <partsList><device><part …/>).

    Vracia {"structures": {štruktúra: počet záznamov}, "articles": [...]} – pri každom artikli sú počty
    kusov rozdelené podľa štruktúry stránok (napr. '=ND+R1', '&DOK'), aby si používateľ mohol vybrať,
    ktoré časti projektu započítať (EPLAN často obsahuje aj kópie stránok v dokumentácii).
    """
    import xml.etree.ElementTree as ET

    root = ET.parse(stream).getroot()
    if root.tag != "partsList":
        raise ValueError("Toto nie je EPLAN export zoznamu dielov (chýba <partsList>).")
    structures, articles = {}, {}
    for part in root.iter("part"):
        pn = (part.get("P_ARTICLEREF_PARTNO") or "").strip()
        if not pn:
            continue
        page = part.get("P_INSTANCE_PAGEFULLNAME") or ""
        struct = page.split("/")[0] or "(bez štruktúry)"
        structures[struct] = structures.get(struct, 0) + 1
        try:
            count = float((part.get("P_ARTICLEREF_COUNT") or "1").replace(",", "."))
        except ValueError:
            count = 1.0
        a = articles.get(pn)
        if a is None:
            prefix, _, rest = pn.partition(".")
            known = rest and prefix.isalpha() and len(prefix) <= 5
            maker_code = part.get("P_ARTICLE_MANUFACTURER") or (prefix if known else "")
            descr = _eplan_text(part.get("P_ARTICLE_DESCR1")) or _eplan_text(part.get("P_ARTICLE_DESCR2"))
            typenr = (part.get("P_ARTICLE_TYPENR") or "").strip()
            name = (descr if typenr and typenr.lower() in descr.lower() else
                    " ".join(x for x in (descr, typenr) if x)) or pn
            params = []
            if typenr:
                params.append("Typ: " + typenr)
            if part.get("P_ARTICLE_ORDERNR"):
                params.append("Obj. č. (EPLAN): " + part.get("P_ARTICLE_ORDERNR"))
            params.append("EPLAN artikel: " + pn)
            length = part.get("P_ARTICLE_PARTIAL_LENGTH_FULL")
            if length and length not in ("0", "0 m"):
                params.append("Dĺžka: " + length)
            note = _eplan_text(part.get("P_ARTICLE_NOTE"))
            if note:
                params.append(note[:400])
            a = articles[pn] = {
                "partno": pn,
                "order_code": rest if known else pn,
                "name": name[:255],
                "manufacturer": EPLAN_MANUFACTURERS.get(maker_code, maker_code),
                "supplier": part.get("P_ARTICLE_SUPPLIER") or "",
                "params": "\n".join(params),
                "category": "Elektro",
                "counts": {}, "tags": {},
            }
        a["counts"][struct] = a["counts"].get(struct, 0) + count
        tag = (part.get("P_FUNC_DEVICETAG_FULLNAME") or "").strip()
        if tag:
            a["tags"].setdefault(struct, [])
            if tag not in a["tags"][struct]:
                a["tags"][struct].append(tag)
    arts = sorted(articles.values(), key=lambda a: (a["manufacturer"].lower(), a["order_code"]))
    return {"structures": structures, "articles": arts}


def eplan_items(parsed, selected_structs, excluded=()):
    """Z načítaného EPLAN súboru vytvorí položky pre import (len zvolené štruktúry a artikle)."""
    items = []
    for a in parsed["articles"]:
        if a["partno"] in excluded:
            continue
        qty = sum(v for s, v in a["counts"].items() if s in selected_structs)
        if qty <= 0:
            continue
        tags = []
        for s in selected_structs:
            for t in a["tags"].get(s, []):
                if t not in tags:
                    tags.append(t)
        note = ("Označenie v schéme: " + ", ".join(tags)) if tags else ""
        items.append({"code": "", "order_code": a["order_code"], "name": a["name"],
                      "manufacturer": a["manufacturer"], "supplier": a["supplier"], "params": a["params"],
                      "category": a["category"], "image": None, "qty": int(round(qty)),
                      "pp_note": note[:500]})
    return items
