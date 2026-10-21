#!/usr/bin/env python3
"""Вечерний итог дня (будни 18:45 МСК): чем закончился день и что завтра.

  — рынок к закрытию: IMOEX, ОФЗ и корпоративные облигации (индексы с купонами), биржевой юань, Brent, золото;
  — курс ЦБ на завтра (ЦБ устанавливает около 15:30) и изменение к сегодняшнему;
  — лидеры дня среди акций (оборот от SHARE_MIN_TURNOVER);
  — понижения рейтингов облигаций за день (сводные, smart-lab);
  — в среду — итоги аукционов ОФЗ;
  — завтра: заседание ЦБ, последний день купить под дивиденды, книги заявок по облигациям.

python evening.py            боевой
python evening.py --demo     демо-данные, печать в консоль"""
import json
import os
import sys
from datetime import datetime

import events
from common import (MONTHS, MSK, SHARE_MIN_TURNOVER, dividends_calendar, deliver, e, fnum, fpct, log, moex_cny_tom, moex_index,
                    next_meeting, pack, pages_url, parallel, pct, yahoo_quote)

HERE = os.path.dirname(os.path.abspath(__file__))


def leaders(rows, n=3):
    out = []
    for r in rows or []:
        prev, last = r.get("PREVPRICE"), r.get("LAST")
        if not (prev and last) or (r.get("VALTODAY") or 0) < SHARE_MIN_TURNOVER:
            continue
        out.append((pct(last, prev), r["SHORTNAME"], r["SECID"]))
    out.sort(key=lambda x: x[0])
    return out[::-1][:n], out[:n]


def collect(now):
    import alerts
    late = now.replace(hour=max(now.hour, 17))
    return parallel({
        "IMOEX": (moex_index, "IMOEX"), "RGBITR": (moex_index, "RGBITR"), "RUCBTRNS": (moex_index, "RUCBTRNS"),
        "CNY": (moex_cny_tom,), "BRENT": (yahoo_quote, "BZ=F"), "GOLD": (yahoo_quote, "GC=F"),
        "CBRT": (alerts.cbr_tomorrow, late), "SHARES": (alerts.board_rows, "shares", "TQBR"),
        "RATINGS": (alerts.rating_moves, now, True), "DIVS": (dividends_calendar,),
        "AUCT": (events.ofz_auction_results, now.date()) if now.weekday() == 2 else (lambda: None,),
    })


def demo():
    return {"IMOEX": {"val": 2431.5, "chg": 0.56}, "RGBITR": {"val": 769.1, "chg": 0.1}, "RUCBTRNS": {"val": 211.4, "chg": 0.05},
            "CNY": (12.55, -0.3), "BRENT": (101.4, 102.2, 61.0), "GOLD": (4101.0, 4086.0, 4300.0),
            "CBRT": {"date": "22.10.2026", "rates": {"USD": (83.40, 83.87), "EUR": (93.30, 93.77), "CNY": (12.53, 12.47)}},
            "SHARES": [{"SECID": "SGZH", "SHORTNAME": "Сегежа", "PREVPRICE": 1.40, "LAST": 1.29, "VALTODAY": 4.5e8},
                       {"SECID": "MTLR", "SHORTNAME": "Мечел", "PREVPRICE": 80.0, "LAST": 85.2, "VALTODAY": 9e8},
                       {"SECID": "SBER", "SHORTNAME": "Сбербанк", "PREVPRICE": 300.0, "LAST": 303.0, "VALTODAY": 2e10},
                       {"SECID": "GAZP", "SHORTNAME": "Газпром", "PREVPRICE": 130.0, "LAST": 128.0, "VALTODAY": 5e9}],
            "RATINGS": {"7": {"name": "Сегежа Групп", "from": "A-", "to": "BBB+", "n": 4}},
            "DIVS": [{"name": "Акрон", "ticker": "AKRN", "div": "146", "yld": "0,9%", "buy_by": "2026-10-22", "registry": "2026-10-23"}],
            "AUCT": {"news": [("Интерфакс", "Минфин РФ разместил ОФЗ на 96,4 млрд руб. при спросе 133,4 млрд руб.", "https://www.interfax.ru/")],
                     "placed": 96.4, "demand": 133.4}}


def tomorrow_lines(d, now):
    t = events.next_workday(now.date())
    out = []
    nm = next_meeting(now.date())
    if nm == t:
        out.append("🏦 Заседание ЦБ по ключевой ставке (решение в 13:30)")
    for x in d.get("DIVS") or []:
        if x.get("buy_by") == t.isoformat():
            out.append(f"✂️ Последний день купить {e(x['name'])} под дивиденд {e(x['div'])} ₽ ({e(x.get('yld', ''))})")
    try:
        with open(os.path.join(HERE, "docs", "digest.json"), encoding="utf-8") as f:
            pl = ((json.load(f).get("placements") or {}).get("items")) or []
        for p in pl:
            if p.get("book") == t.isoformat():
                out.append(f"🆕 Книга заявок: {e(p['name'])}" + (f" · {e(p['coupon'])}" if p.get("coupon") else ""))
    except Exception:  # noqa: BLE001
        pass
    if t.weekday() == 2:
        out.append("📄 Аукционы ОФЗ Минфина (если объявлены)")
    return t, out[:6]


def caption(d, now):
    """Подпись к вечерней карточке: то, чего нет на картинке (рейтинги, аукционы, завтра). Не длиннее 1000 знаков."""
    L = [f"🌆 <b>Итоги дня · {now.day} {MONTHS[now.month - 1]}</b>"]
    rt = d.get("RATINGS") or {}
    if rt:
        L.append("🧾 Рейтинги ↓: " + ", ".join(f"{e(g['name'][:30])} {e(g['from'])}→{e(g['to'])}" for g in list(rt.values())[:4]))
    a = d.get("AUCT")
    if a and a.get("placed"):
        L.append(f"📄 Аукционы ОФЗ: {fnum(a['placed'], 1)} млрд ₽" + (f" при спросе {fnum(a['demand'], 1)} млрд" if a.get("demand") else ""))
    t, tl = tomorrow_lines(d, now)
    if tl:
        L.append(f"\n🗓 <b>Завтра, {t.day} {MONTHS[t.month - 1]}</b>")
        L += tl
    out = "\n".join(L)
    import re
    while len(re.sub(r"<[^>]+>", "", out)) > 1000 and len(L) > 2:
        L.pop()
        out = "\n".join(L)
    return out


def build(d, now):
    L = [f"🌆 <b>Итоги дня · {now.day} {MONTHS[now.month - 1]}</b>"]
    rows = []
    for k, lab in (("IMOEX", "IMOEX"), ("RGBITR", "ОФЗ TR"), ("RUCBTRNS", "Корп. TR")):
        r = d.get(k)
        if r and r.get("chg") is not None:
            rows.append(f"{'🟢' if r['chg'] > 0 else '🔴' if r['chg'] < 0 else '⚪'} {lab} {fnum(r['val'], 0 if r['val'] > 1000 else 2)} ({fpct(r['chg'])})")
    c = d.get("CNY")
    if c and c[0]:
        rows.append(f"{'🟢' if (c[1] or 0) > 0 else '🔴'} Юань (биржа) {fnum(c[0], 3)} ₽ ({fpct(c[1])})")
    for k, lab in (("BRENT", "Brent"), ("GOLD", "Золото")):
        q = d.get(k)
        if q and q[0] and q[1]:
            ch = pct(q[0], q[1])
            rows.append(f"{'🟢' if ch > 0 else '🔴'} {lab} ${fnum(q[0], 0 if q[0] > 1000 else 2)} ({fpct(ch, 1)})")
    L += rows
    cb = d.get("CBRT")
    if cb:
        parts = []
        for code in ("USD", "EUR", "CNY"):
            v1, v0 = cb["rates"].get(code, (None, None))
            if v1:
                parts.append(f"{code} {fnum(v1, 4 if code == 'CNY' else 2)} ({fpct(pct(v1, v0), 2)})")
        if parts:
            L.append(f"\n💱 <b>Курс ЦБ на {e(cb['date'][:5])}</b>: " + " · ".join(parts))
    up, down = leaders(d.get("SHARES"))
    if up or down:
        L.append("\n📈 <b>Лидеры дня</b>")
        if up:
            L.append("рост: " + ", ".join(f"{e(n)} {fpct(ch, 1)}" for ch, n, _s in up if ch > 0))
        if down:
            L.append("падение: " + ", ".join(f"{e(n)} {fpct(ch, 1)}" for ch, n, _s in down if ch < 0))
    rt = d.get("RATINGS") or {}
    if rt:
        L.append("\n🧾 <b>Рейтинги — понижения за день</b>: " + ", ".join(
            f"{e(g['name'][:40])} {e(g['from'])}→{e(g['to'])}" for g in list(rt.values())[:6]))
    a = d.get("AUCT")
    if a and a.get("placed"):
        L.append(f"\n📄 <b>Аукционы ОФЗ</b>: размещено {fnum(a['placed'], 1)} млрд ₽"
                 + (f" при спросе {fnum(a['demand'], 1)} млрд" if a.get("demand") else ""))
    t, tl = tomorrow_lines(d, now)
    if tl:
        L.append(f"\n🗓 <b>Завтра, {t.day} {MONTHS[t.month - 1]}</b>")
        L += tl
    return "\n".join(x for x in L if x is not None)


def main():
    is_demo = "--demo" in sys.argv
    now = datetime.now(MSK)
    manual = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
    if not is_demo and not manual and not events.is_workday(now.date()):
        log("Нерабочий день — вечерний итог не отправляю.")
        return
    d = demo() if is_demo else collect(now)
    if not is_demo and not any(d.get(k) for k in ("IMOEX", "CNY", "CBRT")):
        log("Источники не ответили — вечерний итог не отправлен.")
        return
    msg = build(d, now)
    up, down = leaders(d.get("SHARES"))
    card_path = None
    try:
        import card
        card_path = card.render_evening(d, now, [x for x in up if x[0] > 0], [x for x in down if x[0] < 0])
    except Exception as ex:  # noqa: BLE001
        log(f"[warn] вечерняя карточка: {type(ex).__name__}: {ex}")
    dash = pages_url()
    kb = None
    if dash:
        base = dash if dash.endswith("/") else dash + "/"
        kb = {"inline_keyboard": [[{"text": "📊 Дашборд", "web_app": {"url": base + "?v=overview"}},
                                   {"text": "💬 Клиенту", "web_app": {"url": base + "?v=answers"}}]]}
    if card_path:
        from digest import deliver_card
        deliver_card(card_path, caption(d, now), kb)
    else:
        deliver(pack([msg]), keyboard=kb)
    # живое сообщение дня: пометить завершённым и открепить
    try:
        import alerts
        import live
        st = alerts.load_state(now.date().isoformat())
        live.close(st, now)
    except Exception as ex:  # noqa: BLE001
        log(f"[warn] живое сообщение: {ex}")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as ex:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        from common import fail
        fail(f"Вечерний итог упал: {type(ex).__name__}: {ex}")
