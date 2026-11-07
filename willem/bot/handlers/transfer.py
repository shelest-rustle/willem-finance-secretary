from __future__ import annotations

from collections.abc import Awaitable, Callable

from aiogram import F, Router
from aiogram.filters import Command, or_f
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from willem.bot.amount import EXPENSE_AMOUNT_RE, parse_amount
from willem.bot.keyboards import (
    CANCEL_LABEL,
    SKIP_LABEL,
    TRANSFER_BUTTON,
    add_back_button,
    build_choice_keyboard,
    build_single_button_keyboard,
)
from willem.config import Config, ledger_user_id
from willem.db import categories as categories_db
from willem.db import sources as sources_db
from willem.db import transactions as transactions_db
from willem.db.categories import Category
from willem.db.connection import connect
from willem.formatting import currency_symbol, format_amount
from willem.sheets_sync import sync_after_insert
from willem.texts import Texts

router = Router(name="transfer")

FROM_PREFIX = "trf_from"
TO_PREFIX = "trf_to"
SUBCATEGORY_PREFIX = "trf_subcat"
SKIP_SUBCATEGORY_CB = "trf_subcat_skip"
SKIP_COMMENT_CB = "trf_comment_skip"
BACK_CB = "trf_back"
CANCEL_CB = "trf_cancel"

Respond = Callable[[str, InlineKeyboardMarkup | None], Awaitable[None]]


class TransferFlow(StatesGroup):
    entering_amount = State()
    choosing_source_from = State()
    choosing_source_to = State()
    entering_target_amount = State()
    choosing_subcategory = State()
    entering_comment = State()


def _cancel_keyboard() -> InlineKeyboardMarkup:
    return add_back_button(None, CANCEL_CB, label=CANCEL_LABEL)


def _back_keyboard() -> InlineKeyboardMarkup:
    return add_back_button(None, BACK_CB)


def _callback_responder(callback: CallbackQuery) -> Respond:
    async def respond(text: str, keyboard: InlineKeyboardMarkup | None) -> None:
        await callback.message.edit_text(text, reply_markup=keyboard)

    return respond


def _auto_subcategories(config: Config, user_id: int) -> tuple[Category | None, list[Category]]:
    """Авто-категория перевода из профиля (`auto_category.transfer`) и её активные подкатегории."""
    auto_category_name = config.auto_category.get("transfer")
    if not auto_category_name:
        return None, []
    with connect(config.db_path) as conn:
        top_level = categories_db.list_categories(conn, ledger_user_id(config, user_id))
        category = next((c for c in top_level if c.name == auto_category_name), None)
        subcategories = (
            categories_db.list_categories(
                conn, ledger_user_id(config, user_id), parent_id=category.id
            )
            if category
            else []
        )
    return category, subcategories


async def _show_sources_from(respond: Respond, state: FSMContext, config: Config, texts: Texts, user_id: int) -> None:
    with connect(config.db_path) as conn:
        active_sources = sources_db.list_sources(conn, ledger_user_id(config, user_id))
    await state.set_state(TransferFlow.choosing_source_from)
    keyboard = build_choice_keyboard(
        [(s.id, s.name) for s in active_sources], callback_prefix=FROM_PREFIX
    )
    await respond(texts.get("common.choose_source_expense"), add_back_button(keyboard, BACK_CB))


async def _show_sources_to(respond: Respond, state: FSMContext, config: Config, texts: Texts, user_id: int) -> None:
    data = await state.get_data()
    with connect(config.db_path) as conn:
        active_sources = sources_db.list_sources(conn, ledger_user_id(config, user_id))
    targets = [s for s in active_sources if s.id != data["source_from_id"]]
    await state.set_state(TransferFlow.choosing_source_to)
    keyboard = build_choice_keyboard([(s.id, s.name) for s in targets], callback_prefix=TO_PREFIX)
    await respond(texts.get("common.choose_source_income"), add_back_button(keyboard, BACK_CB))


async def _show_target_amount(respond: Respond, state: FSMContext, config: Config, texts: Texts) -> None:
    data = await state.get_data()
    with connect(config.db_path) as conn:
        source_to = sources_db.get_source(conn, data["source_to_id"])
    await state.set_state(TransferFlow.entering_target_amount)
    await respond(texts.get("transfer.enter_target_amount", target_name=source_to.name), _back_keyboard())


async def _is_cross_currency(state: FSMContext, config: Config) -> bool:
    data = await state.get_data()
    with connect(config.db_path) as conn:
        source_from = sources_db.get_source(conn, data["source_from_id"])
        source_to = sources_db.get_source(conn, data["source_to_id"])
    return source_from.currency != source_to.currency


@router.message(or_f(Command("transfer"), F.text == TRANSFER_BUTTON))
async def start_transfer(message: Message, state: FSMContext, texts: Texts) -> None:
    await state.clear()
    await state.set_state(TransferFlow.entering_amount)
    await message.answer(texts.get("transfer.enter_amount"), reply_markup=_cancel_keyboard())


@router.callback_query(TransferFlow.entering_amount, F.data == CANCEL_CB)
async def cancel_transfer(callback: CallbackQuery, state: FSMContext, texts: Texts) -> None:
    await state.clear()
    await callback.message.edit_text(texts.get("common.cancelled"))
    await callback.answer()


@router.message(TransferFlow.entering_amount, F.text.regexp(EXPENSE_AMOUNT_RE))
async def enter_amount(message: Message, state: FSMContext, config: Config, texts: Texts) -> None:
    amount = parse_amount(message.text)
    with connect(config.db_path) as conn:
        active_sources = sources_db.list_sources(conn, ledger_user_id(config, message.from_user.id))

    if len(active_sources) < 2:
        await message.answer(texts.get("transfer.not_enough_sources"))
        await state.clear()
        return

    await state.update_data(amount=amount)

    async def respond(text: str, keyboard: InlineKeyboardMarkup | None) -> None:
        await message.answer(text, reply_markup=keyboard)

    await _show_sources_from(respond, state, config, texts, message.from_user.id)


@router.callback_query(TransferFlow.choosing_source_from, F.data.startswith(f"{FROM_PREFIX}:"))
async def choose_source_from(
    callback: CallbackQuery, state: FSMContext, config: Config, texts: Texts
) -> None:
    source_from_id = callback.data.removeprefix(f"{FROM_PREFIX}:")
    with connect(config.db_path) as conn:
        active_sources = sources_db.list_sources(
            conn, ledger_user_id(config, callback.from_user.id)
        )

    targets = [s for s in active_sources if s.id != source_from_id]
    if not targets:
        await callback.message.edit_text(texts.get("transfer.no_second_source"))
        await state.clear()
        await callback.answer()
        return

    await state.update_data(source_from_id=source_from_id)
    await _show_sources_to(_callback_responder(callback), state, config, texts, callback.from_user.id)
    await callback.answer()


@router.callback_query(TransferFlow.choosing_source_to, F.data.startswith(f"{TO_PREFIX}:"))
async def choose_source_to(
    callback: CallbackQuery, state: FSMContext, config: Config, texts: Texts
) -> None:
    source_to_id = callback.data.removeprefix(f"{TO_PREFIX}:")
    data = await state.get_data()
    await state.update_data(source_to_id=source_to_id)
    respond = _callback_responder(callback)

    if not await _is_cross_currency(state, config):
        await state.update_data(final_target_amount=data["amount"])
        await _maybe_show_subcategory(respond, state, config, texts, callback.from_user.id)
    else:
        await _show_target_amount(respond, state, config, texts)
    await callback.answer()


@router.message(TransferFlow.entering_target_amount, F.text.regexp(EXPENSE_AMOUNT_RE))
async def enter_target_amount(
    message: Message, state: FSMContext, config: Config, texts: Texts
) -> None:
    target_amount = parse_amount(message.text)
    await state.update_data(final_target_amount=target_amount)

    async def respond(text: str, keyboard: InlineKeyboardMarkup | None) -> None:
        await message.answer(text, reply_markup=keyboard)

    await _maybe_show_subcategory(respond, state, config, texts, message.from_user.id)


async def _maybe_show_subcategory(
    respond: Respond, state: FSMContext, config: Config, texts: Texts, user_id: int
) -> None:
    """Если в профиле задан auto_category для transfer (например "Переводы") — сразу
    подкатегории этой категории, без выбора из полного списка категорий."""
    category, subcategories = _auto_subcategories(config, user_id)
    if category is not None:
        await state.update_data(category_id=category.id)
    if subcategories:
        await _show_subcategories(respond, state, texts, subcategories)
        return
    await _prompt_comment(respond, state, texts)


async def _show_subcategories(
    respond: Respond, state: FSMContext, texts: Texts, subcategories: list[Category]
) -> None:
    await state.set_state(TransferFlow.choosing_subcategory)
    keyboard = build_choice_keyboard(
        [(c.id, c.name) for c in subcategories],
        callback_prefix=SUBCATEGORY_PREFIX,
        extra_buttons=[(SKIP_LABEL, SKIP_SUBCATEGORY_CB)],
    )
    await respond(texts.get("common.choose_subcategory"), add_back_button(keyboard, BACK_CB))


@router.callback_query(TransferFlow.choosing_subcategory, F.data.startswith(f"{SUBCATEGORY_PREFIX}:"))
async def choose_subcategory(callback: CallbackQuery, state: FSMContext, texts: Texts) -> None:
    subcategory_id = callback.data.removeprefix(f"{SUBCATEGORY_PREFIX}:")
    await state.update_data(subcategory_id=subcategory_id)
    await _prompt_comment(_callback_responder(callback), state, texts)
    await callback.answer()


@router.callback_query(TransferFlow.choosing_subcategory, F.data == SKIP_SUBCATEGORY_CB)
async def skip_subcategory(callback: CallbackQuery, state: FSMContext, texts: Texts) -> None:
    await _prompt_comment(_callback_responder(callback), state, texts)
    await callback.answer()


async def _prompt_comment(respond: Respond, state: FSMContext, texts: Texts) -> None:
    await state.set_state(TransferFlow.entering_comment)
    keyboard = build_single_button_keyboard(SKIP_LABEL, SKIP_COMMENT_CB)
    await respond(texts.get("transfer.enter_comment"), add_back_button(keyboard, BACK_CB))


# --- «Назад»: на шаг раньше; данные отменённого шага сбрасываются ---


@router.callback_query(TransferFlow.choosing_source_from, F.data == BACK_CB)
async def back_to_amount(callback: CallbackQuery, state: FSMContext, texts: Texts) -> None:
    await state.set_state(TransferFlow.entering_amount)
    await callback.message.edit_text(texts.get("transfer.enter_amount"), reply_markup=_cancel_keyboard())
    await callback.answer()


@router.callback_query(TransferFlow.choosing_source_to, F.data == BACK_CB)
async def back_to_source_from(callback: CallbackQuery, state: FSMContext, config: Config, texts: Texts) -> None:
    await state.update_data(source_from_id=None)
    await _show_sources_from(_callback_responder(callback), state, config, texts, callback.from_user.id)
    await callback.answer()


async def _back_before_subcategory(callback: CallbackQuery, state: FSMContext, config: Config, texts: Texts) -> None:
    """Шаг перед подкатегорией/комментарием: сумма зачисления (если валюты разные) или «Куда»."""
    respond = _callback_responder(callback)
    await state.update_data(category_id=None, final_target_amount=None)
    if await _is_cross_currency(state, config):
        await _show_target_amount(respond, state, config, texts)
    else:
        await state.update_data(source_to_id=None)
        await _show_sources_to(respond, state, config, texts, callback.from_user.id)


@router.callback_query(TransferFlow.entering_target_amount, F.data == BACK_CB)
async def back_to_source_to(callback: CallbackQuery, state: FSMContext, config: Config, texts: Texts) -> None:
    await state.update_data(source_to_id=None)
    await _show_sources_to(_callback_responder(callback), state, config, texts, callback.from_user.id)
    await callback.answer()


@router.callback_query(TransferFlow.choosing_subcategory, F.data == BACK_CB)
async def back_from_subcategory(callback: CallbackQuery, state: FSMContext, config: Config, texts: Texts) -> None:
    await _back_before_subcategory(callback, state, config, texts)
    await callback.answer()


@router.callback_query(TransferFlow.entering_comment, F.data == BACK_CB)
async def back_from_comment(callback: CallbackQuery, state: FSMContext, config: Config, texts: Texts) -> None:
    _, subcategories = _auto_subcategories(config, callback.from_user.id)
    if subcategories:
        await state.update_data(subcategory_id=None)
        await _show_subcategories(_callback_responder(callback), state, texts, subcategories)
    else:
        await _back_before_subcategory(callback, state, config, texts)
    await callback.answer()


@router.message(TransferFlow.entering_comment, F.text)
async def enter_comment(message: Message, state: FSMContext, config: Config, texts: Texts) -> None:
    text = await _record_transfer(state, config, texts, message.from_user.id, comment=message.text)
    await message.answer(text)


@router.callback_query(TransferFlow.entering_comment, F.data == SKIP_COMMENT_CB)
async def skip_comment(callback: CallbackQuery, state: FSMContext, config: Config, texts: Texts) -> None:
    text = await _record_transfer(state, config, texts, callback.from_user.id, comment=None)
    await callback.message.edit_text(text)
    await callback.answer()


async def _record_transfer(
    state: FSMContext, config: Config, texts: Texts, user_id: int, *, comment: str | None
) -> str:
    data = await state.get_data()
    amount = data["amount"]
    source_from_id = data["source_from_id"]
    source_to_id = data["source_to_id"]
    target_amount = data["final_target_amount"]
    category_id = data.get("category_id")
    subcategory_id = data.get("subcategory_id")
    ledger_id = ledger_user_id(config, user_id)
    who = config.people.get(user_id)
    await state.clear()

    with connect(config.db_path) as conn:
        source_from = sources_db.get_source(conn, source_from_id)
        source_to = sources_db.get_source(conn, source_to_id)
        category = categories_db.get_category(conn, category_id) if category_id else None
        subcategory = categories_db.get_category(conn, subcategory_id) if subcategory_id else None
        tx = transactions_db.insert_transaction(
            conn,
            user_id=ledger_id,
            type="transfer",
            amount=amount,
            currency=source_from.currency,
            source_id=source_from_id,
            target_amount=target_amount,
            target_currency=source_to.currency,
            target_source_id=source_to_id,
            category_id=category_id,
            subcategory_id=subcategory_id,
            who=who,
            comment=comment,
        )

    symbol_from = currency_symbol(source_from.currency)
    if source_from.currency == source_to.currency:
        text = texts.get(
            "transfer.recorded_same_currency",
            amount=format_amount(amount),
            symbol_from=symbol_from,
            source_from=source_from.name,
            source_to=source_to.name,
        )
    else:
        symbol_to = currency_symbol(source_to.currency)
        text = texts.get(
            "transfer.recorded_cross_currency",
            amount=format_amount(amount),
            symbol_from=symbol_from,
            source_from=source_from.name,
            target_amount=format_amount(target_amount),
            symbol_to=symbol_to,
            source_to=source_to.name,
        )

    suffix = await sync_after_insert(
        config,
        texts,
        user_id,
        tx,
        source_name=source_from.name,
        category_name=category.name if category else None,
        subcategory_name=subcategory.name if subcategory else None,
        target_name=source_to.name,
        target_kind=source_to.kind,
    )
    return f"{text}.{suffix}{texts.get('transfer.done_suffix')}"
