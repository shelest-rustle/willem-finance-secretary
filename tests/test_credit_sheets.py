from __future__ import annotations

import pytest

from willem import credit_sheets
from willem.credit_sheets import (
    EARLY_BLOCK_TITLE,
    EARLY_TOTAL_LABEL,
    OVERPAYMENT_REDUCE,
    OVERPAYMENT_SHORTEN,
    SCHEDULE_ACTUAL_COL,
    SCHEDULE_FIRST_ROW,
    CreditSheetCapacityError,
    CreditSheetLayoutError,
    CreditSyncOutcome,
    _find_early_payment_row,
    _find_regular_payment_row,
    _table_layout,
    record_early_payment,
    record_regular_payment,
    sync_credit_payment,
)

# Границы для тестового листа: график 8..31 (24 строки, как у "МТС Кредит Лики"),
# лог досрочных взносов 35..64 (30 строк).
SCHEDULE_LAST_ROW = 31
EARLY_FIRST_ROW = 35
EARLY_LAST_ROW = 64

SCHEDULE_RANGE = f"{SCHEDULE_ACTUAL_COL}{SCHEDULE_FIRST_ROW}:{SCHEDULE_ACTUAL_COL}{SCHEDULE_LAST_ROW}"
EARLY_RANGE = f"A{EARLY_FIRST_ROW}:A{EARLY_LAST_ROW}"
LAYOUT_RANGE = f"A{SCHEDULE_FIRST_ROW}:A{credit_sheets._MAX_SCAN_ROW}"


def _layout_column(schedule_last_row: int, early_first_row: int, early_last_row: int) -> list[list]:
    """Строит содержимое колонки A от SCHEDULE_FIRST_ROW и до строки "Итого" включительно —
    ровно то, что вычитывает `_table_layout`."""
    rows: list[list] = []
    for row_num in range(SCHEDULE_FIRST_ROW, early_last_row + 2):
        if row_num == schedule_last_row + 2:
            rows.append([EARLY_BLOCK_TITLE])
        elif row_num == early_last_row + 1:
            rows.append([EARLY_TOTAL_LABEL])
        else:
            rows.append([])
    return rows


DEFAULT_LAYOUT_RESPONSE = _layout_column(SCHEDULE_LAST_ROW, EARLY_FIRST_ROW, EARLY_LAST_ROW)


class FakeConfig:
    google_sheets_credentials_path = "fake.json"
    google_sheets_spreadsheet_id = "fake-id"


class FakeWorksheet:
    title = "МТС Кредит Лики"

    def __init__(
        self,
        get_responses: dict[str, list[list]] | None = None,
        fail_times: int = 0,
        layout_response: list[list] | None = None,
    ) -> None:
        responses = dict(get_responses or {})
        responses.setdefault(LAYOUT_RANGE, layout_response or DEFAULT_LAYOUT_RESPONSE)
        self.get_responses = responses
        self.fail_times = fail_times
        self.updates: list = []

    def get(self, range_name: str, value_render_option: str | None = None):
        return self.get_responses.get(range_name, [])

    def update(self, range_name: str, values, value_input_option: str | None = None) -> None:
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("API недоступен")
        self.updates.append((range_name, values, value_input_option))


# -- определение границ таблиц по самому листу --------------------------------------


def test_table_layout_reads_bounds_from_landmarks() -> None:
    worksheet = FakeWorksheet(layout_response=_layout_column(31, 35, 64))
    assert _table_layout(worksheet) == (31, 35, 64)


def test_table_layout_handles_different_schedule_length() -> None:
    # "Tinkoff REF Кредит Лики" в реальности: график 8..43, лог 47..76.
    worksheet = FakeWorksheet(layout_response=_layout_column(43, 47, 76))
    assert _table_layout(worksheet) == (43, 47, 76)


def test_table_layout_raises_when_landmarks_missing() -> None:
    worksheet = FakeWorksheet(layout_response=[[] for _ in range(50)])
    with pytest.raises(CreditSheetLayoutError):
        _table_layout(worksheet)


# -- поиск целевой строки для обычного платежа ------------------------------------


def test_find_regular_payment_row_empty_range_returns_first_row() -> None:
    worksheet = FakeWorksheet(get_responses={SCHEDULE_RANGE: []})
    assert _find_regular_payment_row(worksheet, SCHEDULE_LAST_ROW) == SCHEDULE_FIRST_ROW


def test_find_regular_payment_row_skips_literal_and_stops_at_blank_or_formula() -> None:
    # Первая строка — уже оплачена литералом, вторая — ещё формула-заглушка "=C9".
    worksheet = FakeWorksheet(get_responses={SCHEDULE_RANGE: [["8558"], ["=C9"]]})
    assert _find_regular_payment_row(worksheet, SCHEDULE_LAST_ROW) == SCHEDULE_FIRST_ROW + 1


def test_find_regular_payment_row_treats_missing_trailing_rows_as_blank() -> None:
    worksheet = FakeWorksheet(get_responses={SCHEDULE_RANGE: [["8558"], ["8558"]]})
    assert _find_regular_payment_row(worksheet, SCHEDULE_LAST_ROW) == SCHEDULE_FIRST_ROW + 2


def test_find_regular_payment_row_returns_none_when_all_filled() -> None:
    count = SCHEDULE_LAST_ROW - SCHEDULE_FIRST_ROW + 1
    worksheet = FakeWorksheet(get_responses={SCHEDULE_RANGE: [["8558"]] * count})
    assert _find_regular_payment_row(worksheet, SCHEDULE_LAST_ROW) is None


# -- поиск первой свободной строки для досрочного взноса ---------------------------


def test_find_early_payment_row_empty_range_returns_first_row() -> None:
    worksheet = FakeWorksheet(get_responses={EARLY_RANGE: []})
    assert _find_early_payment_row(worksheet, EARLY_FIRST_ROW, EARLY_LAST_ROW) == EARLY_FIRST_ROW


def test_find_early_payment_row_returns_first_gap() -> None:
    worksheet = FakeWorksheet(get_responses={EARLY_RANGE: [["20.09.2026"], [], ["20.10.2026"]]})
    assert _find_early_payment_row(worksheet, EARLY_FIRST_ROW, EARLY_LAST_ROW) == EARLY_FIRST_ROW + 1


def test_find_early_payment_row_returns_none_when_full() -> None:
    count = EARLY_LAST_ROW - EARLY_FIRST_ROW + 1
    worksheet = FakeWorksheet(get_responses={EARLY_RANGE: [["20.09.2026"]] * count})
    assert _find_early_payment_row(worksheet, EARLY_FIRST_ROW, EARLY_LAST_ROW) is None


# -- запись обычного платежа --------------------------------------------------------


def test_record_regular_payment_writes_amount_only(monkeypatch: pytest.MonkeyPatch) -> None:
    worksheet = FakeWorksheet(get_responses={SCHEDULE_RANGE: []})
    monkeypatch.setattr(credit_sheets, "_get_worksheet", lambda config, title: worksheet)

    result = record_regular_payment(FakeConfig(), "МТС Кредит Лики", 8558.0)

    assert result is True
    assert len(worksheet.updates) == 1
    range_name, values, value_input_option = worksheet.updates[0]
    assert range_name == f"{SCHEDULE_ACTUAL_COL}{SCHEDULE_FIRST_ROW}"
    assert values == [[8558.0]]
    assert value_input_option == "USER_ENTERED"


def test_record_regular_payment_uses_correct_row_for_longer_schedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # "Tinkoff REF Кредит Лики": график длиннее (8..43), первая строка уже оплачена.
    worksheet = FakeWorksheet(
        layout_response=_layout_column(43, 47, 76),
        get_responses={f"{SCHEDULE_ACTUAL_COL}{SCHEDULE_FIRST_ROW}:{SCHEDULE_ACTUAL_COL}43": [["10920"]]},
    )
    monkeypatch.setattr(credit_sheets, "_get_worksheet", lambda config, title: worksheet)

    record_regular_payment(FakeConfig(), "Tinkoff REF Кредит Лики", 10920.0)

    range_name, _, _ = worksheet.updates[0]
    assert range_name == f"{SCHEDULE_ACTUAL_COL}{SCHEDULE_FIRST_ROW + 1}"


def test_record_regular_payment_retries_once_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    worksheet = FakeWorksheet(get_responses={SCHEDULE_RANGE: []}, fail_times=1)
    monkeypatch.setattr(credit_sheets, "_get_worksheet", lambda config, title: worksheet)

    result = record_regular_payment(FakeConfig(), "МТС Кредит Лики", 8558.0)

    assert result is True
    assert len(worksheet.updates) == 1


def test_record_regular_payment_fails_after_two_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    worksheet = FakeWorksheet(get_responses={SCHEDULE_RANGE: []}, fail_times=2)
    monkeypatch.setattr(credit_sheets, "_get_worksheet", lambda config, title: worksheet)

    result = record_regular_payment(FakeConfig(), "МТС Кредит Лики", 8558.0)

    assert result is False
    assert worksheet.updates == []


def test_record_regular_payment_raises_capacity_error_when_full(monkeypatch: pytest.MonkeyPatch) -> None:
    count = SCHEDULE_LAST_ROW - SCHEDULE_FIRST_ROW + 1
    worksheet = FakeWorksheet(get_responses={SCHEDULE_RANGE: [["8558"]] * count})
    monkeypatch.setattr(credit_sheets, "_get_worksheet", lambda config, title: worksheet)

    with pytest.raises(CreditSheetCapacityError):
        record_regular_payment(FakeConfig(), "МТС Кредит Лики", 8558.0)


def test_record_regular_payment_raises_layout_error_when_landmarks_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worksheet = FakeWorksheet(layout_response=[[] for _ in range(50)])
    monkeypatch.setattr(credit_sheets, "_get_worksheet", lambda config, title: worksheet)

    with pytest.raises(CreditSheetLayoutError):
        record_regular_payment(FakeConfig(), "МТС Кредит Лики", 8558.0)


# -- запись досрочного взноса --------------------------------------------------------


def test_record_early_payment_writes_full_row(monkeypatch: pytest.MonkeyPatch) -> None:
    worksheet = FakeWorksheet(get_responses={EARLY_RANGE: []})
    monkeypatch.setattr(credit_sheets, "_get_worksheet", lambda config, title: worksheet)

    result = record_early_payment(
        FakeConfig(),
        "МТС Кредит Лики",
        date_str="20.09.2026",
        amount=20000.0,
        reduce_payment=True,
        comment="Премия",
    )

    assert result is True
    range_name, values, value_input_option = worksheet.updates[0]
    assert range_name == f"A{EARLY_FIRST_ROW}:D{EARLY_FIRST_ROW}"
    assert values == [["20.09.2026", 20000.0, OVERPAYMENT_REDUCE, "Премия"]]
    assert value_input_option == "USER_ENTERED"


def test_record_early_payment_shorten_label(monkeypatch: pytest.MonkeyPatch) -> None:
    worksheet = FakeWorksheet(get_responses={EARLY_RANGE: []})
    monkeypatch.setattr(credit_sheets, "_get_worksheet", lambda config, title: worksheet)

    record_early_payment(
        FakeConfig(),
        "МТС Кредит Лики",
        date_str="20.09.2026",
        amount=20000.0,
        reduce_payment=False,
        comment=None,
    )

    _, values, _ = worksheet.updates[0]
    assert values[0][2] == OVERPAYMENT_SHORTEN
    assert values[0][3] == ""


def test_record_early_payment_raises_capacity_error_when_full(monkeypatch: pytest.MonkeyPatch) -> None:
    count = EARLY_LAST_ROW - EARLY_FIRST_ROW + 1
    worksheet = FakeWorksheet(get_responses={EARLY_RANGE: [["20.09.2026"]] * count})
    monkeypatch.setattr(credit_sheets, "_get_worksheet", lambda config, title: worksheet)

    with pytest.raises(CreditSheetCapacityError):
        record_early_payment(
            FakeConfig(),
            "МТС Кредит Лики",
            date_str="20.09.2026",
            amount=1.0,
            reduce_payment=False,
            comment=None,
        )


# -- асинхронная обёртка --------------------------------------------------------------


async def test_sync_credit_payment_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(credit_sheets, "record_regular_payment", lambda *a, **k: True)

    outcome = await sync_credit_payment(
        FakeConfig(),
        sheet_title="МТС Кредит Лики",
        is_early=False,
        amount=8558.0,
        reduce_payment=False,
        comment=None,
        date_str="20.09.2026",
    )

    assert outcome is CreditSyncOutcome.OK


async def test_sync_credit_payment_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(credit_sheets, "record_regular_payment", lambda *a, **k: False)

    outcome = await sync_credit_payment(
        FakeConfig(),
        sheet_title="МТС Кредит Лики",
        is_early=False,
        amount=8558.0,
        reduce_payment=False,
        comment=None,
        date_str="20.09.2026",
    )

    assert outcome is CreditSyncOutcome.ERROR


async def test_sync_credit_payment_capacity_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*args, **kwargs):
        raise CreditSheetCapacityError("full")

    monkeypatch.setattr(credit_sheets, "record_early_payment", _raise)

    outcome = await sync_credit_payment(
        FakeConfig(),
        sheet_title="МТС Кредит Лики",
        is_early=True,
        amount=20000.0,
        reduce_payment=False,
        comment=None,
        date_str="20.09.2026",
    )

    assert outcome is CreditSyncOutcome.CAPACITY_EXHAUSTED


async def test_sync_credit_payment_layout_error_maps_to_generic_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(*args, **kwargs):
        raise CreditSheetLayoutError("landmarks not found")

    monkeypatch.setattr(credit_sheets, "record_regular_payment", _raise)

    outcome = await sync_credit_payment(
        FakeConfig(),
        sheet_title="МТС Кредит Лики",
        is_early=False,
        amount=1.0,
        reduce_payment=False,
        comment=None,
        date_str="20.09.2026",
    )

    assert outcome is CreditSyncOutcome.ERROR


async def test_sync_credit_payment_routes_early_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        credit_sheets, "record_regular_payment", lambda *a, **k: calls.append("regular") or True
    )
    monkeypatch.setattr(
        credit_sheets, "record_early_payment", lambda *a, **k: calls.append("early") or True
    )

    await sync_credit_payment(
        FakeConfig(),
        sheet_title="МТС Кредит Лики",
        is_early=True,
        amount=1.0,
        reduce_payment=False,
        comment=None,
        date_str="20.09.2026",
    )

    assert calls == ["early"]


# -- снимок графика платежей (напоминания об оплате) --------------------------------


def test_parse_schedule_date_parses_ru_format() -> None:
    assert credit_sheets._parse_schedule_date("19.11.2026") == "2026-11-19"


def test_parse_schedule_date_raises_on_unknown_format() -> None:
    with pytest.raises(CreditSheetLayoutError):
        credit_sheets._parse_schedule_date("2026-11-19")


def test_parse_planned_amount_handles_plain_number() -> None:
    assert credit_sheets._parse_planned_amount(8558.0) == 8558.0


def test_parse_planned_amount_handles_formatted_string_with_currency_symbol() -> None:
    assert credit_sheets._parse_planned_amount("10 837,00 ₽") == 10837.0


def test_parse_planned_amount_handles_non_breaking_space() -> None:
    assert credit_sheets._parse_planned_amount("10\xa0920,00 ₽") == 10920.0


def test_read_credit_schedule_pairs_dates_and_amounts(monkeypatch: pytest.MonkeyPatch) -> None:
    worksheet = FakeWorksheet(
        get_responses={
            f"B{SCHEDULE_FIRST_ROW}:B{SCHEDULE_LAST_ROW}": [["19.09.2026"], ["19.10.2026"]],
            f"C{SCHEDULE_FIRST_ROW}:C{SCHEDULE_LAST_ROW}": [["8 558,00 ₽"], ["8 558,00 ₽"]],
        }
    )
    monkeypatch.setattr(credit_sheets, "_get_worksheet", lambda config, title: worksheet)

    rows = credit_sheets.read_credit_schedule(FakeConfig(), "МТС Кредит Лики")

    assert rows == [
        (SCHEDULE_FIRST_ROW, "2026-09-19", 8558.0),
        (SCHEDULE_FIRST_ROW + 1, "2026-10-19", 8558.0),
    ]


def test_read_credit_schedule_skips_incomplete_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Строки за пределами уже заполненного графика (пустые дата/сумма) не попадают в снимок."""
    worksheet = FakeWorksheet(
        get_responses={
            f"B{SCHEDULE_FIRST_ROW}:B{SCHEDULE_LAST_ROW}": [["19.09.2026"], []],
            f"C{SCHEDULE_FIRST_ROW}:C{SCHEDULE_LAST_ROW}": [["8 558,00 ₽"], []],
        }
    )
    monkeypatch.setattr(credit_sheets, "_get_worksheet", lambda config, title: worksheet)

    rows = credit_sheets.read_credit_schedule(FakeConfig(), "МТС Кредит Лики")

    assert rows == [(SCHEDULE_FIRST_ROW, "2026-09-19", 8558.0)]


async def test_resync_credit_schedules_writes_snapshot_per_credit(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from willem.db import credit_reminders as credit_reminders_db
    from willem.db.connection import connect, init_db

    class MultiCreditConfig(FakeConfig):
        db_path = str(tmp_path / "test.db")
        credit_sheets = {"МТС Кредит Лики": "МТС Кредит Лики", "Сломанный лист": "Сломанный лист"}

    init_db(MultiCreditConfig.db_path)

    good_worksheet = FakeWorksheet(
        get_responses={
            f"B{SCHEDULE_FIRST_ROW}:B{SCHEDULE_LAST_ROW}": [["19.09.2026"]],
            f"C{SCHEDULE_FIRST_ROW}:C{SCHEDULE_LAST_ROW}": [["8 558,00 ₽"]],
        }
    )

    def fake_get_worksheet(config, title):
        if title == "Сломанный лист":
            raise CreditSheetLayoutError("не найдена разметка")
        return good_worksheet

    monkeypatch.setattr(credit_sheets, "_get_worksheet", fake_get_worksheet)

    # Ошибка на одном кредите не должна прерывать обновление остальных.
    await credit_sheets.resync_credit_schedules(MultiCreditConfig())

    with connect(MultiCreditConfig.db_path) as conn:
        rows = credit_reminders_db.list_schedule(conn, "МТС Кредит Лики")
        broken_rows = credit_reminders_db.list_schedule(conn, "Сломанный лист")

    assert len(rows) == 1
    assert broken_rows == []
