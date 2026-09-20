from __future__ import annotations

import asyncio
import logging
from enum import Enum

import gspread

from willem.config import Config

logger = logging.getLogger(__name__)

# Layout кредитных листов ("МТС Кредит Лики" и аналоги) — см. MTS_CREDIT_TEST.md.
# Ёмкость обеих таблиц фиксированная и намеренно не расширяется автоматически (~2 года
# форы для графика платежей, 30 записей для досрочных взносов) — при исчерпании нужно
# вручную протянуть формулы вниз в самой таблице.

# Таблица 1 «ПЛАТЕЖИ ПО ГРАФИКУ» — обычные ежемесячные платежи.
SCHEDULE_FIRST_ROW = 8
SCHEDULE_LAST_ROW = 31
SCHEDULE_ACTUAL_COL = "D"  # Факт оплаты
SCHEDULE_OVERPAY_COL = "I"  # Что сделать, если заплатила больше плана

# Таблица 2 «ДОСРОЧНЫЕ ПЛАТЕЖИ И ПЕРЕПЛАТЫ» — плоский лог, дописывается снизу.
EARLY_FIRST_ROW = 35
EARLY_LAST_ROW = 64
EARLY_DATE_COL = "A"
EARLY_AMOUNT_COL = "B"
EARLY_ACTION_COL = "C"
EARLY_COMMENT_COL = "D"

# Литералы data validation в обеих таблицах — должны совпадать дословно с выпадающими
# списками в самих листах.
OVERPAYMENT_SHORTEN = "Сократить срок"
OVERPAYMENT_REDUCE = "Уменьшить платёж"

_client_cache: gspread.Client | None = None


class CreditSheetCapacityError(RuntimeError):
    """В зарезервированном диапазоне не осталось свободных строк — нужно расширять вручную."""


class CreditSyncOutcome(Enum):
    OK = "ok"
    CAPACITY_EXHAUSTED = "capacity_exhausted"
    ERROR = "error"


def _get_client(config: Config) -> gspread.Client:
    global _client_cache
    if _client_cache is None:
        _client_cache = gspread.service_account(filename=config.google_sheets_credentials_path)
    return _client_cache


def _get_worksheet(config: Config, sheet_title: str) -> gspread.Worksheet:
    spreadsheet = _get_client(config).open_by_key(config.google_sheets_spreadsheet_id)
    return spreadsheet.worksheet(sheet_title)


def _find_regular_payment_row(worksheet: gspread.Worksheet) -> int | None:
    """Первая строка графика, где ещё нет реально внесённого факта оплаты.

    "Ещё не оплачено" — это пустая ячейка ИЛИ до сих пор формула (строка-кандидат на
    оплату по умолчанию зеркалит план через `=C<row>`, пока в неё не впишут литерал —
    см. MTS_CREDIT_TEST.md, раздел 2).
    """
    formulas = worksheet.get(
        f"{SCHEDULE_ACTUAL_COL}{SCHEDULE_FIRST_ROW}:{SCHEDULE_ACTUAL_COL}{SCHEDULE_LAST_ROW}",
        value_render_option="FORMULA",
    )
    for offset in range(SCHEDULE_LAST_ROW - SCHEDULE_FIRST_ROW + 1):
        cell = str(formulas[offset][0]) if offset < len(formulas) and formulas[offset] else ""
        if cell == "" or cell.startswith("="):
            return SCHEDULE_FIRST_ROW + offset
    return None


def _find_early_payment_row(worksheet: gspread.Worksheet) -> int | None:
    """Первая пустая строка в логе досрочных взносов (по дате взноса, колонка A)."""
    values = worksheet.get(f"{EARLY_DATE_COL}{EARLY_FIRST_ROW}:{EARLY_DATE_COL}{EARLY_LAST_ROW}")
    for offset in range(EARLY_LAST_ROW - EARLY_FIRST_ROW + 1):
        cell = str(values[offset][0]).strip() if offset < len(values) and values[offset] else ""
        if cell == "":
            return EARLY_FIRST_ROW + offset
    return None


def record_regular_payment(
    config: Config, sheet_title: str, amount: float, *, reduce_payment: bool
) -> bool:
    """Обычный ежемесячный платёж — пишет факт оплаты в первую незаполненную строку
    графика (и, при необходимости, выбор «Уменьшить платёж»/«Сократить срок»)."""
    action_label = OVERPAYMENT_REDUCE if reduce_payment else OVERPAYMENT_SHORTEN
    for attempt in (1, 2):
        try:
            worksheet = _get_worksheet(config, sheet_title)
            row = _find_regular_payment_row(worksheet)
            if row is None:
                raise CreditSheetCapacityError(
                    f"В графике листа «{sheet_title}» не осталось свободных строк "
                    f"({SCHEDULE_FIRST_ROW}:{SCHEDULE_LAST_ROW})."
                )
            worksheet.batch_update(
                [
                    {"range": f"{SCHEDULE_ACTUAL_COL}{row}", "values": [[amount]]},
                    {"range": f"{SCHEDULE_OVERPAY_COL}{row}", "values": [[action_label]]},
                ],
                value_input_option="USER_ENTERED",
            )
            return True
        except CreditSheetCapacityError:
            raise
        except Exception:
            logger.exception(
                "Ошибка записи обычного платежа в кредитный лист «%s» (попытка %s/2)",
                sheet_title,
                attempt,
            )
    return False


def record_early_payment(
    config: Config,
    sheet_title: str,
    *,
    date_str: str,
    amount: float,
    reduce_payment: bool,
    comment: str | None,
) -> bool:
    """Досрочный взнос/переплата — дописывает строку в конец лога Таблицы 2."""
    action_label = OVERPAYMENT_REDUCE if reduce_payment else OVERPAYMENT_SHORTEN
    for attempt in (1, 2):
        try:
            worksheet = _get_worksheet(config, sheet_title)
            row = _find_early_payment_row(worksheet)
            if row is None:
                raise CreditSheetCapacityError(
                    f"В логе досрочных взносов листа «{sheet_title}» не осталось "
                    f"свободных строк ({EARLY_FIRST_ROW}:{EARLY_LAST_ROW})."
                )
            worksheet.update(
                range_name=f"{EARLY_DATE_COL}{row}:{EARLY_COMMENT_COL}{row}",
                values=[[date_str, amount, action_label, comment or ""]],
                value_input_option="USER_ENTERED",
            )
            return True
        except CreditSheetCapacityError:
            raise
        except Exception:
            logger.exception(
                "Ошибка записи досрочного взноса в кредитный лист «%s» (попытка %s/2)",
                sheet_title,
                attempt,
            )
    return False


async def sync_credit_payment(
    config: Config,
    *,
    sheet_title: str,
    is_early: bool,
    amount: float,
    reduce_payment: bool,
    comment: str | None,
    date_str: str,
) -> CreditSyncOutcome:
    """Блокирующие вызовы gspread уходят в отдельный поток — как и `sheets.append_transaction`."""
    try:
        if is_early:
            success = await asyncio.to_thread(
                record_early_payment,
                config,
                sheet_title,
                date_str=date_str,
                amount=amount,
                reduce_payment=reduce_payment,
                comment=comment,
            )
        else:
            success = await asyncio.to_thread(
                record_regular_payment,
                config,
                sheet_title,
                amount,
                reduce_payment=reduce_payment,
            )
    except CreditSheetCapacityError:
        return CreditSyncOutcome.CAPACITY_EXHAUSTED
    return CreditSyncOutcome.OK if success else CreditSyncOutcome.ERROR
