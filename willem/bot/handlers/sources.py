from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, or_f
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from willem.bot.amount import EXPENSE_AMOUNT_RE, parse_amount
from willem.bot.keyboards import SOURCES_BUTTON, build_choice_keyboard, build_manage_list_keyboard
from willem.config import Config
from willem.db import sources as sources_db
from willem.db.connection import connect
from willem.db.sources import Source
from willem.db.transactions import get_source_balance, insert_transaction
from willem.formatting import currency_symbol, format_amount, source_type_label
from willem.sheets_sync import sync_after_insert

router = Router(name="sources")

VIEW_PREFIX = "src_view"
ADD_CB = "src_add"
ADD_TYPE_PREFIX = "src_add_type"
ADD_CURRENCY_PREFIX = "src_add_currency"
RENAME_PREFIX = "src_rename"
CURRENCY_EDIT_PREFIX = "src_currency_edit"
CURRENCY_SET_PREFIX = "src_currency_set"
ADJUST_PREFIX = "src_adjust"
ARCHIVE_PREFIX = "src_archive"
BACK_CB = "src_back"

TYPE_OPTIONS = [("card", "Карта"), ("cash", "Наличные"), ("crypto", "Крипто")]
CURRENCY_OPTIONS = [("KZT", "KZT"), ("RUB", "RUB"), ("USD", "USD"), ("USDT", "USDT")]


class SourceFlow(StatesGroup):
    adding_name = State()
    adding_type = State()
    adding_currency = State()
    renaming = State()
    editing_currency = State()
    adjusting_balance = State()


async def _list_sources(config: Config, user_id: int) -> list[Source]:
    with connect(config.db_path) as conn:
        return sources_db.list_sources(conn, user_id)


def _list_keyboard(items: list[Source]) -> InlineKeyboardMarkup:
    return build_manage_list_keyboard(
        [(s.id, s.name) for s in items], view_prefix=VIEW_PREFIX, add_callback=ADD_CB
    )


def _detail_text(source: Source, balance: float) -> str:
    symbol = currency_symbol(source.currency)
    return (
        f"«{source.name}». {source_type_label(source.type)} · {source.currency}.\n"
        f"Баланс: {format_amount(balance)} {symbol}."
    )


def _detail_keyboard(source_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Переименовать", callback_data=f"{RENAME_PREFIX}:{source_id}")
    builder.button(text="💱 Валюта", callback_data=f"{CURRENCY_EDIT_PREFIX}:{source_id}")
    builder.button(text="⚖️ Остаток", callback_data=f"{ADJUST_PREFIX}:{source_id}")
    builder.button(text="🗄 Архивировать", callback_data=f"{ARCHIVE_PREFIX}:{source_id}")
    builder.button(text="‹ Назад", callback_data=BACK_CB)
    builder.adjust(2, 2, 1)
    return builder.as_markup()


@router.message(or_f(Command("sources"), F.text == SOURCES_BUTTON))
async def show_sources(message: Message, state: FSMContext, config: Config) -> None:
    await state.clear()
    items = await _list_sources(config, message.from_user.id)
    await message.answer("Источники. 💳", reply_markup=_list_keyboard(items))


@router.callback_query(F.data == BACK_CB)
async def back_to_list(callback: CallbackQuery, state: FSMContext, config: Config) -> None:
    await state.clear()
    items = await _list_sources(config, callback.from_user.id)
    await callback.message.edit_text("Источники. 💳", reply_markup=_list_keyboard(items))
    await callback.answer()


@router.callback_query(F.data.startswith(f"{VIEW_PREFIX}:"))
async def view_source(callback: CallbackQuery, config: Config) -> None:
    source_id = callback.data.removeprefix(f"{VIEW_PREFIX}:")
    with connect(config.db_path) as conn:
        source = sources_db.get_source(conn, source_id)
        if source is None:
            await callback.answer("Источник не найден.")
            return
        balance = get_source_balance(conn, source_id)

    await callback.message.edit_text(
        _detail_text(source, balance), reply_markup=_detail_keyboard(source_id)
    )
    await callback.answer()


# --- Добавление ---


@router.callback_query(F.data == ADD_CB)
async def start_add(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SourceFlow.adding_name)
    await callback.message.edit_text("Название нового источника. 💳")
    await callback.answer()


@router.message(SourceFlow.adding_name, F.text)
async def add_name(message: Message, state: FSMContext) -> None:
    await state.update_data(name=message.text)
    await state.set_state(SourceFlow.adding_type)
    keyboard = build_choice_keyboard(TYPE_OPTIONS, callback_prefix=ADD_TYPE_PREFIX, columns=3)
    await message.answer("Тип источника.", reply_markup=keyboard)


@router.callback_query(SourceFlow.adding_type, F.data.startswith(f"{ADD_TYPE_PREFIX}:"))
async def add_type(callback: CallbackQuery, state: FSMContext) -> None:
    type_ = callback.data.removeprefix(f"{ADD_TYPE_PREFIX}:")
    await state.update_data(type=type_)
    await state.set_state(SourceFlow.adding_currency)
    keyboard = build_choice_keyboard(
        CURRENCY_OPTIONS, callback_prefix=ADD_CURRENCY_PREFIX, columns=4
    )
    await callback.message.edit_text("Валюта.", reply_markup=keyboard)
    await callback.answer()


@router.callback_query(SourceFlow.adding_currency, F.data.startswith(f"{ADD_CURRENCY_PREFIX}:"))
async def add_currency(callback: CallbackQuery, state: FSMContext, config: Config) -> None:
    currency = callback.data.removeprefix(f"{ADD_CURRENCY_PREFIX}:")
    data = await state.get_data()
    await state.clear()
    with connect(config.db_path) as conn:
        sources_db.create_source(
            conn, callback.from_user.id, data["name"], data["type"], currency
        )
    await callback.message.edit_text(f"Добавил источник: «{data['name']}». 💳")
    await callback.answer()


# --- Переименование ---


@router.callback_query(F.data.startswith(f"{RENAME_PREFIX}:"))
async def start_rename(callback: CallbackQuery, state: FSMContext) -> None:
    source_id = callback.data.removeprefix(f"{RENAME_PREFIX}:")
    await state.update_data(source_id=source_id)
    await state.set_state(SourceFlow.renaming)
    await callback.message.edit_text("Новое название. ✏️")
    await callback.answer()


@router.message(SourceFlow.renaming, F.text)
async def finish_rename(message: Message, state: FSMContext, config: Config) -> None:
    data = await state.get_data()
    await state.clear()
    with connect(config.db_path) as conn:
        sources_db.update_source(conn, data["source_id"], name=message.text)
    await message.answer(f"Переименовал в «{message.text}». ✏️")


# --- Валюта (редактирование) ---


@router.callback_query(F.data.startswith(f"{CURRENCY_EDIT_PREFIX}:"))
async def start_edit_currency(callback: CallbackQuery, state: FSMContext) -> None:
    source_id = callback.data.removeprefix(f"{CURRENCY_EDIT_PREFIX}:")
    await state.update_data(source_id=source_id)
    await state.set_state(SourceFlow.editing_currency)
    keyboard = build_choice_keyboard(
        CURRENCY_OPTIONS, callback_prefix=CURRENCY_SET_PREFIX, columns=4
    )
    await callback.message.edit_text("Новая валюта.", reply_markup=keyboard)
    await callback.answer()


@router.callback_query(SourceFlow.editing_currency, F.data.startswith(f"{CURRENCY_SET_PREFIX}:"))
async def finish_edit_currency(callback: CallbackQuery, state: FSMContext, config: Config) -> None:
    currency = callback.data.removeprefix(f"{CURRENCY_SET_PREFIX}:")
    data = await state.get_data()
    await state.clear()
    with connect(config.db_path) as conn:
        sources_db.update_source(conn, data["source_id"], currency=currency)
    await callback.message.edit_text(f"Валюта обновлена: {currency}. 💱")
    await callback.answer()


# --- Коррекция остатка ---


@router.callback_query(F.data.startswith(f"{ADJUST_PREFIX}:"))
async def start_adjust(callback: CallbackQuery, state: FSMContext, config: Config) -> None:
    source_id = callback.data.removeprefix(f"{ADJUST_PREFIX}:")
    with connect(config.db_path) as conn:
        source = sources_db.get_source(conn, source_id)
        balance = get_source_balance(conn, source_id)

    await state.update_data(source_id=source_id)
    await state.set_state(SourceFlow.adjusting_balance)
    symbol = currency_symbol(source.currency)
    await callback.message.edit_text(
        f"Текущий остаток: {format_amount(balance)} {symbol}.\n"
        f"Введите фактический остаток. ⚖️"
    )
    await callback.answer()


@router.message(SourceFlow.adjusting_balance, F.text.regexp(EXPENSE_AMOUNT_RE))
async def finish_adjust(message: Message, state: FSMContext, config: Config) -> None:
    new_balance = parse_amount(message.text)
    data = await state.get_data()
    await state.clear()

    with connect(config.db_path) as conn:
        source = sources_db.get_source(conn, data["source_id"])
        current_balance = get_source_balance(conn, data["source_id"])
        delta = new_balance - current_balance
        tx = None
        if delta != 0:
            tx = insert_transaction(
                conn,
                user_id=message.from_user.id,
                type="adjustment",
                amount=delta,
                currency=source.currency,
                source_id=data["source_id"],
                comment="Коррекция остатка",
            )

    symbol = currency_symbol(source.currency)
    suffix = ""
    if tx is not None:
        suffix = await sync_after_insert(
            config, message.from_user.id, tx, source_name=source.name
        )
    await message.answer(f"Остаток обновлён: {format_amount(new_balance)} {symbol}.{suffix} ⚖️")


# --- Архивация ---


@router.callback_query(F.data.startswith(f"{ARCHIVE_PREFIX}:"))
async def archive(callback: CallbackQuery, config: Config) -> None:
    source_id = callback.data.removeprefix(f"{ARCHIVE_PREFIX}:")
    with connect(config.db_path) as conn:
        source = sources_db.get_source(conn, source_id)
        sources_db.archive_source(conn, source_id)
    await callback.message.edit_text(f"Архивировал «{source.name}». 🗄")
    await callback.answer()
