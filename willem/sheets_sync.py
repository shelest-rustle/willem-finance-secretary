from __future__ import annotations

import asyncio

from willem import sheets
from willem.config import Config
from willem.db import categories as categories_db
from willem.db import sources as sources_db
from willem.db import transactions as transactions_db
from willem.db.connection import connect
from willem.db.transactions import Transaction

SYNC_ERROR_SUFFIX = (
    " Ошибка при отправке в Google Sheets, добавлено в очередь на отправку ночью."
)


async def sync_after_insert(
    config: Config,
    user_id: int,
    tx: Transaction,
    *,
    source_name: str,
    category_name: str | None = None,
    target_name: str | None = None,
) -> str:
    """Пытается синхронизировать только что записанную операцию в Sheets.

    Возвращает суффикс для ответа пользователю: пустую строку при успехе (или если
    синк вообще не нужен — не-владельцу) либо текст об ошибке и ночной очереди.
    """
    if user_id != config.owner_telegram_id:
        return ""

    success = await asyncio.to_thread(
        sheets.append_transaction,
        config,
        tx,
        source_name=source_name,
        category_name=category_name,
        target_name=target_name,
    )
    if not success:
        return SYNC_ERROR_SUFFIX

    with connect(config.db_path) as conn:
        transactions_db.mark_synced(conn, tx.id)
    return ""


def _resync_owner_unsynced(config: Config) -> int:
    """Синхронно досинкивает все неотправленные операции владельца. Возвращает число успешных."""
    synced_count = 0
    with connect(config.db_path) as conn:
        pending = transactions_db.list_unsynced(conn, config.owner_telegram_id)
        for tx in pending:
            source = sources_db.get_source(conn, tx.source_id)
            category = categories_db.get_category(conn, tx.category_id) if tx.category_id else None
            target = (
                sources_db.get_source(conn, tx.target_source_id) if tx.target_source_id else None
            )
            success = sheets.append_transaction(
                config,
                tx,
                source_name=source.name,
                category_name=category.name if category else None,
                target_name=target.name if target else None,
            )
            if success:
                transactions_db.mark_synced(conn, tx.id)
                synced_count += 1
    return synced_count


async def resync_unsynced_for_owner(config: Config) -> int:
    """Асинхронная обёртка для ночной джобы — блокирующие вызовы уходят в отдельный поток."""
    return await asyncio.to_thread(_resync_owner_unsynced, config)
