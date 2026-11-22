"""Native parser and real temporary disk SQL; provider bytes are explicitly simulated."""
import asyncio,json
from datetime import datetime
from types import SimpleNamespace
import httpx,pytest
from sqlalchemy import select,text
from sqlalchemy.ext.asyncio import AsyncSession,async_sessionmaker,create_async_engine
from app.api import reading
from app.database import Base
from app.models import ReadingConversionCache

ARTICLE='世界很大，学习知识帮助我们理解世界。'
def delta(content='完整 world',finish=None):
    return 'data: '+json.dumps({'choices':[{'delta':{'content':content},'finish_reason':finish}]},ensure_ascii=False)+'\n\n'

async def parse(monkeypatch,body,status=200):
    original=httpx.AsyncClient;calls=[]
    async def simulation(req):
        assert str(req.url)=='https://api.deepseek.com/v1/chat/completions'
        calls.append(json.loads(req.content));return httpx.Response(status,text=body,headers={'content-type':'text/event-stream'})
    monkeypatch.setattr(reading.httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(simulation),**kw))
    results=[]
    try:
        async for event in reading.stream_deepseek(ARTICLE,[],[]):results.append(json.loads(event[6:]))
    except Exception as exc:return results,calls,exc
    return results,calls,None

@pytest.mark.parametrize('tail',[
    '', 'data: {bad-json}\n\n', 'data: '+json.dumps({'error':{'message':'explicit simulated outage'}})+'\n\n',
    delta('', 'length')+'data: [DONE]\n\n',delta('', 'content_filter')+'data: [DONE]\n\n',
    delta('', 'unknown')+'data: [DONE]\n\n'])
async def test_native_incomplete_or_error_cannot_emit_success(monkeypatch,tail):
    events,calls,error=await parse(monkeypatch,delta('半截 partial')+tail)
    assert not any(e.get('done') for e in events),events
    assert error is not None,'Provider failure/EOF must reach the existing dictionary fallback'
    assert len(calls)==1 and calls[0]['stream'] is True

@pytest.mark.parametrize('body',['data: [DONE]\n\n',delta('','stop')+'data: [DONE]\n\n',delta('   ','stop')])
async def test_native_empty_completed_output_is_not_a_success(monkeypatch,body):
    events,calls,error=await parse(monkeypatch,body)
    assert error is not None and not any(e.get('done') for e in events),events

@pytest.mark.parametrize('body',[
    delta('完整 world')+'data: [DONE]\n\n',delta('完整 world','stop'),
    delta('完整 world')+delta('','stop')+'data: {"choices":[],"usage":{"total_tokens":18}}\n\ndata: [DONE]\n\n',
    ': heartbeat\r\n\r\n'+delta('完整 world','stop').replace('data: ','data:').replace('\n','\r\n'),
    'data: {"choices":[{"delta":{"role":"assistant"},"finish_reason":null}]}\n\n'+delta('完整 world','stop'),
])
async def test_native_valid_completion_role_usage_and_sse_spacing(monkeypatch,body):
    events,calls,error=await parse(monkeypatch,body)
    assert error is None,error
    terminal=[e for e in events if e.get('done')];assert len(terminal)==1
    assert terminal[0]['result']=='完整 world' and ''.join(e.get('token','') for e in events)=='完整 world'

async def test_native_http_error_remains_explicit(monkeypatch):
    events,calls,error=await parse(monkeypatch,'explicit simulated outage',503)
    assert not any(e.get('done') for e in events) and events[0]['error']=='API 503'

@pytest.mark.parametrize('finish',['tool_calls','insufficient_system_resource','aborted'])
async def test_documented_non_text_completion_reasons_are_not_success(monkeypatch,finish):
    events,calls,error=await parse(monkeypatch,delta('半截 partial')+delta('',finish)+'data: [DONE]\n\n')
    assert error is not None and not any(e.get('done') for e in events)

@pytest.mark.parametrize('token',[42,['fragment'],{'fragment':'value'}])
async def test_invalid_delta_token_cannot_be_skipped_into_partial_success(monkeypatch,token):
    bad='data: '+json.dumps({'choices':[{'delta':{'content':token},'finish_reason':'stop'}]})+'\n\n'
    events,calls,error=await parse(monkeypatch,delta('半截 partial')+bad+'data: [DONE]\n\n')
    assert error is not None and not any(e.get('done') for e in events)

async def test_network_read_failure_after_partial_does_not_emit_success(monkeypatch):
    original=httpx.AsyncClient
    class Broken(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield delta('半截 partial').encode()
            raise httpx.ReadError('explicit simulated transport disconnect')
    monkeypatch.setattr(reading.httpx,'AsyncClient',lambda **kw:original(
        transport=httpx.MockTransport(lambda req:httpx.Response(200,stream=Broken(),headers={'content-type':'text/event-stream'})),**kw))
    values=[]
    with pytest.raises(httpx.ReadError):
        async for event in reading.stream_deepseek(ARTICLE,[],[]):values.append(json.loads(event[6:]))
    assert values==[{'token':'半截 partial'}]

@pytest.fixture
async def disk(tmp_path):
    path=tmp_path/'reading.sqlite';engine=create_async_engine('sqlite+aiosqlite:///'+path.as_posix(),connect_args={'timeout':10})
    async with engine.begin() as c:await c.run_sync(ReadingConversionCache.__table__.create)
    factory=async_sessionmaker(engine,expire_on_commit=False)
    yield SimpleNamespace(factory=factory,engine=engine,path=path)
    await engine.dispose()

def force_legacy_misses(monkeypatch,count):
    original=AsyncSession.get;gate=asyncio.Event();seen=0
    async def get(self,entity,*args,**kwargs):
        nonlocal seen
        value=await original(self,entity,*args,**kwargs)
        if entity is ReadingConversionCache and value is None:
            seen+=1
            if seen==count:gate.set()
            await asyncio.wait_for(gate.wait(),3)
        return value
    monkeypatch.setattr(AsyncSession,'get',get)

@pytest.mark.parametrize('count',[2,8])
async def test_real_sql_concurrent_misses_save_one_complete_pair(disk,monkeypatch,count):
    force_legacy_misses(monkeypatch,count)
    async def save(i):
        async with disk.factory() as db:await reading.cache_set('same','result-'+str(i),[{'cn':'词','en':str(i)}],db)
    outcomes=await asyncio.gather(*(save(i) for i in range(count)),return_exceptions=True)
    assert all(v is None for v in outcomes),outcomes
    async with disk.factory() as db:
        rows=(await db.scalars(select(ReadingConversionCache))).all();assert len(rows)==1
        assert rows[0].result=='result-'+json.loads(rows[0].vocabulary)[0]['en']

async def test_real_sql_different_keys_and_refresh_survive_reopen(disk):
    async def save(key,value):
        async with disk.factory() as db:await reading.cache_set(key,value,[{'cn':value,'en':value}],db)
    await asyncio.gather(save('a','first'),save('b','other'))
    await save('a','refresh')
    async with disk.factory() as db:
        assert await reading.cache_get('a',db)=={'result':'refresh','vocabulary':[{'cn':'refresh','en':'refresh'}]}
        assert (await reading.cache_get('b',db))['result']=='other'
    await disk.engine.dispose()
    other=create_async_engine('sqlite+aiosqlite:///'+disk.path.as_posix())
    try:
        async with async_sessionmaker(other)() as db:assert (await reading.cache_get('a',db))['result']=='refresh'
    finally:await other.dispose()

async def test_real_sql_failure_is_not_silently_a_success(disk):
    async with disk.engine.begin() as c:await c.execute(text('DROP TABLE reading_conversion_cache'))
    async with disk.factory() as db:
        with pytest.raises(Exception,match='no such table'):await reading.cache_set('a','value',[],db)

@pytest.mark.parametrize('name',['sqlite','mysql','mariadb'])
async def test_atomic_cache_statement_compiles_for_supported_dialect(name):
    from sqlalchemy.dialects import sqlite,mysql
    dialect=sqlite.dialect() if name=='sqlite' else mysql.dialect();dialect.name=name
    statements=[]
    class Session:
        def get_bind(self):return SimpleNamespace(dialect=dialect)
        async def execute(self,statement):statements.append(str(statement.compile(dialect=dialect)))
        async def commit(self):pass
        async def get(self,*args):raise AssertionError('Atomic dialect must not read before write')
    await reading.cache_set('key','value',[],Session())
    assert len(statements)==1
    assert ('ON CONFLICT' if name=='sqlite' else 'ON DUPLICATE KEY UPDATE') in statements[0]
