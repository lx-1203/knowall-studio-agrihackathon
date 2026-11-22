"""Authenticated central knowledge discovery and private learning API."""
from typing import Literal
from copy import deepcopy

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update

from app.database import get_db
from app.core.auth import get_current_user
from app.core.knowledge_network import register_extraction, enqueue_extraction, job_response, valid_sources, rebuild_relations
from app.core.knowledge_network_seed import seed_compiled_concepts
from app.core.knowledge_authority import source_search_response, schedule_source_searches
from app.models.knowledge_authority import AuthoritySourceSearch
from app.models.knowledge_network import NetworkConcept, ConceptLearning, ConceptRelation, ExtractionJob
from app.models import Document

router = APIRouter(prefix='/api/v1/knowledge-network', tags=['knowledge-network'])


class ExtractionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    document_id: str = Field(min_length=1, max_length=36)
    subject: str = Field(default='', max_length=80)
    model: str | None = Field(default=None, min_length=1, max_length=256)


class PublishRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reviewed: Literal[True]


class LearningRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    saved: bool
    mastery: Literal['unseen', 'learning', 'mastered']
    note: str = Field(max_length=10000)


def learning_response(row):
    return {'saved': row.saved, 'mastery': row.mastery, 'note': row.note}


async def concept_context(db, cid, uid):
    concept = await db.get(NetworkConcept, cid, populate_existing=True)
    owned = await valid_sources(db, uid, cid)
    if not concept or (concept.public_content is None and not owned):
        raise HTTPException(404, '知识点不存在')
    learning = await db.get(ConceptLearning, (uid, cid), populate_existing=True)
    return concept, owned, learning


def private_content(owned):
    """Combine this owner's explanations without changing any public definition."""
    if not owned:
        return None
    content = deepcopy(owned[0][0].content)
    fields = ('aliases', 'formulas', 'conditions', 'key_points', 'pitfalls', 'prerequisites', 'related', 'supplements')
    for source, _, _ in owned[1:]:
        for field in fields:
            content[field] = list(dict.fromkeys(content.get(field, []) + source.content.get(field, [])))
        alternate = source.content.get('definition', '')
        if alternate and alternate != content['definition']:
            content['supplements'] = list(dict.fromkeys(content.get('supplements', []) + [alternate]))
    return content


def item_response(concept, owned, learning, public=False):
    content = concept.public_content if public or not owned else private_content(owned)
    content = content or {}
    status = concept.review_status if public or not owned else ('reviewed' if all(s.reviewed for s, _, _ in owned) else 'needs_review')
    return {'id': concept.id, 'name': concept.name, 'subject': concept.subject, 'topic': concept.topic,
            'definition': content.get('definition', ''), 'aliases': content.get('aliases', []),
            'review_status': status, 'in_learning': bool(learning and learning.saved),
            # Authority-search sources are independent of the internal uploaded evidence.
            'source_count': 0}


async def detail(db, cid, uid, public=False):
    concept, owned, learning = await concept_context(db, cid, uid)
    all_concepts = {c.id: c for c in (await db.scalars(select(NetworkConcept))).all()}
    private_visible = {s.concept_id for s, _, _ in await valid_sources(db, uid)}
    relation_rows = (await db.scalars(select(ConceptRelation).where(ConceptRelation.source_id == cid))).all()
    relations, seen = [], set()
    for rel in relation_rows:
        target = all_concepts.get(rel.target_id)
        if not target:
            continue
        public_relation = rel.is_public and concept.public_content is not None and target.public_content is not None
        private_relation = not public and rel.user_id == uid and (target.public_content is not None or target.id in private_visible)
        key = (rel.target_id, rel.relation_type)
        if (public_relation or private_relation) and key not in seen:
            seen.add(key)
            relations.append({'source_id': cid, 'target_id': target.id, 'relation_type': rel.relation_type, 'target_name': target.name})
    content = concept.public_content if public or not owned else private_content(owned)
    source_search = await source_search_response(db, cid)
    return {**item_response(concept, owned, learning, public), 'content': content,
            'source_count': len(source_search['sources']),
            'sources': [], 'source_search': source_search,
            'can_contribute': bool(owned), 'relations': relations,
            'learning': learning_response(learning) if learning else None}


@router.post('/extractions', status_code=202)
async def start_extraction(req: ExtractionRequest, background_tasks: BackgroundTasks, response: Response,
                           db=Depends(get_db), user=Depends(get_current_user)):
    job = await register_extraction(db, req.document_id, user.id, req.subject, req.model)
    if job.status == 'completed':
        response.status_code = 200
    enqueue_extraction(background_tasks, db, job)
    return job_response(job)


@router.get('/extractions/{job_id}')
async def extraction_status(job_id: str, db=Depends(get_db), user=Depends(get_current_user)):
    job = await db.get(ExtractionJob, job_id, populate_existing=True)
    # Source deletion or ownership changes revoke access to old job data as well.
    from app.models import Document
    doc = await db.get(Document, job.document_id, populate_existing=True) if job else None
    if not job or job.user_id != user.id or not doc or (doc.metadata_ or {}).get('knowledge_owner_id') != user.id:
        raise HTTPException(404, '提炼任务不存在')
    return job_response(job)


@router.get('/concepts')
async def list_concepts(scope: Literal['public', 'personal'] = 'public', q: str = Query('', max_length=200),
                        subject: str = Query('', max_length=80), limit: int = Query(50, ge=1, le=200),
                        offset: int = Query(0, ge=0), document_id: str | None = Query(None, max_length=36),
                        db=Depends(get_db), user=Depends(get_current_user)):
    if document_id:
        doc = await db.get(Document, document_id, populate_existing=True)
        if not doc or (doc.metadata_ or {}).get('knowledge_owner_id') != user.id:
            raise HTTPException(404, '所选资料不存在或不属于当前用户')
    if scope == 'public':
        await seed_compiled_concepts(db)
        await db.commit()
    owned = await valid_sources(db, user.id)
    if document_id:
        owned = [row for row in owned if row[1].id == document_id]
    grouped = {}
    for row in owned:
        grouped.setdefault(row[0].concept_id, []).append(row)
    states = {s.concept_id: s for s in (await db.scalars(select(ConceptLearning).where(ConceptLearning.user_id == user.id))).all()}
    query = select(NetworkConcept).order_by(NetworkConcept.subject, NetworkConcept.topic, NetworkConcept.name, NetworkConcept.id)
    if subject:
        query = query.where(NetworkConcept.subject == subject.strip())
    if scope == 'public':
        query = query.where(NetworkConcept.public_content.is_not(None))
    authority_counts = {r.concept_id: len(r.sources or []) for r in (await db.scalars(select(AuthoritySourceSearch))).all()}
    items = []
    for concept in (await db.scalars(query)).all():
        mine = grouped.get(concept.id, [])
        if document_id and not mine:
            continue
        state = states.get(concept.id)
        if scope == 'personal' and not mine and not (state and state.saved and concept.public_content is not None):
            continue
        item = item_response(concept, mine, state, scope == 'public')
        item['source_count'] = authority_counts.get(concept.id, 0)
        if q and q.casefold() not in ' '.join([item['name'], item['topic'], item['definition'], *item['aliases']]).casefold():
            continue
        items.append(item)
    return {'items': items[offset:offset + limit], 'total': len(items)}


@router.get('/documents')
async def discovery_documents(db=Depends(get_db), user=Depends(get_current_user)):
    rows = (await db.scalars(select(Document).where(
        Document.metadata_['knowledge_owner_id'].as_string() == user.id,
    ).order_by(Document.created_at.desc(), Document.id))).all()
    return {'items': [{'id': doc.id, 'filename': doc.filename, 'status': doc.status} for doc in rows]}


@router.get('/discover')
async def discover_concepts(scope: Literal['public', 'personal'] = 'personal',
                            q: str = Query('', max_length=200), subject: str = Query('', max_length=80),
                            document_id: str | None = Query(None, max_length=36),
                            limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                            db=Depends(get_db), user=Depends(get_current_user)):
    query = q.strip()
    # Public discovery always needs a keyword; personal discovery can use an owned document.
    if not query and (scope == 'public' or not document_id):
        return {'items': [], 'total': 0}
    return await list_concepts(scope=scope, q=query, subject=subject.strip(), document_id=document_id,
                               limit=limit, offset=offset, db=db, user=user)


@router.get('/concepts/{concept_id}')
async def get_concept(concept_id: str, background_tasks: BackgroundTasks,
                      scope: Literal['public', 'personal'] = 'personal', db=Depends(get_db), user=Depends(get_current_user)):
    concept, _, _ = await concept_context(db, concept_id, user.id)
    if scope == 'public' and concept.public_content is None:
        raise HTTPException(404, '知识点尚未贡献至中央库')
    result = await detail(db, concept_id, user.id, public=scope == 'public')
    if result['source_search']['status'] == 'searching':
        background_tasks.add_task(schedule_source_searches, [concept_id], user.id)
    return result


@router.post('/concepts/{concept_id}/publish')
async def publish_concept(concept_id: str, req: PublishRequest, db=Depends(get_db), user=Depends(get_current_user)):
    concept, owned, _ = await concept_context(db, concept_id, user.id)
    if not owned:
        raise HTTPException(404, '需要本人提炼证据并确认校对后贡献')
    # First reviewed definition wins, including concurrent publications.
    await db.execute(update(NetworkConcept).where(NetworkConcept.id == concept_id, NetworkConcept.public_content.is_(None))
                     .values(public_content=owned[0][0].content, review_status='reviewed'))
    for source, _, _ in owned:
        source.reviewed = True
    await rebuild_relations(db, user.id)
    rows = (await db.scalars(select(ConceptRelation).where(ConceptRelation.user_id == user.id))).all()
    for row in rows:
        if row.source_id == concept_id:
            row.reviewed = True
    await db.flush()
    public_ids = select(NetworkConcept.id).where(NetworkConcept.public_content.is_not(None))
    # A different contributor may publish the target after this relationship was reviewed.
    await db.execute(update(ConceptRelation).where(ConceptRelation.reviewed.is_(True),
                                                   ConceptRelation.source_id.in_(public_ids),
                                                   ConceptRelation.target_id.in_(public_ids))
                     .values(is_public=True))
    await db.commit()
    return await detail(db, concept_id, user.id, public=True)


@router.put('/concepts/{concept_id}/learning')
async def update_learning(concept_id: str, req: LearningRequest, db=Depends(get_db), user=Depends(get_current_user)):
    _, _, row = await concept_context(db, concept_id, user.id)
    if not row:
        row = ConceptLearning(user_id=user.id, concept_id=concept_id)
        db.add(row)
    row.saved, row.mastery, row.note = req.saved, req.mastery, req.note
    await db.commit()
    return learning_response(row)
