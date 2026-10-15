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
