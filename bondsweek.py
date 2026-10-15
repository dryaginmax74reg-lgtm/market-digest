"""Понедельничный блок дайджеста «Облигации: итоги недели».

- Лучшие бумаги: переплата к «норме» своего рейтинга и срока (из docs/bonds_meta.json, считает bonds_meta.py).
- Размещения: сбор заявок на этой и следующей неделе + прошедшие за неделю (bonds-lab.ru).
- Рейтинги и дефолты за прошлую неделю: изменения сводного рейтинга (smart-lab, отслеживает bonds_meta.py).
Каждая бумага — ссылка на страницу подбора с этой бумагой.
"""
import json
import os
from datetime import date, timedelta

from bondscore import rscore
from common import e, fnum, header, log, pages_url

HERE = os.path.dirname(os.path.abspath(__file__))
META = os.path.join(HERE, "docs", "bonds_meta.json")
STATE = os.path.join(HERE, "bonds_state.json")


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def collect():
    import placements
    d = {"meta": _load(META), "state": _load(STATE), "placements": None}
    try:
        d["placements"] = placements.fetch_placements()
    except Exception as ex:  # noqa: BLE001
        log(f"[warn] размещения bonds-lab: {ex}")
    return d


def _ddmm(iso):
    return f"{iso[8:10]}.{iso[5:7]}" if iso else ""


def _link(secid, name):
    base = pages_url()
    if not base:
        return f"<b>{e(name)}</b>"
    base = base if base.endswith("/") else base + "/"
    return f'<a href="{e(base)}bonds.html#q={e(secid)}">{e(name)}</a>'


def _fq(f):
    return "ежемес." if f and f >= 12 else "ежекв." if f and f >= 4 else f"{f} р/год" if f else ""


def block(d, today):
    if not d:
        return ""
    lines = []
    top = (d.get("meta") or {}).get("top") or {}
    groups = [("AA", "AA–AAA"), ("A", "A"), ("CNY", "¥ юани")]
    if any(top.get(k) for k, _ in groups):
        lines.append("<b>Лучшие бумаги</b> <i>— переплата к норме своего рейтинга и срока</i>")
        for k, title in groups:
            for t in top.get(k) or []:
                cp = f" · купон {fnum(t['cpn'])}% {_fq(t.get('freq'))}" if t.get("cpn") else ""
                lines.append(f"• {title}: {_link(t['id'], t['name'])} {e(t['rating'])} · {fnum(t['ytm'] * 100)}% "
                             f"(+{fnum(t['prem'] * 100)} п.п.) · до {e(_ddmm(t['end']))}.{e((t['end'] or '')[2:4])}{cp}")
    pl = d.get("placements")
    if pl is not None:
        t0 = today.isoformat()
        soon = [p for p in pl if p["book"] and t0 <= p["book"] <= (today + timedelta(days=13)).isoformat()]
        past = [p for p in pl if p["book"] and (today - timedelta(days=7)).isoformat() <= p["book"] < t0]
        lines.append("")
        lines.append('<b>Размещения</b> <i>— <a href="https://bonds-lab.ru/placements">bonds-lab</a></i>')
        for title, items in (("сбор заявок впереди", sorted(soon, key=lambda p: p["book"])),
                             ("прошли за неделю", sorted(past, key=lambda p: p["book"]))):
            if not items:
                continue
            lines.append(f"<i>{title}:</i>")
            for p in items[:8]:
                rt = ", ".join(p["ratings"][:2]) or "без рейтинга"
                cp = p["coupon"] if p["coupon"] and "позднее" not in p["coupon"].lower() else "купон уточняется"
                extra = " · " + e(p["note"][:70]) if p["note"] and ("оферт" in p["note"].lower() or "суборд" in p["note"].lower()
                                                                   or "квал" in p["note"].lower()) else ""
                lines.append(f'• <a href="{e(p["url"])}">{e(p["name"])}</a> · {e(rt)} · {e(cp)} · сбор до {_ddmm(p["book"])}'
                             f'{", размещение " + _ddmm(p["place"]) if p["place"] else ""} · {e(p["volume"])}{extra}')
        if not soon and not past:
            last = max((p["book"] for p in pl if p["book"]), default=None)
            lines.append(f"новых нет{'; последние данные источника — сбор до ' + _ddmm(last) if last else ''}")
    st = d.get("state") or {}
    ch = [c for c in st.get("rating_changes") or []
          if 1 <= (today - date.fromisoformat(c["date"])).days <= 7 or (today - date.fromisoformat(c["date"])).days == 0]
    if ch:
        down = sorted([c for c in ch if rscore(c["to"]) > rscore(c["from"])], key=lambda c: -rscore(c["to"]))
        up = [c for c in ch if rscore(c["to"]) < rscore(c["from"])]
        dflt = [c for c in down if c["to"] in ("D", "C", "CC") or c["to"].startswith("C")]
        lines.append("")
        lines.append(f"<b>Рейтинги за неделю</b> <i>— сводные, smart-lab: ↓ {len(down)}, ↑ {len(up)}</i>")
        if dflt:
            lines.append("🔴 Дефолт/преддефолт: " + ", ".join(f"{_link(c['secid'], c.get('name') or c['secid'])} {e(c['from'])}→{e(c['to'])}" for c in dflt[:6]))
        rest = [c for c in down if c not in dflt]
        if rest:
            lines.append("⬇️ " + ", ".join(f"{_link(c['secid'], c.get('name') or c['secid'])} {e(c['from'])}→{e(c['to'])}" for c in rest[:8])
                         + (f" и ещё {len(rest) - 8}" if len(rest) > 8 else ""))
        if up:
            lines.append("⬆️ " + ", ".join(f"{_link(c['secid'], c.get('name') or c['secid'])} {e(c['from'])}→{e(c['to'])}" for c in up[:6])
                         + (f" и ещё {len(up) - 6}" if len(up) > 6 else ""))
    if not lines:
        return ""
    return header("🧾", "ОБЛИГАЦИИ: ИТОГИ НЕДЕЛИ") + "\n" + "\n".join(lines).strip()


def demo(today):
    d = lambda n: (today + timedelta(days=n)).isoformat()  # noqa: E731
    return {"meta": {"top": {
        "AA": [{"id": "RU000A10EMU3", "name": "sГТЛК2P-12", "rating": "AA-", "ytm": 0.1970, "prem": 0.0185, "end": "2030-03-02", "cpn": 16.25, "freq": 12},
               {"id": "RU000A10FGX6", "name": "АФБАНК1Р19", "rating": "AA", "ytm": 0.1765, "prem": 0.0139, "end": "2029-06-07", "cpn": 14.2, "freq": 12}],
        "A": [{"id": "RU000A10D533", "name": "Систем2P12", "rating": "A+", "ytm": 0.2003, "prem": 0.0205, "end": "2027-12-20", "cpn": 18.5, "freq": 12}],
        "CNY": [{"id": "RU000A10B347", "name": "Акрон Б1P7", "rating": "AA", "ytm": 0.096, "prem": 0.0184, "end": "2027-03-04", "cpn": 8.0, "freq": 4}]}},
        "placements": [
            {"name": "ЯТЭК", "book": d(3), "ratings": ["A(RU)", "ruA-"], "coupon": "ΣКС + 3.5%", "period": "30 дней", "place": d(6),
             "maturity": "", "volume": "1 млрд", "note": "", "url": "https://bonds-lab.ru/placements"},
            {"name": "ЛОЭСК", "book": d(-3), "ratings": ["A.ru"], "coupon": "18.5%", "period": "30 дней", "place": d(1),
             "maturity": "", "volume": "≤ 1 млрд", "note": "Оферта через 2 года (18.09.2028)", "url": "https://bonds-lab.ru/placements"}],
        "state": {"rating_changes": [
            {"secid": "RU000A10C8F3", "name": "Брус 2Р04", "from": "A-", "to": "BBB", "date": d(-3)},
            {"secid": "RU000A0ZZ1", "name": "Пример Д", "from": "B-", "to": "D", "date": d(-2)},
            {"secid": "RU000A10EF52", "name": "Сбер Sb52R", "from": "AA+", "to": "AAA", "date": d(-5)}]}}
