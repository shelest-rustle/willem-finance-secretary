from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from willem.bot.handlers import categories, expense, income, reports, sources, start, transfer
from willem.bot.middlewares import AllowedUsersMiddleware
from willem.config import Config
from willem.scheduler import create_scheduler
from willem.texts import Texts

logger = logging.getLogger(__name__)


def create_dispatcher(config: Config) -> Dispatcher:
    dp = Dispatcher()
    allowed_users = AllowedUsersMiddleware(config.allowed_telegram_ids)
    dp.message.middleware(allowed_users)
    dp.callback_query.middleware(allowed_users)
    dp.include_router(start.router)
    dp.include_router(expense.router)
    dp.include_router(income.router)
    dp.include_router(transfer.router)
    dp.include_router(categories.router)
    dp.include_router(sources.router)
    dp.include_router(reports.router)
    return dp


async def run_bot(config: Config, texts: Texts) -> None:
    bot = Bot(token=config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = create_dispatcher(config)

    scheduler = create_scheduler(config)
    scheduler.start()

    logger.info("Запуск polling (профиль: %s)", config.profile_name)
    try:
        await dp.start_polling(bot, config=config, texts=texts)
    finally:
        scheduler.shutdown(wait=False)
