#!/usr/bin/env python3
"""
Збирає каталог «Взуття на Висоті» з YML-вигрузки MyDrop.

    python build.py --feed vygruzka.xml --template template.html --out index.html
    python build.py --feed "https://backend.mydrop.com.ua/...?..." --template template.html --out index.html

Якщо --feed не вказано, береться змінна оточення FEED_URL.
"""
import argparse, hashlib, html, json, os, re, sys, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from collections import OrderedDict
from datetime import datetime, timezone, timedelta

PIC_PREFIX = "https://backend.mydrop.com.ua/vendor/products/uploads/"
EMOJI = re.compile(r"[\U0001F000-\U0001FFFF\u2600-\u27BF\uFE0F\u200d]")
BRANDS = re.compile(
    r"\b(в\s+стилі\s+\S+|міу|miu|balenciaga|баленсіага|hermes|гермес|bratz|loro\s*piana|"
    r"chanel|шанель|prada|прада|gucci|гучі|dior|діор|valentino|валентино|jacquemus|ysl|"
    r"louboutin|лубутен|fendi|фенді|alaia|алая|bottega|боттега|celine|селін|"
    r"(?:тм\s+|від\s+)?(?:nikart|tapuli|lonza|allsy|andano|lizmar|monelli|nivelle|richesse|ricchi|roberto\s+netti|netti|olli\s+shoes))\b", re.I)
FIXES = [
    (r"ботілььйон|ботільйон|ботилтйон|ботильон|ботільон", "ботильйон"), (r"бососніж", "босоніж"), (r"круглмй", "круглий"),
    (r"плаформ", "платформ"), (r"демисезон", "демісезон"), (r"\bкабл\b\.?", "каблук"),
    (r"(\d)\s*см", r"\1 см"),
]
TYPES = [
    ("Аксесуари та догляд", r"засоби|догляд|спрей|\bшнурки\b|\bшнурок\b|шкарпет|гетр"),
    ("Босоніжки та шльопанці", r"босоніж|бососніж|шльопан|шльопк|сабо|мюлі|мюли|сандал|слайдер|еспадрил|в'єтнам|вʼєтнам"),
    ("Кросівки та кеди", r"кросів|снікер|\bкеди\b|\bкед\b"),
    ("Лофери та балетки", r"лофер|\bофер|клог|балетк|мокасин|сліпон"),
    ("Туфлі", r"туфл|слінгбек|човник"),
    ("Ботфорти", r"ботфорт"),
    ("Козаки", r"козак|козачк|казак|ковбой"),
    ("Ботильйони", r"ботильйон|ботільйон|ботилтйон|ботильон|ботільон"),
    ("Уггі та дутики", r"\bугг|\bугі|уггі|дутик|дутік|сноубутс"),
    ("Черевики", r"черевик|челсі|ботінк|берці|берц"),
    ("Чоботи", r"чобот|труби|жатк|жокей|панчох|накидк"),
]
COLORS = [
    ("чорний", r"чорн"), ("білий", r"\bбіл"), ("молочний", r"молоч|молоко|айвор|кремов"),
    ("бежевий", r"беж"), ("пісок", r"пісок|піщан"), ("сірий", r"\bсір"),
    ("шоколад", r"шоколад|шоколод"), ("мокко", r"мокк|\bмока"),
    ("коричневий", r"коричн|коньяк|тоффі|кемел|\bруд|карамел|горіх|капучин|\bкава"),
    ("бордовий", r"бордо|бордов|марсал|вишнев"), ("червоний", r"червон"),
    ("рожевий", r"рожев|пудр|фукс"), ("зелений", r"зелен|хакі|олив|мʼят|м'ят"),
    ("синій", r"\bсин|блакит|голуб|джинс"), ("жовтий", r"жовт|лимон"),
    ("золотий", r"золот"), ("срібний", r"срібл|\bсріб"),
]


def is_sheet(src):
    return bool(re.search(r"output=csv|\.csv(\?|$)|docs\.google\.com/spreadsheets", src or ""))


def read_bytes(src):
    if re.match(r"https?://", src):
        req = urllib.request.Request(src, headers={"User-Agent": "catalog-builder"})
        with urllib.request.urlopen(req, timeout=180) as r:
            return r.read()
    with open(src, "rb") as f:
        return f.read()


def drive_pic(u):
    """Посилання Google Диска → пряме посилання на зображення."""
    u = u.strip()
    m = re.search(r"drive\.google\.com/(?:file/d/|open\?id=|uc\?(?:export=\w+&)?id=)([\w-]{20,})", u) \
        or re.search(r"drive\.google\.com/.*[?&]id=([\w-]{20,})", u)
    return f"https://lh3.googleusercontent.com/d/{m.group(1)}=w1200" if m else u


def localize_pics(products, outdir):
    """Фото з Google Диска зберігаємо на самому сайті (папка img/), щоб вони відкривались у всіх,
    а не лише в того, хто увійшов у Google. Уже збережені не завантажуємо вдруге."""
    imgdir = os.path.join(outdir, "img")
    os.makedirs(imgdir, exist_ok=True)
    keep, errors = set(), []
    for p in products:
        out = []
        for u in p["g"]:
            m = re.match(r"https://lh3\.googleusercontent\.com/d/([\w-]+)", u)
            if not m:
                out.append(u)
                continue
            fid = m.group(1)
            fn = fid + ".jpg"
            path = os.path.join(imgdir, fn)
            if not os.path.exists(path):
                data = None
                for src in (u, f"https://drive.google.com/uc?export=view&id={fid}"):
                    try:
                        req = urllib.request.Request(src, headers={"User-Agent": "Mozilla/5.0"})
                        with urllib.request.urlopen(req, timeout=60) as r:
                            body = r.read()
                            if r.headers.get_content_type().startswith("image/") and len(body) > 2000:
                                data = body
                                break
                    except Exception:
                        pass
                if data is None:
                    # закрите фото не показуємо (у клієнток воно однаково не відкриється)
                    errors.append(f"{p['a']}: фото {fid[:10]}… закрите — відкрийте доступ «Усі, хто має посилання»")
                    continue
                with open(path, "wb") as f:
                    f.write(data)
            keep.add(fn)
            out.append(f"{SITE}/img/{fn}")
        p["g"] = out
    products[:] = [p for p in products if p["g"]]  # без жодного відкритого фото модель не показуємо
    for fn in os.listdir(imgdir):
        if fn not in keep:
            os.remove(os.path.join(imgdir, fn))
    return errors


SHEET_TYPES = {label.lower(): label for label, _ in TYPES}
SHEET_SEASON = {"зима": "w", "зимові": "w", "демісезон": "d", "деміс": "d", "осінь": "d", "весна": "d", "літо": "s", "літні": "s"}


SHEET_REPORT = {}


def build_sheet(data, prefix="my"):
    """Ваші власні товари з Google Таблиці (опублікованої як CSV)."""
    import csv, io
    text = data.decode("utf-8-sig", errors="replace")
    rep = SHEET_REPORT
    rep.update({"bytes": len(data), "start": text[:60].replace("\n", " "), "rows": 0, "skipped": []})
    if text.lstrip().lower().startswith(("<!doctype", "<html")):
        rep["error"] = "замість таблиці Google повернув сторінку (таблицю не опубліковано як CSV?)"
        return []
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return []
    rep["rows"] = len(rows) - 1
    rep["head"] = rows[0]
    head = [h.strip().lower() for h in rows[0]]
    def col(*names):
        for n in names:
            for i, h in enumerate(head):
                if h.startswith(n):
                    return i
        return None
    C = {"a": col("артикул", "код"), "n": col("назва", "название"), "p": col("ціна", "цена"),
         "o": col("стара", "старая"), "z": col("розмір", "размер"), "t": col("тип"), "s": col("сезон"),
         "c": col("колір", "цвет"), "m": col("матеріал", "материал"), "h": col("каблук"),
         "g": col("фото"), "d": col("опис", "описание")}
    get = lambda r, k: (r[C[k]].strip() if C[k] is not None and C[k] < len(r) else "")
    num = lambda v: float(re.sub(r"[^\d.]", "", v.replace(",", ".")) or 0) if v else 0
    products, seen = [], set()
    for r in rows[1:]:
        art, raw = get(r, "a"), get(r, "n")
        price, old = num(get(r, "p")), num(get(r, "o"))
        if not art or not raw or price <= 0:
            if any(x.strip() for x in r):
                rep["skipped"].append(f"{art or '?'}: немає артикулу, назви або ціни")
            continue
        slug = re.sub(r"[^0-9A-Za-z_-]", "", art) or hashlib.sha1(art.encode()).hexdigest()[:8]
        gid = prefix + slug
        if gid in seen:
            continue
        seen.add(gid)
        sizes = {}
        for tok in re.split(r"[,;\n]+", get(r, "z")):
            m = re.match(r"\s*(\d{2})(?:[.,]5)?\s*(?:[-–:/]\s*(\d{2}(?:[.,]\d)?)\s*(?:см|cm)?)?\s*(?:\((\d+)\))?", tok)
            if not m or not 30 <= int(m.group(1)) <= 46:
                continue
            cm = m.group(2).replace(",", ".") if m.group(2) else None
            cm = (cm if "." in cm else cm + ".0") if cm else None
            q = int(m.group(3)) if m.group(3) else 5
            sizes[int(m.group(1))] = [int(m.group(1)), cm, q]
        pics = [drive_pic(u) for u in re.split(r"[,\s]+", get(r, "g")) if u.startswith("http")][:8]
        if not sizes or not pics:
            rep["skipped"].append(f"{art}: " + ("немає розмірів" if not sizes else "немає фото"))
            continue
        name, code = clean_name(raw)
        desc = clean_desc(get(r, "d"))
        text = raw + "\n" + get(r, "m") + "\n" + desc
        t_raw = get(r, "t").lower()
        typ = SHEET_TYPES.get(t_raw) or classify(raw + " " + t_raw, "")
        season = next((v for k, v in SHEET_SEASON.items() if get(r, "s").lower().startswith(k)), "") or season_of(raw, "", desc, typ)
        heel = num(get(r, "h"))
        old = old if old * 0.95 >= price else 0
        products.append({
            "i": gid, "a": art, "n": name or raw, "t": typ, "s": season,
            "c": find_first(COLORS, get(r, "c")) or find_first(COLORS, raw) or get(r, "c").lower(),
            "m": material_of(get(r, "m")) or material_of(text) or get(r, "m").lower(),
            "h": heel if heel else heel_of(text, raw),
            "p": int(price) if float(price).is_integer() else price,
            **({"o": int(old) if float(old).is_integer() else old} if old else {}),
            "g": pics, "z": [sizes[k] for k in sorted(sizes)], "d": desc,
        })
    products.reverse()  # нові рядки внизу таблиці → вгорі каталогу
    return products


def load_feed(src):
    if re.match(r"https?://", src):
        req = urllib.request.Request(src, headers={"User-Agent": "catalog-builder"})
        with urllib.request.urlopen(req, timeout=180) as r:
            data = r.read()
    else:
        with open(src, "rb") as f:
            data = f.read()
    return ET.fromstring(data)


def clean_name(name):
    code = None
    m = re.match(r"\s*код\s*(\d+)\s*", name, re.I)
    if m:
        code, name = m.group(1), name[m.end():]
    name = EMOJI.sub(" ", name)
    name = name.split("!")[0]
    name = re.sub(r"^\s*\d+\s+(?=\D)", "", name)
    name = re.sub(r"^офер", "Лофер", name, flags=re.I)
    name = BRANDS.sub(" ", name)
    # слова КАПСОМ (кирилиця) -> малими
    name = re.sub(r"\b[А-ЯІЇЄҐʼ']{2,}\b", lambda m: m.group(0).lower(), name)
    for a, b in FIXES:
        name = re.sub(a, b, name, flags=re.I)
    name = re.sub(r"\b(зима|демі|деми)\b\s*$", "", name.strip(), flags=re.I)
    name = re.sub(r"\s{2,}", " ", name).strip(" ,.-|")
    name = re.sub(r"^\((.*)\)$", r"\1", name)
    return (name[:1].upper() + name[1:]) if name else name, code


def clean_desc(d):
    if not d:
        return ""
    d = re.sub(r"<br\s*/?>", "\n", d, flags=re.I)
    d = re.sub(r"<[^>]+>", " ", d)
    d = html.unescape(d)
    d = EMOJI.sub("", d)
    d = BRANDS.sub("", d)
    lines = [re.sub(r"\s{2,}", " ", l).strip(" ,") for l in d.split("\n")]
    d = "\n".join(l for l in lines if l)
    return d[:900]


def find_first(patterns, text):
    best = None
    for label, pat in patterns:
        m = re.search(pat, text, re.I)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), label)
    return best[1] if best else ""


def classify(name, cat):
    t = name.lower()
    for label, pat in TYPES:
        if re.search(pat, t):
            return label
    c = (cat or "").lower()
    for label, pat in TYPES:
        if re.search(pat, c):
            return label
    return "Інше"


def season_of(raw_name, cat, desc, typ):
    nc = (raw_name + " " + (cat or "")).lower()
    if "❄" in raw_name or "зим" in nc:
        return "w"
    if re.search(r"демі|деми|осін|весн", nc) or re.search(r"[🍂🍁]", raw_name):
        return "d"
    if re.search(r"літн|літо", nc) or typ == "Босоніжки та шльопанці":
        return "s"
    dl = desc.lower()
    if re.search(r"сезон\s*[-:–]?\s*зим|хутр", dl):
        return "w"
    if re.search(r"сезон\s*[-:–]?\s*(демі|осінь|весна)|байк|фліс", dl):
        return "d"
    return ""


def material_of(text):
    t = text.lower()
    eco = r"еко[\s-]?"
    if re.search(r"натуральн\w*\s+(лак\w*\s+)?шкір", t): return "натуральна шкіра"
    if re.search(r"натуральн\w*\s+замш", t): return "натуральна замша"
    if re.search(eco + r"замш", t): return "еко-замша"
    if re.search(r"\bлак", t): return "лак"
    if re.search(eco + r"(шкір|кож)", t): return "еко-шкіра"
    if re.search(r"замш", t): return "замша"
    if re.search(r"шкір", t): return "шкіра"
    if re.search(r"текстил|трикотаж|сітк", t): return "текстиль"
    return ""


def heel_of(text, name=""):
    m = re.search(r"(\d{1,2}(?:[.,]\d)?)\s*см", name)
    if m and 1 <= float(m.group(1).replace(",", ".")) <= 15:
        return float(m.group(1).replace(",", "."))
    t = text.lower().replace(",", ".")
    if re.search(r"без\s+каблук|плоск\w*\s+підошв", t):
        return 0
    pats = [r"(?:каблук\w*|підбор\w*|шпильк\w*)[^\d\n]{0,14}(\d{1,2}(?:\.\d)?)\s*см",
            r"(\d{1,2}(?:\.\d)?)\s*см\s*(?:каблук|підбор|кабл)"]
    for p in pats:
        m = re.search(p, t)
        if m:
            v = float(m.group(1))
            if 0 <= v <= 20:
                return v
    return None


def parse_size(txt):
    txt = (txt or "").strip()
    m = re.match(r"(\d{2})", txt)
    if not m:
        return None, None
    size = int(m.group(1))
    if not 33 <= size <= 46:
        return None, None
    cm = re.search(r"(\d{2}(?:[.,]\d)?)\s*см", txt) or re.search(r"-\s*(\d{2}(?:[.,]\d)?)", txt[2:])
    cm = cm.group(1).replace(",", ".") if cm else None
    if cm and "." not in cm:
        cm += ".0"
    return size, cm


# Для моделей сторонніх постачальників (не MyDrop) зі знижкою (є <oldprice>):
# у вигрузці ціна зі знижкою без вашої націнки, тому додаємо її тут.
SALE_MARKUP = 0.25  # +25% до ціни зі знижкою

# Постачальники, у яких у вигрузці стоїть дроп-ціна: додаємо вашу націнку в гривнях.
# Ключ — назва постачальника малими літерами, лише букви й цифри ("Ledi_shoes" -> "ledishoes").
SUPPLIER_MARKUP = {"ledishoes": 500}
SUPPLIER_PARAM = re.compile(r"постачальн|поставщ|продав|supplier|vendor|seller|магазин", re.I)
# Як впізнати постачальника за артикулом, якщо його назви немає у вигрузці.
# Ledi_shoes: у вигрузці перед кодом постачальника стоїть "700" (307 -> 700307, 20 -> 70020).
SUPPLIER_ART = {"ledishoes": re.compile(r"^700\d{1,3}(?:-\d{2})?$")}
MARKUP_HITS = {}


def supplier_markup(o):
    names = [o.findtext("vendor") or ""]
    names += [p.text or "" for p in o.iter("param") if SUPPLIER_PARAM.search(p.get("name") or "")]
    for n in names:
        key = re.sub(r"[^0-9a-zа-яіїєґ]", "", n.lower())
        if key in SUPPLIER_MARKUP:
            return key, SUPPLIER_MARKUP[key]
    art = (o.findtext("vendorCode") or "").strip()
    for key, pat in SUPPLIER_ART.items():
        if pat.match(art) and key in SUPPLIER_MARKUP:
            return key, SUPPLIER_MARKUP[key]
    return None, 0


SIZE_PARAM = re.compile(r"розм|разм|size", re.I)
FOOT_PARAM = re.compile(r"довжин|длин|устілк|стельк|стоп", re.I)
COLOR_PARAM = re.compile(r"колір|цвет|color", re.I)


def offer_sizes(off):
    """Повертає [(розмір, см)] для пропозиції. Якщо є параметр «Розмір», беремо лише його."""
    prms = list(off.iter("param"))
    named = [p for p in prms if SIZE_PARAM.search(p.get("name") or "")]
    foot = next((p.text for p in prms if FOOT_PARAM.search(p.get("name") or "") and p.text), None)
    out = []
    for prm in (named or prms):
        s, cm = parse_size(prm.text)
        if s is None:
            continue
        if not cm and foot:
            m = re.search(r"\d{2}(?:[.,]\d)?", foot)
            if m:
                cm = m.group(0).replace(",", ".")
                cm = cm if "." in cm else cm + ".0"
        out.append((s, cm))
    return out


def build(root, prefix=""):
    cats = {c.get("id"): (c.text or "").strip() for c in root.iter("category")}
    groups = OrderedDict()
    for o in root.iter("offer"):
        groups.setdefault(prefix + (o.get("group_id") or o.get("id")), []).append(o)

    products = []
    for gid, offers in groups.items():
        o = offers[0]
        try:
            price = float(o.findtext("price") or 0)
        except ValueError:
            price = 0
        if price <= 0:
            continue
        try:
            old = float(o.findtext("oldprice") or 0)
        except ValueError:
            old = 0
        sup, extra = supplier_markup(o)
        if extra:
            price += extra
            old = old + extra if old else 0
            MARKUP_HITS[sup] = MARKUP_HITS.get(sup, 0) + 1
        if prefix and old > price:
            price = float(round(price * (1 + SALE_MARKUP) / 10) * 10)  # округлюємо до 10 ₴
        old = old if old * 0.95 >= price else 0  # знижку менше 5% не показуємо
        raw = (o.findtext("name") or "").strip()
        name, code = clean_name(raw)
        base = gid[len(prefix):]
        if base.isdigit():  # назва на кшталт «Уггі … 11591», де число — код групи
            name = re.sub(r"\s*\b" + base + r"\b\s*$", "", name).strip() or name
        if not name:
            continue
        cat = cats.get(o.findtext("categoryId"), "")
        desc = clean_desc(o.findtext("description") or "")
        typ = classify(raw, cat)
        text = raw + "\n" + desc
        color_line = re.search(r"колір\s*[:\-–]?\s*([^\n,.;]+)", desc, re.I)
        color = find_first(COLORS, raw) or (find_first(COLORS, color_line.group(1)) if color_line else "")
        if not color:
            cp = next((p.text for p in o.iter("param") if COLOR_PARAM.search(p.get("name") or "") and p.text), "")
            color = find_first(COLORS, cp) if cp else ""
        pics = [p.text.strip().replace(PIC_PREFIX, "") for p in o.findall("picture") if p.text and p.text.strip()][:8]

        sizes = {}
        for off in offers:
            for s, cm in offer_sizes(off):
                try:
                    q = int(float(off.findtext("quantity_in_stock") or 0))
                except ValueError:
                    q = 0
                ok = q > 0 or (off.findtext("available") or off.get("available") or "").strip() == "true"
                n = q if q > 0 else (5 if ok else 0)  # кількість пар; 5 = «є, кількість невідома»
                prev = sizes.get(s)
                sizes[s] = [s, cm or (prev[1] if prev else None), max(n, prev[2] if prev else 0)]
        if not sizes:
            continue

        products.append({
            "i": gid,
            "a": re.sub(r"-\d{2}$", "", (o.findtext("vendorCode") or "").strip()) or code or gid,
            "n": name,
            "t": typ,
            "s": season_of(raw, cat, desc, typ),
            "c": color,
            "m": material_of(text),
            "h": heel_of(text, raw),
            "p": int(price) if price.is_integer() else price,
            **({"o": int(old) if old.is_integer() else old} if old else {}),
            "g": pics,
            "z": [sizes[k] for k in sorted(sizes)],
            "d": desc,
        })

    products.sort(key=lambda p: int(re.sub(r"\D", "", p["i"]) or 0), reverse=True)
    return products


SITE = "https://vzuttyanavusoti.com"
# Звідки товар: "own" — ваша таблиця, "tapuli" — фабрика TAPULI, "mydrop" — постачальники MyDrop.
# Товари з цього списку не потрапляють у фід Google Покупок (на сайті вони лишаються).
GOOGLE_EXCLUDE = {"tapuli"}

# Ваші хіти — артикули (як у вигрузці чи таблиці). Разом з вашими товарами й знижками
# вони отримують у фіді мітку custom_label_2 = "focus": реклама Google показує лише їх.
HITS = """
52290, vn-003, 238, 700305, 234, 12468, 10895, 3967, 3966, 3965, 3960, 3959, 3955, 3950, 3949, 3948,
3946, 3945, 3944, 3940, 3926, 3927, 3989, 3870, 3861, 232, 197, 171, 172, 166, 12477, 12476, 12475,
70036, 12462, 5163, 12459, 230, 5165-1, 12454, 700298, 700299, 12452, 5175, 700296, 5187,
# + 75 зимових і демісезонних моделей MyDrop (чоботи, черевики, уггі, ботфорти, ботильйони, козаки;
#   1500–4500 ₴, найбільше розмірів у наявності) — додано 21.11.2026, щоб реклама мала з чого вибирати
10904, 5260, 9845-1, 5181-1, 5184, 5454-1, 5453-1, 5403, 5404, 5402, 5360, 5359-1, 5262, 3469, 10905, 10899-1, 10838, 10822, 10804-1, 10750-1, 9850, 9849, 9788-1, 9764, 9664, 5231, 5228, 10927, 10923, 10870, 10869, 8946, 10506, 9749, 7146, 9682, 9660-1, 9589, 8753, 8748, 3491, 10780, 2250, 10779, 9839, 2978, 10818, 3451, 3452, 3453, 3489, 3074, 3969, 5173, 3938, 3033, 5174, 5172, 3954, 3528, 9861-1, 8809, 9474, 3964, 5221, 5220, 5218, 5217, 5254, 5252, 3493, 5157, 5156, 3947, 3442
"""
HIT_SET = {x.strip().lower() for x in re.split(r"[,\s]+", re.sub(r"#.*", "", HITS)) if x.strip()}


def source_of(p):
    i = str(p["i"])
    return "own" if i.startswith("my") else "tapuli" if i.startswith("ta") else "mydrop"
GCAT = {"Аксесуари та догляд": "Apparel & Accessories > Shoe Accessories"}


SEASON_WORD = {"w": "зимові", "d": "демісезонні", "s": "літні"}
BOOT_TYPES = {"Чоботи", "Черевики", "Ботильйони", "Ботфорти", "Козаки", "Уггі та дутики", "Кросівки та кеди"}


def google_title(p):
    """Назва для Google: тип + «жіночі» + сезон + решта назви + матеріал + колір + розміри —
    саме ці слова люди вводять у пошук («чоботи жіночі зимові шкіряні чорні 38»)."""
    t = re.sub(r"\s+", " ", str(p["n"])).strip(" ,.")
    t = re.sub(r"^[\w-]*\d[\w-]*\s+", "", t)  # артикул на початку назви — прибираємо
    head, _, rest = t.partition(" ")
    add = []
    if p["t"] not in ("Аксесуари та догляд", "Інше") and "жіноч" not in t.lower():
        add.append("жіночі")
    season = SEASON_WORD.get(p.get("s"))
    if (season and p["t"] in BOOT_TYPES and season[:5] not in t.lower()
            and not (p.get("s") == "w" and "зим" in t.lower())
            and not (p.get("s") == "d" and "демі" in t.lower())):
        add.append(season)
    if head.lower() in ("жіночі", "жіноче", "жіночий"):
        t = " ".join([head] + add[1:] + ([rest] if rest else []))
    else:
        t = " ".join([head] + add + ([rest] if rest else []))
    parts = [t]
    stem = lambda w: w.lower()[:max(3, len(w) - 2)]
    m = (p.get("m") or "").strip()
    if m and stem(re.split(r"[\s-]+", m)[-1]) not in t.lower():
        parts.append(m)
    c = (p.get("c") or "").strip().lower()
    if c and stem(c.split()[0]) not in t.lower():
        parts.append(re.sub(r"(ий|ій)$", "і", c) if " " not in c else c)
    sizes = [z[0] for z in p["z"] if z[2]]
    nums = [s for s in sizes if str(s).replace(".", "").isdigit()]
    if nums:
        lo, hi = min(nums, key=float), max(nums, key=float)
        parts.append(f"р. {lo}" if lo == hi else f"р. {lo}-{hi}")
    return ", ".join(parts)


def google_feed(products, path):
    """Фід товарів для Google Merchant Center (google.xml): моделі в наявності."""
    from xml.sax.saxutils import escape as x
    pic = lambda f: f if re.match(r"https?://", f) else PIC_PREFIX + f
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<rss version="2.0" xmlns:g="http://base.google.com/ns/1.0"><channel>',
           "<title>Взуття на Висоті</title>", f"<link>{SITE}/</link>",
           "<description>Жіноче взуття</description>"]
    for p in products:
        if not p["g"] or not p["p"]:
            continue
        src = source_of(p)
        if src in GOOGLE_EXCLUDE:  # ці товари на сайті є, але в рекламу Google не йдуть
            continue
        title = google_title(p)
        desc = p["d"] or title
        link = f"{SITE}/m/{p['i']}"
        cat = GCAT.get(p["t"], "Apparel & Accessories > Shoes")
        common = (
            f"<g:title>{x(title[:150])}</g:title>"
            f"<g:description>{x(desc[:4900])}</g:description>"
            f"<g:link>{x(link)}</g:link>"
            f"<g:image_link>{x(pic(p['g'][0]))}</g:image_link>"
            + "".join(f"<g:additional_image_link>{x(pic(u))}</g:additional_image_link>" for u in p["g"][1:8])
            + (f"<g:price>{p['o']} UAH</g:price><g:sale_price>{p['p']} UAH</g:sale_price>" if p.get("o") else f"<g:price>{p['p']} UAH</g:price>")
            + "<g:condition>new</g:condition>"
            "<g:brand>Взуття на Висоті</g:brand><g:identifier_exists>no</g:identifier_exists>"
            f"<g:google_product_category>{x(cat)}</g:google_product_category>"
            f"<g:product_type>{x(p['t'] or 'Взуття')}</g:product_type>"
            "<g:gender>female</g:gender><g:age_group>adult</g:age_group>"
            + f"<g:custom_label_0>{src}</g:custom_label_0>"
            + ("<g:custom_label_1>sale</g:custom_label_1>" if p.get("o") else "")
            + ("<g:custom_label_2>focus</g:custom_label_2>"
               if (src == "own" or p.get("o") or str(p["a"]).strip().lower() in HIT_SET) else "")
            + (f"<g:color>{x(p['c'])}</g:color>" if p["c"] else "")
            + (f"<g:material>{x(p['m'])}</g:material>" if p["m"] else "")
        )
        sizes = [str(z[0]) for z in p["z"] if z[2]]
        if not sizes:
            continue
        out.append(
            f"<item><g:id>{x(str(p['i']))}</g:id>{common}"
            "<g:availability>in_stock</g:availability></item>")
    out.append("</channel></rss>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--feed", default=os.environ.get("FEED_URL"))
    ap.add_argument("--template", default="template.html")
    ap.add_argument("--out", default="index.html")
    a = ap.parse_args()
    if not a.feed:
        sys.exit("Вкажіть --feed або змінну FEED_URL")
    # Основна вигрузка обовʼязкова; додаткові (FEED_URL2, FEED_URL3) — якщо задані.
    # Якщо додаткова тимчасово недоступна, сайт оновлюється з рештою.
    extra = [os.environ.get(k) for k in ("FEED_URL2", "FEED_URL3", "FEED_URL4")]
    merged = {}
    status = []
    for i, src in enumerate([a.feed] + [u for u in extra if u and u.strip()]):
        try:
            host = re.sub(r"^www\.", "", urllib.parse.urlparse(src.strip()).netloc)
            prefix = "" if (i == 0 or "mydrop" in host or not host) else re.sub(r"[^a-z]", "", host)[:2]
            if is_sheet(src):
                part = build_sheet(read_bytes(src.strip()))
                SHEET_REPORT["photo_errors"] = localize_pics(part, os.path.dirname(os.path.abspath(a.out)))
            else:
                part = build(load_feed(src.strip()), prefix)
        except Exception as e:
            if i == 0:
                raise
            print(f"Додаткова вигрузка {i + 1} недоступна: {e}", file=sys.stderr)
            status.append({"feed": i + 1, "error": str(e)[:200]})
            continue
        print(f"Вигрузка {i + 1}: {len(part)} моделей")
        status.append({"feed": i + 1, "models": len(part), **({"sheet": dict(SHEET_REPORT)} if is_sheet(src) else {}), **({"markup": dict(MARKUP_HITS)} if MARKUP_HITS else {})})
        MARKUP_HITS.clear()
        # Кожна вигрузка вже відсортована від нових; перемішуємо їх рівномірно,
        # щоб новинки всіх постачальників були вгорі каталогу.
        own = is_sheet(src)  # ваші власні товари — завжди першими в каталозі
        for n, p in enumerate(part):
            if p["i"] not in merged:
                merged[p["i"]] = ((-1 + n / 1e6) if own else (n + 0.5) / len(part), i, p)
    products = [p for _, _, p in sorted(merged.values(), key=lambda t: (t[0], t[1]))]
    kyiv = datetime.now(timezone.utc) + timedelta(hours=3)
    updated = kyiv.strftime("%d.%m.%Y, %H:%M")
    with open(os.path.join(os.path.dirname(os.path.abspath(a.out)), "status.json"), "w", encoding="utf-8") as f:
        json.dump({"updated": updated, "feeds": status}, f, ensure_ascii=False, indent=1)
    outdir = os.path.dirname(os.path.abspath(a.out))
    # Каталог — окремим файлом data.json (без описів, щоб був легшим);
    # повний запис кожної моделі з описом — у d/<id>.json (відкривається миттєво з реклами).
    lite = {"updated": updated, "products": [{k: v for k, v in p.items() if k != "d"} for p in products]}
    body = json.dumps(lite, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(outdir, "data.json"), "w", encoding="utf-8") as f:
        f.write(body)
    ver = hashlib.sha1(body.encode("utf-8")).hexdigest()[:10]
    ddir = os.path.join(outdir, "d")
    os.makedirs(ddir, exist_ok=True)
    dkeep = set()
    for p in products:
        fn = re.sub(r"[^0-9A-Za-z_-]", "_", str(p["i"])) + ".json"
        dkeep.add(fn)
        with open(os.path.join(ddir, fn), "w", encoding="utf-8") as f:
            json.dump(p, f, ensure_ascii=False, separators=(",", ":"))
    for fn in os.listdir(ddir):
        if fn not in dkeep:
            os.remove(os.path.join(ddir, fn))
    js = json.dumps({"updated": updated, "src": f"/data.json?v={ver}"}, ensure_ascii=False)
    with open(a.template, encoding="utf-8") as f:
        tpl = f.read()
    assert "/*__DATA__*/null" in tpl, "У шаблоні немає мітки /*__DATA__*/null"
    html_out = tpl.replace("/*__DATA__*/null", js)
    # Окремий маленький файл на кожну модель (p/<id>.json): сервер оформлення
    # замовлень читає лише потрібні, а не весь каталог (ліміт процесора Cloudflare).
    pdir = os.path.join(os.path.dirname(os.path.abspath(a.out)), "p")
    os.makedirs(pdir, exist_ok=True)
    keep = {"_ok.json"}
    for p in products:
        fn = re.sub(r"[^0-9A-Za-z_-]", "_", str(p["i"])) + ".json"
        keep.add(fn)
        rec = [p["p"], p["a"], ", ".join(x for x in (p["n"], p["c"]) if x), [z[0] for z in p["z"] if z[2]]]
        with open(os.path.join(pdir, fn), "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(pdir, "_ok.json"), "w", encoding="utf-8") as f:
        f.write('{"ok":1}')
    for fn in os.listdir(pdir):
        if fn not in keep:
            os.remove(os.path.join(pdir, fn))
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(html_out)
    google_feed(products, os.path.join(os.path.dirname(os.path.abspath(a.out)), "google.xml"))
    in_stock = sum(1 for p in products if any(z[2] for z in p["z"]))
    print(f"Готово: {len(products)} моделей, в наявності {in_stock} -> {a.out}")


if __name__ == "__main__":
    main()
