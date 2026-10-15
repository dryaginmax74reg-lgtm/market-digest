// Команды боту: Cloudflare Worker (бесплатно, без своего сервера).
// Переменные (Settings → Variables and Secrets):
//   BOT_TOKEN        — токен бота (Secret)
//   ALLOWED_CHAT_ID  — ваш chat_id: бот отвечает только вам
//   DASHBOARD_URL    — адрес дашборда, например https://login.github.io/repo/
//   SETUP_KEY        — любое длинное слово-пароль для настройки (Secret)
// Настройка: открыть в браузере https://<ваш-воркер>.workers.dev/setup?key=<SETUP_KEY>

const COMMANDS = [
  ["now", "Срез рынка сейчас"],
  ["rates", "Ставки: ключевая, вклады, RUONIA, RUSFAR CNY, ОФЗ"],
  ["fx", "Валюты: ЦБ и биржевой юань"],
  ["curve", "Кривая ОФЗ сейчас"],
  ["calc", "Расчёт: /calc 100млн 1г"],
  ["divs", "Ближайшие дивидендные отсечки"],
  ["bonds", "Подбор облигаций и портфель"],
  ["dash", "Открыть дашборд"],
  ["help", "Что умеет бот"],
];

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    if (url.pathname === "/setup") return setup(url, env);
    if (url.pathname === "/tg" && req.method === "POST") {
      if (req.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.SETUP_KEY) return new Response("forbidden", { status: 403 });
      const upd = await req.json();
      try { await handle(upd, env); } catch (e) { console.log("error", e && e.stack || e); }
      return new Response("ok");
    }
    return new Response("market-digest bot");
  },
};

// ---------------------------------------------------------------- настройка вебхука, меню и кнопки
async function setup(url, env) {
  if (url.searchParams.get("key") !== env.SETUP_KEY) return new Response("Неверный key", { status: 403 });
  const hook = `${url.origin}/tg`;
  const out = {};
  out.webhook = await tg(env, "setWebhook", { url: hook, secret_token: env.SETUP_KEY, allowed_updates: ["message"] });
  out.commands = await tg(env, "setMyCommands", { commands: COMMANDS.map(([command, description]) => ({ command, description })) });
  if (env.DASHBOARD_URL) {
    out.menu = await tg(env, "setChatMenuButton", { menu_button: { type: "web_app", text: "Дашборд", web_app: { url: env.DASHBOARD_URL } } });
  }
  return new Response(JSON.stringify(out, null, 2), { headers: { "content-type": "application/json; charset=utf-8" } });
}

async function tg(env, method, body) {
  const r = await fetch(`https://api.telegram.org/bot${env.BOT_TOKEN}/${method}`, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
  });
  return r.json();
}

async function reply(env, chatId, html, keyboard) {
  const body = { chat_id: chatId, text: html, parse_mode: "HTML", disable_web_page_preview: true };
  if (keyboard) body.reply_markup = keyboard;
  return tg(env, "sendMessage", body);
}

// ---------------------------------------------------------------- форматирование
const nf = (v, d = 2) => v == null || isNaN(v) ? "н/д" : (v + (v >= 0 ? 1e-9 : -1e-9)).toLocaleString("ru-RU", { minimumFractionDigits: d, maximumFractionDigits: d }).replace(/ /g, " ");
const sg = (v, d = 2, s = "%") => v == null || isNaN(v) ? "" : (v > 0 ? "+" : v < 0 ? "−" : "") + nf(Math.abs(v), d) + s;
const money = v => Math.round(v).toLocaleString("ru-RU").replace(/ /g, " ") + " ₽";
const esc = s => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
function table(rows) {
  const n = Math.max(...rows.map(r => r.length));
  rows = rows.map(r => r.concat(Array(n - r.length).fill("")));
  const w = [...Array(n).keys()].map(i => Math.max(...rows.map(r => String(r[i]).length)));
  return "<pre>" + esc(rows.map(r => r.map((c, i) => i === 0 ? String(c).padEnd(w[i]) : String(c).padStart(w[i])).join(" ").trimEnd()).join("\n")) + "</pre>";
}

// ---------------------------------------------------------------- данные
async function getData(env) {
  if (!env.DASHBOARD_URL) return null;
  const base = env.DASHBOARD_URL.endsWith("/") ? env.DASHBOARD_URL : env.DASHBOARD_URL + "/";
  const r = await fetch(base + "data.json?t=" + Date.now(), { cf: { cacheTtl: 300 } });
  return r.ok ? r.json() : null;
}
const last = (D, k) => { const s = D && D.series[k]; return s && s.data.length ? s.data[s.data.length - 1] : null; };
const prev = (D, k) => { const s = D && D.series[k]; return s && s.data.length > 1 ? s.data[s.data.length - 2] : null; };
function asOf(D, k, iso) { let v = null; for (const [d, x] of (D && D.series[k] ? D.series[k].data : [])) { if (d <= iso) v = x; else break; } return v; }

async function iss(path) {
  const r = await fetch("https://iss.moex.com/iss/" + path);
  return r.json();
}
async function moexIndex(secid) {
  const j = await iss(`engines/stock/markets/index/securities/${secid}.json?iss.meta=off&iss.only=marketdata&marketdata.columns=CURRENTVALUE,LASTCHANGEPRC,YEARCHANGEPRC,UPDATETIME`);
  const r = j.marketdata.data[0]; return r ? { val: r[0], chg: r[1], ytd: r[2], time: r[3] } : null;
}
async function moexCny() {
  const j = await iss("engines/currency/markets/selt/boards/CETS/securities/CNYRUB_TOM.json?iss.meta=off&iss.only=marketdata&marketdata.columns=LAST,LASTTOPREVPRICE,UPDATETIME");
  const r = j.marketdata.data[0]; return r && r[0] ? { val: r[0], chg: r[1], time: r[2] } : null;
}
async function zcyc() {
  const j = await iss("engines/stock/zcyc.json?iss.meta=off&iss.only=yearyields");
  const out = {}; let t = "";
  for (const [d, tm, p, v] of j.yearyields.data) { out[p] = v; t = `${d} ${tm}`; }
  return { points: out, time: t };
}
const safe = async (p) => { try { return await p; } catch (e) { return null; } };

// ---------------------------------------------------------------- команды
async function handle(upd, env) {
  const msg = upd.message; if (!msg || !msg.text) return;
  const chat = msg.chat.id;
  if (String(chat) !== String(env.ALLOWED_CHAT_ID)) { await reply(env, chat, "Это приватный бот."); return; }
  const [cmdRaw, ...args] = msg.text.trim().split(/\s+/);
  const cmd = cmdRaw.toLowerCase().replace(/@.*/, "");
  const D = await safe(getData(env));
  const dash = env.DASHBOARD_URL && (env.DASHBOARD_URL.endsWith("/") ? env.DASHBOARD_URL : env.DASHBOARD_URL + "/");
  const kb = (hash, text) => dash ? { inline_keyboard: [[{ text, web_app: { url: dash + hash } }]] } : undefined;
  switch (cmd) {
    case "/start": case "/help":
      return reply(env, chat, "<b>Команды</b>\n" + COMMANDS.map(([c, d]) => `/${c} — ${esc(d)}`).join("\n"), kb("", "📊 Дашборд"));
    case "/now": return reply(env, chat, await cmdNow(D), kb("", "📊 Дашборд"));
    case "/rates": return reply(env, chat, await cmdRates(D), kb("#RATES", "🏦 Ставки на дашборде"));
    case "/fx": return reply(env, chat, await cmdFx(D), kb("#CNY", "🇨🇳 Юань на дашборде"));
    case "/curve": return reply(env, chat, await cmdCurve(D), kb("#CURVE", "📉 Кривая на дашборде"));
    case "/calc": return reply(env, chat, cmdCalc(D, args), kb("#CALC", "🧮 Открыть калькулятор"));
    case "/divs": return reply(env, chat, await cmdDivs(), undefined);
    case "/dash": return reply(env, chat, "Дашборд:", kb("", "📊 Открыть"));
    case "/bonds": return reply(env, chat, "Подбор облигаций: фильтры по рейтингу, сроку, доходности, валюте; сборка портфеля по профилю риска, налоги, сценарии ставок, отчёт клиенту.",
      dash ? { inline_keyboard: [[{ text: "🧾 Подбор", web_app: { url: dash + "bonds.html" } }, { text: "💼 Портфель", web_app: { url: dash + "bonds.html#PORT" } }]] } : undefined);
    default:
      if (/^\d/.test(cmdRaw)) return reply(env, chat, cmdCalc(D, [cmdRaw, ...args]), kb("#CALC", "🧮 Открыть калькулятор"));
      return reply(env, chat, "Не понял. /help — список команд. Можно просто написать сумму и срок: «50млн 6м».");
  }
}

async function cmdNow(D) {
  const [im, cny, z] = await Promise.all([safe(moexIndex("IMOEX")), safe(moexCny()), safe(zcyc())]);
  const rows = [];
  if (im) rows.push(["IMOEX", nf(im.val, 0), sg(im.chg), sg(im.ytd, 1)]);
  if (cny) rows.push(["CNY/RUB биржа", nf(cny.val, 3), sg(cny.chg), ""]);
  const key = last(D, "KEY"); if (key) rows.push(["Ключевая", nf(key[1]) + "%", "", ""]);
  const dep = last(D, "DEP"); if (dep) rows.push(["Вклады топ-10", nf(dep[1]) + "%", "", ""]);
  if (z && z.points[1]) rows.push(["ОФЗ 1г", nf(z.points[1]) + "%", "", ""]);
  const b = last(D, "BRENT"), bp = prev(D, "BRENT"); if (b) rows.push(["Brent $ (утро)", nf(b[1]), bp ? sg((b[1] / bp[1] - 1) * 100) : "", ""]);
  const t = im && im.time ? ` · биржа ${im.time.slice(0, 5)} МСК` : "";
  return `<b>Рынок сейчас</b>${t}\n` + table([["", "", "день", "YTD"], ...rows]);
}

async function cmdRates(D) {
  const z = await safe(zcyc());
  const rows = [];
  for (const [k, l] of [["KEY", "Ключевая"], ["DEP", "Вклады топ-10"], ["RUONIA", "RUONIA"], ["RUSFARCNY", "RUSFAR CNY"], ["CORP_Y", "Корп. облиг."]]) {
    const v = last(D, k); if (!v) continue;
    const y0 = asOf(D, k, `${new Date().getFullYear() - 1}-12-31`);
    rows.push([l, nf(v[1]) + "%", y0 != null ? sg(v[1] - y0, 2, "") : ""]);
  }
  const pts = z ? z.points : {};
  for (const p of [1, 2, 5, 10]) if (pts[p] != null) rows.push([`ОФЗ ${p}г`, nf(pts[p]) + "%", ""]);
  const key = last(D, "KEY");
  let note = "";
  if (key && pts[1] != null) {
    const ke = (Math.pow(1 + key[1] / 100 / 365, 365) - 1) * 100, s = pts[1] - ke;
    const view = s <= -1.5 ? "заметное снижение ставки" : s <= -0.5 ? "снижение ставки" : s >= 0.5 ? "повышение ставки" : "ставку около текущей";
    note = `\n🔮 Рынок закладывает ${view} (ОФЗ 1г ${nf(pts[1])}% vs ключевая ≈${nf(ke)}%)`;
  }
  return "<b>Ставки</b>" + table([["", "", "YTD п.п."], ...rows]) + note;
}

async function cmdFx(D) {
  const cny = await safe(moexCny());
  const rows = [];
  for (const [k, l] of [["USD", "USD/RUB ЦБ"], ["EUR", "EUR/RUB ЦБ"], ["CNY", "CNY/RUB ЦБ"]]) {
    const v = last(D, k), p = prev(D, k); if (!v) continue;
    const y0 = asOf(D, k, `${new Date().getFullYear() - 1}-12-31`);
    rows.push([l, nf(v[1]), p ? sg((v[1] / p[1] - 1) * 100) : "", y0 ? sg((v[1] / y0 - 1) * 100, 1) : ""]);
  }
  if (cny) rows.push(["CNY/RUB биржа", nf(cny.val, 3), sg(cny.chg), ""]);
  return "<b>Валюты</b>" + table([["", "", "день", "YTD"], ...rows]) + (cny ? `<i>биржа: ${esc(cny.time || "")} МСК; ЦБ — курс на дату последнего обновления дашборда</i>` : "");
}

async function cmdCurve(D) {
  const z = await safe(zcyc());
  if (!z) return "Кривая ОФЗ недоступна.";
  const base = D && D.curves && D.curves["начало года"] ? Object.fromEntries(D.curves["начало года"].points) : {};
  const rows = [0.5, 1, 2, 3, 5, 7, 10, 15].filter(p => z.points[p] != null).map(p => [p < 1 ? `${p * 12} мес` : `${p} ${p < 5 ? "г" : "лет"}`, nf(z.points[p]) + "%", base[p] != null ? sg(z.points[p] - base[p], 2, "") : ""]);
  return `<b>Кривая ОФЗ</b> <i>${esc(z.time)}</i>` + table([["срок", "", "YTD п.п."], ...rows]);
}

// ---------------------------------------------------------------- калькулятор
function parseAmount(s) {
  if (!s) return null;
  const t = s.toLowerCase().replace(/\s/g, "").replace(",", ".");
  const m = t.match(/^([\d.]+)(млрд|bn|b|млн|mln|m|м|тыс|k|к)?$/);
  if (!m) return null;
  const mult = { "млрд": 1e9, "bn": 1e9, "b": 1e9, "млн": 1e6, "mln": 1e6, "m": 1e6, "м": 1e6, "тыс": 1e3, "k": 1e3, "к": 1e3 }[m[2]] || 1;
  return parseFloat(m[1]) * mult;
}
function parseTerm(s) {
  if (!s) return 12;
  const t = s.toLowerCase().replace(/\s/g, "").replace(",", ".");
  const m = t.match(/^([\d.]+)(мес|месяц\w*|м|m|г|год|года|лет|y)?$/);
  if (!m) return 12;
  const n = parseFloat(m[1]);
  return /^(г|год|года|лет|y)$/.test(m[2] || "") ? n * 12 : n;
}
// Налог - только с дохода по этой сумме. Доход 2026 г.: 13%/15% (порог 2,4 млн).
// Доход с 2027 г.: законопроект № 617-9 (если примут) - шкала 13/15/18/20/22%,
// необлагаемый процент по вкладам = 1 млн × макс. ключевая ПРОШЛОГО года.
const REFORM_YEAR = 2027;
function tax1315(b) { b = Math.max(0, b); return Math.min(b, 2.4e6) * 0.13 + Math.max(0, b - 2.4e6) * 0.15; }
function taxScale(b) {
  b = Math.max(0, b); let t = 0, lo = 0;
  for (const [hi, r] of [[2.4e6, .13], [5e6, .15], [20e6, .18], [50e6, .20], [Infinity, .22]]) { if (b > lo) t += (Math.min(b, hi) - lo) * r; lo = hi; }
  return t;
}
const yearTax = (y, base, reform) => (reform && y >= REFORM_YEAR) ? taxScale(base) : tax1315(base);
function splitYears(start, months) {
  const end = new Date(start); end.setMonth(end.getMonth() + months);
  const total = end - start, parts = []; let cur = new Date(start);
  while (cur < end) { const ny = new Date(cur.getFullYear() + 1, 0, 1); parts.push([cur.getFullYear(), (Math.min(ny, end) - cur) / total]); cur = ny; }
  return { parts, endYear: new Date(end - 1).getFullYear() };
}
// облигация по номиналу, доходность y (эфф.), f купонов в год; купоны реинвестируются после 13% НДФЛ,
// в конце каждого года налог пересчитывается по шкале. Возвращает чистый доход (та же логика, что bondmath.js на дашборде).
function bondNet(A, y, f, months, reform, start = new Date()) {
  const end = new Date(start); end.setMonth(end.getMonth() + months);
  const U = d => Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()), E = U(end);
  let cash = 0, t = U(start), prev = t;
  const yrs = {}, Y = k => (yrs[k] = yrs[k] || { b: 0, w: 0 });
  const settle = k => { if (!yrs[k] || yrs[k].done) return; cash -= yearTax(k, yrs[k].b, reform) - yrs[k].w; yrs[k].done = 1; };
  const income = (k, g) => { cash += g - 0.13 * g; Y(k).b += g; Y(k).w += 0.13 * g; };
  const grow = to => {
    while (t < to) {
      const k = new Date(t).getUTCFullYear(), ye = Date.UTC(k + 1, 0, 1), s = Math.min(to, ye);
      if (cash > 0) income(k, cash * (Math.pow(1 + y, (s - t) / 864e5 / 365) - 1));
      t = s; if (s === ye && to > ye) settle(k);
    }
  };
  for (let n = 1; ; n++) {
    const d = new Date(start); d.setMonth(d.getMonth() + Math.round(12 / f * n));
    const ms = Math.min(U(d), E);
    grow(ms);
    income(new Date(ms).getUTCFullYear(), A * (Math.pow(1 + y, (ms - prev) / 864e5 / 365) - 1)); prev = ms;
    if (ms >= E) break;
  }
  Object.keys(yrs).forEach(k => settle(+k));
  return cash;
}
function maxKeyOn1st(D, y) {
  let mx = null; const lastD = last(D, "KEY");
  for (let m = 0; m < 12; m++) { const iso = `${y}-${String(m + 1).padStart(2, "0")}-01`; if (lastD && iso > lastD[0]) break; const v = asOf(D, "KEY", iso); if (v != null) mx = Math.max(mx ?? v, v); }
  return mx;
}
function curveAt(D, years) {
  const c = D && D.curves && D.curves["сегодня"]; if (!c) return null;
  const p = c.points.slice().sort((a, b) => a[0] - b[0]);
  if (years <= p[0][0]) return p[0][1]; if (years >= p[p.length - 1][0]) return p[p.length - 1][1];
  for (let i = 1; i < p.length; i++) if (years <= p[i][0]) { const [x0, y0] = p[i - 1], [x1, y1] = p[i]; return y0 + (y1 - y0) * (years - x0) / (x1 - x0); }
}
function cmdCalc(D, args) {
  // «100млн 1г», «25 000 000 6м», «1,5 млрд 2 года»: срок - в конце, остальное - сумма
  let amount = null, months = 12;
  let text = args.join(" ").trim();
  const tm = text.match(/\s([\d.,]+\s*(мес|месяц\w*|м|m|г|год|года|лет|y))$/i);
  if (tm && text.slice(0, tm.index).trim()) { months = parseTerm(tm[1]); text = text.slice(0, tm.index).trim(); }
  amount = parseAmount(text.replace(/(\d)\s+(?=\d)/g, "$1"));
  if (!amount) return "Пример: <code>/calc 100млн 1г</code> или <code>/calc 25000000 6м</code>";
  const t = months / 12;
  const dep = last(D, "DEP"), ru = last(D, "RUONIA"), corp = last(D, "CORP_Y"), keyNow = (last(D, "KEY") || [0, 0])[1];
  const start = new Date(), nowY = start.getFullYear();
  const exempt = (y, reform) => {
    const ky = reform && y >= REFORM_YEAR ? y - 1 : y;
    return ((ky <= nowY ? maxKeyOn1st(D, ky) : null) ?? keyNow) / 100 * 1e6;
  };
  const { parts, endYear } = splitYears(start, months);
  const taxFor = (kind, inc, reform) => kind === "mmf" ? yearTax(endYear, inc, reform)
    : parts.reduce((s, [y, f]) => s + yearTax(y, inc * f - (kind === "dep" ? exempt(y, reform) : 0), reform), 0);
  const R = { dep: dep ? dep[1] : 12, mmf: ru ? Math.max(0, ru[1] - 0.4) : 12, ofz: curveAt(D, t) ?? 13, corp: corp ? corp[1] : 16 };
  const rows = [
    ["dep", "Вклад", R.dep, amount * (Math.pow(1 + R.dep / 100 / 12, 12 * t) - 1)],
    ["mmf", "Фонд ден. рынка", R.mmf, amount * (Math.pow(1 + R.mmf / 100 / 365, 365 * t) - 1)],
    ["ofz", "ОФЗ на срок", R.ofz, null, 2],
    ["corp", "Корп. облиг.", R.corp, null, 12],
  ].map(([k, n, r, inc, f]) => {
    if (f) { const a = bondNet(amount, r / 100, f, months, false), b = bondNet(amount, r / 100, f, months, true); return { n, r, net: b, extra: a - b }; }
    const tn = taxFor(k, inc, false), tr = taxFor(k, inc, true); return { n, r, net: inc - tr, extra: tr - tn };
  });
  const reformHits = endYear >= REFORM_YEAR;
  const base = rows[0].net;
  const fmtM = v => Math.abs(v) >= 1e6 ? nf(v / 1e6, 2) + " млн" : money(v).replace(" ₽", "");
  return `<b>Расчёт: ${fmtM(amount)} ₽ на ${months % 12 === 0 ? months / 12 + " г." : months + " мес."}</b>\n<i>на руки после НДФЛ, налог только с этой суммы</i>` +
    table([["", "ставка", "на руки", "к вкладу"], ...rows.map(r => [r.n, nf(r.r) + "%", fmtM(r.net), r === rows[0] ? "—" : (r.net >= base ? "+" : "−") + fmtM(Math.abs(r.net - base))])]) +
    (reformHits ? `<i>Доход с 2027 г. — по законопроекту № 617-9 (13–22%, ещё не принят). Из-за реформы налог выше на: ` +
      rows.map(r => `${r.n} ${fmtM(r.extra)} ₽`).join(", ") + ".</i>\n" : "") +
    `<i>Вклад — макс. ставка топ-10 с ежемесячной капитализацией; облигации — до погашения, купоны реинвестируются после 13% НДФЛ, в конце года доплата по шкале. Ориентир, не рекомендация.</i>`;
}

// ---------------------------------------------------------------- дивиденды
async function cmdDivs() {
  const r = await fetch("https://smart-lab.ru/dividends/", { headers: { "User-Agent": "Mozilla/5.0" } });
  if (!r.ok) return "Календарь дивидендов недоступен.";
  const html = await r.text();
  const strip = s => s.replace(/<[^>]+>/g, "").replace(/&nbsp;/g, " ").trim();
  const today = new Date().toISOString().slice(0, 10);
  const out = [];
  for (const [, attrs, tr] of html.matchAll(/<tr([^>]*)>([\s\S]*?)<\/tr>/g)) {
    const tds = [...tr.matchAll(/<td[^>]*>([\s\S]*?)<\/td>/g)].map(m => strip(m[1]));
    if (tds.length < 9) continue;
    const m = tds[7].match(/(\d\d)\.(\d\d)\.(\d{4})/); if (!m) continue;
    const iso = `${m[3]}-${m[2]}-${m[1]}`; if (iso < today) continue;
    out.push([iso, `▪️ ${m[1]}.${m[2]} · ${esc(tds[0])} · ${esc(tds[3])} ₽ (${esc(tds[4])})` + (attrs.includes("dividend_approved") ? "" : " · <i>рекоменд.</i>")]);
  }
  out.sort((a, b) => a[0] < b[0] ? -1 : 1);
  return out.length ? "<b>Ближайшие отсечки</b>\n" + out.slice(0, 15).map(x => x[1]).join("\n") : "Ближайших отсечек нет.";
}
