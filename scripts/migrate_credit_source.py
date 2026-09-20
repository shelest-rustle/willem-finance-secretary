"""Одноразовая миграция источника "Tinkoff REF Кредит Лики" для профиля Pantalone.

Четвёртый кредит появился после первоначального сидинга источников профиля — Лика
завела для него отдельный лист в таблице и подкатегорию в "Справочники" (см.
MTS_CREDIT_TEST.md). `profiles/pantalone.yaml::seed_sources` теперь содержит и этот
источник, но сидинг (`willem.db.seed.seed_defaults`) применяет `seed_sources` только
на пустом списке источников для пользователя — для уже засеянного профиля источник
нужно добавить этим скриптом.

Идемпотентно: если источник с таким именем уже есть (активный), повторно не создаётся.

Использование:
    PROFILE=pantalone poetry run python scripts/migrate_credit_source.py
        — сухой прогон (по умолчанию): печатает, что будет сделано, ничего не меняет.
    PROFILE=pantalone poetry run python scripts/migrate_credit_source.py --write
        — реально применяет изменения к БД (config.db_path).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

SOURCE_NAME = "Tinkoff REF Кредит Лики"
SOURCE_TYPE = "Кредит"
SOURCE_CURRENCY = "RUB"
SOURCE_OWNER = "Лика"
SOURCE_KIND = "debt"  # "Кредит" в debt_types профиля -> kind="debt", см. willem/config.py


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write", action="store_true", help="Реально применить изменения (по умолчанию — сухой прогон)"
    )
    args = parser.parse_args(argv)

    os.environ.setdefault("PROFILE", "pantalone")
    if os.environ["PROFILE"] != "pantalone":
        raise SystemExit(
            f"PROFILE={os.environ['PROFILE']!r}, а миграция рассчитана только на "
            f"профиль pantalone — прервано ради безопасности."
        )

    from willem.bootstrap import bootstrap
    from willem.config import ledger_user_id
    from willem.db import sources as sources_db
    from willem.db.connection import connect

    config, _texts = bootstrap()
    ledger_id = ledger_user_id(config, config.owner_telegram_id)

    with connect(config.db_path) as conn:
        existing = sources_db.list_sources(conn, ledger_id, active_only=False)
        existing_credit_sources = [s.name for s in existing if s.type == SOURCE_TYPE]
        already_exists = any(s.name == SOURCE_NAME and s.is_active for s in existing)

        print(f"Существующие источники типа «{SOURCE_TYPE}»: {existing_credit_sources}")
        print(f"Будет добавлено: {'(ничего, уже есть)' if already_exists else SOURCE_NAME!r}")

        if not args.write:
            print()
            print("Сухой прогон — ничего не изменено. Запустите с --write для применения.")
            return

        if not already_exists:
            sources_db.create_source(
                conn,
                ledger_id,
                SOURCE_NAME,
                SOURCE_TYPE,
                SOURCE_CURRENCY,
                kind=SOURCE_KIND,
                owner=SOURCE_OWNER,
            )

        print()
        print("Готово.")


if __name__ == "__main__":
    main(sys.argv[1:])
