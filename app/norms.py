"""Ekvivalenty noriem spojovacieho materiálu DIN ↔ ISO.

Keď je na skladovej karte uvedená norma DIN (napr. „DIN 912 M6x20“), aplikácia pozná aj zodpovedajúcu
ISO normu (ISO 4762) a naopak – hľadanie funguje podľa oboch a na karte sa ekvivalent zobrazí.
Tabuľku je možné doplniť; rozmery a vlastnosti sa medzi DIN a ISO verziou môžu mierne líšiť
(napr. veľkosť kľúča pri M10/M12/M14/M22), pre hľadanie to však nevadí.
"""
import re

# DIN číslo -> ISO čísla
DIN_TO_ISO = {
    "1": ["2339"],        # kužeľový kolík
    "7": ["2338"],        # valcový kolík
    "84": ["1207"],       # skrutka s valcovou hlavou, drážka
    "85": ["1580"],       # skrutka s plochou guľovou hlavou, drážka
    "94": ["1234"],       # závlačka
    "125": ["7089", "7090"],  # podložka (A / B so zrazením)
    "433": ["7092"],      # podložka malá
    "439": ["4035"],      # matica nízka
    "555": ["4034"],      # matica šesťhranná, trieda C
    "558": ["4018"],      # skrutka šesťhranná s celým závitom, trieda C
    "601": ["4016"],      # skrutka šesťhranná, trieda C
    "912": ["4762"],      # skrutka s valcovou hlavou a vnútorným šesťhranom (imbus)
    "913": ["4026"],      # stavacia skrutka s plochým koncom
    "914": ["4027"],      # stavacia skrutka s kužeľovým hrotom
    "915": ["4028"],      # stavacia skrutka s čapom
    "916": ["4029"],      # stavacia skrutka s vencom
    "931": ["4014"],      # skrutka šesťhranná s čiastočným závitom
    "933": ["4017"],      # skrutka šesťhranná s celým závitom
    "934": ["4032"],      # matica šesťhranná
    "960": ["8765"],      # skrutka šesťhranná, jemný závit, čiastočný
    "961": ["8676"],      # skrutka šesťhranná, jemný závit, celý
    "963": ["2009"],      # zápustná skrutka, drážka
    "964": ["2010"],      # šošovková zápustná skrutka, drážka
    "965": ["7046"],      # zápustná skrutka, krížová drážka
    "966": ["7047"],      # šošovková zápustná skrutka, krížová drážka
    "982": ["7040"],      # poistná matica s polyamidovým krúžkom
    "985": ["10511"],     # poistná matica nízka s polyamidovým krúžkom
    "1443": ["2340"],     # čap bez hlavy
    "1444": ["2341"],     # čap s hlavou
    "1481": ["8752"],     # pružný kolík ťažký
    "6325": ["8734"],     # valcový kolík kalený
    "6914": ["7412"],     # skrutka pre oceľové konštrukcie (HV)
    "7346": ["13337"],    # pružný kolík ľahký
    "7976": ["1479"],     # samorezná skrutka so šesťhrannou hlavou
    "7981": ["7049"],     # samorezná skrutka s plochou guľovou hlavou
    "7982": ["7050"],     # samorezná zápustná skrutka
    "7983": ["7051"],     # samorezná šošovková zápustná skrutka
    "7985": ["7045"],     # skrutka s plochou guľovou hlavou, krížová drážka
    "7991": ["10642"],    # zápustná skrutka s vnútorným šesťhranom
    "9021": ["7093"],     # podložka veľká
}
ISO_TO_DIN = {}
for _din, _isos in DIN_TO_ISO.items():
    for _iso in _isos:
        ISO_TO_DIN.setdefault(_iso, []).append(_din)

_RE = re.compile(r"(?<![A-Za-z])(DIN|ISO)[\s\-_]*(\d{1,5})(?!\d)", re.IGNORECASE)


def found_norms(*texts):
    """Normy uvedené v textoch: {("DIN", "912"), ("ISO", "4762"), …}."""
    out = set()
    for t in texts:
        for kind, num in _RE.findall(str(t or "")):
            out.add((kind.upper(), num.lstrip("0") or "0"))
    return out


def equivalents(*texts):
    """Ekvivalentné normy, ktoré v textoch ešte nie sú – napr. pre „DIN 912 M6x20“ → ["ISO 4762"]."""
    have = found_norms(*texts)
    out = []
    for kind, num in sorted(have):
        others = DIN_TO_ISO.get(num, []) if kind == "DIN" else ISO_TO_DIN.get(num, [])
        other_kind = "ISO" if kind == "DIN" else "DIN"
        for o in others:
            if (other_kind, o) not in have and f"{other_kind} {o}" not in out:
                out.append(f"{other_kind} {o}")
    return out


def part_equivalents(part):
    return equivalents(part.get("name"), part.get("code"), part.get("order_code"),
                       part.get("params"), part.get("note"))


def search_text(part):
    """Text pre hľadanie: normy na karte + ich ekvivalenty (aby DIN 912 našlo aj kartu s ISO 4762)."""
    have = found_norms(part.get("name"), part.get("code"), part.get("order_code"), part.get("params"), part.get("note"))
    return " ".join([f"{k} {n}" for k, n in sorted(have)] + part_equivalents(part))



# norma v názve vrátane predpony „EN“ / „DIN EN“ (napr. „DIN EN ISO 4017“) a variantu A/B pri DIN 125
_NAME_RE = re.compile(r"(?<![A-Za-z])(?:(?:DIN[\s\-]+)?EN[\s\-]+)?(DIN|ISO)[\s\-_]*(\d{1,5})(?!\d)(?:[\s\-]*([AB])(?![A-Za-z0-9]))?",
                      re.IGNORECASE)
_SEP_RE = re.compile(r"[\s/=,;]*")


def _pair(kind, num, variant):
    """Kanonický zápis dvojice „DIN … / ISO …“ pre normu, alebo None, ak ekvivalent nepoznáme."""
    if kind == "DIN":
        isos = DIN_TO_ISO.get(num)
        if not isos:
            return None
        if num == "125":
            iso = "7090" if variant == "B" else "7089"
            return (f"DIN 125-{variant}" if variant else "DIN 125"), f"ISO {iso}", {("DIN", "125"), ("ISO", iso)}
        return f"DIN {num}", f"ISO {isos[0]}", {("DIN", num)} | {("ISO", i) for i in isos}
    dins = ISO_TO_DIN.get(num)
    if not dins:
        return None
    if dins == ["125"]:
        v = "B" if num == "7090" else "A"
        return f"DIN 125-{v}", f"ISO {num}", {("DIN", "125"), ("ISO", num)}
    return f"DIN {dins[0]}", f"ISO {num}", {("ISO", num)} | {("DIN", d) for d in dins}


def name_with_equivalents(name):
    """Zjednotí zápis normy v názve na „DIN … / ISO …“ (vždy najprv DIN, potom ISO):
    „Skrutka DIN 912 M6x20“ → „Skrutka DIN 912 / ISO 4762 M6x20“,
    „Matica ISO 4032 M8“ → „Matica DIN 934 / ISO 4032 M8“,
    „Skrutka ISO 4762 / DIN 912 M6“ → „Skrutka DIN 912 / ISO 4762 M6“.
    Normy bez známeho ekvivalentu (napr. DIN 471) zostanú bez zmeny."""
    name = name or ""
    tokens = list(_NAME_RE.finditer(name))
    out, pos, done, i = [], 0, set(), 0
    while i < len(tokens):
        m = tokens[i]
        kind, num = m.group(1).upper(), m.group(2).lstrip("0") or "0"
        variant = (m.group(3) or "").upper() if kind == "DIN" and num == "125" else ""
        start, end = m.start(), (m.end() if variant or kind != "DIN" or num != "125" else m.end())
        if kind != "DIN" or num != "125":
            # písmeno A/B za inou normou (napr. „ISO 4762 A“) nie je súčasť normy – necháme ho v texte
            end = m.end(2) if m.group(3) else m.end()
        if (kind, num) in done:                 # druhá polovica už zapísanej dvojice inde v názve → vynechať
            out.append(name[pos:start].rstrip(" /=,;") + " ")
            pos = end
            i += 1
            continue
        pair = _pair(kind, num, variant)
        if not pair:
            i += 1
            continue
        din_txt, iso_txt, members = pair
        # ak hneď za normou nasleduje jej ekvivalent (napr. „ISO 4762 / DIN 912“), prepíšeme obe naraz
        if i + 1 < len(tokens):
            n = tokens[i + 1]
            k2, n2 = n.group(1).upper(), n.group(2).lstrip("0") or "0"
            if (k2, n2) in members and k2 != kind and _SEP_RE.fullmatch(name[end:n.start()]):
                if k2 == "DIN" and n2 == "125" and n.group(3):
                    din_txt = f"DIN 125-{n.group(3).upper()}"
                end = n.end() if (k2 == "DIN" and n2 == "125") or not n.group(3) else n.end(2)
                i += 1
        out.append(name[pos:start] + f"{din_txt} / {iso_txt}")
        pos = end
        done |= members
        i += 1
    out.append(name[pos:])
    return re.sub(r"\s{2,}", " ", "".join(out)).strip()[:255]



# podložky: DIN udáva priemer otvoru (DIN 125 - A 8,4), ISO menovitú veľkosť = závit (ISO 7089 - 8)
_WASHER_HOLE = {"1,6": "1,7", "2": "2,2", "2,5": "2,7", "3": "3,2", "4": "4,3", "5": "5,3", "6": "6,4", "8": "8,4",
                "10": "10,5", "12": "13", "14": "15", "16": "17", "20": "21", "24": "25", "30": "31", "36": "37"}
_WASHER_HOLE_LARGE = {"3": "3,2", "4": "4,3", "5": "5,3", "6": "6,4", "8": "8,4", "10": "10,5", "12": "13", "16": "17"}
_HOLE_TO_NOMINAL = {v: k for k, v in _WASHER_HOLE.items()}
_HOLE_TO_NOMINAL_LARGE = {v: k for k, v in _WASHER_HOLE_LARGE.items()}
# DIN podložka -> (ISO, tabuľka otvorov)
_WASHERS_DIN = {"125": None, "433": "7092", "9021": "7093"}
_WASHERS_ISO = {"7089": ("125", "A"), "7090": ("125", "B"), "7092": ("433", ""), "7093": ("9021", "")}

_NUM = r"\d+(?:[.,]\d+)?"
_SIZE_RE = re.compile(r"(?<![A-Za-z0-9])M\s*(" + _NUM + r")(?:\s*[x×X]\s*(" + _NUM + r"))?(?:\s*[x×X]\s*(" + _NUM + r"))?")


def _n(v):
    """Číslo v tvare „6,4“ (desatinná čiarka), bez zbytočných núl."""
    v = v.replace(".", ",")
    if "," in v:
        v = v.rstrip("0").rstrip(",")
    return v


def _extras(text):
    """Trieda pevnosti, materiál a pod. (8.8, 10.9, A2-70, 200 HV …) – všetko s číslicou okrem rozmeru."""
    out = []
    for m in re.finditer(r"(\d{2,3})\s*HV\b|[A-Za-z]?\d[\w.,\-]*", text):
        w = (m.group(1) + " HV") if m.group(1) else m.group(0).strip(",;-_.")
        if w and w not in out:
            out.append(w)
    return out


def _clean_spec(seg):
    """Rozmer v zápise „ - M3 x 8“, „ - 0,8 x 6“, „ - B - 3 x 16“, „ - 2,5 x 8 - A“ (Inventor) → zjednotený text.
    Vracia None, ak text nie je takýto čistý zápis (napr. „Skrutka … M6x20 zinok“)."""
    s = seg.strip()
    if not s.startswith("-"):
        return None
    s = s.lstrip("- ").strip()
    parts = [p.strip() for p in re.split(r"\s+-\s+|\s+-(?=\S)|(?<=\S)-\s+", s) if p.strip()]
    tok = re.compile(r"^(?:[A-Z]|M?\d+(?:[.,]\d+)?(?:\s*[a-z]\d+)?(?:\s*[x×X]\s*\d+(?:[.,]\d+)?)*|"
                     r"\d+\.\d+|A[24](?:-\d+)?|\d{2,3}\s*HV|St|[a-z]\d+)$")
    if not parts or not all(tok.match(p) for p in parts):
        return None
    parts = [re.sub(r"\s*[x×X]\s*", " x ", re.sub(r"\s+", " ", p)) for p in parts]
    return " - ".join(parts)


def designations(text):
    """Úplné označenie podľa DIN aj ISO v zápise ako z Inventora:
    „DIN 912 - M4 x 20“ → („DIN 912 - M4 x 20“, „ISO 4762 - M4 x 20“),
    „Skrutka DIN 912 M6x20 8.8“ → („DIN 912 - M6 x 20 - 8.8“, „ISO 4762 - M6 x 20 - 8.8“),
    „DIN 125 - A 4,3“ → („DIN 125 - A 4,3“, „ISO 7089 - 4“)  (podložky: otvor ↔ menovitá veľkosť).
    Vracia None, ak text neobsahuje normu so známym ekvivalentom (napr. DIN 127)."""
    text = text or ""
    tokens = list(_NAME_RE.finditer(text))
    chosen = None
    for m in tokens:
        kind, num = m.group(1).upper(), m.group(2).lstrip("0") or "0"
        variant = (m.group(3) or "").upper()
        if (kind == "DIN" and num in DIN_TO_ISO) or (kind == "ISO" and num in ISO_TO_DIN):
            chosen = (m, kind, num, variant)
            break
    if not chosen:
        return None
    m, kind, num, variant = chosen
    # text patriaci k tejto norme: od normy po ďalšiu normu (alebo „/“), inak celý text bez noriem
    nxt = [t.start() for t in tokens if t.start() > m.start()]
    seg_end = min([text.find("/", m.end()) if text.find("/", m.end()) >= 0 else len(text)] + nxt)
    seg = text[m.end():seg_end]
    if not re.search(r"\d", seg):
        rest, pos = [], 0
        for t in tokens:
            rest.append(text[pos:t.start()])
            pos = t.end()
        rest.append(text[pos:])
        seg = " ".join(rest).split("/")[0]
    if not (kind == "DIN" and num == "125"):
        variant = ""   # písmeno za inou normou je súčasť rozmeru (napr. „ISO 2341 - B - 3 x 16“) – vrátime ho do textu
        if m.group(3):
            seg = " - " + m.group(3) + seg

    # --- podložky
    din_washer = kind == "DIN" and num in _WASHERS_DIN
    iso_washer = kind == "ISO" and num in _WASHERS_ISO
    if din_washer or iso_washer:
        if din_washer:
            din_num = num
            form = variant or ("A" if num == "125" else "")
            iso_num = ("7090" if form == "B" else "7089") if num == "125" else _WASHERS_DIN[num]
        else:
            din_num, form = _WASHERS_ISO[num]
            iso_num = num
        large = din_num == "9021"
        sm = re.search(r"(?<![A-Za-z0-9])(?:M\s*)?(" + _NUM + r")", seg)
        if not sm:
            return None
        val = _n(sm.group(1))
        if din_washer:
            hole = val
            nominal = (_HOLE_TO_NOMINAL_LARGE if large else _HOLE_TO_NOMINAL).get(hole)
        else:
            nominal = val
            hole = (_WASHER_HOLE_LARGE if large else _WASHER_HOLE).get(nominal)
        if not nominal or not hole:
            return None                     # rozmer nepoznáme – radšej nič nemeníme
        extra = [e for e in _extras(seg[sm.end():]) if e not in (val,)]
        tail = "".join(" - " + e for e in extra)
        din_txt = f"DIN {din_num} - " + (f"{form} " if form else "") + hole + tail
        return din_txt, f"ISO {iso_num} - {nominal}{tail}"

    # --- skrutky, matice, kolíky …
    din_num = num if kind == "DIN" else ISO_TO_DIN[num][0]
    iso_num = DIN_TO_ISO[num][0] if kind == "DIN" else num
    clean = _clean_spec(seg)
    if clean:                                # zápis ako z Inventora („- M3 x 8“, „- B - 3 x 16“) prevezmeme celý
        return f"DIN {din_num} - {clean}", f"ISO {iso_num} - {clean}"
    sm = _SIZE_RE.search(seg)
    if sm:
        a, b, c = sm.group(1), sm.group(2), sm.group(3)
        size = f"M{_n(a)}" + (f" x {_n(b)}" if b else "") + (f" x {_n(c)}" if c else "")
        extra = _extras(seg[sm.end():])
    else:
        nums = _extras(seg)                  # napr. kolík „ISO 2338 6m6x20“
        if not nums:
            return None
        size, extra = re.sub(r"(?<=\d)\s*[x×X]\s*(?=\d)", " x ", nums[0]), nums[1:]
    tail = "".join(" - " + e for e in extra)
    return f"DIN {din_num} - {size}{tail}", f"ISO {iso_num} - {size}{tail}"


def order_code_with_norms(order_code, name=""):
    """Objednávacie číslo s úplným označením podľa DIN aj ISO oddeleným „ / “:
    „DIN 912 M6x20 8.8 / ISO 4762 M6x20 8.8“.
    - prázdne objednávacie číslo → zostaví sa z názvu (ak je v ňom norma),
    - objednávacie číslo s normou → zjednotí sa na „DIN … / ISO …“,
    - iné objednávacie číslo (napr. kód dodávateľa) sa nemení."""
    order_code = (order_code or "").strip()
    if order_code:
        if not found_norms(order_code):
            return order_code
        source = order_code
    else:
        source = name or ""
    d = designations(source)
    if not d:
        return order_code
    return f"{d[0]} / {d[1]}"[:150]
