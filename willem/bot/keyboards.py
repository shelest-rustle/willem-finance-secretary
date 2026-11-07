from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

TRANSFER_BUTTON = "Перевод"
CATEGORIES_BUTTON = "Категории"
SOURCES_BUTTON = "Источники"
BALANCES_BUTTON = "Баланс"
TODAY_BUTTON = "Сегодня"
WEEK_BUTTON = "Неделя"
LAST_BUTTON = "Последние"
EDIT_BUTTON = "Изменить"
CREDITS_BUTTON = "Кредиты"

SKIP_LABEL = "Пропустить"
BACK_LABEL = "‹ Назад"
CANCEL_LABEL = "Отмена"


def add_back_button(
    markup: InlineKeyboardMarkup | None, callback_data: str, label: str = BACK_LABEL
) -> InlineKeyboardMarkup:
    """Добавляет отдельной нижней строкой кнопку «‹ Назад» (шаг назад во флоу) — или
    «Отмена» (`label=CANCEL_LABEL`) на первом шаге, когда возвращаться уже некуда."""
    rows = list(markup.inline_keyboard) if markup else []
    rows.append([InlineKeyboardButton(text=label, callback_data=callback_data)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_choice_keyboard(
    items: list[tuple[str, str]],
    callback_prefix: str,
    columns: int = 2,
    extra_buttons: list[tuple[str, str]] | None = None,
) -> InlineKeyboardMarkup:
    """`extra_buttons` — доп. кнопки (подпись, callback_data), каждая отдельной строкой под
    основной сеткой выбора — используется для кнопки «Пропустить» или переключателя «Кто»."""
    builder = InlineKeyboardBuilder()
    for item_id, label in items:
        builder.button(text=label, callback_data=f"{callback_prefix}:{item_id}")
    builder.adjust(columns)
    markup = builder.as_markup()
    for label, callback_data in extra_buttons or []:
        markup.inline_keyboard.append(
            [InlineKeyboardButton(text=label, callback_data=callback_data)]
        )
    return markup


def build_manage_list_keyboard(
    items: list[tuple[str, str]],
    view_prefix: str,
    add_callback: str,
    add_label: str = "➕ Добавить",
    columns: int = 2,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for item_id, label in items:
        builder.button(text=label, callback_data=f"{view_prefix}:{item_id}")
    builder.button(text=add_label, callback_data=add_callback)
    builder.adjust(columns)
    return builder.as_markup()


def build_single_button_keyboard(label: str, callback_data: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=label, callback_data=callback_data)
    return builder.as_markup()


def build_main_menu(show_credits: bool = False) -> ReplyKeyboardMarkup:
    """`show_credits` включает кнопку «Кредиты» — только для профилей с настроенными
    `credit_sheets` (сейчас Pantalone), см. willem/bot/handlers/credits.py."""
    builder = ReplyKeyboardBuilder()
    builder.button(text=TRANSFER_BUTTON)
    builder.button(text=BALANCES_BUTTON)
    builder.button(text=CATEGORIES_BUTTON)
    builder.button(text=SOURCES_BUTTON)
    builder.button(text=TODAY_BUTTON)
    builder.button(text=WEEK_BUTTON)
    builder.button(text=LAST_BUTTON)
    builder.button(text=EDIT_BUTTON)
    rows = [2, 2, 2, 2]
    if show_credits:
        builder.button(text=CREDITS_BUTTON)
        rows.append(1)
    builder.adjust(*rows)
    return builder.as_markup(resize_keyboard=True)
