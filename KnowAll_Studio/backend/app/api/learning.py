"""Authenticated source-based learning workbench endpoints."""
import csv
import io
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.core.model_connections import choose_request_model
from app.core.auth import get_current_user, load_user_api_keys
from app.core.documents import assert_document_access
from app.core import study_pack_service as service

router = APIRouter(prefix='/api/v1/learning', tags=['learning'])
from app.api.material_state import router as material_state_router
router.include_router(material_state_router)


class GeneratePackRequest(BaseModel):
    document_id: str
    question_count: int = Field(default=10, ge=0, le=100)
    card_count: int = Field(default=10, ge=0, le=100)
    concurrency: int = Field(default=2, ge=1, le=4)
    max_section_chars: int = Field(default=6000, ge=1000, le=8000)
    model: str = Field(default='deepseek-chat', min_length=1, max_length=256)
    question_types: list[str] = Field(default_factory=lambda: ['single_choice', 'matching', 'ordering', 'classification'])

    @field_validator('question_types')
    @classmethod
    def supported_types(cls, values):
        if not values or len(values) > len(service.SUPPORTED_TYPES) or any(v not in service.SUPPORTED_TYPES for v in values):
            raise ValueError('请选择支持的题型')
        return list(dict.fromkeys(values))


class SessionRequest(BaseModel):
    mode: Literal['smart', 'practice'] = 'smart'
    limit: int = Field(default=20, ge=1, le=100)


class AnswerRequest(BaseModel):
    question_id: str
    answer: str = Field(max_length=20000)
    time_spent_ms: int = Field(default=0, ge=0, le=86400000)


class ReviewRequest(BaseModel):
    card_id: str
    rating: int = Field(ge=1, le=4)


class RepairRequest(BaseModel):
    question_text: str = Field(max_length=20000)
    options: list[dict] = Field(default_factory=list, max_length=100)
    answer: str = Field(max_length=20000)
    analysis: str = Field(default='', max_length=20000)


@router.get('/documents/{document_id}/preview')
async def preview_document(document_id: str, question_count: int = Query(10, ge=0, le=100),
                           card_count: int = Query(10, ge=0, le=100), db: AsyncSession = Depends(get_db),
                           current_user=Depends(get_current_user)):
    if await assert_document_access(db, document_id, current_user.id) is None:
        raise HTTPException(404, '资料不存在')
    return service.preview(await service._chunks(db, document_id, current_user.id), dict(question_count=question_count, card_count=card_count))


@router.get('/documents/{document_id}/pack')
async def get_document_pack(document_id: str, db: AsyncSession = Depends(get_db), current_user=Depends(get_current_user)):
    return await service.get_latest_pack(db, document_id, current_user.id)


@router.post('/generate')
async def generate(req: GeneratePackRequest, db: AsyncSession = Depends(get_db), current_user=Depends(get_current_user)):
    await assert_document_access(db, req.document_id, current_user.id)
    await load_user_api_keys(current_user.id, db, True)
    await choose_request_model(req, current_user.id, db)
    return await service.generate_pack(db, req.document_id, current_user.id, req.model_dump(exclude={'document_id'}))


@router.post('/packs/{pack_id}/session')
async def start_session(pack_id: str, req: SessionRequest, db: AsyncSession = Depends(get_db), current_user=Depends(get_current_user)):
    return await service.build_session(db, pack_id, current_user.id, req.limit, req.mode)


@router.post('/packs/{pack_id}/answers')
async def submit(pack_id: str, req: AnswerRequest, db: AsyncSession = Depends(get_db), current_user=Depends(get_current_user)):
    return await service.submit_answer(db, pack_id, current_user.id, req.question_id, req.answer, req.time_spent_ms)


@router.post('/packs/{pack_id}/review')
async def review(pack_id: str, req: ReviewRequest, db: AsyncSession = Depends(get_db), current_user=Depends(get_current_user)):
    return await service.review_pack_card(db, pack_id, current_user.id, req.card_id, req.rating)


@router.patch('/packs/{pack_id}/questions/{question_id}')
async def repair(pack_id: str, question_id: str, req: RepairRequest, db: AsyncSession = Depends(get_db), current_user=Depends(get_current_user)):
    return await service.repair_question(db, pack_id, question_id, current_user.id, req.model_dump())


@router.get('/packs/{pack_id}/export')
async def export_pack(pack_id: str, format: Literal['json', 'markdown', 'csv'] = 'json',
                      db: AsyncSession = Depends(get_db), current_user=Depends(get_current_user)):
    pack = await service._owned_pack(db, pack_id, current_user.id)
    data = await service.serialize_live_pack(db, pack, await service._chunks(db, pack.document_id, current_user.id))
    if format == 'json':
        return Response(json.dumps(data, ensure_ascii=False, indent=2), media_type='application/json',
                        headers={'Content-Disposition': 'attachment; filename="learning-pack.json"'})
    if format == 'markdown':
        lines = ['# ' + data['title'], '', '来源版本：' + data['source_hash'],
                 '来源状态：' + ('已变化' if data['stale'] else '与生成时一致')]
        for s in data['sections']:
            lines.extend(['', '## ' + s['title'], '', '来源：' + ', '.join(s['chunk_ids']) + ' / ' + ', '.join(s['page_ranges']), '', s['notes']])
            for q in s['questions']:
                lines.extend(['', '### 练习：' + q['question_text']])
                for option in q.get('options', []):
                    lines.append(str(option.get('label', '')) + '. ' + str(option.get('text', '')))
                    if option.get('choices'):
                        lines.append('候选：' + '、'.join(map(str, option['choices'])))
                lines.extend(['答案：' + q['answer'], '解析：' + q['analysis'],
                              '质量：' + ('可练习' if q['quality']['eligible'] else '需修复：' + ', '.join(q['quality']['issues']))])
            for c in s['flashcards']:
                lines.extend(['', '### 闪卡：' + c['front'], c['back']])
            lines.extend('生成问题：' + e for e in s['errors'])
        return Response('\n'.join(lines), media_type='text/markdown', headers={'Content-Disposition': 'attachment; filename="learning-pack.md"'})
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['章节', '题目ID', '题型', '题目', '可练习', '质量问题', '来源片段', '页码'])
    for s in data['sections']:
        for q in s['questions']:
            row = [s['title'], q['id'], q['question_type'], q['question_text'], str(q['quality']['eligible']),
                   '; '.join(q['quality']['issues']), '; '.join(s['chunk_ids']), '; '.join(s['page_ranges'])]
            # Neutralize spreadsheet formulas in AI/user supplied CSV cells.
            writer.writerow(["'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value for value in row])
    return Response('\ufeff' + output.getvalue(), media_type='text/csv',
                    headers={'Content-Disposition': 'attachment; filename="question-quality.csv"'})
