"""Actual backend/provider localhost HTTP; external AI/search explicitly simulated."""
import asyncio, hashlib, importlib, json, os
from pathlib import Path
from datetime import datetime
from contextlib import asynccontextmanager
from types import SimpleNamespace
import pytest
from fastapi import FastAPI, Request
from sqlalchemy import select
from app.models import APIQuota, APICallLog, APICache
from app.core.api_scheduler.client import UnifiedAPIClient, GenerationConfig, RequestContext, _current_user_id
from tests.test_generation_state_flow import flow
from tests.test_generation_shutdown_http_flow import server


@pytest.fixture
def evidence(request,tmp_path):
    data={'Test':request.node.nodeid,'Start':datetime.now().astimezone().isoformat(),
          'ExternalAI':'explicit simulation only','Search':'explicit simulation only',
          'BackendSamples':[],'ProviderRequests':[],'BlockedEndpoints':[]}
    yield data
    data['End']=datetime.now().astimezone().isoformat()
    root=Path(__file__).resolve().parents[1]
    data['SourceHashes']={p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in (
        'app/core/api_scheduler/client.py','app/core/book/compiler.py',
        'app/core/book/block_generators.py','app/core/agents/research_agent.py')}
    out=Path(os.environ.get('KNOWALL_NATIVE_GENERATION_EVIDENCE_DIR',str(tmp_path/'http-evidence')))
    out.mkdir(parents=True,exist_ok=True)
    (out/('real-native-'+hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:16]+'.json')).write_text(
        json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8')


def record(evidence,response):
    evidence['BackendSamples'].append({'Origin':str(response.request.url),'Method':response.request.method,
        'Status':response.status_code,'Body':response.text})


@asynccontextmanager
async def native(flow,monkeypatch,evidence,protocol):
    import app.database as database
    import app.core.api_scheduler as api
    from app.core.api_scheduler import cache,client
    from app.core.api_scheduler.adapters import openai,anthropic,http_utils
    rate=importlib.import_module('app.core.api_scheduler.rate_limiter')
    monkeypatch.setattr(cache,'async_session',database.async_session)
    monkeypatch.setattr(rate,'async_session',database.async_session)
    monkeypatch.setattr(client,'rate_limiter',rate.RateLimiter())
    unified=UnifiedAPIClient();monkeypatch.setattr(api,'api_client',unified)
    provider=FastAPI()
    @provider.post('/v1/chat/completions')
    @provider.post('/v1/messages')
    async def explicit_simulation(req:Request):
        body=await req.json()
        evidence['ProviderRequests'].append({'Path':str(req.url),'Payload':body,'Simulation':True})
        prompt=body['messages'][-1]['content']
        content=await flow.chat(prompt)
        if protocol=='anthropic':
            return {'model':'explicit-simulated-reported','content':[{'type':'text','text':content}],
                'usage':{'input_tokens':7,'output_tokens':11},'stop_reason':'end_turn'}
        return {'model':'explicit-simulated-reported','choices':[{'message':{'content':content}}],
            'usage':{'prompt_tokens':7,'completion_tokens':11}}
    token=_current_user_id.set('local_user')
    try:
        async with server(provider) as simulated:
            origin=str(simulated.base_url).rstrip('/')
            unified.configure_adapter(protocol,'explicit-simulated-no-real-key',origin+'/v1','explicit-simulated-selected-model')
            def only_local(url,headers,payload,timeout):
                if not url.startswith(origin+'/'):
                    evidence['BlockedEndpoints'].append(url);raise RuntimeError('blocked non-local provider')
                return http_utils.sync_post(url,headers,payload,timeout)
            monkeypatch.setattr(openai,'sync_post',only_local);monkeypatch.setattr(anthropic,'sync_post',only_local)
            async with server(flow.client._transport.app) as backend:
                yield SimpleNamespace(client=backend,api=unified,factory=database.async_session)
    finally:
        await flow.agent.close();await flow.compiler.close();_current_user_id.reset(token)


async def wait_terminal(client,path,evidence):
    async with asyncio.timeout(10):
        while True:
            r=await client.get(path)
            assert r.status_code==200,r.text
            if r.json()['status'] in ('completed','error'):
                record(evidence,r);return r.json()
            await asyncio.sleep(.01)


@pytest.mark.parametrize('protocol',['openai','anthropic'])
@pytest.mark.parametrize('kind',['book','research'])
async def test_native_full_generation_parameters_output_and_persistent_cache(flow,monkeypatch,evidence,protocol,kind):
    async with native(flow,monkeypatch,evidence,protocol) as n:
        path='/api/v1/books/create' if kind=='book' else '/api/v1/research/start'
        payload={'title':'原生明确模拟教材','source_doc_ids':['source']} if kind=='book' else {'topic':'原生明确模拟研究'}
        outputs=[]
        for iteration in range(2):
            r=await n.client.post(path,json=payload);record(evidence,r)
            assert r.status_code==200,r.text
            identity=r.json()['id' if kind=='book' else 'task_id']
            statuspath=f'/api/v1/books/{identity}' if kind=='book' else f'/api/v1/research/{identity}/status'
            data=await wait_terminal(n.client,statuspath,evidence)
            assert data['status']=='completed',data
            assert data['progress']==1 and not data.get('error')
            if kind=='book':
                blocks=data['chapters'][0]['sections'][0]['blocks']
                assert [b['type'] for b in blocks]==['text','quiz','callout','retrieval_practice']
                assert '线粒体' in blocks[0]['content'] and json.loads(blocks[1]['content'])['questions'][0]['answer']=='A'
                assert json.loads(blocks[2]['content'])['content']=='重点：能量转换'
                r=await n.client.get(f'/api/v1/books/{identity}/export');record(evidence,r)
                assert r.status_code==200 and 'filename*=UTF-8' in r.headers['content-disposition']
                assert all(s in r.text for s in ('原生明确模拟教材','线粒体','哪种细胞器','重点：能量转换'))
                outputs.append(r.text)
            else:
                assert '研究结果' in data['report'] and '[来源1]' in data['report'] and len(data['sources'])==1
                r=await n.client.get(f'/api/v1/research/{identity}/stream');record(evidence,r)
                assert r.status_code==200 and 'event: complete' in r.text and '研究结果' in r.text
                outputs.append(data['report'])
            expected=4 if kind=='book' else 3
            assert len(evidence['ProviderRequests'])==expected, 'second identical generation must hit durable SQL cache'
        assert outputs[0]==outputs[1]
        requests=evidence['ProviderRequests']
        assert [r['Payload']['max_tokens'] for r in requests]==([4096,4096,4096,256] if kind=='book' else [4096,4096,8192])
        assert all(r['Payload']['model']=='explicit-simulated-selected-model' for r in requests)
        assert all(r['Path'].endswith('/messages' if protocol=='anthropic' else '/chat/completions') for r in requests)
        if kind=='book':assert '隔离资料：线粒体参与能量转换' in requests[1]['Payload']['messages'][0]['content']
        async with n.factory() as db:
            logs=(await db.scalars(select(APICallLog))).all()
            quotas=(await db.scalars(select(APIQuota))).all()
            caches=(await db.scalars(select(APICache))).all()
        assert len(logs)==len(caches)==expected and all(log.success and log.model_name=='explicit-simulated-reported' for log in logs)
        assert len(quotas)==1 and quotas[0].used_today==quotas[0].total_used==expected*18
        assert not evidence['BlockedEndpoints']
        evidence.update({'NativeAdapter':protocol,'PaidSimulatedCalls':expected,'SecondGenerationProviderCalls':0,
            'QuotaUsed':quotas[0].used_today,'APILogRows':len(logs),'CacheRows':len(caches),'FinalOutput':outputs[0],
            'SourceDatabase':str(n.factory.kw['bind'].url),'CompleteGenerations':2})


@pytest.mark.parametrize('protocol',['openai','anthropic'])
async def test_native_user_model_override_preserves_config_and_default_followup(flow,monkeypatch,evidence,protocol):
    async with native(flow,monkeypatch,evidence,protocol) as n:
        registered=n.api._adapters[protocol]
        n.api.configure_adapter(protocol+'_alice','explicit-simulated-user-key',registered.base_url,'explicit-simulated-user-default')
        config=GenerationConfig(model=protocol+'/requested-A',max_tokens=777,max_retries=0,temperature=.2,top_p=.8)
        for model,limit in [(protocol+'/requested-B',256),(None,None),(None,None)]:
            result=await n.api.chat('明确模拟问题',config=config,context=RequestContext(user_id='alice'),model=model,max_tokens=limit)
            assert '线粒体' in result
        assert config.model==protocol+'/requested-A' and config.max_tokens==777
        assert n.api._adapters[protocol+'_alice'].model_name=='explicit-simulated-user-default'
        requests=evidence['ProviderRequests']
        assert [(r['Payload']['model'],r['Payload']['max_tokens']) for r in requests]==[('requested-B',256),('requested-A',777)]
        assert all(r['Payload']['temperature']==.2 and r['Payload']['top_p']==.8 for r in requests)
        async with n.factory() as db:
            quotas=(await db.scalars(select(APIQuota))).all()
            logs=(await db.scalars(select(APICallLog))).all()
            caches=(await db.scalars(select(APICache))).all()
        assert len(quotas)==1 and quotas[0].user_id=='alice' and quotas[0].used_today==36
        assert len(logs)==len(caches)==2 and all(log.user_id=='alice' and log.success for log in logs)
        assert not evidence['BlockedEndpoints']
        evidence.update({'Scope':'native-client-to-provider; no backend API request',
            'NativeAdapter':protocol,'PaidSimulatedCalls':2,'RepeatedDefaultProviderCalls':0,'QuotaUsed':36,
            'APILogRows':2,'CacheRows':2,'CallerConfigModel':config.model,'CallerConfigMaxTokens':config.max_tokens})
