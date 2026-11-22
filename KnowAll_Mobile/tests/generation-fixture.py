"""Production generation/preview HTTP routes and SQLite; deterministic model stub, no real AI."""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from contextlib import asynccontextmanager
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'KnowAll_Studio/backend'))
from fastapi import FastAPI, Depends, Request, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.database import Base, get_db
from app.models import Document, DocumentChunk
from app.models.study_pack import StudyPack
from app.api import learning, documents
from app.core.auth import get_current_user, get_optional_user
from app.core import study_pack_service as service
from app.core.api_scheduler import api_client

engine = create_async_engine('sqlite+aiosqlite:///:memory:')
sessions = async_sessionmaker(engine, expire_on_commit=False)
real_ai_calls = 0
generation_gate = asyncio.Event()
generation_gate.set()
async def prohibit_ai(*args, **kwargs):
    global real_ai_calls
    real_ai_calls += 1
    raise AssertionError('Real AI forbidden in this fixture')
api_client.generate = prohibit_ai
async def deterministic_section(section, question_count, card_count, config):
    if config['model'] == 'fixture-recovery': await generation_gate.wait()
    if config['model'] == 'fixture-all-fail': raise ValueError('Deterministic section failure')
    if config['model'] == 'fixture-slow': await asyncio.sleep(1)
    actual_questions = max(0, question_count - 1) if config['model'] == 'fixture-partial' else question_count
    return dict(notes='已整理：' + section['title'],
        questions=[dict(question_type='single_choice', question_text=section['title'] + '的练习题',
            options=[dict(label='A', text='正确项'), dict(label='B', text='错误项')], answer='A', analysis='来自本章内容。') for _ in range(actual_questions)],
        flashcards=[dict(front=section['title'] + '的知识卡问题？', back='本章知识卡答案。') for _ in range(card_count)])
service.generate_section = deterministic_section
async def identity(request: Request):
    token = request.headers.get('authorization')
    if token == 'Bearer local-ui-test-token': return SimpleNamespace(id='ui-test')
    if token == 'Bearer other-account-token': return SimpleNamespace(id='other')
    raise HTTPException(401)
async def database():
    async with sessions() as db: yield db
async def reset_data():
    generation_gate.set()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    async with sessions() as db:
        for doc_id, filename, kind in [('bio', '生物教材.md', 'knowledge'), ('history', '历史试卷.md', 'question_bank'), ('empty', '待解析资料.pdf', 'knowledge')]:
            db.add(Document(id=doc_id, filename=filename, file_type='pdf' if doc_id == 'empty' else 'md', sha256=doc_id * 15,
                status='processing' if doc_id == 'empty' else 'ready', metadata_={'knowledge_owner_id': 'ui-test', 'subject': '生物' if doc_id == 'bio' else '历史', 'material_kind': kind}))
            if doc_id != 'empty':
                for i, title in enumerate(['第一章 细胞', '第二章 遗传']):
                    db.add(DocumentChunk(id=f'{doc_id}-{i}', doc_id=doc_id, chunk_index=i, text_content=f'# {title}\n本章学习资料提供知识说明与例子。', page_range=f'p{i+1}'))
        await db.commit()
@asynccontextmanager
async def lifespan(app):
    await reset_data()
    yield
    await engine.dispose()
app = FastAPI(lifespan=lifespan)
app.include_router(learning.router)
app.include_router(documents.router)
app.dependency_overrides[get_db] = database
app.dependency_overrides[get_current_user] = identity
app.dependency_overrides[get_optional_user] = identity
@app.post('/fixture/reset')
async def reset(user=Depends(identity)):
    if user.id != 'ui-test': raise HTTPException(403)
    await reset_data()
    return {'reset': True}
@app.get('/fixture/packs')
async def packs(db=Depends(get_db), user=Depends(identity)):
    if user.id != 'ui-test': raise HTTPException(403)
    packs = (await db.execute(select(StudyPack).order_by(StudyPack.created_at, StudyPack.id))).scalars().all()
    return dict(real_ai_calls=real_ai_calls, packs=[dict(id=p.id, document_id=p.document_id, config=p.config,
        question_count=sum(len(s.get('questions', [])) for s in p.sections), card_count=sum(len(s.get('flashcards', [])) for s in p.sections)) for p in packs])
@app.post('/fixture/generation-gate/{action}')
async def gate(action: str, user=Depends(identity)):
    if user.id != 'ui-test': raise HTTPException(403)
    if action == 'block': generation_gate.clear()
    elif action == 'release': generation_gate.set()
    else: raise HTTPException(400)
    return {'released': generation_gate.is_set()}
if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=8898, log_level='warning')
