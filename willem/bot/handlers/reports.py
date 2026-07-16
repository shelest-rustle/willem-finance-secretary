from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, or_f
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from willem.bot.amount import EXPENSE_AMOUNT_RE, parse_amount
from willem.bot.keyboards import (
    BALANCES_BUTTON,
    LAST_BUTTON,
    TODAY_BUTTON,
    WEEK_BUTTON,
    build_choice_keyboard,
)
from willem.config import Config
from willem.db import categories as categories_db
from willem.db import sources as sources_db
from willem.db import transactions as transactions_db
from willem.db.connection import connect
from willem.db.transactions import Transaction
from willem.formatting import currency_symbol, format_amount
from willem.timeutil import format_local_datetime, period_bounds

router = Router(name="reports")

EDIT_AMOUNT_CB = "edl_amount"
EDIT_COMMENT_CB = "edl_comment"
EDIT_CATEGORY_CB = "edl_category"
EDIT_CATEGORY_PREFIX = "edl_category_pick"


class EditLastFlow(StatesGroup):
    entering_amount = State()
    entering_comment = State()
    choosing_category = State()


def _category_word(count: int) -> str:
    return "категории" if count == 1 else "категориям"


def _period_report_text(period_transactions: list[Transaction], label: str) -> str:
    expenses = [t for t in period_transactions if t.type == "expense"]
    if not expenses:
        return f"{label}: трат нет."

    by_currency: dict[str, tuple[float, set[str]]] = {}
    for t in expenses:
        total, category_ids = by_currency.get(t.currency, (0.0, set()))
        by_currency[t.currency] = (total + t.amount, category_ids | {t.category_id})

    parts = [
        f"{format_amount(total)} {currency_symbol(currency)} по {len(category_ids)} "
        f"{_category_word(len(category_ids))}"
        for currency, (total, category_ids) in sorted(by_currency.items())
    ]
    return f"{label}: " + "; ".join(parts) + "."


def _balances_text(balances: list[tuple[sources_db.Source, float]]) -> str:
    if not balances:
        return "Нет активных источников."

    ordered = sorted(balances, key=lambda pair: pair[1], reverse=True)
    lines = [
        f"{source.name}: {format_amount(balance)} {currency_symbol(source.currency)}"
        for source, balance in ordered
    ]

    totals: dict[str, float] = {}
    for source, balance in balances:
        totals[source.currency] = totals.get(source.currency, 0.0) + balance
    total_parts = [
        f"{format_amount(total)} {currency_symbol(currency)}"
        for currency, total in sorted(totals.items())
    ]

    return (
        "Остатки. ⚖️\n\n" + "\n".join(lines) + "\n\nВсего: " + ", ".join(total_parts) + "."
    )


def _transaction_summary(
    tx: Transaction,
    *,
    source_name: str,
    category_name: str | None = None,
    target_name: str | None = None,
) -> str:
    symbol = currency_symbol(tx.currency)
    amount = format_amount(tx.amount)

    if tx.type == "expense":
        return f"💸 {amount} {symbol} — {category_name} ({source_name})"
    if tx.type == "income":
        return f"💰 {amount} {symbol} — {source_name}"
    if tx.type == "transfer":
        if tx.currency == tx.target_currency:
            return f"🔄 {amount} {symbol} {source_name} → {target_name}"
        target_amount = format_amount(tx.target_amount)
        target_symbol = currency_symbol(tx.target_currency)
        return f"🔄 {amount} {symbol} {source_name} → {target_amount} {target_symbol} {target_name}"

    sign = "+" if tx.amount > 0 else ""
    return f"⚖️ {sign}{amount} {symbol} — {source_name}"


def _resolve_summary(conn, tx: Transaction) -> str:
    source = sources_db.get_source(conn, tx.source_id)
    category = categories_db.get_category(conn, tx.category_id) if tx.category_id else None
    target = sources_db.get_source(conn, tx.target_source_id) if tx.target_source_id else None
    return _transaction_summary(
        tx,
        source_name=source.name,
        category_name=category.name if category else None,
        target_name=target.name if target else None,
    )


def _editable_fields(tx_type: str) -> list[str]:
    if tx_type == "expense":
        return ["amount", "category", "comment"]
    if tx_type in ("income", "adjustment"):
        return ["amount", "comment"]
    return ["comment"]  # transfer


def _edit_last_keyboard(tx_type: str) -> InlineKeyboardMarkup:
    fields = _editable_fields(tx_type)
    builder = InlineKeyboardBuilder()
    if "amount" in fields:
        builder.button(text="✏️ Сумма", callback_data=EDIT_AMOUNT_CB)
    if "category" in fields:
        builder.button(text="🗂 Категория", callback_data=EDIT_CATEGORY_CB)
    builder.button(text="💬 Комментарий", callback_data=EDIT_COMMENT_CB)
    builder.adjust(2)
    return builder.as_markup()


@router.message(or_f(Command("today"), F.text == TODAY_BUTTON))
async def show_today(message: Message, state: FSMContext, config: Config) -> None:
    await state.clear()
    start, end = period_bounds("today", config.timezone)
    with connect(config.db_path) as conn:
        period_transactions = transactions_db.sum_by_type_and_period(
            conn, message.from_user.id, start, end
        )
    await message.answer(_period_report_text(period_transactions, "Сегодня"))


@router.message(or_f(Command("week"), F.text == WEEK_BUTTON))
async def show_week(message: Message, state: FSMContext, config: Config) -> None:
    await state.clear()
    start, end = period_bounds("week", config.timezone)
    with connect(config.db_path) as conn:
        period_transactions = transactions_db.sum_by_type_and_period(
            conn, message.from_user.id, start, end
        )
    await message.answer(_period_report_text(period_transactions, "За неделю"))


@router.message(or_f(Command("balances"), F.text == BALANCES_BUTTON))
async def show_balances(message: Message, state: FSMContext, config: Config) -> None:
    await state.clear()
    with connect(config.db_path) as conn:
        active_sources = sources_db.list_sources(conn, message.from_user.id)
        balances = [
            (source, transactions_db.get_source_balance(conn, source.id))
            for source in active_sources
        ]
    await message.answer(_balances_text(balances))


@router.message(or_f(Command("last"), F.text == LAST_BUTTON))
async def show_last(message: Message, state: FSMContext, config: Config) -> None:
    await state.clear()
    with connect(config.db_path) as conn:
        recent = transactions_db.list_recent(conn, message.from_user.id, limit=5)
        lines = [
            f"{format_local_datetime(tx.created_at_utc, config.timezone)} · {_resolve_summary(conn, tx)}"
            for tx in recent
        ]

    if not lines:
        await message.answer("Операций пока нет. 📋")
        return
    await message.answer("Последние операции. 📋\n\n" + "\n".join(lines))


@router.message(Command("delete_last"))
async def delete_last(message: Message, state: FSMContext, config: Config) -> None:
    await state.clear()
    with connect(config.db_path) as conn:
        recent = transactions_db.list_recent(conn, message.from_user.id, limit=1)
        if not recent:
            await message.answer("Операций пока нет. 🗑")
            return
        tx = recent[0]
        summary = _resolve_summary(conn, tx)
        transactions_db.soft_delete_transaction(conn, tx.id)
    await message.answer(f"Удалил: {summary}. 🗑")


@router.message(Command("edit_last"))
async def edit_last(message: Message, state: FSMContext, config: Config) -> None:
    await state.clear()
    with connect(config.db_path) as conn:
        recent = transactions_db.list_recent(conn, message.from_user.id, limit=1)
        if not recent:
            await message.answer("Операций пока нет. ✏️")
            return
        tx = recent[0]
        summary = _resolve_summary(conn, tx)

    await state.update_data(transaction_id=tx.id, currency=tx.currency)
    await message.answer(
        f"Последняя операция: {summary}. Что изменить?",
        reply_markup=_edit_last_keyboard(tx.type),
    )


@router.callback_query(F.data == EDIT_AMOUNT_CB)
async def start_edit_amount(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(EditLastFlow.entering_amount)
    await callback.message.edit_text("Новая сумма. ✏️")
    await callback.answer()


@router.message(EditLastFlow.entering_amount, F.text.regexp(EXPENSE_AMOUNT_RE))
async def finish_edit_amount(message: Message, state: FSMContext, config: Config) -> None:
    text = await _apply_amount_edit(state, config, parse_amount(message.text))
    await message.answer(text)


@router.callback_query(F.data == EDIT_COMMENT_CB)
async def start_edit_comment(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(EditLastFlow.entering_comment)
    await callback.message.edit_text("Новый комментарий. 💬")
    await callback.answer()


@router.message(EditLastFlow.entering_comment, F.text)
async def finish_edit_comment(message: Message, state: FSMContext, config: Config) -> None:
    text = await _apply_comment_edit(state, config, message.text)
    await message.answer(text)


@router.callback_query(F.data == EDIT_CATEGORY_CB)
async def start_edit_category(callback: CallbackQuery, state: FSMContext, config: Config) -> None:
    with connect(config.db_path) as conn:
        active_categories = categories_db.list_categories(conn, callback.from_user.id)
    await state.set_state(EditLastFlow.choosing_category)
    keyboard = build_choice_keyboard(
        [(c.id, c.name) for c in active_categories], callback_prefix=EDIT_CATEGORY_PREFIX
    )
    await callback.message.edit_text("Выберите категорию. 🗂", reply_markup=keyboard)
    await callback.answer()


@router.callback_query(
    EditLastFlow.choosing_category, F.data.startswith(f"{EDIT_CATEGORY_PREFIX}:")
)
async def finish_edit_category(callback: CallbackQuery, state: FSMContext, config: Config) -> None:
    category_id = callback.data.removeprefix(f"{EDIT_CATEGORY_PREFIX}:")
    text = await _apply_category_edit(state, config, category_id)
    await callback.message.edit_text(text)
    await callback.answer()


async def _apply_amount_edit(state: FSMContext, config: Config, amount: float) -> str:
    data = await state.get_data()
    await state.clear()
    with connect(config.db_path) as conn:
        transactions_db.update_transaction(conn, data["transaction_id"], amount=amount)
    symbol = currency_symbol(data["currency"])
    return f"Сумма обновлена: {format_amount(amount)} {symbol}. ✏️"


async def _apply_comment_edit(state: FSMContext, config: Config, comment: str) -> str:
    data = await state.get_data()
    await state.clear()
    with connect(config.db_path) as conn:
        transactions_db.update_transaction(conn, data["transaction_id"], comment=comment)
    return f"Комментарий обновлён: «{comment}». 💬"


async def _apply_category_edit(state: FSMContext, config: Config, category_id: str) -> str:
    data = await state.get_data()
    await state.clear()
    with connect(config.db_path) as conn:
        category = categories_db.get_category(conn, category_id)
        transactions_db.update_transaction(conn, data["transaction_id"], category_id=category_id)
    return f"Категория обновлена: «{category.name}». 🗂"
