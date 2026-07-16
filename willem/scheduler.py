from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from willem.config import Config
from willem.sheets_sync import resync_unsynced_for_owner

logger = logging.getLogger(__name__)


def create_scheduler(config: Config) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=config.timezone)
    scheduler.add_job(
        _run_nightly_resync,
        CronTrigger(hour=3, minute=0),
        args=[config],
        id="nightly_sheets_resync",
    )
    return scheduler


async def _run_nightly_resync(config: Config) -> None:
    logger.info("Ночная досинхронизация Google Sheets: старт")
    synced_count = await resync_unsynced_for_owner(config)
    logger.info("Ночная досинхронизация Google Sheets: готово, синхронизировано %s", synced_count)
