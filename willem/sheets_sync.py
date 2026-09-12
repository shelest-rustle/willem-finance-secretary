from __future__ import annotations

import asyncio

from willem import sheets
from willem.config import Config
from willem.db import categories as categories_db
from willem.db import sources as sources_db
from willem.db import transactions as transactions_db
from willem.db.connection import connect
from willem.db.transactions import Transaction
from willem.texts import Texts


async def sync_after_insert(
    config: Config,
    texts: Texts,
    user_id: int,
    tx: Transaction,
    *,
    source_name: str,
    category_name: str | None = None,
    subcategory_name: str | None = None,
    target_name: str | None = None,
    target_kind: str | None = None,
) -> str:
    """Пытается синхронизировать только что записанную операцию в Sheets.

    Возвращает суффикс для ответа пользователю: пустую строку при успехе (или если
    синк вообще не нужен — по умолчанию только владельцу, если в профиле не включён
    `sync_all_users`) либо текст об ошибке и ночной очереди.
    """
    synced_user_ids = config.allowed_telegram_ids if config.sync_all_users else (config.owner_telegram_id,)
    if user_id not in synced_user_ids:
        return ""

    success = await asyncio.to_thread(
        sheets.append_transaction,
        config,
        tx,
        source_name=source_name,
        category_name=category_name,
        subcategory_name=subcategory_name,
        target_name=target_name,
        target_kind=target_kind,
    )
    if not success:
        return texts.get("common.sync_error_suffix")

    with connect(config.db_path) as conn:
        transactions_db.mark_synced(conn, tx.id)
    return ""


def resync_owner_unsynced(config: Config) -> int:
    """Синхронно досинкивает все неотправленные операции — владельца, либо всех разрешённых
    пользователей, если в профиле включён `sync_all_users`. Возвращает число успешных."""
    synced_user_ids = config.allowed_telegram_ids if config.sync_all_users else (config.owner_telegram_id,)
    synced_count = 0
    with connect(config.db_path) as conn:
        pending = [
            tx for user_id in synced_user_ids for tx in transactions_db.list_unsynced(conn, user_id)
        ]
        for tx in pending:
            source = sources_db.get_source(conn, tx.source_id)
            category = categories_db.get_category(conn, tx.category_id) if tx.category_id else None
            subcategory = (
                categories_db.get_category(conn, tx.subcategory_id) if tx.subcategory_id else None
            )
            target = (
                sources_db.get_source(conn, tx.target_source_id) if tx.target_source_id else None
            )
            success = sheets.append_transaction(
                config,
                tx,
                source_name=source.name,
                category_name=category.name if category else None,
                subcategory_name=subcategory.name if subcategory else None,
                target_name=target.name if target else None,
                target_kind=target.kind if target else None,
            )
            if success:
                transactions_db.mark_synced(conn, tx.id)
                synced_count += 1
    return synced_count


async def resync_unsynced_for_owner(config: Config) -> int:
    """Асинхронная обёртка для ночной джобы — блокирующие вызовы уходят в отдельный поток."""
    return await asyncio.to_thread(resync_owner_unsynced, config)
