"""发布管道:文档处理完成 → 自动生成 .kaf 并登记。

设计:
  - 文档上传解析完成后, 若开启发布(enabled), 自动调用 publish_document()
  - 内容来源: 优先用解析产物的结构化 Markdown(标题+正文+表格), 无则用清洗文本
  - 幂等: 同一文档重复触发不会生成重复 .kaf(按 document_id 查找已有记录)
  - 不阻塞主上传流程: 发布失败仅记日志, 不影响文档解析
"""
import logging
import hashlib
from functools import partial
from pathlib import Path
from filelock import AsyncFileLock, Timeout
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession, AsyncConnection
from sqlalchemy.exc import SQLAlchemyError, TimeoutError as SQLAlchemyTimeoutError
from starlette.concurrency import run_in_threadpool

from app.core.kaf_service import get_kaf_service
from app.core import kaf_service
from app.models.kaf import KafFile

logger = logging.getLogger("knowall.kaf.publisher")
MYSQL_PUBLICATION_LOCK_TIMEOUT = 10


def _mysql_publication_lock_name(database: str, source_id: str) -> str:
    # GET_LOCK is server-wide and limits names to 64 characters.
    return 'kaf:' + hashlib.sha256((database + '\0' + source_id).encode()).hexdigest()[:60]


async def _publish_locked(db: AsyncSession, source_id: str, build, connection=None, lock_name=None):
    statement = select(KafFile.file_id).where(KafFile.source_doc_id == source_id).limit(1)
    existing = (await connection.scalar(statement) if connection is not None
                else (await db.execute(statement)).scalar_one_or_none())
    if existing:
        return None
    if connection is not None:
        # The lock holds a separate connection. Reserve the caller's connection
        # before writing any file, so a one-slot pool fails without an orphan.
        try:
            await db.connection()
        except SQLAlchemyTimeoutError:
            logger.warning('KAF 发布连接池容量不足，跳过本次可选发布: %s', source_id)
            return None
    try:
        kf = await run_in_threadpool(build)
    except Exception as exc:
        logger.error("发布 .kaf 失败: source=%s err=%s", source_id, exc)
        return None
    if connection is not None:
        # Building runs in a worker thread. The dedicated lock session may
        # have disappeared during that work; never register after a known
        # lock loss. The object has not yet entered any database transaction.
        try:
            owned = await connection.scalar(select(func.is_used_lock(lock_name) == func.connection_id()))
        except SQLAlchemyError:
            await run_in_threadpool(kaf_service._discard_unregistered,
                                    Path(kf.storage_path), kf.file_id, kf.sha256)
            raise
        if not owned:
            await run_in_threadpool(kaf_service._discard_unregistered,
                                    Path(kf.storage_path), kf.file_id, kf.sha256)
            logger.warning('KAF 数据库来源锁已丢失，跳过登记: %s', source_id)
            return None
    try:
        await kaf_service.register_publication(db, kf)
    except kaf_service.KAFSourceConflictError:
        return None
    logger.info("已发布 .kaf: %s -> %s (%d B)", source_id, kf.file_id, kf.file_size)
    return {"file_id": kf.file_id, "title": kf.title, "size": kf.file_size}


async def _publish_once(db: AsyncSession, source_id: str, build) -> dict | None:
    """Serialize by shared directory and, on MySQL, by database source identity."""
    lock_dir = kaf_service.KAF_DIR / ".publish-locks"
    try:
        await run_in_threadpool(lock_dir.mkdir, parents=True, exist_ok=True)
        lock = AsyncFileLock(lock_dir / (hashlib.sha256(source_id.encode()).hexdigest() + ".lock"),
                             timeout=10)
        await lock.acquire()
    except (OSError, Timeout) as exc:
        logger.error(".kaf 发布锁不可用: source=%s err=%s", source_id, exc)
        return None

    try:
        # The check must happen after acquisition and the lock lasts through
        # commit, so a second worker sees the first worker's persisted result.
        if db.bind.dialect.name == 'mysql':
            engine = db.bind.engine if isinstance(db.bind, AsyncConnection) else db.bind
            try:
                async with engine.connect() as connection:
                    try:
                        database = await connection.scalar(select(func.database()))
                        name = _mysql_publication_lock_name(database, source_id)
                        acquired = await connection.scalar(select(func.get_lock(name, MYSQL_PUBLICATION_LOCK_TIMEOUT)))
                        if acquired != 1:
                            logger.warning('KAF 数据库来源锁未取得，跳过本次可选发布: %s', source_id)
                            return None
                        # A fresh connection sees the latest committed rows even
                        # when the caller has an old REPEATABLE READ snapshot.
                        return await _publish_locked(db, source_id, build, connection, name)
                    finally:
                        # COMMIT/ROLLBACK and pool close do not release GET_LOCK.
                        # Native invalidation terminates this dedicated session,
                        # including cancellation or an uncertain lock response.
                        # Never return a possibly locked session to the pool.
                        await connection.invalidate()
            except SQLAlchemyTimeoutError:
                logger.warning('KAF 来源确认连接池繁忙，跳过本次可选发布: %s', source_id)
                return None
        return await _publish_locked(db, source_id, build)
    finally:
        await lock.release()


def build_markdown_from_parse(text: str, headings: list, tables: list | None = None) -> str:
    """把解析产物组装成结构化 Markdown(标题层级 + 正文)。

    headings 来自 docling HeadingNode; 无标题时直接返回清洗全文。
    """
    md_parts = []
    if headings:
        for h in headings:
            level = min(int(getattr(h, "level", 1) or 1), 6)
            title = (getattr(h, "title", "") or "").strip()
            if title:
                md_parts.append(f"{'#' * level} {title}")
        # 大纲 + 全文(正文在标题之后, 语义上更接近原文档)
        if text.strip():
            md_parts.append(text.strip())
    else:
        md_parts.append(text.strip())
    return "\n\n".join(md_parts).strip()


async def publish_document(
    db: AsyncSession,
    document_id: str,
    filename: str,
    topic: str = "",
    title: str = "",
    markdown: str = "",
    glossary_refs: list | None = None,
    enabled_tiers: str = "free,pro,enterprise",
) -> dict | None:
    """把一个文档发布成 .kaf(幂等)。返回 {file_id, title, size} 或 None(已存在)。"""
    service = get_kaf_service()

    md_text = (markdown or "").strip()
    if not md_text:
        logger.warning("publish_document: 空内容, 跳过 %s", document_id)
        return None

    return await _publish_once(db, document_id, partial(service.publish_from_markdown,
            md_text,
            topic=topic or "未分类",
            title=title or filename or "未命名资料",
            source_doc_id=document_id,
            source_doc_filename=filename,
            glossary_refs=glossary_refs,
            enabled_tiers=enabled_tiers,
    ))


async def publish_summary_package(
    db: AsyncSession,
    summary_id: str,
    title: str,
    topic: str,
    chapters: list,
    glossary_refs: list | None = None,
    enabled_tiers: str = "free,pro,enterprise",
) -> dict | None:
    """把「知识总纲」(结构化章节)发布成 .kaf。chapters: [{title, body}, ...]"""
    service = get_kaf_service()

    package = {
        "type": "course_material",
        "schema_version": 1,
        "title": title,
        "subject": topic,
        "chapters": [
            {
                "id": f"ch-{i + 1:03d}",
                "title": ch.get("title", "未命名章节"),
                "body": ch.get("body", ""),
                "nodes": [{
                    "id": f"node-{i + 1:04d}",
                    "kind": "markdown",
                    "title": ch.get("title", "未命名章节"),
                    "content_md": ch.get("body", ""),
                }],
            }
            for i, ch in enumerate(chapters)
        ],
        "glossary_refs": glossary_refs or [],
        "meta": {"source_summary_id": summary_id, "schema_version": 1},
    }
    meta = {
        "title": title,
        "topic": topic,
        "source_doc_id": f"summary:{summary_id}",
        "source_doc_filename": f"{title}.md",
        "node_count": len(chapters),
        "enabled_tiers": enabled_tiers,
    }
    return await _publish_once(db, meta["source_doc_id"],
                               partial(service.publish_from_package, package, meta))
