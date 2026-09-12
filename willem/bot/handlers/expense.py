from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from willem.bot.amount import EXPENSE_AMOUNT_RE, parse_amount
from willem.bot.keyboards import SKIP_LABEL, build_choice_keyboard, build_single_button_keyboard
from willem.config import Config
from willem.db import categories as categories_db
from willem.db import sources as sources_db
from willem.db import transactions as transactions_db
from willem.db.categories import Category
from willem.db.connection import connect
from willem.formatting import currency_symbol, format_amount, period_word
from willem.sheets_sync import sync_after_insert
from willem.texts import Texts
from willem.timeutil import period_bounds

router = Router(name="expense")

SOURCE_PREFIX = "exp_src"
CATEGORY_PREFIX = "exp_cat"
SUBCATEGORY_PREFIX = "exp_subcat"
SKIP_SUBCATEGORY_CB = "exp_subcat_skip"
SKIP_COMMENT_CB = "exp_comment_skip"
WHO_TOGGLE_CB = "exp_who_toggle"

FAMILY_WHO = "Семья"


class ExpenseFlow(StatesGroup):
    choosing_source = State()
    choosing_category = State()
    choosing_subcategory = State()
    entering_comment = State()


def _category_keyboard(
    categories: list[Category], config: Config, user_id: int, current_who: str | None
) -> InlineKeyboardMarkup:
    extra = []
    auto_name = config.people.get(user_id)
    if auto_name:
        label = FAMILY_WHO if current_who == FAMILY_WHO else auto_name
        extra = [(label, WHO_TOGGLE_CB)]
    return build_choice_keyboard(
        [(c.id, c.name) for c in categories], callback_prefix=CATEGORY_PREFIX, extra_buttons=extra
    )


@router.message(StateFilter(None), F.text.regexp(EXPENSE_AMOUNT_RE))
async def start_expense(message: Message, state: FSMContext, config: Config, texts: Texts) -> None:
    amount = parse_amount(message.text)
    with connect(config.db_path) as conn:
        active_sources = sources_db.list_sources(conn, message.from_user.id)

    if not active_sources:
        await message.answer(texts.get("common.no_active_sources"))
        return

    await state.update_data(amount=amount)
    await state.set_state(ExpenseFlow.choosing_source)
    keyboard = build_choice_keyboard(
        [(s.id, s.name) for s in active_sources], callback_prefix=SOURCE_PREFIX
    )
    await message.answer(texts.get("common.choose_source_expense"), reply_markup=keyboard)


@router.callback_query(ExpenseFlow.choosing_source, F.data.startswith(f"{SOURCE_PREFIX}:"))
async def choose_source(
    callback: CallbackQuery, state: FSMContext, config: Config, texts: Texts
) -> None:
    source_id = callback.data.removeprefix(f"{SOURCE_PREFIX}:")
    with connect(config.db_path) as conn:
        active_categories = categories_db.list_categories(conn, callback.from_user.id)

    if not active_categories:
        await callback.message.edit_text(texts.get("common.no_active_categories"))
        await state.clear()
        await callback.answer()
        return

    await state.update_data(source_id=source_id)
    await state.set_state(ExpenseFlow.choosing_category)
    keyboard = _category_keyboard(active_categories, config, callback.from_user.id, None)
    await callback.message.edit_text(texts.get("common.choose_category"), reply_markup=keyboard)
    await callback.answer()


@router.callback_query(ExpenseFlow.choosing_category, F.data == WHO_TOGGLE_CB)
async def toggle_who(callback: CallbackQuery, state: FSMContext, config: Config) -> None:
    data = await state.get_data()
    new_who = None if data.get("who") == FAMILY_WHO else FAMILY_WHO
    await state.update_data(who=new_who)
    with connect(config.db_path) as conn:
        active_categories = categories_db.list_categories(conn, callback.from_user.id)
    keyboard = _category_keyboard(active_categories, config, callback.from_user.id, new_who)
    await callback.message.edit_reply_markup(reply_markup=keyboard)
    await callback.answer()


@router.callback_query(ExpenseFlow.choosing_category, F.data.startswith(f"{CATEGORY_PREFIX}:"))
async def choose_category(
    callback: CallbackQuery, state: FSMContext, config: Config, texts: Texts
) -> None:
    category_id = callback.data.removeprefix(f"{CATEGORY_PREFIX}:")
    await state.update_data(category_id=category_id)
    with connect(config.db_path) as conn:
        subcategories = categories_db.list_categories(
            conn, callback.from_user.id, parent_id=category_id
        )
    if subcategories:
        await state.set_state(ExpenseFlow.choosing_subcategory)
        keyboard = build_choice_keyboard(
            [(c.id, c.name) for c in subcategories],
            callback_prefix=SUBCATEGORY_PREFIX,
            extra_buttons=[(SKIP_LABEL, SKIP_SUBCATEGORY_CB)],
        )
        await callback.message.edit_text(texts.get("common.choose_subcategory"), reply_markup=keyboard)
    else:
        await _prompt_comment(callback.message, state, config, texts)
    await callback.answer()


@router.callback_query(ExpenseFlow.choosing_subcategory, F.data.startswith(f"{SUBCATEGORY_PREFIX}:"))
async def choose_subcategory(
    callback: CallbackQuery, state: FSMContext, config: Config, texts: Texts
) -> None:
    subcategory_id = callback.data.removeprefix(f"{SUBCATEGORY_PREFIX}:")
    await state.update_data(subcategory_id=subcategory_id)
    await _prompt_comment(callback.message, state, config, texts)
    await callback.answer()


@router.callback_query(ExpenseFlow.choosing_subcategory, F.data == SKIP_SUBCATEGORY_CB)
async def skip_subcategory(
    callback: CallbackQuery, state: FSMContext, config: Config, texts: Texts
) -> None:
    await _prompt_comment(callback.message, state, config, texts)
    await callback.answer()


async def _prompt_comment(message: Message, state: FSMContext, config: Config, texts: Texts) -> None:
    await state.set_state(ExpenseFlow.entering_comment)
    keyboard = (
        build_single_button_keyboard(SKIP_LABEL, SKIP_COMMENT_CB) if config.optional_comment else None
    )
    await message.edit_text(texts.get("expense.enter_comment"), reply_markup=keyboard)


@router.message(ExpenseFlow.entering_comment, F.text)
async def enter_comment(message: Message, state: FSMContext, config: Config, texts: Texts) -> None:
    text = await _record_expense(
        state, config, texts, message.from_user.id, comment=message.text
    )
    await message.answer(text)


@router.callback_query(ExpenseFlow.entering_comment, F.data == SKIP_COMMENT_CB)
async def skip_comment(callback: CallbackQuery, state: FSMContext, config: Config, texts: Texts) -> None:
    text = await _record_expense(state, config, texts, callback.from_user.id, comment=None)
    await callback.message.edit_text(text)
    await callback.answer()


async def _record_expense(
    state: FSMContext, config: Config, texts: Texts, user_id: int, *, comment: str | None
) -> str:
    data = await state.get_data()
    amount = data["amount"]
    source_id = data["source_id"]
    category_id = data["category_id"]
    subcategory_id = data.get("subcategory_id")
    who = data.get("who") or config.people.get(user_id)
    await state.clear()

    with connect(config.db_path) as conn:
        source = sources_db.get_source(conn, source_id)
        category = categories_db.get_category(conn, category_id)
        subcategory = categories_db.get_category(conn, subcategory_id) if subcategory_id else None
        tx = transactions_db.insert_transaction(
            conn,
            user_id=user_id,
            type="expense",
            amount=amount,
            currency=source.currency,
            source_id=source_id,
            category_id=category_id,
            subcategory_id=subcategory_id,
            who=who,
            comment=comment,
        )

        symbol = currency_symbol(source.currency)
        text = texts.get(
            "expense.recorded", amount=format_amount(amount), symbol=symbol, category=category.name
        )

        if category.limit_amount is not None:
            start, end = period_bounds(category.limit_period, config.timezone)
            total = transactions_db.sum_expenses_by_category(
                conn, category_id, source.currency, start, end
            )
            if total >= category.limit_amount * 0.8:
                text += texts.get(
                    "expense.limit_warning",
                    period=period_word(category.limit_period),
                    total=format_amount(total),
                    limit=format_amount(category.limit_amount),
                    symbol=symbol,
                )

    suffix = await sync_after_insert(
        config,
        texts,
        user_id,
        tx,
        source_name=source.name,
        category_name=category.name,
        subcategory_name=subcategory.name if subcategory else None,
    )
    return f"{text}.{suffix}{texts.get('expense.done_suffix')}"
