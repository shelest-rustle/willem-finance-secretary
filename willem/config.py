from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Переменная окружения {name} не задана")
    return value


@dataclass(frozen=True)
class Config:
    bot_token: str
    owner_telegram_id: int
    allowed_telegram_ids: tuple[int, ...]
    db_path: str
    google_sheets_credentials_path: str
    google_sheets_spreadsheet_id: str
    timezone: str
    log_level: str


def _parse_additional_ids(raw: str) -> list[int]:
    return [int(part) for part in raw.split(",") if part.strip()]


def load_config() -> Config:
    owner_telegram_id = int(_require("OWNER_TELEGRAM_ID"))
    additional_ids = _parse_additional_ids(os.environ.get("ADDITIONAL_TELEGRAM_IDS", ""))
    allowed_telegram_ids = (owner_telegram_id, *dict.fromkeys(additional_ids))

    return Config(
        bot_token=_require("BOT_TOKEN"),
        owner_telegram_id=owner_telegram_id,
        allowed_telegram_ids=allowed_telegram_ids,
        db_path=os.environ.get("DB_PATH", "./data/willem.db"),
        google_sheets_credentials_path=os.environ.get(
            "GOOGLE_SHEETS_CREDENTIALS_PATH", "./credentials.json"
        ),
        google_sheets_spreadsheet_id=os.environ.get("GOOGLE_SHEETS_SPREADSHEET_ID", ""),
        timezone=os.environ.get("TIMEZONE", "Asia/Almaty"),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
    )
