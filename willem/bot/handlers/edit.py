from __future__ import annotations

import html
import re
import sqlite3
from collections.abc import Awaitable, Callable

from aiogram import F, Router
from aiogram.filters import Command, StateFilter, or_f
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from willem.bot.amount import EXPENSE_AMOUNT_RE, parse_amount
from willem.bot.handlers.reports import last_line, resolve_summary, short_id
from willem.bot.keyboards import EDIT_BUTTON, build_choice_keyboard, build_single_button_keyboard
from willem.config import FAMILY_WHO, Config, ledger_user_id
from willem.db import categories as categories_db
from willem.db import sources as sources_db
from willem.db import transactions as transactions_db
from willem.db.connection import connect
from willem.db.sources import Source
from willem.db.transactions import Transaction
from willem.sheets_sync import sync_after_update, synced_user_ids
from willem.texts import Texts
from willem.timeutil import format_local_datetime

router = Router(name="edit")

FIELD_PREFIX = "edt_field"
SOURCE_PREFIX = "edt_src"
CATEGORY_PREFIX = "edt_cat"
SUBCATEGORY_PREFIX = "edt_sub"
NO_SUBCATEGORY_CB = "edt_sub_none"
WHO_PREFIX = "edt_who"
CLEAR_COMMENT_CB = "edt_comment_clear"
DELETE_CONFIRM_CB = "edt_delete_yes"
BACK_CB = "edt_back"
DONE_CB = "edt_done"
CANCEL_CB = "edt_cancel"

ID_RE = re.compile(r"^[0-9a-f-]{8,36}$")

Respond = Callable[[str, InlineKeyboardMarkup | None], Awaitable[None]]

# Порядок полей = порядок кнопок в карточке; подпись берётся из texts по ключу edit.btn_<поле>.
_FIELD_ORDER = (
    "amount",
    "target_amount",
    "source",
    "target_source",
    "category",
    "subcategory",
    "who",
    "comment",
)


class EditFlow(StatesGroup):
    entering_id = State()
    choosing_field = State()
    entering_amount = State()
    entering_target_amount = State()
    entering_comment = State()
    choosing_source = State()
    choosing_category = State()
    choosing_subcategory = State()
    choosing_who = State()
    confirming_delete = State()


_SUB_STATES = (
    EditFlow.entering_amount,
    EditFlow.entering_target_amount,
    EditFlow.entering_comment,
    EditFlow.choosing_source,
    EditFlow.choosing_category,
    EditFlow.choosing_subcategory,
    EditFlow.choosing_who,
    EditFlow.confirming_delete,
)


# --- чистая логика (без aiogram) ---


def normalize_id_input(text: str) -> str | None:
    """ID операции из ввода пользователя: полный uuid или его префикс от 8 символов."""
    value = text.strip().lower()
    return value if ID_RE.match(value) else None


def editable_fields(tx: Transaction, config: Config, *, has_subcategories: bool) -> list[str]:
    """Какие поля можно править у операции — см. UPDATE_SPEC.md, п. 2. Тип и дата не
    редактируются; у коррекции остатка — только комментарий (сумма коррекции вычислялась
    из фактического остатка, её правка задним числом делает этот остаток неправдой)."""
    if tx.type == "adjustment":
        return ["comment"]
    fields = ["amount"]
    if tx.type == "transfer":
        if tx.currency != tx.target_currency:
            fields.append("target_amount")
        fields += ["source", "target_source"]
    else:
        fields.append("source")
    if tx.type == "expense":
        fields.append("category")
    if has_subcategories:
        fields.append("subcategory")
    if config.people:
        fields.append("who")
    fields.append("comment")
    return fields


def source_change(tx: Transaction, new_source: Source, *, target: bool) -> tuple[dict, str | None]:
    """Поля для смены источника (`target=True` — «Куда» у перевода) и какую сумму после
    этого обязательно нужно переспросить: "amount" / "target_amount" / None. Сумма
    хранится в валюте источника, поэтому при смене валюты старое число теряет смысл."""
    if not target:
        fields = {"source_id": new_source.id, "currency": new_source.currency}
        return fields, ("amount" if new_source.currency != tx.currency else None)

    fields = {"target_source_id": new_source.id, "target_currency": new_source.currency}
    if new_source.currency == tx.currency:
        fields["target_amount"] = tx.amount
        return fields, None
    if new_source.currency != tx.target_currency:
        return fields, "target_amount"
    return fields, None


def amount_fields(tx: Transaction, amount: float, pending: dict | None = None) -> dict:
    """Новая сумма (с учётом отложенной смены источника). У перевода в одной валюте
    сумма зачисления всегда равна сумме списания — как при записи в transfer.py."""
    fields = {**(pending or {}), "amount": amount}
    if tx.type == "transfer":
        currency = fields.get("currency", tx.currency)
        target_currency = fields.get("target_currency", tx.target_currency)
        if currency == target_currency:
            fields["target_amount"] = amount
    return fields


def who_options(config: Config) -> list[str]:
    return [*dict.fromkeys(config.people.values()), FAMILY_WHO]


# --- работа с БД ---


def _load_editable(conn: sqlite3.Connection, config: Config, user_id: int, tx_id: str | None):
    """Операция, если её всё ещё можно править этим пользователем: существует, не удалена
    (в shared-ledger её мог параллельно удалить другой участник) и из его леджера."""
    tx = transactions_db.get_transaction(conn, tx_id) if tx_id else None
    if tx is None or tx.deleted or tx.user_id != ledger_user_id(config, user_id):
        return None
    return tx


def _auto_category_id(conn, tx: Transaction, config: Config, ledger_id: int) -> str | None:
    auto_name = config.auto_category.get(tx.type)
    if not auto_name:
        return None
    top_level = categories_db.list_categories(conn, ledger_id)
    category = next((c for c in top_level if c.name == auto_name), None)
    return category.id if category else None


def _subcategory_parent_id(conn, tx: Transaction, config: Config, ledger_id: int) -> str | None:
    """Категория, из детей которой выбирается подкатегория: у расхода — его категория,
    у дохода/перевода — авто-категория профиля. None, если активных подкатегорий нет."""
    if tx.type == "expense":
        parent_id = tx.category_id
    elif tx.type in ("income", "transfer"):
        parent_id = tx.category_id or _auto_category_id(conn, tx, config, ledger_id)
    else:
        parent_id = None
    if parent_id and categories_db.list_categories(conn, ledger_id, parent_id=parent_id):
        return parent_id
    return None


def _card_text(conn, tx: Transaction, config: Config, texts: Texts) -> str:
    details = ""
    if tx.subcategory_id:
        subcategory = categories_db.get_category(conn, tx.subcategory_id)
        if subcategory:
            details += texts.get("edit.detail_subcategory", name=html.escape(subcategory.name))
    if tx.who:
        details += texts.get("edit.detail_who", who=html.escape(tx.who))
    if tx.comment:
        details += texts.get("edit.detail_comment", comment=html.escape(tx.comment))
    return texts.get(
        "edit.card",
        short_id=short_id(tx),
        date=format_local_datetime(tx.created_at_utc, config.timezone),
        summary=html.escape(resolve_summary(conn, tx, texts)),
        details=details,
    )


def _fields_keyboard(tx: Transaction, fields: list[str], texts: Texts) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for field in _FIELD_ORDER:
        if field in fields:
            label_key = "source_from" if field == "source" and tx.type == "transfer" else field
            builder.button(text=texts.get(f"edit.btn_{label_key}"), callback_data=f"{FIELD_PREFIX}:{field}")
    builder.button(text=texts.get("edit.btn_delete"), callback_data=f"{FIELD_PREFIX}:delete")
    builder.button(text=texts.get("edit.btn_done"), callback_data=DONE_CB)
    builder.adjust(2)
    return builder.as_markup()


def _back_keyboard(texts: Texts, extra: list[tuple[str, str]] | None = None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for label, callback_data in [*(extra or []), (texts.get("edit.btn_back"), BACK_CB)]:
        builder.button(text=label, callback_data=callback_data)
    builder.adjust(1)
    return builder.as_markup()


def _touches_credit_sheet(conn, config: Config, user_id: int, before: Transaction, after: Transaction) -> bool:
    """Правка расхода-платежа по кредиту (pantalone, credit_sheets): запись в листе графика
    кредита бот найти не может (там нет нашего id) — пользователя нужно предупредить."""
    if before.type != "expense" or user_id not in synced_user_ids(config):
        return False
    names = set()
    for subcategory_id in (before.subcategory_id, after.subcategory_id):
        if subcategory_id:
            subcategory = categories_db.get_category(conn, subcategory_id)
            if subcategory:
                names.add(subcategory.name)
    if not names & set(config.credit_sheets):
        return False
    return (
        before.amount != after.amount
        or before.subcategory_id != after.subcategory_id
        or after.deleted
    )


# --- хендлеры ---


def _message_responder(message: Message) -> Respond:
    async def respond(text: str, keyboard: InlineKeyboardMarkup | None) -> None:
        await message.answer(text, reply_markup=keyboard)

    return respond


def _callback_responder(callback: CallbackQuery) -> Respond:
    async def respond(text: str, keyboard: InlineKeyboardMarkup | None) -> None:
        await callback.message.edit_text(text, reply_markup=keyboard)

    return respond


async def _show_card(
    respond: Respond, state: FSMContext, config: Config, texts: Texts, user_id: int, notice: str = ""
) -> None:
    data = await state.get_data()
    with connect(config.db_path) as conn:
        tx = _load_editable(conn, config, user_id, data.get("transaction_id"))
        if tx is None:
            await state.clear()
            await respond(f"{notice}{texts.get('edit.not_found')}", None)
            return
        parent_id = _subcategory_parent_id(conn, tx, config, ledger_user_id(config, user_id))
        fields = editable_fields(tx, config, has_subcategories=parent_id is not None)
        text = _card_text(conn, tx, config, texts)
    await state.set_state(EditFlow.choosing_field)
    await state.update_data(pending={})
    await respond(f"{notice}{text}", _fields_keyboard(tx, fields, texts))


async def _apply(
    respond: Respond, state: FSMContext, config: Config, texts: Texts, user_id: int, fields: dict
) -> None:
    data = await state.get_data()
    tx_id = data.get("transaction_id")
    with connect(config.db_path) as conn:
        before = _load_editable(conn, config, user_id, tx_id)
        if before is None:
            await state.clear()
            await respond(texts.get("edit.not_found"), None)
            return
        transactions_db.update_transaction(conn, tx_id, **fields)
        after = transactions_db.get_transaction(conn, tx_id)
        credit_warning = _touches_credit_sheet(conn, config, user_id, before, after)

    suffix = await sync_after_update(config, texts, user_id, tx_id)
    if credit_warning:
        suffix += texts.get("edit.credit_sheet_warning")
    await _show_card(respond, state, config, texts, user_id, notice=texts.get("edit.updated", suffix=suffix))


@router.message(or_f(Command("edit"), F.text == EDIT_BUTTON))
async def start_edit(message: Message, state: FSMContext, texts: Texts) -> None:
    await state.clear()
    await state.set_state(EditFlow.entering_id)
    await message.answer(
        texts.get("edit.enter_id"),
        reply_markup=build_single_button_keyboard(texts.get("edit.btn_cancel"), CANCEL_CB),
    )


@router.callback_query(EditFlow.entering_id, F.data == CANCEL_CB)
async def cancel_edit(callback: CallbackQuery, state: FSMContext, texts: Texts) -> None:
    await state.clear()
    await callback.message.edit_text(texts.get("edit.cancelled"))
    await callback.answer()


@router.message(EditFlow.entering_id, F.text)
async def enter_id(message: Message, state: FSMContext, config: Config, texts: Texts) -> None:
    prefix = normalize_id_input(message.text)
    if prefix is None:
        await message.answer(texts.get("edit.invalid_id"))
        return

    with connect(config.db_path) as conn:
        found = transactions_db.find_by_id_prefix(
            conn, ledger_user_id(config, message.from_user.id), prefix
        )
        lines = [last_line(conn, tx, texts, config.timezone) for tx in found]

    if not found:
        await message.answer(texts.get("edit.not_found"))
        return
    if len(found) > 1:
        await message.answer(texts.get("edit.ambiguous", lines="\n".join(lines)))
        return

    await state.update_data(transaction_id=found[0].id)
    await _show_card(_message_responder(message), state, config, texts, message.from_user.id)


@router.callback_query(EditFlow.choosing_field, F.data == DONE_CB)
async def finish_edit(callback: CallbackQuery, state: FSMContext, texts: Texts) -> None:
    await state.clear()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(texts.get("edit.done"))
    await callback.answer()


@router.callback_query(StateFilter(*_SUB_STATES), F.data == BACK_CB)
async def back_to_card(callback: CallbackQuery, state: FSMContext, config: Config, texts: Texts) -> None:
    await _show_card(_callback_responder(callback), state, config, texts, callback.from_user.id)
    await callback.answer()


@router.callback_query(EditFlow.choosing_field, F.data.startswith(f"{FIELD_PREFIX}:"))
async def choose_field(callback: CallbackQuery, state: FSMContext, config: Config, texts: Texts) -> None:
    field = callback.data.removeprefix(f"{FIELD_PREFIX}:")
    user_id = callback.from_user.id
    ledger_id = ledger_user_id(config, user_id)
    data = await state.get_data()
    respond = _callback_responder(callback)

    with connect(config.db_path) as conn:
        tx = _load_editable(conn, config, user_id, data.get("transaction_id"))
        if tx is None:
            await state.clear()
            await respond(texts.get("edit.not_found"), None)
            await callback.answer()
            return

        if field == "amount":
            await state.set_state(EditFlow.entering_amount)
            await respond(texts.get("edit.enter_amount"), _back_keyboard(texts))
        elif field == "target_amount":
            target = sources_db.get_source(conn, tx.target_source_id)
            await state.set_state(EditFlow.entering_target_amount)
            await respond(
                texts.get("edit.enter_target_amount", target_name=html.escape(target.name)),
                _back_keyboard(texts),
            )
        elif field in ("source", "target_source"):
            excluded = {tx.source_id, tx.target_source_id}
            choices = [s for s in sources_db.list_sources(conn, ledger_id) if s.id not in excluded]
            await state.update_data(source_target=(field == "target_source"))
            await state.set_state(EditFlow.choosing_source)
            is_incoming = field == "target_source" or tx.type == "income"
            await respond(
                texts.get("common.choose_source_income" if is_incoming else "common.choose_source_expense"),
                build_choice_keyboard(
                    [(s.id, s.name) for s in choices],
                    callback_prefix=SOURCE_PREFIX,
                    extra_buttons=[(texts.get("edit.btn_back"), BACK_CB)],
                ),
            )
        elif field == "category":
            choices = categories_db.list_categories(conn, ledger_id)
            await state.set_state(EditFlow.choosing_category)
            await respond(
                texts.get("common.choose_category"),
                build_choice_keyboard(
                    [(c.id, c.name) for c in choices],
                    callback_prefix=CATEGORY_PREFIX,
                    extra_buttons=[(texts.get("edit.btn_back"), BACK_CB)],
                ),
            )
        elif field == "subcategory":
            parent_id = _subcategory_parent_id(conn, tx, config, ledger_id)
            # Доход/перевод, записанный без авто-категории: подкатегорию ставим вместе с ней.
            pending = {"category_id": parent_id} if parent_id and tx.category_id != parent_id else {}
            await state.update_data(pending=pending)
            await _prompt_subcategory(respond, state, conn, texts, ledger_id, parent_id)
        elif field == "who":
            await state.set_state(EditFlow.choosing_who)
            await respond(
                texts.get("edit.choose_who"),
                build_choice_keyboard(
                    list(enumerate(who_options(config))),
                    callback_prefix=WHO_PREFIX,
                    extra_buttons=[(texts.get("edit.btn_back"), BACK_CB)],
                ),
            )
        elif field == "comment":
            extra = (
                [(texts.get("edit.btn_clear_comment"), CLEAR_COMMENT_CB)]
                if config.optional_comment and tx.comment
                else None
            )
            await state.set_state(EditFlow.entering_comment)
            await respond(texts.get("edit.enter_comment"), _back_keyboard(texts, extra))
        elif field == "delete":
            await state.set_state(EditFlow.confirming_delete)
            await respond(
                texts.get("edit.confirm_delete"),
                _back_keyboard(texts, [(texts.get("edit.btn_delete_confirm"), DELETE_CONFIRM_CB)]),
            )
    await callback.answer()


async def _prompt_subcategory(
    respond: Respond, state: FSMContext, conn, texts: Texts, ledger_id: int, parent_id: str
) -> None:
    subcategories = categories_db.list_categories(conn, ledger_id, parent_id=parent_id)
    await state.set_state(EditFlow.choosing_subcategory)
    await respond(
        texts.get("common.choose_subcategory"),
        build_choice_keyboard(
            [(c.id, c.name) for c in subcategories],
            callback_prefix=SUBCATEGORY_PREFIX,
            extra_buttons=[
                (texts.get("edit.btn_no_subcategory"), NO_SUBCATEGORY_CB),
                (texts.get("edit.btn_back"), BACK_CB),
            ],
        ),
    )


@router.message(EditFlow.entering_amount, F.text.regexp(EXPENSE_AMOUNT_RE))
async def enter_amount(message: Message, state: FSMContext, config: Config, texts: Texts) -> None:
    data = await state.get_data()
    with connect(config.db_path) as conn:
        tx = _load_editable(conn, config, message.from_user.id, data.get("transaction_id"))
    if tx is None:
        await state.clear()
        await message.answer(texts.get("edit.not_found"))
        return
    fields = amount_fields(tx, parse_amount(message.text), data.get("pending"))
    await _apply(_message_responder(message), state, config, texts, message.from_user.id, fields)


@router.message(EditFlow.entering_target_amount, F.text.regexp(EXPENSE_AMOUNT_RE))
async def enter_target_amount(message: Message, state: FSMContext, config: Config, texts: Texts) -> None:
    data = await state.get_data()
    fields = {**(data.get("pending") or {}), "target_amount": parse_amount(message.text)}
    await _apply(_message_responder(message), state, config, texts, message.from_user.id, fields)


@router.message(EditFlow.entering_comment, F.text)
async def enter_comment(message: Message, state: FSMContext, config: Config, texts: Texts) -> None:
    await _apply(
        _message_responder(message), state, config, texts, message.from_user.id, {"comment": message.text}
    )


@router.callback_query(EditFlow.entering_comment, F.data == CLEAR_COMMENT_CB)
async def clear_comment(callback: CallbackQuery, state: FSMContext, config: Config, texts: Texts) -> None:
    await _apply(_callback_responder(callback), state, config, texts, callback.from_user.id, {"comment": None})
    await callback.answer()


@router.callback_query(EditFlow.choosing_source, F.data.startswith(f"{SOURCE_PREFIX}:"))
async def choose_source(callback: CallbackQuery, state: FSMContext, config: Config, texts: Texts) -> None:
    source_id = callback.data.removeprefix(f"{SOURCE_PREFIX}:")
    user_id = callback.from_user.id
    data = await state.get_data()
    respond = _callback_responder(callback)
    with connect(config.db_path) as conn:
        tx = _load_editable(conn, config, user_id, data.get("transaction_id"))
        new_source = sources_db.get_source(conn, source_id)
    if tx is None or new_source is None:
        await state.clear()
        await respond(texts.get("edit.not_found"), None)
        await callback.answer()
        return

    fields, needs = source_change(tx, new_source, target=bool(data.get("source_target")))
    if needs is None:
        await _apply(respond, state, config, texts, user_id, fields)
    else:
        # Сумма в другой валюте — сначала спрашиваем её, в БД пишем всё одним апдейтом.
        await state.update_data(pending=fields)
        await state.set_state(
            EditFlow.entering_amount if needs == "amount" else EditFlow.entering_target_amount
        )
        await respond(
            texts.get("edit.enter_amount_new_currency", currency=new_source.currency),
            _back_keyboard(texts),
        )
    await callback.answer()


@router.callback_query(EditFlow.choosing_category, F.data.startswith(f"{CATEGORY_PREFIX}:"))
async def choose_category(callback: CallbackQuery, state: FSMContext, config: Config, texts: Texts) -> None:
    category_id = callback.data.removeprefix(f"{CATEGORY_PREFIX}:")
    ledger_id = ledger_user_id(config, callback.from_user.id)
    respond = _callback_responder(callback)
    with connect(config.db_path) as conn:
        has_children = bool(categories_db.list_categories(conn, ledger_id, parent_id=category_id))
        if has_children:
            await state.update_data(pending={"category_id": category_id})
            await _prompt_subcategory(respond, state, conn, texts, ledger_id, category_id)
    if not has_children:
        # Подкатегория старой категории к новой не относится — сбрасываем.
        await _apply(
            respond, state, config, texts, callback.from_user.id,
            {"category_id": category_id, "subcategory_id": None},
        )
    await callback.answer()


@router.callback_query(EditFlow.choosing_subcategory, F.data.startswith(f"{SUBCATEGORY_PREFIX}:"))
async def choose_subcategory(callback: CallbackQuery, state: FSMContext, config: Config, texts: Texts) -> None:
    subcategory_id = callback.data.removeprefix(f"{SUBCATEGORY_PREFIX}:")
    data = await state.get_data()
    fields = {**(data.get("pending") or {}), "subcategory_id": subcategory_id}
    await _apply(_callback_responder(callback), state, config, texts, callback.from_user.id, fields)
    await callback.answer()


@router.callback_query(EditFlow.choosing_subcategory, F.data == NO_SUBCATEGORY_CB)
async def choose_no_subcategory(callback: CallbackQuery, state: FSMContext, config: Config, texts: Texts) -> None:
    data = await state.get_data()
    fields = {**(data.get("pending") or {}), "subcategory_id": None}
    await _apply(_callback_responder(callback), state, config, texts, callback.from_user.id, fields)
    await callback.answer()


@router.callback_query(EditFlow.choosing_who, F.data.startswith(f"{WHO_PREFIX}:"))
async def choose_who(callback: CallbackQuery, state: FSMContext, config: Config, texts: Texts) -> None:
    options = who_options(config)
    index = int(callback.data.removeprefix(f"{WHO_PREFIX}:"))
    await _apply(
        _callback_responder(callback), state, config, texts, callback.from_user.id, {"who": options[index]}
    )
    await callback.answer()


@router.callback_query(EditFlow.confirming_delete, F.data == DELETE_CONFIRM_CB)
async def confirm_delete(callback: CallbackQuery, state: FSMContext, config: Config, texts: Texts) -> None:
    user_id = callback.from_user.id
    data = await state.get_data()
    tx_id = data.get("transaction_id")
    await state.clear()
    with connect(config.db_path) as conn:
        before = _load_editable(conn, config, user_id, tx_id)
        if before is None:
            await callback.message.edit_text(texts.get("edit.not_found"))
            await callback.answer()
            return
        summary = html.escape(resolve_summary(conn, before, texts))
        transactions_db.soft_delete_transaction(conn, tx_id)
        after = transactions_db.get_transaction(conn, tx_id)
        credit_warning = _touches_credit_sheet(conn, config, user_id, before, after)

    suffix = await sync_after_update(config, texts, user_id, tx_id)
    if credit_warning:
        suffix += texts.get("edit.credit_sheet_warning")
    await callback.message.edit_text(texts.get("edit.deleted", summary=summary, suffix=suffix))
    await callback.answer()
