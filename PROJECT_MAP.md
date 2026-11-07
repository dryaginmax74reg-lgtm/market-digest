# Карта Market Digest

| Задача | Откуда начать |
| --- | --- |
| Утренний дайджест | [digest.py](digest.py) собирает данные и формирует выпуск. [common.py](common.py) — MOEX ISS, ЦБ, сеть, форматы и Telegram. [events.py](events.py), [official_events.py](official_events.py), [insights.py](insights.py) — календарь и аналитика. [card.py](card.py), [charts.py](charts.py) — визуализация. |
| Алерты и обзоры | [alerts.py](alerts.py) и [live.py](live.py) — сигналы и состояние сообщения. [review.py](review.py) — недельный и месячный обзор. |
| Облигации | [bonds_meta.py](bonds_meta.py) — справочник, [bondsweek.py](bondsweek.py) — блок выпуска, [credit_events.py](credit_events.py) — кредитные события. [bondscore.py](bondscore.py) и [docs/bondscore.js](docs/bondscore.js) — парные реализации скоринга. [docs/bondmath.js](docs/bondmath.js) — расчёты портфеля. |
| Pages, Mini App и команды | [docs/index.html](docs/index.html) — дашборд. [docs/bonds.html](docs/bonds.html) — облигации, [docs/bonds-preview.html](docs/bonds-preview.html) — отдельная версия интерфейса. [worker/README.md](worker/README.md) — Cloudflare Worker, webhook, команды и KV. |
| Форматы и эксплуатация | [README.md](README.md) — пользовательские режимы и настройки. [notes/optimization.md](notes/optimization.md), [notes/market-intelligence.md](notes/market-intelligence.md) — решения и ограничения. |

[Workflows](.github/workflows) разделяют утренний выпуск (`digest.yml`, сбор → публикация Pages → отправка), справочник (`bonds.yml`), алерты (`alerts.yml`), обзоры (`review.yml`) и автономные проверки (`checks.yml`). Три workflow, которые пишут файлы в Git, используют группу `repo-writer`.

Автоматически обновляются `docs/{data,digest,bonds_meta}.json`, `bonds_state.json`, `alerts_state.json` и часть `data/`. Для поиска причины начните с кода, который пишет нужный файл.

## Быстрые проверки

```sh
DRY_RUN=1 python -m unittest discover -s tests -p test_optimization.py -v
PYTHONPATH=. python tests/test_parity.py
node --test tests/bondmath.test.js tests/bondscore.test.js
python alerts.py --demo
```

Полный набор — в [checks.yml](.github/workflows/checks.yml). Python-скрипты, указанные там поимённо, запускайте напрямую. Графические тесты требуют matplotlib и Pillow.
