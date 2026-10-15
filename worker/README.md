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

Команды: `/now`, `/rates`, `/fx`, `/curve`, `/calc 100млн 1г`, `/divs`, `/dash`.
Можно писать без команды: «50млн 6м» — сразу расчёт.
