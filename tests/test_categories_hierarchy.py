from __future__ import annotations

from pathlib import Path

from willem.db import categories
from willem.db.connection import connect, init_db


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
