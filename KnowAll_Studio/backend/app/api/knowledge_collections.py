"""Knowledge-only collections: shared compiled starters and owned generated content."""
import hashlib
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.auth import get_current_user, load_user_api_keys
from app.core.documents import load_documents_with_chunks
from app.core.knowledge_collections import (
    CollectionContent, builtin_collections, consolidate, make_batches, generate_collection,
)
from app.database import get_db
from app.models.knowledge_collection import KnowledgeCollection

router = APIRouter(prefix='/api/v1/knowledge-collections', tags=['knowledge-collections'])
logger = logging.getLogger(__name__)


class SourceSelection(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    document_ids: list[str] = Field(min_length=1, max_length=10)


class GenerateCollectionRequest(SourceSelection):
    title: str = Field(min_length=1, max_length=120)
    subject: str = Field(min_length=1, max_length=80)
    model: str = Field(default=settings.default_model, min_length=1, max_length=128)


class UpdateCollectionRequest(CollectionContent):
    reviewed: bool = False


def response(row: KnowledgeCollection, *, detail: bool = True) -> dict:
    result = {'id': row.id, 'title': row.title, 'subject': row.subject,
              'kind': 'personal', 'review_status': row.review_status,
              'created_at': row.created_at.isoformat(), 'point_count': len(row.points)}
    if detail:
        result['points'] = row.points
    return result


async def selected_sources(req: SourceSelection, db: AsyncSession, user_id: str | None = None):
    ids = list(dict.fromkeys(req.document_ids))
    docs, chunks_by_doc = await load_documents_with_chunks(db, ids, user_id=user_id)
    if len(docs) != len(ids):
        raise HTTPException(404, '所选资料不存在，请刷新列表')
    if any(doc.status != 'ready' or not chunks_by_doc.get(doc.id) for doc in docs):
        raise HTTPException(409, '所选资料尚未解析完成，请先完成资料解析')
    texts = [chunk.text_content for doc in docs for chunk in chunks_by_doc[doc.id]]
    try:
        batches = make_batches(texts)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return docs, batches, texts


@router.get('')
async def list_collections(db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    rows = (await db.execute(select(KnowledgeCollection).where(KnowledgeCollection.user_id == user.id)
                            .order_by(KnowledgeCollection.created_at.desc()).limit(200))).scalars().all()
    builtin = [{k: v for k, v in entry.items() if k != 'points'} | {'point_count': len(entry['points'])}
               for entry in builtin_collections()]
    return builtin + [response(row, detail=False) for row in rows]


@router.post('/preview')
async def preview(req: SourceSelection, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    docs, batches, texts = await selected_sources(req, db, user.id)
    return {'batch_count': len(batches), 'characters': sum(map(len, texts)),
            'documents': [{'id': doc.id, 'name': doc.filename} for doc in docs],
            'chunk_count': len(texts)}


@router.post('/import', status_code=201)
async def import_collection(req: CollectionContent, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    try:
        points = consolidate(req.points)
    except ValueError as exc:
        raise HTTPException(422, '合并后的知识点字段过长，请按主题拆分集锦') from exc
    row = KnowledgeCollection(user_id=user.id, title=req.title, subject=req.subject,
                              points=[p.model_dump() for p in points],
                              provenance={'method': 'structured_import'})
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return response(row)


@router.put('/{collection_id}')
async def update_collection(collection_id: str, req: UpdateCollectionRequest,
                            db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    row = (await db.execute(select(KnowledgeCollection).where(
        KnowledgeCollection.id == collection_id, KnowledgeCollection.user_id == user.id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, '知识点集锦不存在或不可修改')
    try:
        points = consolidate(req.points)
    except ValueError as exc:
        raise HTTPException(422, '合并后的知识点字段过长，请按主题拆分集锦') from exc
    row.title, row.subject = req.title, req.subject
    row.points = [point.model_dump() for point in points]
    row.review_status = 'reviewed' if req.reviewed else 'needs_review'
    await db.commit()
    await db.refresh(row)
    return response(row)


@router.post('/generate', status_code=201)
async def generate(req: GenerateCollectionRequest, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    docs, batches, texts = await selected_sources(req, db, user.id)
    await load_user_api_keys(user.id, db, True)
    try:
        points, models = await generate_collection(batches, req.subject, req.model, user.id)
    except Exception as exc:
        logger.warning('Knowledge collection generation failed: %s', type(exc).__name__)
        raise HTTPException(502, '本次提炼未完成，未保存部分结果。请检查模型配置后重试。') from exc
    content = CollectionContent(title=req.title, subject=req.subject, points=points)
    row = KnowledgeCollection(user_id=user.id, title=content.title, subject=content.subject,
                              points=[p.model_dump() for p in content.points],
                              provenance={'method': 'document_extraction', 'document_ids': [d.id for d in docs],
                                          'source_hash': hashlib.sha256('\n\n'.join(texts).encode()).hexdigest(),
                                          'models': models, 'batch_count': len(batches)})
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return response(row)


@router.get('/{collection_id}')
async def get_collection(collection_id: str, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    for entry in builtin_collections():
        if entry['id'] == collection_id:
            return {**entry, 'point_count': len(entry['points'])}
    row = (await db.execute(select(KnowledgeCollection).where(
        KnowledgeCollection.id == collection_id, KnowledgeCollection.user_id == user.id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, '知识点集锦不存在')
    return response(row)
