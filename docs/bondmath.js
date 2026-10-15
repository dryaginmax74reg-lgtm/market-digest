/* bondmath.js — математика облигаций для конструктора портфеля.
 * Работает и в браузере (window.BondMath), и в node (module.exports).
 *
 * Конвенции (как у Мосбиржи):
 *  - доходность эффективная годовая, база ACT/365, дисконтирование от даты расчётов (T+1);
 *  - цена «грязная» = чистая цена в % × текущий номинал / 100 + НКД;
 *  - в поток попадают купоны и погашения с датой строго позже даты расчётов;
 *  - если есть put-оферта — считается доходность к оферте (весь остаток номинала гасится по цене оферты);
 *  - неизвестные будущие купоны (флоатеры, «фикс. неизвестный» после оферты) — по последней известной ставке, помечаются est.
 * Все суммы — в валюте номинала на ОДНУ облигацию.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.BondMath = factory();
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';
  const DAY = 86400000;

  // ---------- даты ----------
  function toMs(d) {
    if (typeof d === 'number') return d;
    if (d instanceof Date) return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate());
    const [y, m, dd] = String(d).slice(0, 10).split('-').map(Number);
    return Date.UTC(y, m - 1, dd);
  }
  const iso = ms => new Date(toMs(ms)).toISOString().slice(0, 10);
  const days = (a, b) => Math.round((toMs(b) - toMs(a)) / DAY);
  const yearOf = d => new Date(toMs(d)).getUTCFullYear();
  const validDate = s => !!s && /^\d{4}-\d{2}-\d{2}/.test(s) && !String(s).startsWith('0000');

  // ---------- денежный поток облигации ----------
  /**
   * bond = {
   *   face: текущий (непогашенный) номинал, initialFace, couponPct: текущая ставка купона %,
   *   matdate, offerdate (put/выкуп; null), offerPrice (% номинала, по умолчанию 100),
   *   coupons: [{date, start, value|null, pct|null}], amorts: [{date, value}]
   * }
   * to: 'auto' | 'mat' | 'offer'
   */
  function buildFlows(bond, settle, to = 'auto') {
    const s = toMs(settle);
    const mat = validDate(bond.matdate) ? toMs(bond.matdate) : null;
    const off = validDate(bond.offerdate) && toMs(bond.offerdate) > s ? toMs(bond.offerdate) : null;
    const useOffer = off && (to === 'offer' || (to === 'auto')) && (!mat || off < mat);
    const end = useOffer ? off : mat;
    if (!end) return { flows: [], end: null, toOffer: false, est: true, error: 'нет даты погашения' };

    const amorts = (bond.amorts || []).filter(a => validDate(a.date) && a.value != null)
      .map(a => ({ ms: toMs(a.date), value: +a.value })).sort((a, b) => a.ms - b.ms);
    const initFace = +(bond.initialFace || bond.face);
    // номинал, непогашенный на дату ms (после погашений в даты <= ms)
    const faceAfter = ms => initFace - amorts.filter(a => a.ms <= ms).reduce((x, a) => x + a.value, 0);

    let lastPct = null, est = false;
    const cps = (bond.coupons || []).filter(c => validDate(c.date))
      .map(c => ({ ms: toMs(c.date), start: validDate(c.start) ? toMs(c.start) : null, value: c.value, pct: c.pct }))
      .sort((a, b) => a.ms - b.ms);
    const flows = [];
    let missing = false;
    for (const c of cps) {
      if (c.value != null) {
        // ставка известного купона: из данных или из суммы (у флоатеров биржа часто даёт только сумму)
        if (c.pct != null && +c.pct > 0) lastPct = +c.pct;
        else if (c.start != null && days(c.start, c.ms) > 0) {
          const f0 = faceAfter(c.start);
          if (f0 > 0) lastPct = +c.value / f0 * 365 / days(c.start, c.ms) * 100;
        }
      }
      if (c.ms <= s || c.ms > end) continue;
      let v = c.value;
      if (v == null) {
        const pct = c.pct != null ? +c.pct : (lastPct != null ? lastPct : (bond.couponPct != null ? +bond.couponPct : NaN));
        const st = c.start != null ? c.start : null;
        if (st == null || !isFinite(pct)) { missing = true; continue; }
        const faceAtStart = faceAfter(st);
        v = Math.round(pct / 100 * faceAtStart * days(st, c.ms) / 365 * 100) / 100;
        est = true;
        c.pct = pct;
      }
      if (!(+v > 0)) continue;                         // нулевые купоны после полного погашения номинала
      flows.push({ ms: c.ms, amt: +v, kind: 'c', est: c.value == null, pct: c.value == null ? null : c.pct });
    }
    let redeemed = 0;
    for (const a of amorts) {
      if (a.ms <= s || a.ms > end) continue;
      if (useOffer && a.ms === end) continue;          // в дату оферты гасим весь остаток ниже
      if (!(a.value > 0)) continue;
      flows.push({ ms: a.ms, amt: a.value, kind: 'a' });
      redeemed += a.value;
    }
    if (useOffer) {
      const rest = faceAfter(end - 1);                 // остаток номинала перед датой оферты
      flows.push({ ms: end, amt: rest * (bond.offerPrice || 100) / 100, kind: 'o' });
    } else {
      // контроль: будущие погашения должны покрывать текущий номинал
      const need = +bond.face;
      if (isFinite(need) && need > 0 && Math.abs(redeemed - need) > 0.01) {
        if (redeemed < need) flows.push({ ms: end, amt: need - redeemed, kind: 'a', fix: true });
      }
    }
    flows.sort((a, b) => a.ms - b.ms);
    for (const f of flows) { f.date = iso(f.ms); f.t = days(s, f.ms) / 365; }
    return { flows, end: iso(end), toOffer: !!useOffer, est, missing };
  }

  // ---------- доходность, дюрация, выпуклость ----------
  function pv(flows, y) { let p = 0; for (const f of flows) p += f.amt * Math.pow(1 + y, -f.t); return p; }
  function dpv(flows, y) { let d = 0; for (const f of flows) d -= f.t * f.amt * Math.pow(1 + y, -f.t - 1); return d; }

  /** эффективная доходность: Σ CF·(1+y)^-t = P. Ньютон с защитой бисекцией. */
  function irr(flows, price) {
    if (!flows.length || !(price > 0)) return null;
    let lo = -0.99, hi = 10;
    if ((pv(flows, lo) - price) * (pv(flows, hi) - price) > 0) return null;
    let y = 0.15;
    for (let i = 0; i < 200; i++) {
      const f = pv(flows, y) - price;
      if (Math.abs(f) < 1e-10 * Math.max(1, price)) return y;
      if (f > 0) lo = y; else hi = y;                    // pv убывает по y
      const d = dpv(flows, y);
      let yn = d ? y - f / d : NaN;
      if (!(yn > lo && yn < hi)) yn = (lo + hi) / 2;
      if (Math.abs(yn - y) < 1e-13) return yn;
      y = yn;
    }
    return y;
  }

  function risk(flows, y) {
    const P = pv(flows, y);
    let d = 0, c = 0;
    for (const f of flows) {
      const v = f.amt * Math.pow(1 + y, -f.t);
      d += f.t * v;
      c += f.t * (f.t + 1) * f.amt * Math.pow(1 + y, -f.t - 2);
    }
    const mac = d / P;
    return { price: P, macaulay: mac, modified: mac / (1 + y), convexity: c / P, dv01: P * mac / (1 + y) / 10000 };
  }

  /** НКД на дату расчётов по текущему купонному периоду (для сверки с биржей). */
  function accrued(bond, settle) {
    const s = toMs(settle);
    const c = (bond.coupons || []).filter(x => validDate(x.date) && validDate(x.start))
      .find(x => toMs(x.start) <= s && toMs(x.date) > s);
    if (!c) return 0;
    let v = c.value;
    if (v == null) {
      const known = (bond.coupons || []).filter(x => x.value != null && validDate(x.start) && toMs(x.date) <= s).slice(-1)[0];
      const pct = c.pct ?? bond.couponPct ?? (known ? known.value / bond.face * 365 / days(known.start, known.date) * 100 : null);
      if (pct == null) return null;
      v = pct / 100 * bond.face * days(c.start, c.date) / 365;
    }
    return Math.round(v * days(c.start, s) / days(c.start, c.date) * 100) / 100;
  }

  /** полный разбор бумаги: цена (чистая, %) → доходность, дюрация и пр. */
  function analyze(bond, settle, cleanPct, accruedInt, to = 'auto') {
    const fl = buildFlows(bond, settle, to);
    if (!fl.flows.length || fl.missing) return { ...fl, ytm: null };
    const ai = accruedInt != null ? +accruedInt : accrued(bond, settle);
    if (ai == null) return { ...fl, ytm: null };
    const dirty = cleanPct / 100 * bond.face + ai;
    const y = irr(fl.flows, dirty);
    if (y == null) return { ...fl, dirty, accrued: ai, ytm: null };
    const r = risk(fl.flows, y);
    return { ...fl, dirty, accrued: ai, ytm: y, ...r, durDays: Math.round(r.macaulay * 365) };
  }

  /** цена после мгновенного сдвига доходности (точная переоценка) и приближение ModD+выпуклость */
  function shock(flows, y, dy) {
    const p0 = pv(flows, y), p1 = pv(flows, y + dy);
    const r = risk(flows, y);
    return { exact: p1 / p0 - 1, approx: -r.modified * dy + 0.5 * r.convexity * dy * dy };
  }

  // ---------- налоги ----------
  const REFORM_YEAR = 2027;
  function tax1315(b) { b = Math.max(0, b); return Math.min(b, 2.4e6) * 0.13 + Math.max(0, b - 2.4e6) * 0.15; }
  const SCALE = [[2.4e6, .13], [5e6, .15], [20e6, .18], [50e6, .20], [Infinity, .22]];
  function taxScale(b) {
    b = Math.max(0, b); let t = 0, prev = 0;
    for (const [lim, r] of SCALE) { if (b <= prev) break; t += (Math.min(b, lim) - prev) * r; prev = lim; }
    return t;
  }
  function yearTax(year, base, reform) { return (reform && year >= REFORM_YEAR) ? taxScale(base) : tax1315(base); }

  // ---------- симуляция портфеля до горизонта (после налогов, с реинвестированием) ----------
  /**
   * positions: [{ id, qty, bond, flows (из buildFlows/analyze), clean (% покупки), accrued (НКД на бумагу при покупке),
   *              fx (курс валюты номинала к рублю, 1 для рублёвых), ytm, floater }]
   * o: { settle, horizon (iso; по умолчанию — последний поток), account: 'std'|'iis3'|'none',
   *      reinvest (эфф. годовая ставка реинвестирования до налога, доля), reform (bool), shift (сдвиг ставок, доля),
   *      fee (комиссия брокера, доля от суммы сделки) }
   * Логика налога (account='std'):
   *  - купон: брокер удерживает 13% сразу; НКД, уплаченный при покупке, уменьшает базу ближайших купонов;
   *  - погашение/амортизация/продажа: доход = выручка − цена покупки части номинала (−комиссии), удерживается 13%;
   *  - доход от реинвестирования — облагается так же (13% сразу);
   *  - 31 декабря: налог за год пересчитывается по шкале (2026: 13/15%; с 2027 при reform — 13…22%),
   *    разница с удержанным доплачивается (или возвращается при убытках);
   *  - другие доходы клиента НЕ учитываются.
   *  account='iis3': налог не удерживается, весь купон реинвестируется (льгота ИИС-3 при соблюдении срока).
   *  Все денежные величины переводятся в рубли по fx (курс считается постоянным).
   * Плечо: позиции с lev:true куплены на заёмные деньги (o.loanRate — эфф. годовая ставка займа, o.loanShift — её сдвиг).
   *  Долг = стоимость lev-позиций, растёт по ставке займа (проценты капитализируются); купоны и погашения заёмных бумаг
   *  сначала гасят долг, остаток долга гасится на горизонте; прочие поступления реинвестируются как обычно; проценты по займу базу НДФЛ не уменьшают (консервативно).
   *  invested/annual — на собственные деньги клиента.
   */
  function simulate(positions, o) {
    const s = toMs(o.settle);
    const shift = o.shift || 0, fee = o.fee || 0;
    const tax = o.account === 'std';
    const r = Math.max(-0.5, (o.reinvest || 0) + shift);
    const rl = Math.max(-0.5, (o.loanRate || 0) + (o.loanShift || 0));
    let lastMs = s;
    for (const p of positions) for (const f of p.flows) lastMs = Math.max(lastMs, f.ms);
    const H = o.horizon ? toMs(o.horizon) : lastMs;

    const ev = [];
    let invested = 0, borrowed = 0;
    const st = positions.map(p => ({
      p, faceLeft: +p.bond.face, basisPerFace: (p.clean / 100) * (1 + fee),
      nkdLeft: tax ? p.accrued * p.qty * p.fx : 0,
    }));
    for (const x of st) {
      const cost = (x.p.clean / 100 * x.p.bond.face * (1 + fee) + x.p.accrued) * x.p.qty * x.p.fx;
      if (x.p.lev) borrowed += cost; else invested += cost;
      for (const f0 of x.p.flows) {
        if (f0.ms > H) continue;
        let f = f0;
        // флоатер: неизвестные будущие купоны двигаются вместе со ставкой
        if (x.p.floater && shift && f0.kind === 'c' && f0.est && f0.pct > 0)
          f = { ...f0, amt: f0.amt * Math.max(0, f0.pct + shift * 100) / f0.pct };
        ev.push({ ms: f.ms, x, f });
      }
    }
    ev.sort((a, b) => a.ms - b.ms);

    let cash = 0, t = s, debt = borrowed, repaid = 0;
    const years = {};          // year -> {base, withheld}
    const Y = y => (years[y] = years[y] || { base: 0, withheld: 0 });
    let gross = { coupons: 0, redemptions: 0, reinvest: 0, loanInterest: 0 }, taxPaid = 0;
    const cal = [];            // денежный календарь: {date, coupon, principal} (в рублях, до налога, на весь портфель)

    function grow(toMs_) {
      if (toMs_ <= t) return;
      // проходим через 31.12, чтобы налог за год считался в своём году
      while (true) {
        const y = new Date(t).getUTCFullYear();
        const ye = Date.UTC(y + 1, 0, 1);          // 1 января следующего года
        const stop = Math.min(toMs_, ye);
        if (stop > t && cash > 0) {
          const g = cash * (Math.pow(1 + r, (stop - t) / DAY / 365) - 1);
          gross.reinvest += g;
          cash += g;
          if (tax) { const w = 0.13 * g; cash -= w; Y(y).base += g; Y(y).withheld += w; }
        }
        if (stop > t && debt > 0) debt *= Math.pow(1 + rl, (stop - t) / DAY / 365);   // проценты по займу
        t = stop;
        if (stop === ye && toMs_ > ye) { settleYear(y); continue; }  // итог года, идём дальше
        break;
      }
    }
    function settleYear(y) {
      if (!tax || !years[y]) return;
      const due = yearTax(y, years[y].base, o.reform);
      const delta = due - years[y].withheld;  // >0 доплата, <0 возврат
      cash -= delta; taxPaid += due; years[y].due = due;
    }

    for (const e of ev) {
      grow(e.ms);
      const x = e.x, q = x.p.qty, fx = x.p.fx;
      const amt = e.f.amt * q * fx;
      const y = yearOf(e.ms);
      if (e.f.kind === 'c') {
        gross.coupons += amt; cash += amt;
        cal.push({ date: e.f.date, id: x.p.id, coupon: amt, principal: 0 });
        let wh = 0;
        if (tax) {
          const off = Math.min(x.nkdLeft, amt); x.nkdLeft -= off;
          const b = amt - off; wh = 0.13 * b; Y(y).base += b; Y(y).withheld += wh; cash -= wh;
        }
        // купон заёмной бумаги (после удержания НДФЛ) сначала гасит займ — не реинвестируется по доходности портфеля
        if (x.p.lev && debt > 0) { const rep = Math.min(debt, Math.max(0, amt - wh)); debt -= rep; cash -= rep; repaid += rep; }
      } else {
        // погашение части номинала (или всего остатка по оферте)
        const faceRedeemed = e.f.kind === 'o' ? x.faceLeft : e.f.amt;
        const basis = faceRedeemed * x.basisPerFace * q * fx;
        x.faceLeft = Math.max(0, x.faceLeft - faceRedeemed);
        gross.redemptions += amt; cash += amt;
        cal.push({ date: e.f.date, id: x.p.id, coupon: 0, principal: amt });
        if (x.p.lev && debt > 0) { const rep = Math.min(debt, amt); debt -= rep; cash -= rep; repaid += rep; }   // погашение заёмной бумаги гасит займ
        if (tax) {
          let g = amt - basis;
          // неиспользованный НКД покупки при полном погашении — расход
          if (x.faceLeft <= 1e-9 && x.nkdLeft > 0) { g -= x.nkdLeft; x.nkdLeft = 0; }
          Y(y).base += g;
          if (g > 0) { Y(y).withheld += 0.13 * g; cash -= 0.13 * g; }
        }
      }
    }
    grow(H);

    // бумаги, не погашенные к горизонту: продаём по цене при доходности ytm+shift
    let mtm = 0;
    for (const x of st) {
      if (x.faceLeft <= 1e-9) continue;
      const rest = x.p.flows.filter(f => f.ms > H).map(f => ({ ...f, t: days(H, f.ms) / 365 }));
      if (!rest.length) continue;
      let yv = (x.p.ytm || 0) + (x.p.floater ? 0 : shift);
      let dirty = pv(rest, yv);
      // НКД в цене продажи: часть «грязной» цены — облагается как доход (как и у брокера)
      const proceeds = dirty * x.p.qty * x.p.fx * (1 - fee);
      const basis = x.faceLeft * x.basisPerFace * x.p.qty * x.p.fx;
      mtm += proceeds;
      cash += proceeds;
      if (tax) {
        let g = proceeds - basis - x.nkdLeft; x.nkdLeft = 0;
        const y = yearOf(H); Y(y).base += g; if (g > 0) { Y(y).withheld += 0.13 * g; cash -= 0.13 * g; }
      }
    }
    gross.loanInterest = debt + repaid - borrowed;
    cash -= debt;                                       // возврат займа с процентами
    // финальный пересчёт по шкале за год горизонта (и все незакрытые годы)
    for (const y of Object.keys(years).map(Number)) if (years[y].due == null) settleYear(y);

    const T = Math.max(1, days(s, H)) / 365;
    const ret = cash / invested;
    return {
      invested, final: cash, profit: cash - invested, years: T,
      annual: Math.pow(ret, 1 / T) - 1,     // эффективная годовая после налогов
      gross, taxPaid, taxByYear: years, mtm, horizon: iso(H), calendar: cal, borrowed, debt,
    };
  }

  /** доходность портфеля к погашению (IRR объединённого потока, в рублях, без налогов) */
  function portfolioYield(positions, settle) {
    const s = toMs(settle);
    const all = []; let P = 0;
    for (const p of positions) {
      P += (p.clean / 100 * p.bond.face + p.accrued) * p.qty * p.fx;
      for (const f of p.flows) all.push({ ms: f.ms, t: days(s, f.ms) / 365, amt: f.amt * p.qty * p.fx });
    }
    const y = irr(all, P);
    if (y == null) return null;
    return { ytm: y, price: P, ...risk(all, y) };
  }

  return { toMs, iso, days, buildFlows, pv, irr, risk, accrued, analyze, shock, simulate, portfolioYield,
           tax1315, taxScale, yearTax, REFORM_YEAR };
}));
