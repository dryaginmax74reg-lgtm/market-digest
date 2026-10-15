#!/usr/bin/env python3
"""Алерты в течение дня: шлёт сообщение, только если что-то сдвинулось сильнее порога.
Пороги - MOVE_THRESHOLDS в common.py. Повторно по тому же инструменту пишет, только если движение
усилилось ещё на половину порога (например, IMOEX −1,5% -> алерт, −2,3% -> ещё алерт).
Состояние за день хранится в alerts_state.json (workflow коммитит его в репозиторий).

python alerts.py            боевой
python alerts.py --demo     демо-данные, печать в консоль
"""
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from common import (MOVE_THRESHOLDS, MSK, coingecko_btc, deliver, e, fnum, fpct, http_get, log, moex_cny_tom,
                    moex_index, parallel, pct, yahoo_quote)

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "alerts_state.json")
KEYRATE_RE = re.compile(r"ключев\w* ставк", re.IGNORECASE)


def load_state(today):
    try:
        with open(STATE, encoding="utf-8") as f:
            s = json.load(f)
        if s.get("date") == today:
            return s
    except Exception:  # noqa: BLE001
        pass
    return {"date": today, "levels": {}, "titles": []}


def save_state(s):
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=1)


def cbr_keyrate_news(hours=3):
    """Свежие сообщения ЦБ о ключевой ставке: [(заголовок, ссылка)]."""
    root = ET.fromstring(http_get("https://www.cbr.ru/rss/RssPress", retries=2, timeout=15))
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    out = []
    for it in root.iter("item"):
        t, link, pub = it.findtext("title") or "", it.findtext("link") or "", it.findtext("pubDate")
        if not KEYRATE_RE.search(t):
            continue
        try:
            if pub and parsedate_to_datetime(pub) < cutoff:
                continue
        except Exception:  # noqa: BLE001
            pass
        out.append((t.strip(), link.strip()))
    return out


def collect():
    return parallel({
        "IMOEX": (moex_index, "IMOEX"), "RGBITR": (moex_index, "RGBITR"), "RUCBTRNS": (moex_index, "RUCBTRNS"),
        "CNY": (moex_cny_tom,), "BRENT": (yahoo_quote, "BZ=F"), "GOLD": (yahoo_quote, "GC=F"),
        "BTC": (coingecko_btc,), "KEYNEWS": (cbr_keyrate_news,),
    })


def demo():
    return {"IMOEX": {"val": 2380.0, "chg": -1.9, "ytd": -14.0, "date": "2026-10-15"},
            "RGBITR": {"val": 760.0, "chg": -0.6, "ytd": 2.4, "date": "2026-10-15"},
            "RUCBTRNS": {"val": 211.0, "chg": -0.1, "ytd": 10.2, "date": "2026-10-15"},
            "CNY": (12.60, 1.2), "BRENT": (101.0, 103.0, 61.0), "GOLD": (4090.0, 4086.0, 4300.0),
            "BTC": (84000.0, 0.7),
            "KEYNEWS": [("Банк России принял решение снизить ключевую ставку на 100 б.п., до 13,00% годовых",
                         "https://www.cbr.ru/press/pr/")]}


def moves(d):
    """[(ключ, изменение_%, текст)]."""
    out = []
    for sid, label in (("IMOEX", "IMOEX"), ("RGBITR", "ОФЗ (RGBI TR)"), ("RUCBTRNS", "Корп. облигации TR")):
        r = d.get(sid)
        if r and r.get("chg") is not None:
            out.append((sid, r["chg"], f"{label} {fpct(r['chg'])} за день → {fnum(r['val'])}"))
    c = d.get("CNY")
    if c and c[1] is not None:
        out.append(("CNY", c[1], f"CNY/RUB (биржа) {fpct(c[1])} → {fnum(c[0], 3)} ₽"))
    for key, label in (("BRENT", "Brent"), ("GOLD", "Золото")):
        q = d.get(key)
        if q and q[0] and q[1]:
            ch = pct(q[0], q[1])
            out.append((key, ch, f"{label} {fpct(ch, 1)} → ${fnum(q[0])}"))
    b = d.get("BTC")
    if b and b[1] is not None:
        out.append(("BTC", b[1], f"BTC {fpct(b[1], 1)} за 24ч → ${fnum(b[0], 0)}"))
    return out


def main():
    is_demo = "--demo" in sys.argv
    now = datetime.now(MSK)
    import events
    if not is_demo and not events.is_workday(now.date()):
        log("Нерабочий день - алерты не проверяю.")
        return
    state = load_state(now.date().isoformat())
    d = demo() if is_demo else collect()
    lines = []
    for key, ch, text in moves(d):
        thr = MOVE_THRESHOLDS.get(key)
        if thr is None or ch is None or abs(ch) < thr:
            continue
        prev = state["levels"].get(key, 0)
        if abs(ch) < prev + thr / 2 and prev:
            continue
        state["levels"][key] = round(abs(ch), 2)
        lines.append(f"{'🟢' if ch > 0 else '🔴'} {e(text)}")
    for title, link in d.get("KEYNEWS") or []:
        if title in state["titles"]:
            continue
        state["titles"].append(title)
        lines.append(f'🏦 <a href="{e(link)}">{e(title)}</a>')
    if not lines:
        log("Сильных движений нет.")
    else:
        msg = f"⚡ <b>АЛЕРТ · {now:%H:%M} МСК</b>\n" + "\n".join(lines)
        deliver([msg])
    if not is_demo:
        save_state(state)


if __name__ == "__main__":
    main()
