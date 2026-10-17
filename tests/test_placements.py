import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import placements
raw = open(os.path.join(os.path.dirname(__file__), "fixtures", "bondslab.html"), encoding="utf-8").read()
p = placements.parse(raw)
assert len(p) == 3, p
a, b, c = p
assert a["name"] == "ЯТЭК" and a["book"] == "2026-10-01" and a["ratings"] == ["A(RU)", "ruA-"], a
assert a["coupon"] == "ΣКС + 3.5%" and a["place"] == "2026-10-06" and a["volume"] == "1 млрд" and a["url"].endswith("e31b"), a
assert b["ratings"] == [] and b["note"].startswith("Амортизация") and b["coupon"] == "Будет определен позднее", b
assert c["ratings"] == ["AA+(RU)", "ruAAA", "AA+.ru"] and "Субординированный" in c["note"], c
print("placements: OK", [(x["name"], x["book"], x["coupon"]) for x in p])

# календарь размещений smart-lab
_sl = placements.parse_smartlab(open(os.path.join(os.path.dirname(__file__), "fixtures", "smartlab_placement.html"), encoding="utf-8").read())
assert len(_sl) == 4, _sl
assert _sl[0]["name"] == "Сегежа Групп-004P-01R" and _sl[0]["book"] == "2026-10-19" and _sl[0]["period"] == "месяц" and _sl[0]["maturity"] == "5 лет" and _sl[0]["volume"] == "от 1 млрд ₽", _sl[0]
assert _sl[0]["coupon"] == "ориентир 21% (YTP 23,14%)" and _sl[0]["note"] == "оферта через 3 мес.", _sl[0]
assert "амортизация" in _sl[1]["note"] and "для квалов" in _sl[1]["note"], _sl[1]
assert _sl[2]["name"] == "ЭкономЛизинг-002Р-04" and _sl[2]["book"] is None and _sl[2]["place"] == "2026-10-20" and _sl[2]["maturity"] == "4 года", _sl[2]
assert _sl[3]["maturity"] == "3,5 года" and _sl[3]["period"] == "полгода", _sl[3]
print("smart-lab: OK", [(x["name"], x["book"] or x["place"]) for x in _sl])

# инфляция ЦБ (таблица hd_base/infl)
from common import parse_inflation  # noqa: E402
_inf = parse_inflation(open(os.path.join(os.path.dirname(__file__), "fixtures", "cbr_infl.html"), encoding="utf-8").read())
assert _inf == [("2026-08-31", 6.33), ("2026-09-30", 6.35)], _inf
print("инфляция: OK", _inf[-1])
