import json, os, subprocess, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bondscore import composite, compute_fair
d = tempfile.mkdtemp(); a, b = os.path.join(d, "in.json"), os.path.join(d, "js.json")
subprocess.run(["node", os.path.join(os.path.dirname(__file__), "parity_bondscore.js"), a, b], check=True)
rows = json.load(open(a)); js = {x[0]: x[1:] for x in json.load(open(b))}
compute_fair(rows)
composite(rows)
bad = 0
for r in rows:
    f, p, fl, sc, cs = js[r["id"]]
    def neq(x, y): return (x is None) != (y is None) or (x is not None and abs(x - y) > 1e-12)
    if neq(f, r["fair"]) or neq(p, r["prem"]) or fl != r["flag"] or neq(sc, r["score"]) or cs != r["cs"]:
        bad += 1
        if bad < 5: print("DIFF", r["id"], (f, p, fl, sc), (r["fair"], r["prem"], r["flag"], r["score"]))
print("parity:", "OK" if not bad else f"{bad} расхождений", f"({len(rows)} бумаг, с оценкой {sum(1 for r in rows if r['fair'] is not None)})")
sys.exit(1 if bad else 0)
