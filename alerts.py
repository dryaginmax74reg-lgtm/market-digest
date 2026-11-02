#!/usr/bin/env python3
"""Алерты в течение дня: шлёт сообщение, только если что-то сдвинулось сильнее порога.
Пороги - MOVE_THRESHOLDS в common.py. Повторно по тому же инструменту пишет, только если движение
усилилось ещё на половину порога (например, IMOEX −1,5% -> алерт, −2,3% -> ещё алерт).
Состояние за день хранится в alerts_state.json (workflow коммитит его в репозиторий).

Отдельные выпуски: только ОФЗ, пороги цены 1% и доходности 0,3 п.п. с фильтром ликвидности.

python alerts.py            боевой
python alerts.py --demo     демо-данные, печать в консоль
"""
import json
import math
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from common import (ASSET_LINES_MAX, ASSET_MOVE_PCT, BOND_BOARDS, BOND_MIN_TRADES, BOND_MIN_TURNOVER, MOVE_THRESHOLDS,
                    FX_CBR_TOMORROW_PCT, RATING_ALERT_HOURS, MSK, SHARE_MIN_TURNOVER, cbr_rates, coingecko_btc, deliver, fail, dividends_calendar, e, fnum, fpct, iss_json, log,
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
        old = (s.get("live") or {})
        if old.get("id") and not old.get("closed"):
            return {"date": today, "levels": {}, "titles": [], "unpin": old["id"]}   # вчерашнее живое сообщение открепим
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
                 "&iss.only=securities,marketdata&securities.columns=SECID,SHORTNAME,SECNAME,PREVPRICE,PREVWAPRICE,YIELDATPREVWAPRICE"
                 "&marketdata.columns=SECID,LAST,WAPRICE,VALTODAY,NUMTRADES,YIELD,YIELDATWAPRICE,VALTODAY_RUR")
    sec = {r[0]: dict(zip(j["securities"]["columns"], r)) for r in j["securities"]["data"]}
    out = []
    for row in j["marketdata"]["data"]:
        m = dict(zip(j["marketdata"]["columns"], row))
        s = sec.get(m["SECID"])
        if s:
            out.append({**s, **m, "BOARD": board})
    return out


def bond_rows():
    rows = board_rows("bonds", "TQOB")
    if not rows:
        raise ValueError("облигации: Мосбиржа не ответила")
    return rows


def cbr_tomorrow(now):
    """Официальный курс ЦБ на следующий рабочий день, если уже опубликован (ЦБ устанавливает около 15:30 МСК):
    {код: (завтра, сегодня)} и дата; иначе None."""
    if now.hour < 15:
        return None
    nxt = now.date() + timedelta(days=1)
    d1, r1 = cbr_rates(nxt)
    d0, r0 = cbr_rates(now.date())
    if not d1 or d1 == d0:
        return None
    return {"date": d1, "rates": {c: (r1.get(c), r0.get(c)) for c in ("USD", "EUR", "CNY")}}


def rating_moves(now, force=False):
    """Понижения и дефолты по сводному рейтингу (smart-lab) к утреннему справочнику docs/bonds_meta.json, по эмитентам."""
    if not force and now.hour not in RATING_ALERT_HOURS:
        return None
    from bonds_meta import sl_ratings
    from bondscore import rscore
    meta = load_meta()
    b, em = meta.get("b") or {}, meta.get("em") or {}
    if not b:
        return None
    new = sl_ratings()
    if sum(1 for v in new.values() if v) < 500:          # сайт не ответил целиком — не сравниваем
        raise ValueError(f"smart-lab: мало рейтингов ({len(new)})")
    by = {}
    for sec, row in b.items():
        old, eid, isin = row[0], row[1], (row[3] if len(row) > 3 else "") or sec
        r = new.get(isin) or new.get(sec)
        if not old or not r or r == old or rscore(r) <= rscore(old):
            continue
        k = str(eid) if eid is not None else sec
        g = by.setdefault(k, {"name": em.get(str(eid)) or sec, "from": old, "to": r, "n": 0})
        g["n"] += 1
        if rscore(r) > rscore(g["to"]):
            g["from"], g["to"] = old, r
    return by


def collect():
    now = datetime.now(MSK)
    return parallel({
        "CBRT": (cbr_tomorrow, now),
        "RGBITR": (moex_index, "RGBITR"),
        "CNY": (moex_cny_tom,), "BRENT": (yahoo_quote, "BZ=F"), "GOLD": (yahoo_quote, "GC=F"),
        "BTC": (coingecko_btc,), "BONDS": (bond_rows,),
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
        value = float(str(x).replace("\u00a0", "").replace(" ", "").replace(",", ".").replace("₽", ""))
        return value if math.isfinite(value) else None
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


PCT = {"SHARE": ASSET_MOVE_PCT, "BOND": ASSET_MOVE_PCT, "CBRT": FX_CBR_TOMORROW_PCT}   # могут переопределяться командой /porog


def load_cfg():
    """Пороги из бота (/porog → Cloudflare KV): WORKER_URL + WORKER_KEY. Нет ответа — пороги по умолчанию из common.py."""
    url, key = (os.environ.get("WORKER_URL") or "").strip().rstrip("/"), (os.environ.get("WORKER_KEY") or "").strip()
    if not url or not key:
        return {}
    try:
        import urllib.parse
        cfg = json.loads(http_get(f"{url}/cfg?key={urllib.parse.quote(key)}", retries=2, timeout=10))
    except Exception as ex:  # noqa: BLE001
        log(f"[warn] настройки бота: {type(ex).__name__}: {ex} — пороги по умолчанию")
        return {}
    used = {}
    for k, v in (cfg or {}).items():
        try:
            v = float(v)
        except (TypeError, ValueError):
            continue
        if not 0 < v <= 50:
            continue
        if k in MOVE_THRESHOLDS:
            MOVE_THRESHOLDS[k] = v
            used[k] = v
        elif k in PCT:
            PCT[k] = v
            used[k] = v
    if used:
        log("пороги из бота: " + ", ".join(f"{k}={v}" for k, v in used.items()))
    return used


def _confirmed(ch, wch, gap=0.0, thr=None):
    """Средневзвешенная цена дня сдвинулась в ту же сторону хотя бы на половину порога (одна сделка — не движение)."""
    return wch is not None and (wch + gap) * ch > 0 and abs(wch + gap) >= (thr or ASSET_MOVE_PCT) / 2


def share_moves(rows, gaps):
    out = []
    for r in rows or []:
        mv = _move(r, SHARE_MIN_TURNOVER)
        if not mv:
            continue
        ch, wch = mv
        g = gaps.get(r["SECID"], 0) / r["PREVPRICE"] * 100 if r["SECID"] in gaps else 0.0
        adj = ch + g                               # без дивидендного гэпа
        if abs(adj) < PCT["SHARE"] or not _confirmed(adj, wch, g, PCT["SHARE"]):
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
        if abs(ch) < PCT["BOND"] or not _confirmed(ch, wch, 0.0, PCT["BOND"]):
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
            "CBRT": {"date": "20.10.2026", "rates": {"USD": (83.10, 83.87), "EUR": (93.50, 93.77), "CNY": (12.71, 12.47)}},
            "RATINGS": {"7": {"name": "Сегежа Групп", "from": "A-", "to": "BBB+", "n": 4},
                        "9": {"name": "Монополия", "from": "BB", "to": "D", "n": 1}},
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
    for sid, label in (("RGBITR", "ОФЗ (RGBI TR)"),):
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


def ofz_candidates(rows):
    """All OFZ types, including CNY issues; yield compares daily weighted yields.

    Only displayed signals are committed to state by main. Missing yields do not
    disable price alerts (e.g. floating-rate OFZ); invalid data never forms a signal.
    """
    out, seen = [], set()
    for r in rows or []:
        sid = r.get("SECID")
        name = r.get("SHORTNAME") or r.get("SECNAME") or ""
        if r.get("BOARD") != "TQOB" or not sid or sid in seen or not name.upper().startswith("ОФЗ"):
            continue
        seen.add(sid)
        turnover = _num(r.get("VALTODAY_RUR"))
        if turnover is None:
            turnover = _num(r.get("VALTODAY"))
        if (turnover or 0) < BOND_MIN_TURNOVER or (_num(r.get("NUMTRADES")) or 0) < BOND_MIN_TRADES:
            continue
        prev, last, wap = (_num(r.get(k)) for k in ("PREVPRICE", "LAST", "WAPRICE"))
        ch = pct(last, prev) if last and last > 0 and prev and prev > 0 else None
        wch = pct(wap, prev) if wap and wap > 0 and prev and prev > 0 else None
        y, py = (_num(r.get(k)) for k in ("YIELDATWAPRICE", "YIELDATPREVWAPRICE"))
        y = y if y is not None and 0 < y < 100 else None
        py = py if py is not None and 0 < py < 100 else None
        dy = y - py if y is not None and py is not None else None
        prev_wap = _num(r.get("PREVWAPRICE"))
        wap_change = pct(wap, prev_wap) if wap and wap > 0 and prev_wap and prev_wap > 0 else None
        signals = []
        if ch is not None and abs(ch) + 1e-9 >= 1 and _confirmed(ch, wch, 0, 1):
            signals.append((f"OFZ:P:{sid}:{'+' if ch > 0 else '-'}", ch, 1.0))
        if dy is not None and abs(dy) + 1e-9 >= 0.3 and wap_change is not None and dy * wap_change < 0:
            signals.append((f"OFZ:Y:{sid}:{'+' if dy > 0 else '-'}", dy, 0.3))
        if not signals:
            continue
        detail = (f"цена {fpct(ch, 2)} → {fnum(last, 2)}% ном." if ch is not None
                  else "цена последней сделки недоступна")
        if y is not None:
            detail += f" · ср.-взв. доходность {fnum(y, 2)}%"
            if dy is not None:
                detail += f" ({dy:+.2f} п.п. к прошлому торговому дню)"
        direction = ch if ch else -(dy or 0)
        line = f"{'📈' if direction > 0 else '📉'} <b>{e(name)}</b> · {detail}"
        out.append((max(abs(v) / t for _, v, t in signals), sid, line, signals))
    return sorted(out, key=lambda x: (-x[0], x[1]))


def main():
    is_demo = "--demo" in sys.argv
    now = datetime.now(MSK)
    import events
    if not is_demo and safe("WORKDAY", events.is_workday, now.date()) is False:
        log("Нерабочий день - алерты не проверяю.")
        return
    state = load_state(now.date().isoformat())
    if not is_demo:
        load_cfg()
    d = demo() if is_demo else collect()
    if is_demo:
        d["BONDS"] = demo_assets()[1]
    def fresh(key, ch, thr):
        """Впервые за день или движение усилилось ещё на половину порога."""
        prev = state["levels"].get(key, 0)
        if prev and abs(ch) < prev + thr / 2:
            return False
        state["levels"][key] = round(abs(ch), 2)
        return True

    # Только макроактивы и все ликвидные выпуски ОФЗ.
    items = []
    for key, ch, line in moves(d):
        thr = MOVE_THRESHOLDS.get(key)
        if thr is not None and ch is not None and abs(ch) >= thr and fresh(key, ch, thr):
            items.append((False, f"{'🟢' if ch > 0 else '🔴'} {e(line)}", False))
    cb = d.get("CBRT")
    if cb:
        for c, (v1, v0) in cb["rates"].items():
            ch = pct(v1, v0) if v1 and v0 else None
            key = "CBRT:" + cb["date"] + c
            if ch is None or abs(ch) < PCT["CBRT"] or key in state["titles"]:
                continue
            state["titles"].append(key)
            items.append((False, f"💱 Курс ЦБ на {e(cb['date'][:5])}: {c} {fnum(v1, 4 if c == 'CNY' else 2)} ₽ ({e(fpct(ch, 2))})", False))
    ofz_items = []
    for strength, sid, line, signals in ofz_candidates(d.get("BONDS")):
        pending = [(key, change, threshold) for key, change, threshold in signals
                   if abs(change) + 1e-9 >= state["levels"].get(key, 0) + threshold / 2
                   or key not in state["levels"]]
        if not pending:
            continue
        ofz_items.append((line, pending))
    for line, signals in ofz_items[:ASSET_LINES_MAX]:
        for key, change, threshold in signals:
            state["levels"][key] = abs(change)
        items.append((False, line, False))
    if len(ofz_items) > ASSET_LINES_MAX:
        items.append((False, f"… и ещё {len(ofz_items)-ASSET_LINES_MAX} выпусков ОФЗ", False))
    urgent = [(ln, wl) for u, ln, wl in items if u]
    if urgent:
        deliver(pack([f"🚨 <b>СРОЧНО · {now:%H:%M} МСК</b>\n" + "\n".join(("⭐ " if wl else "") + ln for ln, wl in urgent)]))
    if not items:
        log("Сильных движений нет.")
    import live
    live.update(state, now, [("❗ " if u and not wl else "") + ln for u, ln, wl in items])   # без пометок «ваш список»: состояние публичное
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

