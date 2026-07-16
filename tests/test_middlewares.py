from __future__ import annotations

from aiogram.types import User

from willem.bot.middlewares import AllowedUsersMiddleware


async def test_allowed_user_is_let_through() -> None:
    middleware = AllowedUsersMiddleware(allowed_ids=[42, 43])
    calls = []

    async def handler(event: object, data: dict) -> str:
        calls.append(event)
        return "ok"

    owner = User(id=42, is_bot=False, first_name="Owner")
    result = await middleware(handler, event=object(), data={"event_from_user": owner})

    assert result == "ok"
    assert len(calls) == 1


async def test_second_allowed_user_is_also_let_through() -> None:
    middleware = AllowedUsersMiddleware(allowed_ids=[42, 43])
    calls = []

    async def handler(event: object, data: dict) -> str:
        calls.append(event)
        return "ok"

    friend = User(id=43, is_bot=False, first_name="Friend")
    result = await middleware(handler, event=object(), data={"event_from_user": friend})

    assert result == "ok"
    assert len(calls) == 1


async def test_stranger_is_silently_dropped() -> None:
    middleware = AllowedUsersMiddleware(allowed_ids=[42])
    calls = []

    async def handler(event: object, data: dict) -> str:
        calls.append(event)
        return "ok"

    stranger = User(id=999, is_bot=False, first_name="Stranger")
    result = await middleware(handler, event=object(), data={"event_from_user": stranger})

    assert result is None
    assert calls == []


async def test_missing_user_is_dropped() -> None:
    middleware = AllowedUsersMiddleware(allowed_ids=[42])
    calls = []

    async def handler(event: object, data: dict) -> str:
        calls.append(event)
        return "ok"

    result = await middleware(handler, event=object(), data={})

    assert result is None
    assert calls == []
