"""Document upload and parsing API routes"""
import asyncio
import os
import hashlib
import logging
import tempfile
import traceback
from typing import Literal
from weakref import WeakValueDictionary
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, BackgroundTasks
from fastapi.responses import FileResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from pydantic import BaseModel, ConfigDict, Field, field_validator
from app.database import get_db
from app.models import Document, DocumentChunk
from app.core.parsing import parser, cleaner, splitter
from app.core.parsing.docreader.ssrf import SSRFBlockedError, assert_url_allowed
from app.core.parsing.page_map import build_page_map
from app.core.parsing.outline_extractor import outline_extractor
from app.core.auth import get_current_user, get_optional_user, get_user_id, load_user_api_keys
from app.config import settings

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])

logger = logging.getLogger("knowall.documents")


def _authenticated_id(user):
    value = getattr(user, 'id', None)
    return value if isinstance(value, str) and value else None


def _document_access(doc, user):
    owner = (doc.metadata_ or {}).get('knowledge_owner_id') if doc else None
    if not doc or (owner and owner != _authenticated_id(user)):
        raise HTTPException(404, 'Document not found')


async def _with_extraction(response, db, user, background):
    owner = _authenticated_id(user)
    if owner and background is not None:
        from app.core.knowledge_network import register_extraction, enqueue_extraction, job_response
        job = await register_extraction(db, response['document_id'], owner)
        enqueue_extraction(background, db, job)
        response['knowledge_extraction'] = job_response(job)
    return response



class MaterialOrganizationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    subject: str = Field(default='', max_length=80)
    material_kind: Literal['knowledge', 'question_bank', 'mixed'] = 'knowledge'

    @field_validator('subject')
    @classmethod
    def trim_subject(cls, value):
        return value.strip()


def _organization(doc):
    meta = doc.metadata_ or {}
    kind = meta.get('material_kind', 'knowledge')
    subject = meta.get('subject', '')
    return {'subject': subject if isinstance(subject, str) else '',
            'material_kind': kind if kind in ('knowledge', 'question_bank', 'mixed') else 'knowledge'}


@router.patch('/{doc_id}/organization')
async def organize_material(doc_id: str, req: MaterialOrganizationRequest,
                            db: AsyncSession = Depends(get_db), current_user=Depends(get_current_user)):
    """Classify a workspace document without replacing its parsed source metadata."""
    doc = await db.get(Document, doc_id)
    _document_access(doc, current_user)
    doc.metadata_ = {**(doc.metadata_ or {}), **req.model_dump()}
    await db.commit()
    return {'id': doc.id, **_organization(doc)}

# Coalesce uploads of identical bytes within one worker. Weak values discard
# idle locks, and the event-loop key keeps locks bound to their owning loop.
_upload_locks = WeakValueDictionary()


def _write_upload_atomically(file_dir: str, local_path: str, content: bytes, digest: str) -> None:
    """Publish complete bytes only; other workers may parse this path now."""
    os.makedirs(file_dir, exist_ok=True)

    def matches_current_file() -> bool:
        try:
            if os.path.getsize(local_path) != len(content):
                return False
            with open(local_path, "rb") as source:
                return hashlib.file_digest(source, "sha256").hexdigest() == digest
        except OSError:
            return False

    if matches_current_file():
        return

    fd, staged_path = tempfile.mkstemp(prefix=".upload-", suffix=".tmp", dir=file_dir)
    try:
        with os.fdopen(fd, "wb") as staged:
            staged.write(content)
        try:
            os.replace(staged_path, local_path)
        except OSError:
            # Windows may refuse a replacement while another worker has this
            # identical file open for parsing. The complete file is sufficient.
            if not matches_current_file():
                raise
    finally:
        try:
            os.unlink(staged_path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("Could not remove staged upload %s: %s", staged_path, exc)


async def _existing_upload_response(db: AsyncSession, doc: Document) -> dict:
    chunks_result = await db.execute(
        select(DocumentChunk)
        .where(DocumentChunk.doc_id == doc.id)
        .order_by(DocumentChunk.chunk_index)
    )
    existing_chunks = chunks_result.scalars().all()
    meta = doc.metadata_ or {}
    return {
        "document_id": doc.id,
        "filename": doc.filename,
        "file_type": doc.file_type,
        "status": doc.status,
        "page_count": doc.page_count,
        "native_outline": meta.get("native_outline_md"),
        "image_count": meta.get("image_count", 0),
        "table_count": meta.get("table_count", 0),
        "chunks": [
            {
                "id": c.id,
                "index": c.chunk_index,
                "token_count": c.token_count,
                "page_range": c.page_range,
                "preview": c.text_content[:200] + "..." if len(c.text_content) > 200 else c.text_content,
            }
            for c in existing_chunks
        ],
        "total_chunks": len(existing_chunks),
        "duplicate": True,
    }

# MIME type mapping for raw file serving
MIME_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xls": "application/vnd.ms-excel",
    "csv": "text/csv",
    "md": "text/markdown",
    "markdown": "text/markdown",
    "txt": "text/plain",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "bmp": "image/bmp",
    "webp": "image/webp",
    "html": "text/html",
    "epub": "application/epub+zip",
    "mhtml": "multipart/related",
    "mht": "multipart/related",
    "json": "application/json",
    "xml": "application/xml",
    "yaml": "text/yaml",
    "yml": "text/yaml",
}


@router.post("/upload")
async def upload_document(file: UploadFile = File(...), db: AsyncSession = Depends(get_db),
                          background_tasks: BackgroundTasks = None, current_user=Depends(get_optional_user)):
    """Upload and parse a document. Returns document ID and chunks."""
    # Validate file
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    supported = {"pdf", "docx", "pptx", "xlsx", "csv", "md", "markdown", "txt",
                 "png", "jpg", "jpeg", "gif", "bmp", "webp",
                 "xmind", "epub", "mhtml", "mht", "html",
                 "py", "js", "ts", "jsx", "tsx", "java", "cpp", "c",
                 "go", "rs", "sql", "yaml", "yml", "json", "xml", "css"}
    if ext not in supported:
        raise HTTPException(400, f"Unsupported file type: .{ext}. Supported: {supported}")

    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    if file.size is not None and file.size > max_bytes:
        raise HTTPException(400, f"File too large. Max {settings.max_upload_size_mb}MB")

    # The reported size can be absent or inaccurate. Bound the actual read as
    # well, before allocating or saving an oversized upload.
    content = await file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(400, f"File too large. Max {settings.max_upload_size_mb}MB")

    # Older URL imports used the plain content-hash namespace. Check both file
    # keys so an upload stays deduplicated even after that URL row is deleted.
    raw_sha256 = hashlib.sha256(content).hexdigest()
    owner_id = _authenticated_id(current_user)
    storage_key = hashlib.sha256((owner_id + '\0' + raw_sha256).encode()).hexdigest() if owner_id else raw_sha256
    lock_key = (asyncio.get_running_loop(), storage_key)
    lock = _upload_locks.get(lock_key)
    if lock is None:
        lock = asyncio.Lock()
        _upload_locks[lock_key] = lock
    async with lock:
        response = await _store_uploaded_document(file, db, content, ext, raw_sha256, owner_id)
    return await _with_extraction(response, db, current_user, background_tasks)


async def _store_uploaded_document(
    file: UploadFile, db: AsyncSession, content: bytes, ext: str, raw_sha256: str, owner_id=None
):
    """Keep the duplicate check and save in the same per-file critical section."""
    dedup_sha256 = hashlib.sha256((owner_id + '\0' + raw_sha256).encode()).hexdigest() if owner_id else raw_sha256
    fallback_sha256 = "f" + dedup_sha256[:63]
    dup = await db.execute(select(Document).where(
        Document.sha256.in_((dedup_sha256, fallback_sha256))
    ))
    matches = dup.scalars().all()
    dup_doc = next((d for d in matches if d.file_type != "url"), None)
    legacy_url_conflict = any(
        d.sha256 == dedup_sha256 and d.file_type == "url" for d in matches
    )
    sha256 = dup_doc.sha256 if dup_doc else (
        fallback_sha256 if legacy_url_conflict else dedup_sha256
    )
    file_dir = os.path.join(settings.document_dir, sha256[:2])
    local_path = os.path.join(file_dir, sha256)
    await asyncio.to_thread(
        _write_upload_atomically, file_dir, local_path, content, raw_sha256
    )

    if dup_doc:
        # Same file already exists — return it directly
        return await _existing_upload_response(db, dup_doc)

    # Parse document
    try:
        parsed = await parser.parse(local_path, ext)
    except ValueError as e:
        logger.warning("文档解析不支持: file=%s ext=%s error=%s", file.filename, ext, e)
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.error("文档解析失败: file=%s ext=%s error=%s\n%s",
                     file.filename, ext, e, traceback.format_exc())
        raise HTTPException(500, f"Failed to parse document: {str(e)}")

    # Clean text
    try:
        cleaned_text = cleaner.clean(parsed.text)
    except Exception as e:
        logger.error("文本清洗失败: file=%s error=%s", file.filename, e)
        raise HTTPException(500, f"Failed to clean document text: {str(e)}")

    # Offset→page index. Must be built on cleaned_text: chunk offsets are computed
    # against the cleaned text, so anchoring against the raw parse would shift every page.
    if not cleaned_text.strip():
        raise HTTPException(400, "未识别到可读取的文字，请检查文件内容；扫描件或图片请确认文字清晰后重新上传。")
    page_map = build_page_map(cleaned_text, parsed.page_items)

    # Split into chunks
    try:
        chunks = splitter.split(cleaned_text, parsed.page_count, page_map=page_map)
    except Exception as e:
        logger.error("文本分块失败: file=%s error=%s", file.filename, e)
        raise HTTPException(500, f"Failed to split document into chunks: {str(e)}")

    # Save to database
    if not chunks:
        raise HTTPException(400, "未生成可用的文本分片，请检查资料内容后重新上传。")
    try:
        # Enhance metadata with structured document info if available
        doc_metadata = dict(parsed.metadata)
        if owner_id:
            doc_metadata['knowledge_owner_id'] = owner_id
        native_outline = None

        if parsed.headings:
            outline_result = outline_extractor.extract(parsed.headings)
            doc_metadata["headings"] = [h.to_dict() for h in parsed.headings]
            doc_metadata["native_outline_md"] = outline_result.markdown
            doc_metadata["image_count"] = len(parsed.images)
            doc_metadata["table_count"] = len(parsed.tables)
            native_outline = outline_result.markdown

        doc = Document(
            filename=file.filename,
            file_type=ext,
            file_size=len(content),
            sha256=sha256,
            local_path=local_path,
            status="ready",
            page_count=parsed.page_count,
            metadata_=doc_metadata,
        )
        db.add(doc)
        try:
            await db.flush()  # ensure doc.id is generated before creating chunks
        except IntegrityError:
            # Another worker can win after our initial SELECT. A failed flush
            # invalidates this transaction, so roll back before re-reading.
            await db.rollback()
            concurrent = (await db.execute(
                select(Document).where(
                    Document.sha256 == sha256, Document.file_type != "url"
                )
            )).scalar_one_or_none()
            if concurrent:
                return await _existing_upload_response(db, concurrent)
            raise

        orm_chunks = []
        for chunk in chunks:
            orm_chunk = DocumentChunk(
                doc_id=doc.id,
                chunk_index=chunk.index,
                content_hash=chunk.content_hash,
                text_content=chunk.text,
                token_count=chunk.token_count,
                page_range=chunk.page_range,
            )
            db.add(orm_chunk)
            orm_chunks.append(orm_chunk)

        await db.flush()  # generate IDs for ORM objects

        await db.commit()

        # 发布管道: 文档解析完成 → 自动生成 .kaf(不阻塞主流程, 失败仅记日志)
        if settings.kaf_publish_enabled and not owner_id:
            from app.core.kaf_publisher import build_markdown_from_parse, publish_document

            md_text = build_markdown_from_parse(cleaned_text, parsed.headings, parsed.tables)
            try:
                await publish_document(
                    db,
                    document_id=doc.id,
                    filename=file.filename,
                    topic=doc_metadata.get("subject", ""),
                    title=file.filename.rsplit(".", 1)[0],
                    markdown=md_text,
                    enabled_tiers=settings.kaf_publish_enabled_tiers,
                )
            except Exception as e:
                logger.warning("自动发布 .kaf 失败(不影响上传): %s", e)

        # 词库自动触发: 文档落库 → 提取文档标题术语入名词解释库(不阻塞主流程, 失败仅记日志)
        try:
            from app.core.glossary.extractor import sync_glossary_from_db
            subject = doc_metadata.get("subject", "") or "综合"

            # Markdown 文档若未提取到 headings（docling 对 md 支持有限），从 cleaned_text 解析 # 标题
            if ext in ("md", "markdown", "txt") and not doc_metadata.get("headings"):
                import re as _re
                parsed_headings = []
                for _line in cleaned_text.split("\n"):
                    _m = _re.match(r"^(#{1,3})\s+(.+?)\s*$", _line)
                    if _m:
                        parsed_headings.append({"title": _m.group(2).strip(), "level": len(_m.group(1))})
                if parsed_headings:
                    doc_metadata["headings"] = parsed_headings
                    # 同步回 DB（metadata JSON 列）
                    doc.metadata_ = doc_metadata
                    await db.commit()

            if not owner_id:
                gsyn = await sync_glossary_from_db(
                    db, subject=subject, document_id=doc.id,
                )
                logger.info("词库自动同步(上传文档): %s", gsyn)
        except Exception as e:
            logger.warning("词库自动同步失败(不影响上传): %s", e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("文档保存失败: file=%s error=%s\n%s",
                     file.filename, e, traceback.format_exc())
        await db.rollback()
        raise HTTPException(500, f"Failed to save document: {str(e)}")

    return {
        "document_id": doc.id,
        "filename": file.filename,
        "file_type": ext,
        "status": "ready",
        "page_count": parsed.page_count,
        "native_outline": native_outline,
        "image_count": len(parsed.images),
        "table_count": len(parsed.tables),
        "chunks": [
            {
                "id": c.id,
                "index": c.chunk_index,
                "token_count": c.token_count,
                "page_range": c.page_range,
                "preview": c.text_content[:200] + "..." if len(c.text_content) > 200 else c.text_content,
            }
            for c in orm_chunks
        ],
        "total_chunks": len(orm_chunks),
    }




class URLImportRequest(BaseModel):
    url: str


def _validate_url_safe(url: str) -> None:
    """Validate URL is safe to fetch (SSRF prevention).

    判定交给 docreader 移植过来的 SSRF 模块：它会把域名解析成 IP 再逐个校验，
    因此十进制/十六进制 IP 变体、云元数据地址、内网保留网段与高危端口都挡得住，
    而原来的"主机名黑名单 + 禁止 .local"对 http://2130706433/ 这类写法完全无效。
    """
    try:
        assert_url_allowed(url)
    except SSRFBlockedError as exc:
        message = {
            "scheme": "仅支持 HTTP/HTTPS URL",
            "no_hostname": "无效的URL，无法解析主机名",
            "invalid_url": "无效的URL格式",
            "dns": "无法解析该URL的主机名",
        }.get(exc.code, "不允许访问内部网络地址")
        raise HTTPException(400, message) from exc


async def _existing_url_response(db: AsyncSession, existing: Document) -> dict:
    saved_chunks = (await db.execute(
        select(DocumentChunk)
        .where(DocumentChunk.doc_id == existing.id)
        .order_by(DocumentChunk.chunk_index)
    )).scalars().all()
    return {
        "document_id": existing.id,
        "filename": existing.filename,
        "file_type": existing.file_type,
        "status": existing.status,
        "total_chunks": len(saved_chunks),
        "chunks": [
            {"index": c.chunk_index, "preview": c.text_content[:200]}
            for c in saved_chunks
        ],
        "duplicate": True,
    }


@router.post("/import-url")
async def import_from_url(req: URLImportRequest, db: AsyncSession = Depends(get_db),
                          background_tasks: BackgroundTasks = None, current_user=Depends(get_optional_user)):
    """Import content from a URL (web page)."""
    # DNS resolution inside the guard is blocking; keep the async API responsive
    # while it checks an untrusted hostname.
    await asyncio.to_thread(_validate_url_safe, req.url)

    # Keep URL keys outside the hexadecimal SHA-256 namespace used by uploads.
    # Also find URL documents imported before the namespace was introduced.
    owner_id = _authenticated_id(current_user)
    namespace_url = owner_id + '\0' + req.url if owner_id else req.url
    legacy_sha256 = hashlib.sha256(namespace_url.encode()).hexdigest()
    sha256 = "u" + legacy_sha256[:63]
    existing = (await db.execute(
        select(Document).where(
            Document.file_type == "url",
            Document.sha256.in_((sha256, legacy_sha256)),
        ).limit(1)
    )).scalar_one_or_none()
    if existing:
        return await _with_extraction(await _existing_url_response(db, existing), db, current_user, background_tasks)

    try:
        parsed = await parser.parse(req.url, "url")
    except SSRFBlockedError as exc:
        raise HTTPException(400, "不允许访问内部网络地址") from exc
    except Exception as e:
        raise HTTPException(500, f"Failed to fetch URL: {str(e)}")

    cleaned_text = cleaner.clean(parsed.text)
    if not cleaned_text.strip():
        raise HTTPException(400, '未识别到可读取的文字')
    chunks = splitter.split(cleaned_text, 1)
    if not chunks:
        raise HTTPException(400, '未生成可用的文本分片')

    file_dir = os.path.join(settings.document_dir, sha256[:2])
    local_path = os.path.join(file_dir, sha256)

    doc = Document(
        filename=req.url[:80],
        file_type="url",
        file_size=len(cleaned_text.encode()),
        sha256=sha256,
        local_path=local_path,
        status="ready",
        page_count=1,
        metadata_={"source_url": req.url, **({"knowledge_owner_id": owner_id} if owner_id else {})},
    )
    db.add(doc)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        existing = (await db.execute(
            select(Document).where(Document.sha256 == sha256, Document.file_type == "url")
        )).scalar_one_or_none()
        if existing:
            return await _with_extraction(await _existing_url_response(db, existing), db, current_user, background_tasks)
        raise

    try:
        for chunk in chunks:
            db.add(DocumentChunk(
                doc_id=doc.id, chunk_index=chunk.index,
                content_hash=chunk.content_hash, text_content=chunk.text,
                token_count=chunk.token_count, page_range=chunk.page_range,
            ))
        await db.flush()
        os.makedirs(file_dir, exist_ok=True)
        with open(local_path, "w", encoding="utf-8") as f:
            f.write(f"URL: {req.url}\n\n{cleaned_text}")
        await db.commit()
    except Exception:
        await db.rollback()
        # Another request can publish the same URL into this shared hash path
        # during rollback. Deleting it here could remove that winner's source.
        raise

    # 词库自动触发: URL 文档落库 → 提取标题术语入名词解释库(失败仅记日志)
    try:
        from app.core.glossary.extractor import sync_glossary_from_db
        if not owner_id:
            gsyn = await sync_glossary_from_db(
                db, subject="综合", document_id=doc.id,
            )
            logger.info("词库自动同步(URL导入): %s", gsyn)
    except Exception as e:
        logger.warning("词库自动同步失败(不影响URL导入): %s", e)

    response = {"document_id": doc.id, "filename": req.url[:80], "file_type": "url",
            "status": "ready", "total_chunks": len(chunks),
            "chunks": [{"index": c.index, "preview": c.text[:200]} for c in chunks]}
    return await _with_extraction(response, db, current_user, background_tasks)


@router.get("/")
async def list_documents(
    limit: int = 1000,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_optional_user),
):
    """List all uploaded documents."""
    result = await db.execute(
        select(Document).order_by(Document.created_at.desc())
    )
    docs = [doc for doc in result.scalars().all()
            if not (doc.metadata_ or {}).get('knowledge_owner_id') or (doc.metadata_ or {}).get('knowledge_owner_id') == _authenticated_id(current_user)]
    docs = docs[offset:offset + limit]
    return [
        {
            "id": d.id,
            "filename": d.filename,
            "file_type": d.file_type,
            "status": d.status,
            "page_count": d.page_count,
            "created_at": d.created_at.isoformat(),
            **_organization(d),
        }
        for d in docs
    ]


@router.get("/{doc_id}/chunks")
async def get_document_chunks(doc_id: str, db: AsyncSession = Depends(get_db), current_user=Depends(get_optional_user)):
    """Get all chunks for a document."""
    # Verify document exists
    result = await db.execute(select(Document).where(Document.id == doc_id))
    doc = result.scalar_one_or_none()
    _document_access(doc, current_user)
    result = await db.execute(
        select(DocumentChunk)
        .where(DocumentChunk.doc_id == doc_id)
        .order_by(DocumentChunk.chunk_index)
    )
    chunks = result.scalars().all()
    return [
        {
            "id": c.id,
            "index": c.chunk_index,
            "text": c.text_content,
            "token_count": c.token_count,
            "page_range": c.page_range,
        }
        for c in chunks
    ]


@router.delete("/{doc_id}")
async def delete_document(doc_id: str, db: AsyncSession = Depends(get_db), current_user=Depends(get_optional_user)):
    """Delete a document and its chunks."""
    result = await db.execute(select(Document).where(Document.id == doc_id))
    doc = result.scalar_one_or_none()
    _document_access(doc, current_user)

    # Delete local file (best-effort, don't fail the whole operation)
    if doc.local_path:
        try:
            if os.path.exists(doc.local_path):
                os.remove(doc.local_path)
        except Exception as e:
            logger.warning("Failed to delete local file %s: %s", doc.local_path, e)

    # Delete cached slide images if any
    slide_cache_dir = os.path.join(settings.export_dir, doc_id)
    if os.path.exists(slide_cache_dir):
        try:
            import shutil
            shutil.rmtree(slide_cache_dir)
        except Exception as e:
            logger.warning("Failed to delete slide cache %s: %s", slide_cache_dir, e)

    from app.core.knowledge_network import remove_document_knowledge
    await remove_document_knowledge(db, doc.id, (doc.metadata_ or {}).get('knowledge_owner_id'))
    await db.delete(doc)
    await db.commit()

    # Clean up ChromaDB vectors (best-effort after DB commit)
    try:
        from app.core.rag import delete_vectors_by_doc_id
        await delete_vectors_by_doc_id(doc_id)
    except Exception as e:
        logger.warning("Failed to delete vectors for doc %s: %s", doc_id, e)

    return {"status": "deleted"}


@router.get("/{doc_id}/raw")
async def get_document_raw(doc_id: str, db: AsyncSession = Depends(get_db), current_user=Depends(get_optional_user)):
    """Serve the original file for client-side preview rendering."""
    result = await db.execute(select(Document).where(Document.id == doc_id))
    doc = result.scalar_one_or_none()
    _document_access(doc, current_user)
    if not doc.local_path or not os.path.exists(doc.local_path):
        return Response(status_code=204)  # document exists but raw file unavailable — not an error
    media_type = MIME_TYPES.get(doc.file_type, "application/octet-stream")
    return FileResponse(
        doc.local_path,
        media_type=media_type,
        filename=doc.filename.encode("ascii", "ignore").decode(),
    )


@router.get("/{doc_id}/slides")
async def get_pptx_slides(doc_id: str, db: AsyncSession = Depends(get_db), current_user=Depends(get_optional_user)):
    """Return slide metadata (count and image URLs) for a PPTX document."""
    result = await db.execute(select(Document).where(Document.id == doc_id))
    doc = result.scalar_one_or_none()
    _document_access(doc, current_user)
    if doc.file_type not in ("pptx",):
        raise HTTPException(400, "幻灯片预览仅支持 PPTX 格式")
    if not doc.local_path or not os.path.exists(doc.local_path):
        raise HTTPException(404, "原始文件不可用")

    from app.core.parsing.pptx_renderer import pptx_renderer
    slide_count = pptx_renderer.get_slide_count(doc.local_path)
    return {
        "document_id": doc_id,
        "slide_count": slide_count,
        "slides": [
            {
                "index": i,
                "image_url": f"/api/v1/documents/{doc_id}/slides/{i}/image",
            }
            for i in range(slide_count)
        ],
    }


@router.get("/{doc_id}/slides/{slide_index}/image")
async def get_pptx_slide_image(doc_id: str, slide_index: int, db: AsyncSession = Depends(get_db), current_user=Depends(get_optional_user)):
    """Return a single PPTX slide rendered as PNG image."""
    result = await db.execute(select(Document).where(Document.id == doc_id))
    doc = result.scalar_one_or_none()
    _document_access(doc, current_user)
    if not doc.local_path or not os.path.exists(doc.local_path):
        raise HTTPException(404, "原始文件不可用")

    from app.core.parsing.pptx_renderer import pptx_renderer
    try:
        image_bytes = pptx_renderer.render_slide(doc.local_path, slide_index)
    except IndexError:
        raise HTTPException(404, f"Slide {slide_index} not found")
    except Exception as e:
        raise HTTPException(500, f"幻灯片渲染失败: {str(e)}")

    from fastapi.responses import Response
    return Response(content=image_bytes, media_type="image/png")


# --- Native Outline & Image Analysis endpoints ---

class ImageAnalysisRequest(BaseModel):
    model: str | None = None
    max_images: int | None = None


@router.get("/{doc_id}/native-outline")
async def get_native_outline(doc_id: str, db: AsyncSession = Depends(get_db), current_user=Depends(get_optional_user)):
    """Get the native document outline extracted by Docling (instant, no LLM)."""
    result = await db.execute(select(Document).where(Document.id == doc_id))
    doc = result.scalar_one_or_none()
    _document_access(doc, current_user)

    meta = doc.metadata_ or {}
    headings = meta.get("headings", [])
    outline_md = meta.get("native_outline_md", "")
    image_count = meta.get("image_count", 0)
    table_count = meta.get("table_count", 0)

    return {
        "document_id": doc_id,
        "headings": headings,
        "outline_markdown": outline_md,
        "image_count": image_count,
        "table_count": table_count,
    }


@router.get("/{doc_id}")
async def get_document_detail(doc_id: str, db: AsyncSession = Depends(get_db), current_user=Depends(get_optional_user)):
    """Get full document details including metadata counts."""
    result = await db.execute(select(Document).where(Document.id == doc_id))
    doc = result.scalar_one_or_none()
    _document_access(doc, current_user)

    meta = doc.metadata_ or {}
    return {
        "id": doc.id,
        "filename": doc.filename,
        "file_type": doc.file_type,
        "file_size": doc.file_size,
        "status": doc.status,
        "page_count": doc.page_count,
        "created_at": doc.created_at.isoformat(),
        "image_count": meta.get("image_count", 0),
        "table_count": meta.get("table_count", 0),
        "has_native_outline": bool(meta.get("native_outline_md", "")),
        "has_image_analyses": bool(meta.get("image_analyses", [])),
        **_organization(doc),
    }


@router.post("/{doc_id}/analyze-images")
async def analyze_document_images(
    doc_id: str,
    req: ImageAnalysisRequest = ImageAnalysisRequest(),
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_optional_user),
):
    """Analyze document images using a vision-capable LLM.

    Results (image descriptions) are returned and can be injected into
    the document content for downstream knowledge tree generation.
    """
    await load_user_api_keys(get_user_id(current_user), db, current_user is not None)
    result = await db.execute(select(Document).where(Document.id == doc_id))
    doc = result.scalar_one_or_none()
    _document_access(doc, current_user)

    meta = doc.metadata_ or {}
    headings = meta.get("headings", [])

    # Re-parse with Docling to get images if not already stored
    if not doc.local_path or not os.path.exists(doc.local_path):
        raise HTTPException(400, "原始文件不可用，无法提取图片")

    ext = doc.file_type
    if ext not in ("pdf", "docx", "pptx", "html"):
        raise HTTPException(400, f"图片分析仅支持 PDF/DOCX/PPTX/HTML，当前格式: {ext}")

    try:
        parsed = await parser.parse(doc.local_path, ext)
    except Exception as e:
        raise HTTPException(500, f"文档解析失败: {str(e)}")

    if not parsed.images:
        return {"document_id": doc_id, "images_analyzed": 0, "descriptions": [],
                "message": "文档中未检测到图片"}

    # Analyze images
    from app.core.parsing.image_analyzer import image_analyzer, ImageAnalysisConfig
    config = ImageAnalysisConfig(
        enabled=True,
        model=req.model or settings.vision_model,
        max_images=req.max_images or settings.vision_max_images,
    )

    try:
        descriptions = await image_analyzer.analyze_images(parsed.images, config)
    except Exception as e:
        raise HTTPException(500, f"图片分析失败: {str(e)}")

    # Store analysis results in document metadata
    analysis_data = [
        {"index": d.image_index, "page": d.page, "caption": d.caption,
         "analysis": d.analysis, "model_used": d.model_used}
        for d in descriptions
    ]
    updated_meta = dict(meta)
    updated_meta["image_analyses"] = analysis_data
    doc.metadata_ = updated_meta
    await db.commit()

    return {
        "document_id": doc_id,
        "images_analyzed": len(descriptions),
        "descriptions": analysis_data,
        "message": f"已分析 {len(descriptions)} 张图片，结果已保存到文档元数据",
    }
