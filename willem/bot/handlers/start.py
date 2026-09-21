from __future__ import annotations

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from willem.bot.keyboards import build_main_menu
from willem.config import Config
from willem.texts import Texts

router = Router(name="start")


@router.message(CommandStart())
async def handle_start(message: Message, config: Config, texts: Texts) -> None:
    keyboard = build_main_menu(show_credits=bool(config.credit_sheets))
    await message.answer(texts.get("start.greeting"), reply_markup=keyboard)
