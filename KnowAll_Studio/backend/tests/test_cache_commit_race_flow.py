"""Disposable disk SQLite, real multi-session writes; barriers control scheduling only."""
import asyncio,importlib,sys,json
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
import pytest
from sqlalchemy import select,event,text
from sqlalchemy.ext.asyncio import AsyncSession,async_sessionmaker,create_async_engine
from sqlalchemy.exc import IntegrityError
from app.database import Base
from app.models import APICache,APIQuota,APICallLog
from app.core.api_scheduler.cache import APICacheManager

class FirstMergeMiss:
    """Pause legacy ORM merge AFTER its actual SQL read, before any INSERT."""
    def __init__(self,parties):
        self.barrier=asyncio.Barrier(parties);self.reads=0;self.parties=parties
    def session_class(self):
        gate=self
        class GatedSession(AsyncSession):
            async def merge(self,instance,*args,**kwargs):
                result=await super().merge(instance,*args,**kwargs)
                if isinstance(instance,APICache) and gate.reads<gate.parties:
                    self.info[instance.cache_key]=True;gate.reads+=1
                    await asyncio.wait_for(gate.barrier.wait(),5)
                return result
        return GatedSession

@pytest.fixture
async def store(tmp_path,monkeypatch):
    cache=importlib.import_module('app.core.api_scheduler.cache')
    engine=create_async_engine('sqlite+aiosqlite:///'+(tmp_path/'cache-race.sqlite').as_posix(),connect_args={'timeout':10})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all,tables=[APICache.__table__,APIQuota.__table__,APICallLog.__table__])
    factory=async_sessionmaker(engine,expire_on_commit=False)
    monkeypatch.setattr(cache,'async_session',factory)
    try:yield SimpleNamespace(engine=engine,factory=factory,cache=cache,path=tmp_path/'cache-race.sqlite')
    finally:await engine.dispose()

async def values(store):
    async with store.factory() as db:return (await db.scalars(select(APICache))).all()

async def write(manager,key,number):
    await manager.set(key,'中文响应-'+str(number),f'model-{number}',number,number+1,ttl_days=number+2)

@pytest.mark.parametrize('parties',[2,8])
async def test_same_key_first_writes_from_independent_managers_all_commit(store,monkeypatch,parties):
    gate=FirstMergeMiss(parties)
    monkeypatch.setattr(store.cache,'async_session',async_sessionmaker(store.engine,class_=gate.session_class(),expire_on_commit=False))
    results=await asyncio.gather(*(write(APICacheManager(),'a'*64,i) for i in range(parties)),return_exceptions=True)
    assert not [repr(x) for x in results if isinstance(x,BaseException)],results
    rows=await values(store);assert len(rows)==1
    row=rows[0];n=int(row.model_used.split('-')[-1])
    assert (row.response_content,row.tokens_input,row.tokens_output,row.ttl_days)==('中文响应-'+str(n),n,n+1,n+2)
    assert await APICacheManager().get('a'*64)==row.response_content

@pytest.mark.parametrize('parties',[2,8])
async def test_distinct_keys_parallel_remain_independent(store,parties):
    await asyncio.gather(*(write(APICacheManager(),str(i).zfill(64),i) for i in range(parties)))
    assert len(await values(store))==parties

async def test_sequential_refresh_updates_full_metadata_and_reopens_disk(store):
    manager=APICacheManager();await write(manager,'b'*64,1)
    old=(await values(store))[0].created_at
    async with store.factory() as db:
        row=await db.get(APICache,'b'*64);row.created_at=datetime.now(timezone.utc).replace(tzinfo=None)-timedelta(days=50);await db.commit()
    await write(manager,'b'*64,9)
    row=(await values(store))[0]
    assert row.created_at>=old and row.ttl_days==11 and row.tokens_input==9 and row.tokens_output==10
    engine=create_async_engine('sqlite+aiosqlite:///'+store.path.as_posix())
    try:
        async with async_sessionmaker(engine)() as db:
            row=await db.get(APICache,'b'*64);assert row.response_content=='中文响应-9' and row.model_used=='model-9'
    finally:await engine.dispose()

@pytest.mark.parametrize('refresh',[False,True])
async def test_expired_read_cannot_delete_concurrent_refresh(store,monkeypatch,refresh):
    manager=APICacheManager();await write(manager,'c'*64,1)
    async with store.factory() as db:
        row=await db.get(APICache,'c'*64);row.created_at=datetime.now(timezone.utc).replace(tzinfo=None)-timedelta(days=90);await db.commit()
    read=asyncio.Event();resume=asyncio.Event();claimed=False
    class PausedRead(AsyncSession):
        async def execute(self,statement,*args,**kwargs):
            nonlocal claimed
            result=await super().execute(statement,*args,**kwargs)
            if getattr(statement,'is_select',False) and not claimed:
                claimed=True;read.set();await asyncio.wait_for(resume.wait(),5)
            return result
    monkeypatch.setattr(store.cache,'async_session',async_sessionmaker(store.engine,class_=PausedRead,expire_on_commit=False))
    task=asyncio.create_task(manager.get_entry('c'*64))
    try:
        await asyncio.wait_for(read.wait(),5)
        if refresh:await write(APICacheManager(),'c'*64,5)
    finally:resume.set()
    assert await task is None
    rows=await values(store)
    assert len(rows)==int(refresh)
    if refresh:assert await manager.get('c'*64)=='中文响应-5'

async def test_database_failure_propagates_without_claiming_saved(store):
    async with store.engine.begin() as conn:await conn.execute(text('DROP TABLE api_cache'))
    with pytest.raises(Exception,match='no such table'):await write(APICacheManager(),'d'*64,1)

async def test_invalid_payload_constraint_not_hidden(store):
    with pytest.raises(IntegrityError):await APICacheManager().set('e'*64,None,'model',7,11)
    assert not await values(store)

async def test_model_success_on_simultaneous_miss_never_falls_back_for_cache_collision(store,monkeypatch):
    from app.core.api_scheduler.client import UnifiedAPIClient,GenerationConfig,RequestContext,TaskType
    from app.core.api_scheduler.adapters.base import BaseModelAdapter,AdapterResponse
    rate=importlib.import_module('app.core.api_scheduler.rate_limiter');client=importlib.import_module('app.core.api_scheduler.client')
    monkeypatch.setattr(rate,'async_session',store.factory);monkeypatch.setattr(client,'rate_limiter',rate.RateLimiter())
    gate=FirstMergeMiss(2);monkeypatch.setattr(store.cache,'async_session',async_sessionmaker(store.engine,class_=gate.session_class(),expire_on_commit=False))
    calls=[];barrier=asyncio.Barrier(2)
    class Simulated(BaseModelAdapter):
        async def chat_completion(self,messages,config):
            calls.append(self.model_name)
            if self.model_name=='primary':await asyncio.wait_for(barrier.wait(),5)
            return AdapterResponse('明确模拟成功','明确模拟成功',self.model_name,7,11)
        def count_tokens(self,text):return len(text)//4
    # Independent clients intentionally do not share an in-process generation
    # lock. Preserve the actual multi-client cache commit collision regression.
    api=UnifiedAPIClient();other=UnifiedAPIClient()
    for name in ('primary','fallback'):
        adapter=Simulated('explicit-simulated-key');adapter.model_name=name;api._adapters[name]=adapter;other._adapters[name]=adapter
    def request(selected=api):return selected.generate(TaskType.CHAT,[{'role':'user','content':'同账号同输入'}],'race','同账号同输入',
        GenerationConfig(model='primary',fallback_models=['fallback'],max_retries=0),RequestContext(user_id='alice'))
    results=await asyncio.gather(request(),request(other),return_exceptions=True)
    assert all(not isinstance(r,BaseException) and r.model_used=='primary' for r in results),results
    assert calls==['primary','primary'],calls
    async with store.factory() as db:
        quota=(await db.scalars(select(APIQuota))).one();logs=(await db.scalars(select(APICallLog))).all()
    assert quota.used_today==quota.total_used==36 and len(logs)==2 and all(r.success for r in logs)
    cached=await request();assert cached.from_cache and calls==['primary','primary']

async def test_two_independent_python_processes_commit_same_disk_cache_key(store):
    # Each subprocess owns its engine/session. Legacy merge pauses after real
    # SELECT until both processes observed the miss; native UPSERT needs no gate.
    script=r'''
import asyncio,sys,json,os
from pathlib import Path
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker,AsyncSession
from app.core.api_scheduler import cache
from app.models import APICache
async def run():
    path,number=sys.argv[1:];number=int(number)
    engine=create_async_engine('sqlite+aiosqlite:///'+path,connect_args={'timeout':10})
    class Gated(AsyncSession):
        async def merge(self,obj,*args,**kwargs):
            result=await super().merge(obj,*args,**kwargs)
            Path(path+str(number)+'.ready').write_text('real SELECT completed')
            async with asyncio.timeout(10):
                while not all(Path(path+str(i)+'.ready').exists() for i in range(2)):await asyncio.sleep(.005)
            return result
    cache.async_session=async_sessionmaker(engine,class_=Gated,expire_on_commit=False)
    try:
        await cache.APICacheManager().set('f'*64,'process-'+str(number),'process-model-'+str(number),7,11)
        print(json.dumps({'Process':os.getpid(),'Success':True,'Source':path}))
    finally:await engine.dispose()
asyncio.run(run())
'''
    jobs=[await asyncio.create_subprocess_exec(sys.executable,'-c',script,str(store.path),str(i),stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE) for i in range(2)]
    try:outputs=await asyncio.wait_for(asyncio.gather(*(j.communicate() for j in jobs)),30)
    finally:
        for j in jobs:
            if j.returncode is None:j.kill();await j.wait()
    assert all(j.returncode==0 for j in jobs),[(j.returncode,o[1].decode(errors='replace')) for j,o in zip(jobs,outputs)]
    reports=[json.loads(o[0].decode().splitlines()[-1]) for o in outputs]
    assert len({r['Process'] for r in reports})==2 and len(await values(store))==1

async def test_mysql_statement_compiles_unique_key_update_with_bound_unicode_payload(monkeypatch):
    # No MySQL server: inspect actual generated SQL/bind parameters only.
    from sqlalchemy.dialects import mysql
    cache=importlib.import_module('app.core.api_scheduler.cache');statements=[]
    class CompileSession:
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        def get_bind(self):return SimpleNamespace(dialect=mysql.dialect())
        async def execute(self,statement):statements.append(statement.compile(dialect=mysql.dialect()))
        async def merge(self,entry):pass
        async def commit(self):pass
    monkeypatch.setattr(cache,'async_session',CompileSession)
    await APICacheManager().set('m'*64,"中文 ' ; DROP TABLE api_cache; --",'model',7,11,ttl_days=5)
    assert len(statements)==1,'MySQL cache write must be one atomic SQL statement'
    compiled=statements[0];sql=str(compiled)
    assert 'ON DUPLICATE KEY UPDATE' in sql and '中文' not in sql and 'DROP TABLE' not in sql
    assert "中文 ' ; DROP TABLE api_cache; --" in compiled.params.values()
