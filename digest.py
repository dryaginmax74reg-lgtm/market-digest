#!/usr/bin/env python3
"""Рыночный дайджест -> Telegram. Только стандартная библиотека, без ИИ и платных API.

Переменные окружения:
  TG_BOT_TOKEN, TG_CHAT_ID   - обязательны для отправки
  DRY_RUN=1                  - не отправлять, а напечатать в консоль
  SHOW_TALKING_POINTS=0      - убрать блок "Что это значит для клиентов"
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


def moex_index(secid, boards=("SNDX", "RTSI")):
    for board in boards:
        url = (f"https://iss.moex.com/iss/engines/stock/markets/index/boards/{board}"
               f"/securities/{secid}.json?iss.meta=off&iss.only=marketdata")
        j = json.loads(http_get(url))
        cols, rows = j["marketdata"]["columns"], j["marketdata"]["data"]
        if rows:
            r = dict(zip(cols, rows[0]))
            val = r.get("CURRENTVALUE") or r.get("LASTVALUE")
            if val:
                return float(val), r.get("LASTCHANGEPRC")
    raise ValueError(f"MOEX {secid}: нет данных")


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

def collect(today):
    d = {}
    d["cur"] = safe("cbr_currencies", cbr_currencies, today)
    d["key_rate"] = safe("cbr_key_rate", cbr_key_rate, today)
    d["ruonia"] = safe("cbr_ruonia", cbr_ruonia, today)
    d["rusfar"] = safe("moex_rusfar", moex_index, "RUSFAR")
    d["rgbi"] = safe("moex_rgbi", moex_index, "RGBI")
    d["imoex"] = safe("moex_imoex", moex_index, "IMOEX")
    d["brent"] = safe("yahoo_brent", yahoo, "BZ=F")
    d["gold"] = safe("yahoo_gold", yahoo, "GC=F")
    d["crypto"] = safe("coingecko", coingecko)
    d["news"] = safe("news", news, int(os.environ.get("NEWS_LIMIT", "8"))) or []
    return d


def demo_data():
    return {
        "cur": {"date": "14.10.2026", "rates": {
            "USD": (83.8675, 83.9175), "EUR": (93.7723, 94.0364), "CNY": (11.74, 11.71)}},
        "key_rate": 14.0,
        "ruonia": (13.73, "2026-10-13"),
        "rusfar": (13.79, None),
        "rgbi": (111.29, 0.04),
        "imoex": (2438.84, 0.51),
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


def build_key(d, today):
    lines = []
    cur = d.get("cur")
    if cur:
        usd, usd_p = cur["rates"]["USD"]
        eur, eur_p = cur["rates"]["EUR"]
        ch = pct(usd, usd_p)
        if ch is not None and round(ch, 2) < 0:
            title = "Рубль укрепляется"
        elif ch is not None and round(ch, 2) > 0:
            title = "Рубль слабеет"
        else:
            title = "Рубль без заметных изменений"
        txt = f"Официальный курс доллара ЦБ: {fnum(usd, 4)} ₽ ({fpct(ch)})"
        if eur:
            txt += f", евро: {fnum(eur, 4)} ₽ ({fpct(pct(eur, eur_p))})."
        lines.append(f"• <b>{e(title)}.</b> {e(txt)}")
    if d.get("brent"):
        p, prev = d["brent"]
        ch = pct(p, prev)
        word = "дорожает" if ch and ch > 0.3 else "дешевеет" if ch and ch < -0.3 else "стабильна"
        lines.append(f"• <b>Нефть {word}.</b> Brent ${fnum(p)} ({fpct(ch, 1)} за день).")
    if d.get("key_rate") is not None:
        nm = next_meeting(today)
        txt = f"Ключевая ставка ЦБ — {fnum(d['key_rate'])}%."
        if nm:
            txt += f" Ближайшее заседание: {nm.day} {MONTHS[nm.month - 1]}."
        lines.append(f"• <b>Ставка.</b> {e(txt)}")
    return "🔑 <b>01 · КЛЮЧЕВОЕ</b>\n" + ("\n".join(lines) if lines else "нет данных")


def build_fx(d):
    cur = d.get("cur")
    if not cur:
        return "💱 <b>02 · ВАЛЮТЫ</b>\nнет данных"
    lines = [f"💱 <b>02 · ВАЛЮТЫ</b> · курс ЦБ на {e(cur['date'][:5])}"]
    for code, label in (("USD", "USD/RUB"), ("EUR", "EUR/RUB"), ("CNY", "CNY/RUB")):
        v, p = cur["rates"][code]
        if v is None:
            lines.append(f"{label}: нет данных")
        else:
            ch = pct(v, p)
            lines.append(f"{label}  <b>{fnum(v, 4)} ₽</b>  {arrow(ch)} {fpct(ch)}")
    lines.append("<i>Официальные курсы, а не котировки для сделки. Изменение — к предыдущему курсу ЦБ.</i>")
    return "\n".join(lines)


def build_rates(d):
    lines = ["🏦 <b>03 · СТАВКИ И ОБЛИГАЦИИ</b>"]
    kr = d.get("key_rate")
    lines.append(f"Ключевая ставка ЦБ  <b>{fnum(kr)}%</b>" if kr is not None else "Ключевая ставка: нет данных")
    if d.get("rusfar"):
        lines.append(f"RUSFAR O/N  <b>{fnum(d['rusfar'][0])}%</b>")
    else:
        lines.append("RUSFAR O/N: нет данных")
    if d.get("ruonia"):
        v, dt = d["ruonia"]
        lines.append(f"RUONIA  <b>{fnum(v)}%</b> · на {dt[8:10]}.{dt[5:7]}")
    else:
        lines.append("RUONIA: нет данных")
    if d.get("rgbi"):
        v, ch = d["rgbi"]
        lines.append(f"Индекс ОФЗ RGBI  <b>{fnum(v)}</b>  {arrow(ch)} {fpct(ch)}")
    else:
        lines.append("RGBI: нет данных")
    lines.append("<i>RUSFAR и RUONIA публикуются с задержкой.</i>")
    return "\n".join(lines)


def build_markets(d):
    lines = ["📈 <b>04 · АКЦИИ И СЫРЬЁ</b>"]
    if d.get("imoex"):
        v, ch = d["imoex"]
        lines.append(f"IMOEX  <b>{fnum(v)}</b>  {arrow(ch)} {fpct(ch)}")
    else:
        lines.append("IMOEX: нет данных")
    for key, label, unit in (("brent", "Brent", "за баррель"), ("gold", "Золото", "за унцию")):
        if d.get(key):
            p, prev = d[key]
            ch = pct(p, prev)
            lines.append(f"{label}  <b>${fnum(p, 0 if p > 1000 else 2)}</b> {unit}  {arrow(ch)} {fpct(ch, 1)}")
        else:
            lines.append(f"{label}: нет данных")
    lines.append("<i>IMOEX — последнее доступное значение (утром это итог прошлой сессии). "
                 "Нефть и золото — фьючерсы, значения на момент запуска.</i>")
    return "\n".join(lines)


def build_crypto(d):
    lines = ["₿ <b>05 · КРИПТОВАЛЮТЫ</b>"]
    c = d.get("crypto")
    if not c:
        lines.append("нет данных")
    else:
        for code, name in (("BTC", "Bitcoin"), ("ETH", "Ethereum")):
            p, ch = c[code]
            lines.append(f"{name} ({code})  <b>${fnum(p, 0)}</b>  {arrow(ch, 1)} {fpct(ch, 1)} за 24ч")
    return "\n".join(lines)


def build_news(d):
    items = d.get("news") or []
    lines = ["📰 <b>06 · НОВОСТИ И СОБЫТИЯ</b>"]
    if not items:
        lines.append("нет свежих новостей по ключевым темам")
    for src, title, link in items:
        lines.append(f'• <a href="{html.escape(link, quote=True)}">{e(title)}</a> <i>({e(src)})</i>')
    return "\n".join(lines)


def build_points(d, today):
    pts = []
    kr, ruonia = d.get("key_rate"), d.get("ruonia")
    bits = []
    if ruonia:
        bits.append(f"RUONIA {fnum(ruonia[0])}%")
    if kr is not None:
        bits.append(f"ключевая ставка {fnum(kr)}%")
    if bits:
        pts.append("<b>Свободная ликвидность.</b> " + e(", ".join(bits)) +
                   ". Сравнивать инструменты стоит по чистой доходности, сроку размещения и доступности средств.")
    if d.get("rgbi"):
        ch = d["rgbi"][1]
        nm = next_meeting(today)
        tail = f" Важны дюрация и сценарий ключевой ставки (заседание {nm.day} {MONTHS[nm.month - 1]})." if nm \
            else " Важны дюрация и сценарий ключевой ставки."
        if ch is not None and ch >= 0.3:
            base = f"RGBI заметно вырос ({fpct(ch)})."
        elif ch is not None and ch <= -0.3:
            base = f"RGBI снижается ({fpct(ch)}): доходности растут."
        else:
            base = f"Движение RGBI небольшое ({fpct(ch)}), устойчивого тренда по этим данным нет."
        pts.append("<b>ОФЗ и фиксация доходности.</b> " + e(base + tail))
    if d.get("cur"):
        usd, usd_p = d["cur"]["rates"]["USD"]
        ch = pct(usd, usd_p)
        direction = "снижается" if ch is not None and ch < 0 else "растёт" if ch is not None and ch > 0 else "почти не меняется"
        pts.append(f"<b>Валютный риск и диверсификация.</b> Официальный курс доллара {direction} ({fpct(ch)}). "
                   "Для клиента важнее будущие валютные расходы и структура активов, чем движение курса за один день.")
    if not pts:
        return None
    return "💼 <b>07 · ЧТО ЭТО ЗНАЧИТ ДЛЯ КЛИЕНТОВ АТОН</b>\n" + "\n".join(f"{i}. {p}" for i, p in enumerate(pts, 1))


def build_blocks(d, now):
    today = now.date()
    head = (f"📊 <b>РЫНОЧНЫЙ ДАЙДЖЕСТ</b>\n{WEEKDAYS[now.weekday()]} · {now.day} {MONTHS[now.month - 1]} {now.year}\n"
            "<i>Российский и глобальный рынки</i>")
    blocks = [head, build_key(d, today), build_fx(d), build_rates(d), build_markets(d),
              build_crypto(d), build_news(d)]
    if os.environ.get("SHOW_TALKING_POINTS", "1") != "0":
        pts = build_points(d, today)
        if pts:
            blocks.append(pts)
    blocks.append(f"<i>Собрано автоматически в {now:%H:%M} МСК. Источники: ЦБ РФ, MOEX, Yahoo Finance, CoinGecko, RSS. "
                  "Справочная информация, не инвестиционная рекомендация.</i>")
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

def tg_send(token, chat_id, text):
    data = urllib.parse.urlencode({
        "chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": "true",
    }).encode()
    resp = json.loads(http_get(f"https://api.telegram.org/bot{token}/sendMessage", data=data))
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

    token, chat = os.environ.get("TG_BOT_TOKEN"), os.environ.get("TG_CHAT_ID")
    if not token or not chat:
        log("Не заданы TG_BOT_TOKEN / TG_CHAT_ID.")
        sys.exit(1)
    for m in msgs:
        tg_send(token, chat, m)
        time.sleep(1)
    log(f"Отправлено сообщений: {len(msgs)}")


if __name__ == "__main__":
    main()
