from __future__ import annotations

from pathlib import Path

from willem.bot.handlers.credits import _detail_text, _list_text
from willem.config import Config, profile_yaml_path
from willem.db import credit_reminders as credit_reminders_db
from willem.db.connection import connect, init_db
from willem.texts import load_texts


def make_config(db_path: Path) -> Config:
    return Config(
        profile_name="pantalone",
        persona="Регистратор",
        bot_token="123:fake",
        owner_telegram_id=1,
        allowed_telegram_ids=(1, 2, 3),
        db_path=str(db_path),
        google_sheets_credentials_path="",
        google_sheets_spreadsheet_id="",
        timezone="Asia/Almaty",
        log_level="INFO",
        seed_sources=(),
        seed_categories=(),
        seed_all_users=False,
        sync_all_users=True,
        auto_category={},
        optional_comment=False,
        people={},
        currency_options=(),
        type_options=(),
        debt_types=(),
        shared_ledger=True,
        sheet_name="Учёт",
        credit_sheets={
            "МТС Кредит Лики": "МТС Кредит Лики",
            "Tinkoff REF Кредит Лики": "Tinkoff REF Кредит Лики",
        },
        debt_wallet_keywords=(),
        debt_obligation_keywords=(),
        credit_reminder_currency="RUB",
    )


def test_list_text_shows_next_payment_per_credit(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(str(db_path))
    config = make_config(db_path)
    texts = load_texts(profile_yaml_path("pantalone"))

    with connect(str(db_path)) as conn:
        credit_reminders_db.replace_schedule(
            conn, "МТС Кредит Лики", [(8, "2099-09-19", 8558.0)]
        )
        # "Tinkoff REF Кредит Лики" — без снимка графика вовсе (ещё не ресинкали).

    text, keyboard = _list_text(config, texts)

    assert "«МТС Кредит Лики»: очередной взнос 19.09.2099, 8 558 ₽" in text
    assert "«Tinkoff REF Кредит Лики»: график исчерпан" in text
    assert len(keyboard.inline_keyboard) == 2


def test_detail_text_reflects_notification_status(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(str(db_path))
    config = make_config(db_path)
    texts = load_texts(profile_yaml_path("pantalone"))

    with connect(str(db_path)) as conn:
        credit_reminders_db.replace_schedule(
            conn, "МТС Кредит Лики", [(8, "2099-09-19", 8558.0)]
        )

    text, enabled = _detail_text("МТС Кредит Лики", config, texts)
    assert enabled is True
    assert "действуют" in text
    assert "19.09.2099" in text

    with connect(str(db_path)) as conn:
        credit_reminders_db.set_reminder_enabled(conn, "МТС Кредит Лики", False)

    text, enabled = _detail_text("МТС Кредит Лики", config, texts)
    assert enabled is False
    assert "приостановлены" in text
