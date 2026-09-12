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

SKIP_LABEL = "Пропустить"


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


def build_main_menu() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.button(text=TRANSFER_BUTTON)
    builder.button(text=BALANCES_BUTTON)
    builder.button(text=CATEGORIES_BUTTON)
    builder.button(text=SOURCES_BUTTON)
    builder.button(text=TODAY_BUTTON)
    builder.button(text=WEEK_BUTTON)
    builder.button(text=LAST_BUTTON)
    builder.adjust(2, 2, 2, 1)
    return builder.as_markup(resize_keyboard=True)
