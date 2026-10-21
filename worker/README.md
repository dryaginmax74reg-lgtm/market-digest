# Команды боту (Cloudflare Worker)

Бесплатно, без своего сервера. Бот отвечает только вам (по chat_id).

1. Зарегистрируйтесь на dash.cloudflare.com (бесплатный план).
2. Workers & Pages → Create → Create Worker → имя, например `market-bot` → Deploy.
3. Edit code → удалите всё → вставьте содержимое `worker.js` → Deploy.
4. Settings → Variables and Secrets → Add:
   - `BOT_TOKEN` — токен бота (тип Secret)
   - `ALLOWED_CHAT_ID` — ваш chat_id (тот же, что в секрете TG_CHAT_ID)
   - `DASHBOARD_URL` — адрес дашборда (https://логин.github.io/репозиторий/)
   - `SETUP_KEY` — любой длинный пароль из латиницы и цифр (тип Secret)
   → Deploy.
5. Откройте в браузере `https://market-bot.<ваш-поддомен>.workers.dev/setup?key=<SETUP_KEY>`.
   Должно быть три `"ok": true`: вебхук, список команд, кнопка «Дашборд» в меню бота.
6. В Telegram напишите боту `/help`.

Команды: `/now`, `/rates`, `/fx`, `/curve`, `/calc 100млн 1г`, `/divs`, `/dash`, `/porog`.

## Пороги алертов командой `/porog`
1. Cloudflare → Storage & Databases → **KV** → Create namespace, имя любое (например `market-cfg`).
2. Воркер → Settings → **Bindings** → Add → KV namespace: Variable name **`CFG`**, namespace — созданный → Deploy.
3. В GitHub: Settings → Secrets and variables → Actions → **Secrets** → добавить
   `WORKER_URL` = `https://market-bot.<ваш-поддомен>.workers.dev` и `WORKER_KEY` = тот же `SETUP_KEY`.
4. Ещё раз откройте `…/setup?key=<SETUP_KEY>` — в меню бота появится `/porog`.

`/porog` — список порогов; `/porog юань 1.5` — изменить; `/porog сброс` — вернуть значения по умолчанию.
Показатели: доллар, евро, юань, индекс, офз, корп, нефть, золото, биткоин, акции, облигации, курсцб.
Алерты читают настройки при каждом запуске; если воркер не ответил — работают с порогами по умолчанию из `common.py`.
Можно писать без команды: «50млн 6м» — сразу расчёт.
