from __future__ import annotations

from datetime import date
from pathlib import Path

from willem.config import Config
from willem.credit_reminders import due_reminders, send_due_reminders
from willem.db import credit_reminders as credit_reminders_db
from willem.db.connection import connect, init_db
from willem.db.credit_reminders import CreditScheduleRow
from willem.texts import Texts

TODAY = date(2026, 11, 16)


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
        credit_sheets={"Tinkoff REF Кредит Лики": "Tinkoff REF Кредит Лики"},
        debt_wallet_keywords=(),
        debt_obligation_keywords=(),
        credit_reminder_currency="RUB",
    )


def _row(payment_date: date, amount: float = 10920.0, credit_key: str = "Tinkoff REF Кредит Лики") -> CreditScheduleRow:
    return CreditScheduleRow(credit_key=credit_key, row_number=8, payment_date=payment_date, planned_amount=amount)


def test_due_reminders_fires_on_each_offset_boundary() -> None:
    schedule = [_row(date(2026, 11, 19))]  # today + 3
    due = due_reminders(schedule, already_sent=set(), acknowledged=set(), today=TODAY)
    assert due == [(schedule[0], 3)]


def test_due_reminders_skips_dates_outside_offsets() -> None:
    schedule = [_row(date(2026, 11, 25))]  # today + 9 — не входит в (3, 2, 1, 0)
    assert due_reminders(schedule, already_sent=set(), acknowledged=set(), today=TODAY) == []


def test_due_reminders_skips_already_sent() -> None:
    schedule = [_row(date(2026, 11, 19))]
    already_sent = {("2026-11-19", 3)}
    assert due_reminders(schedule, already_sent, acknowledged=set(), today=TODAY) == []


def test_due_reminders_skips_acknowledged_payment() -> None:
    """Кнопка "Уже оплачено" гасит ВСЕ оставшиеся офсеты для этой даты, не только текущий."""
    schedule = [_row(date(2026, 11, 17))]  # today + 1
    acknowledged = {"2026-11-17"}
    assert due_reminders(schedule, already_sent=set(), acknowledged=acknowledged, today=TODAY) == []


def test_due_reminders_fires_on_payment_day_itself() -> None:
    schedule = [_row(date(2026, 11, 16))]  # offset 0
    assert due_reminders(schedule, already_sent=set(), acknowledged=set(), today=TODAY) == [
        (schedule[0], 0)
    ]


def test_due_reminders_handles_multiple_rows_independently() -> None:
    due_row = _row(date(2026, 11, 18))  # offset 2
    far_row = _row(date(2026, 12, 1))
    due = due_reminders([due_row, far_row], already_sent=set(), acknowledged=set(), today=TODAY)
    assert due == [(due_row, 2)]


class _FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str, reply_markup=None) -> None:
        self.sent.append((chat_id, text))


async def test_send_due_reminders_sends_to_all_allowed_users_and_logs(
    tmp_path: Path, texts: Texts
) -> None:
    db_path = tmp_path / "test.db"
    init_db(str(db_path))
    config = make_config(db_path)

    with connect(str(db_path)) as conn:
        credit_reminders_db.replace_schedule(
            conn, "Tinkoff REF Кредит Лики", [(8, "2026-11-19", 10920.0)]
        )

    bot = _FakeBot()
    await send_due_reminders_at(bot, config, texts, today=date(2026, 11, 16))

    assert {chat_id for chat_id, _ in bot.sent} == set(config.allowed_telegram_ids)
    assert all("Tinkoff REF Кредит Лики" in text for _, text in bot.sent)

    with connect(str(db_path)) as conn:
        assert credit_reminders_db.already_sent(conn, "Tinkoff REF Кредит Лики") == {
            ("2026-11-19", 3)
        }


async def test_send_due_reminders_skips_disabled_credit(
    tmp_path: Path, texts: Texts
) -> None:
    db_path = tmp_path / "test.db"
    init_db(str(db_path))
    config = make_config(db_path)

    with connect(str(db_path)) as conn:
        credit_reminders_db.replace_schedule(
            conn, "Tinkoff REF Кредит Лики", [(8, "2026-11-19", 10920.0)]
        )
        credit_reminders_db.set_reminder_enabled(conn, "Tinkoff REF Кредит Лики", False)

    bot = _FakeBot()
    await send_due_reminders_at(bot, config, texts, today=date(2026, 11, 16))

    assert bot.sent == []


async def test_send_due_reminders_skips_acknowledged_payment(
    tmp_path: Path, texts: Texts
) -> None:
    db_path = tmp_path / "test.db"
    init_db(str(db_path))
    config = make_config(db_path)

    with connect(str(db_path)) as conn:
        credit_reminders_db.replace_schedule(
            conn, "Tinkoff REF Кредит Лики", [(8, "2026-11-19", 10920.0)]
        )
        credit_reminders_db.ack_payment(
            conn, "Tinkoff REF Кредит Лики", date(2026, 11, 19), "2026-11-13T00:00:00+00:00"
        )

    bot = _FakeBot()
    await send_due_reminders_at(bot, config, texts, today=date(2026, 11, 16))

    assert bot.sent == []


async def test_send_due_reminders_does_not_duplicate_on_second_run(
    tmp_path: Path, texts: Texts
) -> None:
    db_path = tmp_path / "test.db"
    init_db(str(db_path))
    config = make_config(db_path)

    with connect(str(db_path)) as conn:
        credit_reminders_db.replace_schedule(
            conn, "Tinkoff REF Кредит Лики", [(8, "2026-11-19", 10920.0)]
        )

    bot = _FakeBot()
    await send_due_reminders_at(bot, config, texts, today=date(2026, 11, 16))
    first_count = len(bot.sent)
    await send_due_reminders_at(bot, config, texts, today=date(2026, 11, 16))

    assert len(bot.sent) == first_count


async def send_due_reminders_at(bot, config, texts, *, today: date) -> None:
    """Обёртка, фиксирующая "сегодня" — send_due_reminders берёт дату из
    timeutil.today_local_date(config.timezone), которую не хочется мокать в каждом тесте
    по отдельности, поэтому подменяем через monkeypatch на уровне модуля перед вызовом."""
    import willem.credit_reminders as module

    original = module.today_local_date
    module.today_local_date = lambda _tz: today
    try:
        await send_due_reminders(bot, config, texts)
    finally:
        module.today_local_date = original
