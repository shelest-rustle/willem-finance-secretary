from __future__ import annotations

import argparse
import asyncio
import sys

from willem.bootstrap import bootstrap
from willem.credit_sheets import resync_credit_schedules
from willem.sheets_sync import resync_owner_unsynced


def resync(_args: argparse.Namespace) -> None:
    config, _texts = bootstrap()
    synced_count = resync_owner_unsynced(config)
    print(f"Синхронизировано операций: {synced_count}")


def resync_credits(_args: argparse.Namespace) -> None:
    config, _texts = bootstrap()
    if not config.credit_sheets:
        print("В профиле не настроены credit_sheets — нечего синхронизировать.")
        return
    asyncio.run(resync_credit_schedules(config))
    print(f"График платежей обновлён для {len(config.credit_sheets)} кредитов.")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m willem.cli")
    subparsers = parser.add_subparsers(required=True)

    resync_parser = subparsers.add_parser(
        "resync", help="Досинкать неотправленные операции владельца в Google Sheets"
    )
    resync_parser.set_defaults(func=resync)

    resync_credits_parser = subparsers.add_parser(
        "resync_credits", help="Обновить снимок графика платежей по кредитам из Google Sheets"
    )
    resync_credits_parser.set_defaults(func=resync_credits)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main(sys.argv[1:])
