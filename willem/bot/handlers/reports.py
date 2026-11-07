from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.filters import Command, or_f
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from willem.bot.keyboards import (
    BALANCES_BUTTON,
    LAST_BUTTON,
    TODAY_BUTTON,
    WEEK_BUTTON,
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

LAST_LIMIT = 20


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


def short_id(tx: Transaction) -> str:
    """Первые 8 символов uuid — по ним операцию можно найти в «Изменить»."""
    return tx.id[:8]


def resolve_summary(conn, tx: Transaction, texts: Texts) -> str:
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


def last_line(conn, tx: Transaction, texts: Texts, tz_name: str) -> str:
    """Строка «Последних»: ID (моноширинный — копируется тапом) · дата · summary.
    Бот работает в ParseMode.HTML, а имена источников/категорий задают пользователи —
    поэтому summary экранируется."""
    return (
        f"<code>{short_id(tx)}</code> · {format_local_datetime(tx.created_at_utc, tz_name)} · "
        f"{html.escape(resolve_summary(conn, tx, texts))}"
    )


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
        recent = transactions_db.list_recent(
            conn, ledger_user_id(config, message.from_user.id), limit=LAST_LIMIT
        )
        lines = [last_line(conn, tx, texts, config.timezone) for tx in recent]

    if not lines:
        await message.answer(texts.get("reports.last_empty"))
        return
    await message.answer(
        f"{texts.get('reports.last_header')}\n\n" + "\n".join(lines)
        + f"\n\n{texts.get('reports.last_edit_hint')}"
    )