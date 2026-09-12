CREATE TABLE IF NOT EXISTS sources (
    id          TEXT PRIMARY KEY,   -- uuid4
    user_id     INTEGER NOT NULL,
    name        TEXT NOT NULL,      -- напр. "Kaspi Gold", "Halyk", "Наличные"
    type        TEXT NOT NULL,      -- card | cash | ... (профильно расширяемо)
    currency    TEXT NOT NULL DEFAULT 'KZT',
    kind        TEXT NOT NULL DEFAULT 'asset',  -- asset | debt
    owner       TEXT,               -- информационно: кому принадлежит (профиль "домохозяйство")
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
