from __future__ import annotations

import pytest

from willem.bot.amount import EXPENSE_AMOUNT_RE, INCOME_AMOUNT_RE, parse_amount


@pytest.mark.parametrize("text", ["3400", "3400.50", "3400,50", "0.5"])
def test_expense_amount_matches(text: str) -> None:
    assert EXPENSE_AMOUNT_RE.match(text)


@pytest.mark.parametrize("text", ["+3400", "abc", "3400 ", "-100", ""])
def test_expense_amount_rejects(text: str) -> None:
    assert not EXPENSE_AMOUNT_RE.match(text)


def test_income_amount_matches() -> None:
    assert INCOME_AMOUNT_RE.match("+100000")


def test_income_amount_rejects_plain_number() -> None:
    assert not INCOME_AMOUNT_RE.match("100000")


def test_parse_amount() -> None:
    assert parse_amount("3400") == 3400.0
    assert parse_amount("3400,50") == 3400.5
    assert parse_amount("+100000") == 100000.0
