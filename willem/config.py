from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv

_PROFILES_DIR = Path(__file__).resolve().parent.parent / "profiles"

_DEFAULT_CURRENCY_OPTIONS = (("KZT", "KZT"), ("RUB", "RUB"), ("USD", "USD"), ("USDT", "USDT"))
_DEFAULT_TYPE_OPTIONS = (("card", "Карта"), ("cash", "Наличные"), ("crypto", "Крипто"))


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Переменная окружения {name} не задана")
    return value


@dataclass(frozen=True)
class Config:
    profile_name: str
    persona: str
    bot_token: str
    owner_telegram_id: int
    allowed_telegram_ids: tuple[int, ...]
    db_path: str
    google_sheets_credentials_path: str
    google_sheets_spreadsheet_id: str
    timezone: str
    log_level: str
    seed_sources: tuple[tuple[str, str, str, str, str | None], ...]
    seed_categories: tuple[tuple[str, tuple[str, ...]], ...]
    seed_all_users: bool
    sync_all_users: bool
    # {"income": "Доходы", "transfer": "Переводы"} — для income/transfer сразу показывать
    # подкатегории этой категории (без выбора из полного списка); тип без записи — без
    # категории вовсе, как раньше.
    auto_category: dict[str, str]
    optional_comment: bool
    people: dict[int, str]
    currency_options: tuple[tuple[str, str], ...]
    type_options: tuple[tuple[str, str], ...]
    debt_types: tuple[str, ...]
    shared_ledger: bool
    sheet_name: str
    # Профильная классификация долговых источников (kind="debt") для /balances — если
    # заданы, вместо единой секции "долги" показываются "долговые кошельки" (кредитки/
    # кубышки, с кредитным лимитом) и "долговые обязательства" (личные долги) отдельно,
    # а источники, не подошедшие ни под одно ключевое слово, из /balances исключаются
    # (см. willem/bot/handlers/reports.py::_balances_text). Пустые кортежи — профиль не
    # настроен на новую схему, используется старое поведение (единая секция "долги").
    debt_wallet_keywords: tuple[str, ...]
    debt_obligation_keywords: tuple[str, ...]
    # {"МТС Лика": "МТС Кредит Лики", ...} — подкатегория "Кредиты и долги" -> название
    # листа с графиком этого кредита в той же Google-таблице. Если подкатегория расхода
    # попадает в этот словарь, включается доп. button-flow (тип платежа / что с
    # переплатой) и запись в соответствующий кредитный лист — см. willem/credit_sheets.py.
    credit_sheets: dict[str, str]


def ledger_user_id(config: Config, telegram_id: int) -> int:
    """Ключ леджера для источников/категорий/транзакций.

    Обычно это и есть реальный Telegram id — у каждого пользователя свой независимый
    леджер. Но если в профиле включён `shared_ledger` (домохозяйство на несколько
    Telegram-аккаунтов) — все сводятся к id владельца профиля, то есть все читают и
    пишут в один и тот же общий леджер. Кто именно совершил операцию — хранится отдельно,
    в `transactions.who`/`config.people`, и на выбор леджера не влияет.
    """
    return config.owner_telegram_id if config.shared_ledger else telegram_id


def _parse_additional_ids(raw: str) -> list[int]:
    return [int(part) for part in raw.split(",") if part.strip()]


def profile_yaml_path(profile_name: str) -> Path:
    return _PROFILES_DIR / f"{profile_name}.yaml"


def _load_profile_yaml(profile_name: str) -> dict:
    path = profile_yaml_path(profile_name)
    if not path.exists():
        raise RuntimeError(f"Профиль не найден: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _parse_seed_sources(
    raw: list[dict], debt_types: tuple[str, ...]
) -> tuple[tuple[str, str, str, str, str | None], ...]:
    result = []
    for s in raw:
        kind = "debt" if s["type"] in debt_types else "asset"
        result.append((s["name"], s["type"], s["currency"], kind, s.get("owner")))
    return tuple(result)


def _parse_seed_categories(raw: list) -> tuple[tuple[str, tuple[str, ...]], ...]:
    result = []
    for item in raw:
        if isinstance(item, str):
            result.append((item, ()))
        else:
            result.append((item["name"], tuple(item.get("subcategories", []))))
    return tuple(result)


def load_config() -> Config:
    profile_name = os.environ.get("PROFILE", "willem")
    load_dotenv(f".env.{profile_name}")
    profile = _load_profile_yaml(profile_name)

    owner_telegram_id = int(_require("OWNER_TELEGRAM_ID"))
    additional_ids = _parse_additional_ids(os.environ.get("ADDITIONAL_TELEGRAM_IDS", ""))
    allowed_telegram_ids = (owner_telegram_id, *dict.fromkeys(additional_ids))

    debt_types = tuple(profile.get("debt_types", []))
    people = {int(k): v for k, v in (profile.get("people") or {}).items()}
    currency_options = tuple(
        tuple(pair) for pair in profile.get("currency_options", _DEFAULT_CURRENCY_OPTIONS)
    )
    type_options = tuple(
        tuple(pair) for pair in profile.get("type_options", _DEFAULT_TYPE_OPTIONS)
    )

    return Config(
        profile_name=profile_name,
        persona=profile.get("persona", profile_name),
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
        seed_sources=_parse_seed_sources(profile.get("seed_sources", []), debt_types),
        seed_categories=_parse_seed_categories(profile.get("seed_categories", [])),
        seed_all_users=bool(profile.get("seed_all_users", False)),
        sync_all_users=bool(profile.get("sync_all_users", False)),
        auto_category=dict(profile.get("auto_category") or {}),
        optional_comment=bool(profile.get("optional_comment", False)),
        people=people,
        currency_options=currency_options,
        type_options=type_options,
        debt_types=debt_types,
        shared_ledger=bool(profile.get("shared_ledger", False)),
        sheet_name=profile.get("sheet_name", "Транзакции"),
        credit_sheets=dict(profile.get("credit_sheets") or {}),
        debt_wallet_keywords=tuple(profile.get("debt_wallet_keywords", [])),
        debt_obligation_keywords=tuple(profile.get("debt_obligation_keywords", [])),
    )
