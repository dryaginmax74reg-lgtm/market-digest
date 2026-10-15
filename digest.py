#!/usr/bin/env python3
"""Ежедневный рыночный дайджест -> Telegram. Без ИИ и платных API.

Запуск:  python digest.py            боевой (нужны TG_BOT_TOKEN, TG_CHAT_ID)
         python digest.py --demo     демо-данные, печать в консоль
         DRY_RUN=1 python digest.py  живые данные, печать в консоль
Настройки: NEWS_LIMIT (по умолчанию 5), MONEY_NEWS_LIMIT (5), DIV_DAYS (14).
Даты заседаний ЦБ и пороги «сильных движений» - в common.py.
"""
import os
import sys
from datetime import date, datetime, timedelta

from common import (MACRO_RE, MONEY_RE, MONTHS, MOVE_THRESHOLDS, MSK, WEEKDAYS, cbr_currencies,
                    cbr_deposit_series, cbr_key_series, cbr_rates, cbr_ruonia_series, chg_txt, coingecko_btc,
                    ddmm, deliver, dividends_calendar, e, fnum, fpct, fpp, header, last_on_or_before, log,
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
        "rusfar0": (moex_index_hist, "RUSFAR", y0 - timedelta(days=14), y0),
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
    return d


def demo_data():
    """Фиксированные данные для проверки вёрстки (порядок величин на 14.10.2026)."""
    return {
        "cur": {"date": "15.10.2026", "rates": {"USD": (83.87, 83.92), "EUR": (93.77, 94.04), "CNY": (12.47, 12.40)}},
        "fx0": ("31.12.2025", {"USD": 78.23, "EUR": 91.70, "CNY": 11.12}),
        "key": [("2025-12-30", 16.0), ("2026-10-13", 14.0), ("2026-10-14", 14.0)],
        "ruonia": [("2025-12-30", 15.79), ("2026-10-13", 13.73)],
        "dep": [("2025-12-21", 14.9), ("2026-09-21", 13.055), ("2026-10-01", 13.055)],
        "mmix": {"RUSFAR": 13.83, "RUSFARCNY": 0.43, "date": "2026-10-14"},
        "rusfar0": [("2025-12-29", 15.77, None, None)],
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
            rows.append((f"{code}/RUB", "н/д"))
            continue
        y = pct(v, base.get(code))
        rows.append((f"{code}/RUB", fnum(v, 2), chg_txt(pct(v, p)), "" if y is None else fpct(y, 1)))
    (u, up), (c, cp) = cur["rates"]["USD"], cur["rates"]["CNY"]
    if u and c:
        cross = u / c
        cross_p = up / cp if up and cp else None
        cross0 = base.get("USD") / base.get("CNY") if base.get("USD") and base.get("CNY") else None
        y = pct(cross, cross0)
        rows.append(("USD/CNY", fnum(cross, 4), chg_txt(pct(cross, cross_p)), "" if y is None else fpct(y, 1)))
    return header("💱", "ВАЛЮТЫ", f"курс ЦБ на {cur['date'][:5]}") + "\n" + table(rows, HEAD)


def build_rates(d, today):
    y0 = f"{today.year - 1}-12-31"
    rows = []
    key = key_now(d)
    key0 = last_on_or_before(d.get("key"), y0)
    rows.append(("Ключевая ЦБ", pcts(key), fpp(key - key0) if key is not None and key0 is not None else ""))
    mm = d.get("mmix") or {}
    for sid, label in (("RUSFAR", "RUSFAR O/N"), ("RUSFARCNY", "RUSFAR CNY")):
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
    if z and key is not None and 1.0 in z["points"]:
        ofz1 = z["points"][1.0]
        s = ofz1 - key
        if s <= -0.5:
            view = "рынок закладывает снижение ставки"
        elif s >= 0.5:
            view = "рынок закладывает повышение ставки"
        else:
            view = "рынок ждёт ставку около текущей"
        lines.append(f"🔮 ОФЗ 1г {fnum(ofz1)}% vs ключевая {fnum(key)}% ({fpp(s)} п.п.): {view}")
    if lines:
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

    row("Депозит топ-10", dep, dep0, spread=False)
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
    note = "доходность, % годовых"
    if dep_ser:
        dd = dep_ser[-1][0]
        dec = {"01": "I", "11": "II", "21": "III"}.get(dd[8:10], "")
        note += f"; депозит - макс. ставка ЦБ, {dec}.{dd[5:7]}"
    return header("💰", "ДЕПОЗИТ vs ОБЛИГАЦИИ", note) + "\n" + table(rows, ("", "", "к деп.", "YTD"))


def build_markets(d):
    rows = []
    for sid, label in (("RGBITR", "ОФЗ RGBI TR"), ("RUCBTRNS", "Корп. TR"), ("IMOEX", "IMOEX")):
        r = d.get(sid)
        if r:
            rows.append((label, fnum(r["val"]), chg_txt(r["chg"]), "" if r["ytd"] is None else fpct(r["ytd"], 1)))
        else:
            rows.append((label, "н/д"))
    for key, label in (("brent", "Brent $"), ("gold", "Золото $")):
        q = d.get(key)
        if q and q[0]:
            p, prev, y0 = q
            y = pct(p, y0)
            rows.append((label, fnum(p, 0 if p > 1000 else 2), chg_txt(pct(p, prev)), "" if y is None else fpct(y, 1)))
        else:
            rows.append((label, "н/д"))
    b, by = d.get("btc"), d.get("btc_y")
    if b or by:
        price = b[0] if b else by[0]
        ch = b[1] if b else pct(by[0], by[1])
        y = pct(price, by[2]) if by else None
        rows.append(("BTC $", fnum(price, 0), chg_txt(ch), "" if y is None else fpct(y, 1)))
    return header("📈", "РЫНКИ", "индексы Мосбиржи — итог прошлой сессии") + "\n" + table(rows, HEAD)


def build_events(d, today):
    days = int(os.environ.get("DIV_DAYS", "14"))
    divs = d.get("divs") or []
    allow = set((d.get("imoex_list") or {}).keys())

    def in_index(t):
        return not allow or t in allow or t.rstrip("P") in allow

    t0, t_reg, t_pay = today.isoformat(), (today + timedelta(days=days)).isoformat(), (today + timedelta(days=7)).isoformat()
    reg = sorted((x for x in divs if x["registry"] and t0 <= x["registry"] <= t_reg and in_index(x["ticker"])),
                 key=lambda x: x["registry"])[:8]
    pay = sorted((x for x in divs if x["pay_by"] and t0 <= x["pay_by"] <= t_pay and in_index(x["ticker"])),
                 key=lambda x: x["pay_by"])[:8]
    parts = [header("🗓", "ДЕНЕЖНЫЕ СОБЫТИЯ")]
    if divs:
        if reg:
            parts.append(f"<b>Дивидендные отсечки, {days} дн.</b> <i>(реестр · купить до)</i>")
            parts += [f"▪️ {ddmm(x['registry'])} · {e(x['name'])} · {e(x['div'])} ₽ ({e(x['yld'])})"
                      + (f" · до {ddmm(x['buy_by'])}" if x["buy_by"] else "") for x in reg]
        if pay:
            parts.append("<b>Выплаты дивидендов, 7 дн.</b> <i>(акционеры получают деньги)</i>")
            parts += [f"▪️ до {ddmm(x['pay_by'])} · {e(x['name'])} · {e(x['div'])} ₽" for x in pay]
        if not reg and not pay:
            parts.append("<i>отсечек и выплат по бумагам индекса в ближайшие дни нет</i>")
    else:
        parts.append("<i>календарь дивидендов недоступен</i>")
    mn = d.get("money_news") or []
    if mn:
        parts.append("<b>Сделки, размещения, выкупы</b>")
        parts += news_lines(mn)
    return "\n".join(parts)


def build_news(d):
    items = d.get("news") or []
    return "\n".join([header("📰", "НОВОСТИ")] + (news_lines(items) or ["нет свежих новостей по ключевым темам"]))


def build_blocks(d, now):
    today = now.date()
    head = (f"📊 <b>РЫНОЧНЫЙ ДАЙДЖЕСТ</b>\n"
            f"<i>{WEEKDAYS[now.weekday()]}, {now.day} {MONTHS[now.month - 1]} {now.year} · {now:%H:%M} МСК · "
            f"YTD — с начала года</i>")
    return [head, build_moves(d, today), build_fx(d), build_rates(d, today), build_yields(d, today),
            build_markets(d), build_events(d, today), build_news(d)]


def keyboard():
    """Кнопки под дайджестом. Если задан DASHBOARD_URL - каждая открывает свой график на дашборде,
    иначе - страницы ЦБ/Мосбиржи/Yahoo."""
    dash = pages_url()
    if dash:
        base = dash if dash.endswith("/") else dash + "/"
        rows = [
            [("📈 IMOEX", base + "#IMOEX"), ("💠 ОФЗ", base + "#RGBITR"), ("🏢 Корп.", base + "#RUCBTRNS")],
            [("📉 Кривая ОФЗ", base + "#CURVE"), ("🏦 Ставки", base + "#RATES"), ("💰 Депозит vs обл.", base + "#PERF")],
            [("💵 Доллар", base + "#USD"), ("🇨🇳 Юань", base + "#CNY"), ("🛢 Brent", base + "#BRENT")],
            [("🥇 Золото", base + "#GOLD"), ("🗓 Дивиденды", "https://smart-lab.ru/dividends/"), ("📊 Весь дашборд", base)],
        ]
    else:
        rows = [
            [("📈 IMOEX", "https://www.moex.com/ru/index/IMOEX"), ("📉 Кривая ОФЗ", "https://www.moex.com/s2532"),
             ("🏦 Ключевая", "https://www.cbr.ru/hd_base/KeyRate/")],
            [("💰 Депозиты", "https://www.cbr.ru/statistics/avgprocstav/"),
             ("🇨🇳 Юань", "https://www.cbr.ru/currency_base/dynamics/?UniDbQuery.VAL_NM_RQ=R01375"),
             ("🛢 Brent", "https://finance.yahoo.com/quote/BZ%3DF/")],
            [("🥇 Золото", "https://finance.yahoo.com/quote/GC%3DF/"), ("🗓 Дивиденды", "https://smart-lab.ru/dividends/")],
        ]
    return {"inline_keyboard": [[{"text": t, "url": u} for t, u in r] for r in rows]}


def main():
    demo = "--demo" in sys.argv
    now = datetime.now(MSK)
    data = demo_data() if demo else collect(now.date())
    core = [data.get("cur"), data.get("key"), data.get("IMOEX"), data.get("brent"), data.get("zcyc")]
    if not demo and not any(core):
        log("Ни один основной источник не ответил - дайджест не отправлен.")
        sys.exit(1)
    deliver(pack(build_blocks(data, now)), keyboard=keyboard())


if __name__ == "__main__":
    main()
