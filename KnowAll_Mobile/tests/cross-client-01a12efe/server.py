"""Isolated real-auth/SQL/HTTP fixture. No production DB, AI or authentication replacement."""
import argparse
import os
import secrets
import sys
from pathlib import Path
from contextlib import asynccontextmanager

parser = argparse.ArgumentParser()
parser.add_argument('--db', required=True)
parser.add_argument('--assets', required=True)
parser.add_argument('--port', required=True, type=int)
args = parser.parse_args()
os.environ['DATABASE_URL'] = 'sqlite+aiosqlite:///' + Path(args.db).resolve().as_posix()
os.environ['JWT_SECRET'] = os.environ.get('CROSS_TEST_JWT', secrets.token_hex(32))
os.environ['DEBUG'] = 'false'
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'KnowAll_Studio/backend'))

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from app.database import Base, engine, async_session, get_db
from app.models import User, Document, DocumentChunk, AnswerRecord, ReviewLog, ReviewSchedule
from app.models.study_pack import StudyPack
from app.core.auth import get_current_user, hash_password
from app.core.api_scheduler import api_client
from app.core import study_pack_service as service
from app.api import auth, documents, learning, flashcards

ai_calls = 0
requests = []
async def forbid_ai(*unused_args, **unused_kwargs):
    global ai_calls
    ai_calls += 1
    raise AssertionError('Real AI is forbidden')
api_client.generate = forbid_ai

async def deterministic(section, question_count, card_count, config):
    return dict(notes='细胞核储存遗传信息。',
                questions=[dict(question_type='single_choice', question_text='细胞核的主要作用？',
                                options=[dict(label='A', text='储存遗传信息'), dict(label='B', text='提供能量')],
                                answer='A', analysis='来源是本资料第一段。')],
                flashcards=[dict(front='细胞核的作用？', back='储存遗传信息。'),
                            dict(front='线粒体的作用？', back='提供能量。')])

async def seed():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_session() as db:
        if await db.get(User, 'cross-a'): return
        for suffix in ['a', 'b']:
            db.add(User(id='cross-' + suffix, username='cross_' + suffix,
                        email='cross_' + suffix + '@example.invalid',
                        password_hash=hash_password('LocalTestOnly_2026!'), is_active=True))
            for kind in ['knowledge', 'question_bank']:
                doc_id = kind + '-' + suffix
                db.add(Document(id=doc_id, filename=('知识资料' if kind == 'knowledge' else '独立试卷') + '.md',
                                file_type='md', sha256=doc_id * 4, status='ready',
                                metadata_={'knowledge_owner_id': 'cross-' + suffix,
                                           'subject': '生物', 'material_kind': kind}))
                db.add(DocumentChunk(id=doc_id + '-1', doc_id=doc_id, chunk_index=0,
                                     text_content='# 细胞\n细胞核储存遗传信息，线粒体提供能量。', page_range='p1'))
        await db.commit()
        await service.generate_pack(db, 'knowledge-a', 'cross-a',
                                    dict(question_count=1, card_count=2, model='deterministic-test',
                                         concurrency=2, max_section_chars=6000, question_types=['single_choice']),
                                    generator=deterministic)

@asynccontextmanager
async def lifespan(app):
    await seed()
    yield
    await engine.dispose()

app = FastAPI(lifespan=lifespan)
for router in [auth.router, documents.router, learning.router, flashcards.router]:
    app.include_router(router)
# Uses production get_db and get_current_user. No identity/dependency override.
app.mount('/__client', StaticFiles(directory=args.assets), name='test-client')

@app.middleware('http')
async def trace(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith('/api/'):
        requests.append(dict(method=request.method, path=request.url.path, status=response.status_code,
                             bearer=request.headers.get('authorization', '').startswith('Bearer ')))
    return response

@app.get('/__test/health')
async def health():
    return dict(pid=os.getpid(), ai_calls=ai_calls)

@app.get('/__test/page/{kind}', response_class=HTMLResponse)
async def page(kind: str):
    if kind not in ['mobile', 'desktop']: raise HTTPException(404)
    return '<!doctype html><meta charset="utf-8"><title>API validation only</title><script type="module">' + \
           f'import * as api from "/__client/{kind}.js"; window.client=api;</script>'

@app.get('/__test/state')
async def state(db=Depends(get_db), user=Depends(get_current_user)):
    pack = (await db.execute(select(StudyPack).where(StudyPack.user_id == user.id))).scalars().first()
    card_ids = [c['id'] for s in pack.sections for c in s.get('flashcards', [])] if pack else []
    answers = (await db.execute(select(AnswerRecord).where(AnswerRecord.user_id == user.id)
                               .order_by(AnswerRecord.answered_at, AnswerRecord.id))).scalars().all()
    schedules = (await db.execute(select(ReviewSchedule).where(ReviewSchedule.card_id.in_(card_ids)))).scalars().all()
    reviews = (await db.execute(select(ReviewLog).where(ReviewLog.card_id.in_(card_ids)))).scalars().all()
    return dict(pid=os.getpid(), ai_calls=ai_calls, answers=[dict(question_id=a.question_id, correct=a.is_correct) for a in answers],
                schedules=[dict(card_id=s.card_id, count=s.review_count, state=s.state,
                                next_review=s.next_review_at.isoformat() if s.next_review_at else None) for s in schedules],
                reviews=[dict(card_id=r.card_id, rating=r.rating) for r in reviews])

@app.get('/__test/trace')
async def read_trace(user=Depends(get_current_user)):
    if user.id != 'cross-a': raise HTTPException(403)
    return dict(requests=requests, ai_calls=ai_calls, pid=os.getpid())

@app.post('/__test/source-change')
async def change_source(db=Depends(get_db), user=Depends(get_current_user)):
    if user.id != 'cross-a': raise HTTPException(403)
    chunk = await db.get(DocumentChunk, 'knowledge-a-1')
    chunk.text_content += '\n来源更新。'
    await db.commit()
    return dict(changed=True)

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning')
