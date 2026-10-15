/* bondscore.js — оценка облигации относительно «соседей»: справедливая доходность, премия, метка риска, итоговый балл.
 *
 * Справедливая доходность бумаги = взвешенная медиана доходностей бумаг той же валюты, того же типа купона
 * (фикс./флоатер), того же сегмента (ОФЗ / обычные / субординированные / секьюритизация) и того же рейтинга (если таких мало — ±1…3 ступени), с весом по близости дюрации
 * (ядро Гаусса, ширина 35% от дюрации, не меньше полугода) и по обороту. Медиана — чтобы единичные выбросы не сдвигали норму.
 * В «соседи» не берутся бумаги с ценой ниже 85% и доходностью выше 60% — это уже проблемные выпуски.
 *
 * Премия = доходность − справедливая. Метка «риск»: премия больше max(3 п.п., 3 × типичный разброс соседей)
 * или цена ниже 60% номинала — рынок требует за бумагу заметно больше, чем за такой же рейтинг, т.е. видит риск,
 * которого рейтинг не отражает.
 * Балл = премия (п.п.) + поправка за ликвидность (−0,4…+0,3 п.п.); у бумаг с меткой риска балла нет.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.BondScore = factory();
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';
  const RAT = ['AAA','AA+','AA','AA-','A+','A','A-','BBB+','BBB','BBB-','BB+','BB','BB-','B+','B','B-','CCC','CC','C','D'];
  const rScore = r => r === 'ГОС' ? 0 : (RAT.indexOf(r) + 1 || 99);
  const MIN_PEERS = 8, MIN_WEIGHT = 2.5;

  function wmedian(vals, ws) {
    const idx = vals.map((v, i) => i).sort((a, b) => vals[a] - vals[b]);
    const tot = ws.reduce((a, b) => a + b, 0); let acc = 0;
    for (const i of idx) { acc += ws[i]; if (acc >= tot / 2) return vals[i]; }
    return vals[idx[idx.length - 1]];
  }
  const liqW = tv => 0.25 + Math.min(1, Math.log10(1 + (tv || 0) / 1e6) / 2);   // 0,25 … 1,25

  /** rows: [{id, cur, kind, ofz, rating, ytm, dur, price, tv, mbs, ok}] — ok=false для бумаг, которые не оцениваем */
  function computeFair(rows) {
    // сегменты сравниваются только между собой: ОФЗ, обычные корп., субординированные, секьюритизация (СФО)
    const gk = r => r.cur + '|' + (r.kind === 'float' ? 'F' : 'X') + '|' + (r.ofz ? 'G' : r.mbs ? 'S' : r.sub ? 'U' : 'C');
    const ref = rows.filter(r => r.ok && r.ytm != null && r.dur > 0 && r.price >= 85 && r.ytm < 0.6 && rScore(r.rating) < 99);
    const groups = {};
    for (const r of ref) (groups[gk(r)] = groups[gk(r)] || []).push(r);
    for (const r of rows) {
      r.fair = r.prem = r.mad = r.score = null; r.flag = null; r.peers = 0;
      if (!r.ok || r.ytm == null || !(r.dur > 0) || rScore(r.rating) >= 99) continue;
      const g = groups[gk(r)] || [], rs = rScore(r.rating);
      let peers = [], width = 0;
      for (width = 0; width <= 3; width++) {
        peers = g.filter(p => p !== r && Math.abs(rScore(p.rating) - rs) <= width);
        if (peers.length >= MIN_PEERS) break;
      }
      if (!peers.length) continue;
      const h = Math.max(0.5, 0.35 * r.dur);
      const ws = peers.map(p => Math.exp(-0.5 * Math.pow((p.dur - r.dur) / h, 2)) * liqW(p.tv));
      const wsum = ws.reduce((a, b) => a + b, 0);
      if (wsum < MIN_WEIGHT * 0.25) continue;                       // рядом по сроку почти никого
      const vals = peers.map(p => p.ytm);
      const fair = wmedian(vals, ws);
      const mad = wmedian(vals.map(v => Math.abs(v - fair)), ws);
      r.fair = fair; r.prem = r.ytm - fair; r.mad = mad; r.peers = peers.length; r.width = width;
      // низкая цена сама по себе не риск (низкий купон), риск — когда рынок требует заметно больше соседей
      if (!r.ofz && (r.prem > Math.max(0.03, 3 * mad) || r.price < 60)) r.flag = 'risk';
      const liq = Math.max(-0.4, Math.min(0.3, 0.25 * Math.log10(Math.max(r.tv || 0, 1e5) / 2e7)));
      r.score = r.flag ? null : r.prem * 100 + (r.ofz ? 0.3 : liq);
    }
    return rows;
  }

  /** Доходность после НДФЛ 13% (брокер удерживает с купонов, с дисконта — при погашении; купоны реинвестируются
   *  по той же доходности). Только для фикс. купона без амортизации — по текущей ставке купона. */
  function afterTax(r, BM, settle) {
    if (r._at !== undefined) return r._at;
    if (!['fixed', 'disc', 'amort', 'float'].includes(r.kind) || !r.end || r.ytm == null || !r.price) return (r._at = null);
    const per = r.couponPeriod > 0 ? r.couponPeriod : 182;
    // амортизация и флоатер — приближённо: как бумага с тем же купоном (у флоатера — текущим или оценкой) без амортизации
    let cpn = r.kind === 'disc' ? 0 : (r.cpn != null ? r.cpn : (r.cpnVal && per ? r.cpnVal / r.face * 365 / per * 100 : null));
    if (cpn == null && r.kind === 'float') cpn = r.ytm * 100 * r.price / 100;
    if (cpn == null) return (r._at = null);
    const s = BM.toMs(settle), end = BM.toMs(r.end), D = 864e5;
    const flows = [];
    for (let t = end; t > s; t -= per * D) flows.push({ ms: t, amt: cpn / 100 * 100 * per / 365, kind: 'c', date: BM.iso(t) });
    if (cpn === 0) flows.length = 0;
    flows.push({ ms: end, amt: 100, kind: 'a', date: BM.iso(end) });
    flows.sort((a, b) => a.ms - b.ms);
    for (const f of flows) f.t = (f.ms - s) / D / 365;
    const ai = r.ai != null && r.face ? r.ai / r.face * 100 : 0;
    const y = BM.irr(flows, r.price + ai);
    if (y == null) return (r._at = null);
    const res = BM.simulate([{ id: r.id, qty: 1, bond: { face: 100 }, flows, clean: r.price, accrued: ai, fx: 1, ytm: y }],
      { settle, account: 'std', reinvest: y, reform: false });
    // налоговая «потеря» на модельном потоке, перенесённая на биржевую доходность бумаги
    return (r._at = r.ytm - (y - res.annual));
  }

  /** Сводная оценка 0–100 — «по сумме показателей» среди переданных бумаг (обычно — после фильтров):
   *  доходность 35% + премия к норме 25% + надёжность (рейтинг) 25% + ликвидность 15%.
   *  Доходность, премия и ликвидность — перцентиль внутри списка (0 — худшая, 1 — лучшая); надёжность: ГОС и AAA = 1, дальше выпукло ((20 − ступень)/19)² — AA 0,8 · A 0,54 · BBB 0,33; без рейтинга 0,05. Доходность сравнивается внутри типа купона.
   *  Бумаги с меткой «риск» получают 0. Результат: r.cs и r.csParts. */
  function composite(rows) {
    const live = rows.filter(r => r.ytm != null && !r.flag);
    const ranker = key => {
      const v = live.map(key).filter(x => x != null && isFinite(x)).sort((a, b) => a - b), n = v.length;
      return x => {
        if (x == null || !isFinite(x) || n < 2) return n < 2 ? 0.5 : 0.3;
        let lo = 0, hi = n; while (lo < hi) { const m = (lo + hi) >> 1; if (v[m] < x) lo = m + 1; else hi = m; }
        let lo2 = lo, hi2 = n; while (lo2 < hi2) { const m = (lo2 + hi2) >> 1; if (v[m] <= x) lo2 = m + 1; else hi2 = m; }
        return ((lo + lo2 - 1) / 2) / (n - 1);
      };
    };
    // доходность сравниваем внутри своего типа купона: у флоатера она — оценка при неизменной ставке, с фикс. купоном не сопоставима
    const isF = r => r.kind === 'float';
    const rYf = ranker(r => isF(r) ? r.ytm : null), rYx = ranker(r => isF(r) ? null : r.ytm);
    const rY = (x, r) => (isF(r) ? rYf : rYx)(x);
    const rP = ranker(r => r.prem), rL = ranker(r => Math.log10(1 + (r.tv || 0)));
    // надёжность: выпуклая шкала — каждая ступень вниз от AAA стоит всё дороже (AAA 1 · AA 0,80 · A 0,54 · A− 0,47 · BBB 0,33 · BBB− 0,28)
    const quality = r => r.rating === 'ГОС' ? 1 : (rScore(r.rating) >= 99 ? 0.05 : Math.pow(Math.max(0, (20 - rScore(r.rating)) / 19), 2));
    for (const r of rows) {
      if (r.ytm == null || r.flag) { r.cs = 0; r.csParts = null; continue; }
      const p = { y: rY(r.ytm, r), p: rP(r.prem), q: quality(r), l: rL(Math.log10(1 + (r.tv || 0))) };
      r.csParts = p;
      r.cs = Math.round(100 * (0.35 * p.y + 0.25 * p.p + 0.25 * p.q + 0.15 * p.l));
    }
    return rows;
  }

  return { computeFair, afterTax, composite, rScore, wmedian };
}));
