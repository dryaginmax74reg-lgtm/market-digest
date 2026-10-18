#!/usr/bin/env python3
"""Отчётность эмитентов облигаций (РСБУ, годовая) из ГИР БО ФНС — bo.nalog.gov.ru. Раз в месяц, с кэшем:
эмитент перезапрашивается, если данных нет или им больше 90 дней (новая годовая отчётность выходит весной).

Пишет docs/fund.json — для страницы облигаций:
  {"updated", "src", "f": {emitent_id: {inn, y, rev, rev0, ebit, int, np, debt, cash, eq, assets, flags, lvl, spv, t}}}
  суммы — тыс. ₽ (как в отчётности); lvl — штраф в ступенях рейтинга (0, 1, 2) для оценки бумаг.

Строки РСБУ: 2110 выручка, 2200 прибыль от продаж (замена EBITDA — амортизации отдельной строкой в РСБУ нет),
2330 проценты к уплате, 2400 чистая прибыль, 1410+1510 займы, 1250 деньги, 1300 капитал, 1600 активы.

Сигналы «слабая отчётность» (осторожные, чтобы не пугать зря):
  • прибыль от продаж ≤ 0 при процентных расходах;
  • прибыль от продаж не покрывает проценты (меньше 1×);
  • чистый долг больше 6 прибылей от продаж;
  • отрицательный капитал.
  Один сигнал — штраф 1 ступень, два и больше (или отрицательный капитал) — 2 ступени.
  Финансовые «прокладки» и холдинги (выручка < 5% активов) не штрафуются: их отчётность не показывает группу.
  Банки, страховые, МФО отчитываются в ЦБ, а не в ФНС — у них данных не будет.

python fundamentals.py           боевой
python fundamentals.py --check   проверка доступа: ISS (ИНН эмитентов) и ГИР БО на двух компаниях, ничего не пишет
"""
import json
import os
import re
import sys
import time
from datetime import datetime

from common import ISS, MSK, http_get, log

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "docs", "fund.json")
REFRESH_DAYS = 90
MAX_PER_RUN = int(os.environ.get("FUND_MAX", "700"))
BASES = ["https://bo.nalog.gov.ru", "https://bo.nalog.ru"]
HDR = {"Accept": "application/json, text/plain, */*", "Referer": "https://bo.nalog.gov.ru/"}
CODES = ["2110", "2200", "2330", "2400", "1410", "1510", "1250", "1300", "1600"]


def iss_rows(path, block):
    rows, start = [], 0
    while True:
        j = json.loads(http_get(f"{ISS}{path}&start={start}"))
        t = j[block]
        page = [dict(zip(t["columns"], r)) for r in t["data"]]
        rows += page
        if len(page) < 100:
            break
        start += len(page)
    return rows


def issuers():
    """{emitent_id: (inn, название)} по торгуемым облигациям (без ОФЗ и иностранных эмитентов)."""
    out = {}
    for r in iss_rows("securities.json?engine=stock&market=bonds&is_trading=1&limit=100"
                      "&securities.columns=secid,emitent_id,emitent_title,emitent_inn", "securities"):
        inn = str(r.get("emitent_inn") or "").strip()
        if r.get("emitent_id") is not None and re.fullmatch(r"\d{10}", inn) and inn != "7710168360":   # Минфин
            out[str(r["emitent_id"])] = (inn, r.get("emitent_title") or "")
    return out


def _get_json(path):
    last = None
    for base in BASES:
        try:
            return json.loads(http_get(base + path, headers=HDR, retries=2, timeout=20))
        except Exception as ex:  # noqa: BLE001
            last = ex
    raise last


def _walk(o, out, pref):
    """Все значения вида current1600 / previous2110 из вложенного JSON."""
    if isinstance(o, dict):
        for k, v in o.items():
            m = re.fullmatch(rf"{pref}(\d{{4}})", str(k))
            if m and isinstance(v, (int, float)):
                out.setdefault(m.group(1), v)
            else:
                _walk(v, out, pref)
    elif isinstance(o, list):
        for v in o:
            _walk(v, out, pref)


def org_id(inn):
    for path in (f"/advanced-search/organizations/search?query={inn}&page=0", f"/nbo/organizations/search?query={inn}&page=0"):
        try:
            j = _get_json(path)
        except Exception:  # noqa: BLE001
            continue
        items = j.get("content") if isinstance(j, dict) else j
        for it in items or []:
            if str(it.get("inn")) == inn and it.get("id") is not None:
                return it["id"]
    return None


def latest_report(oid):
    """(год, {код: текущее значение}, {код: прошлый год}) последней годовой отчётности."""
    reps = _get_json(f"/nbo/organizations/{oid}/bfo/")
    if isinstance(reps, dict):
        reps = reps.get("content") or []
    reps = [r for r in reps if str(r.get("period", "")).isdigit()]
    if not reps:
        return None
    rep = max(reps, key=lambda r: int(r["period"]))
    cur, prev = {}, {}
    _walk(rep, cur, "current")
    _walk(rep, prev, "previous")
    if not cur and rep.get("id") is not None:          # в списке нет цифр — берём подробности отчёта
        det = _get_json(f"/nbo/bfo/{rep['id']}/details")
        _walk(det, cur, "current")
        _walk(det, prev, "previous")
    return int(rep["period"]), cur, prev


def assess(cur, prev):
    g = lambda d, k: float(d.get(k) or 0)
    rev, ebit, intr, np_, eq, assets = g(cur, "2110"), g(cur, "2200"), g(cur, "2330"), g(cur, "2400"), g(cur, "1300"), g(cur, "1600")
    debt, cash = g(cur, "1410") + g(cur, "1510"), g(cur, "1250")
    x = {"rev": rev, "rev0": g(prev, "2110"), "ebit": ebit, "int": intr, "np": np_, "debt": debt, "cash": cash,
         "sdebt": g(cur, "1510"), "eq": eq, "assets": assets}
    spv = assets > 0 and rev < 0.05 * assets
    flags = []
    if not spv:
        if ebit <= 0 and intr > 0:
            flags.append("убыток от продаж при процентных расходах")
        elif intr > 0 and ebit / intr < 1:
            flags.append(f"прибыль от продаж покрывает проценты только на {ebit / intr:.1f}×".replace(".", ","))
        if ebit > 0 and (debt - cash) / ebit > 6:
            flags.append(f"чистый долг — {(debt - cash) / ebit:.1f} прибыли от продаж".replace(".", ","))
    if eq < 0:
        flags.append("отрицательный капитал")
    lvl = 0 if not flags else 2 if (len(flags) >= 2 or eq < 0) else 1
    x.update({"flags": flags, "lvl": lvl, "spv": spv})
    return x


def fetch_one(inn):
    oid = org_id(inn)
    if oid is None:
        return None
    rep = latest_report(oid)
    if not rep:
        return None
    y, cur, prev = rep
    if not any(k in cur for k in CODES):
        return None
    x = assess(cur, prev)
    x["y"] = y
    return x


def check():
    ok = True
    try:
        em = issuers()
        log(f"ISS: эмитентов с ИНН — {len(em)}")
    except Exception as ex:  # noqa: BLE001
        ok = False
        log(f"ISS: ошибка {type(ex).__name__}: {ex}")
    for inn, name in (("7708503727", "РЖД"), ("7728168971", "Альфа? (проверка банка — данных быть не должно)")):
        for base in BASES:
            try:
                raw = http_get(f"{base}/advanced-search/organizations/search?query={inn}&page=0", headers=HDR, retries=1, timeout=20)
                log(f"{base}: поиск {name}: HTTP 200, {len(raw)} байт, начало: {raw[:160]!r}")
            except Exception as ex:  # noqa: BLE001
                log(f"{base}: поиск {name}: {type(ex).__name__}: {ex}")
        try:
            x = fetch_one(inn)
            log(f"ГИР БО {name}: {json.dumps(x, ensure_ascii=False)[:400] if x else 'нет данных'}")
            if inn == "7708503727" and not x:
                ok = False
        except Exception as ex:  # noqa: BLE001
            ok = False
            log(f"ГИР БО {name}: {type(ex).__name__}: {ex}")
    log("ИТОГ: " + ("доступ есть ✅" if ok else "доступа нет ❌ — пришлите этот лог"))
    return ok


def main():
    if "--check" in sys.argv:
        sys.exit(0 if check() else 1)
    now = datetime.now(MSK)
    try:
        old = json.load(open(OUT, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        old = {"f": {}}
    f = old.get("f") or {}
    em = issuers()
    log(f"эмитентов с ИНН: {len(em)}")
    done = fails = 0
    t0 = time.time()
    for eid, (inn, name) in em.items():
        x = f.get(eid)
        if x and x.get("t") and (now.date() - datetime.fromisoformat(x["t"]).date()).days < REFRESH_DAYS:
            continue
        if done >= MAX_PER_RUN or time.time() - t0 > 40 * 60:
            log("лимит запуска — остальные в следующий раз")
            break
        done += 1
        try:
            y = fetch_one(inn)
        except Exception as ex:  # noqa: BLE001
            fails += 1
            log(f"[warn] {name} ({inn}): {type(ex).__name__}: {ex}")
            if fails >= 15 and fails == done:
                log("ГИР БО не отвечает — выхожу, файл не трогаю")
                sys.exit(1)
            continue
        f[eid] = dict(y or {"none": True}, inn=inn, t=now.date().isoformat())
        time.sleep(0.4)
    f = {k: v for k, v in f.items() if k in em}
    out = {"updated": now.strftime("%Y-%m-%d %H:%M"), "src": "ГИР БО ФНС (РСБУ, годовая)", "f": f}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
    have = sum(1 for v in f.values() if not v.get("none"))
    weak = sum(1 for v in f.values() if v.get("lvl"))
    log(f"docs/fund.json: эмитентов {len(f)}, с отчётностью {have}, со слабой отчётностью {weak}; запрошено сейчас {done}, ошибок {fails}")


if __name__ == "__main__":
    main()
