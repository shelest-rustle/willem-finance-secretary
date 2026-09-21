from __future__ import annotations

from pathlib import Path

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from dataclasses import replace

from willem.bot.handlers.transfer import _record_transfer
from willem.config import Config
from willem.texts import Texts
from willem.db import sources
from willem.db.connection import connect, init_db
from willem.db.transactions import get_source_balance, get_transaction, insert_transaction


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


async def test_transfer_same_currency(tmp_path: Path, texts: Texts) -> None:
    db_path = tmp_path / "test.db"
    init_db(str(db_path))
    config = make_config(db_path)

    with connect(str(db_path)) as conn:
        cash = sources.create_source(conn, 1, "Наличные", "cash", "KZT")
        kaspi = sources.create_source(conn, 1, "Kaspi", "card", "KZT")
        insert_transaction(
            conn, user_id=1, type="income", amount=50000, currency="KZT", source_id=cash.id
        )

    state = make_state()
    await state.update_data(
        amount=5000, source_from_id=cash.id, source_to_id=kaspi.id, final_target_amount=5000
    )

    text = await _record_transfer(state, config, texts, 1, comment=None)

    assert text == "Записал перевод: 5 000 ₸ Наличные → Kaspi. 🔄"
    assert await state.get_state() is None

    with connect(str(db_path)) as conn:
        assert get_source_balance(conn, cash.id) == 45000
        assert get_source_balance(conn, kaspi.id) == 5000


async def test_transfer_cross_currency(tmp_path: Path, texts: Texts) -> None:
    db_path = tmp_path / "test.db"
    init_db(str(db_path))
    config = make_config(db_path)

    with connect(str(db_path)) as conn:
        tbank = sources.create_source(conn, 1, "Т-банк", "card", "RUB")
        kaspi = sources.create_source(conn, 1, "Kaspi", "card", "KZT")
        insert_transaction(
            conn, user_id=1, type="income", amount=20000, currency="RUB", source_id=tbank.id
        )

    state = make_state()
    await state.update_data(
        amount=10000, source_from_id=tbank.id, source_to_id=kaspi.id, final_target_amount=25000
    )

    text = await _record_transfer(state, config, texts, 1, comment=None)

    assert text == "Записал перевод: 10 000 ₽ Т-банк → 25 000 ₸ Kaspi. 🔄"

    with connect(str(db_path)) as conn:
        assert get_source_balance(conn, tbank.id) == 10000
        assert get_source_balance(conn, kaspi.id) == 25000


async def test_transfer_fills_who_from_real_sender(tmp_path: Path, texts: Texts) -> None:
    db_path = tmp_path / "test.db"
    init_db(str(db_path))
    config = replace(make_config(db_path), people={1: "Ярослав"})

    with connect(str(db_path)) as conn:
        cash = sources.create_source(conn, 1, "Наличные", "cash", "KZT")
        kaspi = sources.create_source(conn, 1, "Kaspi", "card", "KZT")
        insert_transaction(
            conn, user_id=1, type="income", amount=50000, currency="KZT", source_id=cash.id
        )

    state = make_state()
    await state.update_data(
        amount=5000, source_from_id=cash.id, source_to_id=kaspi.id, final_target_amount=5000
    )

    await _record_transfer(state, config, texts, 1, comment=None)

    with connect(str(db_path)) as conn:
        recent = conn.execute(
            "SELECT id FROM transactions WHERE type = 'transfer' ORDER BY created_at_utc DESC LIMIT 1"
        ).fetchone()
        tx = get_transaction(conn, recent["id"])
        assert tx.who == "Ярослав"
