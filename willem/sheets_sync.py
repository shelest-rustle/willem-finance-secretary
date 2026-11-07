from __future__ import annotations

import asyncio
import sqlite3

from willem import sheets
from willem.config import Config
from willem.db import categories as categories_db
from willem.db import sources as sources_db
from willem.db import transactions as transactions_db
from willem.db.connection import connect
from willem.db.transactions import Transaction
from willem.texts import Texts


def synced_user_ids(config: Config) -> tuple[int, ...]:
    """Telegram id, чьи операции пишутся в Google Sheets: по умолчанию только владелец,
    все разрешённые пользователи — если в профиле включён `sync_all_users`."""
    return config.allowed_telegram_ids if config.sync_all_users else (config.owner_telegram_id,)


def _sheet_names(conn: sqlite3.Connection, tx: Transaction) -> dict:
    """Имена источников/категорий операции — то, что `sheets.upsert_transaction` пишет в строку."""
    source = sources_db.get_source(conn, tx.source_id)
    category = categories_db.get_category(conn, tx.category_id) if tx.category_id else None
    subcategory = categories_db.get_category(conn, tx.subcategory_id) if tx.subcategory_id else None
    target = sources_db.get_source(conn, tx.target_source_id) if tx.target_source_id else None
    return {
        "source_name": source.name,
        "category_name": category.name if category else None,
        "subcategory_name": subcategory.name if subcategory else None,
        "target_name": target.name if target else None,
        "target_kind": target.kind if target else None,
    }


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
    if user_id not in synced_user_ids(config):
        return ""

    success = await asyncio.to_thread(
        sheets.upsert_transaction,
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


async def sync_after_update(config: Config, texts: Texts, user_id: int, transaction_id: str) -> str:
    """Приводит строку операции в Sheets к текущему состоянию в БД после правки или
    удаления (перечитывает операцию заново). Суффикс ответа — как у `sync_after_insert`;
    при ошибке операция остаётся `synced = 0`, и её досинкает ночная джоба."""
    if user_id not in synced_user_ids(config):
        return ""

    with connect(config.db_path) as conn:
        tx = transactions_db.get_transaction(conn, transaction_id)
        names = _sheet_names(conn, tx)

    success = await asyncio.to_thread(sheets.upsert_transaction, config, tx, **names)
    if not success:
        return texts.get("common.sync_error_suffix")

    with connect(config.db_path) as conn:
        transactions_db.mark_synced(conn, tx.id)
    return ""


def resync_owner_unsynced(config: Config) -> int:
    """Синхронно досинкивает все неотправленные операции — владельца, либо всех разрешённых
    пользователей, если в профиле включён `sync_all_users`. Возвращает число успешных.
    Неотправленные правки и удаления тоже сюда попадают — upsert перезапишет/удалит строку."""
    synced_count = 0
    with connect(config.db_path) as conn:
        pending = [
            tx
            for user_id in synced_user_ids(config)
            for tx in transactions_db.list_unsynced(conn, user_id)
        ]
        for tx in pending:
            success = sheets.upsert_transaction(config, tx, **_sheet_names(conn, tx))
            if success:
                transactions_db.mark_synced(conn, tx.id)
                synced_count += 1
    return synced_count


async def resync_unsynced_for_owner(config: Config) -> int:
    """Асинхронная обёртка для ночной джобы — блокирующие вызовы уходят в отдельный поток."""
    return await asyncio.to_thread(resync_owner_unsynced, config)
