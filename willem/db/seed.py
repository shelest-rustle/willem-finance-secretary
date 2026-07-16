from __future__ import annotations

import sqlite3

from willem.db import categories, sources

DEFAULT_SOURCES = [
    ("Kaspi", "card", "KZT"),
    ("bcc", "card", "KZT"),
    ("Freedom", "card", "KZT"),
    ("Т-банк", "card", "RUB"),
    ("Озон Банк", "card", "RUB"),
    ("BYBIT", "crypto", "KZT"),
    ("Кубышка", "card", "RUB"),
    ("Наличные", "cash", "KZT"),
]

DEFAULT_CATEGORIES = [
    "Транспорт",
    "Питание",
    "Артемида",
    "Здоровье",
    "Квартира",
    "Спорт",
    "Подписки",
    "Связь",
    "Германия",
    "Друзья",
    "Шоппинг",
    "Образование",
    "Долги",
    "Быт",
    "Подарки",
    "Кредиты",
    "Другое",
]


def seed_defaults(conn: sqlite3.Connection, user_id: int) -> None:
    """Заполняет источники и категории по умолчанию, если для пользователя ещё ничего нет."""
    if not sources.list_sources(conn, user_id, active_only=False):
        for name, type_, currency in DEFAULT_SOURCES:
            sources.create_source(conn, user_id, name, type_, currency)

    if not categories.list_categories(conn, user_id, active_only=False):
        for name in DEFAULT_CATEGORIES:
            categories.create_category(conn, user_id, name)
