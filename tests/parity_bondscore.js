// Сверка JS (docs/bondscore.js) и Python (bondscore.py) на одних и тех же случайных данных.
const S = require('../docs/bondscore.js'); const fs = require('fs');
let seed = 7; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
const R = ['ГОС','AAA','AA+','AA','AA-','A+','A','A-','BBB+','BBB','BB','B',''];
const rows = [];
for (let i = 0; i < 600; i++) {
  const rating = R[Math.floor(rnd() * R.length)];
  rows.push({ id: 'b' + i, cur: rnd() < 0.85 ? 'RUB' : 'CNY', kind: rnd() < 0.2 ? 'float' : 'fixed', ofz: rating === 'ГОС',
    mbs: rnd() < 0.05, sub: rnd() < 0.05, rating, ytm: 0.08 + rnd() * 0.25 + (rnd() < 0.03 ? 0.5 : 0), dur: 0.1 + rnd() * 5,
    price: 50 + rnd() * 55, tv: rnd() < 0.2 ? 0 : rnd() * 3e8, ok: rnd() < 0.97 });
}
fs.writeFileSync(process.argv[2], JSON.stringify(rows));
S.computeFair(rows);
fs.writeFileSync(process.argv[3], JSON.stringify(rows.map(r => [r.id, r.fair, r.prem, r.flag, r.score])));
