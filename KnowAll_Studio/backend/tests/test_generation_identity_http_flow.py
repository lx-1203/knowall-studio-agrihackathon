"""Actual signed backend + encrypted keys + native local provider; external AI simulated."""
import asyncio,hashlib,importlib,json,os
from pathlib import Path
from datetime import datetime
from collections import Counter
import pytest
from fastapi import FastAPI,Request
from sqlalchemy import select
from app.models import APIKey,APIQuota,APICallLog,APICache
from app.core.api_scheduler.client import UnifiedAPIClient
from tests.test_generation_identity_flow import flow,identity,status_path
from tests.test_generation_shutdown_http_flow import server

@pytest.fixture
def evidence(request,tmp_path):
    data={'Test':request.node.nodeid,'Start':datetime.now().astimezone().isoformat(),
        'ExternalAI':'explicit simulation only','Search':'explicit simulation only','BackendSamples':[],
        'ProviderRequests':[],'BlockedEndpoints':[]}
    yield data
    data['End']=datetime.now().astimezone().isoformat()
    root=Path(__file__).resolve().parents[1]
    paths=['app/api/books.py','app/api/research.py','app/core/book/compiler.py','app/core/book/models.py',
        'app/core/agents/research_agent.py','app/core/api_scheduler/client.py','app/core/auth.py','app/core/generation_access.py']
    data['SourceHashes']={p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in paths if (root/p).exists()}
    out=Path(os.environ.get('KNOWALL_GENERATION_IDENTITY_EVIDENCE_DIR',str(tmp_path/'http-evidence')))
    out.mkdir(parents=True,exist_ok=True)
    (out/('real-identity-'+hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:16]+'.json')).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8')

def record(evidence,r,account):
    evidence['BackendSamples'].append({'Origin':str(r.request.url),'Method':r.request.method,
        'Account':account,'Status':r.status_code,'Body':r.text})

@pytest.mark.parametrize('kind',['book','research'])
async def test_real_signed_concurrent_generations_load_encrypted_keys_and_isolate_results(identity,monkeypatch,tmp_path,evidence,kind):
    import app.core.api_scheduler as api
    from app.core.api_scheduler import cache,client
    from app.core.api_scheduler.adapters import anthropic,http_utils
    from app.core import crypto
    rate=importlib.import_module('app.core.api_scheduler.rate_limiter')
    monkeypatch.setattr(cache,'async_session',identity.factory);monkeypatch.setattr(rate,'async_session',identity.factory)
    monkeypatch.setattr(client,'rate_limiter',rate.RateLimiter())
    monkeypatch.setattr(crypto,'_KEY_FILE',tmp_path/'secret.key')
    async with identity.factory() as db:
        for account in ('alice','bob'):
            db.add(APIKey(user_id=account,provider='anthropic',key_encrypted=crypto.encrypt_api_key('explicit-simulated-'+account)))
        await db.commit()
    unified=UnifiedAPIClient();monkeypatch.setattr(api,'api_client',unified)
    provider=FastAPI()
    @provider.post('/v1/messages')
    async def simulated(req:Request):
        payload=await req.json();marker=req.headers.get('x-api-key') or req.headers.get('authorization','').removeprefix('Bearer ')
        assert marker.startswith('explicit-simulated-')
        evidence['ProviderRequests'].append({'Origin':str(req.url),'Payload':payload,'CredentialMarker':marker,'Simulation':True})
        body=await identity.chat(payload['messages'][-1]['content'])
        return {'model':payload['model']+'-reported','content':[{'type':'text','text':body}],
            'usage':{'input_tokens':7,'output_tokens':11}}
    async with server(provider) as p:
        origin=str(p.base_url).rstrip('/')
        unified.configure_adapter('anthropic','explicit-simulated-global',origin+'/v1','explicit-global-default')
        def isolated(url,headers,payload,timeout):
            allowed=[origin+'/v1/messages','https://api.anthropic.com/v1/messages']
            if url not in allowed:
                evidence['BlockedEndpoints'].append(url);raise RuntimeError('non-local/unexpected model URL blocked')
            # Default stored-key endpoint intentionally redirected before any actual network call.
            return http_utils.sync_post(origin+'/v1/messages',headers,payload,timeout)
        monkeypatch.setattr(anthropic,'sync_post',isolated)
        async with server(identity.client._transport.app) as backend:
            async def generate(account):
                path='/api/v1/books/create' if kind=='book' else '/api/v1/research/start'
                body={'title':'明确模拟私有教材','source_doc_ids':['source'],'user_id':'forged'} if kind=='book' else {'topic':'明确模拟私有研究','user_id':'forged'}
                r=await backend.post(path,headers=identity.headers.get(account,{}),json=body);record(evidence,r,account)
                assert r.status_code==200,r.text
                key=r.json()['id' if kind=='book' else 'task_id']
                async with asyncio.timeout(10):
                    while True:
                        r=await backend.get(status_path(kind,key),headers=identity.headers.get(account,{}))
                        assert r.status_code==200,r.text
                        if r.json()['status'] in ('completed','error'):
                            record(evidence,r,account);break
                        await asyncio.sleep(.01)
                assert r.json()['status']=='completed',r.text
                return key
            alice,bob=await asyncio.gather(generate('alice'),generate('bob'))
            n=4 if kind=='book' else 3
            assert Counter(r['CredentialMarker'] for r in evidence['ProviderRequests'])=={'explicit-simulated-alice':n,'explicit-simulated-bob':n}
            await generate('alice');await generate('bob')
            assert len(evidence['ProviderRequests'])==2*n
            local=await generate('local_user')
            assert Counter(r['CredentialMarker'] for r in evidence['ProviderRequests'])=={'explicit-simulated-alice':n,'explicit-simulated-bob':n,'explicit-simulated-global':n}
            suffixes=['','/export'] if kind=='book' else ['/status','/stream']
            for viewer in ('bob','local_user'):
                for suffix in suffixes:
                    r=await backend.get(('/api/v1/books/' if kind=='book' else '/api/v1/research/')+alice+suffix,headers=identity.headers.get(viewer,{}))
                    record(evidence,r,viewer);assert r.status_code==404,r.text
                if kind=='book':
                    r=await backend.delete('/api/v1/books/'+alice,headers=identity.headers.get(viewer,{}))
                    record(evidence,r,viewer);assert r.status_code==404,r.text
            r=await backend.get(('/api/v1/books/' if kind=='book' else '/api/v1/research/')+alice+('/export' if kind=='book' else '/stream'),headers=identity.headers['alice'])
            record(evidence,r,'alice');assert r.status_code==200 and ('线粒体' in r.text if kind=='book' else 'event: complete' in r.text)
            for viewer in ('alice','bob','local_user'):
                r=await backend.get('/api/v1/books' if kind=='book' else '/api/v1/research/tasks',headers=identity.headers.get(viewer,{}))
                record(evidence,r,viewer);items=r.json()['books' if kind=='book' else 'tasks']
                assert len(items)==(1 if viewer=='local_user' else 2)
            async with identity.factory() as db:
                quotas=(await db.scalars(select(APIQuota))).all();logs=(await db.scalars(select(APICallLog))).all();caches=(await db.scalars(select(APICache))).all()
            assert {q.user_id:q.used_today for q in quotas}=={'alice':n*18,'bob':n*18,'local_user':n*18}
            assert len(logs)==len(caches)==3*n
            assert Counter(log.user_id for log in logs)=={'alice':n,'bob':n,'local_user':n}
            assert not evidence['BlockedEndpoints']
            evidence.update({'CompleteGenerations':5,'PaidSimulatedCalls':3*n,'RepeatedOwnProviderCalls':0,
                'Quota':{q.user_id:q.used_today for q in quotas},'CacheRows':len(caches),'SourceDatabase':str(identity.factory.kw['bind'].url),
                'NativeAdapter':'Anthropic default endpoint redirected to actual localhost before networking','KeysEncryptedInTemporaryDirectory':True})
