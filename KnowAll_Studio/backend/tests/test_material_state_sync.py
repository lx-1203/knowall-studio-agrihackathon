"""Versioned material state: real JWT + isolated SQL, no model calls."""
import asyncio
from types import SimpleNamespace
import httpx
import pytest
from fastapi import FastAPI, APIRouter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.database import Base, get_db
from app.models import User, Document, DocumentChunk
from app.core.auth import create_access_token
try:
    from app.api.material_state import router
except ModuleNotFoundError:
    router = APIRouter()

@pytest.fixture
async def state_client(tmp_path):
    engine = create_async_engine('sqlite+aiosqlite:///' + (tmp_path / 'state.sqlite').as_posix())
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn: await conn.run_sync(Base.metadata.create_all)
    async with sessions() as db:
        for user in ['a', 'b']:
            db.add(User(id=user, username=user, email=user+'@test.invalid', password_hash='not-used', is_active=True))
        db.add(Document(id='doc', filename='测试.md', file_type='md', status='ready', sha256='file', metadata_={'knowledge_owner_id':'a'}))
        db.add(DocumentChunk(id='chunk', doc_id='doc', chunk_index=0, text_content='原文', page_range='p1'))
        await db.commit()
    async def database():
        async with sessions() as db: yield db
    app = FastAPI(); app.include_router(router, prefix='/api/v1/learning')
    app.dependency_overrides[get_db] = database
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                headers={'Authorization':'Bearer '+create_access_token('a')}) as client:
        yield client, sessions
    await engine.dispose()

def url(kind='note'): return '/api/v1/learning/documents/doc/state/'+kind
def put(text='笔记', revision=0, operation='operation-one', kind='note', source=None):
    return dict(account_id='a', base_revision=revision, operation_id=operation,
                payload={'text':text} if kind=='note' else {'chunk_index':0,'chunk_id':'chunk'}, source_version=source)

async def test_first_write_read_clear_and_independent_progress(state_client):
    c,_=state_client
    empty=await c.get(url()); assert empty.status_code==200
    assert empty.json()['revision']==0 and empty.json()['payload'] is None
    first=await c.put(url(),json=put()); assert first.status_code==200
    assert first.json()['revision']==1
    assert (await c.get(url())).json()['payload']=={'text':'笔记'}
    progress=await c.put(url('text_progress'),json=put(kind='text_progress',source=empty.json()['current_source_version']))
    assert progress.status_code==200 and progress.json()['revision']==1
    cleared=await c.put(url(),json=put('',1,'clear-note'))
    assert cleared.status_code==200 and cleared.json()['payload']=={'text':''}

async def test_cas_and_durable_operation_replay(state_client):
    c,_=state_client
    await c.put(url(),json=put())
    conflict=await c.put(url(),json=put('晚到的离线编辑',0,'offline'))
    assert conflict.status_code==409
    assert conflict.json()['detail']['current']['payload']=={'text':'笔记'}
    await c.put(url(),json=put('新笔记',1,'second'))
    replay=await c.put(url(),json=put())
    assert replay.status_code==200 and replay.json()['revision']==2
    assert replay.json()['payload']=={'text':'新笔记'}
    assert (await c.put(url(),json=put('不同请求',0,'operation-one'))).status_code==409

async def test_concurrent_create_and_update_have_one_winner(state_client):
    c,_=state_client
    results=await asyncio.gather(c.put(url(),json=put('A',0,'race-a')),c.put(url(),json=put('B',0,'race-b')))
    assert sorted(r.status_code for r in results)==[200,409]
    assert (await c.get(url())).json()['revision']==1
    results=await asyncio.gather(c.put(url(),json=put('C',1,'race-c')),c.put(url(),json=put('D',1,'race-d')))
    assert sorted(r.status_code for r in results)==[200,409]
    assert (await c.get(url())).json()['revision']==2

async def test_progress_source_and_locator_validation(state_client):
    c,sessions=state_client
    head=(await c.get(url('text_progress'))).json()
    request=put(kind='text_progress',source=head['current_source_version'])
    assert (await c.put(url('text_progress'),json=request)).status_code==200
    async with sessions() as db:
        chunk=await db.get(DocumentChunk,'chunk'); chunk.text_content='新的原文'; await db.commit()
    assert (await c.get(url('text_progress'))).json()['stale'] is True
    bad=put(kind='text_progress',revision=1,operation='old-source',source=head['current_source_version'])
    assert (await c.put(url('text_progress'),json=bad)).status_code==409
    bad['source_version']=(await c.get(url('text_progress'))).json()['current_source_version']
    bad['payload']['chunk_id']='wrong'
    assert (await c.put(url('text_progress'),json=bad)).status_code==422

async def test_account_auth_and_payload_boundaries(state_client):
    c,_=state_client
    assert (await c.get(url(),headers={'Authorization':'Bearer '+create_access_token('b')})).status_code==404
    bad=put(); bad['account_id']='b'
    assert (await c.put(url(),json=bad)).status_code==403
    assert (await c.get(url(),headers={'Authorization':'Bearer broken'})).status_code==401
    for payload in [dict(text=123),dict(text='x',extra=True),dict(text='x'*100001)]:
        bad=put(); bad['payload']=payload
        assert (await c.put(url(),json=bad)).status_code==422
    bad=put(); bad['base_revision']=True
    assert (await c.put(url(),json=bad)).status_code==422
async def test_get_is_read_only_and_duplicate_concurrent_operation_has_one_revision(state_client):
    from app.models.material_state import MaterialState, MaterialStateOperation
    c,sessions=state_client
    await c.get(url())
    async with sessions() as db:
        assert (await db.execute(select(MaterialState))).scalars().all()==[]
        assert (await db.execute(select(MaterialStateOperation))).scalars().all()==[]
    results=await asyncio.gather(c.put(url(),json=put()),c.put(url(),json=put()))
    assert [r.status_code for r in results]==[200,200]
    assert (await c.get(url())).json()['revision']==1
