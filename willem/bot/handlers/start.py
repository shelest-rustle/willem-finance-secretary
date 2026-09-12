from __future__ import annotations

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from willem.bot.keyboards import build_main_menu
from willem.texts import Texts

router = Router(name="start")


@router.message(CommandStart())
async def handle_start(message: Message, texts: Texts) -> None:
    await message.answer(texts.get("start.greeting"), reply_markup=build_main_menu())
