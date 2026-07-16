from __future__ import annotations

import logging

import gspread

from willem.config import Config
from willem.db.transactions import Transaction
from willem.timeutil import format_sheet_datetime

logger = logging.getLogger(__name__)

SHEET_NAME = "Транзакции"
ID_COLUMN = 11

_TYPE_LABELS = {
    "expense": "Расход",
    "income": "Доход",
    "transfer": "Перевод",
    "adjustment": "Коррекция",
}

_client_cache: gspread.Client | None = None


def _get_client(config: Config) -> gspread.Client:
    global _client_cache
    if _client_cache is None:
        _client_cache = gspread.service_account(filename=config.google_sheets_credentials_path)
    return _client_cache


def _get_worksheet(config: Config) -> gspread.Worksheet:
    spreadsheet = _get_client(config).open_by_key(config.google_sheets_spreadsheet_id)
    return spreadsheet.worksheet(SHEET_NAME)


def row_for_transaction(
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


def append_transaction(
    config: Config,
    tx: Transaction,
    *,
    source_name: str,
    category_name: str | None = None,
    target_name: str | None = None,
) -> bool:
    """Синхронно, с одним ретраем, пытается записать операцию в Sheets.

    Блокирующий (gspread не asyncio) — вызывающий код должен уводить это в поток.
    """
    row = row_for_transaction(
        tx,
        source_name=source_name,
        category_name=category_name,
        target_name=target_name,
        tz_name=config.timezone,
    )
    for attempt in (1, 2):
        try:
            worksheet = _get_worksheet(config)
            existing_ids = set(worksheet.col_values(ID_COLUMN)[1:])
            if tx.id in existing_ids:
                return True
            worksheet.append_row(row, value_input_option="USER_ENTERED")
            return True
        except Exception:
            logger.exception("Ошибка записи в Google Sheets (попытка %s/2)", attempt)
    return False
