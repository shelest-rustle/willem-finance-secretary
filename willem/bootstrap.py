from __future__ import annotations

import logging

from willem.config import Config, load_config, profile_yaml_path
from willem.db.connection import connect, init_db
from willem.db.seed import seed_defaults
from willem.logging_conf import setup_logging
from willem.texts import Texts, load_texts

logger = logging.getLogger(__name__)


def bootstrap() -> tuple[Config, Texts]:
    config = load_config()
    setup_logging(config.log_level)
    init_db(config.db_path)
    seed_user_ids = (
        config.allowed_telegram_ids if config.seed_all_users else (config.owner_telegram_id,)
    )
    with connect(config.db_path) as conn:
        for user_id in seed_user_ids:
            seed_defaults(conn, user_id, config.seed_sources, config.seed_categories)
    texts = load_texts(profile_yaml_path(config.profile_name))
    logger.info("База данных готова: %s (профиль: %s)", config.db_path, config.profile_name)
    return config, texts
