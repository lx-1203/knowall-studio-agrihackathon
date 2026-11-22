"""Temporary real JWT/SQL routes. Only the model reply is a deterministic stub."""
import os
import secrets
import sys
import tempfile
from pathlib import Path
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
root = Path(tempfile.mkdtemp(prefix='knowall-tools-01a12efd-'))
os.environ['DATABASE_URL'] = 'sqlite+aiosqlite:///' + (root / 'test.sqlite').as_posix()
os.environ['JWT_SECRET'] = secrets.token_hex(32)
os.environ['KNOWALL_DATA_ROOT'] = str(root)
os.environ['DEBUG'] = 'false'
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'KnowAll_Studio/backend'))
from fastapi import FastAPI, Request
from app.database import Base, engine, async_session
from app.models import User, QuestionBank, Deck, Flashcard, ReviewSchedule, RecitationPassage, RecitationRecord, Conversation, Message
from app.core.auth import hash_password
from app.core.api_scheduler import api_client
from app.api import auth, questions, flashcards, recitation, chat, gamification
real_ai_calls = 0
stub_replies = 0
trace = []
async def forbid_ai(*args, **kwargs):
    global real_ai_calls
    real_ai_calls += 1
    raise AssertionError('Paid/real model forbidden in tool acceptance')
api_client.generate = forbid_ai
async def deterministic_chat(message, *args, **kwargs):
    global stub_replies
    stub_replies += 1
    return '这是隔离测试回复：细胞核保存遗传信息。'
chat.ai_assistant.chat = deterministic_chat

@asynccontextmanager
async def lifespan(app):
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_session() as db:
        db.add(User(id='tools-http', username='tools_http', nickname='小林', email='tools@example.invalid', password_hash=hash_password('LocalToolsTest_2026!'), is_active=True))
        db.add(QuestionBank(id='q1', question_type='single_choice', question_text='细胞核的主要作用是什么？', options=[dict(label='A', text='储存遗传信息'), dict(label='B', text='提供能量')], correct_answer='A', analysis='细胞核保存遗传信息。'))
        db.add(QuestionBank(id='q2', question_type='true_false', question_text='DNA 可以保存遗传信息。', options=[], correct_answer='true', analysis='DNA是遗传信息的载体。'))
        db.add(QuestionBank(id='q3', question_type='multi_choice', question_text='哪些属于遗传物质？', options=[dict(label='A', text='DNA'), dict(label='B', text='RNA'), dict(label='C', text='脂质')], correct_answer='A,B', analysis='DNA和RNA都可承载遗传信息。'))
        for i, name in [(1, '细胞生物学'), (2, '遗传学')]:
            db.add(Deck(id='d' + str(i), name=name, user_id='tools-http', card_count=1))
            db.add(Flashcard(id='c' + str(i), deck_id='d' + str(i), card_type='qa', front='细胞膜的作用？' if i == 1 else 'DNA 的作用？', back='调节物质交换。' if i == 1 else '保存遗传信息。'))
            db.add(ReviewSchedule(id='s' + str(i), card_id='c' + str(i), state='new', review_count=0))
        db.add(RecitationPassage(id='p1', user_id='tools-http', title='细胞结构', content='细胞核储存遗传信息。', category='custom'))
        db.add(RecitationRecord(id='due1', user_id='tools-http', passage_id='p1', mode='dictation', accuracy=.8, review_count=1, state='review', next_review_at=datetime.now() - timedelta(days=1)))
        for i, title in [(1, '细胞结构讨论'), (2, '遗传学讨论')]:
            db.add(Conversation(id='conv' + str(i), user_id='tools-http', title=title, role_preset='tutor'))
            db.add(Message(id='m' + str(i), conversation_id='conv' + str(i), role='assistant', content='细胞核保存遗传信息。' if i == 1 else 'DNA 是遗传物质。'))
        await db.commit()
    yield
    await engine.dispose()
app = FastAPI(lifespan=lifespan)
for router in [auth.router, questions.router, flashcards.router, recitation.router, chat.router, gamification.router]:
    app.include_router(router)
@app.middleware('http')
async def log(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith('/api/'):
        trace.append(dict(path=request.url.path, method=request.method, status=response.status_code))
    return response
@app.get('/__test/health')
async def health():
    return dict(real_ai_calls=real_ai_calls, stub_replies=stub_replies, requests=trace)
if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=8107, log_level='warning')
