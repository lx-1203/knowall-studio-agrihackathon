"""Shared source loaders with explicit private-upload ownership checks.

Legacy documents without ownership metadata retain their existing access.
Owned uploads require an authenticated owner ID; unscoped generation callers
must use the knowledge network or personal learning routes instead.
"""
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document, DocumentChunk


def _assert_owner(metadata, user_id):
    owner = (metadata or {}).get('knowledge_owner_id')
    if owner and (not user_id or owner != user_id):
        raise HTTPException(403, '私有资料仅限上传者访问；请登录后通过知识网络或资料学习使用')


async def assert_document_access(db: AsyncSession, document_id: str, user_id: str | None = None) -> Document | None:
    """Require an explicit authenticated owner for private uploads."""
    doc = await db.get(Document, document_id, populate_existing=True)
    if doc is not None:
        _assert_owner(doc.metadata_, user_id)
    return doc


async def assert_documents_access(
    db: AsyncSession, document_ids: list[str], user_id: str | None = None,
) -> list[Document]:
    """Check requested sources in one query; skip missing IDs, preserve order."""
    if not document_ids:
        return []
    result = await db.execute(
        select(Document).where(Document.id.in_(document_ids))
        .execution_options(populate_existing=True)
    )
    docs_by_id = {doc.id: doc for doc in result.scalars().all()}
    for doc in docs_by_id.values():
        _assert_owner(doc.metadata_, user_id)
    return [docs_by_id[i] for i in document_ids if i in docs_by_id]


async def load_document_chunks(
    db: AsyncSession,
    document_id: str,
    chunk_ids: list[str] | None = None,
    *, user_id: str | None = None,
) -> list[DocumentChunk]:
    """Load document chunks by explicit ids, or all chunks of a document.

    Args:
        db: Active async session.
        document_id: Document whose chunks to load.
        user_id: Explicit authenticated account ID; required for owned uploads.
        chunk_ids: When truthy, load exactly these chunks (caller-selected
            subset wins). When falsy, load every chunk of ``document_id``
            ordered by ``chunk_index``.

    Returns:
        The matching chunks. Empty when the document has none, or when
        ``chunk_ids`` matched nothing — callers decide whether that is a 404.

    Raises:
        HTTPException: Private document ownership does not match the explicit user_id.
    """
    statement = (select(DocumentChunk, Document.metadata_)
                 .join(Document, Document.id == DocumentChunk.doc_id)
                 .where(DocumentChunk.doc_id == document_id)
                 .order_by(DocumentChunk.chunk_index))
    if chunk_ids:
        statement = statement.where(DocumentChunk.id.in_(chunk_ids))
    rows = (await db.execute(statement)).all()
    for _, metadata in rows:
        _assert_owner(metadata, user_id)
    return [chunk for chunk, _ in rows]


async def load_documents_with_chunks(
    db: AsyncSession,
    document_ids: list[str],
    *, user_id: str | None = None,
) -> tuple[list[Document], dict[str, list[DocumentChunk]]]:
    """Load several documents and their chunks in **two** queries total.

    Replaces a `for doc_id in document_ids:` loop that issued two SELECTs per
    document (found by `scripts/find_n_plus_one.py`, round 21). With 20 requested
    documents that was 40 round-trips; it is now 2 regardless of the count.

    Ordering matters and is preserved:

    * documents come back in the order of ``document_ids`` (missing ids skipped),
      because callers concatenate their chunk text into an LLM prompt;
    * each document's chunks are ordered by ``chunk_index``.

    Args:
        db: Active async session.
        user_id: Explicit authenticated account ID; required for owned uploads.
        document_ids: Requested ids, in the order the caller wants them.
            May be empty, in which case no query is issued.

    Returns:
        ``(documents_in_requested_order, {doc_id: [chunks_by_index]})``.
        A requested id with no matching document is simply absent from the list.
    """
    if not document_ids:
        return [], {}

    docs_result = await db.execute(
        select(Document).where(Document.id.in_(document_ids)).execution_options(populate_existing=True)
    )
    docs_by_id = {d.id: d for d in docs_result.scalars().all()}
    for doc in docs_by_id.values():
        _assert_owner(doc.metadata_, user_id)

    chunks_result = await db.execute(
        select(DocumentChunk)
        .where(DocumentChunk.doc_id.in_(list(docs_by_id)))
        .order_by(DocumentChunk.doc_id, DocumentChunk.chunk_index)
    )
    chunks_by_doc: dict[str, list[DocumentChunk]] = {}
    for chunk in chunks_result.scalars().all():
        chunks_by_doc.setdefault(chunk.doc_id, []).append(chunk)

    return [docs_by_id[i] for i in document_ids if i in docs_by_id], chunks_by_doc
