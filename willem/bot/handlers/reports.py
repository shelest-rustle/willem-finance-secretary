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
from willem.config import Config, ledger_user_id
from willem.db import categories as categories_db
from willem.db import sources as sources_db
from willem.db import transactions as transactions_db
from willem.db.sources import matches_keywords
from willem.db.connection import connect
from willem.db.transactions import Transaction
from willem.formatting import currency_symbol, format_amount, format_currency_totals
from willem.texts import Texts
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


def _period_report_text(period_transactions: list[Transaction], label: str, texts: Texts) -> str:
    expenses = [t for t in period_transactions if t.type == "expense"]
    if not expenses:
        return texts.get("reports.period_no_expenses", label=label)

    by_currency: dict[str, tuple[float, set[str]]] = {}
    for t in expenses:
        total, category_ids = by_currency.get(t.currency, (0.0, set()))
        by_currency[t.currency] = (total + t.amount, category_ids | {t.category_id})

    parts = [
        texts.get(
            "reports.period_line",
            total=format_amount(total),
            symbol=currency_symbol(currency),
            count=len(category_ids),
            category_word=_category_word(len(category_ids)),
        )
        for currency, (total, category_ids) in sorted(by_currency.items())
    ]
    return f"{label}: " + "; ".join(parts) + "."


def _period_category_breakdown_text(
    period_transactions: list[Transaction], category_names: dict[str, str], texts: Texts
) -> str:
    """Разрез расходов периода по категориям — сколько потрачено на каждую (по всем
    валютам, без смешивания их друг с другом). Пусто, если трат за период не было."""
    expenses = [t for t in period_transactions if t.type == "expense"]
    if not expenses:
        return ""

    totals: dict[tuple[str, str], float] = {}
    for t in expenses:
        key = (t.category_id, t.currency)
        totals[key] = totals.get(key, 0.0) + t.amount

    by_category: dict[str, list[tuple[str, float]]] = {}
    for (category_id, currency), amount in totals.items():
        by_category.setdefault(category_id, []).append((currency, amount))

    lines = [
        texts.get(
            "reports.period_category_line",
            name=category_names.get(category_id, "?"),
            amounts=format_currency_totals(by_category[category_id]),
        )
        for category_id in sorted(by_category, key=lambda cid: category_names.get(cid, ""))
    ]
    return f"{texts.get('reports.period_category_header')}\n\n" + "\n".join(lines)


def _owner_sort_key(owner: str | None, preferred: list[str]) -> tuple[int, str]:
    if owner is None:
        return (2, "")
    if owner in preferred:
        return (0, str(preferred.index(owner)).zfill(3))
    return (1, owner)


def _balance_lines(balances: list[tuple[sources_db.Source, float]], texts: Texts) -> list[str]:
    ordered = sorted(balances, key=lambda pair: pair[1], reverse=True)
    return [
        texts.get(
            "reports.balances_line",
            source=source.name,
            amount=format_amount(balance),
            symbol=currency_symbol(source.currency),
        )
        for source, balance in ordered
    ]


def _grouped_lines(
    balances: list[tuple[sources_db.Source, float]],
    texts: Texts,
    owner_order: list[str],
    line_builder,
) -> list[str]:
    """Строит список строк, сгруппированных по владельцу источника (`config.people`,
    затем прочие метки владельца, например "Семья"). Для профилей без владельцев у
    источников (например `willem`) группировка не показывается — плоский список."""
    owners = {source.owner for source, _ in balances}
    if owners == {None}:
        lines = line_builder(balances, texts)
    else:
        lines = []
        for owner in sorted(owners, key=lambda o: _owner_sort_key(o, owner_order)):
            group = [(s, b) for s, b in balances if s.owner == owner]
            if owner is not None:
                lines.append(texts.get("reports.balances_owner_header", owner=owner))
            lines.extend(line_builder(group, texts))
            lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
    return lines


def _balance_block(
    balances: list[tuple[sources_db.Source, float]],
    texts: Texts,
    *,
    header_key: str,
    owner_order: list[str],
    total_key: str | None = None,
) -> str:
    lines = _grouped_lines(balances, texts, owner_order, _balance_lines)
    body = f"{texts.get(header_key)}\n\n" + "\n".join(lines)
    if total_key is None:
        return body

    totals: dict[str, float] = {}
    for source, balance in balances:
        totals[source.currency] = totals.get(source.currency, 0.0) + balance
    total_parts = [
        f"{format_amount(total)} {currency_symbol(currency)}"
        for currency, total in sorted(totals.items())
    ]
    return body + f"\n\n{texts.get(total_key)}" + ", ".join(total_parts) + "."


def _debt_wallet_lines(balances: list[tuple[sources_db.Source, float]], texts: Texts) -> list[str]:
    """Для кредиток/кубышек — не просто остаток, а разбивка: лимит, доступно, к оплате.

    "Остаток" (`balance`) источника — это то, что сейчас реально доступно к трате по
    карте (уменьшается расходами с неё, увеличивается погашениями/пополнениями) — то
    есть уже само по себе и есть "На счету", без каких-либо дополнительных вычислений.
    "К оплате" — это то, что из выданного лимита уже израсходовано: лимит минус остаток.
    Для свежей, ещё не потраченной карты остаток должен быть выставлен равным лимиту
    (через /sources → "⚖️ Остаток") — тогда "к оплате" закономерно покажет 0."""
    ordered = sorted(balances, key=lambda pair: pair[1], reverse=True)
    lines: list[str] = []
    for source, balance in ordered:
        symbol = currency_symbol(source.currency)
        lines.append(texts.get("reports.debt_wallet_name", name=source.name))
        if source.credit_limit is None:
            lines.append(
                texts.get(
                    "reports.debt_wallet_no_limit", amount=format_amount(balance), symbol=symbol
                )
            )
        else:
            available = balance
            owed = source.credit_limit - balance
            lines.append(
                texts.get(
                    "reports.debt_wallet_limit",
                    amount=format_amount(source.credit_limit),
                    symbol=symbol,
                )
            )
            lines.append(
                texts.get("reports.debt_wallet_available", amount=format_amount(available), symbol=symbol)
            )
            lines.append(
                texts.get("reports.debt_wallet_owed", amount=format_amount(owed), symbol=symbol)
            )
        lines.append("")
    return lines


def _debt_wallet_block(
    balances: list[tuple[sources_db.Source, float]], texts: Texts, owner_order: list[str]
) -> str:
    lines = _grouped_lines(balances, texts, owner_order, _debt_wallet_lines)
    return f"{texts.get('reports.debt_wallets_header')}\n\n" + "\n".join(lines)


def _balances_text(
    balances: list[tuple[sources_db.Source, float]], texts: Texts, config: Config
) -> str:
    """Активы (`kind != 'debt'`) — секция "Кровоток", как и раньше. Долговые источники
    (`kind == 'debt'`) — если в профиле заданы `debt_wallet_keywords`/`debt_obligation_keywords`
    (сейчас только Pantalone), делятся по подстроке в названии на "Долговые кошельки"
    (кредитки/кубышки — с разбивкой лимит/доступно/к оплате, без общего итога, т.к. лимиты
    и остатки разных источников складывать в одну цифру некорректно) и "Долговые обязательства"
    (личные долги). Источники, не подошедшие ни под одно ключевое слово (например installment-
    кредиты, которые теперь ведутся в отдельных Google-листах), в /balances не показываются.
    Если ни одно ключевое слово не задано — прежнее поведение: единая секция "Долги" с итогом."""
    assets = [(s, b) for s, b in balances if s.kind != "debt"]
    debts = [(s, b) for s, b in balances if s.kind == "debt"]

    owner_order = list(dict.fromkeys(config.people.values()))
    blocks = []
    if assets:
        blocks.append(
            _balance_block(
                assets, texts,
                header_key="reports.balances_header", total_key="reports.balances_total_prefix",
                owner_order=owner_order,
            )
        )

    if config.debt_wallet_keywords or config.debt_obligation_keywords:
        wallets = [(s, b) for s, b in debts if matches_keywords(s.name, config.debt_wallet_keywords)]
        obligations = [
            (s, b)
            for s, b in debts
            if not matches_keywords(s.name, config.debt_wallet_keywords)
            and matches_keywords(s.name, config.debt_obligation_keywords)
        ]
        if wallets:
            blocks.append(_debt_wallet_block(wallets, texts, owner_order))
        if obligations:
            blocks.append(
                _balance_block(obligations, texts, header_key="reports.debts_header", owner_order=owner_order)
            )
    elif debts:
        blocks.append(
            _balance_block(
                debts, texts,
                header_key="reports.debts_header", total_key="reports.debts_total_prefix",
                owner_order=owner_order,
            )
        )

    if not blocks:
        return texts.get("reports.balances_empty")
    return "\n\n".join(blocks)


def _transaction_summary(
    tx: Transaction,
    texts: Texts,
    *,
    source_name: str,
    category_name: str | None = None,
    target_name: str | None = None,
) -> str:
    symbol = currency_symbol(tx.currency)
    amount = format_amount(tx.amount)

    if tx.type == "expense":
        return texts.get(
            "reports.summary_expense", amount=amount, symbol=symbol, category=category_name,
            source=source_name,
        )
    if tx.type == "income":
        return texts.get("reports.summary_income", amount=amount, symbol=symbol, source=source_name)
    if tx.type == "transfer":
        if tx.currency == tx.target_currency:
            return texts.get(
                "reports.summary_transfer_same",
                amount=amount, symbol=symbol, source=source_name, target=target_name,
            )
        target_amount = format_amount(tx.target_amount)
        target_symbol = currency_symbol(tx.target_currency)
        return texts.get(
            "reports.summary_transfer_diff",
            amount=amount, symbol=symbol, source=source_name,
            target_amount=target_amount, target_symbol=target_symbol, target=target_name,
        )

    sign = "+" if tx.amount > 0 else ""
    return texts.get(
        "reports.summary_adjustment", sign=sign, amount=amount, symbol=symbol, source=source_name
    )


def _resolve_summary(conn, tx: Transaction, texts: Texts) -> str:
    source = sources_db.get_source(conn, tx.source_id)
    category = categories_db.get_category(conn, tx.category_id) if tx.category_id else None
    target = sources_db.get_source(conn, tx.target_source_id) if tx.target_source_id else None
    return _transaction_summary(
        tx,
        texts,
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


def _resolve_category_names(conn, period_transactions: list[Transaction]) -> dict[str, str]:
    category_ids = {t.category_id for t in period_transactions if t.type == "expense"}
    names = {}
    for category_id in category_ids:
        category = categories_db.get_category(conn, category_id)
        names[category_id] = category.name if category else "?"
    return names


async def _period_report_answer(
    message: Message, config: Config, texts: Texts, user_id: int, period: str, label_key: str
) -> None:
    start, end = period_bounds(period, config.timezone)
    with connect(config.db_path) as conn:
        period_transactions = transactions_db.sum_by_type_and_period(
            conn, ledger_user_id(config, user_id), start, end
        )
        category_names = _resolve_category_names(conn, period_transactions)

    text = _period_report_text(period_transactions, texts.get(label_key), texts)
    breakdown = _period_category_breakdown_text(period_transactions, category_names, texts)
    if breakdown:
        text += "\n\n" + breakdown
    await message.answer(text)


@router.message(or_f(Command("today"), F.text == TODAY_BUTTON))
async def show_today(message: Message, state: FSMContext, config: Config, texts: Texts) -> None:
    await state.clear()
    await _period_report_answer(
        message, config, texts, message.from_user.id, "today", "reports.label_today"
    )


@router.message(or_f(Command("week"), F.text == WEEK_BUTTON))
async def show_week(message: Message, state: FSMContext, config: Config, texts: Texts) -> None:
    await state.clear()
    await _period_report_answer(
        message, config, texts, message.from_user.id, "week", "reports.label_week"
    )


@router.message(or_f(Command("balances"), F.text == BALANCES_BUTTON))
async def show_balances(message: Message, state: FSMContext, config: Config, texts: Texts) -> None:
    await state.clear()
    with connect(config.db_path) as conn:
        active_sources = sources_db.list_sources(conn, ledger_user_id(config, message.from_user.id))
        balances = [
            (source, transactions_db.get_source_balance(conn, source.id))
            for source in active_sources
        ]
    await message.answer(_balances_text(balances, texts, config))


@router.message(or_f(Command("last"), F.text == LAST_BUTTON))
async def show_last(message: Message, state: FSMContext, config: Config, texts: Texts) -> None:
    await state.clear()
    with connect(config.db_path) as conn:
        recent = transactions_db.list_recent(conn, ledger_user_id(config, message.from_user.id), limit=5)
        lines = [
            f"{format_local_datetime(tx.created_at_utc, config.timezone)} · {_resolve_summary(conn, tx, texts)}"
            for tx in recent
        ]

    if not lines:
        await message.answer(texts.get("reports.last_empty"))
        return
    await message.answer(f"{texts.get('reports.last_header')}\n\n" + "\n".join(lines))


@router.message(Command("delete_last"))
async def delete_last(message: Message, state: FSMContext, config: Config, texts: Texts) -> None:
    await state.clear()
    with connect(config.db_path) as conn:
        recent = transactions_db.list_recent(conn, ledger_user_id(config, message.from_user.id), limit=1)
        if not recent:
            await message.answer(texts.get("reports.delete_empty"))
            return
        tx = recent[0]
        summary = _resolve_summary(conn, tx, texts)
        transactions_db.soft_delete_transaction(conn, tx.id)
    await message.answer(texts.get("reports.deleted", summary=summary))


@router.message(Command("edit_last"))
async def edit_last(message: Message, state: FSMContext, config: Config, texts: Texts) -> None:
    await state.clear()
    with connect(config.db_path) as conn:
        recent = transactions_db.list_recent(conn, ledger_user_id(config, message.from_user.id), limit=1)
        if not recent:
            await message.answer(texts.get("reports.edit_empty"))
            return
        tx = recent[0]
        summary = _resolve_summary(conn, tx, texts)

    await state.update_data(transaction_id=tx.id, currency=tx.currency)
    await message.answer(
        texts.get("reports.edit_prompt", summary=summary),
        reply_markup=_edit_last_keyboard(tx.type),
    )


@router.callback_query(F.data == EDIT_AMOUNT_CB)
async def start_edit_amount(callback: CallbackQuery, state: FSMContext, texts: Texts) -> None:
    await state.set_state(EditLastFlow.entering_amount)
    await callback.message.edit_text(texts.get("reports.edit_amount_prompt"))
    await callback.answer()


@router.message(EditLastFlow.entering_amount, F.text.regexp(EXPENSE_AMOUNT_RE))
async def finish_edit_amount(message: Message, state: FSMContext, config: Config, texts: Texts) -> None:
    text = await _apply_amount_edit(state, config, texts, parse_amount(message.text))
    await message.answer(text)


@router.callback_query(F.data == EDIT_COMMENT_CB)
async def start_edit_comment(callback: CallbackQuery, state: FSMContext, texts: Texts) -> None:
    await state.set_state(EditLastFlow.entering_comment)
    await callback.message.edit_text(texts.get("reports.edit_comment_prompt"))
    await callback.answer()


@router.message(EditLastFlow.entering_comment, F.text)
async def finish_edit_comment(message: Message, state: FSMContext, config: Config, texts: Texts) -> None:
    text = await _apply_comment_edit(state, config, texts, message.text)
    await message.answer(text)


@router.callback_query(F.data == EDIT_CATEGORY_CB)
async def start_edit_category(callback: CallbackQuery, state: FSMContext, config: Config, texts: Texts) -> None:
    with connect(config.db_path) as conn:
        active_categories = categories_db.list_categories(
            conn, ledger_user_id(config, callback.from_user.id)
        )
    await state.set_state(EditLastFlow.choosing_category)
    keyboard = build_choice_keyboard(
        [(c.id, c.name) for c in active_categories], callback_prefix=EDIT_CATEGORY_PREFIX
    )
    await callback.message.edit_text(texts.get("common.choose_category"), reply_markup=keyboard)
    await callback.answer()


@router.callback_query(
    EditLastFlow.choosing_category, F.data.startswith(f"{EDIT_CATEGORY_PREFIX}:")
)
async def finish_edit_category(callback: CallbackQuery, state: FSMContext, config: Config, texts: Texts) -> None:
    category_id = callback.data.removeprefix(f"{EDIT_CATEGORY_PREFIX}:")
    text = await _apply_category_edit(state, config, texts, category_id)
    await callback.message.edit_text(text)
    await callback.answer()


async def _apply_amount_edit(state: FSMContext, config: Config, texts: Texts, amount: float) -> str:
    data = await state.get_data()
    await state.clear()
    with connect(config.db_path) as conn:
        transactions_db.update_transaction(conn, data["transaction_id"], amount=amount)
    symbol = currency_symbol(data["currency"])
    return texts.get("reports.amount_updated", amount=format_amount(amount), symbol=symbol)


async def _apply_comment_edit(state: FSMContext, config: Config, texts: Texts, comment: str) -> str:
    data = await state.get_data()
    await state.clear()
    with connect(config.db_path) as conn:
        transactions_db.update_transaction(conn, data["transaction_id"], comment=comment)
    return texts.get("reports.comment_updated", comment=comment)


async def _apply_category_edit(state: FSMContext, config: Config, texts: Texts, category_id: str) -> str:
    data = await state.get_data()
    await state.clear()
    with connect(config.db_path) as conn:
        category = categories_db.get_category(conn, category_id)
        transactions_db.update_transaction(conn, data["transaction_id"], category_id=category_id)
    return texts.get("reports.category_updated", name=category.name)
