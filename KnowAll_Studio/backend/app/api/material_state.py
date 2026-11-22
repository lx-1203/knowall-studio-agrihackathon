"""Private, compare-and-set material notes and text positions. GET is strictly read-only."""
import hashlib
import json
from datetime import datetime, timezone
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from app.database import get_db
from app.core.auth import get_current_user
from app.core.learning_workbench import source_fingerprint
from app.models import Document, DocumentChunk
from app.models.material_state import MaterialState, MaterialStateOperation

router = APIRouter()
Kind = Literal['note', 'text_progress', 'pdf_progress', 'image_progress']
class NotePayload(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    text: str = Field(max_length=100000)
class ProgressPayload(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    chunk_index: StrictInt = Field(ge=0, le=1000000)
    chunk_id: str | None = Field(default=None, max_length=100)
class PdfPayload(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    page_number: StrictInt = Field(ge=1)
    zoom: float = Field(ge=.5, le=3, allow_inf_nan=False, strict=True)
    zoom_basis: Literal['fit_width']
class StateWrite(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    account_id: str = Field(min_length=1, max_length=36)
    base_revision: StrictInt = Field(ge=0, le=2147483646)
    operation_id: str = Field(min_length=1, max_length=80, pattern=r'^[A-Za-z0-9_-]+$')
    payload: dict
    source_version: str | None = Field(default=None, max_length=64)

async def context(db, document_id, user_id, kind='note'):
    doc = await db.get(Document, document_id, populate_existing=True)
    if not doc or ((doc.metadata_ or {}).get('knowledge_owner_id') not in (None, '', user_id)):
        raise HTTPException(404, '资料不存在')
    if kind in ('pdf_progress', 'image_progress'):
        from app.core.parsing.document_preview import IMAGE_TYPES
        file_type = (doc.file_type or '').lower()
        if (kind == 'pdf_progress' and file_type != 'pdf') or (kind == 'image_progress' and file_type not in IMAGE_TYPES):
            raise HTTPException(415, '原件格式与阅读状态类型不匹配')
        from app.api.document_preview import _run
        from app.core.parsing.document_preview import preview_metadata
        return None, await _run(preview_metadata, doc.local_path, doc.file_type)
    chunks = (await db.execute(select(DocumentChunk).where(DocumentChunk.doc_id == document_id)
                             .order_by(DocumentChunk.chunk_index, DocumentChunk.id))).scalars().all()
    return chunks, source_fingerprint(chunks)

async def head(db, user_id, document_id, kind, current_source):
    info = current_source if isinstance(current_source, dict) else None
    current_source = info['source_version'] if info else current_source
    row = await db.get(MaterialState, (user_id, document_id, kind), populate_existing=True)
    return dict(version=1, account_id=user_id, document_id=document_id, kind=kind,
                revision=row.revision if row else 0, payload=row.payload if row else None,
                source_version=row.source_version if row else None, current_source_version=current_source,
                current_page_count=info['page_count'] if info else None,
                stale=bool(row and kind in ('text_progress','pdf_progress','image_progress') and row.source_version != current_source),
                updated_at=row.updated_at.replace(tzinfo=timezone.utc).isoformat() if row else None)

@router.get('/documents/{document_id}/state/{kind}')
async def read_state(document_id: str, kind: Kind, db=Depends(get_db), user=Depends(get_current_user)):
    _, source = await context(db, document_id, user.id, kind)
    return await head(db, user.id, document_id, kind, source)

@router.put('/documents/{document_id}/state/{kind}')
async def write_state(document_id: str, kind: Kind, req: StateWrite, db=Depends(get_db), user=Depends(get_current_user)):
    # rollback expires ORM objects even with expire_on_commit=False; freeze the identity first.
    user_id = user.id
    if req.account_id != user_id: raise HTTPException(403, '同步账号已变化')
    chunks, source = await context(db, document_id, user_id, kind)
    source_digest = source['source_version'] if kind in ('pdf_progress','image_progress') else source
    try:
        payload = {'note':NotePayload,'text_progress':ProgressPayload,'pdf_progress':PdfPayload,'image_progress':PdfPayload}[kind].model_validate(req.payload).model_dump()
    except ValidationError as error:
        raise HTTPException(422, '同步内容格式无效') from error
    digest = hashlib.sha256(json.dumps(req.model_dump(), ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    op_key = (user_id, document_id, kind, req.operation_id)
    async def replay():
        operation = await db.get(MaterialStateOperation, op_key, populate_existing=True)
        if operation:
            if operation.request_hash != digest: raise HTTPException(409, detail={'code':'operation_reused'})
            _, fresh_source = await context(db, document_id, user_id, kind)
            return await head(db, user_id, document_id, kind, fresh_source)
        return None
    repeated = await replay()
    if repeated is not None: return repeated
    if kind == 'text_progress':
        if req.source_version != source:
            raise HTTPException(409, detail={'code':'source_changed', 'current':await head(db,user_id,document_id,kind,source)})
        index = payload['chunk_index']
        if index >= len(chunks) or (payload['chunk_id'] is not None and payload['chunk_id'] != chunks[index].id):
            raise HTTPException(422, '正文位置与当前资料不匹配')
        payload['chunk_id'] = chunks[index].id
    if kind in ('pdf_progress','image_progress'):
        if req.source_version != source_digest:
            raise HTTPException(409, detail={'code':'source_changed','current':await head(db,user_id,document_id,kind,source)})
        if payload['page_number'] > source['page_count']: raise HTTPException(422, '页码超出当前原件')
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    values = dict(revision=req.base_revision+1, payload=payload,
                  source_version=source_digest if kind in ('text_progress','pdf_progress','image_progress') else None, updated_at=now)
    try:
        if req.base_revision == 0:
            db.add(MaterialState(user_id=user_id, document_id=document_id, kind=kind, **values))
            await db.flush()
        else:
            changed = await db.execute(update(MaterialState).where(MaterialState.user_id==user_id,
                MaterialState.document_id==document_id, MaterialState.kind==kind,
                MaterialState.revision==req.base_revision).values(**values))
            if changed.rowcount != 1:
                await db.rollback()
                repeated = await replay()
                if repeated is not None: return repeated
                _, source = await context(db, document_id, user_id, kind)
                raise HTTPException(409, detail={'code':'revision_conflict','current':await head(db,user_id,document_id,kind,source)})
        db.add(MaterialStateOperation(user_id=user_id, document_id=document_id, kind=kind,
                                     operation_id=req.operation_id, request_hash=digest, created_at=now))
        await db.commit()
    except IntegrityError:
        await db.rollback()
        repeated = await replay()
        if repeated is not None: return repeated
        _, source = await context(db, document_id, user_id, kind)
        raise HTTPException(409, detail={'code':'revision_conflict','current':await head(db,user_id,document_id,kind,source)})
    return await head(db, user_id, document_id, kind, source)
