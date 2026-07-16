from __future__ import annotations

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


def make_config(db_path: Path) -> Config:
    return Config(
        bot_token="123:fake",
        owner_telegram_id=1,
        allowed_telegram_ids=(1,),
        db_path=str(db_path),
        google_sheets_credentials_path="",
        google_sheets_spreadsheet_id="",
        timezone="Asia/Almaty",
        log_level="INFO",
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
        comment=None,
        created_at_utc="2026-07-16T10:00:00+00:00",
        synced=False,
        deleted=False,
    )
    base.update(overrides)
    return Transaction(**base)


# --- _period_report_text ---


def test_period_report_no_expenses() -> None:
    assert _period_report_text([], "Сегодня") == "Сегодня: трат нет."


def test_period_report_single_currency_single_category() -> None:
    txs = [make_expense(amount=3400, category_id="c1")]
    assert _period_report_text(txs, "Сегодня") == "Сегодня: 3 400 ₸ по 1 категории."


def test_period_report_single_currency_multiple_categories() -> None:
    txs = [
        make_expense(amount=3400, category_id="c1"),
        make_expense(amount=9000, category_id="c2"),
        make_expense(amount=100, category_id="c3"),
    ]
    assert _period_report_text(txs, "Сегодня") == "Сегодня: 12 500 ₸ по 3 категориям."


def test_period_report_ignores_non_expense_types() -> None:
    txs = [
        make_expense(amount=3400, category_id="c1"),
        make_expense(type="income", amount=50000, category_id=None),
    ]
    assert _period_report_text(txs, "Сегодня") == "Сегодня: 3 400 ₸ по 1 категории."


def test_period_report_splits_by_currency() -> None:
    txs = [
        make_expense(amount=3400, currency="KZT", category_id="c1"),
        make_expense(amount=500, currency="RUB", category_id="c2"),
    ]
    assert (
        _period_report_text(txs, "За неделю")
        == "За неделю: 3 400 ₸ по 1 категории; 500 ₽ по 1 категории."
    )


# --- _balances_text ---


def test_balances_text_empty() -> None:
    assert _balances_text([]) == "Нет активных источников."


def test_balances_text_sorted_descending_with_totals() -> None:
    kaspi = sources.Source(id="s1", user_id=1, name="Kaspi", type="card", currency="KZT", is_active=True)
    bcc = sources.Source(id="s2", user_id=1, name="bcc", type="card", currency="KZT", is_active=True)
    tbank = sources.Source(id="s3", user_id=1, name="Т-банк", type="card", currency="RUB", is_active=True)
    text = _balances_text([(kaspi, 71000), (bcc, -39000), (tbank, 500.5)])
    assert text == (
        "Остатки. ⚖️\n\n"
        "Kaspi: 71 000 ₸\n"
        "Т-банк: 500.50 ₽\n"
        "bcc: -39 000 ₸\n\n"
        "Всего: 32 000 ₸, 500.50 ₽."
    )


# --- _transaction_summary ---


def test_transaction_summary_expense() -> None:
    tx = make_expense(amount=3400, currency="KZT")
    text = _transaction_summary(tx, source_name="Kaspi", category_name="Продукты")
    assert text == "💸 3 400 ₸ — Продукты (Kaspi)"


def test_transaction_summary_income() -> None:
    tx = make_expense(type="income", amount=50000, category_id=None)
    text = _transaction_summary(tx, source_name="Kaspi")
    assert text == "💰 50 000 ₸ — Kaspi"


def test_transaction_summary_transfer_same_currency() -> None:
    tx = make_expense(
        type="transfer",
        amount=1000,
        category_id=None,
        target_amount=1000,
        target_currency="KZT",
    )
    text = _transaction_summary(tx, source_name="Kaspi", target_name="Наличные")
    assert text == "🔄 1 000 ₸ Kaspi → Наличные"


def test_transaction_summary_transfer_cross_currency() -> None:
    tx = make_expense(
        type="transfer",
        amount=1000,
        currency="KZT",
        category_id=None,
        target_amount=5,
        target_currency="RUB",
    )
    text = _transaction_summary(tx, source_name="Kaspi", target_name="Т-банк")
    assert text == "🔄 1 000 ₸ Kaspi → 5 ₽ Т-банк"


def test_transaction_summary_adjustment_positive() -> None:
    tx = make_expense(type="adjustment", amount=5000, category_id=None)
    text = _transaction_summary(tx, source_name="Kaspi")
    assert text == "⚖️ +5 000 ₸ — Kaspi"


def test_transaction_summary_adjustment_negative() -> None:
    tx = make_expense(type="adjustment", amount=-3000, category_id=None)
    text = _transaction_summary(tx, source_name="Kaspi")
    assert text == "⚖️ -3 000 ₸ — Kaspi"


# --- edit_last apply helpers ---


async def test_apply_amount_edit(tmp_path: Path) -> None:
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
    text = await _apply_amount_edit(state, config, 2500)

    assert text == "Сумма обновлена: 2 500 ₸. ✏️"
    with connect(str(db_path)) as conn:
        row = conn.execute("SELECT amount FROM transactions WHERE id = ?", (tx.id,)).fetchone()
        assert row["amount"] == 2500
    assert await state.get_state() is None


async def test_apply_comment_edit(tmp_path: Path) -> None:
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
    text = await _apply_comment_edit(state, config, "зарплата")

    assert text == "Комментарий обновлён: «зарплата». 💬"
    with connect(str(db_path)) as conn:
        row = conn.execute("SELECT comment FROM transactions WHERE id = ?", (tx.id,)).fetchone()
        assert row["comment"] == "зарплата"


async def test_apply_category_edit(tmp_path: Path) -> None:
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
    text = await _apply_category_edit(state, config, category_b.id)

    assert text == "Категория обновлена: «Транспорт». 🗂"
    with connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT category_id FROM transactions WHERE id = ?", (tx.id,)
        ).fetchone()
        assert row["category_id"] == category_b.id
