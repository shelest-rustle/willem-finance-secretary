from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, or_f
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from willem.bot.amount import EXPENSE_AMOUNT_RE, parse_amount
from willem.bot.keyboards import TRANSFER_BUTTON, build_choice_keyboard
from willem.config import Config
from willem.db import sources as sources_db
from willem.db import transactions as transactions_db
from willem.db.connection import connect
from willem.formatting import currency_symbol, format_amount
from willem.sheets_sync import sync_after_insert

router = Router(name="transfer")

FROM_PREFIX = "trf_from"
TO_PREFIX = "trf_to"


class TransferFlow(StatesGroup):
    entering_amount = State()
    choosing_source_from = State()
    choosing_source_to = State()
    entering_target_amount = State()


@router.message(or_f(Command("transfer"), F.text == TRANSFER_BUTTON))
async def start_transfer(message: Message, state: FSMContext) -> None:
    await state.set_state(TransferFlow.entering_amount)
    await message.answer("Укажите сумму списания. 🔄")


@router.message(TransferFlow.entering_amount, F.text.regexp(EXPENSE_AMOUNT_RE))
async def enter_amount(message: Message, state: FSMContext, config: Config) -> None:
    amount = parse_amount(message.text)
    with connect(config.db_path) as conn:
        active_sources = sources_db.list_sources(conn, message.from_user.id)

    if len(active_sources) < 2:
        await message.answer("Нужно минимум два активных источника для перевода.")
        await state.clear()
        return

    await state.update_data(amount=amount)
    await state.set_state(TransferFlow.choosing_source_from)
    keyboard = build_choice_keyboard(
        [(s.id, s.name) for s in active_sources], callback_prefix=FROM_PREFIX
    )
    await message.answer("Источник списания. 💳", reply_markup=keyboard)


@router.callback_query(TransferFlow.choosing_source_from, F.data.startswith(f"{FROM_PREFIX}:"))
async def choose_source_from(callback: CallbackQuery, state: FSMContext, config: Config) -> None:
    source_from_id = callback.data.removeprefix(f"{FROM_PREFIX}:")
    with connect(config.db_path) as conn:
        active_sources = sources_db.list_sources(conn, callback.from_user.id)

    targets = [s for s in active_sources if s.id != source_from_id]
    if not targets:
        await callback.message.edit_text("Нет второго источника для перевода.")
        await state.clear()
        await callback.answer()
        return

    await state.update_data(source_from_id=source_from_id)
    await state.set_state(TransferFlow.choosing_source_to)
    keyboard = build_choice_keyboard([(s.id, s.name) for s in targets], callback_prefix=TO_PREFIX)
    await callback.message.edit_text("Источник зачисления. 💳", reply_markup=keyboard)
    await callback.answer()


@router.callback_query(TransferFlow.choosing_source_to, F.data.startswith(f"{TO_PREFIX}:"))
async def choose_source_to(callback: CallbackQuery, state: FSMContext, config: Config) -> None:
    source_to_id = callback.data.removeprefix(f"{TO_PREFIX}:")
    data = await state.get_data()

    with connect(config.db_path) as conn:
        source_from = sources_db.get_source(conn, data["source_from_id"])
        source_to = sources_db.get_source(conn, source_to_id)

    await state.update_data(source_to_id=source_to_id)

    if source_from.currency == source_to.currency:
        text = await _record_transfer(
            state, config, callback.from_user.id, target_amount=data["amount"]
        )
        await callback.message.edit_text(text)
        await callback.answer()
        return

    await state.set_state(TransferFlow.entering_target_amount)
    await callback.message.edit_text(f"Сколько зачислилось на {source_to.name}? 💱")
    await callback.answer()


@router.message(TransferFlow.entering_target_amount, F.text.regexp(EXPENSE_AMOUNT_RE))
async def enter_target_amount(message: Message, state: FSMContext, config: Config) -> None:
    target_amount = parse_amount(message.text)
    text = await _record_transfer(
        state, config, message.from_user.id, target_amount=target_amount
    )
    await message.answer(text)


async def _record_transfer(
    state: FSMContext, config: Config, user_id: int, *, target_amount: float
) -> str:
    data = await state.get_data()
    amount = data["amount"]
    source_from_id = data["source_from_id"]
    source_to_id = data["source_to_id"]
    await state.clear()

    with connect(config.db_path) as conn:
        source_from = sources_db.get_source(conn, source_from_id)
        source_to = sources_db.get_source(conn, source_to_id)
        tx = transactions_db.insert_transaction(
            conn,
            user_id=user_id,
            type="transfer",
            amount=amount,
            currency=source_from.currency,
            source_id=source_from_id,
            target_amount=target_amount,
            target_currency=source_to.currency,
            target_source_id=source_to_id,
        )

    symbol_from = currency_symbol(source_from.currency)
    if source_from.currency == source_to.currency:
        text = (
            f"Записал перевод: {format_amount(amount)} {symbol_from} "
            f"{source_from.name} → {source_to.name}"
        )
    else:
        symbol_to = currency_symbol(source_to.currency)
        text = (
            f"Записал перевод: {format_amount(amount)} {symbol_from} {source_from.name} → "
            f"{format_amount(target_amount)} {symbol_to} {source_to.name}"
        )

    suffix = await sync_after_insert(
        config, user_id, tx, source_name=source_from.name, target_name=source_to.name
    )
    return f"{text}.{suffix} 🔄"
