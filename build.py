#!/usr/bin/env python3
"""
Збирає каталог «Взуття на Висоті» з YML-вигрузки MyDrop.

    python build.py --feed vygruzka.xml --template template.html --out index.html
    python build.py --feed "https://backend.mydrop.com.ua/...?..." --template template.html --out index.html

Якщо --feed не вказано, береться змінна оточення FEED_URL.
"""
import argparse, html, json, os, re, sys, urllib.parse, urllib.request
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
GCAT = {"Аксесуари та догляд": "Apparel & Accessories > Shoe Accessories"}


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
        title = p["n"]
        if p["m"] and p["m"].split()[-1] not in title.lower():
            title += ", " + p["m"]
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
    extra = [os.environ.get(k) for k in ("FEED_URL2", "FEED_URL3")]
    merged = {}
    for i, src in enumerate([a.feed] + [u for u in extra if u and u.strip()]):
        try:
            host = re.sub(r"^www\.", "", urllib.parse.urlparse(src.strip()).netloc)
            prefix = "" if (i == 0 or "mydrop" in host or not host) else re.sub(r"[^a-z]", "", host)[:2]
            part = build(load_feed(src.strip()), prefix)
        except Exception as e:
            if i == 0:
                raise
            print(f"Додаткова вигрузка {i + 1} недоступна: {e}", file=sys.stderr)
            continue
        print(f"Вигрузка {i + 1}: {len(part)} моделей")
        for p in part:
            merged.setdefault(p["i"], p)
    products = sorted(merged.values(), key=lambda p: int(re.sub(r"\D", "", p["i"]) or 0), reverse=True)
    kyiv = datetime.now(timezone.utc) + timedelta(hours=3)
    data = {"updated": kyiv.strftime("%d.%m.%Y, %H:%M"), "products": products}
    js = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
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
