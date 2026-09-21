from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, or_f
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from willem.bot.keyboards import CREDITS_BUTTON
from willem.config import Config
from willem.db import credit_reminders as credit_reminders_db
from willem.db.connection import connect
from willem.db.credit_reminders import CreditScheduleRow
from willem.formatting import currency_symbol, format_amount
from willem.texts import Texts
from willem.timeutil import format_date_ru, today_local_date

router = Router(name="credits")

VIEW_PREFIX = "credit_view"
TOGGLE_PREFIX = "credit_toggle"
BACK_CB = "credit_back"


def _credit_keys(config: Config) -> list[str]:
    return list(config.credit_sheets.keys())


def _list_line(credit_key: str, next_row: CreditScheduleRow | None, config: Config, texts: Texts) -> str:
    if next_row is None:
        return texts.get("credits.list_line_none", name=credit_key)
    return texts.get(
        "credits.list_line",
        name=credit_key,
        date=format_date_ru(next_row.payment_date),
        amount=format_amount(next_row.planned_amount),
        symbol=currency_symbol(config.credit_reminder_currency),
    )


def _list_keyboard(credit_keys: list[str]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for idx, credit_key in enumerate(credit_keys):
        builder.button(text=credit_key, callback_data=f"{VIEW_PREFIX}:{idx}")
    builder.adjust(1)
    return builder.as_markup()


def _detail_keyboard(idx: int, enabled: bool, texts: Texts) -> InlineKeyboardMarkup:
    toggle_key = "credits.notify_toggle_off" if enabled else "credits.notify_toggle_on"
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.get(toggle_key), callback_data=f"{TOGGLE_PREFIX}:{idx}")
    builder.button(text="‹ Назад", callback_data=BACK_CB)
    builder.adjust(1, 1)
    return builder.as_markup()


def _list_text(config: Config, texts: Texts) -> tuple[str, InlineKeyboardMarkup]:
    credit_keys = _credit_keys(config)
    today = today_local_date(config.timezone)
    with connect(config.db_path) as conn:
        lines = [
            _list_line(credit_key, credit_reminders_db.next_payment(conn, credit_key, today), config, texts)
            for credit_key in credit_keys
        ]
    text = texts.get("credits.list_title") + "\n\n" + "\n".join(lines)
    return text, _list_keyboard(credit_keys)


def _detail_text(credit_key: str, config: Config, texts: Texts) -> tuple[str, bool]:
    today = today_local_date(config.timezone)
    with connect(config.db_path) as conn:
        next_row = credit_reminders_db.next_payment(conn, credit_key, today)
        enabled = credit_reminders_db.is_reminder_enabled(conn, credit_key)
    status = texts.get("credits.notify_on" if enabled else "credits.notify_off")
    if next_row is None:
        text = texts.get("credits.detail_none", name=credit_key, status=status)
    else:
        text = texts.get(
            "credits.detail",
            name=credit_key,
            status=status,
            date=format_date_ru(next_row.payment_date),
            amount=format_amount(next_row.planned_amount),
            symbol=currency_symbol(config.credit_reminder_currency),
        )
    return text, enabled


@router.message(or_f(Command("credits"), F.text == CREDITS_BUTTON))
async def show_credits(message: Message, config: Config, texts: Texts) -> None:
    text, keyboard = _list_text(config, texts)
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data == BACK_CB)
async def back_to_list(callback: CallbackQuery, config: Config, texts: Texts) -> None:
    text, keyboard = _list_text(config, texts)
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith(f"{VIEW_PREFIX}:"))
async def view_credit(callback: CallbackQuery, config: Config, texts: Texts) -> None:
    idx = int(callback.data.removeprefix(f"{VIEW_PREFIX}:"))
    credit_keys = _credit_keys(config)
    if idx >= len(credit_keys):
        await callback.answer(texts.get("credits.not_found"))
        return
    text, enabled = _detail_text(credit_keys[idx], config, texts)
    await callback.message.edit_text(text, reply_markup=_detail_keyboard(idx, enabled, texts))
    await callback.answer()


@router.callback_query(F.data.startswith(f"{TOGGLE_PREFIX}:"))
async def toggle_credit(callback: CallbackQuery, config: Config, texts: Texts) -> None:
    idx = int(callback.data.removeprefix(f"{TOGGLE_PREFIX}:"))
    credit_keys = _credit_keys(config)
    if idx >= len(credit_keys):
        await callback.answer(texts.get("credits.not_found"))
        return
    credit_key = credit_keys[idx]
    with connect(config.db_path) as conn:
        enabled = credit_reminders_db.is_reminder_enabled(conn, credit_key)
        credit_reminders_db.set_reminder_enabled(conn, credit_key, not enabled)
    await callback.answer(
        texts.get("credits.notify_toggled_off" if enabled else "credits.notify_toggled_on")
    )
    text, still_enabled = _detail_text(credit_key, config, texts)
    await callback.message.edit_text(text, reply_markup=_detail_keyboard(idx, still_enabled, texts))
