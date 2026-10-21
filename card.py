"""Карточка дня: картинка 1080×1350 в стиле «Кабинет» (как дашборд и страница облигаций):
бумажный фон, тёмно-синий — главное, бронза — тонкие акценты; текст — Onest, цифры и заголовки — Literata.
Используется digest.py: render_card(data, series, now) -> путь к PNG."""
import os
from datetime import timedelta

from common import MONTHS, WEEKDAYS, fnum, fpct, fpp, market_view, next_meeting, pct

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "card.png")

BG, CARD, INK, INK2, INK3, LINE = "#f4f0e8", "#fcfaf6", "#16202b", "#5d6670", "#8f969d", "#e5ded1"
ACC, ACC2, UP, DOWN, WARN = "#1b2f45", "#8f6e3c", "#2d6a4f", "#a3392b", "#9a5a12"
NAVY, CREAM, BRASS2 = "#1b2f45", "#f4f0e8", "#f0e6d3"
W, H = 1080, 1350


def _fonts():
    """Onest (текст) и Literata (цифры, заголовки) из файлов рядом со скриптом; иначе DejaVu."""
    import logging
    logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)
    from matplotlib import font_manager as fm
    have = set()
    for f, fam in (("Onest-Medium.ttf", "Onest"), ("Onest-SemiBold.ttf", "Onest"), ("Literata-Medium.ttf", "Literata")):
        p = os.path.join(HERE, "fonts", f)
        if os.path.exists(p):
            fm.fontManager.addfont(p)
            have.add(fam)
    sans = (["Onest"] if "Onest" in have else []) + ["DejaVu Sans"]
    serif = (["Literata"] if "Literata" in have else []) + ["DejaVu Serif"]
    return sans, serif


def render_card(d, series, now, moves=(), path=OUT):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.patches import FancyBboxPatch

    sans, serif = _fonts()
    plt.rcParams["font.family"] = sans
    today = now.date()
    fig = plt.figure(figsize=(W / 100, H / 100), dpi=100, facecolor=BG)

    def fx(x):
        return x / W

    def fy(y):
        return 1 - y / H

    def text(x, y, s, size, color=INK, weight=500, ha="left", va="top", disp=False):
        fig.text(fx(x), fy(y), s, fontsize=size, color=color, fontweight=weight, ha=ha, va=va,
                 family=serif if disp else sans)

    def box(x, y, w, h, color=CARD, r=34, z=0):
        p = FancyBboxPatch((fx(x), fy(y + h)), fx(w), h / H, boxstyle=f"round,pad=0,rounding_size={r / W}",
                           transform=fig.transFigure, facecolor=color, edgecolor="none", zorder=z,
                           mutation_aspect=W / H)
        fig.add_artist(p)
        return p

    def spark(x, y, w, h, key, color, days=92):
        pts = series.get(key) or []
        cut = (today - timedelta(days=days)).isoformat()
        pts = [v for dd, v in pts if dd >= cut]
        if len(pts) < 2:
            return
        ax = fig.add_axes([fx(x), fy(y + h), fx(w), h / H])
        ax.set_axis_off()
        xs = np.arange(len(pts))
        step = key in ("KEY", "DEP")
        if step:
            ax.step(xs, pts, where="post", color=color, lw=2.0)
        else:
            ax.plot(xs, pts, color=color, lw=2.0, solid_capstyle="round")
        lo, hi = min(pts), max(pts)
        pad = (hi - lo) * 0.15 or abs(hi) * 0.01 or 1
        ax.set_ylim(lo - pad, hi + pad)
        ax.fill_between(xs, pts, lo - pad, color=color, alpha=0.06, step="post" if step else None, lw=0)
        ax.set_xlim(0, len(pts) - 1)

    # ---------- шапка
    text(60, 44, "Рынки сегодня", 38, disp=True)
    text(60, 116, f"{WEEKDAYS[now.weekday()]}, {now.day} {MONTHS[now.month - 1]} · {now:%H:%M} МСК", 17, INK2)

    # ---------- светофор дня: «Спокойно» / «Внимание» / «Действовать» и главная причина
    y0 = 170
    st = d.get("_status")
    if st:
        lvl, word, reasons = st
        bg, fg = [("#e1ebe3", UP), ("#f3e6cf", WARN), ("#f3dcd6", DOWN)][lvl]
        box(60, 160, W - 120, 76, bg, r=26)
        fig.text(fx(96), fy(198), "●", fontsize=22, color=fg, ha="left", va="center", family=sans)
        fig.text(fx(128), fy(197), word, fontsize=26, color=fg, fontweight=500, ha="left", va="center", family=serif)
        why = (reasons[0] if reasons else "")
        if why:
            fig.text(fx(360), fy(198), why[:58] + ("…" if len(why) > 58 else ""), fontsize=15, color=INK2,
                     ha="left", va="center", family=sans)
        y0 = 256
    hero_h = 222
    box(60, y0, W - 120, hero_h, NAVY)
    key = (d.get("key") or [(None, None)])[-1][1]
    key0 = None
    for dd, v in d.get("key") or []:
        if dd <= f"{today.year - 1}-12-31":
            key0 = v
    text(95, y0 + 28, "Ключевая ставка ЦБ", 16, "#b9c4d0")
    text(95, y0 + 56, f"{fnum(key)}%" if key is not None else "н/д", 50, CREAM, disp=True)
    if key is not None and key0 is not None:
        text(95, y0 + 166, f"{fpp(key - key0)} п.п. с начала года", 15, "#b9c4d0")
    nm = next_meeting(today)
    rx = 560
    if nm:
        days = (nm - today).days
        text(rx, y0 + 28, "Следующее заседание", 14, "#b9c4d0")
        text(rx, y0 + 52, f"{nm.day} {MONTHS[nm.month - 1]}" + (" · сегодня" if days == 0 else f" · через {days} дн."),
             21, CREAM, disp=True)
    z = d.get("zcyc") or {}
    ofz1 = (z.get("points") or {}).get(1.0)
    mv = market_view(key, ofz1)
    if mv:
        spread, key_eff, view = mv
        text(rx, y0 + 100, "Рынок закладывает (по ОФЗ 1 год)", 14, "#b9c4d0")
        text(rx, y0 + 124, view, 21, "#e3c99a", disp=True)
        text(rx, y0 + 164, f"ОФЗ 1г {fnum(ofz1)}% vs ключевая ≈{fnum(key_eff)}%", 13, "#b9c4d0")
        text(rx, y0 + 186, "(обе — в годовом начислении)", 12, "#8d9aa8")

    # ---------- сильные движения
    y = y0 + hero_h + 22
    if moves:
        mh = 58 + 30 * min(len(moves), 3)
        box(60, y, W - 120, mh, BRASS2)
        text(90, y + 16, "Сильные движения", 14, ACC2, 600)
        for i, (_icon, t) in enumerate(moves[:3]):
            text(90, y + 46 + 30 * i, "• " + t, 15, INK)
        y += mh + 22

    # ---------- плитки 3×3
    cur = d.get("cur") or {"rates": {}}
    fx0 = (d.get("fx0") or (None, {}))[1]
    dep_ser = d.get("dep") or []
    dep = dep_ser[-1][1] if dep_ser else None
    dep0 = None
    for dd, v in dep_ser:
        if dd <= f"{today.year - 1}-12-31":
            dep0 = v
    corp_h = d.get("corp_h") or []
    corp_y = float(corp_h[-1][2]) if corp_h and corp_h[-1][2] else None

    def day_line(ch, suffix="за день"):
        if ch is None:
            return ("", INK2)
        sym = "▲" if round(ch, 2) > 0 else "▼" if round(ch, 2) < 0 else "▬"
        return (f"{sym} {fpct(ch)} {suffix}", UP if round(ch, 2) > 0 else DOWN if round(ch, 2) < 0 else INK2)

    def ytd_line(y_, pp=False):
        if y_ is None:
            return ""
        return f"с начала года {fpp(y_)} п.п." if pp else f"с начала года {fpct(y_, 1)}"

    tiles = []   # (подпись, значение, (строка1, цвет), строка2, ключ ряда, цвет линии)
    tiles.append(("Вклады, топ-10 банков", f"{fnum(dep)}%" if dep is not None else "н/д",
                  (f"к ключевой {fpp(dep - key)} п.п." if dep is not None and key is not None else "", INK2),
                  ytd_line(dep - dep0 if dep is not None and dep0 is not None else None, True), "DEP", ACC))
    tiles.append(("Доходность корп. облигаций", f"{fnum(corp_y)}%" if corp_y is not None else "н/д",
                  (f"к вкладам {fpp(corp_y - dep)} п.п." if corp_y is not None and dep is not None else "",
                   UP if corp_y is not None and dep is not None and corp_y > dep else INK2),
                  "индекс Мосбиржи", "CORP_Y", ACC))
    mm = d.get("mmix") or {}
    rc = mm.get("RUSFARCNY")
    rc0 = (d.get("rusfarcny0") or [(None, None)])[-1][1]
    tiles.append(("RUSFAR CNY (юань O/N)", f"{fnum(rc)}%" if rc is not None else "н/д",
                  ("ставка денег в юанях", INK2),
                  ytd_line(rc - rc0 if rc is not None and rc0 is not None else None, True), "RUSFARCNY", ACC))
    for code, label in (("USD", "Доллар, ₽ (ЦБ)"), ("CNY", "Юань, ₽ (ЦБ)")):
        v, p = cur["rates"].get(code, (None, None))
        tiles.append((label, fnum(v) if v else "н/д", day_line(pct(v, p)), ytd_line(pct(v, fx0.get(code))), code, None))
    r = d.get("IMOEX")
    if r:
        tiles.append(("Индекс Мосбиржи", fnum(r["val"], 0), day_line(r.get("chg")), ytd_line(r.get("ytd")), "IMOEX", None))
    else:
        tiles.append(("Индекс Мосбиржи", "н/д", ("", INK2), "", "IMOEX", None))
    for k, label, sk in (("brent", "Нефть Brent, $", "BRENT"), ("gold", "Золото, $", "GOLD")):
        q = d.get(k)
        if q and q[0]:
            tiles.append((label, fnum(q[0], 0 if q[0] > 1000 else 2), day_line(pct(q[0], q[1])),
                          ytd_line(pct(q[0], q[2])), sk, None))
        else:
            tiles.append((label, "н/д", ("", INK2), "", sk, None))
    b, by = d.get("btc"), d.get("btc_y")
    if b or by:
        price = b[0] if b else by[0]
        ch = b[1] if b else pct(by[0], by[1])
        tiles.append(("Bitcoin, $", fnum(price, 0), day_line(ch, "за 24ч"), ytd_line(pct(price, by[2]) if by else None),
                      "BTC", None))
    else:
        tiles.append(("Bitcoin, $", "н/д", ("", INK2), "", "BTC", None))

    gap = 20
    tw = (W - 120 - 2 * gap) / 3
    avail = H - 75 - y
    th = min(290, (avail - gap * 2) / 3)
    for i, (label, val, (l1, c1), l2, sk, lc) in enumerate(tiles[:9]):
        cx = 60 + (i % 3) * (tw + gap)
        cy = y + (i // 3) * (th + gap)
        box(cx, cy, tw, th, r=26)
        text(cx + 22, cy + 20, label, 12.5, INK2)
        text(cx + 22, cy + 42, val, 28, INK, disp=True)
        if l1:
            text(cx + 22, cy + 98, l1, 12.5, c1, 600)
        if l2:
            text(cx + 22, cy + 120, l2, 12, INK3)
        cut = (today - timedelta(days=92)).isoformat()
        pts = [v for dd_, v in (series.get(sk) or []) if dd_ >= cut]
        color = lc or (UP if len(pts) > 1 and pts[-1] >= pts[0] else DOWN)
        if th - 150 > 40:
            spark(cx + 22, cy + 148, tw - 44, th - 164, sk, color)

    text(W / 2, H - 38, "мини-графики — 3 месяца · источники: Банк России, Мосбиржа, Yahoo Finance", 12.5, INK3,
         ha="center")
    fig.savefig(path, facecolor=BG)
    plt.close(fig)
    return path


def render_evening(d, now, ups=(), downs=(), path=os.path.join(HERE, "card_evening.png")):
    """Вечерняя карточка: тёмно-синяя палитра «Кабинета» — закрытие дня, курс ЦБ на завтра, лидеры дня."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyBboxPatch

    sans, serif = _fonts()
    plt.rcParams["font.family"] = sans
    Wv, Hv = 1080, 1240
    NAV, TILE, CR, MUT, MINT, CORAL, GOLD = "#16273b", "#1f3550", "#f4f0e8", "#9fb0c2", "#8fd3b0", "#f0a093", "#e3c99a"
    fig = plt.figure(figsize=(Wv / 100, Hv / 100), dpi=100, facecolor=NAV)

    def fx(x):
        return x / Wv

    def fy(y):
        return 1 - y / Hv

    def text(x, y, s, size, color=CR, weight=500, ha="left", va="top", disp=False):
        fig.text(fx(x), fy(y), s, fontsize=size, color=color, fontweight=weight, ha=ha, va=va, family=serif if disp else sans)

    def box(x, y, w, h, color=TILE, r=28):
        fig.add_artist(FancyBboxPatch((fx(x), fy(y + h)), fx(w), h / Hv, boxstyle=f"round,pad=0,rounding_size={r / Wv}",
                                      transform=fig.transFigure, facecolor=color, edgecolor="none", mutation_aspect=Wv / Hv))

    def chg_txt(ch, d_=2):
        if ch is None:
            return "", MUT
        return (("▲ " if ch > 0 else "▼ " if ch < 0 else "") + fpct(ch, d_)), (MINT if ch > 0 else CORAL if ch < 0 else MUT)

    text(60, 50, "Итоги дня", 40, disp=True)
    text(60, 120, f"{WEEKDAYS[now.weekday()]}, {now.day} {MONTHS[now.month - 1]} · закрытие основной сессии", 17, MUT)

    tiles = []
    for k, lab in (("IMOEX", "Индекс Мосбиржи"), ("RGBITR", "ОФЗ (с купонами)"), ("RUCBTRNS", "Корп. облигации")):
        r = d.get(k)
        if r:
            tiles.append((lab, fnum(r["val"], 0 if r["val"] > 1000 else 2), r.get("chg")))
    c = d.get("CNY")
    if c and c[0]:
        tiles.append(("Юань, биржа", fnum(c[0], 3) + " ₽", c[1]))
    for k, lab in (("BRENT", "Нефть Brent, $"), ("GOLD", "Золото, $")):
        q = d.get(k)
        if q and q[0]:
            tiles.append((lab, fnum(q[0], 0 if q[0] > 1000 else 2), pct(q[0], q[1])))
    gap, y = 18, 180
    tw, th = (Wv - 120 - 2 * gap) / 3, 168
    for i, (lab, val, ch) in enumerate(tiles[:6]):
        cx, cy = 60 + (i % 3) * (tw + gap), y + (i // 3) * (th + gap)
        box(cx, cy, tw, th)
        text(cx + 22, cy + 20, lab, 13.5, MUT)
        text(cx + 22, cy + 46, val, 30, CR, disp=True)
        t, col = chg_txt(ch)
        text(cx + 22, cy + 118, t + (" за день" if t else ""), 14, col, 600)
    y += 2 * (th + gap) + 10

    cb = d.get("CBRT")
    if cb:
        box(60, y, Wv - 120, 150, "#24405e")
        text(90, y + 22, f"Курс ЦБ на {cb['date'][:5]}", 15, GOLD, 600)
        for i, code in enumerate(("USD", "EUR", "CNY")):
            v1, v0 = cb["rates"].get(code, (None, None))
            if not v1:
                continue
            x = 90 + i * 315
            text(x, y + 56, {"USD": "Доллар", "EUR": "Евро", "CNY": "Юань"}[code], 13.5, MUT)
            text(x, y + 78, fnum(v1, 4 if code == "CNY" else 2) + " ₽", 26, CR, disp=True)
            t, col = chg_txt(pct(v1, v0))
            text(x + 190, y + 88, t, 13.5, col, 600)
        y += 150 + gap + 4

    if ups or downs:
        box(60, y, Wv - 120, 210)
        text(90, y + 22, "Лидеры дня среди акций", 15, GOLD, 600)
        for col_i, (title, lst, colr) in enumerate((("рост", ups, MINT), ("падение", downs, CORAL))):
            x = 90 + col_i * 470
            text(x, y + 58, title, 13.5, MUT)
            for j, (ch, name, _sec) in enumerate(lst[:3]):
                text(x, y + 86 + j * 36, f"{name[:22]}", 18, CR)
                text(x + 380, y + 86 + j * 36, fpct(ch, 1), 18, colr, 600, ha="right")
        y += 210 + gap

    text(Wv / 2, y + 14, "источники: Мосбиржа, Банк России, Yahoo Finance · не инвестиционная рекомендация", 12.5, MUT, ha="center")
    fig.savefig(path, facecolor=NAV)
    plt.close(fig)
    try:                      # обрезать пустой низ
        from PIL import Image
        im = Image.open(path)
        im.crop((0, 0, im.width, min(im.height, int((y + 60) * im.height / Hv)))).save(path)
    except Exception:  # noqa: BLE001
        pass
    return path
