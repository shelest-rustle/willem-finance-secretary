from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User

logger = logging.getLogger(__name__)


class AllowedUsersMiddleware(BaseMiddleware):
    """Пропускает апдейты только от разрешённых пользователей, остальные молча отбрасывает."""

    def __init__(self, allowed_ids: Iterable[int]) -> None:
        self.allowed_ids = set(allowed_ids)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user")
        if user is None or user.id not in self.allowed_ids:
            if user is not None:
                logger.warning("Отклонён апдейт от чужого user_id=%s", user.id)
            return None
        return await handler(event, data)
