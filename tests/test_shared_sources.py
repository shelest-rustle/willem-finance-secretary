from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from willem.config import Config, ledger_user_id
from willem.db import categories, seed, sources
from willem.db.connection import connect, init_db
from willem.db.transactions import get_source_balance, insert_transaction

LIKA = 431744401
YAROSLAV = 1459561428


def make_config(**overrides) -> Config:
    base = dict(
        profile_name="pantalone",
        persona="Регистратор",
        bot_token="123:fake",
        owner_telegram_id=LIKA,
        allowed_telegram_ids=(LIKA, YAROSLAV),
        db_path=":memory:",
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
        people={LIKA: "Лика", YAROSLAV: "Ярослав"},
        currency_options=(),
        type_options=(),
        debt_types=(),
        shared_ledger=False,
        sheet_name="Учёт",
        credit_sheets={},
        debt_wallet_keywords=(),
        debt_obligation_keywords=(),
    )
    base.update(overrides)
    return Config(**base)


def test_ledger_user_id_stays_real_by_default() -> None:
    config = make_config(shared_ledger=False)
    assert ledger_user_id(config, LIKA) == LIKA
    assert ledger_user_id(config, YAROSLAV) == YAROSLAV


def test_ledger_user_id_resolves_to_owner_when_shared() -> None:
    config = make_config(shared_ledger=True)
    assert ledger_user_id(config, LIKA) == LIKA
    assert ledger_user_id(config, YAROSLAV) == LIKA  # оба сводятся к владельцу профиля


def test_shared_ledger_seeded_once_covers_both_real_users(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(str(db_path))
    config = make_config(shared_ledger=True, db_path=str(db_path))

    seed_sources = [
        ("Наличные", "Наличные", "KZT", "asset", "Семья"),
        ("Kaspi Лики", "Дебетовая карта", "KZT", "asset", "Лика"),
    ]

    with connect(str(db_path)) as conn:
        # Бутстрап сеет один раз, под id владельца — как делает bootstrap.py при shared_ledger.
        seed.seed_defaults(conn, config.owner_telegram_id, seed_sources, [])

        lika_ledger = ledger_user_id(config, LIKA)
        yaroslav_ledger = ledger_user_id(config, YAROSLAV)
        assert lika_ledger == yaroslav_ledger

        lika_sources = sources.list_sources(conn, lika_ledger)
        yaroslav_sources = sources.list_sources(conn, yaroslav_ledger)
        assert {s.name for s in lika_sources} == {"Наличные", "Kaspi Лики"}
        assert lika_sources == yaroslav_sources


def test_shared_ledger_balance_reflects_transactions_from_both_users(tmp_path: Path) -> None:
    """income от Лики и расход Ярослава — оба пишут в один и тот же леджер (владельца),
    поэтому баланс общий независимо от того, кто из двух совершил операцию."""
    db_path = tmp_path / "test.db"
    init_db(str(db_path))
    config = make_config(shared_ledger=True, db_path=str(db_path))

    with connect(str(db_path)) as conn:
        cash = sources.create_source(conn, config.owner_telegram_id, "Наличные", "Наличные", "KZT")
        category = categories.create_category(conn, config.owner_telegram_id, "Быт")

        insert_transaction(
            conn,
            user_id=ledger_user_id(config, LIKA),
            type="income",
            amount=10000,
            currency="KZT",
            source_id=cash.id,
            who="Лика",
        )
        insert_transaction(
            conn,
            user_id=ledger_user_id(config, YAROSLAV),
            type="expense",
            amount=3000,
            currency="KZT",
            source_id=cash.id,
            category_id=category.id,
            who="Ярослав",
        )

        assert get_source_balance(conn, cash.id) == 7000


def test_make_config_with_replace_toggles_shared_ledger() -> None:
    """Просто проверка, что helper-конфиг для тестов сам по себе корректен и переключаем."""
    config = make_config()
    shared = replace(config, shared_ledger=True)
    assert config.shared_ledger is False
    assert shared.shared_ledger is True
