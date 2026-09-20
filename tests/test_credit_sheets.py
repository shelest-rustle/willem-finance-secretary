from __future__ import annotations

import pytest

from willem import credit_sheets
from willem.credit_sheets import (
    EARLY_FIRST_ROW,
    EARLY_LAST_ROW,
    OVERPAYMENT_REDUCE,
    OVERPAYMENT_SHORTEN,
    SCHEDULE_ACTUAL_COL,
    SCHEDULE_FIRST_ROW,
    SCHEDULE_LAST_ROW,
    SCHEDULE_OVERPAY_COL,
    CreditSheetCapacityError,
    CreditSyncOutcome,
    _find_early_payment_row,
    _find_regular_payment_row,
    record_early_payment,
    record_regular_payment,
    sync_credit_payment,
)

SCHEDULE_RANGE = f"{SCHEDULE_ACTUAL_COL}{SCHEDULE_FIRST_ROW}:{SCHEDULE_ACTUAL_COL}{SCHEDULE_LAST_ROW}"
EARLY_RANGE = f"A{EARLY_FIRST_ROW}:A{EARLY_LAST_ROW}"


class FakeConfig:
    google_sheets_credentials_path = "fake.json"
    google_sheets_spreadsheet_id = "fake-id"


class FakeWorksheet:
    def __init__(self, get_responses: dict[str, list[list]] | None = None, fail_times: int = 0) -> None:
        self.get_responses = get_responses or {}
        self.fail_times = fail_times
        self.batch_updates: list = []
        self.updates: list = []

    def get(self, range_name: str, value_render_option: str | None = None):
        return self.get_responses.get(range_name, [])

    def batch_update(self, data, value_input_option: str | None = None) -> None:
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("API недоступен")
        self.batch_updates.append((data, value_input_option))

    def update(self, range_name: str, values, value_input_option: str | None = None) -> None:
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("API недоступен")
        self.updates.append((range_name, values, value_input_option))


# -- поиск целевой строки для обычного платежа ------------------------------------


def test_find_regular_payment_row_empty_range_returns_first_row() -> None:
    worksheet = FakeWorksheet(get_responses={SCHEDULE_RANGE: []})
    assert _find_regular_payment_row(worksheet) == SCHEDULE_FIRST_ROW


def test_find_regular_payment_row_skips_literal_and_stops_at_blank_or_formula() -> None:
    # Первая строка — уже оплачена литералом, вторая — ещё формула-заглушка "=C9".
    worksheet = FakeWorksheet(get_responses={SCHEDULE_RANGE: [["8558"], ["=C9"]]})
    assert _find_regular_payment_row(worksheet) == SCHEDULE_FIRST_ROW + 1


def test_find_regular_payment_row_treats_missing_trailing_rows_as_blank() -> None:
    worksheet = FakeWorksheet(get_responses={SCHEDULE_RANGE: [["8558"], ["8558"]]})
    assert _find_regular_payment_row(worksheet) == SCHEDULE_FIRST_ROW + 2


def test_find_regular_payment_row_returns_none_when_all_filled() -> None:
    count = SCHEDULE_LAST_ROW - SCHEDULE_FIRST_ROW + 1
    worksheet = FakeWorksheet(get_responses={SCHEDULE_RANGE: [["8558"]] * count})
    assert _find_regular_payment_row(worksheet) is None


# -- поиск первой свободной строки для досрочного взноса ---------------------------


def test_find_early_payment_row_empty_range_returns_first_row() -> None:
    worksheet = FakeWorksheet(get_responses={EARLY_RANGE: []})
    assert _find_early_payment_row(worksheet) == EARLY_FIRST_ROW


def test_find_early_payment_row_returns_first_gap() -> None:
    worksheet = FakeWorksheet(get_responses={EARLY_RANGE: [["20.09.2026"], [], ["20.10.2026"]]})
    assert _find_early_payment_row(worksheet) == EARLY_FIRST_ROW + 1


def test_find_early_payment_row_returns_none_when_full() -> None:
    count = EARLY_LAST_ROW - EARLY_FIRST_ROW + 1
    worksheet = FakeWorksheet(get_responses={EARLY_RANGE: [["20.09.2026"]] * count})
    assert _find_early_payment_row(worksheet) is None


# -- запись обычного платежа --------------------------------------------------------


def test_record_regular_payment_writes_amount_and_default_action(monkeypatch: pytest.MonkeyPatch) -> None:
    worksheet = FakeWorksheet(get_responses={SCHEDULE_RANGE: []})
    monkeypatch.setattr(credit_sheets, "_get_worksheet", lambda config, title: worksheet)

    result = record_regular_payment(FakeConfig(), "МТС Кредит Лики", 8558.0, reduce_payment=False)

    assert result is True
    assert len(worksheet.batch_updates) == 1
    data, value_input_option = worksheet.batch_updates[0]
    assert value_input_option == "USER_ENTERED"
    assert {"range": f"{SCHEDULE_ACTUAL_COL}{SCHEDULE_FIRST_ROW}", "values": [[8558.0]]} in data
    assert {
        "range": f"{SCHEDULE_OVERPAY_COL}{SCHEDULE_FIRST_ROW}",
        "values": [[OVERPAYMENT_SHORTEN]],
    } in data


def test_record_regular_payment_reduce_payment_writes_reduce_label(monkeypatch: pytest.MonkeyPatch) -> None:
    worksheet = FakeWorksheet(get_responses={SCHEDULE_RANGE: []})
    monkeypatch.setattr(credit_sheets, "_get_worksheet", lambda config, title: worksheet)

    record_regular_payment(FakeConfig(), "МТС Кредит Лики", 15000.0, reduce_payment=True)

    data, _ = worksheet.batch_updates[0]
    assert {
        "range": f"{SCHEDULE_OVERPAY_COL}{SCHEDULE_FIRST_ROW}",
        "values": [[OVERPAYMENT_REDUCE]],
    } in data


def test_record_regular_payment_retries_once_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    worksheet = FakeWorksheet(get_responses={SCHEDULE_RANGE: []}, fail_times=1)
    monkeypatch.setattr(credit_sheets, "_get_worksheet", lambda config, title: worksheet)

    result = record_regular_payment(FakeConfig(), "МТС Кредит Лики", 8558.0, reduce_payment=False)

    assert result is True
    assert len(worksheet.batch_updates) == 1


def test_record_regular_payment_fails_after_two_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    worksheet = FakeWorksheet(get_responses={SCHEDULE_RANGE: []}, fail_times=2)
    monkeypatch.setattr(credit_sheets, "_get_worksheet", lambda config, title: worksheet)

    result = record_regular_payment(FakeConfig(), "МТС Кредит Лики", 8558.0, reduce_payment=False)

    assert result is False
    assert worksheet.batch_updates == []


def test_record_regular_payment_raises_capacity_error_when_full(monkeypatch: pytest.MonkeyPatch) -> None:
    count = SCHEDULE_LAST_ROW - SCHEDULE_FIRST_ROW + 1
    worksheet = FakeWorksheet(get_responses={SCHEDULE_RANGE: [["8558"]] * count})
    monkeypatch.setattr(credit_sheets, "_get_worksheet", lambda config, title: worksheet)

    with pytest.raises(CreditSheetCapacityError):
        record_regular_payment(FakeConfig(), "МТС Кредит Лики", 8558.0, reduce_payment=False)


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


def test_record_early_payment_blank_comment_becomes_empty_string(monkeypatch: pytest.MonkeyPatch) -> None:
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
