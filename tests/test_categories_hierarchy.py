from __future__ import annotations

from pathlib import Path

from willem.db import categories, sources
from willem.db.connection import connect, init_db
from willem.db.transactions import insert_transaction, sum_expenses_by_subcategory_grouped


def test_list_categories_defaults_to_top_level_only(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(str(db_path))

    with connect(str(db_path)) as conn:
        home = categories.create_category(conn, 1, "Дом")
        categories.create_category(conn, 1, "Аренда", parent_id=home.id)
        categories.create_category(conn, 1, "Коммуналка", parent_id=home.id)
        categories.create_category(conn, 1, "Транспорт")

        top_level = categories.list_categories(conn, 1)
        assert {c.name for c in top_level} == {"Дом", "Транспорт"}

        subcategories = categories.list_categories(conn, 1, parent_id=home.id)
        assert {c.name for c in subcategories} == {"Аренда", "Коммуналка"}


def test_flat_categories_have_no_children(tmp_path: Path) -> None:
    """Категории без подкатегорий (как у Виллема) — list_categories(parent_id=...) для них
    просто возвращает пустой список, шаг выбора подкатегории не должен показываться."""
    db_path = tmp_path / "test.db"
    init_db(str(db_path))

    with connect(str(db_path)) as conn:
        category = categories.create_category(conn, 1, "Продукты")
        assert categories.list_categories(conn, 1, parent_id=category.id) == []


def test_sum_expenses_by_subcategory_grouped(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(str(db_path))

    with connect(str(db_path)) as conn:
        source = sources.create_source(conn, 1, "Наличные", "cash", "KZT")
        food = categories.create_category(conn, 1, "Домашняя еда")
        snacks = categories.create_category(conn, 1, "Вкусняшки", parent_id=food.id)
        groceries = categories.create_category(conn, 1, "Продукты", parent_id=food.id)
        categories.create_category(conn, 1, "Перекусы", parent_id=food.id)  # без трат

        insert_transaction(
            conn, user_id=1, type="expense", amount=12400, currency="KZT",
            source_id=source.id, category_id=food.id, subcategory_id=snacks.id,
        )
        insert_transaction(
            conn, user_id=1, type="expense", amount=141893, currency="KZT",
            source_id=source.id, category_id=food.id, subcategory_id=groceries.id,
        )
        # Расход по категории без подкатегории — не должен попадать в разбивку.
        insert_transaction(
            conn, user_id=1, type="expense", amount=999, currency="KZT",
            source_id=source.id, category_id=food.id,
        )

        result = sum_expenses_by_subcategory_grouped(conn, food.id, "0000", "9999")

    assert result == {
        snacks.id: [("KZT", 12400.0)],
        groceries.id: [("KZT", 141893.0)],
    }
