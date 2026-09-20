from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    id: str
    user_id: int
    name: str
    type: str
    currency: str
    kind: str
    owner: str | None
    credit_limit: float | None
    is_active: bool

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Source:
        return cls(
            id=row["id"],
            user_id=row["user_id"],
            name=row["name"],
            type=row["type"],
            currency=row["currency"],
            kind=row["kind"],
            owner=row["owner"],
            credit_limit=row["credit_limit"],
            is_active=bool(row["is_active"]),
        )


def create_source(
    conn: sqlite3.Connection,
    user_id: int,
    name: str,
    type: str,
    currency: str = "KZT",
    kind: str = "asset",
    owner: str | None = None,
    credit_limit: float | None = None,
) -> Source:
    source_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO sources (id, user_id, name, type, currency, kind, owner, credit_limit, is_active) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)",
        (source_id, user_id, name, type, currency, kind, owner, credit_limit),
    )
    return Source(
        id=source_id,
        user_id=user_id,
        name=name,
        type=type,
        currency=currency,
        kind=kind,
        owner=owner,
        credit_limit=credit_limit,
        is_active=True,
    )


def get_source(conn: sqlite3.Connection, source_id: str) -> Source | None:
    row = conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
    return Source.from_row(row) if row else None


def list_sources(
    conn: sqlite3.Connection, user_id: int, active_only: bool = True
) -> list[Source]:
    query = "SELECT * FROM sources WHERE user_id = ?"
    params: list = [user_id]
    if active_only:
        query += " AND is_active = 1"
    query += " ORDER BY name"
    rows = conn.execute(query, params).fetchall()
    return [Source.from_row(r) for r in rows]


def update_source(
    conn: sqlite3.Connection,
    source_id: str,
    *,
    name: str | None = None,
    currency: str | None = None,
) -> None:
    fields = []
    params: list = []
    if name is not None:
        fields.append("name = ?")
        params.append(name)
    if currency is not None:
        fields.append("currency = ?")
        params.append(currency)
    if not fields:
        return
    params.append(source_id)
    conn.execute(f"UPDATE sources SET {', '.join(fields)} WHERE id = ?", params)


def set_credit_limit(conn: sqlite3.Connection, source_id: str, credit_limit: float) -> None:
    conn.execute("UPDATE sources SET credit_limit = ? WHERE id = ?", (credit_limit, source_id))


def archive_source(conn: sqlite3.Connection, source_id: str) -> None:
    conn.execute("UPDATE sources SET is_active = 0 WHERE id = ?", (source_id,))


def matches_keywords(name: str, keywords: Sequence[str]) -> bool:
    """Содержит ли название источника хотя бы одно из ключевых слов (без учёта
    регистра) — используется для профильной классификации долговых источников
    (кредитки/кубышки vs личные долги), см. `Config.debt_wallet_keywords` /
    `Config.debt_obligation_keywords`."""
    lowered = name.lower()
    return any(keyword.lower() in lowered for keyword in keywords)
