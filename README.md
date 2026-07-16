# Willem / Виллем

Личный секретарь по ведению расходов — Telegram-бот на aiogram для фиксации трат, доходов и
переводов между источниками (карты, нал, крипта, разные валюты), с автосинхронизацией
в Google Sheets и аналитикой поверх этих данных через Claude.

## Возможности

- **Расход** — сумма → источник → категория → обязательный комментарий → запись, с проверкой
  лимита категории за период (предупреждение при 80%+, без блокировки записи)
- **Доход** — `+сумма` → источник → обязательный комментарий, баланс источника растёт
- **Перевод между источниками** — `/transfer` → сумма → откуда → куда; если валюты разные —
  отдельно спрашивается фактическая сумма зачисления (курс нигде не хранится и не считается)
- **Категории и источники** — CRUD через `/categories`, `/sources`: создание, переименование,
  лимит (категории) / валюта (источники), архивация, коррекция остатка источника (тип операции
  `adjustment` — ручная правка баланса без создания фиктивного расхода)
- **Отчёты и правки** — `/today`, `/week`, `/balances`, `/last`, `/edit_last`, `/delete_last`,
  плюс кнопки этих же команд на основной клавиатуре
- **Google Sheets** — запись каждой операции в лист «Транзакции» (дедуп по `id`, автоматический
  ретрай при ошибке, при неудаче — постановка в ночную очередь)
- **Мультипользовательность** — у каждого разрешённого Telegram-пользователя полностью свой
  леджер (источники/категории/транзакции не пересекаются); синк в Google Sheets — только
  для владельца бота

## Стек

- Python 3.13+, [aiogram 3.x](https://docs.aiogram.dev/)
- sqlite3 (стандартная библиотека, без ORM), единый реестр операций (`transactions`) вместо
  раздельных таблиц для расходов/доходов — так переводы и балансы считаются одним и тем же способом
- `gspread` + `google-auth` — синхронизация в Google Sheets через service account
- `APScheduler` — ночная джоба (03:00 по локальной таймзоне) на досинхронизацию неотправленных операций

## Локальный запуск

```bash
poetry install
cp .env.example .env   # заполнить реальными значениями, см. ниже
poetry run pytest -q          # тесты
poetry run ruff check .       # линтер
poetry run python main.py     # поднимет polling
```

## Переменные окружения (`.env`)

| Переменная | Назначение |
|---|---|
| `BOT_TOKEN` | токен бота от @BotFather |
| `OWNER_TELEGRAM_ID` | telegram id владельца — только ему сидятся дефолтные категории/источники и включается синк в Google Sheets |
| `ADDITIONAL_TELEGRAM_IDS` | доп. пользователи через запятую — свой леджер, без сидинга, без синка в Sheets |
| `DB_PATH` | путь к sqlite-файлу, по умолчанию `./data/willem.db` |
| `GOOGLE_SHEETS_CREDENTIALS_PATH` | путь к JSON-ключу service account, по умолчанию `./credentials.json` |
| `GOOGLE_SHEETS_SPREADSHEET_ID` | ID таблицы (часть URL между `/d/` и `/edit`) |
| `TIMEZONE` | таймзона для отчётов/лимитов/дат, напр. `Asia/Almaty` |
| `LOG_LEVEL` | уровень логирования |

## Настройка Google Sheets

1. Создать проект в [console.cloud.google.com](https://console.cloud.google.com/), включить
   **Google Sheets API** и **Google Drive API**
2. Создать **Service Account** (Credentials → Create Credentials → Service account)
3. Сгенерировать JSON-ключ (вкладка Keys → Add Key → Create new key → JSON), сохранить как
   `credentials.json` в корне проекта (уже в `.gitignore`)
4. Создать таблицу, лист внутри назвать ровно **«Транзакции»**
5. Дать доступ **Editor** на таблицу email'у service account (`client_email` из JSON-ключа)
6. ID таблицы — в `GOOGLE_SHEETS_SPREADSHEET_ID`

Шапка листа «Транзакции» (11 колонок): `Дата | Тип | Сумма | Валюта | Источник | Категория |
Комментарий | Сумма зачисления | Валюта зачисления | Источник зачисления | id`

## Схема sqlite

Три таблицы: `sources` (источники), `categories` (категории с лимитом и периодом),
`transactions` (единый реестр — `type`: `expense` / `income` / `transfer` / `adjustment`).
Подробности — в `willem/db/schema.sql`.

## Деплой

Бот развёрнут и работает на **Hetzner Cloud** (VPS), под управлением systemd + Docker Compose.

```bash
docker compose build
docker compose up -d
```

`.env` и `credentials.json` не попадают в git — их нужно перенести на сервер отдельно (`scp`),
на образ они тоже не попадают (`.dockerignore`).

Для постоянной работы (переживает ребут сервера) — systemd-юнит [`deploy/willem-bot.service`](deploy/willem-bot.service),
оборачивающий `docker compose up -d` / `down`:

```bash
sudo cp deploy/willem-bot.service /etc/systemd/system/willem-bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now willem-bot.service
```

Бот работает через long-polling — входящих вебхуков нет, наружу открывать ничего не нужно
кроме SSH.

### Разовые операции обслуживания

Досинкать неотправленные операции владельца в Google Sheets вручную, не дожидаясь ночной джобы:

```bash
docker compose exec willem python -m willem.cli resync
```

## Тесты

56 тестов (`poetry run pytest -q`), покрывают "чистую" бизнес-логику (запись операций, лимиты,
форматирование, синк в Sheets, CLI). Маршрутизация aiogram (какой хендлер на что реагирует)
проверяется вручную вживую.
