#!/usr/bin/env python3
"""Ежедневный рыночный дайджест -> Telegram. Без ИИ и платных API.

Запуск:  python digest.py            боевой (нужны TG_BOT_TOKEN, TG_CHAT_ID): карточка-картинка + кнопки
         python digest.py --build    только собрать (карточка, docs/digest.json для кнопок «Подробнее» и др.)
         python digest.py --send     отправить собранное (в Actions — после публикации сайта)
         python digest.py --demo     демо-данные, печать в консоль
         DRY_RUN=1 python digest.py  живые данные, печать в консоль
Настройки: NEWS_LIMIT (по умолчанию 5), MONEY_NEWS_LIMIT (5), DIV_DAYS (14).
Даты заседаний ЦБ и пороги «сильных движений» - в common.py.
"""
import json
import os
import sys
from datetime import date, datetime, timedelta

import bondsweek
import events

from common import (MACRO_RE, MONEY_RE, MONTHS, MOVE_THRESHOLDS, MSK, WEEKDAYS, cbr_currencies,
                    cbr_deposit_series, cbr_key_series, cbr_rates, cbr_ruonia_series, coingecko_btc,
                    ddmm, deliver, market_view, dividends_calendar, e, fnum, fpct, fpp, header, last_on_or_before, log,
                    moex_imoex_constituents, moex_index, moex_index_hist, moex_mmix, moex_zcyc, moex_zcyc_back,
                    news, news_lines, next_meeting, pack, pages_url, parallel, pct, pcts, table, yahoo_quote)

ZCYC_POINTS = (1, 2, 5, 10)


# ---------------------------------------------------------------- сбор

def collect(today):
    y0 = date(today.year - 1, 12, 31)
    jobs = {
        "cur": (cbr_currencies, today),
        "fx0": (cbr_rates, y0),
        "key": (cbr_key_series, y0 - timedelta(days=20), today),
        "ruonia": (cbr_ruonia_series, y0 - timedelta(days=20), today),
        "dep": (cbr_deposit_series,),
        "mmix": (moex_mmix,),
        "rusfarcny0": (moex_index_hist, "RUSFARCNY", y0 - timedelta(days=14), y0),
        "zcyc": (moex_zcyc,),
        "zcyc0": (moex_zcyc_back, y0),
        "RGBITR": (moex_index, "RGBITR"),
        "RUCBTRNS": (moex_index, "RUCBTRNS"),
        "IMOEX": (moex_index, "IMOEX"),
        "corp_h": (moex_index_hist, "RUCBTRNS", today - timedelta(days=12), today),
        "brent": (yahoo_quote, "BZ=F"),
        "gold": (yahoo_quote, "GC=F"),
        "ust": (yahoo_quote, "^TNX"),
        "btc_y": (yahoo_quote, "BTC-USD"),
        "btc": (coingecko_btc,),
        "divs": (dividends_calendar,),
        "imoex_list": (moex_imoex_constituents,),
    }
    d = parallel(jobs)
    macro = news(int(os.environ.get("NEWS_LIMIT", "5")), MACRO_RE) or []
    money = news(int(os.environ.get("MONEY_NEWS_LIMIT", "5")), MONEY_RE, cbr_all=False,
                 exclude=[t for _s, t, _l in macro]) or []
    d["news"], d["money_news"] = macro, money
    d["watch"] = events.load_watchlist()
    # понедельник (или BONDS_WEEK=1): облигации — лучшие бумаги, размещения, рейтинги за неделю
    if today.weekday() == 0 or os.environ.get("BONDS_WEEK") == "1":
        d["bondsweek"] = bondsweek.collect()
        d["placements"] = (d["bondsweek"] or {}).get("placements")
    else:
        try:
            import placements
            d["placements"] = placements.fetch_placements()
        except Exception as ex:  # noqa: BLE001
            log(f"[warn] размещения bonds-lab: {ex}")
            d["placements"] = None
    d["watch_news"] = events.watch_news(d["watch"], exclude=[t for _s, t, _l in macro + money])
    apply_sanity(d)
    return d


def apply_sanity(d):
    """Выкинуть значения вне разумных диапазонов (ошибка источника не должна попасть в дайджест)."""
    from common import sane
    cur = d.get("cur")
    if cur:
        for c in ("USD", "EUR", "CNY"):
            v, p = cur["rates"].get(c, (None, None))
            cur["rates"][c] = (sane(c, v), sane(c, p))
    for k in ("IMOEX", "RGBITR", "RUCBTRNS"):
        if d.get(k) and sane(k, d[k]["val"]) is None:
            d[k] = None
    for k, n in (("brent", "BRENT"), ("gold", "GOLD"), ("btc_y", "BTC")):
        if d.get(k) and sane(n, d[k][0]) is None:
            d[k] = None
    if d.get("btc") and sane("BTC", d["btc"][0]) is None:
        d["btc"] = None
    if d.get("ust") and sane("UST10", d["ust"][0]) is None:
        d["ust"] = None
    if d.get("key") and sane("KEY", d["key"][-1][1]) is None:
        d["key"] = None
    if d.get("dep") and sane("DEP", d["dep"][-1][1]) is None:
        d["dep"] = None
    if d.get("zcyc"):
        d["zcyc"]["points"] = {p: v for p, v in d["zcyc"]["points"].items() if sane("OFZ", v) is not None}


def demo_data():
    """Фиксированные данные для проверки вёрстки (порядок величин на 14.10.2026)."""
    return {
        "cur": {"date": "15.10.2026", "rates": {"USD": (83.87, 83.92), "EUR": (93.77, 94.04), "CNY": (12.47, 12.40)}},
        "fx0": ("31.12.2025", {"USD": 78.23, "EUR": 91.70, "CNY": 11.12}),
        "key": [("2025-12-30", 16.0), ("2026-10-13", 14.0), ("2026-10-14", 14.0)],
        "ruonia": [("2025-12-30", 15.79), ("2026-10-13", 13.73)],
        "dep": [("2025-12-21", 14.9), ("2026-09-21", 13.055), ("2026-10-01", 13.055)],
        "mmix": {"RUSFAR": 13.83, "RUSFARCNY": 0.43, "date": "2026-10-14"},
        "rusfarcny0": [("2025-12-29", 3.1, None, None)],
        "zcyc": {"date": "2026-10-14", "points": {1.0: 13.1, 2.0: 13.9, 5.0: 14.6, 10.0: 14.4}},
        "zcyc0": {"date": "2025-12-30", "points": {1.0: 13.14, 2.0: 13.92, 5.0: 14.58, 10.0: 14.44}},
        "RGBITR": {"val": 768.36, "chg": 0.35, "ytd": 3.45, "date": "2026-10-14"},
        "RUCBTRNS": {"val": 211.30, "chg": -0.01, "ytd": 10.39, "date": "2026-10-14"},
        "IMOEX": {"val": 2417.91, "chg": -0.04, "ytd": -12.6, "date": "2026-10-14"},
        "corp_h": [("2026-10-13", 211.3, 16.74, 674)],
        "brent": (102.2, 106.6, 61.0),
        "gold": (4086.0, 4156.0, 4300.0),
        "ust": (4.12, 4.08, 4.57),
        "btc_y": (83400.0, 83100.0, 87000.0),
        "btc": (83400.0, 0.35),
        "divs": [
            {"name": "Мать и Дитя", "ticker": "MDMG", "period": "2кв 2026", "div": "38", "yld": "2,8%",
             "buy_by": "2026-10-16", "registry": "2026-10-19", "pay_by": "2026-10-29"},
            {"name": "Акрон", "ticker": "AKRN", "period": "2кв 2026", "div": "146", "yld": "0,9%",
             "buy_by": "2026-10-30", "registry": "2026-11-02", "pay_by": "2026-11-12"},
            {"name": "БСП ап", "ticker": "BSPBP", "period": "2кв 2026", "div": "0,22", "yld": "0,7%",
             "buy_by": "2026-10-02", "registry": "2026-10-05", "pay_by": "2026-10-19"},
        ],
        "imoex_list": {"MDMG": "Мать и Дитя", "AKRN": "Акрон", "BSPB": "БСП"},
        "news": [("Банк России", "Банк России опубликовал мониторинг предприятий", "https://www.cbr.ru/"),
                 ("Интерфакс", "Инфляция в США в сентябре ускорилась до 3,7%", "https://www.interfax.ru/")],
        "money_news": [("РБК", "Компания X проведёт SPO на 10 млрд рублей", "https://www.rbc.ru/"),
                       ("Коммерсантъ", "Фонд Y приобрёл долю в сети клиник", "https://www.kommersant.ru/")],
    }


# ---------------------------------------------------------------- расчёты

def key_now(d):
    ser = d.get("key") or []
    return ser[-1][1] if ser else None


def strong_moves(d, today):
    """[(иконка, текст)] - только то, что сдвинулось сильнее порогов."""
    out = []
    cur = d.get("cur")
    if cur:
        for code in ("USD", "CNY", "EUR"):
            v, p = cur["rates"][code]
            ch = pct(v, p)
            if ch is not None and abs(ch) >= MOVE_THRESHOLDS[code]:
                word = "рубль слабеет" if ch > 0 else "рубль укрепляется"
                out.append(("🔴" if ch > 0 else "🟢", f"{code}/RUB {fpct(ch)} за день ({word}) → {fnum(v)} ₽"))
    for sid, label in (("IMOEX", "IMOEX"), ("RGBITR", "ОФЗ (RGBI TR)"), ("RUCBTRNS", "Корп. облигации")):
        r = d.get(sid)
        if r and r.get("chg") is not None and abs(r["chg"]) >= MOVE_THRESHOLDS[sid]:
            out.append(("🟢" if r["chg"] > 0 else "🔴", f"{label} {fpct(r['chg'])} за день"))
    for key, label, thr in (("brent", "Brent", "BRENT"), ("gold", "Золото", "GOLD")):
        q = d.get(key)
        if q and q[0] and q[1]:
            ch = pct(q[0], q[1])
            if abs(ch) >= MOVE_THRESHOLDS[thr]:
                out.append(("🟢" if ch > 0 else "🔴", f"{label} {fpct(ch, 1)} → ${fnum(q[0])}"))
    b = d.get("btc")
    if b and b[1] is not None and abs(b[1]) >= MOVE_THRESHOLDS["BTC"]:
        out.append(("🟢" if b[1] > 0 else "🔴", f"BTC {fpct(b[1], 1)} за 24ч"))
    ser = d.get("key") or []
    if len(ser) >= 2 and ser[-1][1] != ser[-2][1] and ser[-1][0] >= (today - timedelta(days=4)).isoformat():
        diff = ser[-1][1] - ser[-2][1]
        out.append(("🏦", f"Ключевая ставка изменена: {fnum(ser[-2][1])}% → {fnum(ser[-1][1])}% ({fpp(diff)} п.п.)"))
    return out


# ---------------------------------------------------------------- вёрстка

HEAD = ("", "", "1д", "YTD")


def dot(ch, d=2):
    """Цветной маркер направления за день."""
    if ch is None or round(ch, d) == 0:
        return "⚪"
    return "🟢" if ch > 0 else "🔴"


def pc(ch, d=2):
    return "" if ch is None else fpct(ch, d)


def fold(title_html, lines):
    """Заголовок + сворачиваемый блок (в Telegram раскрывается по нажатию)."""
    if not lines:
        return ""
    return title_html + "\n<blockquote expandable>" + "\n".join(lines) + "</blockquote>"


def build_moves(d, today):
    items = strong_moves(d, today)
    if not items:
        return ""
    return header("⚡", "СИЛЬНЫЕ ДВИЖЕНИЯ") + "\n" + "\n".join(f"{i} {e(t)}" for i, t in items)


def build_fx(d):
    cur = d.get("cur")
    if not cur:
        return header("💱", "ВАЛЮТЫ") + "\nнет данных"
    base = (d.get("fx0") or (None, {}))[1]
    rows = []
    for code in ("USD", "EUR", "CNY"):
        v, p = cur["rates"][code]
        if v is None:
            rows.append((f"⚪ {code}/RUB", "н/д"))
            continue
        y, ch = pct(v, base.get(code)), pct(v, p)
        rows.append((f"{dot(ch)} {code}/RUB", fnum(v, 2), pc(ch), "" if y is None else fpct(y, 1)))
    (u, up), (c, cp) = cur["rates"]["USD"], cur["rates"]["CNY"]
    if u and c:
        cross = u / c
        cross_p = up / cp if up and cp else None
        cross0 = base.get("USD") / base.get("CNY") if base.get("USD") and base.get("CNY") else None
        y = pct(cross, cross0)
        ch = pct(cross, cross_p)
        rows.append((f"{dot(ch)} USD/CNY", fnum(cross, 3), pc(ch), "" if y is None else fpct(y, 1)))
    return header("💱", "ВАЛЮТЫ", f"ЦБ на {cur['date'][:5]} · день · с начала года") + "\n" + table(rows)


def build_rates(d, today):
    y0 = f"{today.year - 1}-12-31"
    rows = []
    key = key_now(d)
    key0 = last_on_or_before(d.get("key"), y0)
    rows.append(("Ключевая ЦБ", pcts(key), fpp(key - key0) if key is not None and key0 is not None else ""))
    mm = d.get("mmix") or {}
    for sid, label in (("RUSFARCNY", "RUSFAR CNY"),):
        v = mm.get(sid)
        h = d.get(sid.lower() + "0") or []
        b = h[-1][1] if h else None
        rows.append((label, pcts(v), fpp(v - b) if v is not None and b is not None else ""))
    ru = d.get("ruonia") or []
    if ru:
        v, b = ru[-1][1], last_on_or_before(ru, y0)
        rows.append((f"RUONIA {ddmm(ru[-1][0])}", pcts(v), fpp(v - b) if b is not None else ""))
    out = header("🏦", "СТАВКИ") + "\n" + table(rows, ("", "", "YTD п.п."))

    lines = []
    nm = next_meeting(today)
    if nm:
        days = (nm - today).days
        when = "сегодня" if days == 0 else f"через {days} дн."
        lines.append(f"🗓 Заседание ЦБ {nm.day} {MONTHS[nm.month - 1]} ({when})")
    z = d.get("zcyc")
    mv = market_view(key, (z or {}).get("points", {}).get(1.0)) if z else None
    if mv:
        sp, ke, view = mv
        lines.append(f"🔮 Рынок закладывает {view}: ОФЗ 1г {fnum(z['points'][1.0])}% vs ключевая ≈{fnum(ke)}% "
                     f"в годовом начислении ({fpp(sp)} п.п.)")
    if lines and not card_ok(d):
        out += "\n" + "\n".join(e(x) for x in lines)
    return out


def build_yields(d, today):
    dep_ser = d.get("dep") or []
    dep = dep_ser[-1][1] if dep_ser else None
    dep0 = last_on_or_before(dep_ser, f"{today.year - 1}-12-31")
    rows = []

    def row(label, v, v0, spread=True):
        rows.append((label, pcts(v), fpp(v - dep) if spread and v is not None and dep is not None else "",
                     fpp(v - v0) if v is not None and v0 is not None else ""))

    dec = ""
    if dep_ser:
        dd = dep_ser[-1][0]
        dec = " " + {"01": "I", "11": "II", "21": "III"}.get(dd[8:10], "") + "." + dd[5:7]
    row("Депозит" + dec, dep, dep0, spread=False)
    z, z0 = d.get("zcyc") or {}, d.get("zcyc0") or {}
    for p in ZCYC_POINTS:
        v = (z.get("points") or {}).get(float(p))
        if v is not None:
            row(f"ОФЗ {p}г", v, (z0.get("points") or {}).get(float(p)))
    ch = d.get("corp_h") or []
    if ch and ch[-1][2]:
        dur = f" {fnum(ch[-1][3] / 365, 1)}г" if ch[-1][3] else ""
        row(f"Корп.{dur}", float(ch[-1][2]), None)
    ust = d.get("ust")
    if ust and ust[0]:
        rows.append(("UST 10Y $", pcts(ust[0]), "", fpp(ust[0] - ust[2]) if ust[2] else ""))
    note = "% годовых · разница с депозитом · YTD п.п."
    return header("💰", "ДЕПОЗИТ vs ОБЛИГАЦИИ", note) + "\n" + table(rows, ("", "", "к деп.", "YTD"))


def build_markets(d):
    rows = []
    for sid, label in (("RGBITR", "ОФЗ TR"), ("RUCBTRNS", "Корп. TR"), ("IMOEX", "IMOEX")):
        r = d.get(sid)
        if r:
            rows.append((f"{dot(r['chg'])} {label}", fnum(r["val"], 0 if r["val"] > 1000 else 2), pc(r["chg"]),
                         "" if r["ytd"] is None else fpct(r["ytd"], 1)))
        else:
            rows.append((f"⚪ {label}", "н/д"))
    for key, label in (("brent", "Brent $"), ("gold", "Золото $")):
        q = d.get(key)
        if q and q[0]:
            p, prev, y0 = q
            ch, y = pct(p, prev), pct(p, y0)
            rows.append((f"{dot(ch)} {label}", fnum(p, 0 if p > 1000 else 2), pc(ch), "" if y is None else fpct(y, 1)))
        else:
            rows.append((f"⚪ {label}", "н/д"))
    b, by = d.get("btc"), d.get("btc_y")
    if b or by:
        price = b[0] if b else by[0]
        ch = b[1] if b else pct(by[0], by[1])
        y = pct(price, by[2]) if by else None
        rows.append((f"{dot(ch)} BTC $", fnum(price, 0), pc(ch), "" if y is None else fpct(y, 1)))
    im = d.get("IMOEX") or {}
    note = "день · с начала года"
    if im.get("date"):
        if im["date"] == datetime.now(MSK).date().isoformat() and im.get("time"):
            note += f"; биржа на {im['time'][:5]} МСК"
        else:
            note += f"; биржа — закрытие {ddmm(im['date'])}"
    return header("📈", "РЫНКИ", note) + "\n" + table(rows)


def build_watch(d, today):
    """⭐ Ваш список: новости и дивиденды по компаниям из секрета WATCHLIST. Не сворачивается."""
    watch = d.get("watch")
    if not watch:
        return ""
    lines = []
    t0, t_end = today.isoformat(), (today + timedelta(days=45)).isoformat()
    for x in sorted(d.get("divs") or [], key=lambda v: v.get("registry") or ""):
        if events.is_watched(x, watch) and x.get("registry") and t0 <= x["registry"] <= t_end:
            lines.append(f"✂️ {e(x['name'])}: дивиденд {e(x['div'])} ₽ ({e(x['yld'])}), реестр {ddmm(x['registry'])}"
                         + (f", выплата до {ddmm(x['pay_by'])}" if x.get("pay_by") else "")
                         + ("" if x.get("approved", True) else " · <i>рекомендация СД</i>"))
        elif events.is_watched(x, watch) and x.get("pay_by") and t0 <= x["pay_by"] <= (today + timedelta(days=7)).isoformat():
            lines.append(f"💸 {e(x['name'])}: выплата дивидендов до {ddmm(x['pay_by'])} ({e(x['div'])} ₽ на акцию)")
    seen = set()
    for src, title, link in (d.get("money_news") or []) + (d.get("news") or []) + (d.get("watch_news") or []):
        if title in seen or not events.is_watched(title, watch):
            continue
        seen.add(title)
        lines += news_lines([(src, title, link)])
    if not lines:
        return ""
    return header("⭐", "ВАШ СПИСОК", "компании из списка наблюдения") + "\n" + "\n".join(lines[:10])


def build_events(d, today):
    days = events.div_window(today)
    divs = d.get("divs") or []
    allow = set((d.get("imoex_list") or {}).keys())
    watch = d.get("watch")

    def in_index(t):
        return not allow or t in allow or t.rstrip("P") in allow

    t0, t_reg, t_pay = today.isoformat(), (today + timedelta(days=days)).isoformat(), (today + timedelta(days=7)).isoformat()
    lim = 12 if days > 14 else 8
    reg = sorted((x for x in divs if x["registry"] and t0 <= x["registry"] <= t_reg
                  and (in_index(x["ticker"]) or events.is_watched(x, watch))), key=lambda x: x["registry"])[:lim]
    pay = sorted((x for x in divs if x["pay_by"] and t0 <= x["pay_by"] <= t_pay
                  and (in_index(x["ticker"]) or events.is_watched(x, watch))), key=lambda x: x["pay_by"])[:lim]

    def star(x):
        return "⭐ " if events.is_watched(x, watch) else ""
    parts = []
    if divs:
        if reg:
            parts.append(f"<b>Дивидендные отсечки, {days} дн.</b> <i>(реестр · купить до)</i>")
            parts += [f"▪️ {star(x)}{ddmm(x['registry'])} · {e(x['name'])} · {e(x['div'])} ₽ ({e(x['yld'])})"
                      + (f" · до {ddmm(x['buy_by'])}" if x["buy_by"] else "")
                      + ("" if x.get("approved", True) else " · <i>только рекомендация СД</i>") for x in reg]
        if pay:
            parts.append("<b>Выплаты дивидендов, 7 дн.</b> <i>(крайний срок выплаты акционерам)</i>")
            parts += [f"▪️ {star(x)}до {ddmm(x['pay_by'])} · {e(x['name'])} · {e(x['div'])} ₽" for x in pay]
        if not reg and not pay:
            parts.append("<i>отсечек и выплат по бумагам индекса в ближайшие дни нет</i>")
    else:
        parts.append("<i>календарь дивидендов недоступен</i>")
    mn = d.get("money_news") or []
    if mn:
        parts.append("<b>Сделки, размещения, выкупы</b>")
        parts += news_lines(mn)
    n = len(reg) + len(pay) + len(mn)
    return fold(header("🗓", "ДЕНЕЖНЫЕ СОБЫТИЯ", f"{n} шт. · нажмите, чтобы раскрыть" if n else None), parts)


def build_news(d):
    items = d.get("news") or []
    if not items:
        return header("📰", "НОВОСТИ") + "\nнет свежих новостей по ключевым темам"
    return fold(header("📰", "НОВОСТИ", f"{len(items)} шт. · нажмите, чтобы раскрыть"), news_lines(items))


def build_blocks(d, now):
    today = now.date()
    head = (f"📊 <b>Подробно · {WEEKDAYS[now.weekday()].lower()}, {now.day} {MONTHS[now.month - 1]}</b>\n"
            f"<i>🟢 рост · 🔴 снижение за день · YTD — с начала года</i>")
    blocks = [head]
    if not card_ok(d):
        blocks.append(build_moves(d, today))
    blocks += events.daily_blocks(today, d, d.get("watch"))
    blocks.append(build_watch(d, today))
    if d.get("bondsweek"):
        blocks.append(bondsweek.block(d["bondsweek"], today))
    return blocks + [build_fx(d), build_rates(d, today), build_yields(d, today),
                     build_markets(d), build_events(d, today), build_news(d)]


def card_ok(d):
    return bool(d.get("_card"))


def caption(d, now):
    """Подпись к карточке дня: дата, сильные движения, повод для звонка, ваш список (только в чате — на сайт не публикуется)."""
    lines = [f"<b>Рынки · {now.day} {MONTHS[now.month - 1]}</b>"]
    moves = strong_moves(d, now.date())
    if moves:
        lines += [f"{i} {e(t)}" for i, t in moves[:4]]
    else:
        lines.append("Спокойное утро: сильных движений нет.")
    hooks = d.get("_hooks") or []
    if hooks:
        lines.append("💡 <b>Повод для звонка</b>")
        lines += [f"• {e(h)}" for h in hooks[:2]]
    nm = next_meeting(now.date())
    if nm and nm == now.date():
        lines.append("🏦 Сегодня заседание ЦБ — флеш с решением около 13:35.")
    watch = watch_lines(d, now.date())
    out = "\n".join(lines)
    if watch:
        extra = "\n⭐ <b>Ваш список</b>"
        for w in watch:
            if len(_plain(out + extra + "\n" + w)) > 1000:
                break
            extra += "\n" + w
        out += extra
    return out


def _plain(html):
    import re as _re
    return _re.sub(r"<[^>]+>", "", html)


def watch_lines(d, today):
    """Строки «Ваш список» (без заголовка) — только для подписи в чате."""
    b = build_watch(d, today)
    if not b:
        return []
    return [ln for ln in b.split("\n")[1:] if ln.strip()][:6]


VIEWS = [("📋 Подробнее", "details"), ("📰 Новости", "news"), ("💰 Денежные события: дивиденды, сделки", "money"),
         ("🆕 Размещения облигаций на 2 недели", "placements")]


def keyboard():
    """Кнопки под карточкой. Адреса — с параметром ?v=… : Telegram, открывая страницу внутри чата,
    дописывает свои данные после «#», поэтому якоря вида #CNY в Mini App не работают."""
    dash = pages_url()
    if dash:
        base = dash if dash.endswith("/") else dash + "/"
        rows = [
            # полные подписи: длинные — по одной в строке, чтобы Telegram их не обрезал на телефоне
            [(VIEWS[0][0], base + "?v=details"), (VIEWS[1][0], base + "?v=news")],
            [(VIEWS[2][0], base + "?v=money")],
            [(VIEWS[3][0], base + "?v=placements")],
            [("📊 Дашборд рынка", base + "?v=overview"), ("⚖ Сравнение", base + "bonds.html?v=compare")],
            [("🧾 Подбор облигаций и портфель", base + "bonds.html")],
        ]
        return {"inline_keyboard": [[{"text": t, "web_app": {"url": u}} for t, u in r] for r in rows]}
    rows = [
        [("📈 IMOEX", "https://www.moex.com/ru/index/IMOEX"), ("📉 Кривая ОФЗ", "https://www.moex.com/s2532"),
         ("🏦 Ключевая", "https://www.cbr.ru/hd_base/KeyRate/")],
        [("💰 Депозиты", "https://www.cbr.ru/statistics/avgprocstav/"), ("🆕 Размещения", "https://bonds-lab.ru/placements"),
         ("🗓 Дивиденды", "https://smart-lab.ru/dividends/")],
    ]
    return {"inline_keyboard": [[{"text": t, "url": u} for t, u in r] for r in rows]}


def placements_soon(pl, today, days=14):
    """Первичные размещения: сбор заявок или размещение в ближайшие days дней (по данным bonds-lab)."""
    if pl is None:
        return None
    t0, t1 = today.isoformat(), (today + timedelta(days=days)).isoformat()
    soon = [p for p in pl if (p.get("book") and t0 <= p["book"] <= t1) or (p.get("place") and t0 <= p["place"] <= t1)]
    soon.sort(key=lambda p: p.get("book") or p.get("place") or "")
    last = max((p.get("book") or "" for p in pl), default="") or None
    return {"items": soon, "days": days, "source_last": last, "total": len(pl)}


def public_sections(d, now):
    """То, что раньше шло текстом под карточкой, — для страницы дашборда (docs/digest.json).
    Список наблюдения (WATCHLIST) сюда НЕ попадает: сайт публичный."""
    today = now.date()
    pub = dict(d)
    pub["watch"] = None
    details = list(events.daily_blocks(today, pub, None))
    if not card_ok(d):
        details.insert(0, build_moves(pub, today))
    if d.get("bondsweek"):
        bw = dict(d["bondsweek"])
        details.append(bondsweek.block(bw, today))
    details += [build_fx(pub), build_rates(pub, today), build_yields(pub, today), build_markets(pub)]
    return {
        "date": today.isoformat(), "updated": now.strftime("%Y-%m-%d %H:%M"),
        "details": [b for b in details if b],
        "money": build_events(pub, today).replace(" · нажмите, чтобы раскрыть", ""),
        "news": build_news(pub).replace(" · нажмите, чтобы раскрыть", ""),
        "placements": placements_soon(d.get("placements"), today),
    }


def write_public(d, now):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "digest.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(public_sections(d, now), f, ensure_ascii=False)
    return path


def make_visuals(d, now, demo=False):
    """История рядов -> данные дашборда (docs/data.json) и карточка дня. Ошибки не ломают дайджест."""
    try:
        import card
        import charts
        if demo:
            series, curves = charts.demo_history(now.date())
        else:
            series, curves = charts.fetch_history(now.date())
            if series:
                charts.write_dashboard_data(series, curves, now)
        d["_hooks"] = events.call_hooks(series or {}, now.date())
        path = card.render_card(d, series or {}, now, strong_moves(d, now.date()))
        d["_card"] = path
    except Exception as ex:  # noqa: BLE001
        log(f"[warn] карточка/графики: {type(ex).__name__}: {ex}")


def now_msk():
    """Текущее время МСК; FAKE_NOW="2026-10-19 07:40" - для проверки поведения в другие дни."""
    fake = os.environ.get("FAKE_NOW")
    if fake:
        return datetime.fromisoformat(fake).replace(tzinfo=MSK)
    return datetime.now(MSK)


SEND_STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".digest_send.json")


def main():
    """Режимы: --build (собрать данные, карточку, docs/digest.json, подготовить отправку), --send (отправить
    подготовленное), без флагов — всё сразу. В GitHub Actions: build → публикация сайта → send,
    чтобы кнопки под карточкой сразу открывали свежий выпуск."""
    demo = "--demo" in sys.argv
    build = "--send" not in sys.argv
    send = "--build" not in sys.argv
    now = now_msk()
    if build:
        manual = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch" or os.environ.get("FORCE") == "1"
        if not demo and not manual and not events.is_workday(now.date()):
            log(f"{now:%d.%m} - нерабочий день, дайджест не отправляю (ручной запуск отправит).")
            if os.path.exists(SEND_STATE):
                os.remove(SEND_STATE)
            return
        data = demo_data() if demo else collect(now.date())
        if demo:
            data["bondsweek"] = bondsweek.demo(now.date())
            data["placements"] = data["bondsweek"]["placements"]
            data["watch"] = events.load_watchlist()
            data.setdefault("watch_news", [])
        core = [data.get("cur"), data.get("key"), data.get("IMOEX"), data.get("brent"), data.get("zcyc")]
        if not demo and not any(core):
            log("Ни один основной источник не ответил - дайджест не отправлен.")
            sys.exit(1)
        make_visuals(data, now, demo)
        try:
            write_public(data, now)
        except Exception as ex:  # noqa: BLE001
            log(f"[warn] digest.json: {type(ex).__name__}: {ex}")
        state = {"card": data.get("_card"), "caption": caption(data, now), "charts": data.get("_charts"),
                 "fallback": None if data.get("_card") else "\n\n".join(build_blocks(data, now))}
        with open(SEND_STATE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
    if send:
        if not os.path.exists(SEND_STATE):
            log("Нечего отправлять (нерабочий день или сборка не прошла).")
            return
        with open(SEND_STATE, encoding="utf-8") as f:
            st = json.load(f)
        kb = keyboard()
        if st.get("card") and os.path.exists(st["card"]):
            deliver_card(st["card"], st["caption"], kb)
        else:   # карточка не нарисовалась — как раньше, текстом
            deliver(pack([st["caption"], st.get("fallback") or ""]), keyboard=kb)
        # «Графики за год» больше не приходят отдельной картинкой — они в «Подробнее», в раскрывающемся блоке


def deliver_card(path, cap, kb=None):
    if os.environ.get("DRY_RUN") == "1" or "--demo" in sys.argv:
        print(f"[карточка] {path}\n{cap}\n" + "-" * 40)
        if kb:
            print("[кнопки]", " | ".join(b["text"] + " → " + ((b.get("web_app") or {}).get("url") or b.get("url", "")) for row in kb["inline_keyboard"] for b in row))
        return
    from common import tg_creds, tg_photo, tg_send
    token, chat = tg_creds()
    try:
        tg_photo(token, chat, path, cap, reply_markup=kb)
    except Exception as ex:  # noqa: BLE001
        log(f"[warn] карточка с кнопками не отправилась ({ex}) — пробую без кнопок")
        try:
            tg_photo(token, chat, path, cap)
            if kb:
                tg_send(token, chat, "Подробности 👇", reply_markup=kb)
        except Exception as ex2:  # noqa: BLE001
            log(f"ОШИБКА ОТПРАВКИ: {ex2}")
            sys.exit(1)


if __name__ == "__main__":
    main()
