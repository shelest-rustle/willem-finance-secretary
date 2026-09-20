from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from willem import sheets
from willem.config import Config
from willem.db import sources
from willem.db.connection import connect, init_db
from willem.db.transactions import insert_transaction
from willem.sheets_sync import resync_owner_unsynced, sync_after_insert
from willem.texts import Texts


def make_config(db_path: Path) -> Config:
    return Config(
        profile_name="willem",
        persona="Виллем",
        bot_token="123:fake",
        owner_telegram_id=1,
        allowed_telegram_ids=(1, 2),
        db_path=str(db_path),
        google_sheets_credentials_path="",
        google_sheets_spreadsheet_id="",
        timezone="Asia/Almaty",
        log_level="INFO",
        seed_sources=(),
        seed_categories=(),
        seed_all_users=False,
        sync_all_users=False,
        auto_category={},
        optional_comment=False,
        people={},
        currency_options=(),
        type_options=(),
        debt_types=(),
        shared_ledger=False,
        sheet_name="Транзакции",
        credit_sheets={},
        debt_wallet_keywords=(),
        debt_obligation_keywords=(),
    )


async def test_sync_after_insert_skips_non_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, texts: Texts
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

    suffix = await sync_after_insert(config, texts, 2, tx, source_name="Kaspi")

    assert suffix == ""
    assert calls == []


async def test_sync_after_insert_syncs_non_owner_when_sync_all_users(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, texts: Texts
) -> None:
    """Профиль с sync_all_users=True (домохозяйство) — синкает не только владельца."""
    db_path = tmp_path / "test.db"
    init_db(str(db_path))
    config = replace(make_config(db_path), sync_all_users=True)

    calls = []
    monkeypatch.setattr(sheets, "append_transaction", lambda *a, **kw: calls.append(1) or True)

    with connect(str(db_path)) as conn:
        source = sources.create_source(conn, 2, "Kaspi", "card", "KZT")
        tx = insert_transaction(
            conn, user_id=2, type="income", amount=1000, currency="KZT", source_id=source.id
        )

    suffix = await sync_after_insert(config, texts, 2, tx, source_name="Kaspi")

    assert suffix == ""
    assert calls == [1]
    with connect(str(db_path)) as conn:
        row = conn.execute("SELECT synced FROM transactions WHERE id = ?", (tx.id,)).fetchone()
        assert row["synced"] == 1


async def test_sync_after_insert_success_marks_synced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, texts: Texts
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

    suffix = await sync_after_insert(config, texts, 1, tx, source_name="Kaspi")

    assert suffix == ""
    with connect(str(db_path)) as conn:
        row = conn.execute("SELECT synced FROM transactions WHERE id = ?", (tx.id,)).fetchone()
        assert row["synced"] == 1


async def test_sync_after_insert_failure_returns_suffix_and_stays_unsynced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, texts: Texts
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

    suffix = await sync_after_insert(config, texts, 1, tx, source_name="Kaspi")

    assert suffix == texts.get("common.sync_error_suffix")
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

    synced_count = resync_owner_unsynced(config)

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


def test_resync_owner_unsynced_syncs_everyone_when_sync_all_users(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "test.db"
    init_db(str(db_path))
    config = replace(make_config(db_path), sync_all_users=True)

    monkeypatch.setattr(sheets, "append_transaction", lambda *a, **kw: True)

    with connect(str(db_path)) as conn:
        owner_source = sources.create_source(conn, 1, "Kaspi", "card", "KZT")
        second_source = sources.create_source(conn, 2, "Наличные", "cash", "KZT")
        insert_transaction(
            conn, user_id=1, type="income", amount=1000, currency="KZT", source_id=owner_source.id
        )
        insert_transaction(
            conn, user_id=2, type="income", amount=500, currency="KZT", source_id=second_source.id
        )

    synced_count = resync_owner_unsynced(config)

    assert synced_count == 2
    with connect(str(db_path)) as conn:
        rows = conn.execute("SELECT synced FROM transactions").fetchall()
        assert all(row["synced"] == 1 for row in rows)


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

    synced_count = resync_owner_unsynced(config)

    assert synced_count == 0
    with connect(str(db_path)) as conn:
        row = conn.execute("SELECT synced FROM transactions WHERE id = ?", (tx.id,)).fetchone()
        assert row["synced"] == 0
