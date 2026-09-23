from __future__ import annotations

import logging
from datetime import date

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from willem.config import Config
from willem.db import credit_reminders as credit_reminders_db
from willem.db.connection import connect
from willem.db.credit_reminders import CreditScheduleRow
from willem.formatting import currency_symbol, format_amount
from willem.texts import Texts
from willem.timeutil import format_date_ru, now_utc_iso, today_local_date

logger = logging.getLogger(__name__)

OFFSETS = (3, 2, 1, 0)

# Кнопка "Уже оплачено" под напоминанием — callback_data кодирует индекс кредита в
# config.credit_sheets (см. willem/bot/handlers/credits.py, тот же приём, что и у
# VIEW_PREFIX/TOGGLE_PREFIX там же) и дату платежа, чтобы гасить оставшиеся офсеты.
ACK_CALLBACK_PREFIX = "credit_ack"


def due_reminders(
    schedule: list[CreditScheduleRow],
    already_sent: set[tuple[str, int]],
    acknowledged: set[str],
    today: date,
) -> list[tuple[CreditScheduleRow, int]]:
    """Какие (строка графика, offset_days) должны получить напоминание прямо сейчас —
    payment_date - offset_days == today, платёж ещё не отмечен оплаченным (`acknowledged`,
    см. кнопку "Уже оплачено") и напоминание по этому офсету ещё не отправлялось
    (`already_sent`). Чистая функция без IO — сеть/БД/бот подставляются вызывающим кодом
    (`send_due_reminders`), поэтому легко тестируется напрямую."""
    due: list[tuple[CreditScheduleRow, int]] = []
    for row in schedule:
        offset = (row.payment_date - today).days
        if offset not in OFFSETS:
            continue
        if row.payment_date.isoformat() in acknowledged:
            continue
        if (row.payment_date.isoformat(), offset) in already_sent:
            continue
        due.append((row, offset))
    return due


def _reminder_text(row: CreditScheduleRow, offset: int, config: Config, texts: Texts) -> str:
    key = "credits.reminder_due_today" if offset == 0 else "credits.reminder"
    return texts.get(
        key,
        credit=row.credit_key,
        amount=format_amount(row.planned_amount),
        symbol=currency_symbol(config.credit_reminder_currency),
        date=format_date_ru(row.payment_date),
    )


def _ack_keyboard(credit_idx: int, payment_date: date, texts: Texts) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text=texts.get("credits.mark_paid_button"),
                callback_data=f"{ACK_CALLBACK_PREFIX}:{credit_idx}:{payment_date.isoformat()}",
            )
        ]]
    )


async def send_due_reminders(bot: Bot, config: Config, texts: Texts) -> None:
    """Для каждого кредита с включёнными уведомлениями — рассылает всем участникам профиля
    (`config.allowed_telegram_ids`) напоминания, чей срок наступил (см. `due_reminders`), с
    кнопкой "Уже оплачено" под каждым (обработка тапа — willem/bot/handlers/credits.py), и
    отмечает их отправленными, чтобы не продублировать при повторном запуске джобы."""
    today = today_local_date(config.timezone)
    credit_keys = list(config.credit_sheets.keys())
    for credit_idx, credit_key in enumerate(credit_keys):
        with connect(config.db_path) as conn:
            if not credit_reminders_db.is_reminder_enabled(conn, credit_key):
                continue
            schedule = credit_reminders_db.list_schedule(conn, credit_key)
            sent = credit_reminders_db.already_sent(conn, credit_key)
            acked = credit_reminders_db.acknowledged_payment_dates(conn, credit_key)

        for row, offset in due_reminders(schedule, sent, acked, today):
            text = _reminder_text(row, offset, config, texts)
            keyboard = _ack_keyboard(credit_idx, row.payment_date, texts)
            for telegram_id in config.allowed_telegram_ids:
                try:
                    await bot.send_message(telegram_id, text, reply_markup=keyboard)
                except Exception:
                    logger.exception(
                        "Не удалось отправить напоминание по кредиту «%s» пользователю %s",
                        credit_key, telegram_id,
                    )
            with connect(config.db_path) as conn:
                credit_reminders_db.mark_sent(
                    conn, credit_key, row.payment_date, offset, now_utc_iso()
                )
