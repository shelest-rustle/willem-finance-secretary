from __future__ import annotations

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from willem.bot.keyboards import build_main_menu

router = Router(name="start")


@router.message(CommandStart())
async def handle_start(message: Message) -> None:
    await message.answer("Виллем на связи. 👋", reply_markup=build_main_menu())
