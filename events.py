"""Календарь и ритм недели: что показывать в какой день.

Даты обновляй раз в год:
  - CBR_MEETINGS в common.py   (cbr.ru/dkp/cal_mp)
  - FOMC_DECISIONS             (federalreserve.gov, раздел FOMC calendars)
  - RU_HOLIDAYS                (производственный календарь; биржа в эти дни не торгует)
"""
import os
import re
from datetime import date, timedelta

from common import CBR_MEETINGS, MONTHS, ddmm, e, fnum, fpp, header, market_view, news, news_lines, next_meeting

# Решения ФРС (второй день заседания, 21:00 МСК)
FOMC_DECISIONS = [
    date(2026, 1, 28), date(2026, 3, 18), date(2026, 4, 29), date(2026, 6, 17), date(2026, 7, 29),
    date(2026, 9, 16), date(2026, 10, 28), date(2026, 12, 9),
    date(2027, 1, 27), date(2027, 3, 17), date(2027, 4, 28), date(2027, 6, 9), date(2027, 7, 28),
    date(2027, 9, 15), date(2027, 10, 27), date(2027, 12, 8),
]

# Нерабочие будни (выходные считаются автоматически). 2027 - добавить, когда выйдет постановление.
RU_HOLIDAYS = {
    date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7), date(2026, 1, 8),
    date(2026, 1, 9), date(2026, 2, 23), date(2026, 3, 9), date(2026, 5, 1), date(2026, 5, 11), date(2026, 6, 12),
    date(2026, 11, 4), date(2026, 12, 31),
    date(2027, 1, 1), date(2027, 1, 4), date(2027, 1, 5), date(2027, 1, 6), date(2027, 1, 7), date(2027, 1, 8),
}

WD_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def is_workday(d):
    return d.weekday() < 5 and d not in RU_HOLIDAYS


def next_workday(d):
    x = d + timedelta(days=1)
    while not is_workday(x):
        x += timedelta(days=1)
    return x


def dstr(d):
    return f"{WD_SHORT[d.weekday()]} {d.day:02d}.{d.month:02d}"


def in_index(ticker, allow):
    return not allow or ticker in allow or ticker.rstrip("P") in allow


# ---------------------------------------------------------------- понедельник: неделя впереди

def week_ahead(today, d, watch=None):
    """Блок «Неделя впереди» (понедельник или первый рабочий день недели)."""
    end = today + timedelta(days=6)
    items = []
    for m in CBR_MEETINGS:
        if today <= m <= end:
            items.append((m, "🏦 Заседание ЦБ по ключевой ставке (решение в 13:30)"))
    for m in FOMC_DECISIONS:
        if today <= m <= end:
            items.append((m, "🇺🇸 Решение ФРС по ставке (21:00 МСК)"))
    x = today
    while x <= end:
        if x.weekday() == 2 and is_workday(x):
            items.append((x, "📄 Аукционы ОФЗ Минфина (если объявлены)"))
        if x.weekday() == 2:
            items.append((x, "📊 Недельная инфляция Росстата (вечером)"))
        if x.day in (1, 11, 21):
            items.append((x, "💰 ЦБ: новая ставка по вкладам топ-10 банков"))
        if x.day == 20 or (x == today and 20 < x.day <= 28):
            items.append((x, "🧾 Налоговый период (до 28-го): экспортёры продают валюту"))
        x += timedelta(days=1)
    allow = set((d.get("imoex_list") or {}).keys())
    for v in d.get("divs") or []:
        r = v.get("registry")
        if r and today.isoformat() <= r <= end.isoformat() and (in_index(v["ticker"], allow) or is_watched(v, watch)):
            star = "⭐ " if is_watched(v, watch) else ""
            items.append((date.fromisoformat(r), f"✂️ {star}Отсечка: {e(v['name'])}, {e(v['div'])} ₽ ({e(v['yld'])})"))
    for x in sorted({h for h in RU_HOLIDAYS if today <= h <= end}):
        items.append((x, "🎌 Нерабочий день, биржа закрыта"))
    if not items:
        return ""
    items.sort(key=lambda t: t[0])
    lines = [f"<b>{dstr(dt)}</b> · {txt}" for dt, txt in items]
    return header("🗓", "НЕДЕЛЯ ВПЕРЕДИ", f"{today.day}–{end.day} {MONTHS[end.month - 1]}") + "\n" + "\n".join(lines)


# ---------------------------------------------------------------- блоки по дням

def meeting_preview(today, d):
    """Накануне заседания ЦБ (последний рабочий день перед ним)."""
    nm = next_meeting(today + timedelta(days=1))
    if not nm or next_workday(today) != nm:
        return ""
    key = (d.get("key") or [(None, None)])[-1][1]
    z = (d.get("zcyc") or {}).get("points") or {}
    lines = [f"Завтра, {nm.day} {MONTHS[nm.month - 1]}, в 13:30 — решение по ключевой ставке."]
    if key is not None:
        lines.append(f"Сейчас ставка {fnum(key)}%.")
    mv = market_view(key, z.get(1.0))
    if mv:
        lines.append(f"Рынок закладывает {mv[2]} (ОФЗ 1г {fnum(z[1.0])}% vs ключевая ≈{fnum(mv[1])}%).")
    hist = d.get("key") or []
    changes = [(a, b) for (a, b), (_c, pb) in zip(hist[1:], hist[:-1]) if b != pb]
    if changes:
        a, b = changes[-1]
        lines.append(f"Последнее изменение: {ddmm(a)} → {fnum(b)}%.")
    lines.append("Флеш с решением придёт около 13:35.")
    return header("⏰", "ЗАВТРА ЗАСЕДАНИЕ ЦБ") + "\n" + e(" ".join(lines))


def tax_period(today):
    if 20 <= today.day <= 28:
        return "🧾 <i>Налоговый период (до 28-го): экспортёры продают валютную выручку — обычно это поддерживает рубль.</i>"
    return ""


def deposit_fresh(d, today):
    """Пометка, если ЦБ только что опубликовал новую декадную ставку."""
    ser = d.get("dep") or []
    if len(ser) < 2:
        return ""
    last, prev = ser[-1], ser[-2]
    if not 0 <= (today - date.fromisoformat(last[0])).days <= 3:
        return ""
    return (f"🆕 <i>ЦБ обновил ставку по вкладам топ-10: {fnum(last[1])}% "
            f"({fpp(last[1] - prev[1])} п.п. к прошлой декаде).</i>")


def season_notes(today):
    out = []
    if today.month == 12 and today.day <= 30:
        out.append("🎄 <i>До 31.12: взносы на ИИС для вычета за этот год; продажа бумаг в убытке для сальдирования "
                   "налога; у клиентов годовые бонусы и дивиденды — повод для звонка.</i>")
    if today.month in (5, 6, 7):
        out.append("💸 <i>Сезон дивидендов: календарь отсечек расширен до 30 дней.</i>")
    return out


def ofz_auctions():
    """Среда: аукционы ОФЗ из новостей (Минфин объявляет накануне)."""
    pat = re.compile(r"(аукцион\w*.*ОФЗ|ОФЗ.*аукцион|Минфин\w* (провед|разместит|предложит))", re.IGNORECASE)
    items = news(3, pat, hours=48, per_source=2, cbr_all=False)
    if not items:
        return ""
    return header("📄", "АУКЦИОНЫ ОФЗ") + "\n" + "\n".join(news_lines(items))


AUCT_RE = re.compile(r"(?=.*ОФЗ)(?=.*(разместил|размещен|размещено|спрос|итог))", re.IGNORECASE)


def _bln(num, unit):
    v = float(num.replace("\xa0", "").replace(" ", "").replace(",", "."))
    return v * 1000 if unit.lower().startswith("трлн") else v


def parse_auction_title(t):
    """Из заголовка «Минфин разместил ОФЗ на 96,4 млрд руб. при спросе 133,4 млрд руб.» — (размещено, спрос) в млрд ₽."""
    num = r"(\d[\d\s\xa0]*(?:[,.]\d+)?)\s*(млрд|трлн)"
    pl = re.search(r"(?:разместил\w*|размещ\w*)[^.]*?(?:на|в)\s+" + num, t, re.IGNORECASE)
    dm = (re.search(r"спрос\w*\s+(?:(?:в|на|составил\w*)\s+)?" + num, t, re.IGNORECASE)
          or re.search(r"спрос\w*[^.]*?(?:в|на|составил\w*)\s+" + num, t, re.IGNORECASE))
    return (_bln(*pl.groups()) if pl else None), (_bln(*dm.groups()) if dm else None)


def ofz_auction_results(today):
    """Четверг: итоги вчерашних аукционов ОФЗ — заголовки лент (Интерфакс, РБК, Коммерсантъ) + объём и спрос из заголовка."""
    items = news(4, AUCT_RE, hours=40, per_source=2, cbr_all=False, skip_exclude=True)
    items = [x for x in items if "ОФЗ" in x[1] and not re.search(r"проведет|проведёт|предложит|объявил\w* аукцион", x[1], re.IGNORECASE)]
    if not items:
        return None
    best = None
    for src, t, link in items:
        pl, dm = parse_auction_title(t)
        if pl and (best is None or (dm and not best[1])):
            best = (pl, dm, t, link, src)
    return {"news": items[:3], "placed": best[0] if best else None, "demand": best[1] if best else None}


def weekly_inflation():
    """Четверг: недельная инфляция (Росстат публикует в среду ~19:00) - заголовки из новостей."""
    pat = re.compile(r"инфляц\w*.*(недел|с \d+ по \d+)|недельн\w* инфляц|цены за неделю|с начала года.*инфляц", re.IGNORECASE)
    items = news(2, pat, hours=30, per_source=1, cbr_all=False)
    if not items:
        return ""
    return header("📊", "НЕДЕЛЬНАЯ ИНФЛЯЦИЯ") + "\n" + "\n".join(news_lines(items))


def daily_blocks(today, d, watch=None):
    """Блоки, зависящие от дня. Возвращает список (в порядке показа)."""
    out = []
    wd = today.weekday()
    first_workday_of_week = wd == 0 or (wd > 0 and all(not is_workday(today - timedelta(days=i)) for i in range(1, wd + 1)))
    if first_workday_of_week:
        out.append(week_ahead(today, d, watch))
    out.append(meeting_preview(today, d))
    if wd == 2:
        out.append(ofz_auctions())
    if wd == 3:
        out.append(weekly_inflation())
    notes = [deposit_fresh(d, today), tax_period(today)] + season_notes(today)
    notes = [n for n in notes if n]
    if notes:
        out.append("\n".join(notes))
    return [b for b in out if b]


def div_window(today):
    return 30 if today.month in (5, 6, 7) else int(os.environ.get("DIV_DAYS", "14"))


# ---------------------------------------------------------------- список наблюдения

def load_watchlist():
    """Секрет WATCHLIST: компании через перевод строки или «;». Можно «Название|ТИКЕР».
    Пишите основу слова, чтобы ловить падежи: «Норникел», «Сбер», «Аэрофлот»."""
    raw = os.environ.get("WATCHLIST", "")
    out = []
    for part in re.split(r"[;\n]+", raw):
        part = part.strip()
        if not part or part.startswith("#"):
            continue
        names = [x.strip() for x in part.split("|") if x.strip()]
        out.append(names)
    return out


def is_watched(v, watch):
    """v: dict дивиденда или строка заголовка."""
    if not watch:
        return False
    text = v if isinstance(v, str) else f"{v.get('name', '')} {v.get('ticker', '')}"
    low = text.lower()
    for names in watch:
        for n in names:
            if n.isupper() and len(n) <= 6:          # тикер - точное совпадение слова
                if re.search(rf"(?<![A-Za-zА-Яа-я]){re.escape(n)}(?![A-Za-zА-Яа-я])", text):
                    return True
            elif n.lower() in low:
                return True
    return False


def watch_news(watch, exclude=()):
    """Все свежие новости (без тематического фильтра), где упомянута компания из списка."""
    if not watch:
        return []
    pat = re.compile("|".join(re.escape(n) for names in watch for n in names if not (n.isupper() and len(n) <= 6)) or "$^",
                     re.IGNORECASE)
    return news(8, pat, hours=26, per_source=4, cbr_all=False, exclude=exclude, skip_exclude=True)


# ---------------------------------------------------------------- повод для звонка

def _window(series, key, today, days):
    cut = (today - timedelta(days=days)).isoformat()
    return [(dd, v) for dd, v in (series.get(key) or []) if dd >= cut]


def _asof(series, key, iso):
    v = None
    for dd, x in series.get(key) or []:
        if dd <= iso:
            v = x
        else:
            break
    return v


def call_hooks(series, today):
    """Факты-экстремумы из истории: показываются, только когда показатель на максимуме/минимуме периода."""
    out = []
    # 1. разрыв доходности корп. облигаций и вкладов
    corp = _window(series, "CORP_Y", today, 92)
    if corp and series.get("DEP"):
        spreads = [(dd, v - (_asof(series, "DEP", dd) or v)) for dd, v in corp if _asof(series, "DEP", dd) is not None]
        if len(spreads) > 20:
            cur = spreads[-1][1]
            hi, lo = max(s for _d, s in spreads), min(s for _d, s in spreads)
            if cur >= hi - 0.02:
                out.append(f"Корп. облигации дают на {fnum(cur)} п.п. больше вкладов — максимальный разрыв за 3 месяца")
            elif cur <= lo + 0.02:
                out.append(f"Разрыв корп. облигаций и вкладов сжался до {fnum(cur)} п.п. — минимум за 3 месяца")
    # 2. ставки по вкладам несколько декад подряд в одну сторону
    dep = series.get("DEP") or []
    if len(dep) >= 4:
        last4 = [v for _d, v in dep[-4:]]
        if all(b < a for a, b in zip(last4, last4[1:])):
            out.append(f"Ставки по вкладам снижаются 3 декады подряд: {fnum(last4[0])}% → {fnum(last4[-1])}%")
        elif all(b > a for a, b in zip(last4, last4[1:])):
            out.append(f"Ставки по вкладам растут 3 декады подряд: {fnum(last4[0])}% → {fnum(last4[-1])}%")
    # 3. экстремумы рынков
    checks = [("RGBI_Y", "Доходность ОФЗ", 92, "3 месяца", "%"), ("IMOEX", "IMOEX", 365, "год", ""),
              ("USD", "Доллар", 92, "3 месяца", " ₽"), ("CNY", "Юань", 92, "3 месяца", " ₽"),
              ("GOLD", "Золото", 365 * 3, "3 года", " $")]
    for key, label, days, per, unit in checks:
        w = _window(series, key, today, days)
        if len(w) < 20:
            continue
        cur = w[-1][1]
        vals = [v for _d, v in w]
        d0 = 0 if cur > 1000 else 2
        if cur >= max(vals):
            out.append(f"{label} {fnum(cur, d0)}{unit} — максимум за {per}")
        elif cur <= min(vals):
            out.append(f"{label} {fnum(cur, d0)}{unit} — минимум за {per}")
    return out
