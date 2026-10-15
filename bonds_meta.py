#!/usr/bin/env python3
"""Справочник облигаций для конструктора портфеля и мониторинга. Запускается раз в день перед дайджестом.

Пишет:
  docs/bonds_meta.json  — для страницы docs/bonds.html:
      {"updated", "rated", "b": {SECID: [рейтинг, id эмитента, средний оборот ₽/день, ISIN]}, "em": {id: название}}
  bonds_state.json      — состояние для мониторинга: история оборотов и цен (10 дней), рейтинги, изменения рейтингов.

Источники: smart-lab.ru (рейтинг — сводный по агентствам, как на сайте), ISS Мосбиржи (эмитенты, обороты, цены).
python bonds_meta.py            боевой
python bonds_meta.py --check    короткая проверка источников без записи
"""
import json
import os
import re
import sys
import time
from datetime import datetime

from bondscore import compute_fair, rscore
from common import ISS, MSK, http_get, log

HERE = os.path.dirname(os.path.abspath(__file__))
META = os.path.join(HERE, "docs", "bonds_meta.json")
STATE = os.path.join(HERE, "bonds_state.json")
KEEP_DAYS = 10
MIN_RATED = 500          # меньше — считаем, что сайт не ответил или сменил вёрстку
SL = "https://smart-lab.ru"
# все типы бумаг (по умолчанию smart-lab скрывает флоатеры, субординированные и пр.)
SL_ALL = ("bonds_variable=-1&bonds_perpetual=-1&bonds_structures=-1&bonds_suborded=-1&bonds_mortage=-1"
          "&bonds_indexed=-1&bonds_vdo=-1&bonds_amortized=-1&bonds_put_offer=-1&bonds_call_offer=-1")
RATINGS = ["AAA", "AA+", "AA", "AA-", "A+", "A", "A-", "BBB+", "BBB", "BBB-", "BB+", "BB", "BB-",
           "B+", "B", "B-", "CCC", "CC", "C", "D"]
ROW_SYM = re.compile(r'symbol="MOEX\.([A-Z0-9]+)"')
ROW_RAT = re.compile(r'bond-rating[^"]*">\s*([^<]*?)\s*</td>')
ROW_TD = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
SL_YIELD = {}  # ISIN/SECID -> доходность по оценке smart-lab, % — для флоатеров, у которых биржа не даёт текущий купон


def _num(x):
    x = re.sub(r"<[^>]+>", "", x).strip().replace(",", ".").replace("\xa0", "").replace(" ", "")
    try:
        return float(x)
    except ValueError:
        return None


def sl_page(path):
    raw = http_get(SL + path, retries=2, timeout=30).decode("utf-8", "replace")
    out = {}
    for tr in re.split(r"<tr[\s>]", raw)[1:]:
        s = ROW_SYM.search(tr)
        if not s:
            continue
        r = ROW_RAT.search(tr)
        rating = (r.group(1).strip() if r else "").upper()
        out[s.group(1)] = rating if rating in RATINGS else ""
        # колонки: №, имя, график, лет до погаш., ДОХОДНОСТЬ, год.куп.дох., ... — первая ячейка с «%» и есть доходность
        for td in ROW_TD.findall(tr):
            if "%" in td:
                y = _num(td.replace("%", ""))
                if y is not None and 0 < y < 200:
                    SL_YIELD[s.group(1)] = y
                break
    return out


def sl_ratings():
    """{ISIN или SECID: рейтинг}. Корпоративные (все типы), валютные, ОФЗ, субфедеральные."""
    res = {}
    for base, paged in ((f"/q/bonds/order_by_val_to_day/desc/{{p}}?{SL_ALL}", True),
                        ("/q/cur_bonds/order_by_val_to_day/desc/{p}", True),
                        ("/q/subfed/", False), ("/q/ofz/", False)):
        prev_keys = None
        for p in range(1, 60 if paged else 2):
            path = base.replace("{p}", f"page{p}/" if p > 1 else "") if paged else base
            try:
                page = sl_page(path)
            except Exception as ex:  # noqa: BLE001
                log(f"[warn] smart-lab {path}: {ex}")
                break
            keys = set(page)
            if not page or keys == prev_keys:
                break
            prev_keys = keys
            for k, v in page.items():
                if v or k not in res:
                    res[k] = v
            time.sleep(0.7)
    return res


def iss_rows(path, block):
    """Все строки блока ISS с постраничной загрузкой (start=)."""
    rows, start = [], 0
    while True:
        sep = "&" if "?" in path else "?"
        j = json.loads(http_get(f"{ISS}{path}{sep}iss.meta=off&start={start}"))
        t = j[block]
        page = [dict(zip(t["columns"], r)) for r in t["data"]]
        rows += page
        cur = j.get(f"{block}.cursor")
        if cur and cur["data"]:
            idx, total, size = cur["data"][0]
            if idx + size >= total:
                break
            start = idx + size
        elif len(page) < 100:
            break
        else:
            start += len(page)
    return rows


def emitters():
    """{secid: (emitent_id, emitent_title, isin)} по торгуемым облигациям."""
    out = {}
    for r in iss_rows("securities.json?engine=stock&market=bonds&is_trading=1&limit=100"
                      "&securities.columns=secid,isin,emitent_id,emitent_title,primary_boardid", "securities"):
        out[r["secid"]] = (r.get("emitent_id"), r.get("emitent_title") or "", r.get("isin") or r["secid"])
    return out


def day_history():
    """Последний торговый день: (дата, {secid: (оборот ₽, средневзв. цена %)})."""
    out, day = {}, None
    for board in ("TQCB", "TQOB"):
        rows = iss_rows(f"history/engines/stock/markets/bonds/boards/{board}/securities.json?limit=100"
                        "&history.columns=SECID,TRADEDATE,VALUE,WAPRICE,CLOSE", "history")
        for r in rows:
            day = max(day or "", r.get("TRADEDATE") or "")
            px = r.get("WAPRICE") or r.get("CLOSE")
            out[r["SECID"]] = (round(r.get("VALUE") or 0), px)
    return day, out


def _kind(s):
    t = s.get("BONDTYPE") or ""
    if re.search(r"Линкер|Структур|Конверт", t, re.I) or "ИОС" in (s.get("SHORTNAME") or ""):
        return "other"
    if re.search(r"плавающ", t, re.I) or s["SECID"].startswith("SU29"):
        return "float"
    if re.search(r"Дисконт", t, re.I):
        return "disc"
    return "amort" if re.search(r"Амортиз", t, re.I) else "fixed"


def board_bonds():
    """Котировки и доходности всех облигаций (TQOB + TQCB) для оценки «лучших бумаг»."""
    out = []
    for board in ("TQOB", "TQCB"):
        j = json.loads(http_get(f"{ISS}engines/stock/markets/bonds/boards/{board}/securities.json?iss.meta=off"
                                "&iss.only=securities,marketdata,marketdata_yields"
                                "&securities.columns=SECID,SHORTNAME,SECNAME,FACEUNIT,BONDTYPE,MATDATE,OFFERDATE,PREVPRICE,COUPONPERCENT,COUPONPERIOD"
                                "&marketdata.columns=SECID,LAST,WAPRICE,OFFER"
                                "&marketdata_yields.columns=SECID,PRICE,EFFECTIVEYIELD,DURATION,YIELDDATE,YIELDDATETYPE"))
        tab = lambda k: [dict(zip(j[k]["columns"], r)) for r in j[k]["data"]]  # noqa: E731
        md = {r["SECID"]: r for r in tab("marketdata")}
        yd = {r["SECID"]: r for r in tab("marketdata_yields")}
        for s in tab("securities"):
            m, y = md.get(s["SECID"], {}), yd.get(s["SECID"], {})
            out.append({"s": s, "board": board,
                        "price": m.get("OFFER") or m.get("LAST") or m.get("WAPRICE") or y.get("PRICE") or s.get("PREVPRICE"),
                        "ytm": y.get("EFFECTIVEYIELD"), "dur": y.get("DURATION"), "end": y.get("YIELDDATE") or s.get("MATDATE"),
                        "ydt": y.get("YIELDDATETYPE")})
    return out


def best_bonds(quotes, ratings, tvavg, today, em=None):
    """Топ по премии к «норме» рейтинга и срока: AA–AAA, A (рубли, фикс. купон), юаневые. Логика — как на странице подбора."""
    rows = []
    for q in quotes:
        s = q["s"]
        kind = _kind(s)
        ofz = q["board"] == "TQOB"
        name = (s.get("SECNAME") or "") + " " + (s.get("SHORTNAME") or "")
        ytm = q["ytm"] / 100 if q["ytm"] is not None else None
        price, end = q["price"], q["end"]
        if kind == "disc" and price and end and end > today:
            yrs = (datetime.fromisoformat(end).date() - datetime.fromisoformat(today).date()).days / 365
            ytm = (100 / price) ** (1 / yrs) - 1 if yrs > 0 else None
        rows.append({"id": s["SECID"], "name": s.get("SHORTNAME"), "cur": "RUB" if s.get("FACEUNIT") == "SUR" else s.get("FACEUNIT"),
                     "kind": kind, "ofz": ofz, "rating": "ГОС" if ofz else ratings.get(s["SECID"], ""),
                     "mbs": q["ydt"] == "MBS" or bool(re.search(r"СФО|Ипотечн|(^|[\s«\"])ИА\s", name, re.I)),
                     "sub": "СУБ" in name, "ytm": ytm, "dur": (q["dur"] or 0) / 365, "price": price or 0,
                     "tv": tvavg.get(s["SECID"], 0), "end": end, "cpn": s.get("COUPONPERCENT"),
                     "freq": round(365 / s["COUPONPERIOD"]) if s.get("COUPONPERIOD") else None,
                     "ok": kind in ("fixed", "amort", "disc") and ytm is not None and bool(price) and bool(end) and end > today})
    compute_fair(rows)
    good = [r for r in rows if r["score"] is not None and not r["ofz"] and not r["sub"] and not r["mbs"]
            and r["tv"] >= 5e6 and r["price"] >= 85 and r["dur"] >= 0.25]

    def pick(f, n):
        best, seen = [], set()
        for r in sorted((r for r in good if f(r)), key=lambda r: -r["score"]):
            key = (em or {}).get(r["id"], (r["id"],))[0] or r["id"]     # не больше одной бумаги на эмитента
            if key in seen:
                continue
            seen.add(key)
            best.append({k: r[k] for k in ("id", "name", "rating", "ytm", "fair", "prem", "dur", "end", "cpn", "freq", "cur")})
            if len(best) >= n:
                break
        return best
    top = {"AA": pick(lambda r: r["cur"] == "RUB" and 1 <= rscore(r["rating"]) <= 4, 3),
           "A": pick(lambda r: r["cur"] == "RUB" and 5 <= rscore(r["rating"]) <= 7, 3),
           "CNY": pick(lambda r: r["cur"] == "CNY" and rscore(r["rating"]) <= 7, 2)}
    names = {r["id"]: r["name"] for r in rows}
    return top, names


def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def main():
    check = "--check" in sys.argv
    now = datetime.now(MSK)
    st = load_state()

    em = emitters()
    log(f"эмитенты: {len(em)} бумаг")
    if check:
        page = sl_page("/q/bonds/")
        log(f"smart-lab, 1-я страница: {len(page)} строк, с рейтингом {sum(1 for v in page.values() if v)}")
        day, hist = day_history()
        log(f"история {day}: {len(hist)} бумаг")
        return

    rat = sl_ratings()
    rated = sum(1 for v in rat.values() if v)
    log(f"smart-lab: {len(rat)} бумаг, с рейтингом {rated}")
    old_r = st.get("ratings") or {}
    if rated < MIN_RATED and old_r:     # не затираем вчерашние рейтинги
        log("[warn] мало рейтингов — оставляю прошлые")
        rat = {}

    day, hist = day_history()
    log(f"история {day}: {len(hist)} бумаг")

    # история оборотов и цен (по дням), последние KEEP_DAYS торговых дней
    days = st.get("days") or []
    tv, px = st.get("tv") or {}, st.get("px") or {}
    if day and day not in days:
        days.append(day)
        for sec, (v, p) in hist.items():
            tv.setdefault(sec, []).append(v)
            px.setdefault(sec, []).append(p)
        for sec in set(tv) - set(hist):          # не торговалась — оборот 0, цена та же
            tv[sec].append(0)
            px.setdefault(sec, []).append(None)
        days = days[-KEEP_DAYS:]
        tv = {k: v[-KEEP_DAYS:] for k, v in tv.items() if any(v[-KEEP_DAYS:])}
        px = {k: v[-KEEP_DAYS:] for k, v in px.items() if k in tv}

    # рейтинги по SECID (smart-lab даёт ISIN для корпоративных и SECID для ОФЗ)
    ratings = {}
    for sec, (_eid, _t, isin) in em.items():
        r = rat.get(sec) or rat.get(isin)
        if r:
            ratings[sec] = r
    if not rat:
        ratings = old_r
    top, names = {}, {}
    try:
        tvavg = {k: sum(v) / len(v) for k, v in tv.items() if v}
        top, names = best_bonds(board_bonds(), ratings, tvavg, now.date().isoformat(), em)
        log("лучшие бумаги: " + ", ".join(f"{k}:{len(v)}" for k, v in top.items()))
    except Exception as ex:  # noqa: BLE001
        log(f"[warn] лучшие бумаги: {ex}")
    changes = st.get("rating_changes") or []
    if rat and old_r:
        for sec, r in ratings.items():
            if old_r.get(sec) and old_r[sec] != r:
                changes.append({"secid": sec, "name": names.get(sec, sec), "from": old_r[sec], "to": r,
                                "date": now.date().isoformat()})
                log(f"рейтинг {sec}: {old_r[sec]} → {r}")
    changes = [c for c in changes if (now.date() - datetime.fromisoformat(c["date"]).date()).days <= 30]

    b, emn = {}, {}
    for sec, (eid, title, isin) in em.items():
        vals = tv.get(sec) or []
        avg = round(sum(vals) / len(vals)) if vals else 0
        b[sec] = [ratings.get(sec, ""), eid, avg, isin if isin != sec else ""]
        sy = SL_YIELD.get(sec) or SL_YIELD.get(isin)
        if sy is not None:
            b[sec].append(sy)
        if eid is not None:
            emn[str(eid)] = title
    meta = {"updated": now.strftime("%Y-%m-%d %H:%M"), "tv_days": len(days), "rated": len(ratings), "b": b, "em": emn,
            "top": top}
    os.makedirs(os.path.dirname(META), exist_ok=True)
    with open(META, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, separators=(",", ":"))
    st.update({"updated": meta["updated"], "days": days, "tv": tv, "px": px, "ratings": ratings,
               "rating_changes": changes})
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, separators=(",", ":"))
    log(f"docs/bonds_meta.json: {len(b)} бумаг, рейтингов {len(ratings)}, дней оборота {len(days)}")


if __name__ == "__main__":
    main()
