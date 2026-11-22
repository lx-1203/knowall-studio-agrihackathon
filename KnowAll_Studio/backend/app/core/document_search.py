"""Account-scoped filename/content search, independent of vector indexing."""
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document, DocumentChunk


async def keyword_search(db: AsyncSession, query: str, limit: int, user_id: str | None) -> list[dict]:
    # Treat SQL wildcard characters literally; bind parameters handle escaping.
    def match(column, term):
        return func.lower(column).contains(term.lower(), autoescape=True)

    terms = list(dict.fromkeys(query.split()))[:16]
    title = Document.filename
    text = DocumentChunk.text_content
    owner = Document.metadata_['knowledge_owner_id'].as_string()
    public = or_(owner.is_(None), owner == '')
    accessible = or_(public, owner == user_id) if user_id else public
    matches = [or_(match(title, term), match(text, term)) for term in terms]
    score = (case((match(title, query), 12), else_=0)
             + case((match(text, query), 8), else_=0)
             + sum(case((condition, 3), else_=0) for condition in matches))
    # Limit each document before the global limit, so one long PDF cannot
    # consume the whole result set. Ranking is performed in SQL, not on a
    # truncated list of candidates or by loading every chunk into Python.
    ranked = select(
        Document.id.label('doc_id'), title.label('filename'),
        DocumentChunk.id.label('id'), text.label('text'),
        DocumentChunk.chunk_index, DocumentChunk.page_range,
        score.label('score'),
        func.row_number().over(partition_by=Document.id,
                               order_by=(score.desc(), DocumentChunk.chunk_index)).label('position'),
    ).outerjoin(DocumentChunk, DocumentChunk.doc_id == Document.id).where(
        accessible, or_(*matches),
    ).subquery()
    rows = (await db.execute(select(ranked).where(ranked.c.position <= 3).order_by(
        ranked.c.score.desc(), ranked.c.doc_id, ranked.c.position,
    ).limit(limit))).mappings().all()
    return [{
        'id': row['id'] or f"document:{row['doc_id']}",
        'text': row['text'] or '', 'score': row['score'],
        'metadata': {'doc_id': row['doc_id'], 'filename': row['filename'],
                     'page_range': row['page_range'], 'chunk_index': row['chunk_index']},
    } for row in rows]


async def label_sources(db: AsyncSession, hits: list[dict]) -> list[dict]:
    """Read current filenames rather than relying on old vector metadata."""
    ids = {hit.get('metadata', {}).get('doc_id') for hit in hits if hit.get('metadata')}
    ids.discard(None)
    if not ids:
        return hits
    filenames = dict((await db.execute(select(Document.id, Document.filename).where(Document.id.in_(ids)))).all())
    return [{**hit, 'metadata': {**(hit.get('metadata') or {}),
                               'filename': filenames.get((hit.get('metadata') or {}).get('doc_id'), '')}}
            for hit in hits]
