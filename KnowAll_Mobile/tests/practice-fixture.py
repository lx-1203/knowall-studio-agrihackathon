"""Production learning/document HTTP routes over an isolated in-memory database.

Seeded questions/cards are deterministic, not AI-generated. Identities are test bearer fixtures.
"""
from pathlib import Path
from types import SimpleNamespace
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'KnowAll_Studio/backend'))
from fastapi import FastAPI, Request, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.database import Base, get_db
from app.models import Document, DocumentChunk, AnswerRecord, ReviewLog, Flashcard, ReviewSchedule
from app.api import learning, documents
from app.core.auth import get_current_user, get_optional_user
from app.core import study_pack_service as service

engine = create_async_engine('sqlite+aiosqlite:///:memory:')
sessions = async_sessionmaker(engine, expire_on_commit=False)
SOURCE = '# 细胞结构\n细胞核储存遗传信息，线粒体提供能量。DNA是遗传信息的载体。'
QUESTIONS = [
    dict(question_type='matching', question_text='匹配结构与功能', options=[dict(label='A', text='细胞核', choices=['储存遗传信息', '提供能量']), dict(label='B', text='线粒体', choices=['储存遗传信息', '提供能量'])], answer='{"A":"储存遗传信息","B":"提供能量"}', analysis='细胞核存储信息，线粒体提供能量。'),
    dict(question_type='ordering', question_text='排列复制过程', options=[dict(label='A', text='复制'), dict(label='B', text='解旋'), dict(label='C', text='检查')], answer='["B","A","C"]', analysis='先解旋，再复制，最后检查。'),
    dict(question_type='classification', question_text='区分细胞结构', options=[dict(label='A', text='细胞壁', choices=['植物细胞', '动物细胞']), dict(label='B', text='中心体', choices=['植物细胞', '动物细胞'])], answer='{"A":"植物细胞","B":"动物细胞"}', analysis='细胞壁属于植物细胞特征。'),
    dict(question_type='multi_choice', question_text='选择遗传物质', options=[dict(label='A', text='DNA'), dict(label='B', text='RNA'), dict(label='C', text='脂质')], answer='A,B', analysis='DNA和RNA承载遗传信息。'),
    dict(question_type='true_false', question_text='DNA保存遗传信息', answer='true', analysis='该说法正确。'),
    dict(question_type='fill_blank', question_text='填写两种遗传物质', answer='DNA;RNA', blanks=[dict(index=1, answer='DNA'), dict(index=2, answer='RNA')], analysis='分别为DNA与RNA。'),
]

async def seed_section(section, question_count, card_count, config):
    return dict(notes='细胞核与线粒体的功能。', questions=QUESTIONS,
                flashcards=[dict(front='细胞核的作用？', back='储存遗传信息。'), dict(front='DNA的作用？', back='保存遗传信息。')])

async def reset_fixtures():
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    async with sessions() as db:
        for doc_id, filename, text in [('bio', '细胞练习.md', SOURCE), ('history', '历史笔记.md', '# 历史\n复习历史资料。')]:
            db.add(Document(id=doc_id, filename=filename, file_type='md', sha256=doc_id * 22, status='ready',
                            metadata_={'knowledge_owner_id': 'ui-test', 'subject': '生物' if doc_id == 'bio' else '历史'}))
            db.add(DocumentChunk(id=doc_id + '-1', doc_id=doc_id, chunk_index=0, text_content=text, page_range='p1'))
        await db.commit()
        await service.generate_pack(db, 'bio', 'ui-test', dict(question_count=6, card_count=2, model='fixture', concurrency=1,
                                    max_section_chars=6000, question_types=[q['question_type'] for q in QUESTIONS]), generator=seed_section)

@asynccontextmanager
async def lifespan(app):
    await reset_fixtures()
    yield
    await engine.dispose()

async def database():
    async with sessions() as db:
        yield db

async def identity(request: Request):
    token = request.headers.get('authorization')
    if token == 'Bearer local-ui-test-token': return SimpleNamespace(id='ui-test')
    if token == 'Bearer other-account-token': return SimpleNamespace(id='other')
    raise HTTPException(401, 'Use the fixture token')

app = FastAPI(lifespan=lifespan)
app.include_router(learning.router)
app.include_router(documents.router)
app.dependency_overrides[get_db] = database
app.dependency_overrides[get_current_user] = identity
app.dependency_overrides[get_optional_user] = identity

@app.post('/fixture/reset')
async def reset(user=Depends(identity)):
    if user.id != 'ui-test': raise HTTPException(403)
    await reset_fixtures()
    return {'reset': True}

@app.get('/fixture/records')
async def records(db=Depends(get_db), user=Depends(identity)):
    if user.id != 'ui-test': raise HTTPException(403)
    answers = (await db.execute(select(AnswerRecord))).scalars().all()
    reviews = (await db.execute(select(ReviewLog))).scalars().all()
    cards = (await db.execute(select(Flashcard))).scalars().all()
    return dict(answers=[dict(question_id=a.question_id, answer=a.user_answer, correct=a.is_correct) for a in answers],
                ratings=[dict(card_id=r.card_id, rating=r.rating) for r in reviews],
                cards=[dict(id=c.id, review_count=c.review_count) for c in cards])

@app.post('/fixture/source/{state}')
async def source_state(state: str, db=Depends(get_db), user=Depends(identity)):
    if user.id != 'ui-test' or state not in ('fresh', 'stale'): raise HTTPException(403)
    chunk = await db.get(DocumentChunk, 'bio-1')
    chunk.text_content = SOURCE + ('\n来源已经更新。' if state == 'stale' else '')
    await db.commit()
    return {'state': state}

@app.post('/fixture/due/{card_id}')
async def force_due(card_id: str, db=Depends(get_db), user=Depends(identity)):
    if user.id != 'ui-test': raise HTTPException(403)
    schedule = (await db.execute(select(ReviewSchedule).where(ReviewSchedule.card_id == card_id))).scalar_one()
    schedule.next_review_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
    await db.commit()
    return {'card_id': card_id}

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=8897, log_level='warning')
