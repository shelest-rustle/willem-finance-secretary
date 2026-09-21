CREATE TABLE IF NOT EXISTS sources (
    id          TEXT PRIMARY KEY,   -- uuid4
    user_id     INTEGER NOT NULL,
    name        TEXT NOT NULL,      -- напр. "Kaspi Gold", "Halyk", "Наличные"
    type        TEXT NOT NULL,      -- card | cash | ... (профильно расширяемо)
    currency    TEXT NOT NULL DEFAULT 'KZT',
    kind        TEXT NOT NULL DEFAULT 'asset',  -- asset | debt
    owner       TEXT,               -- информационно: кому принадлежит (профиль "домохозяйство")
    credit_limit REAL,              -- только для кредиток/кубышек, вводится вручную через /sources
    is_active   INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS categories (
    id            TEXT PRIMARY KEY, -- uuid4
    user_id       INTEGER NOT NULL,
    name          TEXT NOT NULL,
    limit_amount  REAL,             -- NULL = без лимита
    limit_period  TEXT NOT NULL DEFAULT 'month',  -- month | week
    parent_id     TEXT REFERENCES categories(id),  -- NULL = категория верхнего уровня
    is_active     INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS transactions (
    id                TEXT PRIMARY KEY,     -- uuid4, ключ дедупа в Sheets
    user_id           INTEGER NOT NULL,
    type              TEXT NOT NULL,        -- expense | income | transfer
    amount            REAL NOT NULL,        -- списание, в валюте source_id
    currency          TEXT NOT NULL,        -- = валюта source_id
    target_amount     REAL,                 -- только transfer: зачисление в валюте target_source_id
    target_currency   TEXT,                 -- только transfer
    source_id         TEXT NOT NULL REFERENCES sources(id),
    target_source_id  TEXT REFERENCES sources(id),
    category_id       TEXT REFERENCES categories(id),     -- верхнего уровня; для expense обязательна
    subcategory_id    TEXT REFERENCES categories(id),      -- опционально, дочерняя category_id
    who               TEXT,                 -- профиль "домохозяйство": кто платил / "Семья"
    comment           TEXT,
    created_at_utc    TEXT NOT NULL,        -- ISO 8601, UTC
    synced            INTEGER NOT NULL DEFAULT 0,
    deleted           INTEGER NOT NULL DEFAULT 0
);

-- Снепшот графика платежей по кредиту (профиль Pantalone, см. profiles/pantalone.yaml::credit_sheets
-- и MTS_CREDIT_TEST.md) — периодически обновляется из Google Sheets, см. willem/credit_schedule_sync.py.
-- Без user_id: кредиты общесемейные (shared_ledger), не привязаны к одному Telegram-пользователю.
CREATE TABLE IF NOT EXISTS credit_schedule (
    credit_key      TEXT NOT NULL,   -- ключ profiles.<profile>.yaml::credit_sheets, напр. "Tinkoff REF Кредит Лики"
    row_number      INTEGER NOT NULL,-- номер строки графика на самом листе (для отладки)
    payment_date    TEXT NOT NULL,   -- ISO-дата YYYY-MM-DD, колонка "Дата платежа"
    planned_amount  REAL NOT NULL,   -- колонка "План платёж"
    PRIMARY KEY (credit_key, row_number)
);

-- Вкл/выкл напоминаний по конкретному кредиту. Отсутствие строки для credit_key = "включено"
-- (см. willem/db/credit_reminders.py::is_reminder_enabled).
CREATE TABLE IF NOT EXISTS credit_reminder_settings (
    credit_key   TEXT PRIMARY KEY,
    enabled      INTEGER NOT NULL DEFAULT 1
);

-- Журнал уже отправленных напоминаний — защита от повторной отправки при повторном
-- запуске джобы в тот же день (например после рестарта бота).
CREATE TABLE IF NOT EXISTS credit_reminder_log (
    credit_key    TEXT NOT NULL,
    payment_date  TEXT NOT NULL,
    offset_days   INTEGER NOT NULL,   -- 3 | 2 | 1 | 0
    sent_at_utc   TEXT NOT NULL,
    PRIMARY KEY (credit_key, payment_date, offset_days)
);
