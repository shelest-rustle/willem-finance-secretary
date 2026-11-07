from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from willem import sheets
from willem.bot.handlers.edit import (
    amount_fields,
    editable_fields,
    normalize_id_input,
    source_change,
    who_options,
)
from willem.config import Config
from willem.db import sources
from willem.db.connection import connect, init_db
from willem.db.sources import Source
from willem.db.transactions import (
    Transaction,
    find_by_id_prefix,
    get_transaction,
    insert_transaction,
    mark_synced,
    soft_delete_transaction,
    update_transaction,
)
from willem.sheets_sync import resync_owner_unsynced, sync_after_update
from willem.texts import Texts


def make_config(db_path: Path | str = "") -> Config:
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
        credit_reminder_currency="RUB",
    )


def make_tx(**overrides) -> Transaction:
    base = dict(
        id="3f2a9c1b-0000-4000-8000-000000000000",
        user_id=1,
        type="expense",
        amount=1000.0,
        currency="KZT",
        target_amount=None,
        target_currency=None,
        source_id="s1",
        target_source_id=None,
        category_id="c1",
        subcategory_id=None,
        who=None,
        comment=None,
        created_at_utc="2026-11-07T10:00:00+00:00",
        synced=True,
        deleted=False,
    )
    base.update(overrides)
    return Transaction(**base)


def make_transfer(**overrides) -> Transaction:
    base = dict(
        type="transfer",
        amount=1000.0,
        currency="KZT",
        target_amount=1000.0,
        target_currency="KZT",
        source_id="s1",
        target_source_id="s2",
        category_id=None,
    )
    base.update(overrides)
    return make_tx(**base)


def make_source(source_id: str, currency: str) -> Source:
    return Source(
        id=source_id, user_id=1, name=source_id, type="card", currency=currency,
        kind="asset", owner=None, credit_limit=None, is_active=True,
    )


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    path = str(tmp_path / "test.db")
    init_db(path)
    return path


def insert_with_id(conn, tx_id: str, user_id: int = 1) -> None:
    """Операция с заданным id — чтобы смоделировать общий префикс у двух uuid."""
    source = sources.create_source(conn, user_id, f"src-{tx_id}", "card", "KZT")
    conn.execute(
        "INSERT INTO transactions (id, user_id, type, amount, currency, source_id, "
        "created_at_utc, synced, deleted) VALUES (?, ?, 'income', 100, 'KZT', ?, ?, 1, 0)",
        (tx_id, user_id, source.id, "2026-11-07T10:00:00+00:00"),
    )


# --- normalize_id_input ---


def test_normalize_id_input_accepts_prefix_and_full_id() -> None:
    assert normalize_id_input("  3F2A9C1B ") == "3f2a9c1b"
    full = "3f2a9c1b-7d4e-4a1b-9c2d-0123456789ab"
    assert normalize_id_input(full) == full


@pytest.mark.parametrize("text", ["3f2a9c1", "привет", "3f2a9c1b%", "1500 кофе", ""])
def test_normalize_id_input_rejects_garbage(text: str) -> None:
    assert normalize_id_input(text) is None


# --- find_by_id_prefix ---


def test_find_by_id_prefix_finds_by_8_chars_and_full_id(db_path: str) -> None:
    with connect(db_path) as conn:
        source = sources.create_source(conn, 1, "Kaspi", "card", "KZT")
        tx = insert_transaction(
            conn, user_id=1, type="income", amount=100, currency="KZT", source_id=source.id
        )
        assert [t.id for t in find_by_id_prefix(conn, 1, tx.id[:8])] == [tx.id]
        assert [t.id for t in find_by_id_prefix(conn, 1, tx.id)] == [tx.id]


def test_find_by_id_prefix_ignores_other_ledger_and_deleted(db_path: str) -> None:
    with connect(db_path) as conn:
        insert_with_id(conn, "aaaaaaaa-1", user_id=2)
        insert_with_id(conn, "aaaaaaaa-2")
        soft_delete_transaction(conn, "aaaaaaaa-2")
        assert find_by_id_prefix(conn, 1, "aaaaaaaa") == []


def test_find_by_id_prefix_returns_all_ambiguous_matches(db_path: str) -> None:
    with connect(db_path) as conn:
        insert_with_id(conn, "bbbbbbbb-1")
        insert_with_id(conn, "bbbbbbbb-2")
        assert {t.id for t in find_by_id_prefix(conn, 1, "bbbbbbbb")} == {"bbbbbbbb-1", "bbbbbbbb-2"}


# --- update_transaction / soft_delete_transaction ---


def test_update_transaction_sets_fields_and_resets_synced(db_path: str) -> None:
    with connect(db_path) as conn:
        insert_with_id(conn, "cccccccc-1")
        rub = sources.create_source(conn, 1, "Т-банк", "card", "RUB")
        update_transaction(
            conn, "cccccccc-1", amount=250.0, currency="RUB", source_id=rub.id, who="Лика", comment="x"
        )
        tx = get_transaction(conn, "cccccccc-1")
    assert (tx.amount, tx.currency, tx.source_id, tx.who, tx.comment) == (250.0, "RUB", rub.id, "Лика", "x")
    assert tx.synced is False


def test_update_transaction_none_clears_field(db_path: str) -> None:
    with connect(db_path) as conn:
        insert_with_id(conn, "cccccccc-2")
        update_transaction(conn, "cccccccc-2", comment="было", who="Семья")
        update_transaction(conn, "cccccccc-2", comment=None, who=None)
        tx = get_transaction(conn, "cccccccc-2")
    assert tx.comment is None
    assert tx.who is None


def test_update_transaction_rejects_unknown_field(db_path: str) -> None:
    with connect(db_path) as conn:
        insert_with_id(conn, "cccccccc-3")
        with pytest.raises(ValueError):
            update_transaction(conn, "cccccccc-3", created_at_utc="2020-01-01")


def test_soft_delete_resets_synced(db_path: str) -> None:
    with connect(db_path) as conn:
        insert_with_id(conn, "cccccccc-4")
        soft_delete_transaction(conn, "cccccccc-4")
        tx = get_transaction(conn, "cccccccc-4")
    assert tx.deleted is True
    assert tx.synced is False


# --- editable_fields ---


def test_editable_fields_willem_expense() -> None:
    fields = editable_fields(make_tx(), make_config(), has_subcategories=False)
    assert fields == ["amount", "source", "category", "comment"]


def test_editable_fields_household_expense_with_subcategories_and_people() -> None:
    config = replace(make_config(), people={10: "Лика", 11: "Ярослав"})
    fields = editable_fields(make_tx(), config, has_subcategories=True)
    assert fields == ["amount", "source", "category", "subcategory", "who", "comment"]


def test_editable_fields_income_has_no_category() -> None:
    fields = editable_fields(make_tx(type="income", category_id=None), make_config(), has_subcategories=False)
    assert fields == ["amount", "source", "comment"]


def test_editable_fields_same_currency_transfer() -> None:
    fields = editable_fields(make_transfer(), make_config(), has_subcategories=False)
    assert fields == ["amount", "source", "target_source", "comment"]


def test_editable_fields_cross_currency_transfer_has_target_amount() -> None:
    tx = make_transfer(target_currency="RUB", target_amount=180.0)
    fields = editable_fields(tx, make_config(), has_subcategories=False)
    assert fields == ["amount", "target_amount", "source", "target_source", "comment"]


def test_editable_fields_adjustment_only_comment() -> None:
    tx = make_tx(type="adjustment", category_id=None)
    assert editable_fields(tx, make_config(), has_subcategories=False) == ["comment"]


def test_who_options_unique_names_plus_family() -> None:
    config = replace(make_config(), people={10: "Лика", 11: "Ярослав", 12: "Лика"})
    assert who_options(config) == ["Лика", "Ярослав", "Семья"]


# --- source_change / amount_fields ---


def test_source_change_same_currency_no_question() -> None:
    fields, needs = source_change(make_tx(), make_source("s5", "KZT"), target=False)
    assert fields == {"source_id": "s5", "currency": "KZT"}
    assert needs is None


def test_source_change_other_currency_asks_amount() -> None:
    fields, needs = source_change(make_tx(), make_source("s5", "RUB"), target=False)
    assert fields == {"source_id": "s5", "currency": "RUB"}
    assert needs == "amount"


def test_amount_fields_merges_pending_source_change() -> None:
    fields = amount_fields(make_tx(), 300.0, {"source_id": "s5", "currency": "RUB"})
    assert fields == {"source_id": "s5", "currency": "RUB", "amount": 300.0}


def test_amount_fields_same_currency_transfer_keeps_target_equal() -> None:
    assert amount_fields(make_transfer(), 777.0) == {"amount": 777.0, "target_amount": 777.0}


def test_amount_fields_cross_currency_transfer_keeps_target() -> None:
    tx = make_transfer(target_currency="RUB", target_amount=180.0)
    assert amount_fields(tx, 2000.0) == {"amount": 2000.0}


def test_transfer_target_change_to_same_currency_sets_target_amount() -> None:
    tx = make_transfer(target_currency="RUB", target_amount=180.0)
    fields, needs = source_change(tx, make_source("s7", "KZT"), target=True)
    assert fields == {"target_source_id": "s7", "target_currency": "KZT", "target_amount": 1000.0}
    assert needs is None


def test_transfer_target_change_to_new_currency_asks_target_amount() -> None:
    fields, needs = source_change(make_transfer(), make_source("s7", "USD"), target=True)
    assert fields == {"target_source_id": "s7", "target_currency": "USD"}
    assert needs == "target_amount"


def test_transfer_target_change_same_target_currency_keeps_target_amount() -> None:
    tx = make_transfer(target_currency="RUB", target_amount=180.0)
    fields, needs = source_change(tx, make_source("s7", "RUB"), target=True)
    assert fields == {"target_source_id": "s7", "target_currency": "RUB"}
    assert needs is None


def test_transfer_from_change_to_target_currency_then_amount_equalizes() -> None:
    """Был KZT → RUB, «Откуда» стало RUB-источником: спрашиваем сумму, и перевод становится
    одновалютным — сумма зачисления должна выровняться с суммой списания."""
    tx = make_transfer(target_currency="RUB", target_amount=180.0)
    pending, needs = source_change(tx, make_source("s8", "RUB"), target=False)
    assert needs == "amount"
    fields = amount_fields(tx, 200.0, pending)
    assert fields["target_amount"] == 200.0
    assert fields["currency"] == "RUB"


# --- синк правок в Sheets ---


async def test_sync_after_update_rewrites_row_and_marks_synced(
    db_path: str, texts: Texts, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(db_path)
    calls = []
    monkeypatch.setattr(sheets, "upsert_transaction", lambda c, tx, **kw: calls.append((tx, kw)) or True)

    with connect(db_path) as conn:
        source = sources.create_source(conn, 1, "Kaspi", "card", "KZT")
        tx = insert_transaction(
            conn, user_id=1, type="income", amount=100, currency="KZT", source_id=source.id
        )
        mark_synced(conn, tx.id)
        update_transaction(conn, tx.id, amount=999.0)

    suffix = await sync_after_update(config, texts, 1, tx.id)

    assert suffix == ""
    assert calls[0][0].amount == 999.0
    assert calls[0][1]["source_name"] == "Kaspi"
    with connect(db_path) as conn:
        assert get_transaction(conn, tx.id).synced is True


async def test_sync_after_update_failure_leaves_unsynced(
    db_path: str, texts: Texts, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(db_path)
    monkeypatch.setattr(sheets, "upsert_transaction", lambda *a, **kw: False)

    with connect(db_path) as conn:
        source = sources.create_source(conn, 1, "Kaspi", "card", "KZT")
        tx = insert_transaction(
            conn, user_id=1, type="income", amount=100, currency="KZT", source_id=source.id
        )
        update_transaction(conn, tx.id, comment="правка")

    suffix = await sync_after_update(config, texts, 1, tx.id)

    assert suffix == texts.get("common.sync_error_suffix")
    with connect(db_path) as conn:
        assert get_transaction(conn, tx.id).synced is False


async def test_sync_after_update_skips_non_synced_user(
    db_path: str, texts: Texts, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(db_path)
    calls = []
    monkeypatch.setattr(sheets, "upsert_transaction", lambda *a, **kw: calls.append(1) or True)

    assert await sync_after_update(config, texts, 2, "whatever") == ""
    assert calls == []


def test_resync_picks_up_failed_edit_and_delete(db_path: str, monkeypatch: pytest.MonkeyPatch) -> None:
    config = make_config(db_path)
    seen = []
    monkeypatch.setattr(sheets, "upsert_transaction", lambda c, tx, **kw: seen.append(tx) or True)

    with connect(db_path) as conn:
        source = sources.create_source(conn, 1, "Kaspi", "card", "KZT")
        edited = insert_transaction(
            conn, user_id=1, type="income", amount=100, currency="KZT", source_id=source.id
        )
        removed = insert_transaction(
            conn, user_id=1, type="income", amount=50, currency="KZT", source_id=source.id
        )
        mark_synced(conn, edited.id)
        mark_synced(conn, removed.id)
        update_transaction(conn, edited.id, amount=120.0)
        soft_delete_transaction(conn, removed.id)

    assert resync_owner_unsynced(config) == 2
    by_id = {tx.id: tx for tx in seen}
    assert by_id[edited.id].amount == 120.0
    assert by_id[removed.id].deleted is True
