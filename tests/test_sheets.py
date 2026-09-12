from __future__ import annotations

import pytest

from willem import sheets
from willem.db.transactions import Transaction


def make_tx(**overrides) -> Transaction:
    base = dict(
        id="tx-1",
        user_id=1,
        type="expense",
        amount=3400.0,
        currency="KZT",
        target_amount=None,
        target_currency=None,
        source_id="s1",
        target_source_id=None,
        category_id="c1",
        subcategory_id=None,
        who=None,
        comment="Продукты на неделю",
        created_at_utc="2026-07-16T08:23:00+00:00",
        synced=False,
        deleted=False,
    )
    base.update(overrides)
    return Transaction(**base)


def test_row_for_transaction_expense() -> None:
    tx = make_tx()
    row = sheets.row_for_transaction(
        tx,
        source_name="Kaspi",
        category_name="Продукты",
        target_name=None,
        tz_name="Asia/Almaty",
    )
    assert row == [
        "16.07.2026 13:23",
        "Расход",
        3400.0,
        "KZT",
        "Kaspi",
        "Продукты",
        "Продукты на неделю",
        "",
        "",
        "",
        "tx-1",
    ]


def test_row_for_transaction_transfer_cross_currency() -> None:
    tx = make_tx(
        type="transfer",
        category_id=None,
        comment=None,
        target_amount=1232.0,
        target_currency="RUB",
    )
    row = sheets.row_for_transaction(
        tx,
        source_name="bcc",
        category_name=None,
        target_name="Озон Банк",
        tz_name="Asia/Almaty",
    )
    assert row == [
        "16.07.2026 13:23",
        "Перевод",
        3400.0,
        "KZT",
        "bcc",
        "",
        "",
        1232.0,
        "RUB",
        "Озон Банк",
        "tx-1",
    ]


def test_row_for_transaction_household_expense_with_subcategory() -> None:
    tx = make_tx(who="Лика")
    row = sheets.row_for_transaction(
        tx,
        source_name="Kaspi Лики KZT",
        category_name="Домашняя еда",
        subcategory_name="Продукты",
        target_name=None,
        tz_name="Asia/Almaty",
        profile_name="pantalone",
    )
    assert row == [
        "16.07.2026 13:23",
        "Расход",
        "Лика",
        "Домашняя еда",
        "Продукты",
        "Kaspi Лики KZT",
        "",
        3400.0,
        "KZT",
        1.0,
        3400.0,
        "Продукты на неделю",
        "tx-1",
    ]


def test_row_for_transaction_household_debt_payment_label() -> None:
    tx = make_tx(
        type="transfer", category_id=None, comment=None, target_amount=3400.0, target_currency="KZT"
    )
    row = sheets.row_for_transaction(
        tx,
        source_name="Kaspi Лики KZT",
        target_name="Т-Кредит Лики",
        target_kind="debt",
        tz_name="Asia/Almaty",
        profile_name="pantalone",
    )
    assert row[1] == "Погашение долга / кредита"
    assert row[5] == "Kaspi Лики KZT"
    assert row[6] == "Т-Кредит Лики"


def test_row_for_transaction_household_kzt_equivalent_blank_between_two_foreign_currencies() -> None:
    tx = make_tx(
        type="transfer",
        currency="RUB",
        category_id=None,
        comment=None,
        target_amount=50.0,
        target_currency="USD",
    )
    row = sheets.row_for_transaction(
        tx,
        source_name="Карта Лики RUB",
        target_name="Кошелёк USD",
        tz_name="Asia/Almaty",
        profile_name="pantalone",
    )
    assert row[9] == ""  # курс к KZT
    assert row[10] == ""  # сумма в KZT


class FakeConfig:
    timezone = "Asia/Almaty"
    profile_name = "willem"
    sheet_name = "Транзакции"
    google_sheets_spreadsheet_id = "fake-id"


class FakeWorksheet:
    def __init__(self, existing_ids: list[str], fail_times: int = 0) -> None:
        self.existing_ids = existing_ids
        self.fail_times = fail_times
        self.appended: list = []

    def col_values(self, index: int) -> list[str]:
        return ["id", *self.existing_ids]

    def append_row(self, row, value_input_option: str) -> None:
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("API недоступен")
        self.appended.append(row)


def test_get_worksheet_uses_profile_sheet_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """Регрессия: имя листа должно браться из config.sheet_name, а не из захардкоженной
    константы — у pantalone лист называется иначе, чем "Транзакции" у willem."""
    requested_names = []

    class FakeSpreadsheet:
        def worksheet(self, name: str):
            requested_names.append(name)
            return "ok"

    class FakeClient:
        def open_by_key(self, key: str) -> FakeSpreadsheet:
            return FakeSpreadsheet()

    monkeypatch.setattr(sheets, "_get_client", lambda config: FakeClient())

    class ProfileConfig(FakeConfig):
        sheet_name = "Учёт"

    sheets._get_worksheet(ProfileConfig())

    assert requested_names == ["Учёт"]


def test_append_transaction_skips_duplicate(monkeypatch: pytest.MonkeyPatch) -> None:
    tx = make_tx()
    worksheet = FakeWorksheet(existing_ids=["tx-1"])
    monkeypatch.setattr(sheets, "_get_worksheet", lambda config: worksheet)

    result = sheets.append_transaction(FakeConfig(), tx, source_name="Kaspi", category_name="Продукты")

    assert result is True
    assert worksheet.appended == []


def test_append_transaction_appends_new_row(monkeypatch: pytest.MonkeyPatch) -> None:
    tx = make_tx()
    worksheet = FakeWorksheet(existing_ids=[])
    monkeypatch.setattr(sheets, "_get_worksheet", lambda config: worksheet)

    result = sheets.append_transaction(FakeConfig(), tx, source_name="Kaspi", category_name="Продукты")

    assert result is True
    assert len(worksheet.appended) == 1


def test_append_transaction_retries_once_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    tx = make_tx()
    worksheet = FakeWorksheet(existing_ids=[], fail_times=1)
    monkeypatch.setattr(sheets, "_get_worksheet", lambda config: worksheet)

    result = sheets.append_transaction(FakeConfig(), tx, source_name="Kaspi", category_name="Продукты")

    assert result is True
    assert len(worksheet.appended) == 1


def test_append_transaction_fails_after_two_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    tx = make_tx()
    worksheet = FakeWorksheet(existing_ids=[], fail_times=2)
    monkeypatch.setattr(sheets, "_get_worksheet", lambda config: worksheet)

    result = sheets.append_transaction(FakeConfig(), tx, source_name="Kaspi", category_name="Продукты")

    assert result is False
    assert worksheet.appended == []
