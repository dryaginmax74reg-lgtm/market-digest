"""Смысловой слой утреннего дайджеста (без ИИ — правила на цифрах):
  since_yesterday   — что изменилось со вчера (ставки, кривая ОФЗ, вклады, курсы, новые размещения, рейтинги)
  day_status        — светофор дня: «Спокойно» / «Внимание» / «Действовать» + причины
  client_answers    — ответы на 5 типовых вопросов клиента с сегодняшними цифрами
  spark             — мини-график из символов ▁▂▃▄▅▆▇█ для строк таблиц
  story             — данные для «Утро за 30 секунд» (Mini App, docs/digest.json)
  voice_text        — текст голосовой сводки примерно на минуту
Все функции терпят отсутствие любых данных: чего нет — того нет в выводе."""
from datetime import timedelta

from common import MONTHS, MOVE_THRESHOLDS, fnum, fpct, fpp, market_view, next_meeting, pct

BARS = "▁▂▃▄▅▆▇█"


# ---------------------------------------------------------------- помощники

def _last2(series, key, max_age_days=None, today=None):
    """(последнее значение, предыдущее значение, дата последнего) из ряда [(iso, v)]."""
    pts = [p for p in (series or {}).get(key) or [] if p[1] is not None]
    if len(pts) < 2:
        return None, None, None
    if max_age_days is not None and today is not None and pts[-1][0] < (today - timedelta(days=max_age_days)).isoformat():
        return None, None, None
    v1 = pts[-1][1]
    for dd, v in reversed(pts[:-1]):
        if dd < pts[-1][0]:
            return v1, v, pts[-1][0]
    return None, None, None


def _ago(series, key, days, today):
    """Значение ряда на дату today − days (последнее до неё)."""
    cut = (today - timedelta(days=days)).isoformat()
    v = None
    for dd, x in (series or {}).get(key) or []:
        if dd <= cut:
            v = x
        else:
            break
    return v


def curve_at(z, t):
    """Доходность кривой ОФЗ на срок t лет (линейная интерполяция)."""
    pts = sorted((float(k), v) for k, v in ((z or {}).get("points") or {}).items())
    if not pts:
        return None
    if t <= pts[0][0]:
        return pts[0][1]
    for (a, va), (b, vb) in zip(pts, pts[1:]):
        if a <= t <= b:
            return va + (vb - va) * (t - a) / (b - a) if b > a else va
    return pts[-1][1]


def spark(series, key, today, days=30, n=8):
    """Мини-график за days дней: n символов ▁…█ (ступенчатые ряды — как есть)."""
    cut = (today - timedelta(days=days)).isoformat()
    pts = [v for dd, v in (series or {}).get(key) or [] if dd >= cut and v is not None]
    if len(pts) < 3:
        return ""
    # n точек равномерно по ряду (последняя — всегда последняя)
    idx = [round(i * (len(pts) - 1) / (n - 1)) for i in range(n)]
    vs = [pts[i] for i in idx]
    lo, hi = min(vs), max(vs)
    if hi - lo < 1e-12:
        return BARS[3] * n
    return "".join(BARS[min(7, int((v - lo) / (hi - lo) * 7.999))] for v in vs)


def _dep(d):
    ser = d.get("dep") or []
    return ser[-1][1] if ser else None


def _key(d):
    ser = d.get("key") or []
    return ser[-1][1] if ser else None


def _corp(d):
    ch = d.get("corp_h") or []
    return float(ch[-1][2]) if ch and ch[-1][2] else None


# ---------------------------------------------------------------- со вчера

def since_yesterday(d, today, strong_keys=()):
    """[(короткий текст для подписи, полный текст, важность 0..2)] — только то, что заметно изменилось."""
    s = d.get("_series") or {}
    out = []
    k1, k0, _ = _last2(s, "KEY")
    if k1 is not None and k0 is not None and abs(k1 - k0) > 1e-6 and (d.get("key") or [[None]])[-1][0] >= (today - timedelta(days=3)).isoformat():
        out.append((f"ключевая {fnum(k1)}%", f"Ключевая ставка: {fnum(k0)}% → {fnum(k1)}%", 2))
    z1, z0 = d.get("zcyc"), d.get("zcyc1")
    if z1 and z0 and z1.get("date") != z0.get("date"):
        ch = [(t, curve_at(z1, t) - curve_at(z0, t)) for t in (1, 3, 5, 10) if curve_at(z1, t) is not None and curve_at(z0, t) is not None]
        if ch and max(abs(c) for _, c in ch) >= 0.05:
            big = max(ch, key=lambda x: abs(x[1]))
            out.append((f"ОФЗ {big[0]}г {fpp(big[1])} п.п.",
                        "Кривая ОФЗ: " + " · ".join(f"{t}г {fpp(c)}" for t, c in ch) + " п.п.", 1 if abs(big[1]) >= 0.15 else 0))
    v1, v0, dd = _last2(s, "DEP", 3, today)
    if v1 is not None and abs(v1 - v0) >= 0.05:
        out.append((f"вклады {fpp(v1 - v0)} п.п.", f"Вклады топ-10: {fnum(v1)}% ({fpp(v1 - v0)} п.п. — новая декада)", 1))
    v1, v0, _ = _last2(s, "RUONIA", 5, today)
    if v1 is not None and abs(v1 - v0) >= 0.15:
        out.append((f"RUONIA {fpp(v1 - v0)}", f"RUONIA: {fnum(v1)}% ({fpp(v1 - v0)} п.п.)", 0))
    c1 = _corp(d)
    v1, v0, _ = _last2(s, "CORP_Y", 5, today)
    if v1 is not None and abs(v1 - v0) >= 0.1:
        out.append((f"корп. {fpp(v1 - v0)} п.п.", f"Доходность корпоративных облигаций: {fnum(c1 if c1 is not None else v1)}% ({fpp(v1 - v0)} п.п.)", 0))
    cur = (d.get("cur") or {}).get("rates") or {}
    for c in ("USD", "EUR", "CNY"):
        v, p = cur.get(c, (None, None))
        ch = pct(v, p)
        if ch is not None and abs(ch) >= 0.5 and c not in strong_keys and abs(ch) < MOVE_THRESHOLDS.get(c, 1.0):
            out.append((f"{c} {fpct(ch, 1)}", f"{c} по ЦБ: {fnum(v)} ₽ ({fpct(ch, 2)})", 0))
    new = d.get("_new_placements") or []
    if new:
        names = ", ".join(new[:3]) + (f" и ещё {len(new) - 3}" if len(new) > 3 else "")
        out.append((f"{len(new)} нов. размещ.", f"Новые размещения: {names}", 0))
    rd = d.get("_rating_day") or []
    if rd:
        nd = sum(1 for x in rd if x[6])
        out.append((f"рейтинги ↓{nd} ↑{len(rd) - nd}", f"Рейтинги облигаций: ↓ {nd} · ↑ {len(rd) - nd}", 1 if nd else 0))
    out.sort(key=lambda x: -x[2])
    return out


# ---------------------------------------------------------------- светофор

def day_status(d, today, moves=(), since=()):
    """(уровень 0/1/2, слово, [причины]) — 2 «Действовать», 1 «Внимание», 0 «Спокойно»."""
    reasons2, reasons1 = [], []
    nm = next_meeting(today)
    if nm and (nm - today).days == 0:
        reasons2.append("сегодня решение ЦБ по ставке")
    elif nm and (nm - today).days == 1:
        reasons1.append("завтра заседание ЦБ")
    rd = d.get("_rating_day") or []
    dflt = [x for x in rd if x[5]]
    down = [x for x in rd if x[6] and not x[5]]
    from bondscore import rscore
    big = [x for x in dflt if rscore(x[2]) <= rscore("BBB-")]      # дефолт бывшего инвестиционного рейтинга — событие рынка
    if big:
        reasons2.append("дефолт: " + ", ".join(x[1][:24] for x in big[:2]))
    elif dflt:
        reasons1.append("дефолт: " + ", ".join(x[1][:24] for x in dflt[:2]))
    if down:
        reasons1.append("понижения рейтингов: " + ", ".join(x[1][:24] for x in down[:2]))
    for _icon, t in moves:
        reasons1.append(t)
    for _short, full, imp in since:
        if imp >= 2:
            reasons2.append(full)
    cap = lambda xs: [x[:1].upper() + x[1:] for x in xs]   # noqa: E731
    if reasons2:
        return 2, "Действовать", cap((reasons2 + reasons1)[:3])
    if reasons1:
        return 1, "Внимание", cap(reasons1[:3])
    return 0, "Спокойно", ["Сильных движений и событий нет"]


# ---------------------------------------------------------------- ответы клиенту

def client_answers(d, today):
    """[(вопрос, ответ)] — пять типовых вопросов с сегодняшними цифрами. Ответы — ориентир для разговора, не рекомендация."""
    s = d.get("_series") or {}
    out = []
    key, dep, z = _key(d), _dep(d), d.get("zcyc")
    o1, o3, o05 = curve_at(z, 1), curve_at(z, 3), curve_at(z, 0.5)
    mv = market_view(key, o1)

    # 1. Фиксировать доходность?
    if key is not None and o3 is not None:
        if mv and mv[0] <= -0.5:
            a = (f"Рынок закладывает {mv[2]}: ОФЗ на год дают {fnum(o1)}%, а ключевая в годовом начислении ≈{fnum(mv[1])}%. "
                 f"Сейчас можно зафиксировать {fnum(o3)}% на 3 года в ОФЗ" + (f" — вклад даёт {fnum(dep)}% и через год переоформится уже ниже" if dep else "")
                 + ". Каждое снижение ставки делает фиксацию дешевле — тянуть невыгодно.")
        elif mv and mv[0] >= 0.5:
            a = (f"Рынок ждёт повышения ставки (ОФЗ 1 год {fnum(o1)}% выше ключевой ≈{fnum(mv[1])}%). Длинную доходность "
                 f"можно набирать частями; короткие бумаги и флоатеры сейчас выигрывают.")
        else:
            a = (f"Рынок ждёт ставку около текущей ({fnum(key)}%). ОФЗ на 3 года — {fnum(o3)}%"
                 + (f", вклад — {fnum(dep)}%" if dep else "") + ". Фиксация на 2–3 года убирает риск снижения ставок без потери дохода.")
        out.append(("Фиксировать доходность сейчас или подождать?", a))

    # 2. Юань
    cur = (d.get("cur") or {}).get("rates") or {}
    cny = cur.get("CNY", (None, None))[0]
    if cny:
        m1 = pct(cny, _ago(s, "CNY", 30, today))
        y0 = (d.get("fx0") or (None, {}))[1].get("CNY")
        ytd = pct(cny, y0)
        fxy = ((d.get("bmeta") or {}).get("fxy") or {}).get("CNY")
        rc = (d.get("mmix") or {}).get("RUSFARCNY")
        a = (f"Юань по ЦБ — {fnum(cny)} ₽" + (f", за месяц {fpct(m1, 1)}" if m1 is not None else "")
             + (f", с начала года {fpct(ytd, 1)}" if ytd is not None else "") + ". ")
        if fxy:
            a += f"Облигации в юанях надёжных эмитентов дают около {fnum(fxy['med'] * 100, 1)}% годовых в валюте"
            a += (f" (короткие деньги в юанях — {fnum(rc)}%)" if rc is not None else "") + ". "
        a += "Это защита от ослабления рубля с доходом, а не ставка на курс."
        out.append(("Что с юанем — покупать?", a))

    # 3. Вклад или облигации
    if dep and o1:
        corp = _corp(d)
        base = 10e6
        a = (f"На 10 млн за год до налога: вклад топ-10 ≈ {fnum(base * dep / 100 / 1e3, 0)} тыс. ₽ ({fnum(dep)}%), "
             f"ОФЗ на 2–3 года ≈ {fnum(base * (o3 or o1) / 100 / 1e3, 0)} тыс. ({fnum(o3 or o1)}%)"
             + (f", корпоративные облигации в среднем по рынку ≈ {fnum(base * corp / 100 / 1e3, 0)} тыс. ({fnum(corp)}%, с учётом их риска)" if corp else "")
             + ". Облигации фиксируют доходность на весь срок и продаются в любой день без потери процентов; "
               "ставку вклада банк пересмотрит при переоформлении.")
        out.append(("Вклад или облигации?", a))

    # 4. Золото
    g = d.get("gold")
    usd = cur.get("USD", (None, None))[0]
    if g and g[0] and usd:
        rub_g = g[0] * usd / 31.1035
        y1 = pct(g[0], _ago(s, "GOLD", 365, today))
        ytd = pct(g[0], g[2])
        a = (f"Золото — ${fnum(g[0], 0)} за унцию, ≈{fnum(rub_g, 0)} ₽ за грамм"
             + (f"; с начала года {fpct(ytd, 1)}" if ytd is not None else "") + (f", за год {fpct(y1, 1)}" if y1 is not None else "")
             + " в долларах. Купонов нет: золото — страховка капитала на 5–10% портфеля, а не источник дохода.")
        out.append(("Золото сейчас — стоит ли?", a))

    # 5. Кэш на 3 месяца
    ru = _last2(s, "RUONIA")[0] or ((d.get("ruonia") or [(None, None)])[-1][1])
    if ru or o05:
        parts = []
        if ru:
            parts.append(f"фонды денежного рынка — около ставки RUONIA, сейчас {fnum(ru)}% годовых, деньги доступны каждый день")
        if dep:
            parts.append(f"вклад — до {fnum(dep)}% (макс. ставка топ-10), но досрочно проценты сгорают")
        if o05:
            parts.append(f"короткие ОФЗ до полугода — около {fnum(o05)}%")
        out.append(("Куда положить деньги на 3 месяца?", "; ".join(parts)[:1].upper() + "; ".join(parts)[1:] + "."))
    return out


# ---------------------------------------------------------------- «Утро за 30 секунд» и голос

def story(d, today, now, status, since, answers):
    """Данные для экранов-историй в Mini App: только публичное (без списка наблюдения)."""
    s = d.get("_series") or {}
    key, dep, z = _key(d), _dep(d), d.get("zcyc")
    cur = (d.get("cur") or {}).get("rates") or {}
    lvl, word, reasons = status
    rates = [["Ключевая ЦБ", key, None], ["Вклады топ-10", dep, None]]
    for t in (1, 3, 10):
        rates.append([f"ОФЗ {t} г", curve_at(z, t), None])
    corp = _corp(d)
    if corp:
        rates.append(["Корп. облигации", corp, None])
    fx = []
    for c, lab in (("USD", "Доллар"), ("EUR", "Евро"), ("CNY", "Юань")):
        v, p = cur.get(c, (None, None))
        if v:
            fx.append([lab, v, pct(v, p), spark(s, c, today)])
    mk = []
    for k, lab in (("IMOEX", "Индекс Мосбиржи"), ("RGBITR", "ОФЗ (с купонами)")):
        r = d.get(k)
        if r:
            mk.append([lab, r["val"], r.get("chg")])
    for k, lab in (("brent", "Brent, $"), ("gold", "Золото, $"), ("spx", "S&P 500")):
        q = d.get(k)
        if q and q[0]:
            mk.append([lab, q[0], pct(q[0], q[1])])
    ev = []
    for x in sorted(d.get("divs") or [], key=lambda v: v.get("buy_by") or ""):
        if x.get("buy_by") and today.isoformat() <= x["buy_by"] <= (today + timedelta(days=7)).isoformat():
            ev.append(f"{x['buy_by'][8:10]}.{x['buy_by'][5:7]} — последний день купить {x['name']} под дивиденд {x['div']} ₽")
    nm = next_meeting(today)
    if nm and (nm - today).days <= 7:
        ev.insert(0, f"{nm.day} {MONTHS[nm.month - 1]} — заседание ЦБ по ставке")
    rd = [f"{x[0]} {x[1][:40]}: {x[2]} → {x[3]}" for x in (d.get("_rating_day") or [])[:4]]
    fxy = (d.get("bmeta") or {}).get("fxy") or {}
    return {"status": {"level": lvl, "word": word, "reasons": reasons},
            "date": today.isoformat(), "time": now.strftime("%H:%M"),
            "since": [full for _s, full, _i in since],
            "rates": [r for r in rates if r[1] is not None],
            "fx": fx, "fxbonds": {c: {"med": v["med"], "best": v.get("best") or []} for c, v in fxy.items()},
            "markets": mk, "events": ev[:5], "ratings": rd,
            "answers": [{"q": q, "a": a} for q, a in answers]}


def _say(x, d=1):
    return fnum(x, d).replace(" ", "")


def voice_text(d, today, now, status, since):
    """Сводка для озвучки: короткие предложения, числа с одним знаком, без скобок и символов."""
    from common import WEEKDAYS
    lvl, word, reasons = status
    key, dep, z = _key(d), _dep(d), d.get("zcyc")
    cur = (d.get("cur") or {}).get("rates") or {}
    parts = [f"Доброе утро. {WEEKDAYS[now.weekday()]}, {now.day} {MONTHS[now.month - 1]}."]
    parts.append({0: "Утро спокойное.", 1: "Сегодня есть на что обратить внимание.", 2: "Сегодня важный день."}[lvl])
    if lvl and reasons:
        r0 = reasons[0].rstrip(".")
        parts.append(r0[:1].upper() + r0[1:] + ".")
    if key is not None:
        t = f"Ключевая ставка {_say(key)} процента"
        mv = market_view(key, curve_at(z, 1))
        if mv:
            t += f", рынок закладывает {mv[2]}"
        parts.append(t + ".")
    if dep is not None and curve_at(z, 3) is not None:
        parts.append(f"Вклады топ-10 дают до {_say(dep)} процента, ОФЗ на три года — {_say(curve_at(z, 3))}.")
    for _s, full, _i in [x for x in since if not x[1].startswith(("Кривая", "Рейтинги"))][:3]:
        parts.append(full.replace("п.п.", "пункта").replace("%", " процента").replace("₽", "рубля").replace("·", ",").replace("→", "до") + ".")
    fxs = []
    for c, nm in (("USD", "доллар"), ("CNY", "юань")):
        v = cur.get(c, (None, None))[0]
        if v:
            fxs.append(f"{nm} {_say(v, 2 if c == 'CNY' else 1)}")
    if fxs:
        parts.append("Курсы ЦБ: " + ", ".join(fxs) + " рубля.")
    r = d.get("IMOEX")
    if r and r.get("chg") is not None and abs(r["chg"]) >= 0.1:
        parts.append(f"Индекс Мосбиржи {'вырос' if r['chg'] > 0 else 'снизился'} на {_say(abs(r['chg']))} процента.")
    rd = d.get("_rating_day") or []
    down = [x for x in rd if x[6]]
    if down:
        parts.append("Понижены рейтинги: " + ", ".join(x[1][:30] for x in down[:3]) + ".")
    nm = next_meeting(today)
    if nm and 0 < (nm - today).days <= 3:
        parts.append(f"Заседание ЦБ через {(nm - today).days} {'день' if (nm - today).days == 1 else 'дня'}.")
    parts.append("Хорошего дня.")
    import re
    txt = " ".join(parts).replace("−", "минус ").replace("+", "плюс ").replace("(", ", ").replace(")", "").replace("  ", " ")
    return re.sub(r"\s+\.", ".", txt)
