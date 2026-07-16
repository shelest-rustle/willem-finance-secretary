from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from typing import Any

_UNSET: Any = object()


@dataclass(frozen=True)
class Category:
    id: str
    user_id: int
    name: str
    limit_amount: float | None
    limit_period: str
    is_active: bool

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Category:
        return cls(
            id=row["id"],
            user_id=row["user_id"],
            name=row["name"],
            limit_amount=row["limit_amount"],
            limit_period=row["limit_period"],
            is_active=bool(row["is_active"]),
        )


def create_category(
    conn: sqlite3.Connection,
    user_id: int,
    name: str,
    limit_amount: float | None = None,
    limit_period: str = "month",
) -> Category:
    category_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO categories (id, user_id, name, limit_amount, limit_period, is_active) "
        "VALUES (?, ?, ?, ?, ?, 1)",
        (category_id, user_id, name, limit_amount, limit_period),
    )
    return Category(
        id=category_id,
        user_id=user_id,
        name=name,
        limit_amount=limit_amount,
        limit_period=limit_period,
        is_active=True,
    )


def get_category(conn: sqlite3.Connection, category_id: str) -> Category | None:
    row = conn.execute("SELECT * FROM categories WHERE id = ?", (category_id,)).fetchone()
    return Category.from_row(row) if row else None


def list_categories(
    conn: sqlite3.Connection, user_id: int, active_only: bool = True
) -> list[Category]:
    query = "SELECT * FROM categories WHERE user_id = ?"
    params: list = [user_id]
    if active_only:
        query += " AND is_active = 1"
    query += " ORDER BY name"
    rows = conn.execute(query, params).fetchall()
    return [Category.from_row(r) for r in rows]


def update_category(
    conn: sqlite3.Connection,
    category_id: str,
    *,
    name: str | None = None,
    limit_amount: float | None = _UNSET,
    limit_period: str | None = None,
) -> None:
    fields = []
    params: list = []
    if name is not None:
        fields.append("name = ?")
        params.append(name)
    if limit_amount is not _UNSET:
        fields.append("limit_amount = ?")
        params.append(limit_amount)
    if limit_period is not None:
        fields.append("limit_period = ?")
        params.append(limit_period)
    if not fields:
        return
    params.append(category_id)
    conn.execute(f"UPDATE categories SET {', '.join(fields)} WHERE id = ?", params)


def archive_category(conn: sqlite3.Connection, category_id: str) -> None:
    conn.execute("UPDATE categories SET is_active = 0 WHERE id = ?", (category_id,))
