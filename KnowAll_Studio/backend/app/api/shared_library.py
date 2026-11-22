"""Opt-in document discovery; readers save only their selected knowledge chunk."""
from uuid import NAMESPACE_URL, uuid5

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.database import get_db
from app.models import Document, DocumentChunk
from app.models.user import KnowledgePoint

router = APIRouter(prefix='/api/v1/shared-library', tags=['shared-library'])


class SharingRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    shared: bool


class AddKnowledgeRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    chunk_id: str = Field(min_length=1, max_length=36)


def is_shared(doc):
    meta = doc.metadata_ or {}
    return bool(meta.get('knowledge_owner_id')) and meta.get('library_shared') is True and doc.status == 'ready'


@router.get('/mine')
async def my_documents(db: AsyncSession = Depends(get_db), user=Depends(get_current_user),
                       offset: int = Query(0, ge=0), limit: int = Query(30, ge=1, le=50)):
    docs = (await db.execute(select(Document).where(
        Document.metadata_['knowledge_owner_id'].as_string() == user.id,
    ).order_by(Document.created_at.desc(), Document.id).offset(offset).limit(limit + 1))).scalars().all()
    return {'items': [{'id': doc.id, 'filename': doc.filename, 'status': doc.status,
                       'shared': (doc.metadata_ or {}).get('library_shared') is True}
                      for doc in docs[:limit]], 'has_more': len(docs) > limit}


@router.patch('/documents/{document_id}/sharing')
async def set_sharing(document_id: str, req: SharingRequest, db: AsyncSession = Depends(get_db),
                      user=Depends(get_current_user)):
    doc = (await db.execute(select(Document).where(Document.id == document_id).with_for_update())).scalar_one_or_none()
    if doc is None or (doc.metadata_ or {}).get('knowledge_owner_id') != user.id:
        raise HTTPException(404, '资料不存在或不是你上传的资料')
    if req.shared and doc.status != 'ready':
        raise HTTPException(409, '请等待资料解析完成后再共享')
    doc.metadata_ = {**(doc.metadata_ or {}), 'library_shared': req.shared}
    await db.commit()
    return {'id': doc.id, 'shared': req.shared}


@router.get('/search')
async def search_shared(q: str = Query('', max_length=200), offset: int = Query(0, ge=0),
                        limit: int = Query(20, ge=1, le=40), db: AsyncSession = Depends(get_db),
                        user=Depends(get_current_user)):
    query = q.strip()
    if not query:
        return {'items': [], 'has_more': False}
    owner = Document.metadata_['knowledge_owner_id'].as_string()
    matches = or_(func.lower(Document.filename).contains(query.lower(), autoescape=True),
                  func.lower(DocumentChunk.text_content).contains(query.lower(), autoescape=True))
    ranked = select(
        Document.id.label('document_id'), Document.filename, DocumentChunk.id.label('chunk_id'),
        DocumentChunk.text_content, DocumentChunk.page_range,
        func.row_number().over(partition_by=Document.id, order_by=DocumentChunk.chunk_index).label('position'),
    ).join(DocumentChunk, DocumentChunk.doc_id == Document.id).where(
        owner.is_not(None), owner != '', Document.metadata_['library_shared'].as_boolean() == True,
        Document.status == 'ready', matches,
    ).subquery()
    rows = (await db.execute(select(ranked).where(ranked.c.position <= 2)
                            .order_by(ranked.c.document_id, ranked.c.position).offset(offset).limit(limit + 1))).mappings().all()
    identities = {str(uuid5(NAMESPACE_URL, f"knowall-shared:{user.id}:{row['document_id']}:{row['chunk_id']}")) for row in rows[:limit]}
    saved_ids = set()
    if identities:
        saved_ids = set((await db.execute(select(KnowledgePoint.id).where(
            KnowledgePoint.id.in_(identities), KnowledgePoint.user_id == user.id,
        ))).scalars().all())
    items = []
    for row in rows[:limit]:
        text = row['text_content']
        position = text.casefold().find(query.casefold())
        start = max(0, position - 100)
        excerpt = text[start:start + 600]
        if start:
            excerpt = '…' + excerpt
        if start + 600 < len(text):
            excerpt += '…'
        identity = str(uuid5(NAMESPACE_URL, f"knowall-shared:{user.id}:{row['document_id']}:{row['chunk_id']}"))
        items.append({'document_id': row['document_id'], 'filename': row['filename'],
                      'chunk_id': row['chunk_id'], 'excerpt': excerpt, 'page_range': row['page_range'],
                      'added': identity in saved_ids})
    return {'items': items, 'has_more': len(rows) > limit}


@router.get('/knowledge/search')
async def search_my_knowledge(q: str = Query('', max_length=200), offset: int = Query(0, ge=0),
                              limit: int = Query(20, ge=1, le=40), db: AsyncSession = Depends(get_db),
                              user=Depends(get_current_user)):
    query = q.strip()
    if not query:
        return {'items': [], 'has_more': False}
    rows = (await db.execute(select(KnowledgePoint).where(
        KnowledgePoint.user_id == user.id,
        or_(func.lower(KnowledgePoint.title).contains(query.lower(), autoescape=True),
            func.lower(KnowledgePoint.content).contains(query.lower(), autoescape=True)),
    ).order_by(KnowledgePoint.updated_at.desc(), KnowledgePoint.id).offset(offset).limit(limit + 1))).scalars().all()
    return {'items': [{'id': point.id, 'title': point.title, 'content': point.content, 'category': point.category}
                      for point in rows[:limit]], 'has_more': len(rows) > limit}


@router.post('/documents/{document_id}/add')
async def add_knowledge(document_id: str, req: AddKnowledgeRequest, db: AsyncSession = Depends(get_db),
                        user=Depends(get_current_user)):
    # Serialize sharing changes with saves, so withdrawing prevents subsequent additions.
    doc = (await db.execute(select(Document).where(Document.id == document_id).with_for_update())).scalar_one_or_none()
    if doc is None or not is_shared(doc):
        raise HTTPException(404, '这份资料已取消共享或暂不可用，请重新检索')
    chunk = await db.get(DocumentChunk, req.chunk_id)
    if chunk is None or chunk.doc_id != document_id:
        raise HTTPException(404, '没有找到这条知识的来源片段')
    identity = str(uuid5(NAMESPACE_URL, f'knowall-shared:{user.id}:{document_id}:{chunk.id}'))
    existing = await db.get(KnowledgePoint, identity)
    if existing is not None:
        return {'knowledge_point_id': identity, 'already_added': True}
    if not chunk.text_content.strip() or len(chunk.text_content) > 49000:
        raise HTTPException(422, '片段内容为空或过长，请选择其他命中片段')
    provenance = f'来源共享资料：{doc.filename}'
    if chunk.page_range:
        provenance += f' · 第 {chunk.page_range} 页'
    point = KnowledgePoint(id=identity, user_id=user.id, title=doc.filename[:500],
                           content=chunk.text_content + '\n\n' + provenance,
                           category=str((doc.metadata_ or {}).get('subject', ''))[:200], tags=['共享知识库'])
    db.add(point)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        if await db.get(KnowledgePoint, identity) is None:
            raise
        return {'knowledge_point_id': identity, 'already_added': True}
    return {'knowledge_point_id': identity, 'already_added': False}
