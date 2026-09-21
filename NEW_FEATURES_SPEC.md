# NEW_FEATURES_SPEC.md — три новые фичи профиля Pantalone

Спецификация для реализации. Все три фичи относятся к профилю **Pantalone** (`profiles/pantalone.yaml`);
код, который приходится трогать в общей части (`willem/`), либо не меняет поведение других профилей
(willem), либо активируется только при наличии соответствующих полей в профильном YAML.

Контекст (см. память проекта и код на 2026-09-21): модули 1–11 исходного плана давно в проде на
Hetzner (systemd + docker compose), не трогать живой процесс без подтверждения. Кредитные листы и их
структура подробно разобраны в `MTS_CREDIT_TEST.md` — фича 3 целиком опирается на неё.

Ответы на уточняющие вопросы (заданы и получены до написания спеки):
- Напоминания о платеже получают **все участники профиля** (Лика, Ярослав, Админ — все id из
  `config.allowed_telegram_ids`), не только «хозяин» конкретного кредита.
- Периоды напоминания — **фиксированные 3/2/1/0 дней**, на кредит настраивается только вкл/выкл
  целиком, без гибкости по отдельным офсетам.
- Даты/суммы платежей нужно **периодически ресинхронизировать** из Google Sheets (не разовый импорт
  и забыли) — потому что банк пересчитывает будущие плановые суммы в колонке «План платёж» после
  включения «Уменьшить платёж» по досрочному взносу, и в БД иначе останется устаревшее число.

---

## Фича 1. Убрать блок «Долговые обязательства» из /balances

### Текущее поведение
`willem/bot/handlers/reports.py::_balances_text` — для источников `kind == "debt"`, если в профиле
заданы `debt_wallet_keywords`/`debt_obligation_keywords` (сейчас это только Pantalone), делит долги на
две секции: «Долговые кошельки» (кредитки/кубышки, `debt_wallet_keywords: ["кредитка", "кубышка"]`) и
«Долговые обязательства» (`debt_obligation_keywords: ["долг"]` → источники «Долг Роме», «Долг Артёму»).
Именно вторую секцию нужно убрать целиком.

### Решение — правка только YAML, без кода
`matches_keywords(name, keywords)` (`willem/db/sources.py:129`) с пустым списком `keywords` всегда
возвращает `False` (`any()` по пустому генератору). Значит если в `profiles/pantalone.yaml` убрать
(или обнулить) поле `debt_obligation_keywords`, список `obligations` в `_balances_text` всегда будет
пуст, и блок `reports.debts_header` перестанет выводиться — без единой правки в `reports.py`.

```yaml
# profiles/pantalone.yaml — было:
debt_obligation_keywords: ["долг"]
# стало: строку удалить полностью (или debt_obligation_keywords: [])
```

**Побочный эффект (важно проговорить с Ликой перед мержем):** источники «Долг Роме» и «Долг Артёму»
не попадают под `debt_wallet_keywords`, поэтому после удаления `debt_obligation_keywords` они вообще
перестанут показываться в `/balances` — так же, как сейчас не показываются installment-кредиты
(«Т-Кредит Лики» и т.п., которые ведутся в отдельных Google-листах). Если нужно видеть остаток по этим
двум личным долгам где-то ещё — это отдельная фича, не входит в этот пункт как сформулировано.

Тексты `reports.debts_header` / `reports.debts_total_prefix` в `messages:` секции `pantalone.yaml`
можно оставить как есть (мёртвый код текстов не мешает) — трогать не обязательно.

**Затронутые файлы:** `profiles/pantalone.yaml` (одна строка).
**Тесты:** существующий `tests/test_reports.py`, если там есть кейс на `debt_obligation_keywords` —
проверить/обновить фикстуру профиля в тесте под новое поведение (пустой список).

---

## Фича 2. Разбивка по подкатегориям в карточке категории

### Текущее поведение
`willem/bot/handlers/categories.py::_detail_text` при открытии категории (`cat_view:<id>`) показывает
название, лимит и суммарный расход по категории с начала месяца одной строкой
(`sum_expenses_by_category_grouped`, группировка только по валюте, без учёта подкатегории).

### Нужно
Под этой строкой — по одной строке на каждую активную подкатегорию, с её собственным расходом с
начала месяца (0, если не тратили), как в примере пользователя:

```
«Домашняя еда». Предел не установлен. Показатель по статье с начала месяца: 162 393 ₸.
«Вкусняшки»: 12 400 ₸
«Перекусы»: 8 100 ₸
«Продукты»: 141 893 ₸
```

Показывать **все активные подкатегории категории** (включая с нулевым расходом за месяц) — это даёт
полную картину сразу, а не только «где что-то было потрачено». Если у категории подкатегорий нет
(профиль `willem` — там подкатегорий вообще не заведено), блок просто не появляется — нулевых правок
для `willem` не требуется.

### Реализация

1. **Новая функция в `willem/db/transactions.py`**, рядом с `sum_expenses_by_category_grouped`:

```python
def sum_expenses_by_subcategory_grouped(
    conn: sqlite3.Connection,
    category_id: str,
    period_start_utc: str,
    period_end_utc: str,
) -> dict[str, list[tuple[str, float]]]:
    """Расход за период по подкатегориям заданной категории верхнего уровня —
    {subcategory_id: [(валюта, сумма), ...]}. Подкатегории без единой операции за
    период в результат не попадают — вызывающий код должен сам добить нулём для
    подкатегорий, которых нет в словаре."""
    rows = conn.execute(
        "SELECT subcategory_id, currency, COALESCE(SUM(amount), 0) AS total FROM transactions "
        "WHERE category_id = ? AND subcategory_id IS NOT NULL AND type = 'expense' AND deleted = 0 "
        "AND created_at_utc >= ? AND created_at_utc <= ? "
        "GROUP BY subcategory_id, currency",
        (category_id, period_start_utc, period_end_utc),
    ).fetchall()
    result: dict[str, list[tuple[str, float]]] = {}
    for row in rows:
        result.setdefault(row["subcategory_id"], []).append((row["currency"], row["total"]))
    return result
```

2. **`willem/bot/handlers/categories.py::view_category`** — рядом с уже читаемым `month_spent`,
   дочитать список подкатегорий и их расходы:

```python
subcategories = categories_db.list_categories(conn, category.user_id, parent_id=category.id)
subcategory_spent = transactions_db.sum_expenses_by_subcategory_grouped(conn, category_id, start, end)
```

   (`category.user_id` уже есть в объекте `Category` — не нужно протаскивать `ledger_user_id` отдельно,
   категория и так принадлежит нужному леджеру.)

3. **`_detail_text`** — новый параметр `subcategories: list[tuple[Category, list[tuple[str, float]]]]`
   (пары «подкатегория» → «её totals, или `[]`, если расходов не было»), после существующего
   `month_spent_suffix`/`month_spent_none` добавить по одной строке на подкатегорию:

```python
for subcategory in subcategories:
    amounts = subcategory_spent.get(subcategory.id, [])
    text += "\n" + texts.get(
        "categories.subcategory_spent_line",
        name=subcategory.name,
        amounts=format_currency_totals(amounts) if amounts else texts.get("categories.subcategory_spent_zero"),
    )
```

   (`format_currency_totals([])` даёт пустую строку — нужен отдельный текст для «0», т.к. валюта
   заранее неизвестна и показать «0 ₸» неоткуда взять символ. Второй вариант проще: если `amounts`
   пуст, просто показать `"0"` без символа валюты — на усмотрение реализации, зафиксировать при ревью.)

4. **Новый текстовый ключ в `willem/texts_base.yaml`** (секция `categories:`):

```yaml
subcategory_spent_line: "«{name}»: {amounts}"
subcategory_spent_zero: "0"
```

   Переопределение в `pantalone.yaml::messages` не требуется — формат нейтральный, голос Панталоне тут
   не участвует (это просто строка данных, как и сама `month_spent_suffix`, которую профиль тоже не
   переопределяет под подкатегории отдельно).

**Затронутые файлы:** `willem/db/transactions.py`, `willem/bot/handlers/categories.py`,
`willem/texts_base.yaml`.
**Тесты:** новый unit-тест на `sum_expenses_by_subcategory_grouped` (по аналогии с существующими
тестами в `tests/test_categories_hierarchy.py`), плюс обновить/добавить тест на `_detail_text` в том же
файле или в тестах категорий.

---

## Фича 3. Напоминания об оплате кредита

### 3.1 Источник данных
Кредитные листы (`credit_sheets` в `profiles/pantalone.yaml`) уже разобраны в `MTS_CREDIT_TEST.md`.
Таблица 1 «ПЛАТЕЖИ ПО ГРАФИКУ»: строки `SCHEDULE_FIRST_ROW` (8) .. `schedule_last_row` (по каждому
кредиту разное, вычисляется на лету через `_table_layout` в `willem/credit_sheets.py`), колонка
**B** — «Дата платежа», колонка **C** — «План платёж». Именно эти два столбца нужно забрать в БД.

### 3.2 Новые таблицы (`willem/db/schema.sql`)

```sql
-- Снепшот графика платежей по кредиту — обновляется периодической ресинхронизацией
-- из Google Sheets (см. willem/credit_schedule_sync.py), не пользователем напрямую.
CREATE TABLE IF NOT EXISTS credit_schedule (
    credit_key      TEXT NOT NULL,   -- ключ = ключ profiles.<profile>.yaml::credit_sheets,
                                      -- например "Tinkoff REF Кредит Лики"
    row_number      INTEGER NOT NULL,-- номер строки графика в самом листе (для отладки/логов)
    payment_date    TEXT NOT NULL,   -- ISO-дата YYYY-MM-DD, из колонки "Дата платежа"
    planned_amount  REAL NOT NULL,   -- из колонки "План платёж"
    PRIMARY KEY (credit_key, row_number)
);

-- Настройка "уведомлять по этому кредиту или нет". Если строки для credit_key нет —
-- считать enabled=1 по умолчанию (см. willem/db/credit_reminders.py).
CREATE TABLE IF NOT EXISTS credit_reminder_settings (
    credit_key   TEXT PRIMARY KEY,
    enabled      INTEGER NOT NULL DEFAULT 1
);

-- Журнал уже отправленных напоминаний — защита от повторной отправки, если джоба
-- запустится дважды за день (рестарт бота и т.п.).
CREATE TABLE IF NOT EXISTS credit_reminder_log (
    credit_key    TEXT NOT NULL,
    payment_date  TEXT NOT NULL,
    offset_days   INTEGER NOT NULL,   -- 3 | 2 | 1 | 0
    sent_at_utc   TEXT NOT NULL,
    PRIMARY KEY (credit_key, payment_date, offset_days)
);
```

Обе таблицы **без `user_id`** — кредиты в Pantalone общесемейные (`shared_ledger`), напоминания не
привязаны к конкретному Telegram-пользователю, аудитория определяется на этапе отправки
(`config.allowed_telegram_ids`), не хранением в БД.

### 3.3 Конфиг

`willem/config.py::Config` — новое поле `credit_reminder_currency: str` (дефолт `"RUB"` — все текущие
кредитные источники Pantalone в RUB, см. `seed_sources`). Отдельное поле профильного YAML не обязательно
заводить, если ограничиться дефолтом; если когда-нибудь появится кредит не в RUB — тогда добавить
`credit_reminder_currency:` в `pantalone.yaml`. Офсеты `(3, 2, 1, 0)` — константа в коде
(`willem/credit_reminders.py`), не поле конфига (по решению из уточняющих вопросов).

### 3.4 Ресинхронизация графика — периодическая, не разовая

Новый модуль **`willem/credit_schedule_sync.py`**:

```python
def read_credit_schedule(config: Config, sheet_title: str) -> list[tuple[int, str, float]]:
    """Блокирующий gspread-вызов: (row_number, payment_date_iso, planned_amount) для всех
    строк графика этого листа. Верхняя граница диапазона — через _table_layout из
    willem/credit_sheets.py (переиспользовать, не дублировать вычисление границ)."""

async def resync_credit_schedules(config: Config) -> None:
    """Для каждого (credit_key, sheet_title) в config.credit_sheets — в отдельном потоке
    (asyncio.to_thread, как и остальные gspread-вызовы в проекте) читает лист и делает
    upsert (DELETE по credit_key + INSERT) в credit_schedule. Ошибка на одном кредите
    (например CreditSheetLayoutError) логируется и не прерывает ресинк остальных."""
```

- **`willem/scheduler.py`** — новая cron-задача `credit_schedule_resync`, например `hour=3, minute=30`
  (сразу после существующей ночной синхронизации Sheets в 03:00), но **только если
  `config.credit_sheets` непусто** — иначе не регистрировать джобу вообще (профили без кредитов не
  должны заводить лишние задачи и лишние обращения к Sheets API).
- **`willem/cli.py`** — новая подкоманда `python -m willem.cli resync_credits` (по образцу уже
  существующей `resync`), чтобы Лика/Ярослав могли принудительно обновить график сразу после того, как
  вписали в таблицу «Уменьшить платёж», не дожидаясь 03:30.

### 3.5 Отправка напоминаний

Новый модуль **`willem/credit_reminders.py`**. Чтобы не тащить сеть/БД в тесты, ядро логики — чистая
функция:

```python
_OFFSETS = (3, 2, 1, 0)

def due_reminders(
    schedule: list[CreditScheduleRow],   # для одного credit_key
    already_sent: set[tuple[str, int]],  # {(payment_date, offset_days), ...} из credit_reminder_log
    today: date,
) -> list[tuple[CreditScheduleRow, int]]:
    """Какие (строка графика, offset_days) должны прямо сейчас получить напоминание —
    payment_date - offset_days == today и ещё не в already_sent. Чистая функция без IO —
    тестируется без gspread/БД/бота."""
    due = []
    for row in schedule:
        offset = (row.payment_date - today).days
        if offset in _OFFSETS and (row.payment_date.isoformat(), offset) not in already_sent:
            due.append((row, offset))
    return due


async def send_due_reminders(bot: Bot, config: Config, texts: Texts) -> None:
    """IO-обвязка: для каждого credit_key с enabled=True в credit_reminder_settings —
    due_reminders(...) -> сформировать текст -> разослать всем config.allowed_telegram_ids ->
    записать в credit_reminder_log."""
```

- **`willem/scheduler.py`** — новая cron-задача `credit_payment_reminders`, время — утро,
  например `hour=9, minute=0` (после ночного ресинка графика, чтобы использовать свежие даты/суммы).
  Тоже только при непустом `config.credit_sheets`.
- Джобе нужен доступ к `bot` (сейчас `create_scheduler(config)` его не получает) — передать `bot` как
  дополнительный аргумент при регистрации job в `willem/bot/app.py::run_bot` (там `bot` уже создан перед
  `scheduler.start()`).

Текст напоминания — **один и тот же шаблон для всех четырёх офсетов** (как в примере пользователя, без
вариаций вида «сегодня последний день»). Новый ключ в `willem/texts_base.yaml` (секция `credits:`,
нейтральная формулировка) и override в `pantalone.yaml::messages` голосом Панталоне:

```yaml
# pantalone.yaml messages:
credits.reminder: "Приближается дата оплаты «{credit}». Нужно внести {amount} {symbol} до {date} включительно. Позаботьтесь о том, чтобы на счету списания было достаточно средств."
```

Дата — формат `ДД.ММ.ГГГГ` (нужен небольшой хелпер в `willem/timeutil.py`, т.к. существующий
`format_sheet_date` принимает ISO-datetime с временем, а `payment_date` — просто календарная дата без
времени: `def format_date_ru(date_iso: str) -> str: return date.fromisoformat(date_iso).strftime("%d.%m.%Y")`).

### 3.6 UI: кнопка «Кредиты»

- `willem/bot/keyboards.py::build_main_menu` — принимает `config` (или просто `bool`), добавляет
  кнопку **«Кредиты»** в реплай-клавиатуру, но только если `config.credit_sheets` непусто (для `willem`
  кнопка не появляется).
- Новый роутер **`willem/bot/handlers/credits.py`**, подключить в `willem/bot/app.py::create_dispatcher`:
  - `/credits` + кнопка → список кредитов: для каждого `credit_key` в `config.credit_sheets` — строка
    с названием, датой и суммой ближайшего платежа (`payment_date >= today`, минимальная такая запись
    в `credit_schedule`; если такой нет — «график исчерпан»), плюс инлайн-кнопка на каждый кредит для
    перехода в детальную карточку.
  - Тап на кредит → карточка: то же самое (дата/сумма ближайшего платежа) + статус напоминаний
    (Включены/Выключены) + кнопка-тумблер, меняющая `credit_reminder_settings.enabled`.
  - Новые тексты: `credits.list_title`, `credits.list_line`, `credits.detail`, `credits.notify_on`,
    `credits.notify_off`, `credits.notify_toggled`, `credits.no_upcoming_payment`, `credits.not_found`
    — в `texts_base.yaml` + переопределения голоса Панталоне в `pantalone.yaml::messages` (аналогично
    остальным разделам профиля).

### 3.7 Первичное наполнение после деплоя

Ничего специального готовить не нужно: первый прогон `python -m willem.cli resync_credits` (вручную,
сразу после раскатки) или первая ночная джоба сама наполнит `credit_schedule` с нуля обычным INSERT.
`credit_reminder_settings` можно не сидить заранее — отсутствие строки трактуется как «включено».

### 3.8 Что нужно проверить эмпирически перед реализацией

По аналогии с тем, как `MTS_CREDIT_TEST.md` заранее проверял формулы через `value_render_option`,
перед кодом стоит быстрым скриптом посмотреть на реальном листе:
- В каком виде `gspread` отдаёт колонку B (дата) при обычном `value_render_option` (по умолчанию
  `FORMATTED_VALUE`) — вероятно строка вида `"19.11.2026"`, но нужно подтвердить формат (могут быть
  `19.11.26`, или другой разделитель) и написать парсер под реальный формат, а не гадать.
- В каком виде отдаётся колонка C (план платёж, сама может быть формулой — см. `MTS_CREDIT_TEST.md`,
  раздел 2, ячейка `C9`) — при `FORMATTED_VALUE` gspread обычно уже возвращает вычисленное значение
  формулы (не саму формулу), но нужно подтвердить, что число распарсится (там может быть разделитель
  тысяч/валютный символ, как в примере `"10 837,00 ₽"` из раздела 1 документа).
- Первая строка графика (row 8, `D8=C8`, «зеркальный» дефолт факта оплаты) в этой фиче не имеет
  значения — берём только дату/план из B/C, а не «оплачено ли», поэтому доп. логика distinguишения
  формулы от литерала (нужная для `record_regular_payment`) тут не нужна.

### Затронутые файлы (фича 3)

- `willem/db/schema.sql` — три новые таблицы.
- Новый `willem/db/credit_reminders.py` (или расширить `db/`-пакет отдельным модулем) — CRUD над
  `credit_schedule` / `credit_reminder_settings` / `credit_reminder_log`.
- Новый `willem/credit_schedule_sync.py` — чтение графика из Sheets, upsert в БД.
- Новый `willem/credit_reminders.py` — чистая функция `due_reminders` + IO-обвязка `send_due_reminders`.
- `willem/config.py` — поле `credit_reminder_currency`.
- `willem/scheduler.py` — две новые cron-задачи (`credit_schedule_resync`, `credit_payment_reminders`),
  обе no-op при пустом `config.credit_sheets`.
- `willem/cli.py` — подкоманда `resync_credits`.
- `willem/timeutil.py` — хелпер `format_date_ru` (или аналог) и `today_local_date`.
- `willem/bot/keyboards.py` — кнопка «Кредиты» (условно).
- Новый `willem/bot/handlers/credits.py`, подключить в `willem/bot/app.py`.
- `willem/bot/app.py` — передать `bot` в новую cron-задачу напоминаний.
- `willem/texts_base.yaml` + `profiles/pantalone.yaml` (`messages:`) — новые текстовые ключи.

### Тесты

- `due_reminders(...)` — чистая функция, полностью покрывается unit-тестами без сети/БД/бота (границы
  офсетов, уже отправленные, кредит не в графике и т.п.) — новый `tests/test_credit_reminders.py`.
- Парсинг графика (`read_credit_schedule`) — тест с замоканным `gspread.Worksheet.get`, по образцу
  существующего `tests/test_credit_sheets.py`.
- `python -m willem.cli resync_credits` — тест по аналогии с `tests/test_cli.py`.
- Существующие 56 тестов должны остаться зелёными, `ruff check .` — чистым.

---

## Открытые вопросы — сняты, реализация завершена (2026-09-21)

1. Фича 1: подтверждено — пропажа «Долг Роме»/«Долг Артёму» из `/balances` ожидаема и приемлема,
   убираем только из сообщения баланса. Реализовано.
2. Фича 2: подкатегории с нулевым расходом за месяц показываются всегда (строка с «0»). Реализовано.
3. Фича 3: время джоб подтверждено как есть — `credit_schedule_resync` в 03:30, `credit_payment_reminders`
   в 09:00, обе в таймзоне профиля (`config.timezone`, для Pantalone — `Asia/Almaty`). Реализовано.
4. Фича 3: для офсета 0 (день платежа) сделана отдельная, более резкая формулировка
   (`credits.reminder_due_today`) — для Pantalone: «Сегодня — крайний срок оплаты «{credit}». Внесите
   {amount} {symbol} до конца дня: договор не предусматривает отсрочек.» (без эмодзи/восклицательных
   знаков — голос Панталоне их не использует). Реализовано.

## Статус реализации

Все три фичи реализованы и покрыты тестами (148 тестов зелёные, `ruff check .` чист):

- Фича 1 — `profiles/pantalone.yaml` (убрано поле `debt_obligation_keywords`), без изменений кода.
- Фича 2 — `willem/db/transactions.py::sum_expenses_by_subcategory_grouped`,
  `willem/bot/handlers/categories.py::_detail_text`/`view_category`, новые ключи в `texts_base.yaml`.
- Фича 3 — новые таблицы `credit_schedule`/`credit_reminder_settings`/`credit_reminder_log`
  (`willem/db/schema.sql`), CRUD в `willem/db/credit_reminders.py`, чтение/ресинк графика в
  `willem/credit_sheets.py` (`read_credit_schedule`, `resync_credit_schedules`), рассылка напоминаний в
  `willem/credit_reminders.py` (`due_reminders`, `send_due_reminders`), две новые cron-задачи в
  `willem/scheduler.py`, CLI-команда `resync_credits`, кнопка «Кредиты» и роутер
  `willem/bot/handlers/credits.py`, тексты в `texts_base.yaml` + голос Панталоне в `pantalone.yaml`.

**Требует проверки на реальных данных перед деплоем** (см. раздел 3.8 выше): точный формат, в котором
`gspread` отдаёт колонку «Дата платежа» (ожидается `ДД.ММ.ГГГГ`) и «План платёж» (число или строка с
пробелом-разделителем тысяч/символом валюты) на настоящих кредитных листах — парсер
(`_parse_schedule_date`/`_parse_planned_amount` в `willem/credit_sheets.py`) написан под формат из
`MTS_CREDIT_TEST.md`, но не проверялся на живой таблице. Рекомендуется после деплоя сразу выполнить
`docker compose exec pantalone python -m willem.cli resync_credits` и проверить `/credits` вручную,
прежде чем полагаться на автоматические напоминания.
