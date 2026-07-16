from __future__ import annotations

import logging

from willem.config import Config, load_config
from willem.db.connection import connect, init_db
from willem.db.seed import seed_defaults
from willem.logging_conf import setup_logging

logger = logging.getLogger(__name__)


def bootstrap() -> Config:
    config = load_config()
    setup_logging(config.log_level)
    init_db(config.db_path)
    with connect(config.db_path) as conn:
        seed_defaults(conn, config.owner_telegram_id)
    logger.info("База данных готова: %s", config.db_path)
    return config
