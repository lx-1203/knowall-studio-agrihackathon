"""Real signed identities/disposable SQL/pipelines; external AI/search explicitly simulated."""
import asyncio
from types import SimpleNamespace
import pytest
from app.models.user import User
from app.core.auth import create_access_token
from app.config import settings
from app.core.api_scheduler.client import _current_user_id
from tests.test_generation_state_flow import flow,terminal

@pytest.fixture
async def identity(flow,monkeypatch):
    import app.database as database
    monkeypatch.setattr(settings,'desktop_require_login',False)
    async with database.async_session() as db:
        for name in ('alice','bob','disabled'):
            db.add(User(id=name,username=name,email=name+'@example.invalid',password_hash='unused',is_active=name!='disabled'))
        await db.commit()
    observed=[]
    async def simulated(prompt,**kwargs):
        observed.append(_current_user_id.get());return await flow.chat(prompt,**kwargs)
    flow.model.side_effect=simulated
    headers={name:{'Authorization':'Bearer '+create_access_token(name)} for name in ('alice','bob','disabled','missing')}
    token=_current_user_id.set('local_user')
    try:yield SimpleNamespace(**vars(flow),headers=headers,observed=observed,factory=database.async_session)
    finally:
        await flow.agent.close();await flow.compiler.close();_current_user_id.reset(token)

async def create(identity,kind,account='alice'):
    path='/api/v1/books/create' if kind=='book' else '/api/v1/research/start'
    body={'title':'私有教材','source_doc_ids':['source'],'user_id':'forged'} if kind=='book' else {'topic':'私有研究','user_id':'forged'}
    r=await identity.client.post(path,json=body,headers=identity.headers.get(account,{}))
    assert r.status_code==200,r.text
    key=r.json()['id' if kind=='book' else 'task_id']
    data=await terminal(identity.client,status_path(kind,key),) if account=='local_user' else await signed_terminal(identity,kind,key,account)
    assert data['status']=='completed',data
    return key

def status_path(kind,key):return f'/api/v1/books/{key}' if kind=='book' else f'/api/v1/research/{key}/status'

async def signed_terminal(identity,kind,key,account):
    async with asyncio.timeout(5):
        while True:
            r=await identity.client.get(status_path(kind,key),headers=identity.headers.get(account,{}))
            assert r.status_code==200,r.text
            if r.json()['status'] in ('completed','error'):return r.json()
            await asyncio.sleep(.001)

@pytest.mark.parametrize('kind',['book','research'])
async def test_signed_create_uses_authenticated_owner_and_background_context(identity,kind):
    key=await create(identity,kind)
    item=identity.compiler.get_book(key) if kind=='book' else identity.agent.get_task(key)
    assert getattr(item,'user_id',None)=='alice'
    assert identity.observed and set(identity.observed)=={'alice'}

@pytest.mark.parametrize('viewer',['bob','local_user'])
@pytest.mark.parametrize('operation',['detail','export','delete','status','stream'])
async def test_foreign_generated_content_is_inaccessible(identity,viewer,operation):
    kind='book' if operation in ('detail','export','delete') else 'research'
    key=await create(identity,kind)
    path=f'/api/v1/books/{key}'+('/export' if operation=='export' else '') if kind=='book' else f'/api/v1/research/{key}/'+operation
    r=await identity.client.request('DELETE' if operation=='delete' else 'GET',path,headers=identity.headers.get(viewer,{}))
    assert r.status_code==404,r.text
    assert (identity.compiler.get_book(key) if kind=='book' else identity.agent.get_task(key)) is not None

@pytest.mark.parametrize('kind',['book','research'])
@pytest.mark.parametrize('viewer',['bob','local_user'])
async def test_lists_only_include_viewers_own_generated_items(identity,kind,viewer):
    await create(identity,kind,'alice');own=await create(identity,kind,viewer)
    path='/api/v1/books' if kind=='book' else '/api/v1/research/tasks'
    r=await identity.client.get(path,headers=identity.headers.get(viewer,{}))
    items=r.json()['books' if kind=='book' else 'tasks']
    assert [i['id' if kind=='book' else 'task_id'] for i in items]==[own]

@pytest.mark.parametrize('kind',['book','research'])
@pytest.mark.parametrize('account,code',[('invalid',401),('missing',401),('disabled',403)])
async def test_bad_presented_identity_does_not_create_or_call_model(identity,kind,account,code):
    headers=identity.headers.get(account,{'Authorization':'Bearer invalid'})
    path='/api/v1/books/create' if kind=='book' else '/api/v1/research/start'
    r=await identity.client.post(path,headers=headers,json={'title':'不该创建'} if kind=='book' else {'topic':'不该创建'})
    assert r.status_code==code,r.text
    assert not identity.compiler._books and not identity.agent._tasks and not identity.observed

@pytest.mark.parametrize('kind',['book','research'])
async def test_login_mode_requires_identity_for_create_and_list(identity,monkeypatch,kind):
    monkeypatch.setattr(settings,'desktop_require_login',True)
    path='/api/v1/books/create' if kind=='book' else '/api/v1/research/start'
    r=await identity.client.post(path,json={'title':'不该创建'} if kind=='book' else {'topic':'不该创建'})
    assert r.status_code==401,r.text
    r=await identity.client.get('/api/v1/books' if kind=='book' else '/api/v1/research/tasks')
    assert r.status_code==401,r.text
    assert not identity.observed

@pytest.mark.parametrize('kind',['book','research'])
async def test_local_anonymous_generation_ignores_ambient_signed_context(identity,kind):
    _current_user_id.set('bob')
    key=await create(identity,kind,'local_user')
    item=identity.compiler.get_book(key) if kind=='book' else identity.agent.get_task(key)
    assert getattr(item,'user_id',None)=='local_user'
    assert set(identity.observed)=={'local_user'}

@pytest.mark.parametrize('kind',['book','research'])
async def test_service_explicit_owner_is_applied_inside_background_job(identity,kind):
    _current_user_id.set('bob')
    item=await identity.agent.start_research('私有研究',user_id='alice') if kind=='research' else await identity.compiler.create_book('私有教材',source_doc_ids=['source'],user_id='alice')
    async with asyncio.timeout(5):
        while item.status.value not in ('completed','error'):await asyncio.sleep(.001)
    assert item.status.value=='completed' and getattr(item,'user_id',None)=='alice'
    assert set(identity.observed)=={'alice'} and _current_user_id.get()=='bob'

@pytest.mark.parametrize('method,path',[('GET','/api/v1/books'),('GET','/api/v1/books/unknown'),
    ('GET','/api/v1/books/unknown/export'),('DELETE','/api/v1/books/unknown'),
    ('GET','/api/v1/research/tasks'),('GET','/api/v1/research/unknown/status'),('GET','/api/v1/research/unknown/stream')])
async def test_invalid_token_cannot_fall_back_to_anonymous_on_read_or_delete(identity,method,path):
    r=await identity.client.request(method,path,headers={'Authorization':'Bearer invalid'})
    assert r.status_code==401,r.text
    assert not identity.observed

@pytest.mark.parametrize('kind',['book','research'])
@pytest.mark.parametrize('authorization',['Basic explicit-simulated-invalid','Bearer '])
async def test_malformed_authorization_cannot_become_local_creation(identity,kind,authorization):
    path='/api/v1/books/create' if kind=='book' else '/api/v1/research/start'
    r=await identity.client.post(path,headers={'Authorization':authorization},json={'title':'不该创建'} if kind=='book' else {'topic':'不该创建'})
    assert r.status_code==401,r.text
    assert not identity.compiler._books and not identity.agent._tasks and not identity.observed
