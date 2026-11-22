"""Real localhost HTTP/SQL/native httpx; all external AI responses explicitly simulated."""
import asyncio,json,hashlib,os,socket
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
import httpx,pytest
from fastapi import FastAPI,Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from app.api import reading
from app.database import get_db
from app.models import ReadingConversionCache
from tests.test_reading_native_stream_flow import disk,delta,ARTICLE,force_legacy_misses

@asynccontextmanager
async def server(app):
    import uvicorn
    listener=socket.socket();listener.bind(('127.0.0.1',0))
    uv=uvicorn.Server(uvicorn.Config(app,log_level='critical',lifespan='off',timeout_graceful_shutdown=2))
    job=asyncio.create_task(uv.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(5):
            while not uv.started:
                if job.done():await job;raise AssertionError('server exited before startup')
                await asyncio.sleep(.01)
        async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{listener.getsockname()[1]}',timeout=10) as client:yield client
    finally:
        uv.should_exit=True
        try:await asyncio.wait_for(job,5)
        finally:listener.close()

@pytest.fixture
def evidence(request,tmp_path):
    d={'Test':request.node.nodeid,'Start':datetime.now().astimezone().isoformat(),'ExternalAI':'explicit simulation only',
       'ProviderRequests':[],'BackendSamples':[],'Scope':'local reading router; no signed account isolation claimed'}
    yield d
    d['End']=datetime.now().astimezone().isoformat();root=Path(__file__).resolve().parents[1]
    d['SourceHashes']={p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in ('app/api/reading.py','app/models/__init__.py','app/database.py')}
    out=Path(os.environ.get('KNOWALL_READING_NATIVE_EVIDENCE_DIR',str(tmp_path/'network')));out.mkdir(parents=True,exist_ok=True)
    (out/('real-reading-'+hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:16]+'.json')).write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf8')

def record(d,r):d['BackendSamples'].append({'Origin':str(r.request.url),'Method':r.request.method,'Status':r.status_code,'Body':r.text})
def events(r):
    assert r.status_code==200,r.text
    return [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith('data: ')]
async def cache_rows(disk):
    async with disk.factory() as db:return (await db.scalars(select(ReadingConversionCache))).all()
def app_for(disk):
    app=FastAPI();app.include_router(reading.router)
    async def database():
        async with disk.factory() as db:yield db
    app.dependency_overrides[get_db]=database;return app

@asynccontextmanager
async def endpoints(disk,monkeypatch,evidence,mode,count=1):
    provider=FastAPI();arrived=asyncio.Event();release=asyncio.Event();closed=asyncio.Event();first_token=asyncio.Event()
    @provider.post('/v1/chat/completions')
    async def simulation(req:Request):
        payload=await req.json();assert req.headers['authorization']=='Bearer explicit-simulated-reading-key'
        evidence['ProviderRequests'].append({'Origin':str(req.url),'Payload':payload,'Simulation':True})
        index=len(evidence['ProviderRequests'])
        if index>=count:arrived.set()
        await asyncio.wait_for(arrived.wait(),5)
        if mode=='http-error':return StreamingResponse(iter(['explicit simulated outage']),status_code=503)
        if not payload.get('stream'):
            return {'choices':[{'message':{'content':'完整 world'}}]}
        bodies={
            'complete':delta('完整 world')+delta('','stop')+'data: [DONE]\n\n',
            'stop-only':delta('完整 world','stop'),
            'eof':delta('半截 partial'),
            'malformed':delta('半截 partial')+'data: {broken}\n\n',
            'error':delta('半截 partial')+'data: {"error":{"message":"explicit simulated outage"}}\n\n',
            'length':delta('半截 partial','length')+'data: [DONE]\n\n',
            'filter':delta('半截 partial','content_filter')+'data: [DONE]\n\n',
            'empty':'data: [DONE]\n\n',
        }
        async def chunks():
            try:
                if mode in ('disconnect','held-complete') and index==1:
                    yield delta('半截 partial' if mode=='disconnect' else '完整 world').encode()
                    first_token.set();await release.wait()
                    if mode=='held-complete':yield (delta('','stop')+'data: [DONE]\n\n').encode()
                else:
                    raw=bodies.get(mode,bodies['complete']).encode()
                    # Deliberately split UTF-8 and JSON across transport chunks.
                    for offset in range(0,len(raw),7):yield raw[offset:offset+7]
            finally:
                if index==1:closed.set()
        return StreamingResponse(chunks(),media_type='text/event-stream')
    monkeypatch.setattr(reading,'DEEPSEEK_API_KEY','explicit-simulated-reading-key')
    async with server(provider) as remote:
        monkeypatch.setattr(reading,'DEEPSEEK_API_URL',str(remote.base_url).rstrip('/')+'/v1/chat/completions')
        async with server(app_for(disk)) as backend:
            try:yield SimpleNamespace(client=backend,release=release,closed=closed,first_token=first_token)
            finally:release.set()

@pytest.mark.parametrize('mode',['eof','malformed','error','length','filter','empty','http-error'])
async def test_actual_native_bad_stream_gets_full_dictionary_not_partial_cache(disk,monkeypatch,evidence,mode):
    async with endpoints(disk,monkeypatch,evidence,mode) as net:
        request={'text':ARTICLE,'level':2}
        r=await net.client.post('/api/v1/reading/convert/stream',json=request);record(evidence,r)
        terminal=[e for e in events(r) if e.get('done')];assert len(terminal)==1
        final=terminal[0];assert final.get('source')=='dictionary',final
        assert final['result'] and 'partial' not in final['result']
        rows=await cache_rows(disk);assert len(rows)==1 and rows[0].result==final['result']
        assert json.loads(rows[0].vocabulary)==final['vocabulary']
        r=await net.client.post('/api/v1/reading/convert/stream',json=request);record(evidence,r)
        assert events(r)==[{'cached':True,'result':final['result'],'vocabulary':final['vocabulary'],'max_new':3}]
        assert len(evidence['ProviderRequests'])==1
        evidence.update({'Mode':mode,'FinalSource':'dictionary','CacheRows':1,'RepeatProviderCalls':0,'Database':str(disk.path)})

@pytest.mark.parametrize('mode',['complete','stop-only'])
async def test_actual_native_tokens_complete_cache_refresh_and_learning_state(disk,monkeypatch,evidence,mode):
    async with endpoints(disk,monkeypatch,evidence,mode) as net:
        request={'text':ARTICLE,'level':1}
        r=await net.client.post('/api/v1/reading/convert/stream',json=request);record(evidence,r)
        data=events(r);assert data[0]['token']=='完整 world' and data[-1]['done'] and data[-1]['result']=='完整 world'
        r=await net.client.post('/api/v1/reading/convert',json=request);record(evidence,r);assert r.json()['source']=='cache'
        assert len(evidence['ProviderRequests'])==1
        for change in ({'skip_cache':True},{'level':3},{'seen_words':[{'cn':'世界','en':'world'}]},{'known_words':[{'cn':'学习','en':'study'}]}):
            r=await net.client.post('/api/v1/reading/convert/stream',json={**request,**change});record(evidence,r)
            assert events(r)[-1]['done'] and events(r)[-1]['result']=='完整 world'
        assert len(evidence['ProviderRequests'])==5 and len(await cache_rows(disk))==4
        evidence.update({'Mode':mode,'FinalSource':'deepseek','CacheRows':4,'RepeatProviderCalls':0,'Database':str(disk.path)})

@pytest.mark.parametrize('kind',['stream','json','mixed'])
async def test_actual_native_two_concurrent_routes_commit_complete_cache(disk,monkeypatch,evidence,kind):
    force_legacy_misses(monkeypatch,2)
    async with endpoints(disk,monkeypatch,evidence,'complete',count=2) as net:
        async def convert(i):
            stream=kind=='stream' or kind=='mixed' and i==0
            r=await net.client.post('/api/v1/reading/convert'+('/stream' if stream else ''),json={'text':ARTICLE});record(evidence,r)
            value=events(r)[-1] if stream else r.json()
            assert value['result']=='完整 world' and value.get('source')!='dictionary',value
            return value
        results=await asyncio.gather(convert(0),convert(1))
        rows=await cache_rows(disk);assert len(rows)==1 and rows[0].result=='完整 world'
        assert json.loads(rows[0].vocabulary)==results[0]['vocabulary']==results[1]['vocabulary']
        assert len(evidence['ProviderRequests'])==2
        r=await net.client.post('/api/v1/reading/convert',json={'text':ARTICLE});record(evidence,r);assert r.json()['source']=='cache'
        evidence.update({'Mode':'concurrent-'+kind,'FinalSource':'deepseek','CacheRows':1,'CompleteConversions':2,'ProviderDeduplicated':False,'RepeatProviderCalls':0,'Database':str(disk.path)})

async def test_actual_client_disconnect_does_not_cache_partial_and_retry_recovers(disk,monkeypatch,evidence):
    async with endpoints(disk,monkeypatch,evidence,'disconnect') as net:
        async with net.client.stream('POST','/api/v1/reading/convert/stream',json={'text':ARTICLE}) as response:
            assert response.status_code==200
            async for line in response.aiter_lines():
                if line.startswith('data: '):
                    event=json.loads(line[6:]);assert event['token']=='半截 partial'
                    evidence['BackendSamples'].append({'Origin':str(response.request.url),'Method':'POST','Status':200,'Body':line,'ClientDisconnectedBeforeTerminal':True});break
        await asyncio.wait_for(net.closed.wait(),3)
        assert not await cache_rows(disk)
        r=await net.client.post('/api/v1/reading/convert/stream',json={'text':ARTICLE});record(evidence,r)
        assert events(r)[-1]['done'] and events(r)[-1]['result']=='完整 world'
        assert len(await cache_rows(disk))==1 and len(evidence['ProviderRequests'])==2
        evidence.update({'Mode':'disconnect','FinalSource':'deepseek','CacheRows':1,'PartialNotCached':True,'RemoteStreamClosed':True,'ActualExternalBillingKnown':False,'Database':str(disk.path)})

async def test_actual_live_token_precedes_cache_and_terminal_follows_commit(disk,monkeypatch,evidence):
    async with endpoints(disk,monkeypatch,evidence,'held-complete') as net:
        received=[]
        async with net.client.stream('POST','/api/v1/reading/convert/stream',json={'text':ARTICLE}) as response:
            async for line in response.aiter_lines():
                if not line.startswith('data: '):continue
                event=json.loads(line[6:]);received.append(event)
                if 'token' in event:
                    assert event['token']=='完整 world' and not await cache_rows(disk)
                    net.release.set()
                if event.get('done'):
                    rows=await cache_rows(disk);assert len(rows)==1 and rows[0].result==event['result']=='完整 world'
        assert len(received)==2 and received[-1]['done']
        evidence['BackendSamples'].append({'Origin':str(response.request.url),'Method':'POST','Status':200,'Body':json.dumps(received,ensure_ascii=False)})
        evidence.update({'Mode':'held-complete','FinalSource':'deepseek','CacheRows':1,'TokenObservedBeforeCompletion':True,'TerminalObservedAfterCommit':True,'Database':str(disk.path)})

@pytest.mark.parametrize('kind',['stream','json'])
async def test_legacy_partial_cache_cannot_bypass_new_completion_rules(disk,monkeypatch,evidence,kind):
    legacy_key=hashlib.md5(json.dumps({'version':2,'text':ARTICLE,'level':1,'known':[],'seen':[]},ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    async with disk.factory() as db:
        db.add(ReadingConversionCache(cache_key=legacy_key,result='半截 partial',vocabulary='[]'));await db.commit()
    async with endpoints(disk,monkeypatch,evidence,'complete') as net:
        r=await net.client.post('/api/v1/reading/convert'+('/stream' if kind=='stream' else ''),json={'text':ARTICLE});record(evidence,r)
        value=events(r)[-1] if kind=='stream' else r.json()
        assert value['result']=='完整 world' and not value.get('cached') and value.get('source')!='cache',value
        assert len(evidence['ProviderRequests'])==1
        rows=await cache_rows(disk);assert len(rows)==2
        assert any(row.cache_key==legacy_key and row.result=='半截 partial' for row in rows)
        evidence.update({'Mode':'legacy-'+kind,'FinalSource':'deepseek','CacheRows':2,'LegacyRowPreserved':True,'LegacyPartialIgnored':True,'Database':str(disk.path)})
