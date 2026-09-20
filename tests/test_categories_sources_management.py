from __future__ import annotations

from willem.bot.handlers.categories import _detail_text as category_detail_text
from willem.bot.handlers.sources import _detail_keyboard, _is_debt_wallet
from willem.bot.handlers.sources import _detail_text as source_detail_text
from willem.db.categories import Category
from willem.db.sources import Source
from willem.texts import Texts


def test_category_detail_text_without_limit(texts: Texts) -> None:
    category = Category(
        id="c1", user_id=1, name="Прочее", limit_amount=None, limit_period="month",
        parent_id=None, is_active=True,
    )
    assert category_detail_text(category, texts) == "«Прочее». Лимит не задан."


def test_category_detail_text_with_limit(texts: Texts) -> None:
    category = Category(
        id="c1", user_id=1, name="Питание", limit_amount=50000, limit_period="week",
        parent_id=None, is_active=True,
    )
    assert category_detail_text(category, texts) == "«Питание». Лимит на неделю: 50 000."


class _FakeConfig:
    debt_wallet_keywords: tuple[str, ...] = ()


def test_source_detail_text(texts: Texts) -> None:
    source = Source(
        id="s1", user_id=1, name="Kaspi", type="card", currency="KZT",
        kind="asset", owner=None, credit_limit=None, is_active=True,
    )
    assert (
        source_detail_text(source, 12345.5, texts, _FakeConfig())
        == "«Kaspi». Карта · KZT.\nБаланс: 12 345.50 ₸."
    )


class _WalletConfig:
    debt_wallet_keywords: tuple[str, ...] = ("кредитка", "кубышка")


def test_source_detail_text_shows_credit_limit_when_set(texts: Texts) -> None:
    source = Source(
        id="s1", user_id=1, name="Т-Кредитка Лики", type="Кредитная карта", currency="RUB",
        kind="debt", owner=None, credit_limit=185000, is_active=True,
    )
    text = source_detail_text(source, -55000, texts, _WalletConfig())
    assert text.endswith("\nКредитный лимит: 185 000 ₽.")


def test_source_detail_text_prompts_to_set_missing_credit_limit(texts: Texts) -> None:
    source = Source(
        id="s1", user_id=1, name="Т-Кредитка Лики", type="Кредитная карта", currency="RUB",
        kind="debt", owner=None, credit_limit=None, is_active=True,
    )
    text = source_detail_text(source, -55000, texts, _WalletConfig())
    assert "лимит не задан" in text.lower()


def test_source_detail_text_no_credit_limit_suffix_for_non_wallet_source(texts: Texts) -> None:
    source = Source(
        id="s1", user_id=1, name="Долг Роме", type="Долг человеку", currency="RUB",
        kind="debt", owner=None, credit_limit=None, is_active=True,
    )
    text = source_detail_text(source, -15000, texts, _WalletConfig())
    assert "лимит" not in text.lower()


def test_is_debt_wallet_matches_keyword_case_insensitively() -> None:
    source = Source(
        id="s1", user_id=1, name="tinkoff КРЕДИТКА лики", type="Кредитная карта", currency="RUB",
        kind="debt", owner=None, credit_limit=None, is_active=True,
    )
    assert _is_debt_wallet(source, _WalletConfig()) is True


def test_is_debt_wallet_false_when_keywords_not_configured() -> None:
    source = Source(
        id="s1", user_id=1, name="Т-Кредитка Лики", type="Кредитная карта", currency="RUB",
        kind="debt", owner=None, credit_limit=None, is_active=True,
    )
    assert _is_debt_wallet(source, _FakeConfig()) is False


def test_detail_keyboard_includes_credit_limit_button_only_when_requested() -> None:
    with_limit = _detail_keyboard("s1", show_credit_limit=True)
    without_limit = _detail_keyboard("s1", show_credit_limit=False)
    with_labels = {btn.text for row in with_limit.inline_keyboard for btn in row}
    without_labels = {btn.text for row in without_limit.inline_keyboard for btn in row}
    assert "💳 Кредитный лимит" in with_labels
    assert "💳 Кредитный лимит" not in without_labels
