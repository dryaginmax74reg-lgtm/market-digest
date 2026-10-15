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
    if (!['fixed', 'disc'].includes(r.kind) || !r.end || r.ytm == null || !r.price) return (r._at = null);
    const per = r.couponPeriod > 0 ? r.couponPeriod : 182;
    const cpn = r.kind === 'disc' ? 0 : (r.cpn != null ? r.cpn : (r.cpnVal && per ? r.cpnVal / r.face * 365 / per * 100 : null));
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

  return { computeFair, afterTax, rScore, wmedian };
}));
