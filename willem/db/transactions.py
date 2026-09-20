from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from willem.timeutil import now_utc_iso

TransactionType = Literal["expense", "income", "transfer", "adjustment"]

_UNSET: Any = object()


@dataclass(frozen=True)
class Transaction:
    id: str
    user_id: int
    type: TransactionType
    amount: float
    currency: str
    target_amount: float | None
    target_currency: str | None
    source_id: str
    target_source_id: str | None
    category_id: str | None
    subcategory_id: str | None
    who: str | None
    comment: str | None
    created_at_utc: str
    synced: bool
    deleted: bool

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Transaction:
        return cls(
            id=row["id"],
            user_id=row["user_id"],
            type=row["type"],
            amount=row["amount"],
            currency=row["currency"],
            target_amount=row["target_amount"],
            target_currency=row["target_currency"],
            source_id=row["source_id"],
            target_source_id=row["target_source_id"],
            category_id=row["category_id"],
            subcategory_id=row["subcategory_id"],
            who=row["who"],
            comment=row["comment"],
            created_at_utc=row["created_at_utc"],
            synced=bool(row["synced"]),
            deleted=bool(row["deleted"]),
        )


def insert_transaction(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    type: TransactionType,
    amount: float,
    currency: str,
    source_id: str,
    target_amount: float | None = None,
    target_currency: str | None = None,
    target_source_id: str | None = None,
    category_id: str | None = None,
    subcategory_id: str | None = None,
    who: str | None = None,
    comment: str | None = None,
) -> Transaction:
    if type == "expense" and category_id is None:
        raise ValueError("expense требует category_id")
    if type == "transfer" and target_source_id is None:
        raise ValueError("transfer требует target_source_id")

    transaction_id = str(uuid.uuid4())
    created_at_utc = now_utc_iso()
    conn.execute(
        """
        INSERT INTO transactions (
            id, user_id, type, amount, currency, target_amount, target_currency,
            source_id, target_source_id, category_id, subcategory_id, who, comment,
            created_at_utc, synced, deleted
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0)
        """,
        (
            transaction_id,
            user_id,
            type,
            amount,
            currency,
            target_amount,
            target_currency,
            source_id,
            target_source_id,
            category_id,
            subcategory_id,
            who,
            comment,
            created_at_utc,
        ),
    )
    return Transaction(
        id=transaction_id,
        user_id=user_id,
        type=type,
        amount=amount,
        currency=currency,
        target_amount=target_amount,
        target_currency=target_currency,
        source_id=source_id,
        target_source_id=target_source_id,
        category_id=category_id,
        subcategory_id=subcategory_id,
        who=who,
        comment=comment,
        created_at_utc=created_at_utc,
        synced=False,
        deleted=False,
    )


def get_transaction(conn: sqlite3.Connection, transaction_id: str) -> Transaction | None:
    row = conn.execute(
        "SELECT * FROM transactions WHERE id = ?", (transaction_id,)
    ).fetchone()
    return Transaction.from_row(row) if row else None


def list_recent(
    conn: sqlite3.Connection, user_id: int, limit: int = 5
) -> list[Transaction]:
    rows = conn.execute(
        "SELECT * FROM transactions WHERE user_id = ? AND deleted = 0 "
        "ORDER BY created_at_utc DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    return [Transaction.from_row(r) for r in rows]


def update_transaction(
    conn: sqlite3.Connection,
    transaction_id: str,
    *,
    amount: float | None = None,
    category_id: str | None = _UNSET,
    comment: str | None = _UNSET,
) -> None:
    fields = []
    params: list = []
    if amount is not None:
        fields.append("amount = ?")
        params.append(amount)
    if category_id is not _UNSET:
        fields.append("category_id = ?")
        params.append(category_id)
    if comment is not _UNSET:
        fields.append("comment = ?")
        params.append(comment)
    if not fields:
        return
    params.append(transaction_id)
    conn.execute(f"UPDATE transactions SET {', '.join(fields)} WHERE id = ?", params)


def soft_delete_transaction(conn: sqlite3.Connection, transaction_id: str) -> None:
    conn.execute("UPDATE transactions SET deleted = 1 WHERE id = ?", (transaction_id,))


def list_unsynced(conn: sqlite3.Connection, user_id: int) -> list[Transaction]:
    rows = conn.execute(
        "SELECT * FROM transactions WHERE user_id = ? AND synced = 0 ORDER BY created_at_utc",
        (user_id,),
    ).fetchall()
    return [Transaction.from_row(r) for r in rows]


def mark_synced(conn: sqlite3.Connection, transaction_id: str) -> None:
    conn.execute("UPDATE transactions SET synced = 1 WHERE id = ?", (transaction_id,))


def sum_expenses_by_category(
    conn: sqlite3.Connection,
    category_id: str,
    currency: str,
    period_start_utc: str,
    period_end_utc: str,
) -> float:
    """Сумма трат по категории за период — только в указанной валюте.

    Категория не привязана к одной валюте (может пополняться с разных источников),
    поэтому суммы в разных валютах не складываются друг с другом — как и балансы.
    Считается по верхнеуровневой категории (`category_id`), независимо от выбранной
    подкатегории — лимит относится к категории целиком.
    """
    row = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM transactions "
        "WHERE category_id = ? AND type = 'expense' AND deleted = 0 AND currency = ? "
        "AND created_at_utc >= ? AND created_at_utc <= ?",
        (category_id, currency, period_start_utc, period_end_utc),
    ).fetchone()
    return row["total"]


def sum_expenses_by_category_grouped(
    conn: sqlite3.Connection,
    category_id: str,
    period_start_utc: str,
    period_end_utc: str,
) -> list[tuple[str, float]]:
    """Как `sum_expenses_by_category`, но сразу по всем валютам — [(валюта, сумма), ...],
    без смешивания разных валют в одно число. Используется в деталях категории для
    показа "потрачено с начала месяца" независимо от того, в какой валюте что тратили."""
    rows = conn.execute(
        "SELECT currency, COALESCE(SUM(amount), 0) AS total FROM transactions "
        "WHERE category_id = ? AND type = 'expense' AND deleted = 0 "
        "AND created_at_utc >= ? AND created_at_utc <= ? "
        "GROUP BY currency",
        (category_id, period_start_utc, period_end_utc),
    ).fetchall()
    return [(row["currency"], row["total"]) for row in rows]


def sum_by_type_and_period(
    conn: sqlite3.Connection, user_id: int, period_start_utc: str, period_end_utc: str
) -> list[Transaction]:
    """Все не удалённые операции пользователя за период — основа для /today, /week."""
    rows = conn.execute(
        "SELECT * FROM transactions WHERE user_id = ? AND deleted = 0 "
        "AND created_at_utc >= ? AND created_at_utc <= ?",
        (user_id, period_start_utc, period_end_utc),
    ).fetchall()
    return [Transaction.from_row(r) for r in rows]


def get_source_balance(conn: sqlite3.Connection, source_id: str) -> float:
    income = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM transactions "
        "WHERE source_id = ? AND type = 'income' AND deleted = 0",
        (source_id,),
    ).fetchone()["total"]
    expense = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM transactions "
        "WHERE source_id = ? AND type = 'expense' AND deleted = 0",
        (source_id,),
    ).fetchone()["total"]
    outgoing_transfer = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM transactions "
        "WHERE source_id = ? AND type = 'transfer' AND deleted = 0",
        (source_id,),
    ).fetchone()["total"]
    incoming_transfer = conn.execute(
        "SELECT COALESCE(SUM(target_amount), 0) AS total FROM transactions "
        "WHERE target_source_id = ? AND type = 'transfer' AND deleted = 0",
        (source_id,),
    ).fetchone()["total"]
    adjustment = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM transactions "
        "WHERE source_id = ? AND type = 'adjustment' AND deleted = 0",
        (source_id,),
    ).fetchone()["total"]
    return income + incoming_transfer - expense - outgoing_transfer + adjustment
