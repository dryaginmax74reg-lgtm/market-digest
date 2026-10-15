"""Первичные размещения облигаций с bonds-lab.ru/placements (публичная страница, без ключа).

fetch_placements() -> [{name, book (дата сбора заявок, iso), ratings [..], coupon, period, place (iso|None),
                        maturity, volume, note, url}]
"""
import html
import re
from datetime import date

from common import http_get

URL = "https://bonds-lab.ru/placements"
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
                    "url": "https://bonds-lab.ru" + link.group(1) if link else URL})
    return out


def fetch_placements():
    return parse(http_get(URL, retries=2, timeout=30).decode("utf-8", "replace"))
