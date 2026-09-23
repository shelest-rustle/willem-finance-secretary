from __future__ import annotations

from datetime import date
from pathlib import Path

from willem.db import credit_reminders as credit_reminders_db
from willem.db.connection import connect, init_db


def test_replace_schedule_overwrites_previous_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(str(db_path))

    with connect(str(db_path)) as conn:
        credit_reminders_db.replace_schedule(
            conn, "МТС Кредит Лики", [(8, "2026-09-19", 8558.0), (9, "2026-10-19", 8558.0)]
        )
        rows = credit_reminders_db.list_schedule(conn, "МТС Кредит Лики")
        assert [r.row_number for r in rows] == [8, 9]

        # Повторный ресинк с другими данными должен полностью заменить старые строки.
        credit_reminders_db.replace_schedule(conn, "МТС Кредит Лики", [(8, "2026-09-19", 9000.0)])
        rows = credit_reminders_db.list_schedule(conn, "МТС Кредит Лики")

    assert len(rows) == 1
    assert rows[0].planned_amount == 9000.0


def test_next_payment_finds_first_row_on_or_after_today(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(str(db_path))

    with connect(str(db_path)) as conn:
        credit_reminders_db.replace_schedule(
            conn,
            "МТС Кредит Лики",
            [(8, "2026-09-19", 8558.0), (9, "2026-10-19", 8558.0), (10, "2026-11-19", 8558.0)],
        )
        result = credit_reminders_db.next_payment(conn, "МТС Кредит Лики", date(2026, 9, 20))

    assert result is not None
    assert result.payment_date == date(2026, 10, 19)


def test_next_payment_none_when_schedule_exhausted(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(str(db_path))

    with connect(str(db_path)) as conn:
        credit_reminders_db.replace_schedule(conn, "МТС Кредит Лики", [(8, "2026-09-19", 8558.0)])
        result = credit_reminders_db.next_payment(conn, "МТС Кредит Лики", date(2026, 10, 1))

    assert result is None


def test_reminder_enabled_defaults_to_true_when_no_row(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(str(db_path))

    with connect(str(db_path)) as conn:
        assert credit_reminders_db.is_reminder_enabled(conn, "МТС Кредит Лики") is True


def test_set_reminder_enabled_toggles_and_persists(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(str(db_path))

    with connect(str(db_path)) as conn:
        credit_reminders_db.set_reminder_enabled(conn, "МТС Кредит Лики", False)
        assert credit_reminders_db.is_reminder_enabled(conn, "МТС Кредит Лики") is False

        credit_reminders_db.set_reminder_enabled(conn, "МТС Кредит Лики", True)
        assert credit_reminders_db.is_reminder_enabled(conn, "МТС Кредит Лики") is True


def test_mark_sent_and_already_sent(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(str(db_path))

    with connect(str(db_path)) as conn:
        assert credit_reminders_db.already_sent(conn, "МТС Кредит Лики") == set()
        credit_reminders_db.mark_sent(
            conn, "МТС Кредит Лики", date(2026, 11, 19), 3, "2026-11-16T00:00:00+00:00"
        )
        assert credit_reminders_db.already_sent(conn, "МТС Кредит Лики") == {("2026-11-19", 3)}


def test_ack_payment_and_acknowledged_payment_dates(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(str(db_path))

    with connect(str(db_path)) as conn:
        assert credit_reminders_db.acknowledged_payment_dates(conn, "МТС Кредит Лики") == set()
        credit_reminders_db.ack_payment(
            conn, "МТС Кредит Лики", date(2026, 11, 19), "2026-11-17T00:00:00+00:00"
        )
        assert credit_reminders_db.acknowledged_payment_dates(conn, "МТС Кредит Лики") == {
            "2026-11-19"
        }


def test_ack_payment_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(str(db_path))

    with connect(str(db_path)) as conn:
        credit_reminders_db.ack_payment(
            conn, "МТС Кредит Лики", date(2026, 11, 19), "2026-11-17T00:00:00+00:00"
        )
        # Повторный тап по кнопке не должен упасть на PRIMARY KEY(credit_key, payment_date).
        credit_reminders_db.ack_payment(
            conn, "МТС Кредит Лики", date(2026, 11, 19), "2026-11-18T00:00:00+00:00"
        )
        assert credit_reminders_db.acknowledged_payment_dates(conn, "МТС Кредит Лики") == {
            "2026-11-19"
        }
