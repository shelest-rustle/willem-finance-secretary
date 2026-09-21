from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from enum import Enum

import gspread

from willem.config import Config
from willem.db import credit_reminders as credit_reminders_db
from willem.db.connection import connect

logger = logging.getLogger(__name__)

# Layout кредитных листов ("МТС Кредит Лики" и аналоги) — см. MTS_CREDIT_TEST.md.
# Ёмкость обеих таблиц конечна и намеренно не расширяется автоматически — при
# исчерпании нужно вручную протянуть формулы вниз в самой таблице. У разных кредитов
# разный срок, поэтому длина Таблицы 1 РАЗНАЯ у разных листов (например, 24 строки
# у "МТС Кредит Лики", но 36 у "Tinkoff REF Кредит Лики") — границы обеих таблиц
# поэтому не хардкодятся, а определяются по самому листу (см. `_table_layout`).

SCHEDULE_FIRST_ROW = 8
SCHEDULE_DATE_COL = "B"     # Дата платежа
SCHEDULE_PLANNED_COL = "C"  # План платёж
SCHEDULE_ACTUAL_COL = "D"  # Факт оплаты
SCHEDULE_OVERPAY_COL = "I"  # Что сделать, если заплатила больше плана

EARLY_DATE_COL = "A"
EARLY_AMOUNT_COL = "B"
EARLY_ACTION_COL = "C"
EARLY_COMMENT_COL = "D"

# Ориентиры для определения границ таблиц — те же на всех кредитных листах.
EARLY_BLOCK_TITLE = "ДОСРОЧНЫЕ ПЛАТЕЖИ И ПЕРЕПЛАТЫ"
EARLY_TOTAL_LABEL = "Итого"
_MAX_SCAN_ROW = 300  # с большим запасом — дальше идёт "ГРАФИК БАНКА", он нас не интересует

# Литералы data validation в обеих таблицах — должны совпадать дословно с выпадающими
# списками в самих листах.
OVERPAYMENT_SHORTEN = "Сократить срок"
OVERPAYMENT_REDUCE = "Уменьшить платёж"

_client_cache: gspread.Client | None = None


class CreditSheetCapacityError(RuntimeError):
    """В зарезервированном диапазоне не осталось свободных строк — нужно расширять вручную."""


class CreditSheetLayoutError(RuntimeError):
    """Не удалось найти ориентиры структуры листа (заголовок Таблицы 2 / строку "Итого") —
    похоже, кто-то поменял разметку кредитного листа."""


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


def _table_layout(worksheet: gspread.Worksheet) -> tuple[int, int, int]:
    """(schedule_last_row, early_first_row, early_last_row) — вычисляются по факту из
    самого листа: ищем заголовок Таблицы 2 и следующую за её данными строку "Итого".
    Между последней строкой графика и заголовком Таблицы 2 всегда ровно одна пустая
    строка, а первая строка данных Таблицы 2 — через одну после заголовка (заголовок
    блока + строка с названиями колонок) — см. MTS_CREDIT_TEST.md."""
    column_a = worksheet.get(f"A{SCHEDULE_FIRST_ROW}:A{_MAX_SCAN_ROW}")
    title_row: int | None = None
    total_row: int | None = None
    for offset, row in enumerate(column_a):
        cell = str(row[0]).strip() if row else ""
        absolute_row = SCHEDULE_FIRST_ROW + offset
        if title_row is None and cell == EARLY_BLOCK_TITLE:
            title_row = absolute_row
        elif title_row is not None and cell == EARLY_TOTAL_LABEL:
            total_row = absolute_row
            break
    if title_row is None or total_row is None:
        raise CreditSheetLayoutError(
            f"Не нашёл на листе «{worksheet.title}» заголовок «{EARLY_BLOCK_TITLE}» "
            f"и/или строку «{EARLY_TOTAL_LABEL}» после него — структура листа не совпадает "
            f"с ожидаемой."
        )
    schedule_last_row = title_row - 2
    early_first_row = title_row + 2
    early_last_row = total_row - 1
    return schedule_last_row, early_first_row, early_last_row


def _find_regular_payment_row(worksheet: gspread.Worksheet, schedule_last_row: int) -> int | None:
    """Первая строка графика, где ещё нет реально внесённого факта оплаты.

    "Ещё не оплачено" — это пустая ячейка ИЛИ до сих пор формула (строка-кандидат на
    оплату по умолчанию зеркалит план через `=C<row>`, пока в неё не впишут литерал —
    см. MTS_CREDIT_TEST.md, раздел 2).
    """
    formulas = worksheet.get(
        f"{SCHEDULE_ACTUAL_COL}{SCHEDULE_FIRST_ROW}:{SCHEDULE_ACTUAL_COL}{schedule_last_row}",
        value_render_option="FORMULA",
    )
    for offset in range(schedule_last_row - SCHEDULE_FIRST_ROW + 1):
        cell = str(formulas[offset][0]) if offset < len(formulas) and formulas[offset] else ""
        if cell == "" or cell.startswith("="):
            return SCHEDULE_FIRST_ROW + offset
    return None


def _find_early_payment_row(
    worksheet: gspread.Worksheet, early_first_row: int, early_last_row: int
) -> int | None:
    """Первая пустая строка в логе досрочных взносов (по дате взноса, колонка A)."""
    values = worksheet.get(f"{EARLY_DATE_COL}{early_first_row}:{EARLY_DATE_COL}{early_last_row}")
    for offset in range(early_last_row - early_first_row + 1):
        cell = str(values[offset][0]).strip() if offset < len(values) and values[offset] else ""
        if cell == "":
            return early_first_row + offset
    return None


def record_regular_payment(config: Config, sheet_title: str, amount: float) -> bool:
    """Обычный ежемесячный платёж — пишет факт оплаты в первую незаполненную строку
    графика. "Что сделать с переплатой" тут не спрашивается и не трогается — эта
    развилка имеет смысл только для досрочных взносов (см. запись Таблицы 2), в
    Таблице 1 остаётся дефолтное "Сократить срок", уже проставленное в самом листе."""
    for attempt in (1, 2):
        try:
            worksheet = _get_worksheet(config, sheet_title)
            schedule_last_row, _, _ = _table_layout(worksheet)
            row = _find_regular_payment_row(worksheet, schedule_last_row)
            if row is None:
                raise CreditSheetCapacityError(
                    f"В графике листа «{sheet_title}» не осталось свободных строк "
                    f"({SCHEDULE_FIRST_ROW}:{schedule_last_row})."
                )
            worksheet.update(
                range_name=f"{SCHEDULE_ACTUAL_COL}{row}",
                values=[[amount]],
                value_input_option="USER_ENTERED",
            )
            return True
        except (CreditSheetCapacityError, CreditSheetLayoutError):
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
            _, early_first_row, early_last_row = _table_layout(worksheet)
            row = _find_early_payment_row(worksheet, early_first_row, early_last_row)
            if row is None:
                raise CreditSheetCapacityError(
                    f"В логе досрочных взносов листа «{sheet_title}» не осталось "
                    f"свободных строк ({early_first_row}:{early_last_row})."
                )
            worksheet.update(
                range_name=f"{EARLY_DATE_COL}{row}:{EARLY_COMMENT_COL}{row}",
                values=[[date_str, amount, action_label, comment or ""]],
                value_input_option="USER_ENTERED",
            )
            return True
        except (CreditSheetCapacityError, CreditSheetLayoutError):
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
            success = await asyncio.to_thread(record_regular_payment, config, sheet_title, amount)
    except CreditSheetCapacityError:
        return CreditSyncOutcome.CAPACITY_EXHAUSTED
    except CreditSheetLayoutError:
        logger.exception("Не удалось определить разметку кредитного листа «%s»", sheet_title)
        return CreditSyncOutcome.ERROR
    return CreditSyncOutcome.OK if success else CreditSyncOutcome.ERROR


# --- Снимок графика платежей (для напоминаний об оплате, см. willem/credit_reminders.py) ---

_SCHEDULE_DATE_FORMAT = "%d.%m.%Y"


def _parse_schedule_date(raw: str) -> str:
    """Дата в листе — "19.11.2026" (см. MTS_CREDIT_TEST.md). Возвращает ISO YYYY-MM-DD."""
    try:
        return datetime.strptime(raw.strip(), _SCHEDULE_DATE_FORMAT).date().isoformat()
    except ValueError as exc:
        raise CreditSheetLayoutError(f"Не удалось распознать дату платежа: {raw!r}") from exc


def _parse_planned_amount(raw: object) -> float:
    """План платежа может прийти как число или как отформатированная строка вида
    "10 837,00 ₽" (неразрывный пробел как разделитель тысяч, запятая как десятичная точка,
    символ валюты) — см. MTS_CREDIT_TEST.md, раздел 1."""
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip().replace("\xa0", " ")
    for junk in ("₽", "₸", "$", "€", " "):
        text = text.replace(junk, "")
    text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError as exc:
        raise CreditSheetLayoutError(f"Не удалось распознать план платежа: {raw!r}") from exc


def read_credit_schedule(config: Config, sheet_title: str) -> list[tuple[int, str, float]]:
    """(row_number, payment_date_iso, planned_amount) для всех заполненных строк графика —
    источник для снимка в БД (`credit_schedule`, см. willem/db/credit_reminders.py). Границы
    диапазона — те же, что и для обычного платежа (`_table_layout`), без дублирования."""
    worksheet = _get_worksheet(config, sheet_title)
    schedule_last_row, _, _ = _table_layout(worksheet)
    dates = worksheet.get(
        f"{SCHEDULE_DATE_COL}{SCHEDULE_FIRST_ROW}:{SCHEDULE_DATE_COL}{schedule_last_row}"
    )
    amounts = worksheet.get(
        f"{SCHEDULE_PLANNED_COL}{SCHEDULE_FIRST_ROW}:{SCHEDULE_PLANNED_COL}{schedule_last_row}"
    )
    rows: list[tuple[int, str, float]] = []
    for offset in range(schedule_last_row - SCHEDULE_FIRST_ROW + 1):
        date_cell = dates[offset][0] if offset < len(dates) and dates[offset] else ""
        amount_cell = amounts[offset][0] if offset < len(amounts) and amounts[offset] else ""
        if date_cell == "" or amount_cell == "":
            continue
        row_number = SCHEDULE_FIRST_ROW + offset
        rows.append((row_number, _parse_schedule_date(str(date_cell)), _parse_planned_amount(amount_cell)))
    return rows


async def resync_credit_schedules(config: Config) -> None:
    """Обновляет снимок графика (`credit_schedule`) для всех кредитов профиля — по одному
    кредиту за раз; ошибка на одном (например `CreditSheetLayoutError`) логируется и не
    прерывает обновление остальных. Вызывается ночной cron-задачей (см. willem/scheduler.py)
    и вручную через `python -m willem.cli resync_credits`."""
    for credit_key, sheet_title in config.credit_sheets.items():
        try:
            rows = await asyncio.to_thread(read_credit_schedule, config, sheet_title)
        except Exception:
            logger.exception("Не удалось обновить график платежей для кредита «%s»", credit_key)
            continue
        with connect(config.db_path) as conn:
            credit_reminders_db.replace_schedule(conn, credit_key, rows)
