#!/usr/bin/env python3
"""Рыночный дайджест -> Telegram. Только стандартная библиотека, без ИИ и платных API.

Переменные окружения:
  TG_BOT_TOKEN, TG_CHAT_ID   - обязательны для отправки
  DRY_RUN=1                  - не отправлять, а напечатать в консоль
  NEWS_LIMIT=8               - сколько новостей показывать
Запуск:  python digest.py            (боевой)
         python digest.py --demo     (демо-данные, без интернета, печатает в консоль)
"""
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

MSK = ZoneInfo("Europe/Moscow")
UA = {"User-Agent": "Mozilla/5.0 (compatible; MarketDigestBot/1.0)"}

# Даты заседаний ЦБ по ключевой ставке. Обновляй по календарю на cbr.ru.
# Берётся ближайшая будущая дата; если список исчерпан, строка про заседание не выводится.
CBR_MEETINGS = [date(2026, 10, 23)]

# Новостные ленты (RSS). Ломается лента - просто пропускается.
FEEDS = [
    ("Банк России", "https://www.cbr.ru/rss/RssPress"),
    ("РБК", "https://rssexport.rbc.ru/rbcnews/news/30/full.rss"),
    ("Интерфакс", "https://www.interfax.ru/rss.asp"),
    ("Коммерсантъ", "https://www.kommersant.ru/RSS/news.xml"),
]
NEWS_KEYWORDS = re.compile(
    r"ключев|ставк|ЦБ\b|Банк[а-я]* России|рубл|курс|нефт|brent|баррел|офз|облигац|инфляц|"
    r"фрс|доллар|евро|юан|санкц|мосбирж|ормуз|opec|опек",
    re.IGNORECASE,
)

# Индексы облигаций Мосбиржи: (тикер, короткая подпись). Редактируй список под себя.
# Тикеры проверены на iss.moex.com. Если тикер не ответит или его данные устарели
# (>10 дней), строка просто пропускается, а в логе будет [warn].
# TR = индекс полной доходности (с учётом купонов); 1г/3г/5+ = сегменты по сроку (дюрации).
BOND_INDICES = [
    ("RGBI", "RGBI (цены)"),
    ("RGBITR", "RGBI TR"),
    ("RUGBITR1Y", "ОФЗ TR 1г"),
    ("RUGBITR3Y", "ОФЗ TR 3г"),
    ("RUGBITR5+", "ОФЗ TR 5+"),
    ("RUCBTRNS", "Корп. TR"),
    ("RUCBTR3YNS", "Корп. TR 3г"),
    ("RUCBTR5YNS", "Корп. TR 5г"),
    ("RUMBTRNS", "Муниц. TR"),
]
# Индексы акций: (тикер, короткая подпись)
EQUITY_INDICES = [
    ("IMOEX", "IMOEX"),
    ("MCFTR", "MCFTR (с див.)"),
    ("RTSI", "RTSI ($)"),
    # ("MOEXOG", "Нефть и газ"), ("MOEXFN", "Финансы"), ("MOEXMM", "Металлы"),
]
# Точки кривой бескупонной доходности ОФЗ (G-curve), годы (на MOEX есть 0.25 ... 20)
ZCYC_POINTS = (1, 2, 3, 5, 7, 10)
MAX_INDEX_AGE_DAYS = 10

WEEKDAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
          "сентября", "октября", "ноября", "декабря"]


# ---------------------------------------------------------------- утилиты

def log(msg):
    print(msg, file=sys.stderr)


def http_get(url, data=None, headers=None, retries=3, timeout=25):
    h = dict(UA)
    if headers:
        h.update(headers)
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            # 4xx (кроме 429) повторять бессмысленно
            if 400 <= e.code < 500 and e.code != 429:
                raise
            last = e
            time.sleep(1.5 * (i + 1))
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (i + 1))
    raise last


def safe(name, fn, *args):
    try:
        return fn(*args)
    except Exception as e:  # noqa: BLE001
        log(f"[warn] {name}: {type(e).__name__}: {e}")
        return None


def ln(el):
    return el.tag.split("}")[-1]


def pct(new, old):
    if new is None or not old:
        return None
    return (new / old - 1) * 100


def fnum(x, d=2):
    return f"{x:,.{d}f}".replace(",", " ").replace(".", ",")


def fpct(x, d=2):
    if x is None:
        return "н/д"
    if round(x, d) == 0:
        return f"{0:.{d}f}%".replace(".", ",")
    s = f"{abs(x):.{d}f}".replace(".", ",")
    return f"{'+' if x > 0 else '−'}{s}%"


def arrow(x, d=2):
    if x is None or round(x, d) == 0:
        return "▬"
    return "▲" if x > 0 else "▼"


def e(s):
    return html.escape(str(s), quote=False)


# ---------------------------------------------------------------- источники

def cbr_rates(d):
    url = "https://www.cbr.ru/scripts/XML_daily.asp?date_req=" + d.strftime("%d/%m/%Y")
    root = ET.fromstring(http_get(url))
    out = {}
    for v in root.findall("Valute"):
        code = v.findtext("CharCode")
        nominal = float(v.findtext("Nominal").replace(",", "."))
        value = float(v.findtext("Value").replace(",", ".")) / nominal
        out[code] = value
    return root.attrib.get("Date"), out


def cbr_currencies(today):
    """Курсы ЦБ на сегодня и предыдущий опубликованный курс (для изменения за день)."""
    cur_date, cur = cbr_rates(today)
    prev = {}
    for back in range(1, 7):
        pd_, p = cbr_rates(today - timedelta(days=back))
        if pd_ != cur_date:
            prev = p
            break
    return {
        "date": cur_date,
        "rates": {c: (cur.get(c), prev.get(c)) for c in ("USD", "EUR", "CNY")},
    }


def cbr_soap(method, from_d, to_d):
    body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soap12:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
        'xmlns:soap12="http://www.w3.org/2003/05/soap-envelope"><soap12:Body>'
        f'<{method} xmlns="http://web.cbr.ru/">'
        f"<fromDate>{from_d:%Y-%m-%d}</fromDate><ToDate>{to_d:%Y-%m-%d}</ToDate>"
        f"</{method}></soap12:Body></soap12:Envelope>"
    ).encode("utf-8")
    raw = http_get(
        "https://www.cbr.ru/DailyInfoWebServ/DailyInfo.asmx",
        data=body,
        headers={"Content-Type": "application/soap+xml; charset=utf-8"},
    )
    return ET.fromstring(raw)


def cbr_key_rate(today):
    root = cbr_soap("KeyRateXML", today - timedelta(days=60), today)
    rows = []
    for el in root.iter():
        if ln(el) == "KR":
            dt = rate = None
            for ch in el:
                if ln(ch) == "DT":
                    dt = ch.text[:10]
                elif ln(ch) == "Rate":
                    rate = float(ch.text.replace(",", "."))
            if dt and rate is not None:
                rows.append((dt, rate))
    if not rows:
        raise ValueError("ключевая ставка: пустой ответ")
    rows.sort()
    return rows[-1][1]


def cbr_ruonia(today):
    root = cbr_soap("RuoniaXML", today - timedelta(days=14), today)
    rows = []
    for parent in root.iter():
        d0 = ruo = None
        for ch in parent:
            if ln(ch) == "D0":
                d0 = ch.text[:10]
            elif ln(ch) == "ruo":
                ruo = float(ch.text.replace(",", "."))
        if d0 and ruo is not None:
            rows.append((d0, ruo))
    if not rows:
        raise ValueError("RUONIA: пустой ответ")
    rows.sort()
    return rows[-1][1], rows[-1][0]


def moex_zcyc():
    """Кривая бескупонной доходности ОФЗ (последние данные): {срок_в_годах: доходность_%}."""
    url = "https://iss.moex.com/iss/engines/stock/zcyc.json?iss.meta=off&iss.only=yearyields"
    blk = json.loads(http_get(url))["yearyields"]
    rows = [dict(zip(blk["columns"], r)) for r in blk["data"]]
    if not rows:
        raise ValueError("G-curve: нет данных")
    last = max((r["tradedate"], r.get("tradetime") or "") for r in rows)
    out = {}
    for r in rows:
        if (r["tradedate"], r.get("tradetime") or "") == last:
            out[float(r["period"])] = float(r["value"])
    return {"date": last[0], "points": out}


def moex_index(secid):
    """Последнее значение индекса: (значение, изменение_%, дата_торгов 'YYYY-MM-DD')."""
    url = ("https://iss.moex.com/iss/engines/stock/markets/index/securities/"
           f"{urllib.parse.quote(secid, safe='')}.json?iss.meta=off&iss.only=marketdata")
    md = json.loads(http_get(url))["marketdata"]
    for row in md["data"]:
        r = dict(zip(md["columns"], row))
        val = r.get("CURRENTVALUE") or r.get("LASTVALUE")
        if val:
            return float(val), r.get("LASTCHANGEPRC"), r.get("TRADEDATE")
    raise ValueError(f"MOEX {secid}: нет данных")


def moex_rusfar():
    """RUSFAR по срокам (площадка MMIX): {'ON': %, '1W': %, '2W': %, '1M': %, '3M': %, 'date': ...}."""
    ids = urllib.parse.quote("RUSFAR,RUSFAR1W,RUSFAR2W,RUSFAR1M,RUSFAR3M", safe="")
    url = ("https://iss.moex.com/iss/engines/stock/markets/index/boards/MMIX/securities.json"
           f"?iss.meta=off&iss.only=marketdata&securities={ids}&marketdata.columns=SECID,CURRENTVALUE,TRADEDATE")
    md = json.loads(http_get(url))["marketdata"]
    by = {r[0]: (r[1], r[2]) for r in md["data"] if r[1] is not None}
    names = {"RUSFAR": "ON", "RUSFAR1W": "1W", "RUSFAR2W": "2W", "RUSFAR1M": "1M", "RUSFAR3M": "3M"}
    out = {names[k]: float(v[0]) for k, v in by.items() if k in names}
    if "ON" not in out:
        raise ValueError("RUSFAR: нет данных")
    out["date"] = by["RUSFAR"][1]
    return out


def yahoo(symbol):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=5d"
    res = json.loads(http_get(url))["chart"]["result"][0]
    price = res["meta"]["regularMarketPrice"]
    closes = [c for c in res["indicators"]["quote"][0]["close"] if c is not None]
    prev = closes[-2] if len(closes) >= 2 else res["meta"].get("previousClose")
    return price, prev


def coingecko():
    url = ("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum"
           "&vs_currencies=usd&include_24hr_change=true")
    j = json.loads(http_get(url))
    return {
        "BTC": (j["bitcoin"]["usd"], j["bitcoin"].get("usd_24h_change")),
        "ETH": (j["ethereum"]["usd"], j["ethereum"].get("usd_24h_change")),
    }


def news(limit):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=26)
    per_source, seen, out = {}, set(), []
    for name, url in FEEDS:
        try:
            root = ET.fromstring(http_get(url, retries=2, timeout=15))
        except Exception as ex:  # noqa: BLE001
            log(f"[warn] rss {name}: {ex}")
            continue
        for it in root.iter("item"):
            title = (it.findtext("title") or "").strip()
            link = (it.findtext("link") or "").strip()
            if not title or not link or title in seen:
                continue
            pub = it.findtext("pubDate")
            if pub:
                try:
                    dt = parsedate_to_datetime(pub)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if dt < cutoff:
                        continue
                except Exception:  # noqa: BLE001
                    pass
            # у ленты ЦБ фильтр по словам не нужен - там и так профильное
            if name != "Банк России" and not NEWS_KEYWORDS.search(title):
                continue
            if per_source.get(name, 0) >= 3:
                continue
            per_source[name] = per_source.get(name, 0) + 1
            seen.add(title)
            out.append((name, title, link))
    return out[:limit]


# ---------------------------------------------------------------- сбор

def fetch_indices(spec):
    """[(тикер, подпись)] -> [(тикер, подпись, значение, изменение_%)] только по свежим и ответившим."""
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(lambda sp: safe(f"moex_index {sp[0]}", moex_index, sp[0]), spec))
    today, out = datetime.now(MSK).date(), []
    for (sid, label), r in zip(spec, results):
        if not r:
            continue
        val, chg, td = r
        try:
            if td and (today - date.fromisoformat(td)).days > MAX_INDEX_AGE_DAYS:
                log(f"[warn] {sid}: данные устарели (последняя дата {td}), пропускаю")
                continue
        except ValueError:
            pass
        out.append((sid, label, val, chg))
    return out


def collect(today):
    d = {}
    d["cur"] = safe("cbr_currencies", cbr_currencies, today)
    d["key_rate"] = safe("cbr_key_rate", cbr_key_rate, today)
    d["ruonia"] = safe("cbr_ruonia", cbr_ruonia, today)
    d["rusfar"] = safe("moex_rusfar", moex_rusfar)
    d["zcyc"] = safe("moex_zcyc", moex_zcyc)
    d["bonds"], d["equity"] = fetch_indices(BOND_INDICES), fetch_indices(EQUITY_INDICES)
    d["imoex"] = next((v for sid, _l, v, _c in d["equity"] if sid == "IMOEX"), None)
    d["brent"] = safe("yahoo_brent", yahoo, "BZ=F")
    d["gold"] = safe("yahoo_gold", yahoo, "GC=F")
    d["crypto"] = safe("coingecko", coingecko)
    d["news"] = safe("news", news, int(os.environ.get("NEWS_LIMIT", "8"))) or []
    return d


def demo_data():
    """Демо-данные для проверки вёрстки (значения реальные на 14.10.2026, но фиксированные)."""
    return {
        "cur": {"date": "14.10.2026", "rates": {
            "USD": (83.8675, 83.9175), "EUR": (93.7723, 94.0364), "CNY": (11.74, 11.71)}},
        "key_rate": 14.0,
        "ruonia": (13.73, "2026-10-13"),
        "rusfar": {"ON": 13.83, "1W": 13.87, "2W": 13.91, "1M": 13.96, "3M": 14.05, "date": "2026-10-14"},
        "zcyc": {"date": "2026-10-14", "points": {1.0: 14.9, 2.0: 15.4, 3.0: 15.8, 5.0: 16.2, 7.0: 16.4, 10.0: 16.5}},
        "bonds": [
            ("RGBI", "RGBI (цены)", 111.35, 0.19), ("RGBITR", "RGBI TR", 767.35, 0.22),
            ("RUGBITR1Y", "ОФЗ TR 1г", 330.29, -0.05), ("RUGBITR3Y", "ОФЗ TR 3г", 850.68, 0.0),
            ("RUGBITR5+", "ОФЗ TR 5+", 643.77, 0.32), ("RUCBTRNS", "Корп. TR", 211.35, 0.01),
            ("RUCBTR3YNS", "Корп. TR 3г", 203.78, 0.02), ("RUCBTR5YNS", "Корп. TR 5г", 188.81, 0.03),
            ("RUMBTRNS", "Муниц. TR", 203.35, 0.0),
        ],
        "equity": [
            ("IMOEX", "IMOEX", 2419.17, 0.01), ("MCFTR", "MCFTR (с див.)", 6971.78, 1.4),
            ("RTSI", "RTSI ($)", 907.8, -0.08),
        ],
        "imoex": 2419.17,
        "brent": (106.60, 105.65),
        "gold": (4086.0, 4156.0),
        "crypto": {"BTC": (83400.0, 0.35), "ETH": (2506.0, 0.3)},
        "news": [
            ("Банк России", "Банк России опубликовал мониторинг предприятий", "https://www.cbr.ru/"),
            ("РБК", "Нефть Brent подорожала на фоне угроз судоходству в Ормузском проливе", "https://www.rbc.ru/"),
            ("Интерфакс", "Инфляция в США в сентябре ускорилась до 3,7%", "https://www.interfax.ru/"),
        ],
    }


# ---------------------------------------------------------------- вёрстка

def next_meeting(today):
    for m in sorted(CBR_MEETINGS):
        if m >= today:
            return m
    return None


def chg_txt(ch, d=2):
    """'▲ +0,22%' / '▼ −0,28%' / '▬ 0,00%' / '' если нет данных."""
    if ch is None:
        return ""
    p = fpct(ch, d)
    if p[0] not in "+−":   # ноль: выравниваем по колонке знака
        p = " " + p
    return f"{arrow(ch, d)} {p}"


def table(rows):
    """rows: [(подпись, значение, изменение|None)] -> выровненный моноширинный блок <pre>."""
    w1 = max(len(r[0]) for r in rows)
    w2 = max(len(r[1]) for r in rows)
    w3 = max((len(r[2]) for r in rows if r[2]), default=0)
    out = []
    for label, val, chg in rows:
        line = f"{label:<{w1}}  {val:>{w2}}"
        if w3:
            line += f"  {(chg or ''):>{w3}}"
        out.append(line.rstrip())
    return "<pre>" + e("\n".join(out)) + "</pre>"


def header(emoji, title, note=None):
    return f"{emoji} <b>{title}</b>" + (f"  <i>{e(note)}</i>" if note else "")


def pcts(x):
    return "н/д" if x is None else f"{fnum(x)}%"


def build_key(d, today):
    lines = []
    cur = d.get("cur")
    if cur:
        usd, usd_p = cur["rates"]["USD"]
        eur, eur_p = cur["rates"]["EUR"]
        ch = pct(usd, usd_p)
        if ch is not None and round(ch, 2) < 0:
            icon, title = "🟢", "Рубль укрепляется"
        elif ch is not None and round(ch, 2) > 0:
            icon, title = "🔴", "Рубль слабеет"
        else:
            icon, title = "⚪", "Рубль без изменений"
        txt = f"USD {fnum(usd, 2)} ₽ ({fpct(ch)})"
        if eur:
            txt += f", EUR {fnum(eur, 2)} ₽ ({fpct(pct(eur, eur_p))})"
        lines.append(f"{icon} <b>{e(title)}.</b> {e(txt)}")
    if d.get("brent"):
        p, prev = d["brent"]
        ch = pct(p, prev)
        if ch is not None and ch > 0.3:
            icon, word = "🟢", "дорожает"
        elif ch is not None and ch < -0.3:
            icon, word = "🔴", "дешевеет"
        else:
            icon, word = "⚪", "стабильна"
        lines.append(f"{icon} <b>Нефть {word}.</b> Brent ${fnum(p)} ({fpct(ch, 1)})")
    if d.get("key_rate") is not None:
        nm = next_meeting(today)
        txt = f"Ключевая {fnum(d['key_rate'])}%"
        if nm:
            txt += f" · заседание ЦБ {nm.day} {MONTHS[nm.month - 1]}"
        lines.append(f"🏦 <b>Ставка.</b> {e(txt)}")
    return header("🔑", "КЛЮЧЕВОЕ") + "\n" + ("\n".join(lines) if lines else "нет данных")


def build_fx(d):
    cur = d.get("cur")
    if not cur:
        return header("💱", "ВАЛЮТЫ") + "\nнет данных"
    rows = []
    for code, label in (("USD", "USD/RUB"), ("EUR", "EUR/RUB"), ("CNY", "CNY/RUB")):
        v, p = cur["rates"][code]
        rows.append((label, "н/д" if v is None else f"{fnum(v, 4)} ₽", chg_txt(pct(v, p)) if v else ""))
    return header("💱", "ВАЛЮТЫ", f"курс ЦБ на {cur['date'][:5]}") + "\n" + table(rows)


def build_rates(d):
    rf = d.get("rusfar") or {}
    rows = [("Ключевая ЦБ", pcts(d.get("key_rate")), None)]
    if rf:
        for tenor, label in (("ON", "RUSFAR O/N"), ("1W", "RUSFAR 1W"), ("1M", "RUSFAR 1M"), ("3M", "RUSFAR 3M")):
            if tenor in rf:
                rows.append((label, pcts(rf[tenor]), None))
    else:
        rows.append(("RUSFAR", "н/д", None))
    if d.get("ruonia"):
        v, dt = d["ruonia"]
        rows.append((f"RUONIA ({dt[8:10]}.{dt[5:7]})", pcts(v), None))
    else:
        rows.append(("RUONIA", "н/д", None))
    out = header("🏦", "СТАВКИ", "денежный рынок") + "\n" + table(rows)
    z = d.get("zcyc")
    if z:
        pts = z["points"]
        sel = [(p, pts[float(p)]) for p in ZCYC_POINTS if float(p) in pts]
        if sel:
            half = (len(sel) + 1) // 2
            left, right = sel[:half], sel[half:]
            lines = []
            for i, (p, v) in enumerate(left):
                line = f"{p:>2}г {fnum(v):>6}%"
                if i < len(right):
                    rp, rv = right[i]
                    line += f"    {rp:>2}г {fnum(rv):>6}%"
                lines.append(line)
            dt = z["date"]
            out += "\n" + header("📉", "Кривая ОФЗ", f"G-curve, доходность, на {dt[8:10]}.{dt[5:7]}") \
                + "\n<pre>" + e("\n".join(lines)) + "</pre>"
    return out


def build_bonds(d):
    items = d.get("bonds") or []
    if not items:
        return header("💠", "ИНДЕКСЫ ОБЛИГАЦИЙ", "Мосбиржа") + "\nнет данных"
    rows = [(label, fnum(v), chg_txt(ch)) for _sid, label, v, ch in items]
    return header("💠", "ИНДЕКСЫ ОБЛИГАЦИЙ", "Мосбиржа") + "\n" + table(rows)


def build_markets(d):
    rows = [(label, fnum(v), chg_txt(ch)) for _sid, label, v, ch in (d.get("equity") or [])]
    for key, label in (("brent", "Brent $/барр."), ("gold", "Золото $/унц.")):
        if d.get(key):
            p, prev = d[key]
            rows.append((label, fnum(p, 0 if p > 1000 else 2), chg_txt(pct(p, prev), 1)))
        else:
            rows.append((label, "н/д", ""))
    return header("📈", "АКЦИИ И СЫРЬЁ") + "\n" + table(rows)


def build_crypto(d):
    c = d.get("crypto")
    if not c:
        return header("₿", "КРИПТО") + "\nнет данных"
    rows = [(code, f"${fnum(c[code][0], 0)}", chg_txt(c[code][1], 1)) for code in ("BTC", "ETH")]
    return header("₿", "КРИПТО", "изменение за 24ч") + "\n" + table(rows)


def build_news(d):
    items = d.get("news") or []
    lines = [header("📰", "НОВОСТИ")]
    if not items:
        lines.append("нет свежих новостей по ключевым темам")
    for src, title, link in items:
        lines.append(f'▪️ <a href="{html.escape(link, quote=True)}">{e(title)}</a> <i>· {e(src)}</i>')
    return "\n".join(lines)


def build_blocks(d, now):
    today = now.date()
    head = (f"📊 <b>РЫНОЧНЫЙ ДАЙДЖЕСТ</b>\n"
            f"<i>{WEEKDAYS[now.weekday()]}, {now.day} {MONTHS[now.month - 1]} {now.year} · данные на {now:%H:%M} МСК</i>")
    blocks = [head, build_key(d, today), build_fx(d), build_rates(d), build_bonds(d),
              build_markets(d), build_crypto(d), build_news(d)]
    blocks.append("<i>ℹ️ Курсы ЦБ — официальные, не котировки для сделки. Индексы и RUSFAR — последние значения "
                  "(утром это итог прошлой сессии). TR — индексы полной доходности (с купонами); 1г/3г/5+ — сегменты "
                  "по сроку. Brent и золото — фьючерсы. Справочная информация, не инвестиционная рекомендация.</i>")
    return blocks


def pack(blocks, limit=3900):
    msgs, cur = [], ""
    for b in blocks:
        cand = b if not cur else cur + "\n\n" + b
        if len(cand) > limit and cur:
            msgs.append(cur)
            cur = b
        else:
            cur = cand
    if cur:
        msgs.append(cur)
    return msgs


# ---------------------------------------------------------------- отправка

def tg_send(token, chat_id, text, html_mode=True):
    params = {"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}
    if html_mode:
        params["parse_mode"] = "HTML"
    base = os.environ.get("TG_API_BASE", "https://api.telegram.org")
    try:
        resp = json.loads(http_get(f"{base}/bot{token}/sendMessage",
                                   data=urllib.parse.urlencode(params).encode()))
    except urllib.error.HTTPError as ex:
        try:
            desc = json.loads(ex.read().decode("utf-8", "replace")).get("description", "")
        except Exception:  # noqa: BLE001
            desc = ""
        if html_mode and "parse entities" in desc.lower():
            log(f"[warn] Telegram не принял HTML ({desc}); отправляю простым текстом")
            plain = html.unescape(re.sub(r"<[^>]+>", "", text))
            return tg_send(token, chat_id, plain, html_mode=False)
        hint = ""
        if "chat not found" in desc.lower() or "chat_id" in desc.lower():
            hint = (" Проверь секрет TG_CHAT_ID (только число, без пробелов и кавычек) "
                    "и что в боте нажат Start.")
        raise RuntimeError(f"Telegram ответил {ex.code}: {desc}.{hint}") from None
    if not resp.get("ok"):
        raise RuntimeError(f"Telegram: {resp}")


def main():
    demo = "--demo" in sys.argv
    now = datetime.now(MSK)
    data = demo_data() if demo else collect(now.date())

    core = [data.get("cur"), data.get("key_rate"), data.get("imoex"), data.get("brent"), data.get("crypto")]
    if not demo and not any(core):
        log("Ни один основной источник не ответил - дайджест не отправлен.")
        sys.exit(1)

    msgs = pack(build_blocks(data, now))
    if demo or os.environ.get("DRY_RUN") == "1":
        for m in msgs:
            print(m, "\n" + "-" * 40)
        return

    token = (os.environ.get("TG_BOT_TOKEN") or "").strip()
    chat = (os.environ.get("TG_CHAT_ID") or "").strip()
    if not token:
        log("Секрет TG_BOT_TOKEN пустой или не задан.")
        sys.exit(1)
    if not chat:
        log("Секрет TG_CHAT_ID пустой или не задан.")
        sys.exit(1)
    try:
        for m in msgs:
            tg_send(token, chat, m)
            time.sleep(1)
    except RuntimeError as ex:
        log(f"ОШИБКА ОТПРАВКИ: {ex}")
        sys.exit(1)
    log(f"Отправлено сообщений: {len(msgs)}")


if __name__ == "__main__":
    main()
