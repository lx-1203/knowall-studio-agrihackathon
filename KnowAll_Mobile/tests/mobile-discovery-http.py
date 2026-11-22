"""Isolated real JWT/SQLite discovery routes; no live model/vector requests."""
import os
import secrets
import sys
import tempfile
from pathlib import Path
from contextlib import asynccontextmanager
root = Path(tempfile.mkdtemp(prefix='knowall-discovery-01a12efd-'))
os.environ['DATABASE_URL'] = 'sqlite+aiosqlite:///' + (root/'test.sqlite').as_posix()
os.environ['JWT_SECRET'] = secrets.token_hex(32)
os.environ['KNOWALL_DATA_ROOT'] = str(root)
os.environ['DEBUG'] = 'false'
sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'KnowAll_Studio/backend'))
from fastapi import FastAPI, Request
from app.database import Base, engine, async_session
from app.models import User, Document, DocumentChunk, KnowledgeTree, Deck, Flashcard
from app.core.auth import hash_password
from app.core.api_scheduler import api_client
from app.core.memory.retrieval import card_retriever
from app.api import auth, documents, search, knowledge, flashcards, material_state
external_calls = 0
card_sql_fallbacks = 0
requests = []
async def forbid_model(*args, **kwargs):
    global external_calls
    external_calls += 1
    raise AssertionError('Live model forbidden')
api_client.generate = forbid_model
def forbid_vector(*args, **kwargs):
    global external_calls
    external_calls += 1
    raise AssertionError('Document keyword search must stay in SQL')
search.search = forbid_vector
search.retrieve_context = forbid_vector
async def vector_unavailable(*args, **kwargs):
    global card_sql_fallbacks
    card_sql_fallbacks += 1
    raise RuntimeError('Fixture explicitly has no vector service')
card_retriever.search = vector_unavailable
@asynccontextmanager
async def lifespan(app):
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_session() as db:
        for uid in ['discovery-user', 'other-user']:
            db.add(User(id=uid, username=uid, email=uid+'@example.invalid', password_hash=hash_password('LocalDiscovery_2026!'), is_active=True))
            doc_id = 'discovery-doc' if uid=='discovery-user' else 'other-doc'
            db.add(Document(id=doc_id, filename='细胞生物学 · 阅读笔记.md' if uid=='discovery-user' else '别人的细胞核私密资料.md', file_type='md', status='ready', sha256=doc_id, page_count=3, metadata_={'knowledge_owner_id':uid,'subject':'生物'}))
            db.add(DocumentChunk(id=doc_id+'-c1', doc_id=doc_id, chunk_index=0, text_content='# 细胞的结构\n\n细胞膜保护内部环境。', page_range='1'))
            db.add(DocumentChunk(id=doc_id+'-c2', doc_id=doc_id, chunk_index=1, text_content='# 细胞核与遗传\n\n细胞核保存遗传信息。DNA通过复制与转录传递信息。', page_range='2–3'))
            db.add(Deck(id=uid+'-deck', name='细胞知识', user_id=uid, card_count=1))
            db.add(Flashcard(id=uid+'-card', deck_id=uid+'-deck', card_type='qa', front='细胞核有什么作用？' if uid=='discovery-user' else '其他账号的细胞核卡', back='保存遗传信息。'))
            db.add(KnowledgeTree(id=uid+'-tree', name='细胞结构与生命活动' if uid=='discovery-user' else '其他账号的私密知识树', doc_ids=[doc_id], tree_data={'tree':{'nodes':[{'id':'n1','label':'细胞结构','summary':'从整体到各个组成部分。','children':[{'id':'n2','label':'细胞膜','summary':'控制物质交换。','children':[]}]}]}}))
        await db.commit()
    yield
    await engine.dispose()
app=FastAPI(lifespan=lifespan)
for module in [auth,documents,search,knowledge,flashcards,material_state]:
    app.include_router(module.router, prefix='/api/v1/learning' if module is material_state else '')
@app.middleware('http')
async def trace(request:Request,call_next):
    response=await call_next(request)
    if request.url.path.startswith('/api/'):
        requests.append({'path':request.url.path,'method':request.method,'status':response.status_code})
    return response
@app.get('/__test/health')
async def health():
    return {'external_calls':external_calls,'card_sql_fallbacks':card_sql_fallbacks,'requests':requests}
if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=8110,log_level='warning')
