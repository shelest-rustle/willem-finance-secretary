from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"
_INDEXES_PATH = Path(__file__).parent / "indexes.sql"

# Колонки, добавленные после первого релиза схемы — на уже существующей БД
# CREATE TABLE IF NOT EXISTS их не создаст, поэтому досоздаём вручную.
_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    ("sources", "kind", "TEXT NOT NULL DEFAULT 'asset'"),
    ("sources", "owner", "TEXT"),
    ("sources", "credit_limit", "REAL"),
    ("categories", "parent_id", "TEXT REFERENCES categories(id)"),
    ("transactions", "subcategory_id", "TEXT REFERENCES categories(id)"),
    ("transactions", "who", "TEXT"),
)


def _apply_migrations(conn: sqlite3.Connection) -> None:
    for table, column, ddl in _MIGRATIONS:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def init_db(db_path: str) -> None:
    """Таблицы → миграция недостающих колонок на уже существующей БД → индексы.

    Порядок важен: индексы (indexes.sql) ссылаются на колонки, которых на старой БД ещё
    может не быть (например `categories.parent_id`) — если создавать их раньше миграции,
    `CREATE INDEX` упадёт с "no such column".
    """
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as conn:
        conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
        _apply_migrations(conn)
        conn.executescript(_INDEXES_PATH.read_text(encoding="utf-8"))


@contextmanager
def connect(db_path: str) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
