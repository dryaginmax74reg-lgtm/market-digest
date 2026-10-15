#!/usr/bin/env python3
"""Недельный / месячный обзор -> Telegram: текст + картинка-одностраничник для клиентов.

python review.py --period week           итоги недели (по пятницам)
python review.py --period month --auto   итоги месяца, только если сегодня последний рабочий день месяца
python review.py --period week --demo    демо (печать + файл client_card.png)
CLIENT_SIGNATURE="Имя Фамилия · инвестиционный советник"  подпись внизу картинки (необязательно)
"""
import os
import sys
from datetime import date, datetime, timedelta

from charts import demo_history, fetch_history
from common import (MONTHS, MONTHS_NOM, MSK, chg_txt, ddmm, deliver, dividends_calendar, e, fnum, fpct, fpp, header,
                    last_on_or_before, log, moex_imoex_constituents, moex_shares_close, next_meeting, pack, parallel,
                    table)
from digest import build_events

HERE = os.path.dirname(os.path.abspath(__file__))
CARD = os.path.join(HERE, "client_card.png")
RATES = {"KEY", "DEP", "RGBI_Y", "CORP_Y", "RUONIA", "RUSFAR", "RUSFARCNY", "UST10"}


def last_weekday_of_month(d):
    nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    import events
    x = nxt - timedelta(days=1)
    while not events.is_workday(x):
        x -= timedelta(days=1)
    return x


def period_base(today, period):
    if period == "month":
        return date(today.year, today.month, 1) - timedelta(days=1)
    return today - timedelta(days=7)


def change(series, key, base_iso, y0_iso):
    s = series.get(key) or []
    if not s:
        return None, None, None
    last = s[-1][1]
    b, y = last_on_or_before(s, base_iso), last_on_or_before(s, y0_iso)
    if key in RATES:
        return last, (last - b if b is not None else None), (last - y if y is not None else None)
    return last, (last / b - 1) * 100 if b else None, (last / y - 1) * 100 if y else None


def deposit_accrual(series, from_iso, to_iso):
    """Доход по депозиту за период, % (начисление по макс. ставке топ-10, без капитализации внутри декады)."""
    s = series.get("DEP") or []
    if not s:
        return None
    t0, t1 = datetime.fromisoformat(from_iso), datetime.fromisoformat(to_iso)
    acc = 1.0
    for i, (d, r) in enumerate(s):
        a = max(datetime.fromisoformat(d), t0)
        b = datetime.fromisoformat(s[i + 1][0]) if i + 1 < len(s) else t1
        b = min(b, t1)
        if b > a:
            acc *= 1 + r / 100 * (b - a).days / 365
    return (acc - 1) * 100


ROWS = [  # ключ, подпись, знаков после запятой
    ("KEY", "Ключевая ЦБ", 2), ("DEP", "Депозит топ-10", 2), ("RGBI_Y", "Доходн. ОФЗ", 2),
    ("CORP_Y", "Доходн. корп.", 2), ("RGBITR", "ОФЗ TR", 1), ("RUCBTRNS", "Корп. TR", 1),
    ("IMOEX", "IMOEX", 0), ("USD", "USD/RUB", 2), ("CNY", "CNY/RUB", 2), ("BRENT", "Brent $", 1),
    ("GOLD", "Золото $", 0), ("BTC", "BTC $", 0),
]


def collect(today, period):
    series, curves = fetch_history(today, years=2)
    base = period_base(today, period)
    extra = parallel({"now": (moex_shares_close, today), "base": (moex_shares_close, base),
                      "imoex_list": (moex_imoex_constituents,), "divs": (dividends_calendar,)})
    return series, curves, extra


def demo(today):
    series, curves = demo_history(today)
    extra = {"now": ("x", {"SBER": 300, "GAZP": 130, "LKOH": 6100, "YDEX": 4200, "PLZL": 2100, "MTSS": 230,
                           "AFLT": 60, "VTBR": 80}),
             "base": ("x", {"SBER": 290, "GAZP": 138, "LKOH": 6000, "YDEX": 4000, "PLZL": 2200, "MTSS": 228,
                            "AFLT": 63, "VTBR": 78}),
             "imoex_list": {"SBER": "Сбербанк", "GAZP": "ГАЗПРОМ ао", "LKOH": "ЛУКОЙЛ", "YDEX": "Яндекс",
                            "PLZL": "Полюс", "MTSS": "МТС-ао", "AFLT": "Аэрофлот", "VTBR": "ВТБ ао"},
             "divs": []}
    return series, curves, extra


def leaders(extra, n=3):
    now, base, names = extra.get("now"), extra.get("base"), extra.get("imoex_list") or {}
    if not now or not base or not names:
        return [], []
    ch = []
    for t, nm in names.items():
        a, b = base[1].get(t), now[1].get(t)
        if a and b:
            ch.append((nm, (b / a - 1) * 100))
    ch.sort(key=lambda x: x[1], reverse=True)
    return ch[:n], ch[-n:][::-1]


def build_text(series, curves, extra, today, period):
    base = period_base(today, period)
    b_iso, y_iso = base.isoformat(), f"{today.year - 1}-12-31"
    pname = "НЕДЕЛИ" if period == "week" else "МЕСЯЦА"
    col = "нед." if period == "week" else "мес."
    if period == "week":
        mon = today - timedelta(days=today.weekday())
        sub = f"{ddmm(mon.isoformat())}–{ddmm(today.isoformat())}.{today.year}"
    else:
        sub = f"{MONTHS_NOM[today.month - 1]} {today.year}"
    blocks = [f"🗞 <b>ИТОГИ {pname}</b>\n<i>{sub} · изменения за {col} и с начала года (YTD)</i>"]

    rate_rows, mkt_rows = [], []
    for key, label, dg in ROWS:
        last, ch, ytd = change(series, key, b_iso, y_iso)
        if last is None:
            continue
        if key in RATES:
            rate_rows.append((label, f"{fnum(last)}%", fpp(ch), fpp(ytd)))
        else:
            mkt_rows.append((label, fnum(last, dg), chg_txt(ch, 1) if ch is not None else "",
                             "" if ytd is None else fpct(ytd, 1)))
    if rate_rows:
        blocks.append(header("🏦", "СТАВКИ И ДОХОДНОСТИ", "изменения в п.п.") + "\n"
                      + table(rate_rows, ("", "", col, "YTD")))
    if mkt_rows:
        blocks.append(header("📈", "РЫНКИ") + "\n" + table(mkt_rows, ("", "", col, "YTD")))

    # что принёс период: депозит против облигаций
    dep_p, dep_y = deposit_accrual(series, b_iso, today.isoformat()), deposit_accrual(series, y_iso, today.isoformat())
    cmp_rows = [("Депозит топ-10", "" if dep_p is None else fpct(dep_p, 2), "" if dep_y is None else fpct(dep_y, 1))]
    for key, label in (("RGBITR", "ОФЗ TR"), ("RUCBTRNS", "Корп. TR")):
        _l, ch, ytd = change(series, key, b_iso, y_iso)
        cmp_rows.append((label, "" if ch is None else fpct(ch, 2), "" if ytd is None else fpct(ytd, 1)))
    blocks.append(header("💰", "ДЕПОЗИТ vs ОБЛИГАЦИИ", "доход за период, %") + "\n" + table(cmp_rows, ("", col, "YTD")))

    # кривая ОФЗ
    now_c = curves.get("сегодня")
    if now_c:
        z_now = dict(now_c["points"])
        rows = []
        for p in (1, 2, 5, 10):
            v = z_now.get(float(p), z_now.get(p))
            if v is not None:
                rows.append((f"ОФЗ {p}г", f"{fnum(v)}%"))
        z0 = curves.get("начало года")
        if z0:
            d0 = dict(z0["points"])
            rows = [(r[0], r[1], fpp(z_now[float(p)] - d0[float(p)]) if float(p) in d0 else "")
                    for r, p in zip(rows, (1, 2, 5, 10))]
        blocks.append(header("📉", "КРИВАЯ ОФЗ") + "\n" + table(rows, ("", "", "YTD п.п.")))

    up, down = leaders(extra)
    if up:
        dnow, dbase = (extra.get("now") or ("",))[0], (extra.get("base") or ("",))[0]
        span = f"закрытия {ddmm(dbase)} → {ddmm(dnow)}" if len(dnow) == 10 and len(dbase) == 10 else ""
        lines = [header("🏆", f"ЛИДЕРЫ И АУТСАЙДЕРЫ {pname}", ("акции из индекса Мосбиржи; " + span).strip("; "))]
        lines += [f"🟢 {e(n)} {fpct(c, 1)}" for n, c in up]
        lines += [f"🔴 {e(n)} {fpct(c, 1)}" for n, c in down]
        blocks.append("\n".join(lines))

    nm = next_meeting(today)
    if nm and (nm - today).days <= 14:
        blocks.append(f"🗓 <b>Впереди:</b> заседание ЦБ по ставке {nm.day} {MONTHS[nm.month - 1]}")
    ev = build_events({"divs": extra.get("divs"), "imoex_list": extra.get("imoex_list"), "money_news": []}, today)
    if extra.get("divs"):
        blocks.append(ev)
    return blocks


# ---------------------------------------------------------------- картинка для клиентов

def client_card(series, today, period, path=CARD):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    INK, INK2, GRID, BG, ACC, NEG = "#14213d", "#5b6275", "#e3e6ec", "#ffffff", "#2a78d6", "#eb6834"
    base = period_base(today, period)
    b_iso, y_iso = base.isoformat(), f"{today.year - 1}-12-31"
    col = "за неделю" if period == "week" else "за месяц"
    title = "Рынки: итоги недели" if period == "week" else "Рынки: итоги месяца"
    mon = today - timedelta(days=today.weekday())
    sub = (f"{mon.day} {MONTHS[mon.month - 1]} – "
           f"{today.day} {MONTHS[today.month - 1]} {today.year}") if period == "week" \
        else f"{MONTHS_NOM[today.month - 1]} {today.year}"

    fig = plt.figure(figsize=(9, 11.25), dpi=120, facecolor=BG)
    plt.rcParams["font.family"] = "DejaVu Sans"
    fig.text(0.06, 0.955, title, fontsize=24, fontweight="bold", color=INK)
    fig.text(0.06, 0.928, sub, fontsize=13, color=INK2)

    rows = [("KEY", "Ключевая ставка ЦБ", "%"), ("DEP", "Вклады: макс. ставка топ-10 банков", "%"),
            ("RGBI_Y", "Доходность ОФЗ", "%"), ("CORP_Y", "Доходность корп. облигаций", "%"),
            ("IMOEX", "Индекс Мосбиржи", ""), ("USD", "Доллар, ₽ (ЦБ)", ""), ("CNY", "Юань, ₽ (ЦБ)", ""),
            ("BRENT", "Нефть Brent, $", ""), ("GOLD", "Золото, $", "")]
    y = 0.885
    fig.text(0.06, y, "", fontsize=10)
    fig.text(0.62, y, "сейчас", fontsize=10, color=INK2, ha="right")
    fig.text(0.79, y, col, fontsize=10, color=INK2, ha="right")
    fig.text(0.94, y, "с начала года", fontsize=10, color=INK2, ha="right")
    y -= 0.012
    fig.add_artist(plt.Line2D([0.06, 0.94], [y, y], color=GRID, lw=1))
    for key, label, unit in rows:
        last, ch, ytd = change(series, key, b_iso, y_iso)
        if last is None:
            continue
        y -= 0.04
        rate = key in RATES
        fig.text(0.06, y, label, fontsize=12.5, color=INK)
        dg = 2 if rate or last < 1000 else 0
        fig.text(0.62, y, f"{fnum(last, dg)}{'%' if rate else ''}", fontsize=12.5, color=INK, ha="right",
                 fontweight="bold")
        for x, v in ((0.79, ch), (0.94, ytd)):
            txt = "—" if v is None else (fpp(v) + " п.п." if rate else fpct(v, 1))
            fig.text(x, y, txt, fontsize=11.5, ha="right", color=INK2)
        fig.add_artist(plt.Line2D([0.06, 0.94], [y - 0.014, y - 0.014], color=GRID, lw=0.6))

    # Диаграмма: что принёс год
    vals = [("Вклад по макс. ставке топ-10*", deposit_accrual(series, y_iso, today.isoformat()))]
    for key, label in (("RGBITR", "ОФЗ (с купонами)"), ("RUCBTRNS", "Корп. облигации (с купонами)"),
                       ("IMOEX", "Акции (индекс Мосбиржи)"), ("GOLD", "Золото, $")):
        _l, _c, ytd = change(series, key, b_iso, y_iso)
        vals.append((label, ytd))
    vals = [v for v in vals if v[1] is not None]
    top = y - 0.07
    fig.text(0.06, top, "Что принёс год: доходность с 1 января", fontsize=14, fontweight="bold", color=INK)
    ax = fig.add_axes([0.38, 0.1, 0.5, top - 0.13])
    names = [v[0] for v in vals][::-1]
    nums = [v[1] for v in vals][::-1]
    ax.barh(names, nums, color=[ACC if n >= 0 else NEG for n in nums], height=0.55)
    span = max(abs(n) for n in nums) if nums else 1
    for i, n in enumerate(nums):
        ax.text(n + (span * 0.03 if n >= 0 else -span * 0.03), i, fpct(n, 1), va="center",
                ha="left" if n >= 0 else "right", fontsize=12, color=INK, fontweight="bold")
    ax.axvline(0, color=INK2, lw=0.8)
    ax.set_xlim(min(0, min(nums)) - span * 0.35, max(0, max(nums)) + span * 0.35)
    ax.set_xticks([])
    for s in ax.spines.values():
        s.set_visible(False)
    ax.tick_params(axis="y", length=0, labelsize=12, colors=INK)
    ax.set_facecolor(BG)

    sig = os.environ.get("CLIENT_SIGNATURE", "").strip()
    if sig:
        fig.text(0.06, 0.07, sig, fontsize=12, color=INK, fontweight="bold")
    fig.text(0.06, 0.018, "* Оценка сверху: доход при размещении под максимальную ставку топ-10 банков; реальный вклад обычно даёт меньше.\n"
                          "Источники: Банк России, Московская биржа, Yahoo Finance. Не является индивидуальной инвестиционной рекомендацией.", fontsize=8.5, color=INK2)
    fig.savefig(path, facecolor=BG)
    plt.close(fig)
    return path


def main():
    period = "month" if "month" in sys.argv else "week"
    is_demo = "--demo" in sys.argv
    today = datetime.now(MSK).date()
    if "--auto" in sys.argv and period == "month" and today != last_weekday_of_month(today):
        log(f"Сегодня {today} не последний рабочий день месяца - месячный обзор не нужен.")
        return
    series, curves, extra = demo(today) if is_demo else collect(today, period)
    if not series:
        log("Нет данных для обзора.")
        sys.exit(1)
    blocks = build_text(series, curves, extra, today, period)
    card = client_card(series, today, period)
    cap = "🖼 Картинка для клиентов: можно переслать как есть."
    deliver(pack(blocks), photos=[(card, cap)])


if __name__ == "__main__":
    main()
