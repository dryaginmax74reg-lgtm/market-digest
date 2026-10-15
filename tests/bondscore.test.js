const S = require('../docs/bondscore.js'), B = require('../docs/bondmath.js');
let fails = 0; const ok = (n, c, i = '') => { console.log((c ? 'OK  ' : 'FAIL') + '  ' + n + (i !== '' ? '  ' + i : '')); if (!c) fails++; };
const near = (a, b, t) => Math.abs(a - b) <= t;
const mk = (id, rating, ytm, dur, extra = {}) => Object.assign({ id, cur: 'RUB', kind: 'fixed', ofz: false, rating, ytm, dur, price: 99, tv: 5e7, ok: true }, extra);
const rows = [];
for (let i = 0; i < 12; i++) rows.push(mk('p' + i, 'AA', 0.15 + (i % 3 - 1) * 0.002, 2 + (i % 4) * 0.1));
rows.push(mk('good', 'AA', 0.16, 2.1), mk('bad', 'AA', 0.20, 2.1), mk('cheap', 'AA', 0.15, 2.1, { price: 55 }));
for (let i = 0; i < 10; i++) rows.push(mk('bb' + i, 'BB', 0.25, 2));
rows.push(mk('sub', 'AA', 0.19, 2.1, { sub: true }));
S.computeFair(rows);
const g = id => rows.find(r => r.id === id);
ok('норма для AA ≈ 15%', near(g('good').fair, 0.15, 0.0021), g('good').fair);
ok('премия +1 п.п., без метки', near(g('good').prem, 0.01, 0.0021) && !g('good').flag);
ok('переплата +5 п.п. → метка «риск»', g('bad').flag === 'risk' && g('bad').score == null);
ok('цена ниже 60% → метка', g('cheap').flag === 'risk');
ok('BB-бумаги не тянут норму AA вверх', g('p0').fair < 0.16);
ok('субординированная сравнивается только с субординированными (их нет) → без оценки', g('sub').fair == null);
ok('сортировка по баллу: good выше p-соседей', g('good').score > Math.max(...rows.filter(r => /^p/.test(r.id)).map(r => r.score)));
// после НДФЛ: бумага по номиналу, купон 10% раз в год, 1 год → 8,7%
const settle = '2026-01-01';
const r1 = { id: 'x', kind: 'fixed', end: '2027-01-01', ytm: null, price: 100, cpn: 10, couponPeriod: 365, face: 1000, ai: 0 };
r1.ytm = 0.10;
const at = S.afterTax(r1, B, settle);
ok('после НДФЛ: купон 10% → 8,70%', near(at, 0.087, 2e-4), at);
// дисконтная: 100/90 за год → 11,11% до налога, налог 13% с 10 → 8,7/90 = 9,67%
const r2 = { id: 'y', kind: 'disc', end: '2027-01-01', ytm: 100 / 90 - 1, price: 90, face: 1000, ai: 0 };
const at2 = S.afterTax(r2, B, settle);
ok('дисконтная: после НДФЛ 9,67%', near(at2, 8.7 / 90, 3e-4), at2);
console.log(fails ? `\n${fails} FAILED` : '\nALL OK'); process.exit(fails ? 1 : 0);
