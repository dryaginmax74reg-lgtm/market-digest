const B = require('../docs/bondmath.js');
let fails = 0;
const ok = (name, cond, info='') => { console.log((cond ? 'OK  ' : 'FAIL') + '  ' + name + (info ? '  ' + info : '')); if (!cond) fails++; };
const near = (a, b, tol) => Math.abs(a - b) <= tol;

// генератор купонной бумаги: номинал 1000, ставка c% годовых, n купонов раз в period дней
function mk({ c = 10, period = 182, n = 6, first = '2027-01-01', amort = null, face = 1000 }) {
  const coupons = [], amorts = [];
  let d = B.toMs(first), start = d - period * 86400000, f = face;
  for (let i = 0; i < n; i++) {
    const v = Math.round(c / 100 * f * period / 365 * 100) / 100;
    coupons.push({ date: B.iso(d), start: B.iso(start), value: v, pct: c });
    if (amort && amort[i]) { amorts.push({ date: B.iso(d), value: amort[i] }); f -= amort[i]; }
    start = d; d += period * 86400000;
  }
  const mat = coupons[n - 1].date;
  if (!amort) amorts.push({ date: mat, value: face });
  return { face, initialFace: face, couponPct: c, matdate: mat, coupons, amorts };
}

// 1. Бескупонная: цена 1000/(1.12)^2 → доходность ровно 12%
{
  const b = { face: 1000, initialFace: 1000, couponPct: 0, matdate: '2028-01-01', coupons: [], amorts: [{ date: '2028-01-01', value: 1000 }] };
  const t = B.days('2026-01-01', '2028-01-01') / 365;
  const P = 1000 / Math.pow(1.12, t);
  const a = B.analyze(b, '2026-01-01', P / 10, 0);
  ok('zero-coupon YTM', near(a.ytm, 0.12, 1e-10), a.ytm);
  ok('zero-coupon Macaulay = срок', near(a.macaulay, t, 1e-10), a.macaulay + ' vs ' + t);
  ok('zero-coupon convexity = t(t+1)/(1+y)^2', near(a.convexity, t * (t + 1) / 1.12 / 1.12, 1e-9));
}
// 2. Купонная по номиналу без НКД: эффективная доходность = (1+c·p/365)^(365/p)−1
{
  const b = mk({ c: 14, period: 182, n: 6, first: '2026-07-02' });
  const settle = '2026-01-01';   // ровно в начале периода -> НКД 0
  const a = B.analyze(b, settle, 100, 0);
  const cp = Math.round(0.14 * 1000 * 182 / 365 * 100) / 100;
  // точная доходность: решаем вручную через формулу аннуитета
  const per = 182 / 365, g = cp / 1000;
  const exp = Math.pow(1 + g, 1 / per) - 1;
  ok('par bond YTM = (1+c·p)^(1/p)−1', near(a.ytm, exp, 1e-9), (a.ytm * 100).toFixed(6) + ' vs ' + (exp * 100).toFixed(6));
  // численная проверка дюрации: -dP/dy·(1+y)/P
  const h = 1e-6, P0 = B.pv(a.flows, a.ytm), Pu = B.pv(a.flows, a.ytm + h), Pd = B.pv(a.flows, a.ytm - h);
  const modNum = -(Pu - Pd) / (2 * h) / P0;
  ok('ModD = −dP/dy/P (числ.)', near(a.modified, modNum, 1e-6), a.modified + ' vs ' + modNum);
  const convNum = (Pu + Pd - 2 * P0) / (h * h) / P0;
  ok('Convexity = d²P/dy²/P (числ.)', near(a.convexity, convNum, 1e-3), a.convexity + ' vs ' + convNum);
  const sh = B.shock(a.flows, a.ytm, 0.01);
  ok('шок +1п.п.: ModD+выпукл. ≈ точная переоценка', near(sh.exact, sh.approx, 2e-4), sh.exact + ' vs ' + sh.approx);
}
// 3. Амортизация: доходность не зависит от того, как разложен поток, проверим IRR через PV
{
  const b = mk({ c: 12, period: 91, n: 8, first: '2026-03-01', amort: [0, 0, 0, 250, 250, 0, 250, 250] });
  b.face = 1000;
  const a = B.analyze(b, '2026-01-15', 98.5, B.accrued(b, '2026-01-15'));
  ok('амортизация: PV(ytm) = грязная цена', near(B.pv(a.flows, a.ytm), a.dirty, 1e-7));
  const sumPrin = a.flows.filter(f => f.kind !== 'c').reduce((s, f) => s + f.amt, 0);
  ok('амортизация: сумма погашений = номинал', near(sumPrin, 1000, 1e-9), sumPrin);
  // купоны после амортизации меньше
  ok('купон после частичного погашения меньше', a.flows.filter(f => f.kind === 'c').slice(-1)[0].amt < a.flows.filter(f => f.kind === 'c')[0].amt);
}
// 4. НКД: середина периода
{
  const b = mk({ c: 10, period: 182, n: 4, first: '2026-07-02' });
  const ai = B.accrued(b, B.iso(B.toMs('2026-01-01') + 91 * 86400000));
  ok('НКД в середине периода = половина купона', near(ai, Math.round(b.coupons[0].value / 2 * 100) / 100, 0.011), ai + ' vs ' + b.coupons[0].value / 2);
}
// 5. Оферта: поток обрезается датой оферты, остаток номинала гасится по 100%
{
  const b = mk({ c: 15, period: 91, n: 20, first: '2026-03-01' });
  b.offerdate = b.coupons[7].date;
  const a = B.analyze(b, '2026-01-15', 100, B.accrued(b, '2026-01-15'));
  ok('к оферте: последний поток — дата оферты', a.flows.slice(-1)[0].date === b.offerdate && a.toOffer);
  ok('к оферте: 8 купонов + номинал', a.flows.length === 9, a.flows.length);
}
// 6. Неизвестные купоны (флоатер): заполняются по последней ставке
{
  const b = mk({ c: 16, period: 30, n: 12, first: '2026-02-01' });
  for (let i = 3; i < 12; i++) { b.coupons[i].value = null; b.coupons[i].pct = null; }
  const a = B.analyze(b, '2026-01-15', 100, B.accrued(b, '2026-01-15'));
  const est = a.flows.filter(f => f.est);
  ok('флоатер: 9 оценочных купонов', est.length === 9 && a.est, est.length);
  ok('флоатер: оценка = известному купону', near(est[0].amt, b.coupons[0].value, 0.011), est[0].amt + ' vs ' + b.coupons[0].value);
}
// 7. Симуляция: без налогов, реинвест по YTM, горизонт = погашение → годовая = YTM (одна бумага)
{
  const b = mk({ c: 13, period: 182, n: 8, first: '2026-04-01' });
  const settle = '2026-01-20', ai = B.accrued(b, settle);
  const a = B.analyze(b, settle, 96.3, ai);
  const pos = [{ id: 'X', qty: 10, bond: b, flows: a.flows, clean: 96.3, accrued: ai, fx: 1, ytm: a.ytm }];
  const s = B.simulate(pos, { settle, account: 'none', reinvest: a.ytm });
  ok('симуляция без налога, реинвест по YTM = YTM', near(s.annual, a.ytm, 1e-9), (s.annual * 100).toFixed(7) + ' vs ' + (a.ytm * 100).toFixed(7));
  const s2 = B.simulate(pos, { settle, account: 'iis3', reinvest: a.ytm });
  ok('ИИС-3 = без налога', near(s2.annual, a.ytm, 1e-9));
  // горизонт раньше погашения и та же доходность — тоже YTM
  const s3 = B.simulate(pos, { settle, account: 'none', reinvest: a.ytm, horizon: '2027-06-10' });
  ok('горизонт до погашения, продажа по той же доходности = YTM', near(s3.annual, a.ytm, 1e-9), s3.annual);
  // портфельный YTM одной бумаги = её YTM
  const py = B.portfolioYield(pos, settle);
  ok('portfolioYield одной бумаги', near(py.ytm, a.ytm, 1e-10));
}
// 8. Налог 13% на бумагу по номиналу без НКД, без реинвеста: 2 купона и погашение
{
  const b = { face: 1000, initialFace: 1000, couponPct: 10, matdate: '2026-12-01',
    coupons: [{ date: '2026-06-01', start: '2025-12-01', value: 50, pct: 10 }, { date: '2026-12-01', start: '2026-06-01', value: 50, pct: 10 }],
    amorts: [{ date: '2026-12-01', value: 1000 }] };
  const settle = '2025-12-01';
  const a = B.analyze(b, settle, 100, 0);
  const pos = [{ id: 'Y', qty: 100, bond: b, flows: a.flows, clean: 100, accrued: 0, fx: 1, ytm: a.ytm }];
  const s = B.simulate(pos, { settle, account: 'std', reinvest: 0 });
  ok('налог: 2 купона по 50×100, удержано 13%', near(s.final, 100000 + 10000 * 0.87, 1e-6), s.final);
  // НКД при покупке уменьшает базу: покупаем с НКД 20
  const pos2 = [{ ...pos[0], accrued: 20 }];
  const s4 = B.simulate(pos2, { settle, account: 'std', reinvest: 0 });
  ok('налог: НКД покупки уменьшает базу', near(s4.final, 100000 + 10000 - 0.13 * (10000 - 2000), 1e-6), s4.final);
  // покупка с дисконтом 95 → налог 13% на 5% номинала при погашении
  const pos3 = [{ ...pos[0], clean: 95 }];
  const s5 = B.simulate(pos3, { settle, account: 'std', reinvest: 0 });
  ok('налог: дисконт при погашении', near(s5.final, 100000 + 8700 - 0.13 * 5000, 1e-6), s5.final);
  // крупная сумма: прогрессия 2026 (15% свыше 2.4 млн)
  const pos4 = [{ ...pos[0], qty: 50000 }];   // купоны 5 млн
  const s6 = B.simulate(pos4, { settle, account: 'std', reinvest: 0 });
  const t26 = 2.4e6 * 0.13 + 2.6e6 * 0.15;
  ok('налог 2026: 13% до 2,4 млн, 15% свыше', near(s6.final, 50e6 + 5e6 - t26, 1e-4), s6.final + ' vs ' + (50e6 + 5e6 - t26));
}
// 9. Реформа 2027: шкала 13/15/18/20/22
{
  ok('шкала: 2,4 млн', near(B.taxScale(2.4e6), 312000, 1e-6));
  ok('шкала: 5 млн', near(B.taxScale(5e6), 312000 + 2.6e6 * 0.15, 1e-6));
  ok('шкала: 60 млн', near(B.taxScale(60e6), 312000 + 390000 + 15e6 * .18 + 30e6 * .2 + 10e6 * .22, 1e-6));
  ok('2026 по старым правилам', near(B.yearTax(2026, 60e6, true), B.tax1315(60e6), 1e-6));
}

// 10. Плечо: займ по ставке = доходности бумаги, без налога → результат на свои деньги = та же доходность
{
  const b = mk({ c: 13, period: 182, n: 8, first: '2026-04-01' });
  const settle = '2026-01-20', ai = B.accrued(b, settle);
  const a = B.analyze(b, settle, 96.3, ai);
  const own = { id: 'O', qty: 10, bond: b, flows: a.flows, clean: 96.3, accrued: ai, fx: 1, ytm: a.ytm };
  const lev = { ...own, id: 'L', qty: 8, lev: true };
  const s = B.simulate([own, lev], { settle, account: 'none', reinvest: a.ytm, loanRate: a.ytm });
  ok('плечо по ставке = доходности: результат не меняется', near(s.annual, a.ytm, 1e-9), s.annual);
  ok('плечо: invested только свои', near(s.invested, B.simulate([own], { settle, account: 'none', reinvest: a.ytm }).invested, 1e-6));
  // займ под 0%: добавка = весь доход заёмной части
  const s0 = B.simulate([own, lev], { settle, account: 'none', reinvest: 0, loanRate: 0 });
  const sOwn = B.simulate([own], { settle, account: 'none', reinvest: 0 });
  const sLevAlone = B.simulate([{ ...lev, lev: false }], { settle, account: 'none', reinvest: 0 });
  ok('плечо под 0%: доход = свой + доход заёмной части', near(s0.profit, sOwn.profit + sLevAlone.profit, 1e-6), s0.profit + ' vs ' + (sOwn.profit + sLevAlone.profit));
  // займ дороже доходности → результат хуже, чем без плеча
  const sBad = B.simulate([own, lev], { settle, account: 'std', reinvest: a.ytm, loanRate: a.ytm + 0.03 });
  const sNo = B.simulate([own], { settle, account: 'std', reinvest: a.ytm });
  ok('дорогой займ ухудшает результат', sBad.annual < sNo.annual, sBad.annual + ' < ' + sNo.annual);
  // ручная проверка: бескупонная, займ 1 год
  const z = { face: 1000, initialFace: 1000, couponPct: 0, matdate: '2027-01-01', coupons: [], amorts: [{ date: '2027-01-01', value: 1000 }] };
  const za = B.analyze(z, '2026-01-01', 90, 0);
  const zo = { id: 'Z', qty: 100, bond: z, flows: za.flows, clean: 90, accrued: 0, fx: 1, ytm: za.ytm };
  const zl = { ...zo, id: 'ZL', qty: 50, lev: true };
  const r = B.simulate([zo, zl], { settle: '2026-01-01', account: 'std', reinvest: 0, loanRate: 0.05, reform: false });
  // свои: 90000 → 100000 (налог 13% с 10000); заём 45000 → 50000 погашение, долг 45000·1.05 = 47250, налог 13% с 5000
  const exp = 100000 - 1300 + 50000 - 650 - 47250;
  ok('плечо, ручной расчёт (бескупонная, 1 год)', near(r.final, exp, 1e-6), r.final + ' vs ' + exp);
}

// 11. Заёмная бумага погашается раньше горизонта: займ гасится в дату погашения, дальше проценты не идут
{
  const z1 = { face: 1000, initialFace: 1000, couponPct: 0, matdate: '2027-01-01', coupons: [], amorts: [{ date: '2027-01-01', value: 1000 }] };
  const z2 = { face: 1000, initialFace: 1000, couponPct: 0, matdate: '2028-01-01', coupons: [], amorts: [{ date: '2028-01-01', value: 1000 }] };
  const a1 = B.analyze(z1, '2026-01-01', 90, 0), a2 = B.analyze(z2, '2026-01-01', 80, 0);
  const own = { id: 'O', qty: 100, bond: z2, flows: a2.flows, clean: 80, accrued: 0, fx: 1, ytm: a2.ytm };
  const lev = { id: 'L', qty: 50, bond: z1, flows: a1.flows, clean: 90, accrued: 0, fx: 1, ytm: a1.ytm, lev: true };
  const r = B.simulate([own, lev], { settle: '2026-01-01', account: 'none', reinvest: 0, loanRate: 0.05 });
  ok('займ гасится погашением заёмной бумаги', near(r.final, 100000 + 50000 - 45000 * 1.05, 1e-6), r.final);
  ok('проценты по займу = 2250', near(r.gross.loanInterest, 2250, 1e-6), r.gross.loanInterest);
}

// 12. Вклад: без капитализации внутри года, переоформление раз в год, налог с необлагаемой частью
{
  const r1 = B.depositNet(1e6, { settle: '2026-01-01', horizon: '2027-01-01', rates: [0.10], reform: false, exempt: () => 160000 });
  ok('вклад 1 год 10%: проценты 100 000, налога нет (ниже лимита)', near(r1.gross, 100000, 0.5) && r1.tax === 0, r1.gross);
  const r2 = B.depositNet(10e6, { settle: '2026-01-01', horizon: '2028-01-01', rates: [0.10, 0.08], reform: false, exempt: () => 160000 });
  // год 1: 1 000 000; год 2: 11 000 000 × 8% = 880 000
  ok('вклад 2 года: переоформление с процентами по новой ставке', near(r2.gross, 1000000 + 880000, 1), r2.gross);
  ok('вклад 2 года: налог 13% сверх 160 тыс. в каждом году', near(r2.tax, 0.13 * (1000000 - 160000) + 0.13 * (880000 - 160000), 1), r2.tax);
  const r3 = B.depositNet(12e6, { settle: '2026-01-01', horizon: '2027-01-01', rates: [0.12], payout: 'monthly', reform: false, exempt: () => 0 });
  ok('вклад, проценты ежемесячно: 12% простых', near(r3.gross, 1440000, 500), r3.gross);
  const r4 = B.depositNet(1e6, { settle: '2026-01-01', horizon: '2026-07-02', rates: [0.10], reform: false, exempt: () => 0 });
  ok('вклад на полгода: простые проценты за 182 дня', near(r4.gross, 1e6 * 0.10 * 182 / 365, 0.5), r4.gross);
  // крупный вклад, 2027 по реформе: 50 млн × 14% = 7 млн дохода в 2027 → шкала
  const r5 = B.depositNet(50e6, { settle: '2026-01-01', horizon: '2028-01-01', rates: [0.14, 0.14], reform: true, exempt: () => 0 });
  // проценты выплачиваются в годовщину вклада: 01.01.2027 и 01.01.2028 — оба года уже по шкале
  ok('вклад по реформе: налог по шкале в год выплаты процентов', near(r5.tax, B.taxScale(7e6) + B.taxScale(57e6 * 0.14), 2), r5.tax);
  const r6 = B.depositNet(50e6, { settle: '2026-01-01', horizon: '2027-01-01', rates: [0.14], reform: false, exempt: () => 0 });
  ok('без реформы: 13% до 2,4 млн и 15% сверху', near(r6.tax, 2.4e6 * 0.13 + 4.6e6 * 0.15, 1), r6.tax);
  // путь ставок по кривой: плоская кривая → ставка не меняется; падающая → снижается
  const flat = B.depositPath(0.13, [[1, 14], [2, 14], [3, 14]], 3);
  ok('плоская кривая — ставка вклада постоянна', flat.every(v => near(v, 0.13, 1e-9)), flat);
  const down = B.depositPath(0.13, [[1, 14], [2, 13], [3, 12.5]], 3);
  ok('падающая кривая — ставка вклада снижается', down[1] < 0.13 && down[2] < down[1], down);
  // вывод купонов: короткая бумага в длинном сроке — погашение реинвестируется, купоны на руки, годовая простая
  {
    const fl = []; for (let k = 1; k <= 4; k++) fl.push({ ms: Date.UTC(2026, 3 * k, 1), amt: 40, kind: 'c', date: '' });
    fl.push({ ms: Date.UTC(2027, 0, 1), amt: 1000, kind: 'a', date: '' });
    const ps = [{ id: 'w', qty: 1000, bond: { face: 1000 }, flows: fl, clean: 100, accrued: 0, fx: 1, ytm: 0.16 }];
    const w = B.simulate(ps, { settle: '2026-01-01', horizon: '2029-01-01', account: 'none', reinvest: 0.16, withdraw: true });
    ok('вывод: купоны на руки = 160 тыс.', near(w.paidOut, 160000, 1e-6), w.paidOut);
    ok('вывод: погашение реинвестировано (1 млн × 1,16² за 2 года)', near(w.final - w.paidOut, 1e6 * Math.pow(1.16, 731 / 365), 1), w.final - w.paidOut);
    ok('вывод: годовая простая', near(w.annual, (w.final / 1e6 - 1) / w.years, 1e-12), w.annual);
  }
  // ИИС-3: освобождение до 30 млн ₽ дохода, с превышения — налог по шкале в год закрытия
  {
    const fl = [{ ms: Date.UTC(2031, 0, 10), amt: 2000, kind: 'a', date: '2031-01-10' }];
    const ps = [{ id: 'z', qty: 50000, bond: { face: 1000 }, flows: fl, clean: 100, accrued: 0, fx: 1, ytm: 0.149 }];
    const r = B.simulate(ps, { settle: '2026-01-10', horizon: '2031-01-10', account: 'iis3', reinvest: 0, reform: true });
    ok('ИИС-3: налог только с дохода сверх 30 млн', near(r.iisTax, B.taxScale(50e6 - 30e6), 1e-6), r.iisTax);
    const r2 = B.simulate([{ ...ps[0], qty: 10000 }], { settle: '2026-01-10', horizon: '2031-01-10', account: 'iis3', reinvest: 0, reform: true });
    ok('ИИС-3: до 30 млн — без налога', r2.iisTax === 0 && r2.taxPaid === 0, r2.taxPaid);
  }
  const up = B.depositPath(0.13, [[1, 14], [2, 15], [3, 16]], 3);
  ok('растущая кривая — ставка вклада не выше текущей', up.every(v => v <= 0.13 + 1e-12), up);
  // доходность ОФЗ по номиналу из бескупонной кривой: плоская кривая → та же ставка; растущая → чуть ниже бескупонной
  ok('parYield: плоская кривая', near(B.parYield([[1, 14], [5, 14]], 3, 2), 0.14, 1e-12));
  ok('parYield: срок короче купона = бескупонная', near(B.parYield([[0.25, 12], [1, 14]], 0.25, 2), 0.12, 1e-12));
  const pz = B.parYield([[1, 14], [3, 16]], 3, 2);
  ok('parYield: растущая кривая — ниже бескупонной на конце', pz < 0.16 && pz > 0.15, pz);
  // ОФЗ на срок: без налога и реинвест по y — годовая = y
  const o = B.parBondNet(1e7, 0.14, 2, '2026-01-01', '2029-01-01', 'none', true, false);
  ok('ОФЗ на срок без налога = доходности кривой', near(o.annual, 0.14, 1e-6), o.annual);
  // ЛДВ: дисконтная бумага (цена 70) гасится через 4 года — рост цены без налога; через 2 года — с налогом
  const zc = (end, n) => [{ id: 'z', qty: n, bond: { face: 1000 }, flows: [{ ms: B.toMs(end), amt: 1000, kind: 'a', date: end }], clean: 70, accrued: 0, fx: 1, ytm: 0.1 }];
  const l4 = B.simulate(zc('2030-01-02', 1000), { settle: '2026-01-01', account: 'std', reinvest: 0, ldv: true });
  ok('ЛДВ: держим больше 3 лет — налога нет', l4.taxPaid === 0 && near(l4.ldvFree, 300000, 1e-6), l4.taxPaid);
  const n4 = B.simulate(zc('2030-01-02', 1000), { settle: '2026-01-01', account: 'std', reinvest: 0, ldv: false });
  ok('без ЛДВ — 13% с роста цены', near(n4.taxPaid, 39000, 1e-6), n4.taxPaid);
  const l2 = B.simulate(zc('2028-01-02', 1000), { settle: '2026-01-01', account: 'std', reinvest: 0, ldv: true });
  ok('ЛДВ: меньше 3 лет — налог есть', near(l2.taxPaid, 39000, 1e-6), l2.taxPaid);
  const big = B.simulate(zc('2030-01-02', 50000), { settle: '2026-01-01', account: 'std', reinvest: 0, ldv: true });
  ok('ЛДВ: лимит 3 млн за год владения (4 года → 12 млн)', near(big.ldvFree, 12e6, 1e-6), big.ldvFree);
}
console.log(fails ? `\n${fails} FAILED` : '\nALL OK');
process.exit(fails ? 1 : 0);
