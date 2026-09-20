"""Одноразовая миграция подкатегорий "Кредиты и долги" для профиля Pantalone.

`profiles/pantalone.yaml::seed_categories` теперь содержит подкатегории "МТС Кредит
Лики", "Tinkoff Кредит Лики", "МТС Кредит Ярослава", "Tinkoff REF Кредит Лики"
(названия совпадают с названиями кредитных листов — так задала Лика в листе
"Справочники") вместо общей "Платёж по кредиту" — так бот может понять, к какому
именно кредитному листу относится платёж (см. MTS_CREDIT_TEST.md). Сидинг
(`willem.db.seed.seed_defaults`) применяет `seed_categories` только на пустой категориях
для пользователя, поэтому для уже засеянного профиля новые подкатегории нужно добавить
этим скриптом.

Идемпотентно: подкатегории, которые уже существуют (активные), повторно не создаются;
"Платёж по кредиту" архивируется, только если ещё активна.

Использование:
    PROFILE=pantalone poetry run python scripts/migrate_credit_categories.py
        — сухой прогон (по умолчанию): печатает, что будет сделано, ничего не меняет.
    PROFILE=pantalone poetry run python scripts/migrate_credit_categories.py --write
        — реально применяет изменения к БД (config.db_path).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

PARENT_CATEGORY_NAME = "Кредиты и долги"
NEW_SUBCATEGORY_NAMES = (
    "МТС Кредит Лики",
    "Tinkoff Кредит Лики",
    "МТС Кредит Ярослава",
    "Tinkoff REF Кредит Лики",
)
RETIRED_SUBCATEGORY_NAME = "Платёж по кредиту"


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
    from willem.db import categories as categories_db
    from willem.db.connection import connect

    config, _texts = bootstrap()
    ledger_id = ledger_user_id(config, config.owner_telegram_id)

    with connect(config.db_path) as conn:
        top_level = categories_db.list_categories(conn, ledger_id)
        parent = next((c for c in top_level if c.name == PARENT_CATEGORY_NAME), None)
        if parent is None:
            raise SystemExit(
                f"Категория «{PARENT_CATEGORY_NAME}» не найдена для пользователя {ledger_id} — "
                f"профиль ещё не засеян, миграция не нужна (сидинг сам создаст новые подкатегории)."
            )

        existing = categories_db.list_categories(conn, ledger_id, active_only=False, parent_id=parent.id)
        existing_by_name = {c.name: c for c in existing}

        to_create = [
            name
            for name in NEW_SUBCATEGORY_NAMES
            if name not in existing_by_name or not existing_by_name[name].is_active
        ]
        retired = existing_by_name.get(RETIRED_SUBCATEGORY_NAME)
        to_archive = retired is not None and retired.is_active

        print(f"Родительская категория «{PARENT_CATEGORY_NAME}»: id={parent.id}")
        print(f"Существующие подкатегории: {[c.name for c in existing if c.is_active]}")
        print()
        print(f"Будет создано: {to_create or '(ничего)'}")
        print(f"Будет архивировано: {[RETIRED_SUBCATEGORY_NAME] if to_archive else '(ничего)'}")

        if not args.write:
            print()
            print("Сухой прогон — ничего не изменено. Запустите с --write для применения.")
            return

        for name in to_create:
            categories_db.create_category(conn, ledger_id, name, parent_id=parent.id)
        if to_archive:
            categories_db.archive_category(conn, retired.id)

        print()
        print("Готово.")


if __name__ == "__main__":
    main(sys.argv[1:])
