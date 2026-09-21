from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class CreditScheduleRow:
    credit_key: str
    row_number: int
    payment_date: date
    planned_amount: float

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> CreditScheduleRow:
        return cls(
            credit_key=row["credit_key"],
            row_number=row["row_number"],
            payment_date=date.fromisoformat(row["payment_date"]),
            planned_amount=row["planned_amount"],
        )


def replace_schedule(
    conn: sqlite3.Connection, credit_key: str, rows: list[tuple[int, str, float]]
) -> None:
    """Полностью заменяет график конкретного кредита — вызывается при ресинхронизации
    из Google Sheets (см. willem/credit_schedule_sync.py). rows: (row_number, payment_date_iso,
    planned_amount)."""
    conn.execute("DELETE FROM credit_schedule WHERE credit_key = ?", (credit_key,))
    conn.executemany(
        "INSERT INTO credit_schedule (credit_key, row_number, payment_date, planned_amount) "
        "VALUES (?, ?, ?, ?)",
        [(credit_key, row_number, payment_date, planned_amount) for row_number, payment_date, planned_amount in rows],
    )


def list_schedule(conn: sqlite3.Connection, credit_key: str) -> list[CreditScheduleRow]:
    rows = conn.execute(
        "SELECT * FROM credit_schedule WHERE credit_key = ? ORDER BY payment_date", (credit_key,)
    ).fetchall()
    return [CreditScheduleRow.from_row(r) for r in rows]


def next_payment(conn: sqlite3.Connection, credit_key: str, today: date) -> CreditScheduleRow | None:
    row = conn.execute(
        "SELECT * FROM credit_schedule WHERE credit_key = ? AND payment_date >= ? "
        "ORDER BY payment_date LIMIT 1",
        (credit_key, today.isoformat()),
    ).fetchone()
    return CreditScheduleRow.from_row(row) if row else None


def is_reminder_enabled(conn: sqlite3.Connection, credit_key: str) -> bool:
    """Отсутствие строки для credit_key трактуется как "включено" — не нужно сидить
    настройки заранее для новых кредитов."""
    row = conn.execute(
        "SELECT enabled FROM credit_reminder_settings WHERE credit_key = ?", (credit_key,)
    ).fetchone()
    return bool(row["enabled"]) if row else True


def set_reminder_enabled(conn: sqlite3.Connection, credit_key: str, enabled: bool) -> None:
    conn.execute(
        "INSERT INTO credit_reminder_settings (credit_key, enabled) VALUES (?, ?) "
        "ON CONFLICT(credit_key) DO UPDATE SET enabled = excluded.enabled",
        (credit_key, int(enabled)),
    )


def already_sent(conn: sqlite3.Connection, credit_key: str) -> set[tuple[str, int]]:
    """{(payment_date_iso, offset_days), ...} — уже отправленные напоминания по этому кредиту."""
    rows = conn.execute(
        "SELECT payment_date, offset_days FROM credit_reminder_log WHERE credit_key = ?",
        (credit_key,),
    ).fetchall()
    return {(row["payment_date"], row["offset_days"]) for row in rows}


def mark_sent(
    conn: sqlite3.Connection, credit_key: str, payment_date: date, offset_days: int, sent_at_utc: str
) -> None:
    conn.execute(
        "INSERT INTO credit_reminder_log (credit_key, payment_date, offset_days, sent_at_utc) "
        "VALUES (?, ?, ?, ?)",
        (credit_key, payment_date.isoformat(), offset_days, sent_at_utc),
    )
