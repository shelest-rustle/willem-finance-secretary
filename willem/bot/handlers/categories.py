from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, or_f
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from willem.bot.amount import EXPENSE_AMOUNT_RE, parse_amount
from willem.bot.keyboards import (
    CATEGORIES_BUTTON,
    build_choice_keyboard,
    build_manage_list_keyboard,
    build_single_button_keyboard,
)
from willem.config import Config, ledger_user_id
from willem.db import categories as categories_db
from willem.db import transactions as transactions_db
from willem.db.categories import Category
from willem.db.connection import connect
from willem.formatting import format_amount, format_currency_totals, period_word
from willem.texts import Texts
from willem.timeutil import period_bounds

router = Router(name="categories")

VIEW_PREFIX = "cat_view"
ADD_CB = "cat_add"
RENAME_PREFIX = "cat_rename"
SETLIMIT_PREFIX = "cat_setlimit"
ARCHIVE_PREFIX = "cat_archive"
BACK_CB = "cat_back"
NO_LIMIT_CB = "cat_no_limit"
PERIOD_PREFIX = "cat_period"
EDIT_NO_LIMIT_CB = "cat_edit_no_limit"
EDIT_PERIOD_PREFIX = "cat_edit_period"

PERIOD_OPTIONS = [("month", "Месяц"), ("week", "Неделя")]


class CategoryFlow(StatesGroup):
    adding_name = State()
    adding_limit_amount = State()
    adding_limit_period = State()
    renaming = State()
    editing_limit_amount = State()
    editing_limit_period = State()


async def _list_categories(config: Config, user_id: int) -> list[Category]:
    with connect(config.db_path) as conn:
        return categories_db.list_categories(conn, ledger_user_id(config, user_id))


def _list_keyboard(items: list[Category]) -> InlineKeyboardMarkup:
    return build_manage_list_keyboard(
        [(c.id, c.name) for c in items], view_prefix=VIEW_PREFIX, add_callback=ADD_CB
    )


def _detail_text(category: Category, month_spent: list[tuple[str, float]], texts: Texts) -> str:
    if category.limit_amount is None:
        text = texts.get("categories.detail_no_limit", name=category.name)
    else:
        text = texts.get(
            "categories.detail_with_limit",
            name=category.name,
            period=period_word(category.limit_period),
            limit=format_amount(category.limit_amount),
        )
    if month_spent:
        text += texts.get("categories.month_spent_suffix", amounts=format_currency_totals(month_spent))
    else:
        text += texts.get("categories.month_spent_none")
    return text


def _detail_keyboard(category_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Переименовать", callback_data=f"{RENAME_PREFIX}:{category_id}")
    builder.button(text="🎯 Лимит", callback_data=f"{SETLIMIT_PREFIX}:{category_id}")
    builder.button(text="🗄 Архивировать", callback_data=f"{ARCHIVE_PREFIX}:{category_id}")
    builder.button(text="‹ Назад", callback_data=BACK_CB)
    builder.adjust(2, 2)
    return builder.as_markup()


@router.message(or_f(Command("categories"), F.text == CATEGORIES_BUTTON))
async def show_categories(message: Message, state: FSMContext, config: Config, texts: Texts) -> None:
    await state.clear()
    items = await _list_categories(config, message.from_user.id)
    await message.answer(texts.get("categories.list_title"), reply_markup=_list_keyboard(items))


@router.callback_query(F.data == BACK_CB)
async def back_to_list(
    callback: CallbackQuery, state: FSMContext, config: Config, texts: Texts
) -> None:
    await state.clear()
    items = await _list_categories(config, callback.from_user.id)
    await callback.message.edit_text(texts.get("categories.list_title"), reply_markup=_list_keyboard(items))
    await callback.answer()


@router.callback_query(F.data.startswith(f"{VIEW_PREFIX}:"))
async def view_category(callback: CallbackQuery, config: Config, texts: Texts) -> None:
    category_id = callback.data.removeprefix(f"{VIEW_PREFIX}:")
    with connect(config.db_path) as conn:
        category = categories_db.get_category(conn, category_id)
        if category is None:
            await callback.answer(texts.get("categories.not_found"))
            return
        start, end = period_bounds("month", config.timezone)
        month_spent = transactions_db.sum_expenses_by_category_grouped(conn, category_id, start, end)

    await callback.message.edit_text(
        _detail_text(category, month_spent, texts), reply_markup=_detail_keyboard(category_id)
    )
    await callback.answer()


# --- Добавление ---


@router.callback_query(F.data == ADD_CB)
async def start_add(callback: CallbackQuery, state: FSMContext, texts: Texts) -> None:
    await state.set_state(CategoryFlow.adding_name)
    await callback.message.edit_text(texts.get("categories.enter_name"))
    await callback.answer()


@router.message(CategoryFlow.adding_name, F.text)
async def add_name(message: Message, state: FSMContext, texts: Texts) -> None:
    await state.update_data(name=message.text)
    await state.set_state(CategoryFlow.adding_limit_amount)
    keyboard = build_single_button_keyboard("Без лимита", NO_LIMIT_CB)
    await message.answer(texts.get("categories.enter_limit_amount"), reply_markup=keyboard)


@router.message(CategoryFlow.adding_limit_amount, F.text.regexp(EXPENSE_AMOUNT_RE))
async def add_limit_amount(message: Message, state: FSMContext, texts: Texts) -> None:
    await state.update_data(limit_amount=parse_amount(message.text))
    await state.set_state(CategoryFlow.adding_limit_period)
    keyboard = build_choice_keyboard(PERIOD_OPTIONS, callback_prefix=PERIOD_PREFIX)
    await message.answer(texts.get("common.choose_limit_period"), reply_markup=keyboard)


@router.callback_query(CategoryFlow.adding_limit_amount, F.data == NO_LIMIT_CB)
async def add_no_limit(
    callback: CallbackQuery, state: FSMContext, config: Config, texts: Texts
) -> None:
    data = await state.get_data()
    await state.clear()
    with connect(config.db_path) as conn:
        categories_db.create_category(conn, ledger_user_id(config, callback.from_user.id), data["name"])
    await callback.message.edit_text(texts.get("categories.added", name=data["name"]))
    await callback.answer()


@router.callback_query(CategoryFlow.adding_limit_period, F.data.startswith(f"{PERIOD_PREFIX}:"))
async def add_limit_period(
    callback: CallbackQuery, state: FSMContext, config: Config, texts: Texts
) -> None:
    period = callback.data.removeprefix(f"{PERIOD_PREFIX}:")
    data = await state.get_data()
    await state.clear()
    with connect(config.db_path) as conn:
        categories_db.create_category(
            conn,
            ledger_user_id(config, callback.from_user.id),
            data["name"],
            limit_amount=data["limit_amount"],
            limit_period=period,
        )
    await callback.message.edit_text(texts.get("categories.added", name=data["name"]))
    await callback.answer()


# --- Переименование ---


@router.callback_query(F.data.startswith(f"{RENAME_PREFIX}:"))
async def start_rename(callback: CallbackQuery, state: FSMContext, texts: Texts) -> None:
    category_id = callback.data.removeprefix(f"{RENAME_PREFIX}:")
    await state.update_data(category_id=category_id)
    await state.set_state(CategoryFlow.renaming)
    await callback.message.edit_text(texts.get("common.enter_new_name"))
    await callback.answer()


@router.message(CategoryFlow.renaming, F.text)
async def finish_rename(message: Message, state: FSMContext, config: Config, texts: Texts) -> None:
    data = await state.get_data()
    await state.clear()
    with connect(config.db_path) as conn:
        categories_db.update_category(conn, data["category_id"], name=message.text)
    await message.answer(texts.get("common.renamed", name=message.text))


# --- Лимит (редактирование) ---


@router.callback_query(F.data.startswith(f"{SETLIMIT_PREFIX}:"))
async def start_setlimit(callback: CallbackQuery, state: FSMContext, texts: Texts) -> None:
    category_id = callback.data.removeprefix(f"{SETLIMIT_PREFIX}:")
    await state.update_data(category_id=category_id)
    await state.set_state(CategoryFlow.editing_limit_amount)
    keyboard = build_single_button_keyboard("Без лимита", EDIT_NO_LIMIT_CB)
    await callback.message.edit_text(texts.get("categories.edit_limit_prompt"), reply_markup=keyboard)
    await callback.answer()


@router.message(CategoryFlow.editing_limit_amount, F.text.regexp(EXPENSE_AMOUNT_RE))
async def edit_limit_amount(message: Message, state: FSMContext, texts: Texts) -> None:
    await state.update_data(limit_amount=parse_amount(message.text))
    await state.set_state(CategoryFlow.editing_limit_period)
    keyboard = build_choice_keyboard(PERIOD_OPTIONS, callback_prefix=EDIT_PERIOD_PREFIX)
    await message.answer(texts.get("common.choose_limit_period"), reply_markup=keyboard)


@router.callback_query(CategoryFlow.editing_limit_amount, F.data == EDIT_NO_LIMIT_CB)
async def edit_no_limit(
    callback: CallbackQuery, state: FSMContext, config: Config, texts: Texts
) -> None:
    data = await state.get_data()
    await state.clear()
    with connect(config.db_path) as conn:
        categories_db.update_category(conn, data["category_id"], limit_amount=None)
    await callback.message.edit_text(texts.get("categories.limit_removed"))
    await callback.answer()


@router.callback_query(
    CategoryFlow.editing_limit_period, F.data.startswith(f"{EDIT_PERIOD_PREFIX}:")
)
async def edit_limit_period(
    callback: CallbackQuery, state: FSMContext, config: Config, texts: Texts
) -> None:
    period = callback.data.removeprefix(f"{EDIT_PERIOD_PREFIX}:")
    data = await state.get_data()
    await state.clear()
    with connect(config.db_path) as conn:
        categories_db.update_category(
            conn, data["category_id"], limit_amount=data["limit_amount"], limit_period=period
        )
    await callback.message.edit_text(
        texts.get(
            "categories.limit_updated",
            amount=format_amount(data["limit_amount"]),
            period=period_word(period),
        )
    )
    await callback.answer()


# --- Архивация ---


@router.callback_query(F.data.startswith(f"{ARCHIVE_PREFIX}:"))
async def archive(callback: CallbackQuery, config: Config, texts: Texts) -> None:
    category_id = callback.data.removeprefix(f"{ARCHIVE_PREFIX}:")
    with connect(config.db_path) as conn:
        category = categories_db.get_category(conn, category_id)
        categories_db.archive_category(conn, category_id)
    await callback.message.edit_text(texts.get("common.archived", name=category.name))
    await callback.answer()
