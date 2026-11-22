"""Signed real localhost pipelines/SQL/native adapter; model/search explicitly simulated."""
import asyncio,hashlib,importlib,json,os
from pathlib import Path
from datetime import datetime
from collections import Counter
import pytest
from fastapi import FastAPI,Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from app.models import APICache,APIQuota,APICallLog,APIKey
from app.core.api_scheduler.client import UnifiedAPIClient
from tests.test_generation_identity_flow import identity,status_path
from tests.test_generation_state_flow import flow
from tests.test_generation_shutdown_http_flow import server
from tests.test_cache_commit_race_flow import FirstMergeMiss

@pytest.fixture
def evidence(request,tmp_path):
    data={'Test':request.node.nodeid,'Start':datetime.now().astimezone().isoformat(),
        'ExternalAI':'explicit simulation only','Search':'explicit simulation only',
        'ProviderRequests':[],'BackendSamples':[],'BlockedEndpoints':[]}
    yield data
    data['End']=datetime.now().astimezone().isoformat();root=Path(__file__).resolve().parents[1]
    data['SourceHashes']={p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in (
        'app/core/api_scheduler/cache.py','app/core/api_scheduler/client.py','app/core/api_scheduler/rate_limiter.py',
        'app/core/agents/research_agent.py','app/core/book/compiler.py','app/core/auth.py',
        'app/api/books.py','app/api/research.py','app/core/generation_access.py')}
    out=Path(os.environ.get('KNOWALL_CACHE_RACE_EVIDENCE_DIR',str(tmp_path/'network')));out.mkdir(parents=True,exist_ok=True)
    (out/('real-race-'+hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:16]+'.json')).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8')

def record(evidence,r):evidence['BackendSamples'].append({'Origin':str(r.request.url),'Status':r.status_code,'Method':r.request.method,'Body':r.text})

@pytest.mark.parametrize('kind',['research','book'])
async def test_signed_same_account_concurrent_pipelines_commit_and_repeat_without_extra_calls(identity,monkeypatch,tmp_path,evidence,kind):
    import app.core.api_scheduler as api
    from app.core.api_scheduler import cache,client
    from app.core.api_scheduler.adapters import anthropic,http_utils
    from app.core import crypto
    rate=importlib.import_module('app.core.api_scheduler.rate_limiter')
    gate=FirstMergeMiss(2)
    monkeypatch.setattr(cache,'async_session',async_sessionmaker(identity.factory.kw['bind'],class_=gate.session_class(),expire_on_commit=False))
    monkeypatch.setattr(rate,'async_session',identity.factory);monkeypatch.setattr(client,'rate_limiter',rate.RateLimiter())
    monkeypatch.setattr(crypto,'_KEY_FILE',tmp_path/'secret.key')
    async with identity.factory() as db:
        db.add(APIKey(user_id='alice',provider='anthropic',key_encrypted=crypto.encrypt_api_key('explicit-simulated-alice')));await db.commit()
    unified=UnifiedAPIClient();monkeypatch.setattr(api,'api_client',unified)
    provider=FastAPI();both_created=asyncio.Event();created=[]
    @provider.post('/v1/messages')
    async def simulate(req:Request):
        payload=await req.json();credential=req.headers.get('authorization','').removeprefix('Bearer ')
        assert credential=='explicit-simulated-alice'
        evidence['ProviderRequests'].append({'Origin':str(req.url),'Payload':payload,'CredentialMarker':credential,'Simulation':True})
        # A same-client duplicate is now queued, so wait for both accepted
        # backend jobs rather than requiring two paid provider arrivals.
        if len(evidence['ProviderRequests'])==1:await asyncio.wait_for(both_created.wait(),5)
        output=await identity.chat(payload['messages'][-1]['content'])
        return {'model':'explicit-simulated-reported','content':[{'type':'text','text':output}],
            'usage':{'input_tokens':7,'output_tokens':11}}
    async with server(provider) as p:
        origin=str(p.base_url).rstrip('/')
        def only_local(url,headers,payload,timeout):
            if url!='https://api.anthropic.com/v1/messages':
                evidence['BlockedEndpoints'].append(url);raise RuntimeError('unexpected external model endpoint blocked')
            return http_utils.sync_post(origin+'/v1/messages',headers,payload,timeout)
        monkeypatch.setattr(anthropic,'sync_post',only_local)
        async with server(identity.client._transport.app) as backend:
            async def generate():
                path='/api/v1/books/create' if kind=='book' else '/api/v1/research/start'
                payload={'title':'同账号并发教材','source_doc_ids':['source']} if kind=='book' else {'topic':'同账号并发研究'}
                r=await backend.post(path,headers=identity.headers['alice'],json=payload);record(evidence,r);assert r.status_code==200,r.text
                key=r.json()['id' if kind=='book' else 'task_id']
                created.append(key)
                if len(created)>=2:both_created.set()
                async with asyncio.timeout(12):
                    while True:
                        r=await backend.get(status_path(kind,key),headers=identity.headers['alice'])
                        assert r.status_code==200,r.text
                        if r.json()['status'] in ('completed','error'):record(evidence,r);return key,r.json()
                        await asyncio.sleep(.01)
            results=await asyncio.gather(generate(),generate())
            evidence['ConcurrentTerminalStatuses']=[v['status'] for k,v in results]
            assert all(v['status']=='completed' for k,v in results),results
            calls=len(evidence['ProviderRequests'])
            key,data=await generate();assert data['status']=='completed',data
            assert len(evidence['ProviderRequests'])==calls
            n=3 if kind=='research' else 4
            async with identity.factory() as db:
                cached=(await db.scalars(select(APICache))).all();logs=(await db.scalars(select(APICallLog))).all();quota=(await db.scalars(select(APIQuota))).one()
            assert len(cached)==n and calls==n
            assert len(logs)==calls and all(r.success and r.user_id=='alice' and r.tokens_input==7 and r.tokens_output==11 for r in logs)
            assert quota.used_today==quota.total_used==calls*18
            for key,data in results:
                path=f'/api/v1/books/{key}/export' if kind=='book' else f'/api/v1/research/{key}/stream'
                r=await backend.get(path,headers=identity.headers['alice']);record(evidence,r)
                assert r.status_code==200 and ('event: complete' in r.text if kind=='research' else '线粒体' in r.text)
                r=await backend.get(path,headers=identity.headers['bob']);record(evidence,r);assert r.status_code==404,r.text
            evidence.update({'CompleteGenerations':3,'PaidSimulatedCalls':calls,'Logs':len(logs),'CacheRows':len(cached),
                'Quota':{'alice':quota.used_today},'RepeatedProviderCalls':0,'SourceDatabase':str(identity.factory.kw['bind'].url),
                'LegacyMergeReadsPaused':gate.reads,'ConcurrentSameAccount':'alice','Schedule':'first provider response held until both backend jobs accepted; same-client in-flight dedupe'})
