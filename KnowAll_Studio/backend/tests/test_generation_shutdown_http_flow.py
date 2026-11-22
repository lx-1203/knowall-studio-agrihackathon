"""Actual lifespan/localhost HTTP/SQL; AI tools or native HTTP provider explicitly simulated."""
import asyncio,json,os,socket,hashlib,importlib
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
import httpx,pytest
from fastapi import FastAPI,Request
from sqlalchemy import select
from app.models import APIQuota
from tests.test_generation_shutdown_flow import flow,paused

@asynccontextmanager
async def server(app,lifespan='off'):
    import uvicorn
    listener=socket.socket();listener.bind(('127.0.0.1',0))
    uv=uvicorn.Server(uvicorn.Config(app,log_level='critical',lifespan=lifespan,timeout_graceful_shutdown=2))
    job=asyncio.create_task(uv.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(5):
            while not uv.started:
                if job.done():await job;raise AssertionError('server did not start')
                await asyncio.sleep(.01)
        async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{listener.getsockname()[1]}',timeout=10) as client:yield client
    finally:
        uv.should_exit=True
        try:await asyncio.wait_for(job,5)
        finally:listener.close()

@pytest.fixture
def evidence(request):
    data={'Test':request.node.nodeid,'Start':datetime.now().astimezone().isoformat(),
          'BackendSamples':[],'ProviderRequests':[],'ExternalAI':'explicit simulation only'}
    yield data
    root=Path(__file__).resolve().parents[1]
    data['End']=datetime.now().astimezone().isoformat()
    data['SourceHashes']={p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in (
        'app/main.py','app/core/agents/research_agent.py','app/core/book/compiler.py')}
    out=Path(os.environ['KNOWALL_SHUTDOWN_EVIDENCE_DIR']);out.mkdir(parents=True,exist_ok=True)
    (out/('real-'+hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:16]+'.json')).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')

def record(evidence,response):
    evidence['BackendSamples'].append({'Origin':str(response.request.url),'Method':response.request.method,
        'Status':response.status_code,'Body':response.text})

def install_lifespan(flow,monkeypatch,tmp_path):
    monkeypatch.setenv('KNOWALL_LOG_DIR',str(tmp_path/'logs'))
    import app.main as main
    monkeypatch.setattr(main,'startup_init',AsyncMock())
    app=flow.client._transport.app;app.router.lifespan_context=main.lifespan
    return app

@pytest.mark.parametrize('kind',['research','book'])
async def test_actual_http_cancelled_status_stream_export_and_delete(paused,monkeypatch,tmp_path,evidence,kind):
    app=install_lifespan(paused,monkeypatch,tmp_path)
    async with server(app,'on') as client:
        path='/api/v1/research/start' if kind=='research' else '/api/v1/books/create'
        r=await client.post(path,json={'topic':'明确模拟研究'} if kind=='research' else {'title':'明确模拟教材','source_doc_ids':['source']})
        record(evidence,r);assert r.status_code==200,r.text
        identity=r.json()['task_id' if kind=='research' else 'id']
        await asyncio.wait_for(paused.entered.wait(),2)
        service=paused.agent if kind=='research' else paused.compiler
        await service.close();assert paused.cleaned.is_set()
        r=await client.get(f'/api/v1/research/{identity}/status' if kind=='research' else f'/api/v1/books/{identity}')
        record(evidence,r);assert r.status_code==200 and r.json()['status']=='error' and r.json()['error']
        if kind=='research':
            r=await client.get(f'/api/v1/research/{identity}/stream');record(evidence,r)
            assert r.status_code==200 and 'event: error' in r.text and 'event: complete' not in r.text
        else:
            r=await client.get(f'/api/v1/books/{identity}/export');record(evidence,r);assert r.status_code==409
            for method,status in (('DELETE',200),('GET',404),('DELETE',404)):
                r=await client.request(method,f'/api/v1/books/{identity}');record(evidence,r);assert r.status_code==status
        assert len(paused.paused_calls)==1
    evidence['CooperativeCleanupFinished']=True

async def test_actual_server_lifespan_stops_both_pending_generations(paused,monkeypatch,tmp_path,evidence):
    app=install_lifespan(paused,monkeypatch,tmp_path)
    async with server(app,'on') as client:
        r=await client.post('/api/v1/research/start',json={'topic':'明确模拟研究'});record(evidence,r)
        assert r.status_code==200;research=paused.agent.get_task(r.json()['task_id'])
        r=await client.post('/api/v1/books/create',json={'title':'明确模拟教材','source_doc_ids':['source']});record(evidence,r)
        assert r.status_code==200;book=paused.compiler.get_book(r.json()['id'])
        async with asyncio.timeout(3):
            while len(paused.paused_calls)<2:await asyncio.sleep(.001)
    assert research.status.value==book.status.value=='error'
    assert not paused.agent._running and not paused.compiler._compilations and paused.cleaned.is_set()
    import app.websocket.sync_server as ws
    assert not ws._background_tasks
    evidence['ShutdownStates']={'research':research.status.value,'book':book.status.value,'WebSocketJobs':0,'GenerationJobs':0}

@pytest.mark.parametrize('kind',['research','book'])
async def test_native_http_shutdown_stops_job_but_not_already_accepted_provider_call(flow,monkeypatch,tmp_path,evidence,kind):
    import app.database as database
    import app.core.api_scheduler as api
    from app.core.api_scheduler import cache,client as client_module
    from app.core.api_scheduler.client import UnifiedAPIClient
    from app.core.api_scheduler.adapters import openai,http_utils
    rate=importlib.import_module('app.core.api_scheduler.rate_limiter')
    monkeypatch.setattr(cache,'async_session',database.async_session)
    monkeypatch.setattr(rate,'async_session',database.async_session)
    monkeypatch.setattr(client_module,'rate_limiter',rate.RateLimiter())
    unified=UnifiedAPIClient();monkeypatch.setattr(api,'api_client',unified)
    provider=FastAPI();entered=asyncio.Event();release=asyncio.Event();finished=asyncio.Event();blocked=[]
    @provider.post('/v1/chat/completions')
    async def explicit_simulation(req:Request):
        body=await req.json();evidence['ProviderRequests'].append({'Path':str(req.url),'Payload':body,'Simulation':True})
        entered.set();await release.wait();finished.set()
        return {'model':'explicit-simulated-reported','choices':[{'message':{'content':'明确模拟晚到模型内容'}}],
                'usage':{'prompt_tokens':7,'completion_tokens':11}}
    app=install_lifespan(flow,monkeypatch,tmp_path)
    async with server(provider) as simulated:
        origin=str(simulated.base_url).rstrip('/')
        unified.configure_adapter('openai','explicit-simulated-no-real-key',origin+'/v1','explicit-simulated-model')
        def only_local(url,headers,payload,timeout):
            if not url.startswith(origin+'/'):
                blocked.append(url);raise RuntimeError('blocked non-local model request')
            return http_utils.sync_post(url,headers,payload,timeout)
        monkeypatch.setattr(openai,'sync_post',only_local)
        try:
            async with server(app,'on') as backend:
                r=await backend.post('/api/v1/research/start' if kind=='research' else '/api/v1/books/create',
                    json={'topic':'明确模拟研究'} if kind=='research' else {'title':'明确模拟教材','source_doc_ids':['source']})
                record(evidence,r);assert r.status_code==200,r.text
                item=flow.agent.get_task(r.json()['task_id']) if kind=='research' else flow.compiler.get_book(r.json()['id'])
                await asyncio.wait_for(entered.wait(),3)
            assert item.status.value=='error' and item.error
            assert not flow.agent._running and not flow.compiler._compilations
            assert not finished.is_set(), 'backend shutdown did not physically terminate accepted remote HTTP work'
            release.set();await asyncio.wait_for(finished.wait(),3)
            await asyncio.sleep(.05)
            assert item.status.value=='error' and len(evidence['ProviderRequests'])==1
            async with database.async_session() as db:
                quotas=(await db.scalars(select(APIQuota))).all()
            assert len(quotas)==1 and quotas[0].used_today==0
            evidence.update({'NativeAdapter':'OpenAICompatAdapter with actual asyncio.to_thread HTTP',
                'ProviderFinishedAfterBackendShutdown':True,'FinalState':item.status.value,'QuotaRecordedAfterCancellation':0,
                'ActualExternalBillingKnown':False,'CancellationBoundary':'accepted provider work continued; late output did not resume cancelled pipeline',
                'BlockedEndpoints':blocked})
            assert blocked==[]
        finally:
            release.set()
