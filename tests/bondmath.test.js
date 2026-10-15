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
console.log(fails ? `\n${fails} FAILED` : '\nALL OK');
process.exit(fails ? 1 : 0);
