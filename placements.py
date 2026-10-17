"""Первичные размещения облигаций: основной источник — календарь smart-lab (обновляется ежедневно),
запасной — bonds-lab.ru/placements (бывает, что не обновляется неделями). Публичные страницы, без ключа.

fetch_placements() -> [{name, book (дата сбора заявок, iso|None), ratings [..], coupon, period, place (iso|None),
                        maturity, volume, note, url, src}]
"""
import html
import re
from datetime import date

from common import http_get

URL = "https://bonds-lab.ru/placements"
SL_URL = "https://smart-lab.ru/calendar/bonds/bonds_placement/country_0/"
FIELD = re.compile(r"<span[^>]*>(Купон|Период|Размещение|Погашение|Объем)</span><span[^>]*>(.*?)</span>", re.S)
SPAN = re.compile(r"<span[^>]*>(.*?)</span>", re.S)


def _clean(x):
    return html.unescape(re.sub(r"<!--.*?-->|<[^>]+>", "", x or "")).strip()


def _dmy(s, century="20"):
    m = re.search(r"(\d\d)\.(\d\d)\.(\d{2,4})", s or "")
    if not m:
        return None
    y = m.group(3)
    y = century + y if len(y) == 2 else y
    try:
        return date(int(y), int(m.group(2)), int(m.group(1))).isoformat()
    except ValueError:
        return None


def parse(raw):
    out = []
    for art in raw.split("<article")[1:]:
        art = art.split("</article>")[0]
        name = re.search(r'<h3[^>]*title="([^"]*)"', art)
        if not name:
            continue
        head = art.split("grid grid-cols")[0]                 # шапка карточки: имя, «Сбор до», рейтинги
        spans = [_clean(s) for s in SPAN.findall(head)]
        book = next((_dmy(s) for s in spans if s.startswith("Сбор до")), None)
        ratings = [s for s in spans if s and not s.startswith("Сбор до") and s != "-" and len(s) <= 12]
        f = {k: _clean(v) for k, v in FIELD.findall(art)}
        note = re.search(r'rounded-2xl p-4[^>]*>(.*?)</div>', art, re.S)
        link = re.search(r'href="(/placements/[^"]+)"', art)
        out.append({"name": html.unescape(name.group(1)), "book": book, "ratings": ratings,
                    "coupon": f.get("Купон", ""), "period": f.get("Период", ""), "place": _dmy(f.get("Размещение")),
                    "maturity": f.get("Погашение", ""), "volume": f.get("Объем", ""),
                    "note": re.sub(r"\s+", " ", _clean(note.group(1))) if note else "",
                    "url": "https://bonds-lab.ru" + link.group(1) if link else URL, "src": "bonds-lab"})
    return out


# ---------------------------------------------------------------- smart-lab: «кн. Сегежа Групп-004P-01R, 5 л., от 1 млрд р., ориент. мес. куп.: 21% (YTP 23,14%), оф. 3 м.»
ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
TD = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
FREQ = {"мес.": "месяц", "кв.": "квартал", "п/г": "полгода", "п/н": "полгода", "год.": "год", "1 р/г": "год"}


def _term(t):
    m = re.fullmatch(r"(\d+(?:,\d+)?)\s*(л|г|м|мес)\.?", t.strip())
    if not m:
        return None
    n, u = m.group(1), m.group(2)
    if u in ("м", "мес"):
        return f"{n} мес."
    x = float(n.replace(",", "."))
    w = "года" if "," in n or (x % 10 in (2, 3, 4) and not 12 <= x % 100 <= 14) else ("год" if x % 10 == 1 and x % 100 != 11 else "лет")
    return f"{n} {w}"


def _desc(text):
    """Разбор описания из календаря smart-lab в поля карточки."""
    t = re.sub(r"\s+", " ", text).strip()
    book = t.lower().startswith("кн.")
    if book:
        t = t[3:].strip()
    parts = [x.strip() for x in t.split(",")]
    # запятая бывает внутри чисел («3,5 г.», «23,14%») — склеиваем обратно куски, начинающиеся с цифры после цифры
    merged = []
    for x in parts:
        if len(merged) >= 2 and re.match(r"^\d", x) and re.search(r"(^|\s|\()\d+$", merged[-1]):   # имя (первый кусок) не склеиваем
            merged[-1] += "," + x
        else:
            merged.append(x)
    name = merged[0] if merged else t
    term = volume = coupon = period = ""
    notes = []
    for x in merged[1:]:
        tm = _term(x)
        if tm and not term:
            term = tm
        elif re.search(r"(млрд|млн|тыс)\.?\s*(р|руб|\$|долл|юан|¥|€|евро)", x) and not volume:
            volume = x.replace(" р.", " ₽").replace("р.", "₽")
        elif "куп" in x:
            m = re.search(r"((?:ориент\.\s*)?)(мес\.|кв\.|п/г|п/н|год\.)?\s*куп\.?:?\s*(.*)", x)
            if m:
                period = FREQ.get(m.group(2) or "", "")
                coupon = ("ориентир " if m.group(1) else "") + m.group(3).strip()
            else:
                coupon = x
        elif x:
            x2 = {"оф.": "оферта", "аморт.": "амортизация", "квал.": "для квалов", "суборд.": "субординированная"}.get(x)
            if x2 is None:
                x2 = re.sub(r"^оф\. ", "оферта через ", x)
                x2 = re.sub(r"(\d) м\.$", r"\1 мес.", x2)
                x2 = re.sub(r"номин - инд\. ", "номинал индексируется на ", x2)
            notes.append(x2)
    return {"name": name, "is_book": book, "coupon": coupon, "period": period, "maturity": term, "volume": volume,
            "note": ", ".join(n for n in notes if n)}


def parse_smartlab(raw):
    out = []
    for row in ROW.findall(raw):
        cells = [_clean(c) for c in TD.findall(row)]
        if len(cells) < 2:
            continue
        d = next((c for c in cells if re.fullmatch(r"\d\d\.\d\d\.\d{4}", c)), None)
        if not d:
            continue
        desc = max(cells, key=len)
        if not desc or desc == d:
            continue
        f = _desc(desc)
        iso = _dmy(d)
        out.append({"name": f["name"], "book": iso if f["is_book"] else None, "place": None if f["is_book"] else iso,
                    "ratings": [], "coupon": f["coupon"], "period": f["period"], "maturity": f["maturity"],
                    "volume": f["volume"], "note": f["note"], "url": SL_URL, "src": "smart-lab"})
    return out


def _key(name):
    return re.sub(r"[^a-zа-яё0-9]", "", (name or "").lower().split("-")[0].split(" ")[0])[:8]


def fetch_placements():
    """smart-lab — основной; из bonds-lab добавляем то, чего нет у smart-lab (например, рейтинги и прошедшие сборы)."""
    sl, bl = [], []
    try:
        sl = parse_smartlab(http_get(SL_URL, retries=2, timeout=30).decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        sl = []
    try:
        bl = parse(http_get(URL, retries=2, timeout=30).decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        bl = []
    if not sl and not bl:
        raise RuntimeError("ни smart-lab, ни bonds-lab не ответили")
    keys = {_key(p["name"]) + (p.get("book") or p.get("place") or "") for p in sl}
    return sl + [p for p in bl if _key(p["name"]) + (p.get("book") or p.get("place") or "") not in keys]
