from __future__ import annotations

import re

EXPENSE_AMOUNT_RE = re.compile(r"^\d+([.,]\d{1,2})?$")
INCOME_AMOUNT_RE = re.compile(r"^\+\d+([.,]\d{1,2})?$")


def parse_amount(text: str) -> float:
    return float(text.strip().lstrip("+").replace(",", "."))
