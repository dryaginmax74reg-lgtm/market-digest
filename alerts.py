#!/usr/bin/env python3
"""Алерты в течение дня: шлёт сообщение, только если что-то сдвинулось сильнее порога.
Пороги - MOVE_THRESHOLDS в common.py. Повторно по тому же инструменту пишет, только если движение
усилилось ещё на половину порога (например, IMOEX −1,5% -> алерт, −2,3% -> ещё алерт).
Состояние за день хранится в alerts_state.json (workflow коммитит его в репозиторий).

Плюс отдельные бумаги: все акции TQBR и все облигации Мосбиржи, если цена изменилась на ASSET_MOVE_PCT (5%)
и больше к прошлому закрытию. Шум отсекается: нужен оборот за день (и число сделок у облигаций), а средневзвешенная
цена дня должна сдвинуться в ту же сторону хотя бы на половину порога. В день отсечки дивидендов акция падает
на размер дивиденда — это не алерт: гэп вычитается (дивиденды — календарь smart-lab).

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

from common import (ASSET_LINES_MAX, ASSET_MOVE_PCT, BOND_BOARDS, BOND_MIN_TRADES, BOND_MIN_TURNOVER, MOVE_THRESHOLDS,
                    MSK, SHARE_MIN_TURNOVER, coingecko_btc, deliver, fail, dividends_calendar, e, fnum, fpct, iss_json, log,
                    moex_cny_tom, moex_index, pack, parallel, pct, safe, yahoo_quote, http_get)

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "alerts_state.json")
META = os.path.join(HERE, "docs", "bonds_meta.json")
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


def board_rows(market, board):
    """Бумаги режима торгов с ценой прошлого закрытия и данными дня: [dict]."""
    j = iss_json(f"engines/stock/markets/{market}/boards/{board}/securities.json?iss.meta=off"
                 "&iss.only=securities,marketdata&securities.columns=SECID,SHORTNAME,PREVPRICE"
                 "&marketdata.columns=SECID,LAST,WAPRICE,VALTODAY,NUMTRADES")
    sec = {r[0]: dict(zip(j["securities"]["columns"], r)) for r in j["securities"]["data"]}
    out = []
    for row in j["marketdata"]["data"]:
        m = dict(zip(j["marketdata"]["columns"], row))
        s = sec.get(m["SECID"])
        if s:
            out.append({**s, **m, "BOARD": board})
    return out


def bond_rows():
    got = parallel({b: (board_rows, "bonds", b) for b in BOND_BOARDS})
    rows = [r for b in BOND_BOARDS for r in (got.get(b) or [])]
    if not rows:
        raise ValueError("облигации: Мосбиржа не ответила")
    return rows


def collect():
    return parallel({
        "IMOEX": (moex_index, "IMOEX"), "RGBITR": (moex_index, "RGBITR"), "RUCBTRNS": (moex_index, "RUCBTRNS"),
        "CNY": (moex_cny_tom,), "BRENT": (yahoo_quote, "BZ=F"), "GOLD": (yahoo_quote, "GC=F"),
        "BTC": (coingecko_btc,), "KEYNEWS": (cbr_keyrate_news,),
        "SHARES": (board_rows, "shares", "TQBR"), "BONDS": (bond_rows,), "DIVS": (dividends_calendar,),
    })


# ---------------------------------------------------------------- отдельные бумаги

def fmoney(x):
    if x >= 1e9:
        return f"{fnum(x / 1e9, 1)} млрд ₽"
    if x >= 1e6:
        return f"{fnum(x / 1e6, 0)} млн ₽"
    return f"{fnum(x / 1e3, 0)} тыс. ₽"


def fprice(x):
    return fnum(x, 4 if x < 1 else 3 if x < 10 else 2)


def _num(x):
    try:
        return float(str(x).replace("\u00a0", "").replace(" ", "").replace(",", ".").replace("₽", ""))
    except (TypeError, ValueError):
        return None


def div_gaps(divs, today):
    """{тикер: дивиденд ₽} у акций, где сегодня первый день торгов без дивиденда (следующий рабочий после «купить до»)."""
    import events
    out = {}
    for v in divs or []:
        bb, dv = v.get("buy_by"), _num(v.get("div"))
        if bb and dv and events.next_workday(bb) == today:
            out[v["ticker"]] = out.get(v["ticker"], 0) + dv
    return out


def _move(r, min_val, min_trades=0):
    """Изменение цены к прошлому закрытию, % — или None, если сделок мало / движение не подтверждено."""
    prev, last, wap = r.get("PREVPRICE"), r.get("LAST"), r.get("WAPRICE")
    if not (prev and last) or (r.get("VALTODAY") or 0) < min_val or (r.get("NUMTRADES") or 0) < min_trades:
        return None
    return pct(last, prev), (pct(wap, prev) if wap else None)


def _confirmed(ch, wch, gap=0.0):
    """Средневзвешенная цена дня сдвинулась в ту же сторону хотя бы на половину порога (одна сделка — не движение)."""
    return wch is not None and (wch + gap) * ch > 0 and abs(wch + gap) >= ASSET_MOVE_PCT / 2


def share_moves(rows, gaps):
    out = []
    for r in rows or []:
        mv = _move(r, SHARE_MIN_TURNOVER)
        if not mv:
            continue
        ch, wch = mv
        g = gaps.get(r["SECID"], 0) / r["PREVPRICE"] * 100 if r["SECID"] in gaps else 0.0
        adj = ch + g                               # без дивидендного гэпа
        if abs(adj) < ASSET_MOVE_PCT or not _confirmed(adj, wch, g):
            continue
        note = f" · без учёта дивидендного гэпа {fpct(-g, 1)}" if g else ""
        out.append(("S:" + r["SECID"], adj, f"{r['SHORTNAME']} ({r['SECID']}) {fpct(adj, 1)} → {fprice(r['LAST'])} ₽"
                                          f" · оборот {fmoney(r['VALTODAY'])}{note}"))
    return out


def load_meta():
    try:
        with open(META, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def bond_moves(rows, meta):
    b, seen, out = (meta or {}).get("b", {}), set(), []
    for r in rows or []:
        if r["SECID"] in seen:
            continue
        mv = _move(r, BOND_MIN_TURNOVER, BOND_MIN_TRADES)
        if not mv:
            continue
        ch, wch = mv
        if abs(ch) < ASSET_MOVE_PCT or not _confirmed(ch, wch):
            continue
        seen.add(r["SECID"])
        m = b.get(r["SECID"]) or []
        rat = "" if r["BOARD"] == "TQOB" else " · " + (m[0] if m and m[0] else "без рейтинга")
        out.append(("B:" + r["SECID"], ch, f"{r['SHORTNAME']}{rat} {fpct(ch, 1)} → {fnum(r['LAST'], 2)}% номинала"
                                          f" · оборот {fmoney(r['VALTODAY'])}"))
    return out


def demo():
    return {"IMOEX": {"val": 2380.0, "chg": -1.9, "ytd": -14.0, "date": "2026-10-15"},
            "RGBITR": {"val": 760.0, "chg": -0.6, "ytd": 2.4, "date": "2026-10-15"},
            "RUCBTRNS": {"val": 211.0, "chg": -0.1, "ytd": 10.2, "date": "2026-10-15"},
            "CNY": (12.60, 1.2), "BRENT": (101.0, 103.0, 61.0), "GOLD": (4090.0, 4086.0, 4300.0),
            "BTC": (84000.0, 0.7),
            "KEYNEWS": [("Банк России принял решение снизить ключевую ставку на 100 б.п., до 13,00% годовых",
                         "https://www.cbr.ru/press/pr/")]}


def demo_assets():
    sh = [{"SECID": "SGZH", "SHORTNAME": "Сегежа", "PREVPRICE": 1.40, "LAST": 1.29, "WAPRICE": 1.31, "VALTODAY": 4.5e8, "NUMTRADES": 9000},
          {"SECID": "MTLR", "SHORTNAME": "Мечел", "PREVPRICE": 80.0, "LAST": 85.2, "WAPRICE": 84.0, "VALTODAY": 9e8, "NUMTRADES": 20000},
          {"SECID": "MGNT", "SHORTNAME": "Магнит", "PREVPRICE": 4000.0, "LAST": 3700.0, "WAPRICE": 3710.0, "VALTODAY": 3e9, "NUMTRADES": 50000},
          {"SECID": "ABCD", "SHORTNAME": "Неликвид", "PREVPRICE": 10.0, "LAST": 12.0, "WAPRICE": 12.0, "VALTODAY": 2e5, "NUMTRADES": 3},
          {"SECID": "SBER", "SHORTNAME": "Сбербанк", "PREVPRICE": 300.0, "LAST": 302.0, "WAPRICE": 301.0, "VALTODAY": 2e10, "NUMTRADES": 1e5}]
    bd = [{"SECID": "RU000A10TEST", "SHORTNAME": "СегежаР2", "BOARD": "TQCB", "PREVPRICE": 78.0, "LAST": 71.2, "WAPRICE": 72.5, "VALTODAY": 1.2e7, "NUMTRADES": 140},
          {"SECID": "SU26238RMFS4", "SHORTNAME": "ОФЗ 26238", "BOARD": "TQOB", "PREVPRICE": 60.0, "LAST": 63.1, "WAPRICE": 62.8, "VALTODAY": 2e9, "NUMTRADES": 9000},
          {"SECID": "RU000A1ONE", "SHORTNAME": "Однасделка", "BOARD": "TQCB", "PREVPRICE": 100.0, "LAST": 90.0, "WAPRICE": 99.6, "VALTODAY": 3e6, "NUMTRADES": 6}]
    return sh, bd, {"MGNT": 330.0}


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
    if not is_demo and safe("WORKDAY", events.is_workday, now.date()) is False:
        log("Нерабочий день - алерты не проверяю.")
        return
    state = load_state(now.date().isoformat())
    d = demo() if is_demo else collect()
    def fresh(key, ch, thr):
        """Впервые за день или движение усилилось ещё на половину порога."""
        prev = state["levels"].get(key, 0)
        if prev and abs(ch) < prev + thr / 2:
            return False
        state["levels"][key] = round(abs(ch), 2)
        return True

    lines = []
    for key, ch, text in moves(d):
        thr = MOVE_THRESHOLDS.get(key)
        if thr is None or ch is None or abs(ch) < thr:
            continue
        if fresh(key, ch, thr):
            lines.append(f"{'🟢' if ch > 0 else '🔴'} {e(text)}")
    if is_demo:
        sh, bd, gaps = demo_assets()
    else:
        sh, bd = d.get("SHARES"), d.get("BONDS")
        gaps = safe("DIVGAPS", div_gaps, d.get("DIVS"), now.date()) or {}
    groups = []
    for title, items in ((f"Акции ±{fnum(ASSET_MOVE_PCT, 0)}% и больше", safe("SHARE_MOVES", share_moves, sh, gaps) or []),
                         (f"Облигации ±{fnum(ASSET_MOVE_PCT, 0)}% и больше", safe("BOND_MOVES", bond_moves, bd, load_meta()) or [])):
        got = sorted([x for x in items if fresh(x[0], x[1], ASSET_MOVE_PCT)], key=lambda x: -abs(x[1]))
        if got:
            body = [f"{'🟢' if ch > 0 else '🔴'} {e(t)}" for _, ch, t in got[:ASSET_LINES_MAX]]
            if len(got) > ASSET_LINES_MAX:
                body.append(f"… и ещё {len(got) - ASSET_LINES_MAX}")
            groups.append(f"<b>{title}</b>\n" + "\n".join(body))
    for title, link in d.get("KEYNEWS") or []:
        if title in state["titles"]:
            continue
        state["titles"].append(title)
        lines.append(f'🏦 <a href="{e(link)}">{e(title)}</a>')
    if not lines and not groups:
        log("Сильных движений нет.")
    else:
        blocks = [f"⚡ <b>АЛЕРТ · {now:%H:%M} МСК</b>" + ("\n" + "\n".join(lines) if lines else "")] + groups
        deliver(pack(blocks))
    if not is_demo:
        save_state(state)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as ex:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        last = traceback.extract_tb(ex.__traceback__)[-1]
        fail(f"Алерты упали: {type(ex).__name__}: {ex} (alerts.py → {last.name}, строка {last.lineno})")
