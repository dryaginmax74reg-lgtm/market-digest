"""Карточка дня: картинка 1080×1350 в стиле дашборда (вариант «финтех»).
Используется digest.py: render_card(data, series, now) -> путь к PNG."""
import os
from datetime import timedelta

from common import MONTHS, WEEKDAYS, fnum, fpct, fpp, next_meeting, pct

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "card.png")

BG, CARD, INK, INK2, INK3, LINE = "#eef1f6", "#ffffff", "#101828", "#667085", "#98a2b3", "#e6eaf1"
ACC, ACC2, UP, DOWN, WARN = "#5b5bf7", "#8b5cf6", "#12b76a", "#f04438", "#f79009"
W, H = 1080, 1350


def _fonts():
    import logging
    logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)
    """Manrope из файлов рядом со скриптом (если загружены в репозиторий), иначе DejaVu Sans."""
    from matplotlib import font_manager as fm
    fam = []
    for f in ("Manrope-SemiBold.ttf", "Manrope-ExtraBold.ttf"):
        p = os.path.join(HERE, f)
        if os.path.exists(p):
            fm.fontManager.addfont(p)
            if "Manrope" not in fam:
                fam.append("Manrope")
    return fam + ["DejaVu Sans"]


def render_card(d, series, now, moves=(), path=OUT):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.patches import FancyBboxPatch

    fam = _fonts()
    plt.rcParams["font.family"] = fam
    today = now.date()
    fig = plt.figure(figsize=(W / 100, H / 100), dpi=100, facecolor=BG)

    def fx(x):
        return x / W

    def fy(y):
        return 1 - y / H

    def text(x, y, s, size, color=INK, weight=800, ha="left", va="top"):
        fig.text(fx(x), fy(y), s, fontsize=size, color=color, fontweight=weight, ha=ha, va=va)

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
            ax.step(xs, pts, where="post", color=color, lw=2.4)
        else:
            ax.plot(xs, pts, color=color, lw=2.4, solid_capstyle="round")
        lo, hi = min(pts), max(pts)
        pad = (hi - lo) * 0.15 or abs(hi) * 0.01 or 1
        ax.set_ylim(lo - pad, hi + pad)
        ax.fill_between(xs, pts, lo - pad, color=color, alpha=0.10, step="post" if step else None, lw=0)
        ax.set_xlim(0, len(pts) - 1)

    # ---------- шапка
    text(60, 46, "Рынки сегодня", 36)
    text(60, 106, f"{WEEKDAYS[now.weekday()]}, {now.day} {MONTHS[now.month - 1]} · {now:%H:%M} МСК", 17, INK2, 600)

    # ---------- главная карточка: ключевая ставка
    y0 = 158
    hero_h = 205
    hero = box(60, y0, W - 120, hero_h, ACC)
    gax = fig.add_axes([fx(60), fy(y0 + hero_h), fx(W - 120), hero_h / H])
    gax.set_axis_off()
    grad = np.linspace(0, 1, 256).reshape(1, -1)
    from matplotlib.colors import LinearSegmentedColormap
    cmap = LinearSegmentedColormap.from_list("g", [ACC, ACC2])
    im = gax.imshow(grad, aspect="auto", cmap=cmap, extent=[0, 1, 0, 1])
    im.set_clip_path(hero)
    key = (d.get("key") or [(None, None)])[-1][1]
    key0 = None
    for dd, v in d.get("key") or []:
        if dd <= f"{today.year - 1}-12-31":
            key0 = v
    text(95, y0 + 28, "Ключевая ставка ЦБ", 16, "#e9e7ff", 600)
    text(95, y0 + 58, f"{fnum(key)}%" if key is not None else "н/д", 44, "#ffffff")
    if key is not None and key0 is not None:
        text(95, y0 + 150, f"{fpp(key - key0)} п.п. с начала года", 15, "#e9e7ff", 600)
    nm = next_meeting(today)
    rx = 560
    if nm:
        days = (nm - today).days
        text(rx, y0 + 28, "Следующее заседание", 14, "#e9e7ff", 600)
        text(rx, y0 + 52, f"{nm.day} {MONTHS[nm.month - 1]}" + (" · сегодня" if days == 0 else f" · через {days} дн."),
             20, "#ffffff")
    z = d.get("zcyc") or {}
    ofz1 = (z.get("points") or {}).get(1.0)
    if ofz1 is not None and key is not None:
        s = ofz1 - key
        view = "снижение ставки" if s <= -0.5 else "повышение ставки" if s >= 0.5 else "ставку без изменений"
        text(rx, y0 + 100, "Рынок закладывает", 14, "#e9e7ff", 600)
        text(rx, y0 + 124, view, 20, "#ffffff")
        text(rx, y0 + 166, f"ОФЗ 1 год {fnum(ofz1)}% ({fpp(s)} п.п. к ключевой)", 13.5, "#e9e7ff", 600)

    # ---------- сильные движения
    y = y0 + hero_h + 22
    if moves:
        mh = 58 + 30 * min(len(moves), 3)
        box(60, y, W - 120, mh, "#fff4e5")
        text(90, y + 16, "Сильные движения", 14, "#b54708", 600)
        for i, (_icon, t) in enumerate(moves[:3]):
            text(90, y + 46 + 30 * i, "• " + t, 15, INK, 600)
        y += mh + 22

    # ---------- плитки 2×4
    cur = d.get("cur") or {"rates": {}}
    fx0 = (d.get("fx0") or (None, {}))[1]
    dep_ser = d.get("dep") or []
    dep = dep_ser[-1][1] if dep_ser else None
    corp_h = d.get("corp_h") or []
    corp_y = float(corp_h[-1][2]) if corp_h and corp_h[-1][2] else None

    def ytd_ret(v, b):
        r = pct(v, b)
        return None if r is None else r

    tiles = []
    # (подпись, значение, строка1 (текст, цвет), строка2, ключ ряда, цвет линии)
    tiles.append(("Вклады, топ-10 банков", f"{fnum(dep)}%" if dep is not None else "н/д",
                  (f"ключевая {fpp(dep - key)} п.п." if dep is not None and key is not None else "", INK2),
                  "максимальная ставка ЦБ", "DEP", ACC))
    tiles.append(("Доходность корп. облигаций", f"{fnum(corp_y)}%" if corp_y is not None else "н/д",
                  (f"к вкладам {fpp(corp_y - dep)} п.п." if corp_y is not None and dep is not None else "", UP
                   if corp_y is not None and dep is not None and corp_y > dep else INK2),
                  "индекс Мосбиржи", "CORP_Y", ACC))
    for code, label in (("USD", "Доллар, ₽"), ("CNY", "Юань, ₽")):
        v, p = cur["rates"].get(code, (None, None))
        ch = pct(v, p)
        y_ = ytd_ret(v, fx0.get(code))
        tiles.append((label, fnum(v) if v else "н/д",
                      (f"{'▲' if (ch or 0) > 0 else '▼' if (ch or 0) < 0 else '▬'} {fpct(ch)} за день" if ch is not None else "",
                       UP if (ch or 0) > 0 else DOWN if (ch or 0) < 0 else INK2),
                      f"с начала года {fpct(y_, 1)}" if y_ is not None else "", code, None))
    for sid, label in (("IMOEX", "Индекс Мосбиржи"), ("RGBITR", "ОФЗ с купонами (RGBI TR)")):
        r = d.get(sid)
        if r:
            ch = r.get("chg")
            tiles.append((label, fnum(r["val"], 0 if r["val"] > 1000 else 2),
                          (f"{'▲' if (ch or 0) > 0 else '▼' if (ch or 0) < 0 else '▬'} {fpct(ch)} за день" if ch is not None else "",
                           UP if (ch or 0) > 0 else DOWN if (ch or 0) < 0 else INK2),
                          f"с начала года {fpct(r['ytd'], 1)}" if r.get("ytd") is not None else "", sid, None))
        else:
            tiles.append((label, "н/д", ("", INK2), "", sid, None))
    for k, label, sk in (("brent", "Нефть Brent, $", "BRENT"), ("gold", "Золото, $", "GOLD")):
        q = d.get(k)
        if q and q[0]:
            ch = pct(q[0], q[1])
            y_ = pct(q[0], q[2])
            tiles.append((label, fnum(q[0], 0 if q[0] > 1000 else 2),
                          (f"{'▲' if (ch or 0) > 0 else '▼' if (ch or 0) < 0 else '▬'} {fpct(ch)} за день" if ch is not None else "",
                           UP if (ch or 0) > 0 else DOWN if (ch or 0) < 0 else INK2),
                          f"с начала года {fpct(y_, 1)}" if y_ is not None else "", sk, None))
        else:
            tiles.append((label, "н/д", ("", INK2), "", sk, None))

    gap = 22
    tw = (W - 120 - gap) / 2
    avail = H - 70 - y
    th = min(205, (avail - gap * 3) / 4)
    for i, (label, val, (l1, c1), l2, sk, lc) in enumerate(tiles[:8]):
        cx = 60 + (i % 2) * (tw + gap)
        cy = y + (i // 2) * (th + gap)
        box(cx, cy, tw, th)
        text(cx + 28, cy + 22, label, 14, INK2, 600)
        text(cx + 28, cy + 46, val, 30, INK)
        if l1:
            text(cx + 28, cy + 100, l1, 14, c1, 600)
        if l2:
            text(cx + 28, cy + 126, l2, 13, INK3, 600)
        cut = (today - timedelta(days=92)).isoformat()
        pts = [v for dd_, v in (series.get(sk) or []) if dd_ >= cut]
        color = lc or (UP if len(pts) > 1 and pts[-1] >= pts[0] else DOWN)
        if th > 160:
            spark(cx + tw * 0.6, cy + 72, tw * 0.34, th - 100, sk, color)

    text(W / 2, H - 38, "мини-графики — 3 месяца · источники: Банк России, Мосбиржа, Yahoo Finance", 12.5, INK3,
         600, ha="center")
    fig.savefig(path, facecolor=BG)
    plt.close(fig)
    return path
