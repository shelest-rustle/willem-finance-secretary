from __future__ import annotations

import argparse
import sys

from willem.bootstrap import bootstrap
from willem.sheets_sync import resync_owner_unsynced


def resync(_args: argparse.Namespace) -> None:
    config, _texts = bootstrap()
    synced_count = resync_owner_unsynced(config)
    print(f"Синхронизировано операций: {synced_count}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m willem.cli")
    subparsers = parser.add_subparsers(required=True)

    resync_parser = subparsers.add_parser(
        "resync", help="Досинкать неотправленные операции владельца в Google Sheets"
    )
    resync_parser.set_defaults(func=resync)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main(sys.argv[1:])
