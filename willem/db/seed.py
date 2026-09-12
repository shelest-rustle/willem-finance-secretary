from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from willem.db import categories, sources


def seed_defaults(
    conn: sqlite3.Connection,
    user_id: int,
    seed_sources: Sequence[tuple[str, str, str, str, str | None]],
    seed_categories: Sequence[tuple[str, Sequence[str]]],
) -> None:
    """Заполняет источники и категории профиля, если для пользователя ещё ничего нет."""
    if not sources.list_sources(conn, user_id, active_only=False):
        for name, type_, currency, kind, owner in seed_sources:
            sources.create_source(conn, user_id, name, type_, currency, kind=kind, owner=owner)

    if not categories.list_categories(conn, user_id, active_only=False):
        for name, subcategory_names in seed_categories:
            category = categories.create_category(conn, user_id, name)
            for sub_name in subcategory_names:
                categories.create_category(conn, user_id, sub_name, parent_id=category.id)
