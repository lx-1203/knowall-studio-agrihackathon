"""Native SQL transactions for durable Anki timestamps, bounded by real time."""
import asyncio
from contextlib import asynccontextmanager
import sqlite3
import time

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession
from app.models import Flashcard
from app.core.card_access import visible_cards
from app.core.anki_export import EXPORT_FORMAT_VERSION

_clock = time
_sleep = asyncio.sleep


@asynccontextmanager
async def anki_export_session(db):
    """Own SQLite's native connection until commit settings have been restored."""
    if db.get_bind().dialect.name != 'sqlite':
        yield db
        return
    bind = db.bind
    engine = bind.engine if isinstance(bind, AsyncConnection) else bind
    # The route has copied its trusted scalar IDs. Release its read connection
    # before checkout, including when the engine has a single-connection pool.
    await db.rollback()
    async with engine.connect() as connection:
        async with AsyncSession(bind=connection, expire_on_commit=False,
                                join_transaction_mode='control_fully') as session:
            yield session


async def commit_anki_export(db):
    """A busy commit rolls back and becomes retryable at the HTTP boundary."""
    if db.get_bind().dialect.name != 'sqlite':
        await db.commit()
        return
    connection = await db.connection()
    original = (await connection.exec_driver_sql('PRAGMA busy_timeout')).scalar_one()
    try:
        await connection.exec_driver_sql(f'PRAGMA busy_timeout={min(original, 250)}')
        await db.commit()
    except BaseException:
        await db.rollback()
        raise
    finally:
        # This connection belongs to the outer context, not Session's pool
        # checkout. Commit/rollback cannot release it before restoration.
        if not connection.invalidated:
            await connection.exec_driver_sql(f'PRAGMA busy_timeout={original}')


async def _execute_with_budget(db, statement, deadline):
    """Use the same native connection for a short SQLite wait and its restore.
    Restore before caller rollback can return this connection to the pool.
    Other database drivers keep their existing locking behavior.
    """
    if db.get_bind().dialect.name != 'sqlite':
        return await db.execute(statement)
    connection = await db.connection()
    original = (await connection.exec_driver_sql('PRAGMA busy_timeout')).scalar_one()
    remaining_ms = max(0, int((deadline - _clock.monotonic()) * 1000))
    timeout_ms = min(original, 100, remaining_ms)
    try:
        await connection.exec_driver_sql(f'PRAGMA busy_timeout={timeout_ms}')
        return await db.execute(statement)
    finally:
        # Native SQLAlchemy invalidates a connection cancelled during driver
        # I/O. It cannot return to the pool; restoring it would mask cancellation
        # with PendingRollbackError. Usable connections must restore before release.
        if not connection.invalidated:
            await connection.exec_driver_sql(f'PRAGMA busy_timeout={original}')


async def prepare_anki_export(db, deck_id, user_id):
    """Leave writes uncommitted until the caller's encoder succeeds.

    SQLite uses conditional updates; MySQL additionally uses native row locks.
    Waiting releases the transaction so edits can proceed. No synthetic future
    timestamp, process cache or HTTP write retry is needed.
    """
    deadline = _clock.monotonic() + 2
    # First export must also supersede pre-migration packages made this second.
    bootstrap_second = int(_clock.time()) + 1
    detail = '导出服务暂时繁忙，请稍后重试'
    while _clock.monotonic() < deadline:
        try:
            cards = (await _execute_with_budget(db, select(Flashcard).where(
                Flashcard.deck_id == deck_id, visible_cards(user_id)
            ).order_by(Flashcard.id).with_for_update().execution_options(
                populate_existing=True), deadline)).scalars().all()
            now = _clock.time()
            waiting = [c.anki_export_mtime for c in cards if
                       c.anki_export_mtime is not None and
                       (c.anki_export_mtime > int(now) or
                        ((c.anki_export_version != c.content_version or
                          c.anki_export_format != EXPORT_FORMAT_VERSION) and
                         c.anki_export_mtime >= int(now)))]
            delay = max(waiting) + 1 - now if waiting else 0
            if any(c.anki_export_mtime is None for c in cards):
                delay = max(delay, bootstrap_second - now)
            if delay > 0:
                detail = '等待导出版本时间同步，请稍后重试'
                await db.rollback()
                remaining = deadline - _clock.monotonic()
                if delay > remaining:
                    break
                await _sleep(max(delay, 0.001))
                continue
            payload = []
            for card in cards:
                modified = card.anki_export_mtime
                if (card.anki_export_version != card.content_version or modified is None or
                        card.anki_export_format != EXPORT_FORMAT_VERSION):
                    modified = int(now)
                    result = await _execute_with_budget(db, update(Flashcard).where(
                        Flashcard.id == card.id,
                        Flashcard.content_version == card.content_version,
                        Flashcard.anki_export_version == card.anki_export_version,
                        Flashcard.anki_export_mtime == card.anki_export_mtime,
                        Flashcard.anki_export_format == card.anki_export_format,
                        visible_cards(user_id),
                    ).values(anki_export_version=card.content_version,
                             anki_export_mtime=modified,
                             anki_export_format=EXPORT_FORMAT_VERSION).execution_options(synchronize_session=False), deadline)
                    if result.rowcount != 1:
                        detail = '导出服务暂时繁忙，请稍后重试'
                        await db.rollback()
                        break
                payload.append({'id': card.id, 'front': card.front, 'back': card.back,
                                'card_type': card.card_type, 'tags': card.tags or [],
                                'anki_export_mtime': modified})
            else:
                return payload
        except OperationalError as error:
            detail = '导出服务暂时繁忙，请稍后重试'
            await db.rollback()
            # Retry only the native SQLite busy/snapshot race, not arbitrary DB errors.
            code = getattr(error.orig, 'sqlite_errorcode', None)
            if code is None or code & 255 != sqlite3.SQLITE_BUSY:
                raise
        await _sleep(0.02)
    await db.rollback()
    raise HTTPException(503, detail,
                        headers={'Retry-After': '1'})
