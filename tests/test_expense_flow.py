from __future__ import annotations

from pathlib import Path

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from willem.bot.handlers.expense import _record_expense
from willem.config import Config
from willem.texts import Texts
from willem.db import categories, sources
from willem.db.connection import connect, init_db


def make_config(db_path: Path) -> Config:
    return Config(
        profile_name="willem",
        persona="Виллем",
        bot_token="123:fake",
        owner_telegram_id=999,
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
        credit_reminder_currency="RUB",
    )


def make_state() -> FSMContext:
    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=1, user_id=1)
    return FSMContext(storage=storage, key=key)


async def test_record_expense_without_limit(tmp_path: Path, texts: Texts) -> None:
    db_path = tmp_path / "test.db"
    init_db(str(db_path))
    config = make_config(db_path)

    with connect(str(db_path)) as conn:
        source = sources.create_source(conn, 1, "Kaspi", "card", "KZT")
        category = categories.create_category(conn, 1, "Продукты")

    state = make_state()
    await state.update_data(amount=3400, source_id=source.id, category_id=category.id)

    text = await _record_expense(state, config, texts, 1, comment="test")

    assert text == "Записал: 3 400 ₸ — Продукты. 💸"
    assert await state.get_state() is None


async def test_record_expense_near_limit(tmp_path: Path, texts: Texts) -> None:
    db_path = tmp_path / "test.db"
    init_db(str(db_path))
    config = make_config(db_path)

    with connect(str(db_path)) as conn:
        source = sources.create_source(conn, 1, "Kaspi", "card", "KZT")
        category = categories.create_category(
            conn, 1, "Продукты", limit_amount=50000, limit_period="month"
        )

    state = make_state()
    await state.update_data(amount=42000, source_id=source.id, category_id=category.id)

    text = await _record_expense(state, config, texts, 1, comment=None)

    assert text == "Записал: 42 000 ₸ — Продукты. По категории за месяц: 42 000 из 50 000 ₸. 💸"


async def test_record_expense_over_limit(tmp_path: Path, texts: Texts) -> None:
    db_path = tmp_path / "test.db"
    init_db(str(db_path))
    config = make_config(db_path)

    with connect(str(db_path)) as conn:
        source = sources.create_source(conn, 1, "Kaspi", "card", "KZT")
        category = categories.create_category(
            conn, 1, "Продукты", limit_amount=50000, limit_period="month"
        )

    state = make_state()
    await state.update_data(amount=48000, source_id=source.id, category_id=category.id)
    await _record_expense(state, config, texts, 1, comment=None)

    state = make_state()
    await state.update_data(amount=3200, source_id=source.id, category_id=category.id)
    text = await _record_expense(state, config, texts, 1, comment=None)

    assert text == "Записал: 3 200 ₸ — Продукты. По категории за месяц: 51 200 из 50 000 ₸. 💸"


async def test_record_expense_ignores_other_currency_in_limit(tmp_path: Path, texts: Texts) -> None:
    db_path = tmp_path / "test.db"
    init_db(str(db_path))
    config = make_config(db_path)

    with connect(str(db_path)) as conn:
        kzt_source = sources.create_source(conn, 1, "Kaspi", "card", "KZT")
        rub_source = sources.create_source(conn, 1, "Т-банк", "card", "RUB")
        category = categories.create_category(
            conn, 1, "Быт", limit_amount=50000, limit_period="month"
        )

    state = make_state()
    await state.update_data(amount=45000, source_id=rub_source.id, category_id=category.id)
    await _record_expense(state, config, texts, 1, comment=None)

    state = make_state()
    await state.update_data(amount=1000, source_id=kzt_source.id, category_id=category.id)
    text = await _record_expense(state, config, texts, 1, comment=None)

    assert text == "Записал: 1 000 ₸ — Быт. 💸"
