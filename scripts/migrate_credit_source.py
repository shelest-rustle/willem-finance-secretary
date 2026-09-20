"""Управление источником "Tinkoff REF Кредит Лики" для профиля Pantalone (add/remove).

История: этот источник был добавлен как аналог трёх других "Кредит"-источников
(Т-Кредит Лики, М-Кредит Лики, М-Кредит Ярослава). После ревью решили, что источники
вообще не подходящее место для installment-кредитов — они ведутся отдельными
Google-листами и подкатегорией "Кредиты и долги" (см. MTS_CREDIT_TEST.md), а /balances
теперь их из `sources` не показывает вовсе. Сама подкатегория "Tinkoff REF Кредит Лики"
и её credit_sheets-маппинг в `profiles/pantalone.yaml` — оставлены, кредит реальный.
Убирается только запись в `sources`.

Идемпотентно в обе стороны: --write без источника (add) не создаёт дублей; --remove без
источника ничего не делает. --remove отказывается удалять источник, если на него уже
есть ссылки в transactions (source_id/target_source_id) — в этом случае надо архивировать
вручную (sources_db.archive_source), а не удалять физически.

Использование:
    PROFILE=pantalone poetry run python scripts/migrate_credit_source.py
        — сухой прогон add (по умолчанию): печатает, что будет сделано, ничего не меняет.
    PROFILE=pantalone poetry run python scripts/migrate_credit_source.py --write
        — реально добавляет источник.
    PROFILE=pantalone poetry run python scripts/migrate_credit_source.py --remove
        — сухой прогон удаления: печатает, что будет удалено, ничего не меняет.
    PROFILE=pantalone poetry run python scripts/migrate_credit_source.py --remove --write
        — реально удаляет источник (если на него нет ссылок в transactions).
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


def _run_add(conn, ledger_id: int, write: bool) -> None:
    from willem.db import sources as sources_db

    existing = sources_db.list_sources(conn, ledger_id, active_only=False)
    existing_credit_sources = [s.name for s in existing if s.type == SOURCE_TYPE]
    already_exists = any(s.name == SOURCE_NAME and s.is_active for s in existing)

    print(f"Существующие источники типа «{SOURCE_TYPE}»: {existing_credit_sources}")
    print(f"Будет добавлено: {'(ничего, уже есть)' if already_exists else SOURCE_NAME!r}")

    if not write:
        print()
        print("Сухой прогон — ничего не изменено. Запустите с --write для применения.")
        return

    if not already_exists:
        sources_db.create_source(
            conn, ledger_id, SOURCE_NAME, SOURCE_TYPE, SOURCE_CURRENCY,
            kind=SOURCE_KIND, owner=SOURCE_OWNER,
        )
    print()
    print("Готово.")


def _run_remove(conn, ledger_id: int, write: bool) -> None:
    from willem.db import sources as sources_db

    existing = sources_db.list_sources(conn, ledger_id, active_only=False)
    target = next((s for s in existing if s.name == SOURCE_NAME), None)

    if target is None:
        print(f"Источник {SOURCE_NAME!r} не найден — удалять нечего.")
        return

    tx_count = sources_db.count_transactions_for_source(conn, target.id)
    print(f"Найден источник: id={target.id}, active={target.is_active}, операций на него: {tx_count}")

    if tx_count > 0:
        raise SystemExit(
            f"На источник {SOURCE_NAME!r} уже есть {tx_count} операций(-я) в transactions — "
            f"физическое удаление отменено ради целостности данных. Если источник больше не "
            f"нужен, используйте sources_db.archive_source (мягкое выведение из оборота), "
            f"а не удаление."
        )

    print(f"Будет удалено: {SOURCE_NAME!r} (id={target.id})")
    if not write:
        print()
        print("Сухой прогон — ничего не изменено. Добавьте --write для применения.")
        return

    sources_db.delete_source(conn, target.id)
    print()
    print("Готово.")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write", action="store_true", help="Реально применить изменения (по умолчанию — сухой прогон)"
    )
    parser.add_argument(
        "--remove", action="store_true", help="Удалить источник вместо добавления"
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
    from willem.db.connection import connect

    config, _texts = bootstrap()
    ledger_id = ledger_user_id(config, config.owner_telegram_id)

    with connect(config.db_path) as conn:
        if args.remove:
            _run_remove(conn, ledger_id, args.write)
        else:
            _run_add(conn, ledger_id, args.write)


if __name__ == "__main__":
    main(sys.argv[1:])
