"""Офлайн-проверка fundamentals.py: ИНН из ISS, поиск и отчётность ГИР БО (поддельные ответы), сигналы."""
import json, os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import fundamentals as fu

ISSROWS = [["RU1", "1", "ПАО Хорошая", "7700000001"], ["RU2", "2", "ООО Слабая", "7700000002"],
           ["RU3", "3", "ООО Финанс", "7700000003"], ["SU1", "9", "Минфин России", "7710168360"], ["RU4", "4", "Банк", "7700000004"]]
REP = {"7700000001": {"current2110": 100000, "current2200": 20000, "current2330": 5000, "current2400": 9000, "current1410": 40000,
                      "current1510": 10000, "current1250": 5000, "current1300": 60000, "current1600": 150000, "previous2110": 90000},
       "7700000002": {"current2110": 50000, "current2200": 3000, "current2330": 6000, "current2400": -4000, "current1410": 30000,
                      "current1510": 20000, "current1250": 1000, "current1300": -2000, "current1600": 60000},
       "7700000003": {"current2110": 10, "current2200": 5, "current2330": 900, "current2400": 1, "current1410": 50000,
                      "current1510": 0, "current1250": 10, "current1300": 100, "current1600": 51000}}
def fake(url, headers=None, retries=3, timeout=25, data=None):
    if "iss" in url:
        start = int(url.split("start=")[1])
        return json.dumps({"securities": {"columns": ["secid", "emitent_id", "emitent_title", "emitent_inn"], "data": ISSROWS[start:start + 100]}}).encode()
    if "search" in url:
        inn = url.split("query=")[1].split("&")[0]
        return json.dumps({"content": [{"id": int(inn[-1]), "inn": inn}] if inn in REP else []}).encode()
    if "/bfo/" in url:
        oid = url.split("/organizations/")[1].split("/")[0]
        inn = "770000000" + oid
        return json.dumps([{"id": 1, "period": "2024", "typeCorrections": [{"correction": {"balance": {}, "financialResult": {}}}]},
                           {"id": 2, "period": "2025", "typeCorrections": [{"correction": {"x": REP[inn]}}]}]).encode()
    raise AssertionError(url)
fu.http_get = fake
fu.time.sleep = lambda s: None
d = tempfile.mkdtemp(); fu.OUT = os.path.join(d, "fund.json")
fu.main()
f = json.load(open(fu.OUT))["f"]
assert set(f) == {"1", "2", "3", "4"}, f.keys()
assert f["1"]["lvl"] == 0 and f["1"]["y"] == 2025 and f["1"]["rev0"] == 90000, f["1"]
assert f["2"]["lvl"] == 2 and "отрицательный капитал" in f["2"]["flags"], f["2"]
assert f["3"]["spv"] and f["3"]["lvl"] == 0, f["3"]
assert f["4"].get("none"), f["4"]
print("fundamentals: OK", {k: (v.get("lvl"), v.get("flags")) for k, v in f.items()})
