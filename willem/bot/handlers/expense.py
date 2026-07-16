from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from willem.bot.amount import EXPENSE_AMOUNT_RE, parse_amount
from willem.bot.keyboards import build_choice_keyboard
from willem.config import Config
from willem.db import categories as categories_db
from willem.db import sources as sources_db
from willem.db import transactions as transactions_db
from willem.db.connection import connect
from willem.formatting import currency_symbol, format_amount, period_word
from willem.sheets_sync import sync_after_insert
from willem.timeutil import period_bounds

router = Router(name="expense")

SOURCE_PREFIX = "exp_src"
CATEGORY_PREFIX = "exp_cat"


class ExpenseFlow(StatesGroup):
    choosing_source = State()
    choosing_category = State()
    entering_comment = State()


@router.message(StateFilter(None), F.text.regexp(EXPENSE_AMOUNT_RE))
async def start_expense(message: Message, state: FSMContext, config: Config) -> None:
    amount = parse_amount(message.text)
    with connect(config.db_path) as conn:
        active_sources = sources_db.list_sources(conn, message.from_user.id)

    if not active_sources:
        await message.answer("Нет активных источников. Добавьте через /sources.")
        return

    await state.update_data(amount=amount)
    await state.set_state(ExpenseFlow.choosing_source)
    keyboard = build_choice_keyboard(
        [(s.id, s.name) for s in active_sources], callback_prefix=SOURCE_PREFIX
    )
    await message.answer("Источник списания. 💳", reply_markup=keyboard)


@router.callback_query(ExpenseFlow.choosing_source, F.data.startswith(f"{SOURCE_PREFIX}:"))
async def choose_source(callback: CallbackQuery, state: FSMContext, config: Config) -> None:
    source_id = callback.data.removeprefix(f"{SOURCE_PREFIX}:")
    with connect(config.db_path) as conn:
        active_categories = categories_db.list_categories(conn, callback.from_user.id)

    if not active_categories:
        await callback.message.edit_text("Нет активных категорий. Добавьте через /categories.")
        await state.clear()
        await callback.answer()
        return

    await state.update_data(source_id=source_id)
    await state.set_state(ExpenseFlow.choosing_category)
    keyboard = build_choice_keyboard(
        [(c.id, c.name) for c in active_categories], callback_prefix=CATEGORY_PREFIX
    )
    await callback.message.edit_text("Выберите категорию. 🗂", reply_markup=keyboard)
    await callback.answer()


@router.callback_query(ExpenseFlow.choosing_category, F.data.startswith(f"{CATEGORY_PREFIX}:"))
async def choose_category(callback: CallbackQuery, state: FSMContext) -> None:
    category_id = callback.data.removeprefix(f"{CATEGORY_PREFIX}:")
    await state.update_data(category_id=category_id)
    await state.set_state(ExpenseFlow.entering_comment)
    await callback.message.edit_text("Оставьте комментарий к расходу. 💬")
    await callback.answer()


@router.message(ExpenseFlow.entering_comment, F.text)
async def enter_comment(message: Message, state: FSMContext, config: Config) -> None:
    text = await _record_expense(state, config, message.from_user.id, comment=message.text)
    await message.answer(text)


async def _record_expense(
    state: FSMContext, config: Config, user_id: int, *, comment: str | None
) -> str:
    data = await state.get_data()
    amount = data["amount"]
    source_id = data["source_id"]
    category_id = data["category_id"]
    await state.clear()

    with connect(config.db_path) as conn:
        source = sources_db.get_source(conn, source_id)
        category = categories_db.get_category(conn, category_id)
        tx = transactions_db.insert_transaction(
            conn,
            user_id=user_id,
            type="expense",
            amount=amount,
            currency=source.currency,
            source_id=source_id,
            category_id=category_id,
            comment=comment,
        )

        symbol = currency_symbol(source.currency)
        text = f"Записал: {format_amount(amount)} {symbol} — {category.name}"

        if category.limit_amount is not None:
            start, end = period_bounds(category.limit_period, config.timezone)
            total = transactions_db.sum_expenses_by_category(
                conn, category_id, source.currency, start, end
            )
            if total >= category.limit_amount * 0.8:
                text += (
                    f". По категории за {period_word(category.limit_period)}: "
                    f"{format_amount(total)} из {format_amount(category.limit_amount)} {symbol}"
                )

    suffix = await sync_after_insert(
        config, user_id, tx, source_name=source.name, category_name=category.name
    )
    return f"{text}.{suffix} 💸"
