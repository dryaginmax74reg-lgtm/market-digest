"""Мониторинг облигаций из портфелей клиентов (секрет PORTFOLIO_BONDS: коды бумаг через запятую).

Каждое утро в дайджесте: оферты и колл-опционы ≤30 дней, погашения ≤30 дней, амортизации ≤14 дней,
смена рейтинга (smart-lab, за 7 дней), дефолт/техдефолт, падение цены (≥3% за день или ≥5% за 5 дней).
Только коды бумаг — никаких данных клиентов.
"""
import json
import os
import re
from datetime import date, timedelta

from common import ISS, e, fnum, header, http_get, log

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "bonds_state.json")
OFFER_DAYS, MAT_DAYS, AMORT_DAYS, RATING_DAYS = 30, 30, 14, 7
DROP_1D, DROP_5D = -3.0, -5.0


def load_ids():
    raw = os.environ.get("PORTFOLIO_BONDS", "")
    ids = []
    for x in re.split(r"[\s,;]+", raw):
        x = x.split(":")[0].strip().upper()
        if re.fullmatch(r"[A-Z0-9]{6,14}", x) and x not in ids:
            ids.append(x)
    return ids


def _iss(path):
    return json.loads(http_get(ISS + path, retries=2))


def _rows(t):
    return [dict(zip(t["columns"], r)) for r in t["data"]]


def _ok(d):
    return d and re.fullmatch(r"\d{4}-\d\d-\d\d", d) and not d.startswith("0000")


def collect(ids=None):
    """{secid: {name, board, mat, offers:[(date,start,end)], call, amort:[(date,value)], def, tdef}} + state."""
    ids = ids if ids is not None else load_ids()
    if not ids:
        return None
    out = {}
    j = _iss("engines/stock/markets/bonds/securities.json?iss.meta=off&iss.only=securities&securities=" + ",".join(ids)
             + "&securities.columns=SECID,BOARDID,SHORTNAME,MATDATE,OFFERDATE,PUTOPTIONDATE,CALLOPTIONDATE,FACEUNIT")
    for r in _rows(j["securities"]):
        if r["SECID"] in out and out[r["SECID"]]["board"] in ("TQCB", "TQOB"):
            continue
        out[r["SECID"]] = {"name": r["SHORTNAME"], "board": r["BOARDID"], "mat": r["MATDATE"],
                           "call": r.get("CALLOPTIONDATE"), "put": r.get("PUTOPTIONDATE") or r.get("OFFERDATE"),
                           "offers": [], "amort": [], "def": False, "tdef": False}
    for sec in list(out):
        try:
            bz = _iss(f"securities/{sec}/bondization.json?iss.meta=off&iss.only=offers,amortizations&limit=unlimited")
            out[sec]["offers"] = [(o["offerdate"], o.get("offerdatestart"), o.get("offerdateend"), o.get("offertype") or "")
                                  for o in _rows(bz["offers"]) if _ok(o.get("offerdate"))]
            out[sec]["amort"] = [(a["amortdate"], a.get("value"), a.get("data_source") or "")
                                 for a in _rows(bz["amortizations"]) if _ok(a.get("amortdate"))]
            ds = _iss(f"securities/{sec}.json?iss.meta=off&iss.only=description")
            dd = {r[0]: r[2] for r in ds["description"]["data"]}
            out[sec]["def"] = dd.get("HASDEFAULT") == "1"
            out[sec]["tdef"] = dd.get("HASTECHNICALDEFAULT") == "1"
        except Exception as ex:  # noqa: BLE001
            log(f"[warn] мониторинг {sec}: {ex}")
    missing = [i for i in ids if i not in out]
    try:
        with open(STATE, encoding="utf-8") as f:
            st = json.load(f)
    except Exception:  # noqa: BLE001
        st = {}
    return {"bonds": out, "missing": missing, "state": st, "ids": ids}


def events(m, today):
    """[(приоритет, текст)] — чем меньше приоритет, тем выше в списке."""
    if not m:
        return []
    ev, t0 = [], today.isoformat()
    lim = lambda n: (today + timedelta(days=n)).isoformat()  # noqa: E731
    st = m.get("state") or {}
    px = st.get("px") or {}
    for sec, b in m["bonds"].items():
        nm = e(b["name"])
        if b["def"] or b["tdef"]:
            ev.append((0, f"🔴 <b>{nm}</b>: у эмитента {'дефолт' if b['def'] else 'технический дефолт'} (данные биржи)"))
        # оферты (put): из графика, иначе из карточки бумаги
        offs = [o for o in b["offers"] if t0 <= o[0] <= lim(OFFER_DAYS) and "погаш" not in o[3].lower()]
        if not offs and _ok(b.get("put")) and t0 <= b["put"] <= lim(OFFER_DAYS):
            offs = [(b["put"], None, None, "Оферта")]
        for d, s, en, _t in offs[:1]:
            win = f", заявки {fmt(s)}–{fmt(en)}" if _ok(s) and _ok(en) else ""
            ev.append((1, f"🔔 <b>{nm}</b>: оферта {fmt(d)} (через {(date.fromisoformat(d) - today).days} дн.{win}) — решить, предъявлять ли"))
        if _ok(b.get("call")) and t0 <= b["call"] <= lim(OFFER_DAYS):
            ev.append((2, f"📞 <b>{nm}</b>: колл-опцион эмитента {fmt(b['call'])} — бумагу могут погасить досрочно"))
        if _ok(b.get("mat")) and t0 <= b["mat"] <= lim(MAT_DAYS):
            ev.append((2, f"💰 <b>{nm}</b>: погашение {fmt(b['mat'])} — деньги вернутся, нужен план реинвестирования"))
        else:
            am = [a for a in b["amort"] if t0 <= a[0] <= lim(AMORT_DAYS) and a[2] != "maturity" and a[0] != b.get("mat")]
            if am:
                a = am[0]
                ev.append((3, f"↩️ <b>{nm}</b>: амортизация {fmt(a[0])}" + (f" ({fnum(a[1])} на бумагу)" if a[1] else "")))
        p = [v for v in (px.get(sec) or []) if v]
        if len(p) >= 2 and p[-2]:
            ch1 = (p[-1] / p[-2] - 1) * 100
            ch5 = (p[-1] / p[-6] - 1) * 100 if len(p) >= 6 and p[-6] else None
            if ch1 <= DROP_1D or (ch5 is not None and ch5 <= DROP_5D):
                ev.append((1, f"📉 <b>{nm}</b>: цена {fnum(p[-1])}% ({fnum(ch1, 1)}% за день"
                              + (f", {fnum(ch5, 1)}% за 5 дней" if ch5 is not None else "") + ")"))
    for c in st.get("rating_changes") or []:
        if c["secid"] in m["bonds"] and (today - date.fromisoformat(c["date"])).days <= RATING_DAYS:
            down = rscore(c["to"]) > rscore(c["from"])
            ev.append((0 if down else 3, f"{'⬇️' if down else '⬆️'} <b>{e(m['bonds'][c['secid']]['name'])}</b>: рейтинг "
                                          f"{e(c['from'])} → {e(c['to'])} ({fmt(c['date'])}, smart-lab)"))
    if m.get("missing"):
        ev.append((4, "❔ не найдены на бирже: " + e(", ".join(m["missing"][:5]))))
    return sorted(ev, key=lambda x: x[0])


RAT = ["AAA", "AA+", "AA", "AA-", "A+", "A", "A-", "BBB+", "BBB", "BBB-", "BB+", "BB", "BB-", "B+", "B", "B-", "CCC", "CC", "C", "D"]


def rscore(r):
    return RAT.index(r) if r in RAT else 99


def fmt(d):
    return f"{d[8:10]}.{d[5:7]}" if d else ""


def block(m, today):
    """Блок дайджеста. Без событий — короткая строка по понедельникам, в остальные дни блока нет."""
    if not m:
        return ""
    ev = events(m, today)
    n = len(m["ids"])
    if not ev:
        if today.weekday() != 0:
            return ""
        return header("💼", "ВАШИ ОБЛИГАЦИИ", f"под наблюдением: {n}") + "\nСобытий на ближайший месяц нет."
    return header("💼", "ВАШИ ОБЛИГАЦИИ", f"под наблюдением: {n}") + "\n" + "\n".join(t for _p, t in ev[:12])


def demo(today):
    d = lambda n: (today + timedelta(days=n)).isoformat()  # noqa: E731
    return {"ids": ["RU000A10DSL1", "RU000A10C8F3", "SU26238RMFS4", "RU000A10EF52"], "missing": [],
            "bonds": {
                "RU000A10DSL1": {"name": "ПГК3Р02", "board": "TQCB", "mat": "2030-11-16", "call": None, "put": d(12),
                                 "offers": [(d(12), d(5), d(9), "Оферта")], "amort": [], "def": False, "tdef": False},
                "RU000A10C8F3": {"name": "Брус 2Р04", "board": "TQCB", "mat": "2028-07-23", "call": None, "put": None,
                                 "offers": [], "amort": [], "def": False, "tdef": True},
                "SU26238RMFS4": {"name": "ОФЗ 26238", "board": "TQOB", "mat": "2041-05-15", "call": None, "put": None,
                                 "offers": [], "amort": [], "def": False, "tdef": False},
                "RU000A10EF52": {"name": "Сбер Sb52R", "board": "TQCB", "mat": d(20), "call": None, "put": None,
                                 "offers": [], "amort": [], "def": False, "tdef": False}},
            "state": {"px": {"RU000A10C8F3": [97.1, 96.8, 96.5, 96.0, 95.2, 93.1, 90.4]},
                      "rating_changes": [{"secid": "RU000A10C8F3", "from": "A-", "to": "BBB", "date": d(-2)}]}}
