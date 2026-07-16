from __future__ import annotations

from pathlib import Path

from willem.db import sources
from willem.db.connection import connect, init_db
from willem.db.transactions import get_source_balance, insert_transaction


def test_adjustment_moves_balance_to_target(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(str(db_path))

    with connect(str(db_path)) as conn:
        source = sources.create_source(conn, 1, "Kaspi", "card", "KZT")
        insert_transaction(
            conn, user_id=1, type="income", amount=10000, currency="KZT", source_id=source.id
        )
        assert get_source_balance(conn, source.id) == 10000

        # Скорректировали остаток вверх (пропущенные расходы/доходы вне бота).
        insert_transaction(
            conn,
            user_id=1,
            type="adjustment",
            amount=5000,
            currency="KZT",
            source_id=source.id,
            comment="Коррекция остатка",
        )
        assert get_source_balance(conn, source.id) == 15000

        # И вниз — коррекция допускает отрицательную дельту.
        insert_transaction(
            conn,
            user_id=1,
            type="adjustment",
            amount=-3000,
            currency="KZT",
            source_id=source.id,
            comment="Коррекция остатка",
        )
        assert get_source_balance(conn, source.id) == 12000
