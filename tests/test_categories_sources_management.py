from __future__ import annotations

from willem.bot.handlers.categories import _detail_text as category_detail_text
from willem.bot.handlers.sources import _detail_text as source_detail_text
from willem.db.categories import Category
from willem.db.sources import Source


def test_category_detail_text_without_limit() -> None:
    category = Category(
        id="c1", user_id=1, name="Прочее", limit_amount=None, limit_period="month", is_active=True
    )
    assert category_detail_text(category) == "«Прочее». Лимит не задан."


def test_category_detail_text_with_limit() -> None:
    category = Category(
        id="c1", user_id=1, name="Питание", limit_amount=50000, limit_period="week", is_active=True
    )
    assert category_detail_text(category) == "«Питание». Лимит на неделю: 50 000."


def test_source_detail_text() -> None:
    source = Source(id="s1", user_id=1, name="Kaspi", type="card", currency="KZT", is_active=True)
    assert source_detail_text(source, 12345.5) == "«Kaspi». Карта · KZT.\nБаланс: 12 345.50 ₸."
