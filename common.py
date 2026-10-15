"""Общие функции: загрузка данных, форматирование, отправка в Telegram.
Только стандартная библиотека (matplotlib нужен лишь charts.py / review.py)."""
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

MSK = ZoneInfo("Europe/Moscow")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
ISS = "https://iss.moex.com/iss/"

# ================================================================ НАСТРОЙКИ

# Даты заседаний ЦБ по ключевой ставке. Обновляй по календарю на cbr.ru.
CBR_MEETINGS = [date(2026, 10, 23), date(2026, 12, 18)]

# Код валюты ЦБ для страниц динамики курса
CBR_VAL_ID = {"USD": "R01235", "EUR": "R01239", "CNY": "R01375"}

# Пороги «сильного движения» за день, % (для ставок - п.п.). Используются в дайджесте и алертах.
MOVE_THRESHOLDS = {
    "USD": 1.0, "CNY": 1.0, "EUR": 1.0,
    "IMOEX": 1.5, "RGBITR": 0.5, "RUCBTRNS": 0.3,
    "BRENT": 3.0, "GOLD": 2.0, "BTC": 5.0,
}

FEEDS = [
    ("Банк России", "https://www.cbr.ru/rss/RssPress"),
    ("РБК", "https://rssexport.rbc.ru/rbcnews/news/30/full.rss"),
    ("Интерфакс", "https://www.interfax.ru/rss.asp"),
    ("Коммерсантъ", "https://www.kommersant.ru/RSS/news.xml"),
]
# Макро: ставка, рубль, нефть, инфляция
MACRO_RE = re.compile(
    r"ключев|ставк|ЦБ\b|Банк[а-я]* России|рубл|курс|нефт|brent|баррел|офз|облигац|инфляц|"
    r"фрс|доллар|евро|юан|санкц|мосбирж|ормуз|opec|опек", re.IGNORECASE)
# Денежные события: у кого-то появляются или двигаются крупные деньги
MONEY_RE = re.compile(
    r"дивиденд|\bIPO\b|\bSPO\b|размещ|книг[аиу] заявок|сбор заявок|выкуп|сделк|приобр[её]л|приобрет|"
    r"покупк[аеу] (дол|компан|актив|бизнес)|продаж[аеу] (дол|бизнес|компан|актив)|продаст|купит|"
    r"слияни|поглощ|M&A|аукцион|погашени|оферт", re.IGNORECASE)

WEEKDAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
          "сентября", "октября", "ноября", "декабря"]
MONTHS_NOM = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август",
              "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]

# ================================================================ утилиты


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
            if 400 <= e.code < 500 and e.code != 429:
                raise
            last = e
        except Exception as e:  # noqa: BLE001
            last = e
        time.sleep(1.5 * (i + 1))
    raise last


def safe(name, fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except Exception as e:  # noqa: BLE001
        log(f"[warn] {name}: {type(e).__name__}: {e}")
        return None


def parallel(jobs, workers=6):
    """jobs: {ключ: (функция, *аргументы)} -> {ключ: результат или None}."""
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {k: ex.submit(safe, k, v[0], *v[1:]) for k, v in jobs.items()}
        return {k: f.result() for k, f in futs.items()}


def ln(el):
    return el.tag.split("}")[-1]


def pct(new, old):
    if new is None or not old:
        return None
    return (new / old - 1) * 100


def fnum(x, d=2):
    return f"{x:,.{d}f}".replace(",", " ").replace(".", ",")


def fpct(x, d=2, sign=True, suffix="%"):
    if x is None:
        return "н/д"
    if round(x, d) == 0:
        return f"{0:.{d}f}{suffix}".replace(".", ",")
    s = f"{abs(x):.{d}f}".replace(".", ",")
    return f"{('+' if x > 0 else '−') if sign else ('' if x > 0 else '−')}{s}{suffix}"


def fpp(x, d=2):
    """Изменение в п.п. без суффикса: '+0,25' / '−1,50' / '0,00' / ''."""
    if x is None:
        return ""
    return fpct(x, d, suffix="")


def arrow(x, d=2):
    if x is None or round(x, d) == 0:
        return "▬"
    return "▲" if x > 0 else "▼"


def chg_txt(ch, d=2):
    """'▲+0,22%' / '▼−0,28%' / '▬ 0,00%' / ''."""
    if ch is None:
        return ""
    p = fpct(ch, d)
    if p[0] not in "+−":
        p = " " + p
    return f"{arrow(ch, d)}{p}"


def e(s):
    return html.escape(str(s), quote=False)


def ddmm(iso):
    return f"{iso[8:10]}.{iso[5:7]}"


def next_meeting(today):
    for m in sorted(CBR_MEETINGS):
        if m >= today:
            return m
    return None


def table(rows, head=None):
    """rows: [(подпись, колонка1, ...)] -> моноширинный блок <pre>. Первая колонка влево, остальные вправо."""
    allrows = ([tuple(head)] if head else []) + [tuple("" if c is None else c for c in r) for r in rows]
    if not allrows:
        return ""
    n = max(len(r) for r in allrows)
    allrows = [r + ("",) * (n - len(r)) for r in allrows]
    w = [max(len(r[i]) for r in allrows) for i in range(n)]
    out = []
    for r in allrows:
        line = f"{r[0]:<{w[0]}}" + "".join(f" {r[i]:>{w[i]}}" for i in range(1, n))
        out.append(line.rstrip())
    return "<pre>" + e("\n".join(out)) + "</pre>"


def header(emoji, title, note=None):
    return f"{emoji} <b>{title}</b>" + (f"  <i>{e(note)}</i>" if note else "")


def pcts(x, d=2):
    return "н/д" if x is None else f"{fnum(x, d)}%"


def pages_url():
    """Адрес дашборда (переменная репозитория DASHBOARD_URL). Пусто - кнопка «Дашборд» не показывается."""
    return os.environ.get("DASHBOARD_URL", "").strip()


# ================================================================ ЦБ РФ

def cbr_rates(d):
    url = "https://www.cbr.ru/scripts/XML_daily.asp?date_req=" + d.strftime("%d/%m/%Y")
    root = ET.fromstring(http_get(url))
    out = {}
    for v in root.findall("Valute"):
        nominal = float(v.findtext("Nominal").replace(",", "."))
        out[v.findtext("CharCode")] = float(v.findtext("Value").replace(",", ".")) / nominal
    return root.attrib.get("Date"), out


def cbr_currencies(today):
    """Курс ЦБ на сегодня + предыдущий опубликованный (для изменения за день)."""
    cur_date, cur = cbr_rates(today)
    prev = {}
    for back in range(1, 7):
        pd_, p = cbr_rates(today - timedelta(days=back))
        if pd_ != cur_date:
            prev = p
            break
    return {"date": cur_date, "rates": {c: (cur.get(c), prev.get(c)) for c in ("USD", "EUR", "CNY")}}


def cbr_fx_series(code, d1, d2):
    """[(iso_date, курс)] по данным ЦБ."""
    url = ("https://www.cbr.ru/scripts/XML_dynamic.asp?date_req1=" + d1.strftime("%d/%m/%Y")
           + "&date_req2=" + d2.strftime("%d/%m/%Y") + "&VAL_NM_RQ=" + CBR_VAL_ID[code])
    root = ET.fromstring(http_get(url))
    out = []
    for r in root.findall("Record"):
        dd, mm, yy = r.attrib["Date"].split(".")
        nom = float(r.findtext("Nominal").replace(",", "."))
        out.append((f"{yy}-{mm}-{dd}", float(r.findtext("Value").replace(",", ".")) / nom))
    return out


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
    raw = http_get("https://www.cbr.ru/DailyInfoWebServ/DailyInfo.asmx", data=body,
                   headers={"Content-Type": "application/soap+xml; charset=utf-8"})
    return ET.fromstring(raw)


def _soap_pairs(root, item_tag, date_tag, val_tag):
    rows = []
    for el in root.iter():
        if item_tag and ln(el) != item_tag:
            continue
        dt = val = None
        for ch in el:
            if ln(ch) == date_tag:
                dt = ch.text[:10]
            elif ln(ch) == val_tag:
                val = float(ch.text.replace(",", "."))
        if dt and val is not None:
            rows.append((dt, val))
    return sorted(set(rows))


def cbr_key_series(d1, d2):
    rows = _soap_pairs(cbr_soap("KeyRateXML", d1, d2), "KR", "DT", "Rate")
    if not rows:
        raise ValueError("ключевая ставка: пусто")
    return rows


def cbr_ruonia_series(d1, d2):
    rows = _soap_pairs(cbr_soap("RuoniaXML", d1, d2), None, "D0", "ruo")
    if not rows:
        raise ValueError("RUONIA: пусто")
    return rows


def cbr_deposit_series():
    """Максимальная ставка по вкладам топ-10 банков (ЦБ, по декадам): [(iso_начала_декады, %)] ~2 года."""
    raw = http_get("https://www.cbr.ru/statistics/avgprocstav/").decode("utf-8", "replace")
    out = []
    for dec, mm, yy, val in re.findall(r"<td>\s*(I{1,3})\.(\d\d)\.(\d{4})\s*</td>\s*<td>\s*([\d,]+)\s*</td>", raw):
        day = {"I": "01", "II": "11", "III": "21"}[dec]
        out.append((f"{yy}-{mm}-{day}", float(val.replace(",", "."))))
    if not out:
        raise ValueError("ставка по депозитам: таблица не найдена")
    return sorted(out)


def last_on_or_before(series, iso):
    best = None
    for d, v in series or []:
        if d <= iso:
            best = v
    return best


# ================================================================ Мосбиржа (ISS)

def iss_json(path):
    return json.loads(http_get(ISS + path))


def moex_index(secid):
    """Текущие данные индекса: dict(val, chg (% за день), ytd (% с начала года), date)."""
    q = urllib.parse.quote(secid, safe="")
    md = iss_json(f"engines/stock/markets/index/securities/{q}.json?iss.meta=off&iss.only=marketdata"
                  "&marketdata.columns=CURRENTVALUE,LASTVALUE,LASTCHANGEPRC,YEARCHANGEPRC,TRADEDATE")["marketdata"]
    for row in md["data"]:
        r = dict(zip(md["columns"], row))
        val = r.get("CURRENTVALUE") or r.get("LASTVALUE")
        if val:
            return {"val": float(val), "chg": r.get("LASTCHANGEPRC"), "ytd": r.get("YEARCHANGEPRC"),
                    "date": r.get("TRADEDATE")}
    raise ValueError(f"MOEX {secid}: нет данных")


def moex_index_hist(secid, d1, d2):
    """История индекса: [(iso, close, yield|None, duration|None)], с пагинацией."""
    q = urllib.parse.quote(secid, safe="")
    out, start = {}, 0
    while True:
        j = iss_json(f"history/engines/stock/markets/index/securities/{q}.json?iss.meta=off"
                     f"&history.columns=TRADEDATE,CLOSE,YIELD,DURATION&from={d1:%Y-%m-%d}&till={d2:%Y-%m-%d}"
                     f"&start={start}")
        rows = j["history"]["data"]
        for r in rows:
            if r[1] is not None:
                out[r[0]] = (r[0], float(r[1]), r[2], r[3])
        cur = dict(zip(j["history.cursor"]["columns"], j["history.cursor"]["data"][0]))
        start = cur["INDEX"] + cur["PAGESIZE"]
        if not rows or start >= cur["TOTAL"]:
            break
    if not out:
        raise ValueError(f"MOEX history {secid}: пусто")
    return [out[k] for k in sorted(out)]


def moex_mmix():
    """RUSFAR O/N и RUSFAR CNY O/N: {'RUSFAR': %, 'RUSFARCNY': %, 'date': ...}."""
    md = iss_json("engines/stock/markets/index/boards/MMIX/securities.json?iss.meta=off&iss.only=marketdata"
                  "&securities=RUSFAR,RUSFARCNY&marketdata.columns=SECID,CURRENTVALUE,TRADEDATE")["marketdata"]
    out = {}
    for sid, v, td in md["data"]:
        if v is not None:
            out[sid] = float(v)
            out["date"] = td
    if "RUSFAR" not in out:
        raise ValueError("RUSFAR: нет данных")
    return out


def moex_zcyc(on_date=None):
    """Кривая ОФЗ: {'date': iso, 'points': {срок: %}}. on_date - на дату (берётся ближайшая до неё)."""
    q = f"&date={on_date:%Y-%m-%d}" if on_date else ""
    blk = iss_json("engines/stock/zcyc.json?iss.meta=off&iss.only=yearyields" + q)["yearyields"]
    rows = [dict(zip(blk["columns"], r)) for r in blk["data"]]
    if on_date:
        rows = [r for r in rows if r["tradedate"] <= on_date.isoformat()]
    if not rows:
        raise ValueError("G-curve: нет данных")
    last = max((r["tradedate"], r.get("tradetime") or "") for r in rows)
    pts = {float(r["period"]): float(r["value"]) for r in rows
           if (r["tradedate"], r.get("tradetime") or "") == last}
    return {"date": last[0], "points": pts}


def moex_zcyc_back(d, max_back=8):
    """Кривая на дату d или ближайший рабочий день до неё."""
    for back in range(max_back):
        z = safe("zcyc", moex_zcyc, d - timedelta(days=back))
        if z and z["points"]:
            return z
    raise ValueError(f"G-curve на {d}: нет данных")


def moex_imoex_constituents():
    """{тикер: короткое имя} акций из индекса Мосбиржи."""
    out, start = {}, 0
    while True:
        j = iss_json(f"statistics/engines/stock/markets/index/analytics/IMOEX.json?iss.meta=off&limit=100&start={start}")
        a = j["analytics"]
        for r in a["data"]:
            d = dict(zip(a["columns"], r))
            out[d["ticker"]] = d["shortnames"]
        if len(a["data"]) < 100:
            break
        start += 100
    if not out:
        raise ValueError("состав IMOEX: пусто")
    return out


def moex_shares_close(d, max_back=7):
    """Цены закрытия всех акций TQBR на дату d (или ближайший торговый день до неё): (iso, {тикер: цена})."""
    for back in range(max_back):
        day = d - timedelta(days=back)
        out, start = {}, 0
        while True:
            j = iss_json(f"history/engines/stock/markets/shares/boards/TQBR/securities.json?iss.meta=off"
                         f"&date={day:%Y-%m-%d}&history.columns=SECID,CLOSE&start={start}")
            rows = j["history"]["data"]
            for sid, close in rows:
                if close:
                    out[sid] = float(close)
            cur = dict(zip(j["history.cursor"]["columns"], j["history.cursor"]["data"][0]))
            start = cur["INDEX"] + cur["PAGESIZE"]
            if not rows or start >= cur["TOTAL"]:
                break
        if out:
            return day.isoformat(), out
    raise ValueError(f"акции на {d}: нет данных")


def moex_cny_tom():
    """Биржевой CNY/RUB (TOM): (цена, изменение_% к прошлому дню)."""
    md = iss_json("engines/currency/markets/selt/boards/CETS/securities/CNYRUB_TOM.json?iss.meta=off"
                  "&iss.only=marketdata&marketdata.columns=LAST,LASTTOPREVPRICE")["marketdata"]
    last, chg = md["data"][0]
    if last is None:
        raise ValueError("CNYRUB_TOM: нет сделок")
    return float(last), chg


# ================================================================ Yahoo, CoinGecko

def yahoo_series(symbol, rng="1y"):
    """[(iso, close)] дневные закрытия + meta (цена и прошлое закрытие)."""
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol, safe='')}"
           f"?interval=1d&range={rng}")
    res = json.loads(http_get(url))["chart"]["result"][0]
    closes = res["indicators"]["quote"][0]["close"]
    ser = []
    for ts, c in zip(res.get("timestamp") or [], closes):
        if c is not None:
            ser.append((datetime.fromtimestamp(ts, timezone.utc).date().isoformat(), float(c)))
    return ser, res["meta"]


def yahoo_quote(symbol):
    """(цена, прошлое закрытие, закрытие на 31.12 прошлого года)."""
    ser, meta = yahoo_series(symbol, "2y")
    price = meta.get("regularMarketPrice") or (ser[-1][1] if ser else None)
    # последний бар Yahoo - текущий/последний торговый день, значит прошлое закрытие - предпоследний
    prev = ser[-2][1] if len(ser) >= 2 else meta.get("previousClose")
    y0 = last_on_or_before(ser, f"{datetime.now(MSK).year - 1}-12-31")
    return price, prev, y0


def coingecko_btc():
    j = json.loads(http_get("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin"
                            "&vs_currencies=usd&include_24hr_change=true"))
    return j["bitcoin"]["usd"], j["bitcoin"].get("usd_24h_change")


# ================================================================ Дивиденды (smart-lab)

def _strip(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def _dmy(s):
    m = re.match(r"(\d\d)\.(\d\d)\.(\d{4})", s.strip())
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None


def dividends_calendar():
    """Ближайшие дивиденды с smart-lab.ru/dividends: [dict(name, ticker, period, div, yld, buy_by, registry, pay_by)]."""
    raw = http_get("https://smart-lab.ru/dividends/", retries=2).decode("utf-8", "replace")
    out, seen = [], set()
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", raw, re.S):
        tds = [_strip(x) for x in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(tds) < 9 or not _dmy(tds[7] or ""):
            continue
        key = (tds[1], tds[7])
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": tds[0], "ticker": tds[1], "period": tds[2], "div": tds[3], "yld": tds[4],
                    "buy_by": _dmy(tds[6]), "registry": _dmy(tds[7]), "pay_by": _dmy(tds[8])})
    if not out:
        raise ValueError("дивиденды: таблица не найдена")
    return out


# ================================================================ Новости

def news(limit, pattern, hours=26, per_source=3, cbr_all=True, exclude=()):
    """[(источник, заголовок, ссылка)] из RSS за последние hours часов, отфильтрованные по pattern."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    cnt, seen, out = {}, set(exclude), []
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
            if not (cbr_all and name == "Банк России") and not pattern.search(title):
                continue
            if cnt.get(name, 0) >= per_source:
                continue
            cnt[name] = cnt.get(name, 0) + 1
            seen.add(title)
            out.append((name, title, link))
    return out[:limit]


def news_lines(items):
    return [f'▪️ <a href="{html.escape(link, quote=True)}">{e(title)}</a> <i>· {e(src)}</i>'
            for src, title, link in items]


# ================================================================ Telegram

def tg_creds():
    token = (os.environ.get("TG_BOT_TOKEN") or "").strip()
    chat = (os.environ.get("TG_CHAT_ID") or "").strip()
    if not token:
        log("Секрет TG_BOT_TOKEN пустой или не задан.")
        sys.exit(1)
    if not chat:
        log("Секрет TG_CHAT_ID пустой или не задан.")
        sys.exit(1)
    return token, chat


def _tg_base():
    return os.environ.get("TG_API_BASE", "https://api.telegram.org")


def _tg_error(ex):
    try:
        desc = json.loads(ex.read().decode("utf-8", "replace")).get("description", "")
    except Exception:  # noqa: BLE001
        desc = ""
    return desc


def tg_send(token, chat_id, text, html_mode=True, reply_markup=None):
    params = {"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}
    if html_mode:
        params["parse_mode"] = "HTML"
    if reply_markup:
        params["reply_markup"] = json.dumps(reply_markup)
    try:
        resp = json.loads(http_get(f"{_tg_base()}/bot{token}/sendMessage",
                                   data=urllib.parse.urlencode(params).encode()))
    except urllib.error.HTTPError as ex:
        desc = _tg_error(ex)
        if html_mode and "parse entities" in desc.lower():
            log(f"[warn] Telegram не принял HTML ({desc}); отправляю простым текстом")
            plain = html.unescape(re.sub(r"<[^>]+>", "", text))
            return tg_send(token, chat_id, plain, html_mode=False, reply_markup=reply_markup)
        hint = ""
        if "chat not found" in desc.lower() or "chat_id" in desc.lower():
            hint = " Проверь секрет TG_CHAT_ID (только число) и что в боте нажат Start."
        raise RuntimeError(f"Telegram ответил {ex.code}: {desc}.{hint}") from None
    if not resp.get("ok"):
        raise RuntimeError(f"Telegram: {resp}")


def tg_photo(token, chat_id, path, caption="", reply_markup=None):
    """Отправка картинки (multipart/form-data)."""
    boundary = uuid.uuid4().hex
    fields = {"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"}
    if reply_markup:
        fields["reply_markup"] = json.dumps(reply_markup)
    body = b""
    for k, v in fields.items():
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n").encode()
    with open(path, "rb") as f:
        img = f.read()
    body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"{os.path.basename(path)}\"\r\n"
             "Content-Type: image/png\r\n\r\n").encode() + img + f"\r\n--{boundary}--\r\n".encode()
    try:
        resp = json.loads(http_get(f"{_tg_base()}/bot{token}/sendPhoto", data=body, retries=2, timeout=60,
                                   headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}))
    except urllib.error.HTTPError as ex:
        raise RuntimeError(f"Telegram (фото) ответил {ex.code}: {_tg_error(ex)}") from None
    if not resp.get("ok"):
        raise RuntimeError(f"Telegram (фото): {resp}")


def pack(blocks, limit=3900):
    msgs, cur = [], ""
    for b in blocks:
        if not b:
            continue
        cand = b if not cur else cur + "\n\n" + b
        if len(cand) > limit and cur:
            msgs.append(cur)
            cur = b
        else:
            cur = cand
    if cur:
        msgs.append(cur)
    return msgs


def deliver(msgs, keyboard=None, photos=()):
    """Отправить сообщения (кнопки - к последнему тексту) и картинки [(путь, подпись)].
    DRY_RUN=1 или --demo -> печать в консоль."""
    if os.environ.get("DRY_RUN") == "1" or "--demo" in sys.argv:
        for m in msgs:
            print(m, "\n" + "-" * 40)
        if keyboard:
            print("[кнопки]", " | ".join(b["text"] for row in keyboard["inline_keyboard"] for b in row))
        for p, cap in photos:
            print(f"[картинка] {p} — {cap}")
        return
    token, chat = tg_creds()
    try:
        for i, m in enumerate(msgs):
            tg_send(token, chat, m, reply_markup=keyboard if i == len(msgs) - 1 else None)
            time.sleep(1)
        for p, cap in photos:
            if p and os.path.exists(p):
                tg_photo(token, chat, p, cap)
                time.sleep(1)
    except Exception as ex:  # noqa: BLE001
        log(f"ОШИБКА ОТПРАВКИ: {type(ex).__name__}: {ex}")
        sys.exit(1)
    log(f"Отправлено: сообщений {len(msgs)}, картинок {len(photos)}")
