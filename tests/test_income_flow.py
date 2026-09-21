from __future__ import annotations

from pathlib import Path

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from willem.bot.handlers.income import _record_income
from willem.config import Config
from willem.texts import Texts
from willem.db import sources
from willem.db.connection import connect, init_db
from willem.db.transactions import get_source_balance


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


async def test_record_income(tmp_path: Path, texts: Texts) -> None:
    db_path = tmp_path / "test.db"
    init_db(str(db_path))
    config = make_config(db_path)

    with connect(str(db_path)) as conn:
        source = sources.create_source(conn, 1, "Kaspi", "card", "KZT")

    state = make_state()
    await state.update_data(amount=100000, source_id=source.id)

    text = await _record_income(state, config, texts, 1, comment="зарплата")

    assert text == "Пополнил: +100 000 ₸ — Kaspi. 💰"
    assert await state.get_state() is None

    with connect(str(db_path)) as conn:
        assert get_source_balance(conn, source.id) == 100000
