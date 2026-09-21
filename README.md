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
- **Профили** — один кодбейз может запускать несколько независимых ботов (свой токен, своя
  таблица, свой «персонаж»/тексты), см. раздел «Профили» ниже

## Стек

- Python 3.13+, [aiogram 3.x](https://docs.aiogram.dev/)
- sqlite3 (стандартная библиотека, без ORM), единый реестр операций (`transactions`) вместо
  раздельных таблиц для расходов/доходов — так переводы и балансы считаются одним и тем же способом
- `gspread` + `google-auth` — синхронизация в Google Sheets через service account
- `APScheduler` — ночная джоба (03:00 по локальной таймзоне) на досинхронизацию неотправленных операций

## Локальный запуск

```bash
poetry install
cp .env.willem.example .env.willem   # заполнить реальными значениями, см. ниже
poetry run pytest -q                 # тесты
poetry run ruff check .              # линтер
PROFILE=willem poetry run python main.py    # поднимет polling для профиля willem
```

`PROFILE` можно не указывать — по умолчанию `willem`.

## Профили

Каждый бот на этом кодбейзе — это **профиль**: свой Telegram-токен, своя Google-таблица, свой
набор дефолтных источников/категорий и (опционально) свой голос в сообщениях. Один процесс = один
профиль = один запущенный бот; несколько профилей — это несколько параллельно запущенных процессов
(см. `docker-compose.yml`, там один сервис на профиль).

Данные профиля разложены по двум местам:

- **`profiles/<profile>.yaml`** — коммитится в git, без секретов: имя персонажа (`persona`),
  `seed_sources`/`seed_categories` (сидятся один раз при первом старте владельцу профиля,
  аналог прежних `DEFAULT_SOURCES`/`DEFAULT_CATEGORIES`), и опциональная секция `messages:` —
  переопределения любых текстов бота поверх базового набора в `willem/texts_base.yaml`.
- **`.env.<profile>`** — секреты, в `.gitignore` (никогда не коммитить, переносить на сервер
  через `scp`, не через вставку в редактор): `BOT_TOKEN`, `OWNER_TELEGRAM_ID`,
  `ADDITIONAL_TELEGRAM_IDS`, `DB_PATH`, `GOOGLE_SHEETS_CREDENTIALS_PATH`,
  `GOOGLE_SHEETS_SPREADSHEET_ID`, `TIMEZONE`, `LOG_LEVEL` — те же переменные, что раньше лежали
  в едином `.env`.

Выбор профиля — переменная окружения `PROFILE` (по умолчанию `willem`): она определяет, какой
`.env.<PROFILE>` подхватить (`load_dotenv`) и какой `profiles/<PROFILE>.yaml` прочитать.

### Как добавить новый профиль

1. Создать бота через @BotFather → `BOT_TOKEN`.
2. Создать Google-таблицу, лист «Транзакции» (или другую структуру, если профилю нужна своя —
   тогда потребуется отдельная ветка в `willem/sheets.py`), выдать доступ Editor тому же service
   account, что и у остальных профилей (можно переиспользовать один `credentials.json`).
3. Скопировать `.env.willem.example` → `.env.<profile>`, заполнить реальными значениями.
4. Создать `profiles/<profile>.yaml` (см. `profiles/willem.yaml` как образец) — как минимум
   `name`, `persona`, `seed_sources`, `seed_categories`; `messages: {}`, если свой голос не нужен.
5. Добавить сервис в `docker-compose.yml` (скопировать блок `willem`, поменять `PROFILE` и
   `env_file`).
6. `scp` новый `.env.<profile>` на сервер, `docker compose up -d`.

## Переменные окружения (`.env.<profile>`)

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

`.env.<profile>` и `credentials.json` не попадают в git — их нужно перенести на сервер отдельно
(`scp`), на образ они тоже не попадают (`.dockerignore`).

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

Для профилей с настроенными `credit_sheets` (сейчас — Pantalone) — обновить снимок графика
платежей по кредитам вручную, не дожидаясь ночной ресинхронизации (03:30):

```bash
docker compose exec pantalone python -m willem.cli resync_credits
```

## Тесты

56 тестов (`poetry run pytest -q`), покрывают "чистую" бизнес-логику (запись операций, лимиты,
форматирование, синк в Sheets, CLI). Маршрутизация aiogram (какой хендлер на что реагирует)
проверяется вручную вживую.
