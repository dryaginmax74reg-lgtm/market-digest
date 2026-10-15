#!/usr/bin/env python3
"""История рядов -> картинка с графиками в Telegram + данные для дашборда (docs/data.json).

python charts.py           боевой: собрать историю, нарисовать, отправить картинку, обновить docs/data.json
python charts.py --demo    синтетические данные, только файлы (без отправки)
NO_PHOTO=1                 не отправлять картинку (только обновить дашборд)
"""
import json
import math
import os
import random
import sys
from datetime import date, datetime, timedelta

from common import (MONTHS, MSK, cbr_deposit_series, cbr_fx_series, cbr_key_series, cbr_ruonia_series, deliver,
                    fnum, log, moex_index_hist, moex_zcyc, moex_zcyc_back, parallel, yahoo_series)

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(HERE, "docs")
OUT_PNG = os.path.join(HERE, "charts.png")
YEARS = 3

# Описание рядов для дашборда: ключ -> (подпись, единица, группа)
META = {
    "IMOEX": ("Индекс Мосбиржи (IMOEX)", "пт", "Акции"),
    "RGBITR": ("ОФЗ: RGBI TR (с купонами)", "пт", "Облигации"),
    "RUCBTRNS": ("Корп. облигации: RUCBTRNS (TR)", "пт", "Облигации"),
    "CORP_Y": ("Доходность корп. облигаций", "%", "Доходности"),
    "RGBI_Y": ("Доходность ОФЗ (RGBI)", "%", "Доходности"),
    "DEP": ("Депозит: макс. ставка топ-10 банков", "%", "Доходности"),
    "KEY": ("Ключевая ставка", "%", "Ставки"),
    "RUONIA": ("RUONIA", "%", "Ставки"),
    "RUSFAR": ("RUSFAR O/N", "%", "Ставки"),
    "RUSFARCNY": ("RUSFAR CNY O/N", "%", "Ставки"),
    "USD": ("USD/RUB (ЦБ)", "₽", "Валюты"),
    "EUR": ("EUR/RUB (ЦБ)", "₽", "Валюты"),
    "CNY": ("CNY/RUB (ЦБ)", "₽", "Валюты"),
    "BRENT": ("Brent", "$", "Сырьё и мир"),
    "GOLD": ("Золото", "$", "Сырьё и мир"),
    "UST10": ("UST 10Y", "%", "Сырьё и мир"),
    "BTC": ("Bitcoin", "$", "Сырьё и мир"),
}


# ---------------------------------------------------------------- данные

def fetch_history(today, years=YEARS):
    d1 = today - timedelta(days=365 * years + 10)
    rng = "5y" if years > 2 else "2y"
    jobs = {
        "IMOEX": (moex_index_hist, "IMOEX", d1, today),
        "RGBITR": (moex_index_hist, "RGBITR", d1, today),
        "RUCBTRNS": (moex_index_hist, "RUCBTRNS", d1, today),
        "RGBI": (moex_index_hist, "RGBI", d1, today),
        "RUSFAR": (moex_index_hist, "RUSFAR", d1, today),
        "RUSFARCNY": (moex_index_hist, "RUSFARCNY", d1, today),
        "USD": (cbr_fx_series, "USD", d1, today),
        "EUR": (cbr_fx_series, "EUR", d1, today),
        "CNY": (cbr_fx_series, "CNY", d1, today),
        "KEY": (cbr_key_series, d1, today),
        "RUONIA": (cbr_ruonia_series, d1, today),
        "DEP": (cbr_deposit_series,),
        "BRENT": (yahoo_series, "BZ=F", rng),
        "GOLD": (yahoo_series, "GC=F", rng),
        "UST10": (yahoo_series, "^TNX", rng),
        "BTC": (yahoo_series, "BTC-USD", rng),
        "Z_NOW": (moex_zcyc,),
        "Z_Y0": (moex_zcyc_back, date(today.year - 1, 12, 31)),
        "Z_1Y": (moex_zcyc_back, today - timedelta(days=365)),
    }
    r = parallel(jobs, workers=6)
    s = {}
    for k in ("IMOEX", "RGBITR", "RUCBTRNS", "RUSFAR", "RUSFARCNY"):
        if r.get(k):
            s[k] = [(x[0], x[1]) for x in r[k]]
    if r.get("RUCBTRNS"):
        s["CORP_Y"] = [(x[0], float(x[2])) for x in r["RUCBTRNS"] if x[2]]
    if r.get("RGBI"):
        s["RGBI_Y"] = [(x[0], float(x[2])) for x in r["RGBI"] if x[2]]
    for k in ("USD", "EUR", "CNY", "KEY", "RUONIA", "DEP"):
        if r.get(k):
            s[k] = r[k]
    cut = d1.isoformat()
    for k in ("BRENT", "GOLD", "UST10", "BTC"):
        if r.get(k):
            s[k] = [x for x in r[k][0] if x[0] >= cut]
    curves = {}
    for k, label in (("Z_NOW", "сегодня"), ("Z_Y0", "начало года"), ("Z_1Y", "год назад")):
        z = r.get(k)
        if z:
            curves[label] = {"date": z["date"], "points": sorted(z["points"].items())}
    return {k: v for k, v in s.items() if v}, curves


def demo_history(today):
    rnd = random.Random(7)
    days = [today - timedelta(days=i) for i in range(365 * YEARS, -1, -1)]
    days = [d for d in days if d.weekday() < 5]

    def walk(start, vol, drift=0.0):
        v, out = start, []
        for d in days:
            v *= 1 + rnd.gauss(drift, vol)
            out.append((d.isoformat(), round(v, 4)))
        return out

    def steps(levels):
        out = []
        n = len(days)
        for i, d in enumerate(days):
            out.append((d.isoformat(), levels[min(len(levels) - 1, i * len(levels) // n)]))
        return out

    s = {
        "IMOEX": walk(3000, 0.011), "RGBITR": walk(600, 0.003, 0.0004), "RUCBTRNS": walk(160, 0.0015, 0.0005),
        "USD": walk(90, 0.006), "EUR": walk(98, 0.006), "CNY": walk(12.3, 0.006),
        "BRENT": walk(80, 0.018), "GOLD": walk(2400, 0.009, 0.0007), "BTC": walk(60000, 0.03, 0.0005),
        "KEY": steps([16, 18, 19, 21, 21, 21, 20, 18, 17, 16, 15, 14]),
        "DEP": steps([15, 17, 18.5, 21, 22, 21, 19.5, 17, 16, 15, 14, 13.05]),
    }
    s["RUONIA"] = [(d, v - 0.3 + rnd.gauss(0, 0.1)) for d, v in s["KEY"]]
    s["RUSFAR"] = [(d, v - 0.2 + rnd.gauss(0, 0.15)) for d, v in s["KEY"]]
    s["RUSFARCNY"] = [(d, max(0.1, 3 + rnd.gauss(0, 1.2))) for d, _ in s["KEY"]]
    s["CORP_Y"] = [(d, v + 2.5 + rnd.gauss(0, 0.2)) for d, v in s["KEY"]]
    s["RGBI_Y"] = [(d, v + 0.6 + rnd.gauss(0, 0.2)) for d, v in s["KEY"]]
    s["UST10"] = walk(4.3, 0.01)
    per = [0.25, 0.5, 1, 2, 3, 5, 7, 10, 15, 20]
    curves = {
        "сегодня": {"date": today.isoformat(), "points": [(p, 12.8 + 1.6 * math.log1p(p) / 3) for p in per]},
        "начало года": {"date": f"{today.year - 1}-12-30", "points": [(p, 12.1 + 2.4 * math.log1p(p) / 3) for p in per]},
        "год назад": {"date": (today - timedelta(days=365)).isoformat(),
                      "points": [(p, 17.5 - 1.5 * math.log1p(p) / 3) for p in per]},
    }
    return s, curves


# ---------------------------------------------------------------- дашборд

def write_dashboard_data(series, curves, now):
    os.makedirs(DOCS, exist_ok=True)
    out = {"updated": now.strftime("%Y-%m-%d %H:%M"), "series": {}, "curves": curves}
    for k, pts in series.items():
        label, unit, group = META.get(k, (k, "", "Прочее"))
        out["series"][k] = {"label": label, "unit": unit, "group": group,
                            "data": [[d, round(v, 4)] for d, v in pts]}
    with open(os.path.join(DOCS, "data.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    log(f"docs/data.json: {len(out['series'])} рядов")


# ---------------------------------------------------------------- картинка

C1, C2, C3 = "#2a78d6", "#eb6834", "#1baf7a"
INK, INK2, GRID, BG = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"


def _last_year(pts, today):
    cut = (today - timedelta(days=365)).isoformat()
    return [(d, v) for d, v in pts if d >= cut]


def render_collage(series, curves, today, path=OUT_PNG):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "axes.edgecolor": GRID,
                         "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
                         "axes.titleweight": "bold", "axes.titlesize": 11, "axes.titlecolor": INK})
    fig, axes = plt.subplots(3, 2, figsize=(9, 11), dpi=120, facecolor=BG)
    fig.suptitle(f"Рынки за год · {today.day} {MONTHS[today.month - 1]} {today.year}", fontsize=14,
                 fontweight="bold", color=INK, x=0.03, ha="left", y=0.985)

    def dt(pts):
        return [datetime.fromisoformat(d) for d, _ in pts], [v for _, v in pts]

    def style(ax, title, sub=None):
        ax.set_facecolor(BG)
        ax.grid(True, color=GRID, linewidth=0.6)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        ax.set_title(title, loc="left", pad=16)
        if sub:
            ax.text(0, 1.015, sub, transform=ax.transAxes, fontsize=8.5, color=INK2, va="bottom")
        ax.xaxis.set_major_locator(mdates.MonthLocator(bymonth=(1, 4, 7, 10)))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m.%y"))

    def line(ax, key, title, color=C1, fmt=lambda v: fnum(v), sub_extra=""):
        pts = _last_year(series.get(key) or [], today)
        if not pts:
            style(ax, title, "нет данных")
            return
        x, y = dt(pts)
        ax.plot(x, y, color=color, linewidth=2)
        ax.scatter([x[-1]], [y[-1]], color=color, s=30, zorder=3, edgecolor=BG, linewidth=1.5)
        ax.annotate(fmt(y[-1]), (x[-1], y[-1]), textcoords="offset points", xytext=(-8, 10), ha="right",
                    fontsize=9, color=INK, fontweight="bold",
                    bbox=dict(boxstyle="round,pad=0.25", fc=BG, ec=GRID, lw=0.8))
        chg = (y[-1] / y[0] - 1) * 100
        style(ax, title, f"за год {'+' if chg >= 0 else '−'}{fnum(abs(chg), 1)}%{sub_extra}")

    line(axes[0][0], "IMOEX", "IMOEX")
    # Облигации: индексы TR, приведённые к 100 год назад
    ax = axes[0][1]
    drawn = []
    for key, label, color in (("RGBITR", "ОФЗ (RGBI TR)", C1), ("RUCBTRNS", "Корп. (TR)", C2)):
        pts = _last_year(series.get(key) or [], today)
        if pts:
            x, y = dt(pts)
            base = y[0]
            y = [v / base * 100 for v in y]
            ax.plot(x, y, color=color, linewidth=2, label=f"{label} {'+' if y[-1] >= 100 else '−'}{fnum(abs(y[-1] - 100), 1)}%")
            drawn.append(1)
    style(ax, "Облигации с купонами", "год назад = 100")
    if drawn:
        ax.legend(frameon=False, fontsize=8.5, loc="upper left")
    line(axes[1][0], "USD", "USD/RUB (ЦБ)", fmt=lambda v: f"{fnum(v)} ₽")
    line(axes[1][1], "CNY", "CNY/RUB (ЦБ)", fmt=lambda v: f"{fnum(v)} ₽")
    # Ставки: ключевая, депозит, доходность корп.
    ax = axes[2][0]
    for key, label, color in (("KEY", "Ключевая", C1), ("DEP", "Депозит топ-10", C2), ("CORP_Y", "Корп. облиг.", C3)):
        pts = _last_year(series.get(key) or [], today)
        if pts:
            x, y = dt(pts)
            ax.plot(x, y, color=color, linewidth=2, label=f"{label} {fnum(y[-1])}%",
                    drawstyle="steps-post" if key in ("KEY", "DEP") else "default")
    style(ax, "Ставки и доходности", "% годовых")
    ax.legend(frameon=False, fontsize=8.5, loc="lower left")
    # Кривая ОФЗ
    ax = axes[2][1]
    for label, color in (("сегодня", C1), ("начало года", C2), ("год назад", C3)):
        c = curves.get(label)
        if c:
            xs = [p for p, _ in c["points"]]
            ys = [v for _, v in c["points"]]
            ax.plot(xs, ys, color=color, linewidth=2, marker="o", markersize=4,
                    label=f"{label} ({c['date'][8:10]}.{c['date'][5:7]}.{c['date'][2:4]})")
    ax.set_facecolor(BG)
    ax.grid(True, color=GRID, linewidth=0.6)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.set_title("Кривая ОФЗ (G-curve)", loc="left", pad=16)
    ax.text(0, 1.015, "доходность, % · срок, лет", transform=ax.transAxes, fontsize=8.5, color=INK2, va="bottom")
    ax.legend(frameon=False, fontsize=8.5)
    for a in axes.flat:
        a.tick_params(labelsize=8.5)
    fig.tight_layout(rect=(0, 0, 1, 0.965), h_pad=2.2)
    fig.savefig(path, facecolor=BG)
    plt.close(fig)
    return path


def main():
    demo = "--demo" in sys.argv
    now = datetime.now(MSK)
    series, curves = demo_history(now.date()) if demo else fetch_history(now.date())
    if not series:
        log("Нет данных для графиков.")
        sys.exit(1)
    write_dashboard_data(series, curves, now)
    path = render_collage(series, curves, now.date())
    log(f"Картинка: {path}")
    if demo or os.environ.get("NO_PHOTO") == "1":
        return
    deliver([], photos=[(path, "📊 Графики за год. Подробнее — кнопка «Дашборд» в дайджесте.")])


if __name__ == "__main__":
    main()
