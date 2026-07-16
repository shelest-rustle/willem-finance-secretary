from __future__ import annotations

_CURRENCY_SYMBOLS = {
    "KZT": "₸",
    "RUB": "₽",
    "USD": "$",
    "USDT": "USDT",
}

_PERIOD_WORDS = {
    "month": "месяц",
    "week": "неделю",
}

_SOURCE_TYPE_LABELS = {
    "card": "Карта",
    "cash": "Наличные",
    "crypto": "Крипто",
}


def currency_symbol(currency: str) -> str:
    return _CURRENCY_SYMBOLS.get(currency, currency)


def source_type_label(source_type: str) -> str:
    return _SOURCE_TYPE_LABELS.get(source_type, source_type)


def format_amount(amount: float) -> str:
    if amount == int(amount):
        text = f"{int(amount):,}"
    else:
        text = f"{amount:,.2f}"
    return text.replace(",", " ")


def period_word(period: str) -> str:
    return _PERIOD_WORDS.get(period, period)
