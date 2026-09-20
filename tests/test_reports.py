from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from willem.bot.handlers.reports import (
    _apply_amount_edit,
    _apply_category_edit,
    _apply_comment_edit,
    _balances_text,
    _period_report_text,
    _transaction_summary,
)
from willem.config import Config
from willem.db import categories, sources
from willem.db.connection import connect, init_db
from willem.db.transactions import Transaction, insert_transaction
from willem.texts import Texts


def make_config(db_path: Path) -> Config:
    return Config(
        profile_name="willem",
        persona="Виллем",
        bot_token="123:fake",
        owner_telegram_id=1,
        allowed_telegram_ids=(1,),
        db_path=str(db_path),
        google_sheets_credentials_path="",
        google_sheets_spreadsheet_id="",
        timezone="Asia/Almaty",
        log_level="INFO",
        seed_sources=(),
        seed_categories=(),
        seed_all_users=False,
        sync_all_users=False,
        auto_category={},
        optional_comment=False,
        people={},
        currency_options=(),
        type_options=(),
        debt_types=(),
        shared_ledger=False,
        sheet_name="Транзакции",
        credit_sheets={},
        debt_wallet_keywords=(),
        debt_obligation_keywords=(),
    )


def make_state() -> FSMContext:
    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=1, user_id=1)
    return FSMContext(storage=storage, key=key)


def make_expense(**overrides) -> Transaction:
    base = dict(
        id="t1",
        user_id=1,
        type="expense",
        amount=1000.0,
        currency="KZT",
        target_amount=None,
        target_currency=None,
        source_id="s1",
        target_source_id=None,
        category_id="c1",
        subcategory_id=None,
        who=None,
        comment=None,
        created_at_utc="2026-07-16T10:00:00+00:00",
        synced=False,
        deleted=False,
    )
    base.update(overrides)
    return Transaction(**base)


# --- _period_report_text ---


def test_period_report_no_expenses(texts: Texts) -> None:
    assert _period_report_text([], "Сегодня", texts) == "Сегодня: трат нет."


def test_period_report_single_currency_single_category(texts: Texts) -> None:
    txs = [make_expense(amount=3400, category_id="c1")]
    assert _period_report_text(txs, "Сегодня", texts) == "Сегодня: 3 400 ₸ по 1 категории."


def test_period_report_single_currency_multiple_categories(texts: Texts) -> None:
    txs = [
        make_expense(amount=3400, category_id="c1"),
        make_expense(amount=9000, category_id="c2"),
        make_expense(amount=100, category_id="c3"),
    ]
    assert _period_report_text(txs, "Сегодня", texts) == "Сегодня: 12 500 ₸ по 3 категориям."


def test_period_report_ignores_non_expense_types(texts: Texts) -> None:
    txs = [
        make_expense(amount=3400, category_id="c1"),
        make_expense(type="income", amount=50000, category_id=None),
    ]
    assert _period_report_text(txs, "Сегодня", texts) == "Сегодня: 3 400 ₸ по 1 категории."


def test_period_report_splits_by_currency(texts: Texts) -> None:
    txs = [
        make_expense(amount=3400, currency="KZT", category_id="c1"),
        make_expense(amount=500, currency="RUB", category_id="c2"),
    ]
    assert (
        _period_report_text(txs, "За неделю", texts)
        == "За неделю: 3 400 ₸ по 1 категории; 500 ₽ по 1 категории."
    )


# --- _balances_text ---


def test_balances_text_empty(texts: Texts) -> None:
    config = make_config(Path("unused"))
    assert _balances_text([], texts, config) == "Нет активных источников."


def test_balances_text_sorted_descending_with_totals(texts: Texts) -> None:
    config = make_config(Path("unused"))
    kaspi = sources.Source(id="s1", user_id=1, name="Kaspi", type="card", currency="KZT", kind="asset", owner=None, credit_limit=None, is_active=True)
    bcc = sources.Source(id="s2", user_id=1, name="bcc", type="card", currency="KZT", kind="asset", owner=None, credit_limit=None, is_active=True)
    tbank = sources.Source(id="s3", user_id=1, name="Т-банк", type="card", currency="RUB", kind="asset", owner=None, credit_limit=None, is_active=True)
    text = _balances_text([(kaspi, 71000), (bcc, -39000), (tbank, 500.5)], texts, config)
    assert text == (
        "Остатки. ⚖️\n\n"
        "Kaspi: 71 000 ₸\n"
        "Т-банк: 500.50 ₽\n"
        "bcc: -39 000 ₸\n\n"
        "Всего: 32 000 ₸, 500.50 ₽."
    )


def test_balances_text_separates_debts_from_assets(texts: Texts) -> None:
    """Долговые источники (kind='debt') не входят в 'Всего' активов — отдельный блок
    со своим подытогом (см. UPGRADE_spec.md, 9.5)."""
    config = make_config(Path("unused"))
    kaspi = sources.Source(
        id="s1", user_id=1, name="Kaspi", type="card", currency="KZT",
        kind="asset", owner=None, credit_limit=None, is_active=True,
    )
    debt = sources.Source(
        id="s2", user_id=1, name="Т-Кредит", type="Кредит", currency="KZT",
        kind="debt", owner=None, credit_limit=None, is_active=True,
    )
    text = _balances_text([(kaspi, 71000), (debt, -30000)], texts, config)
    assert text == (
        "Остатки. ⚖️\n\n"
        "Kaspi: 71 000 ₸\n\n"
        "Всего: 71 000 ₸.\n\n"
        "Долги. ⚖️\n\n"
        "Т-Кредит: -30 000 ₸\n\n"
        "Итого долгов: -30 000 ₸."
    )


def test_balances_text_groups_by_owner_when_present(texts: Texts) -> None:
    """Профиль "домохозяйство": источники с владельцем группируются по человеку внутри
    каждого блока (активы/долги), в порядке config.people, остальные метки — после."""
    config = replace(make_config(Path("unused")), people={1: "Лика", 2: "Ярослав"})
    lika_card = sources.Source(
        id="s1", user_id=1, name="Kaspi Лики", type="card", currency="KZT",
        kind="asset", owner="Лика", credit_limit=None, is_active=True,
    )
    yaroslav_card = sources.Source(
        id="s2", user_id=1, name="Kaspi Ярослава", type="card", currency="KZT",
        kind="asset", owner="Ярослав", credit_limit=None, is_active=True,
    )
    shared_cash = sources.Source(
        id="s3", user_id=1, name="Наличные", type="cash", currency="KZT",
        kind="asset", owner="Семья", credit_limit=None, is_active=True,
    )
    lika_debt = sources.Source(
        id="s4", user_id=1, name="Т-Кредит Лики", type="Кредит", currency="RUB",
        kind="debt", owner="Лика", credit_limit=None, is_active=True,
    )
    text = _balances_text(
        [(lika_card, 50000), (yaroslav_card, 30000), (shared_cash, 15000), (lika_debt, -100000)],
        texts,
        config,
    )
    assert text == (
        "Остатки. ⚖️\n\n"
        "Лика:\n"
        "Kaspi Лики: 50 000 ₸\n"
        "\n"
        "Ярослав:\n"
        "Kaspi Ярослава: 30 000 ₸\n"
        "\n"
        "Семья:\n"
        "Наличные: 15 000 ₸\n\n"
        "Всего: 95 000 ₸.\n\n"
        "Долги. ⚖️\n\n"
        "Лика:\n"
        "Т-Кредит Лики: -100 000 ₽\n\n"
        "Итого долгов: -100 000 ₽."
    )


# --- _balances_text: 3-секционная схема (Pantalone: debt_wallet_keywords/debt_obligation_keywords) ---


def _pantalone_style_config() -> Config:
    return replace(
        make_config(Path("unused")),
        debt_wallet_keywords=("кредитка", "кубышка"),
        debt_obligation_keywords=("долг",),
    )


def test_balances_text_debt_wallet_with_limit_shows_breakdown(texts: Texts) -> None:
    config = _pantalone_style_config()
    card = sources.Source(
        id="s1", user_id=1, name="Tinkoff Кредитка Лики", type="Кредитная карта", currency="RUB",
        kind="debt", owner=None, credit_limit=185000, is_active=True,
    )
    text = _balances_text([(card, -55000)], texts, config)
    assert text == (
        "Долговые кошельки. 💳\n\n"
        "«Tinkoff Кредитка Лики»\n"
        "Кредитный лимит: 185 000 ₽\n"
        "На счету: 130 000 ₽\n"
        "К оплате: 55 000 ₽"
    )


def test_balances_text_debt_wallet_without_limit_shows_fallback(texts: Texts) -> None:
    config = _pantalone_style_config()
    card = sources.Source(
        id="s1", user_id=1, name="Tinkoff Кредитка Лики", type="Кредитная карта", currency="RUB",
        kind="debt", owner=None, credit_limit=None, is_active=True,
    )
    text = _balances_text([(card, -55000)], texts, config)
    assert text == (
        "Долговые кошельки. 💳\n\n"
        "«Tinkoff Кредитка Лики»\n"
        "Остаток: -55 000 ₽ (лимит не задан — /sources)"
    )


def test_balances_text_debt_obligations_have_no_total(texts: Texts) -> None:
    """Пункт запроса: убрать некорректный общий итог у долговых источников."""
    config = _pantalone_style_config()
    debt = sources.Source(
        id="s1", user_id=1, name="Долг Роме", type="Долг человеку", currency="RUB",
        kind="debt", owner="Семья", credit_limit=None, is_active=True,
    )
    text = _balances_text([(debt, -15000)], texts, config)
    assert text == "Долги. ⚖️\n\nСемья:\nДолг Роме: -15 000 ₽"
    assert "Итого" not in text


def test_balances_text_debt_source_matching_neither_keyword_is_excluded(texts: Texts) -> None:
    """Источники типа 'Кредит' (например installment-кредиты, теперь в отдельных
    Google-листах) не подходят ни под кошельки, ни под обязательства — не показываются."""
    config = _pantalone_style_config()
    installment = sources.Source(
        id="s1", user_id=1, name="Т-Кредит Лики", type="Кредит", currency="RUB",
        kind="debt", owner="Лика", credit_limit=None, is_active=True,
    )
    text = _balances_text([(installment, -300000)], texts, config)
    assert text == "Нет активных источников."


def test_balances_text_three_sections_together(texts: Texts) -> None:
    config = _pantalone_style_config()
    cash = sources.Source(
        id="s1", user_id=1, name="Наличные", type="Наличные", currency="KZT",
        kind="asset", owner="Семья", credit_limit=None, is_active=True,
    )
    card = sources.Source(
        id="s2", user_id=1, name="Т-Кубышка Лики", type="Кредитный лимит", currency="RUB",
        kind="debt", owner="Лика", credit_limit=25000, is_active=True,
    )
    debt = sources.Source(
        id="s3", user_id=1, name="Долг Артёму", type="Долг человеку", currency="RUB",
        kind="debt", owner="Семья", credit_limit=None, is_active=True,
    )
    installment = sources.Source(
        id="s4", user_id=1, name="М-Кредит Лики", type="Кредит", currency="RUB",
        kind="debt", owner="Лика", credit_limit=None, is_active=True,
    )
    text = _balances_text(
        [(cash, 10000), (card, -5000), (debt, -10000), (installment, -300000)], texts, config
    )
    assert text == (
        "Остатки. ⚖️\n\n"
        "Семья:\n"
        "Наличные: 10 000 ₸\n\n"
        "Всего: 10 000 ₸.\n\n"
        "Долговые кошельки. 💳\n\n"
        "Лика:\n"
        "«Т-Кубышка Лики»\n"
        "Кредитный лимит: 25 000 ₽\n"
        "На счету: 20 000 ₽\n"
        "К оплате: 5 000 ₽\n\n"
        "Долги. ⚖️\n\n"
        "Семья:\n"
        "Долг Артёму: -10 000 ₽"
    )


# --- _transaction_summary ---


def test_transaction_summary_expense(texts: Texts) -> None:
    tx = make_expense(amount=3400, currency="KZT")
    text = _transaction_summary(tx, texts, source_name="Kaspi", category_name="Продукты")
    assert text == "💸 3 400 ₸ — Продукты (Kaspi)"


def test_transaction_summary_income(texts: Texts) -> None:
    tx = make_expense(type="income", amount=50000, category_id=None)
    text = _transaction_summary(tx, texts, source_name="Kaspi")
    assert text == "💰 50 000 ₸ — Kaspi"


def test_transaction_summary_transfer_same_currency(texts: Texts) -> None:
    tx = make_expense(
        type="transfer",
        amount=1000,
        category_id=None,
        target_amount=1000,
        target_currency="KZT",
    )
    text = _transaction_summary(tx, texts, source_name="Kaspi", target_name="Наличные")
    assert text == "🔄 1 000 ₸ Kaspi → Наличные"


def test_transaction_summary_transfer_cross_currency(texts: Texts) -> None:
    tx = make_expense(
        type="transfer",
        amount=1000,
        currency="KZT",
        category_id=None,
        target_amount=5,
        target_currency="RUB",
    )
    text = _transaction_summary(tx, texts, source_name="Kaspi", target_name="Т-банк")
    assert text == "🔄 1 000 ₸ Kaspi → 5 ₽ Т-банк"


def test_transaction_summary_adjustment_positive(texts: Texts) -> None:
    tx = make_expense(type="adjustment", amount=5000, category_id=None)
    text = _transaction_summary(tx, texts, source_name="Kaspi")
    assert text == "⚖️ +5 000 ₸ — Kaspi"


def test_transaction_summary_adjustment_negative(texts: Texts) -> None:
    tx = make_expense(type="adjustment", amount=-3000, category_id=None)
    text = _transaction_summary(tx, texts, source_name="Kaspi")
    assert text == "⚖️ -3 000 ₸ — Kaspi"


# --- edit_last apply helpers ---


async def test_apply_amount_edit(tmp_path: Path, texts: Texts) -> None:
    db_path = tmp_path / "test.db"
    init_db(str(db_path))
    config = make_config(db_path)

    with connect(str(db_path)) as conn:
        source = sources.create_source(conn, 1, "Kaspi", "card", "KZT")
        category = categories.create_category(conn, 1, "Продукты")
        tx = insert_transaction(
            conn,
            user_id=1,
            type="expense",
            amount=1000,
            currency="KZT",
            source_id=source.id,
            category_id=category.id,
        )

    state = make_state()
    await state.update_data(transaction_id=tx.id, currency="KZT")
    text = await _apply_amount_edit(state, config, texts, 2500)

    assert text == "Сумма обновлена: 2 500 ₸. ✏️"
    with connect(str(db_path)) as conn:
        row = conn.execute("SELECT amount FROM transactions WHERE id = ?", (tx.id,)).fetchone()
        assert row["amount"] == 2500
    assert await state.get_state() is None


async def test_apply_comment_edit(tmp_path: Path, texts: Texts) -> None:
    db_path = tmp_path / "test.db"
    init_db(str(db_path))
    config = make_config(db_path)

    with connect(str(db_path)) as conn:
        source = sources.create_source(conn, 1, "Kaspi", "card", "KZT")
        tx = insert_transaction(
            conn, user_id=1, type="income", amount=1000, currency="KZT", source_id=source.id
        )

    state = make_state()
    await state.update_data(transaction_id=tx.id, currency="KZT")
    text = await _apply_comment_edit(state, config, texts, "зарплата")

    assert text == "Комментарий обновлён: «зарплата». 💬"
    with connect(str(db_path)) as conn:
        row = conn.execute("SELECT comment FROM transactions WHERE id = ?", (tx.id,)).fetchone()
        assert row["comment"] == "зарплата"


async def test_apply_category_edit(tmp_path: Path, texts: Texts) -> None:
    db_path = tmp_path / "test.db"
    init_db(str(db_path))
    config = make_config(db_path)

    with connect(str(db_path)) as conn:
        source = sources.create_source(conn, 1, "Kaspi", "card", "KZT")
        category_a = categories.create_category(conn, 1, "Продукты")
        category_b = categories.create_category(conn, 1, "Транспорт")
        tx = insert_transaction(
            conn,
            user_id=1,
            type="expense",
            amount=1000,
            currency="KZT",
            source_id=source.id,
            category_id=category_a.id,
        )

    state = make_state()
    await state.update_data(transaction_id=tx.id, currency="KZT")
    text = await _apply_category_edit(state, config, texts, category_b.id)

    assert text == "Категория обновлена: «Транспорт». 🗂"
    with connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT category_id FROM transactions WHERE id = ?", (tx.id,)
        ).fetchone()
        assert row["category_id"] == category_b.id
