#!/usr/bin/env python3
"""Утренняя самопроверка страницы облигаций на живых данных Мосбиржи.

Собирает портфели во всех сочетаниях (профиль × сумма 3 млн–3 млрд × срок × валюты), считает их тем же кодом,
что и страница, и прогоняет «самопроверку» (sanityIssues в bonds.html): недовложено, неправдоподобный итог,
бумага в дефолте/стрессе, доли не сходятся, нет одного лота и т. д.
Если находит ошибки — пишет в Telegram, что и где. Если всё в порядке — молчит.
Если сама проверка не смогла отработать (нет данных, страница упала) — тоже пишет, чтобы тишина не значила «всё хорошо».

python tools/selfcheck.py                 боевой (нужны TG_BOT_TOKEN, TG_CHAT_ID)
python tools/selfcheck.py --dry           без отправки в Telegram, печатает отчёт
python tools/selfcheck.py --iss URL       другой адрес ISS (для тестов на снимке)
python tools/selfcheck.py --quick         урезанный набор сочетаний
python tools/selfcheck.py --url U --route DIR   тест в песочнице: готовый адрес страницы и подмена внешних библиотек
"""
import asyncio
import functools
import http.server
import itertools
import json
import os
import sys
import threading
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from common import log, tg_creds, tg_send, e  # noqa: E402

PROFS = ["cons", "mod", "inc", "vdo", "ofz"]
AMTS = [3e6, 10e6, 30e6, 100e6, 300e6, 1e9, 3e9]
TERMS = [6, 12, 24, 36, 60]
CURS = [{"RUB": 100, "CNY": 0, "USD": 0, "EUR": 0}, {"RUB": 70, "CNY": 30, "USD": 0, "EUR": 0},
        {"RUB": 50, "CNY": 30, "USD": 20, "EUR": 0}]
PNAME = {"cons": "Консервативный", "mod": "Умеренный", "inc": "Доходный", "vdo": "Короткие ВДО", "ofz": "Только ОФЗ"}


def arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def serve_docs():
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    h = functools.partial(Quiet, directory=os.path.join(ROOT, "docs"))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


async def run(url, cases, route=None):
    from playwright.async_api import async_playwright
    found, page_err = [], []
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page()
        pg.on("pageerror", lambda ex: page_err.append(str(ex)))
        if route:
            await pg.route("**/*", route)
        await pg.goto(url)
        await pg.wait_for_function("window.__BONDS && window.__BONDS.U.length>0", timeout=120000)
        n_u = await pg.evaluate("window.__BONDS.U.length")
        log(f"бумаг загружено: {n_u}")
        for i, (prof, amt, term, cur) in enumerate(cases):
            try:
                r = await pg.evaluate("""async ([prof,amt,term,cur])=>{ const B=window.__BONDS; B.P.amount=amt; B.P.term=term; B.P.profile=prof;
                  B.P.params=JSON.parse(JSON.stringify(B.PROFILES[prof])); B.P.cur=cur; B.P.pos=[]; B.P.carry={on:false,ltv:100,pos:[],warns:[]};
                  await B.autoBuildChecked(); const A=await B.computeA(B.P.pos,{light:true});
                  if(!A||A.empty) return {issues:[{lvl:'err', t:'портфель не собрался — ни одной бумаги'}], warns:B.P.warns};
                  return {issues:B.sanityOf(A), warns:B.P.warns}; }""", [prof, amt, term, cur])
            except Exception as ex:  # noqa: BLE001
                r = {"issues": [{"lvl": "err", "t": f"расчёт упал: {str(ex)[:200]}"}], "warns": []}
            for it in r["issues"]:
                if it["lvl"] == "err":
                    found.append(((prof, amt, term, cur), it["t"]))
            if i % 50 == 0:
                log(f"{i}/{len(cases)}")
        await b.close()
    return found, page_err, n_u


def label(c):
    prof, amt, term, cur = c
    cu = "/".join(f"{k} {v}" for k, v in cur.items() if v)
    s = f"{amt/1e9:g} млрд" if amt >= 1e9 else f"{amt/1e6:g} млн"
    return f"{PNAME[prof]}, {s}, {term} мес, {cu}"


def main():
    dry = "--dry" in sys.argv
    cases = [c for c in itertools.product(PROFS, AMTS, TERMS, CURS) if not (c[0] in ("ofz", "vdo") and c[3]["RUB"] < 100)]
    if "--quick" in sys.argv:
        cases = cases[::7]
    srv = serve_docs()
    iss = arg("--iss")
    url = arg("--url") or (f"http://127.0.0.1:{srv.server_address[1]}/bonds.html" + (f"?iss={iss}" if iss else ""))
    try:
        route = None
        if arg("--route"):                    # тесты в песочнице: внешние библиотеки отдаём из локальных файлов
            sys.path.insert(0, arg("--route"))
            from matrix import route          # noqa: E402
        found, page_err, n_u = asyncio.run(run(url, cases, route))
    except Exception as ex:  # noqa: BLE001
        msg = f"⚠️ <b>Самопроверка облигаций не отработала</b>\n{e(type(ex).__name__)}: {e(str(ex)[:300])}\nСтраница могла не загрузить данные Мосбиржи — проверьте её перед встречами."
        log(msg)
        if not dry:
            tg_send(*tg_creds(), msg)
        sys.exit(1)
    # группируем: одна и та же проблема в разных сочетаниях
    lines = []
    groups = defaultdict(list)
    for c, t in found:
        groups[t].append(c)
    for t, cs in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:12]:
        lines.append(f"• {e(t)} — {len(cs)} сочет.: {e(label(cs[0]))}{' и др.' if len(cs) > 1 else ''}")
    bad = len({label(c) for c, _ in found})
    log(f"сочетаний {len(cases)}, с ошибками {bad}, ошибок страницы {len(page_err)}")
    if page_err:
        lines.insert(0, f"• ошибки JavaScript на странице: {e('; '.join(page_err[:2])[:300])}")
    if not lines:
        log("всё в порядке — в Telegram не пишу")
        return
    msg = (f"⚠️ <b>Самопроверка облигаций: {bad} из {len(cases)} портфелей с ошибками</b>\n"
           f"цены Мосбиржи сегодня, {n_u} бумаг\n\n" + "\n".join(lines) +
           "\n\nПеред встречей проверьте такие портфели — на странице они помечены «Проверьте перед показом».")
    print(msg)
    if not dry:
        tg_send(*tg_creds(), msg)


if __name__ == "__main__":
    main()
