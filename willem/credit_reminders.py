from __future__ import annotations

import logging
from datetime import date

from aiogram import Bot

from willem.config import Config
from willem.db import credit_reminders as credit_reminders_db
from willem.db.connection import connect
from willem.db.credit_reminders import CreditScheduleRow
from willem.formatting import currency_symbol, format_amount
from willem.texts import Texts
from willem.timeutil import format_date_ru, now_utc_iso, today_local_date

logger = logging.getLogger(__name__)

OFFSETS = (3, 2, 1, 0)


def due_reminders(
    schedule: list[CreditScheduleRow],
    already_sent: set[tuple[str, int]],
    today: date,
) -> list[tuple[CreditScheduleRow, int]]:
    """Какие (строка графика, offset_days) должны получить напоминание прямо сейчас —
    payment_date - offset_days == today и ещё не отправлялось. Чистая функция без IO —
    сеть/БД/бот подставляются вызывающим кодом (`send_due_reminders`), поэтому легко
    тестируется напрямую."""
    due: list[tuple[CreditScheduleRow, int]] = []
    for row in schedule:
        offset = (row.payment_date - today).days
        if offset in OFFSETS and (row.payment_date.isoformat(), offset) not in already_sent:
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


async def send_due_reminders(bot: Bot, config: Config, texts: Texts) -> None:
    """Для каждого кредита с включёнными уведомлениями — рассылает всем участникам профиля
    (`config.allowed_telegram_ids`) напоминания, чей срок наступил (см. `due_reminders`), и
    отмечает их отправленными, чтобы не продублировать при повторном запуске джобы."""
    today = today_local_date(config.timezone)
    for credit_key in config.credit_sheets:
        with connect(config.db_path) as conn:
            if not credit_reminders_db.is_reminder_enabled(conn, credit_key):
                continue
            schedule = credit_reminders_db.list_schedule(conn, credit_key)
            sent = credit_reminders_db.already_sent(conn, credit_key)

        for row, offset in due_reminders(schedule, sent, today):
            text = _reminder_text(row, offset, config, texts)
            for telegram_id in config.allowed_telegram_ids:
                try:
                    await bot.send_message(telegram_id, text)
                except Exception:
                    logger.exception(
                        "Не удалось отправить напоминание по кредиту «%s» пользователю %s",
                        credit_key, telegram_id,
                    )
            with connect(config.db_path) as conn:
                credit_reminders_db.mark_sent(
                    conn, credit_key, row.payment_date, offset, now_utc_iso()
                )
