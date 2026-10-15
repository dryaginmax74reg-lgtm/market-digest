"""Офлайн-проверка bonds_meta.py на подменённых ответах источников."""
import json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bonds_meta as bm

ROW = ('<tr><td class="trades-table__chartrow"></td><td>1.9</td><td class="up">15.2%</td><td>14.2%</td><td>14.2%</td>'
       '<td class="bond-rating bond-rating--20">{r}</td><td>53</td><td>289.5</td><td>11.67</td><td>12</td><td>2.7</td>'
       '<td><span class="portfolio_action" symbol="MOEX.{s}">+</span></td></tr>')
def sl_html(rows): return ("<html><table>" + "".join(ROW.format(r=r, s=s) for s, r in rows) + "</table></html>").encode()
CORP = [[(f"RU000A1{p:02d}{i:03d}", ["AAA", "AA-", "BBB", "-"][i % 4]) for i in range(100)] for p in range(1, 4)]
def fake_get(url, **kw):
    if "smart-lab" in url:
        if "/q/bonds/" in url:
            p = 1
            if "/page" in url: p = int(url.split("/page")[1].split("/")[0])
            return sl_html(CORP[p - 1]) if p <= 3 else sl_html(CORP[2])     # сайт повторяет последнюю страницу
        if "/q/ofz/" in url: return sl_html([("SU26238RMFS4", "")])
        return sl_html([])
    if "securities.json?engine" in url:
        start = int(url.split("start=")[1])
        allsec = [s for pg in CORP for s, _ in pg] + ["SU26238RMFS4"]
        rows = [[s, s if s.startswith("RU") else "RU000A1038V6", 7 if i % 2 else 9, "Эмитент %d" % (i % 2), "TQCB"] for i, s in enumerate(allsec)]
        return json.dumps({"securities": {"columns": ["secid", "isin", "emitent_id", "emitent_title", "primary_boardid"], "data": rows[start:start + 100]}}).encode()
    if "history" in url:
        start = int(url.split("start=")[1]); board = "TQOB" if "TQOB" in url else "TQCB"
        secs = ["SU26238RMFS4"] if board == "TQOB" else [s for s, _ in CORP[0]]
        rows = [[s, "2026-10-14", 1e6 * (i + 1), 99.5, 99.6] for i, s in enumerate(secs)]
        return json.dumps({"history": {"columns": ["SECID", "TRADEDATE", "VALUE", "WAPRICE", "CLOSE"], "data": rows[start:start + 100]},
                           "history.cursor": {"columns": ["INDEX", "TOTAL", "PAGESIZE"], "data": [[start, len(rows), 100]]}}).encode()
    raise AssertionError(url)
bm.http_get = fake_get
bm.time.sleep = lambda s: None
bm.MIN_RATED = 100
d = tempfile.mkdtemp(); bm.META = os.path.join(d, "m.json"); bm.STATE = os.path.join(d, "s.json")
bm.main()
m = json.load(open(bm.META)); st = json.load(open(bm.STATE))
assert len(m["b"]) == 301, len(m["b"])
assert m["b"]["RU000A101000"][0] == "AAA" and m["b"]["RU000A101003"][0] == "", m["b"]["RU000A101003"]
assert m["b"]["RU000A101000"][2] == 1000000, m["b"]["RU000A101000"]
assert m["em"]["7"] == "Эмитент 1"
assert m["b"]["RU000A101000"][4:] == [15.2], m["b"]["RU000A101000"]
# второй день: рейтинг сменился -> фиксируется изменение
CORP[0][0] = ("RU000A101000", "AA+")
st["days"] = ["2026-10-13"]; json.dump(st, open(bm.STATE, "w"))
bm.main()
st = json.load(open(bm.STATE))
ch = st["rating_changes"]
assert ch and ch[-1]["secid"] == "RU000A101000" and ch[-1]["from"] == "AAA" and ch[-1]["to"] == "AA+", ch
assert st["days"] == ["2026-10-13", "2026-10-14"] and len(st["tv"]["RU000A101000"]) == 2
print("bonds_meta: OK", len(m["b"]), "бумаг, рейтингов", m["rated"])
