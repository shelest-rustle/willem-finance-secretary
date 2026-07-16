from __future__ import annotations

from pathlib import Path

import pytest

from willem import sheets
from willem.config import Config
from willem.db import sources
from willem.db.connection import connect, init_db
from willem.db.transactions import insert_transaction
from willem.sheets_sync import SYNC_ERROR_SUFFIX, _resync_owner_unsynced, sync_after_insert


def make_config(db_path: Path) -> Config:
    return Config(
        bot_token="123:fake",
        owner_telegram_id=1,
        allowed_telegram_ids=(1, 2),
        db_path=str(db_path),
        google_sheets_credentials_path="",
        google_sheets_spreadsheet_id="",
        timezone="Asia/Almaty",
        log_level="INFO",
    )


async def test_sync_after_insert_skips_non_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "test.db"
    init_db(str(db_path))
    config = make_config(db_path)

    calls = []
    monkeypatch.setattr(sheets, "append_transaction", lambda *a, **kw: calls.append(1) or True)

    with connect(str(db_path)) as conn:
        source = sources.create_source(conn, 2, "Kaspi", "card", "KZT")
        tx = insert_transaction(
            conn, user_id=2, type="income", amount=1000, currency="KZT", source_id=source.id
        )

    suffix = await sync_after_insert(config, 2, tx, source_name="Kaspi")

    assert suffix == ""
    assert calls == []


async def test_sync_after_insert_success_marks_synced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "test.db"
    init_db(str(db_path))
    config = make_config(db_path)

    monkeypatch.setattr(sheets, "append_transaction", lambda *a, **kw: True)

    with connect(str(db_path)) as conn:
        source = sources.create_source(conn, 1, "Kaspi", "card", "KZT")
        tx = insert_transaction(
            conn, user_id=1, type="income", amount=1000, currency="KZT", source_id=source.id
        )

    suffix = await sync_after_insert(config, 1, tx, source_name="Kaspi")

    assert suffix == ""
    with connect(str(db_path)) as conn:
        row = conn.execute("SELECT synced FROM transactions WHERE id = ?", (tx.id,)).fetchone()
        assert row["synced"] == 1


async def test_sync_after_insert_failure_returns_suffix_and_stays_unsynced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "test.db"
    init_db(str(db_path))
    config = make_config(db_path)

    monkeypatch.setattr(sheets, "append_transaction", lambda *a, **kw: False)

    with connect(str(db_path)) as conn:
        source = sources.create_source(conn, 1, "Kaspi", "card", "KZT")
        tx = insert_transaction(
            conn, user_id=1, type="income", amount=1000, currency="KZT", source_id=source.id
        )

    suffix = await sync_after_insert(config, 1, tx, source_name="Kaspi")

    assert suffix == SYNC_ERROR_SUFFIX
    with connect(str(db_path)) as conn:
        row = conn.execute("SELECT synced FROM transactions WHERE id = ?", (tx.id,)).fetchone()
        assert row["synced"] == 0


def test_resync_owner_unsynced_ignores_other_users(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "test.db"
    init_db(str(db_path))
    config = make_config(db_path)

    monkeypatch.setattr(sheets, "append_transaction", lambda *a, **kw: True)

    with connect(str(db_path)) as conn:
        owner_source = sources.create_source(conn, 1, "Kaspi", "card", "KZT")
        friend_source = sources.create_source(conn, 2, "Наличные", "cash", "KZT")
        owner_tx = insert_transaction(
            conn, user_id=1, type="income", amount=1000, currency="KZT", source_id=owner_source.id
        )
        insert_transaction(
            conn, user_id=2, type="income", amount=500, currency="KZT", source_id=friend_source.id
        )

    synced_count = _resync_owner_unsynced(config)

    assert synced_count == 1
    with connect(str(db_path)) as conn:
        owner_row = conn.execute(
            "SELECT synced FROM transactions WHERE id = ?", (owner_tx.id,)
        ).fetchone()
        assert owner_row["synced"] == 1
        friend_row = conn.execute(
            "SELECT synced FROM transactions WHERE user_id = 2"
        ).fetchone()
        assert friend_row["synced"] == 0


def test_resync_owner_unsynced_leaves_failures_unsynced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "test.db"
    init_db(str(db_path))
    config = make_config(db_path)

    monkeypatch.setattr(sheets, "append_transaction", lambda *a, **kw: False)

    with connect(str(db_path)) as conn:
        source = sources.create_source(conn, 1, "Kaspi", "card", "KZT")
        tx = insert_transaction(
            conn, user_id=1, type="income", amount=1000, currency="KZT", source_id=source.id
        )

    synced_count = _resync_owner_unsynced(config)

    assert synced_count == 0
    with connect(str(db_path)) as conn:
        row = conn.execute("SELECT synced FROM transactions WHERE id = ?", (tx.id,)).fetchone()
        assert row["synced"] == 0
