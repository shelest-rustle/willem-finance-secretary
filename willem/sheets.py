from __future__ import annotations

import logging

import gspread

from willem.config import Config
from willem.db.transactions import Transaction
from willem.timeutil import format_sheet_datetime

logger = logging.getLogger(__name__)

_TYPE_LABELS = {
    "expense": "Расход",
    "income": "Доход",
    "transfer": "Перевод",
    "adjustment": "Коррекция",
}

_HOUSEHOLD_TYPE_LABELS = {
    "expense": "Расход",
    "income": "Доход",
    "transfer": "Перевод",
    "adjustment": "Коррекция остатка",
}

_client_cache: gspread.Client | None = None


def _get_client(config: Config) -> gspread.Client:
    global _client_cache
    if _client_cache is None:
        _client_cache = gspread.service_account(filename=config.google_sheets_credentials_path)
    return _client_cache


def _get_worksheet(config: Config) -> gspread.Worksheet:
    spreadsheet = _get_client(config).open_by_key(config.google_sheets_spreadsheet_id)
    return spreadsheet.worksheet(config.sheet_name)


def _kzt_equivalent(tx: Transaction) -> tuple[float | None, float | None]:
    """Курс и сумма в KZT.

    Для переводов "Курс" — кросс-курс между валютой списания и валютой зачисления,
    вычисленный прямо из двух сумм самого перевода (Сумма списания / Сумма зачисления,
    т.е. сколько единиц валюты списания за 1 единицу валюты зачисления) — работает для
    любой пары валют, без привязки к KZT и без внешних справочников. "Сумма в KZT" —
    отдельно, и только там, где это выводится из уже имеющихся данных: сама операция
    в KZT, либо перевод, у которого один из концов в KZT. Для одиночных операций
    (expense/income/adjustment) не в KZT и для переводов между двумя не-KZT валютами
    сумма в KZT не выводится — колонка остаётся пустой (см. UPGRADE_spec.md, 9.7)."""
    if tx.type == "transfer":
        rate = tx.amount / tx.target_amount if tx.target_amount else None
        if tx.currency == "KZT":
            amount_kzt = tx.amount
        elif tx.target_currency == "KZT":
            amount_kzt = tx.target_amount
        else:
            amount_kzt = None
        return rate, amount_kzt
    if tx.currency == "KZT":
        return 1.0, tx.amount
    return None, None


def _default_row(
    tx: Transaction,
    *,
    source_name: str,
    category_name: str | None,
    target_name: str | None,
    tz_name: str,
) -> list:
    return [
        format_sheet_datetime(tx.created_at_utc, tz_name),
        _TYPE_LABELS[tx.type],
        tx.amount,
        tx.currency,
        source_name,
        category_name or "",
        tx.comment or "",
        tx.target_amount if tx.target_amount is not None else "",
        tx.target_currency or "",
        target_name or "",
        tx.id,
    ]


def _household_row(
    tx: Transaction,
    *,
    source_name: str,
    category_name: str | None,
    subcategory_name: str | None,
    target_name: str | None,
    target_kind: str | None,
    tz_name: str,
) -> list:
    if tx.type == "income":
        debit, credit = "", source_name
    elif tx.type == "transfer":
        debit, credit = source_name, target_name or ""
    else:  # expense, adjustment
        debit, credit = source_name, ""

    type_label = _HOUSEHOLD_TYPE_LABELS[tx.type]
    if tx.type == "transfer" and target_kind == "debt":
        type_label = "Погашение долга / кредита"

    rate, amount_kzt = _kzt_equivalent(tx)

    return [
        format_sheet_datetime(tx.created_at_utc, tz_name),
        type_label,
        tx.who or "",
        category_name or "",
        subcategory_name or "",
        debit,
        credit,
        tx.amount,
        tx.currency,
        tx.target_amount if tx.target_amount is not None else "",
        tx.target_currency or "",
        rate if rate is not None else "",
        amount_kzt if amount_kzt is not None else "",
        tx.comment or "",
        tx.id,
    ]


def row_for_transaction(
    tx: Transaction,
    *,
    source_name: str,
    category_name: str | None = None,
    subcategory_name: str | None = None,
    target_name: str | None = None,
    target_kind: str | None = None,
    tz_name: str,
    profile_name: str = "willem",
) -> list:
    if profile_name == "pantalone":
        return _household_row(
            tx,
            source_name=source_name,
            category_name=category_name,
            subcategory_name=subcategory_name,
            target_name=target_name,
            target_kind=target_kind,
            tz_name=tz_name,
        )
    return _default_row(
        tx,
        source_name=source_name,
        category_name=category_name,
        target_name=target_name,
        tz_name=tz_name,
    )


def append_transaction(
    config: Config,
    tx: Transaction,
    *,
    source_name: str,
    category_name: str | None = None,
    subcategory_name: str | None = None,
    target_name: str | None = None,
    target_kind: str | None = None,
) -> bool:
    """Синхронно, с одним ретраем, пытается записать операцию в Sheets.

    Блокирующий (gspread не asyncio) — вызывающий код должен уводить это в поток.
    """
    row = row_for_transaction(
        tx,
        source_name=source_name,
        category_name=category_name,
        subcategory_name=subcategory_name,
        target_name=target_name,
        target_kind=target_kind,
        tz_name=config.timezone,
        profile_name=config.profile_name,
    )
    id_column = len(row)
    for attempt in (1, 2):
        try:
            worksheet = _get_worksheet(config)
            existing_ids = set(worksheet.col_values(id_column)[1:])
            if tx.id in existing_ids:
                return True
            worksheet.append_row(row, value_input_option="USER_ENTERED")
            return True
        except Exception:
            logger.exception("Ошибка записи в Google Sheets (попытка %s/2)", attempt)
    return False
