"""Оценка облигаций относительно «соседей» — точная копия docs/bondscore.js (computeFair) для дайджеста.

Норма = взвешенная медиана доходностей бумаг той же валюты, типа купона, сегмента и рейтинга (±1…3 ступени,
если соседей меньше 8), вес — близость дюрации (Гаусс, ширина 35% дюрации, не меньше 0,5 г.) × ликвидность.
Премия = доходность − норма; метка «риск» — премия > max(3 п.п., 3×разброс) или цена < 60%.
"""
import math

RAT = ["AAA", "AA+", "AA", "AA-", "A+", "A", "A-", "BBB+", "BBB", "BBB-", "BB+", "BB", "BB-",
       "B+", "B", "B-", "CCC", "CC", "C", "D"]
MIN_PEERS, MIN_WEIGHT = 8, 2.5


def rscore(r):
    if r == "ГОС":
        return 0
    return RAT.index(r) + 1 if r in RAT else 99


def wmedian(vals, ws):
    idx = sorted(range(len(vals)), key=lambda i: vals[i])
    tot, acc = sum(ws), 0.0
    for i in idx:
        acc += ws[i]
        if acc >= tot / 2:
            return vals[i]
    return vals[idx[-1]]


def _liq_w(tv):
    return 0.25 + min(1.0, math.log10(1 + (tv or 0) / 1e6) / 2)


def _gk(r):
    seg = "G" if r.get("ofz") else "S" if r.get("mbs") else "U" if r.get("sub") else "C"
    return f"{r['cur']}|{'F' if r.get('kind') == 'float' else 'X'}|{seg}"


def compute_fair(rows):
    ref = [r for r in rows if r.get("ok") and r.get("ytm") is not None and (r.get("dur") or 0) > 0
           and (r.get("price") or 0) >= 85 and r["ytm"] < 0.6 and rscore(r.get("rating")) < 99]
    groups = {}
    for r in ref:
        groups.setdefault(_gk(r), []).append(r)
    for r in rows:
        r.update(fair=None, prem=None, mad=None, score=None, flag=None, peers=0)
        if not r.get("ok") or r.get("ytm") is None or not (r.get("dur") or 0) > 0 or rscore(r.get("rating")) >= 99:
            continue
        g, rs = groups.get(_gk(r), []), rscore(r["rating"])
        peers, width = [], 0
        for width in range(4):
            peers = [p for p in g if p is not r and abs(rscore(p["rating"]) - rs) <= width]
            if len(peers) >= MIN_PEERS:
                break
        if not peers:
            continue
        h = max(0.5, 0.35 * r["dur"])
        ws = [math.exp(-0.5 * ((p["dur"] - r["dur"]) / h) ** 2) * _liq_w(p.get("tv")) for p in peers]
        if sum(ws) < MIN_WEIGHT * 0.25:
            continue
        vals = [p["ytm"] for p in peers]
        fair = wmedian(vals, ws)
        mad = wmedian([abs(v - fair) for v in vals], ws)
        r.update(fair=fair, prem=r["ytm"] - fair, mad=mad, peers=len(peers), width=width)
        if not r.get("ofz") and (r["prem"] > max(0.03, 3 * mad) or r["price"] < 60):
            r["flag"] = "risk"
        liq = max(-0.4, min(0.3, 0.25 * math.log10(max(r.get("tv") or 0, 1e5) / 2e7)))
        r["score"] = None if r["flag"] else r["prem"] * 100 + (0.3 if r.get("ofz") else liq)
    return rows
