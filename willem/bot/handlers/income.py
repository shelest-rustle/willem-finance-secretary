from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from willem.bot.amount import INCOME_AMOUNT_RE, parse_amount
from willem.bot.keyboards import build_choice_keyboard
from willem.config import Config
from willem.db import sources as sources_db
from willem.db import transactions as transactions_db
from willem.db.connection import connect
from willem.formatting import currency_symbol, format_amount
from willem.sheets_sync import sync_after_insert

router = Router(name="income")

SOURCE_PREFIX = "inc_src"


class IncomeFlow(StatesGroup):
    choosing_source = State()
    entering_comment = State()


@router.message(StateFilter(None), F.text.regexp(INCOME_AMOUNT_RE))
async def start_income(message: Message, state: FSMContext, config: Config) -> None:
    amount = parse_amount(message.text)
    with connect(config.db_path) as conn:
        active_sources = sources_db.list_sources(conn, message.from_user.id)

    if not active_sources:
        await message.answer("Нет активных источников. Добавьте через /sources.")
        return

    await state.update_data(amount=amount)
    await state.set_state(IncomeFlow.choosing_source)
    keyboard = build_choice_keyboard(
        [(s.id, s.name) for s in active_sources], callback_prefix=SOURCE_PREFIX
    )
    await message.answer("Источник зачисления. 💳", reply_markup=keyboard)


@router.callback_query(IncomeFlow.choosing_source, F.data.startswith(f"{SOURCE_PREFIX}:"))
async def choose_source(callback: CallbackQuery, state: FSMContext) -> None:
    source_id = callback.data.removeprefix(f"{SOURCE_PREFIX}:")
    await state.update_data(source_id=source_id)
    await state.set_state(IncomeFlow.entering_comment)
    await callback.message.edit_text("Оставьте комментарий к доходу. 💬")
    await callback.answer()


@router.message(IncomeFlow.entering_comment, F.text)
async def enter_comment(message: Message, state: FSMContext, config: Config) -> None:
    text = await _record_income(state, config, message.from_user.id, comment=message.text)
    await message.answer(text)


async def _record_income(
    state: FSMContext, config: Config, user_id: int, *, comment: str | None
) -> str:
    data = await state.get_data()
    amount = data["amount"]
    source_id = data["source_id"]
    await state.clear()

    with connect(config.db_path) as conn:
        source = sources_db.get_source(conn, source_id)
        tx = transactions_db.insert_transaction(
            conn,
            user_id=user_id,
            type="income",
            amount=amount,
            currency=source.currency,
            source_id=source_id,
            comment=comment,
        )

    symbol = currency_symbol(source.currency)
    text = f"Пополнил: +{format_amount(amount)} {symbol} — {source.name}"
    suffix = await sync_after_insert(config, user_id, tx, source_name=source.name)
    return f"{text}.{suffix} 💰"
