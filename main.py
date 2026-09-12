from __future__ import annotations

import asyncio

from willem.bootstrap import bootstrap
from willem.bot.app import run_bot


def main() -> None:
    config, texts = bootstrap()
    asyncio.run(run_bot(config, texts))


if __name__ == "__main__":
    main()
