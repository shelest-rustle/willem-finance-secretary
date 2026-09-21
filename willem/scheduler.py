from __future__ import annotations

import logging

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from willem.config import Config
from willem.credit_reminders import send_due_reminders
from willem.credit_sheets import resync_credit_schedules
from willem.sheets_sync import resync_unsynced_for_owner
from willem.texts import Texts

logger = logging.getLogger(__name__)


def create_scheduler(config: Config, bot: Bot, texts: Texts) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=config.timezone)
    scheduler.add_job(
        _run_nightly_resync,
        CronTrigger(hour=3, minute=0),
        args=[config],
        id="nightly_sheets_resync",
    )
    # Обе кредитные джобы имеют смысл только для профилей с настроенными credit_sheets
    # (сейчас — только Pantalone) — для остальных профилей не регистрируются вовсе, чтобы
    # не плодить лишние обращения к Google Sheets API впустую.
    if config.credit_sheets:
        scheduler.add_job(
            _run_credit_schedule_resync,
            CronTrigger(hour=3, minute=30),
            args=[config],
            id="credit_schedule_resync",
        )
        scheduler.add_job(
            _run_credit_reminders,
            CronTrigger(hour=9, minute=0),
            args=[config, bot, texts],
            id="credit_payment_reminders",
        )
    return scheduler


async def _run_nightly_resync(config: Config) -> None:
    logger.info("Ночная досинхронизация Google Sheets: старт")
    synced_count = await resync_unsynced_for_owner(config)
    logger.info("Ночная досинхронизация Google Sheets: готово, синхронизировано %s", synced_count)


async def _run_credit_schedule_resync(config: Config) -> None:
    logger.info("Ресинхронизация графика платежей по кредитам: старт")
    await resync_credit_schedules(config)
    logger.info("Ресинхронизация графика платежей по кредитам: готово")


async def _run_credit_reminders(config: Config, bot: Bot, texts: Texts) -> None:
    logger.info("Проверка напоминаний об оплате кредитов: старт")
    await send_due_reminders(bot, config, texts)
    logger.info("Проверка напоминаний об оплате кредитов: готово")
