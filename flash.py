#!/usr/bin/env python3
"""Флеш в день заседания ЦБ: ждёт пресс-релиз о ключевой ставке (13:30 МСК) и сразу присылает,
что изменилось: новая ставка, ожидал ли рынок, разрывы с ОФЗ, вкладами и корп. облигациями.

python flash.py            боевой (выходит сразу, если сегодня не день заседания)
python flash.py --demo     пример сообщения (без интернета)
FORCE=1                    запустить и не в день заседания (для проверки)
"""
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

from common import (CBR_MEETINGS, MONTHS, MSK, cbr_deposit_series, cbr_key_series, deliver, e, fnum, fpp,
                    http_get, key_effective, log, market_view, moex_index_hist, moex_zcyc, moex_zcyc_back, next_meeting,
                    pages_url, parallel, pcts, table)

DECISION_RE = re.compile(r"ключев\w* ставк", re.IGNORECASE)
RATE_RE = re.compile(r"(\d{1,2}[,.]\d{1,2})\s*%")


def find_decision(today):
    """Пресс-релиз ЦБ о ключевой ставке, опубликованный сегодня: (заголовок, ссылка, новая ставка) или None."""
    root = ET.fromstring(http_get("https://www.cbr.ru/rss/RssPress", retries=2, timeout=15))
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        if not DECISION_RE.search(title):
            continue
        pub = it.findtext("pubDate")
        try:
            if pub and parsedate_to_datetime(pub).astimezone(MSK).date() != today:
                continue
        except Exception:  # noqa: BLE001
            pass
        m = RATE_RE.search(title)
        if m:
            return title, (it.findtext("link") or "").strip(), float(m.group(1).replace(",", "."))
    return None


def wait_decision(today, minutes):
    deadline = time.time() + minutes * 60
    while True:
        try:
            r = find_decision(today)
            if r:
                return r
        except Exception as ex:  # noqa: BLE001
            log(f"[warn] RSS ЦБ: {ex}")
        if time.time() > deadline:
            return None
        time.sleep(60)


def build(today, before, title, link, new_rate):
    key_old = before.get("key_old")
    diff = None if key_old is None else new_rate - key_old
    if diff is None:
        verb = "Решение ЦБ"
    elif diff < 0:
        verb = f"ЦБ снизил ставку на {abs(diff) * 100:.0f} б.п."
    elif diff > 0:
        verb = f"ЦБ повысил ставку на {diff * 100:.0f} б.п."
    else:
        verb = "ЦБ сохранил ставку"
    lines = [f"⚡ <b>{verb}: ключевая {fnum(new_rate)}%</b>"]
    exp = before.get("expected")
    if exp and diff is not None:
        exp_dir = "down" if "снижение" in exp else "up" if "повышение" in exp else "flat"
        fact = "down" if diff < 0 else "up" if diff > 0 else "flat"
        ok = "✅ совпало с ожиданиями рынка" if exp_dir == fact else "❗ рынок ожидал другого"
        lines.append(f"<i>Накануне рынок закладывал {e(exp)} — {ok}.</i>")

    ke = key_effective(new_rate)
    rows = [("Ключевая", pcts(new_rate), f"было {fnum(key_old)}" if key_old is not None else "")]
    z = before.get("zcyc_now") or {}
    for p in (1, 2, 5):
        v = z.get(float(p))
        if v is not None:
            rows.append((f"ОФЗ {p}г", pcts(v), f"{fpp(v - ke)} к кл.*"))
    dep = before.get("dep")
    if dep is not None:
        rows.append(("Вклады топ-10", pcts(dep), f"{fpp(dep - new_rate)} к кл."))
    corp = before.get("corp")
    if corp is not None:
        rows.append(("Корп. облиг.", pcts(corp), f"{fpp(corp - (dep or new_rate))} к вкл."))
    lines.append(table(rows))
    lines.append("<i>* ОФЗ сравниваются с ключевой в годовом начислении (≈" + fnum(ke) + "%). "
                 "Ставки по вкладам ЦБ публикует раз в декаду — реакция банков будет видна в ближайших публикациях.</i>")
    nm = next_meeting(today + timedelta(days=1))
    if nm:
        lines.append(f"🗓 Следующее заседание: {nm.day} {MONTHS[nm.month - 1]}")
    if link:
        lines.append(f'<a href="{e(link)}">Пресс-релиз ЦБ</a>')
    return "\n".join(lines)


def snapshot(today):
    r = parallel({
        "key": (cbr_key_series, today - timedelta(days=30), today),
        "zcyc_prev": (moex_zcyc_back, today - timedelta(days=1)),
        "zcyc_now": (moex_zcyc,),
        "dep": (cbr_deposit_series,),
        "corp": (moex_index_hist, "RUCBTRNS", today - timedelta(days=10), today),
    })
    out = {"key_old": (r.get("key") or [(None, None)])[-1][1]}
    zp = (r.get("zcyc_prev") or {}).get("points") or {}
    mv = market_view(out["key_old"], zp.get(1.0))
    out["expected"] = mv[2] if mv else None
    out["zcyc_now"] = (r.get("zcyc_now") or {}).get("points") or {}
    out["dep"] = (r.get("dep") or [(None, None)])[-1][1]
    ch = r.get("corp") or []
    out["corp"] = float(ch[-1][2]) if ch and ch[-1][2] else None
    return out


def keyboard():
    dash = pages_url()
    if not dash:
        return None
    base = dash if dash.endswith("/") else dash + "/"
    return {"inline_keyboard": [[{"text": "🏦 Ставки на дашборде", "url": base + "#RATES"},
                                 {"text": "🧮 Калькулятор", "url": base + "#CALC"}]]}


def main():
    now = datetime.now(MSK)
    today = now.date()
    if "--demo" in sys.argv:
        before = {"key_old": 14.0, "expected": "снижение ставки", "zcyc_now": {1.0: 13.5, 2.0: 14.6, 5.0: 15.9},
                  "dep": 13.06, "corp": 16.6}
        msg = build(today, before, "Банк России снизил ключевую ставку на 100 б.п., до 13,00% годовых",
                    "https://www.cbr.ru/press/pr/", 13.0)
        deliver([msg], keyboard=keyboard())
        return
    if today not in CBR_MEETINGS and os.environ.get("FORCE") != "1":
        log(f"{today} - не день заседания ЦБ, флеш не нужен.")
        return
    before = snapshot(today)
    found = wait_decision(today, int(os.environ.get("WAIT_MINUTES", "40")))
    if not found:
        log("Пресс-релиз о ключевой ставке не найден за отведённое время.")
        sys.exit(1)
    title, link, rate = found
    # кривая ОФЗ уже успела отреагировать - берём свежую
    try:
        before["zcyc_now"] = moex_zcyc()["points"]
    except Exception as ex:  # noqa: BLE001
        log(f"[warn] кривая после решения: {ex}")
    deliver([build(today, before, title, link, rate)], keyboard=keyboard())


if __name__ == "__main__":
    main()
