"""Real signed HTTP/SQL/native Anthropic; remote AI and search are explicit simulations."""
import asyncio,hashlib,importlib,json,os
from datetime import datetime
from pathlib import Path
import pytest
from fastapi import FastAPI,Request
from sqlalchemy import select
from app.models import APIQuota,APICallLog,APICache,APIKey
from app.core.api_scheduler.client import UnifiedAPIClient
from tests.test_generation_identity_flow import identity,status_path
from tests.test_generation_state_flow import flow
from tests.test_generation_shutdown_http_flow import server

@pytest.fixture
def evidence(request,tmp_path):
    data={'Test':request.node.nodeid,'Start':datetime.now().astimezone().isoformat(),
        'ExternalAI':'explicit simulation only','Search':'explicit simulation only','BackendSamples':[],
        'ProviderRequests':[],'BlockedEndpoints':[]}
    yield data
    data['End']=datetime.now().astimezone().isoformat();root=Path(__file__).resolve().parents[1]
    data['SourceHashes']={p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in (
        'app/core/api_scheduler/client.py','app/core/api_scheduler/cache.py','app/core/api_scheduler/rate_limiter.py',
        'app/core/book/compiler.py','app/core/agents/research_agent.py','app/api/books.py','app/api/research.py','app/core/auth.py','app/core/generation_access.py')}
    out=Path(os.environ.get('KNOWALL_SINGLEFLIGHT_EVIDENCE_DIR',str(tmp_path/'network')));out.mkdir(parents=True,exist_ok=True)
    (out/('real-singleflight-'+hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:16]+'.json')).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8')

def record(evidence,r):evidence['BackendSamples'].append({'Origin':str(r.request.url),'Status':r.status_code,'Method':r.request.method,'Body':r.text})

@pytest.mark.parametrize('kind,mode',[('research','complete'),('book','complete'),('book','cancel-waiter'),('book','cancel-leader')])
async def test_actual_signed_same_request_pipeline_and_cancellation_boundaries(identity,monkeypatch,tmp_path,evidence,kind,mode):
    import app.core.api_scheduler as api
    from app.core.api_scheduler import client,cache
    from app.core.api_scheduler.adapters import anthropic,http_utils
    from app.core import crypto
    rate=importlib.import_module('app.core.api_scheduler.rate_limiter')
    monkeypatch.setattr(cache,'async_session',identity.factory);monkeypatch.setattr(rate,'async_session',identity.factory)
    monkeypatch.setattr(client,'rate_limiter',rate.RateLimiter());monkeypatch.setattr(crypto,'_KEY_FILE',tmp_path/'secret.key')
    async with identity.factory() as db:
        db.add(APIKey(user_id='alice',provider='anthropic',key_encrypted=crypto.encrypt_api_key('explicit-simulated-alice')));await db.commit()
    unified=UnifiedAPIClient();monkeypatch.setattr(api,'api_client',unified)
    entered=asyncio.Event();release=asyncio.Event();finished=asyncio.Event();provider=FastAPI()
    @provider.post('/v1/messages')
    async def simulate(req:Request):
        body=await req.json();credential=req.headers.get('authorization','').removeprefix('Bearer ')
        assert credential=='explicit-simulated-alice'
        evidence['ProviderRequests'].append({'Origin':str(req.url),'Payload':body,'CredentialMarker':credential,'Simulation':True})
        if len(evidence['ProviderRequests'])==1:
            entered.set();await release.wait();finished.set()
        output=await identity.chat(body['messages'][-1]['content'])
        return {'model':'explicit-simulated-reported','content':[{'type':'text','text':output}],
            'usage':{'input_tokens':7,'output_tokens':11}}
    async with server(provider) as p:
        origin=str(p.base_url).rstrip('/')
        def only_local(url,headers,payload,timeout):
            if url!='https://api.anthropic.com/v1/messages':
                evidence['BlockedEndpoints'].append(url);raise RuntimeError('unexpected model endpoint blocked')
            return http_utils.sync_post(origin+'/v1/messages',headers,payload,timeout)
        monkeypatch.setattr(anthropic,'sync_post',only_local)
        try:
            async with server(identity.client._transport.app) as backend:
                async def create():
                    r=await backend.post('/api/v1/books/create' if kind=='book' else '/api/v1/research/start',
                        headers=identity.headers['alice'],json={'title':'同输入原生教材','source_doc_ids':['source']} if kind=='book' else {'topic':'同输入原生研究'})
                    record(evidence,r);assert r.status_code==200,r.text;return r.json()['id' if kind=='book' else 'task_id']
                async def terminal(key):
                    async with asyncio.timeout(12):
                        while True:
                            r=await backend.get(status_path(kind,key),headers=identity.headers['alice']);assert r.status_code==200,r.text
                            if r.json()['status'] in ('completed','error'):record(evidence,r);return r.json()
                            await asyncio.sleep(.01)
                first=await create();await asyncio.wait_for(entered.wait(),4);second=await create()
                # Deliberate simulated latency validates two actual accepted
                # backend jobs while the first external HTTP response is held.
                await asyncio.sleep(.05)
                assert len(evidence['ProviderRequests'])==1,'duplicate paid call while leader held'
                if mode.startswith('cancel'):
                    target=second if mode=='cancel-waiter' else first
                    removed=identity.compiler.get_book(target)
                    r=await backend.delete('/api/v1/books/'+target,headers=identity.headers['alice']);record(evidence,r);assert r.status_code==200,r.text
                    r=await backend.get('/api/v1/books/'+target,headers=identity.headers['alice']);record(evidence,r);assert r.status_code==404,r.text
                    if mode=='cancel-leader':
                        result=await terminal(second);assert result['status']=='completed'
                        assert not finished.is_set(),'accepted remote call remains held after leader cancellation'
                release.set();await asyncio.wait_for(finished.wait(),4)
                remaining=[first,second] if mode=='complete' else [first if mode=='cancel-waiter' else second]
                for key in remaining:assert (await terminal(key))['status']=='completed'
                if mode.startswith('cancel'):
                    assert removed.status.value=='error' and removed.error
                n=3 if kind=='research' else 4
                expected=n+int(mode=='cancel-leader')
                assert len(evidence['ProviderRequests'])==expected
                third=await create();assert (await terminal(third))['status']=='completed'
                assert len(evidence['ProviderRequests'])==expected
                async with identity.factory() as db:
                    quota=(await db.scalars(select(APIQuota))).one();logs=(await db.scalars(select(APICallLog))).all();cached=(await db.scalars(select(APICache))).all()
                assert quota.used_today==quota.total_used==n*18 and len(logs)==len(cached)==n
                assert all(r.success and r.user_id=='alice' for r in logs)
                for key in remaining:
                    url=f'/api/v1/books/{key}/export' if kind=='book' else f'/api/v1/research/{key}/stream'
                    r=await backend.get(url,headers=identity.headers['alice']);record(evidence,r);assert r.status_code==200
                    r=await backend.get(url,headers=identity.headers['bob']);record(evidence,r);assert r.status_code==404
                assert not unified._generation_locks and not evidence['BlockedEndpoints']
                evidence.update({'Kind':kind,'Mode':mode,'CompletedGenerations':len(remaining)+1,'ProviderAccepted':expected,
                    'RecordedPaidCalls':n,'CacheRows':n,'Logs':n,'Quota':{'alice':n*18},'RepeatedProviderCalls':0,
                    'LeaderCancelledRemoteContinued':mode=='cancel-leader','ActualExternalBillingKnown':False,
                    'SourceDatabase':str(identity.factory.kw['bind'].url),'ClientLockReferencesAtEnd':0})
        finally:release.set()
