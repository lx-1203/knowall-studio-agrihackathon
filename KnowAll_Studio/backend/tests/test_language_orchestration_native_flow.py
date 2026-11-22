"""Other existing language entrypoints; native HTTP, only AI/coverage simulated."""
import json,socket,hashlib
from pathlib import Path
from unittest.mock import AsyncMock
import pytest
from fastapi import FastAPI,Request
from app.api import language,agents
from app.database import get_db
from app.models import APICache,APICallLog,LanguageVocabulary,APIQuota,Document
from app.core.agents.orchestrator import orchestrator
from app.core.api_scheduler.adapters.openai import OpenAICompatAdapter
from tests.test_language_cache_recovery_flow import language_flow,cache_flow,flow,rows,VALID
from tests.test_reading_native_stream_http_flow import server

@pytest.mark.parametrize('entry',['json','sse'])
async def test_language_orchestration_bad_output_reports_error_then_recovers(language_flow,monkeypatch,entry):
    f=language_flow;d=f.evidence;d['AI']='explicit localhost provider; coverage report explicitly simulated'
    d['BackendHTTP']=[];d['ProviderHTTP']=[];ports=[];phase={'bad':True}
    monkeypatch.setattr(orchestrator,'_generate_coverage_report',AsyncMock(return_value={'explicit_simulation':True}))
    provider=FastAPI()
    @provider.post('/v1/chat/completions')
    async def simulation(req:Request):
        payload=await req.json();assert req.headers['authorization']=='Bearer language-orchestration-test-key'
        content='[]' if phase['bad'] else json.dumps(VALID,ensure_ascii=False)
        d['ProviderHTTP'].append({'URL':str(req.url),'Payload':payload,'Content':content,'Simulation':True})
        return {'choices':[{'message':{'content':content}}],'usage':{'prompt_tokens':7,'completion_tokens':11}}
    backend=FastAPI();backend.include_router(agents.router)
    async def db():
        async with f.factory() as session:yield session
    backend.dependency_overrides[get_db]=db
    async with server(provider) as remote:
        ports.append(remote.base_url.port);f.api._adapters.clear()
        f.api._adapters['deepseek-chat_alice']=OpenAICompatAdapter('language-orchestration-test-key',str(remote.base_url).rstrip('/')+'/v1','local-test')
        async with server(backend) as client:
            ports.append(client.base_url.port)
            path='/api/v1/agents/orchestrate'+('/stream' if entry=='sse' else '')
            payload={'summary_id':'summary','document_id':'source','agents':['language'],'language_type':'english'}
            async def send():
                r=await client.post(path,json=payload,headers=f.alice)
                d['BackendHTTP'].append({'URL':str(r.request.url),'Method':'POST','Status':r.status_code,'Body':r.text})
                assert r.status_code==200,r.text
                return r
            first=await send()
            if entry=='json':assert first.json()['results']['language']['status']=='error'
            else:assert 'event: agent_error' in first.text and 'event: agent_complete' not in first.text
            assert await rows(f,APICache)==[] and await rows(f,LanguageVocabulary)==[]
            phase['bad']=False
            for _ in range(2):
                r=await send()
                if entry=='json':assert r.json()['results']['language']['status']=='success'
                else:assert 'event: agent_complete' in r.text and 'event: agent_error' not in r.text
            assert len(d['ProviderHTTP'])==2
            assert len(await rows(f,LanguageVocabulary))==len(await rows(f,APICache))==1
            assert [l.success for l in await rows(f,APICallLog)]==[False,True]
            d['SQL']={'Words':1,'Cache':1,'Logs':[False,True],'Quota':(await rows(f,APIQuota))[0].used_today}
    d['ClosedPorts']=ports
    for port in ports:
        with socket.socket() as probe:assert probe.connect_ex(('127.0.0.1',port))!=0

async def test_signed_accounts_use_own_provider_cache_and_source_guard(language_flow):
    f=language_flow;d=f.evidence;d['BackendHTTP']=[];d['ProviderHTTP']=[];ports=[]
    d['Scope']='JWT/cache/quota user scope; vocabulary stays shared per document as existing contract'
    provider=FastAPI()
    @provider.post('/v1/chat/completions')
    async def simulation(req:Request):
        auth=req.headers['authorization'];assert auth in ('Bearer alice-language-key','Bearer bob-language-key')
        payload=await req.json();d['ProviderHTTP'].append({'URL':str(req.url),'User':'alice' if 'alice-' in auth else 'bob','Payload':payload,'Simulation':True})
        return {'choices':[{'message':{'content':json.dumps(VALID)}}],'usage':{'prompt_tokens':7,'completion_tokens':11}}
    backend=FastAPI();backend.include_router(language.router)
    async def db():
        async with f.factory() as session:yield session
    backend.dependency_overrides[get_db]=db
    async with server(provider) as remote:
        ports.append(remote.base_url.port);f.api._adapters.clear()
        for name in ('alice','bob'):
            f.api._adapters['deepseek-chat_'+name]=OpenAICompatAdapter(name+'-language-key',str(remote.base_url).rstrip('/')+'/v1','local-test')
        async with server(backend) as client:
            ports.append(client.base_url.port)
            async def send(name,args):
                r=await client.post('/api/v1/language/vocabulary/generate',headers=getattr(f,name),json=args)
                d['BackendHTTP'].append({'URL':str(r.request.url),'User':name,'Method':'POST','Status':r.status_code,'Body':r.text})
                return r
            bad=await send('alice',{'document_id':'missing','summary_id':'summary'})
            assert bad.status_code==404 and d['ProviderHTTP']==[]
            bad=await send('alice',{'document_id':'source','summary_id':'missing'})
            assert bad.status_code==404 and d['ProviderHTTP']==[]
            args={'document_id':'source','summary_id':'summary'}
            ids=[]
            for name in ('alice','bob','alice','bob'):
                r=await send(name,args);assert r.status_code==200,r.text
                ids.append(r.json()['result']['words'][0]['id'])
            assert len(set(ids))==1  # shared document vocabulary, not private user words
            assert [c['User'] for c in d['ProviderHTTP']]==['alice','bob']
            assert len(await rows(f,APICache))==2 and len(await rows(f,LanguageVocabulary))==1
            quota=await rows(f,APIQuota);assert {q.user_id:q.used_today for q in quota}=={'alice':18,'bob':18}
            d['SQL']={'Cache':2,'SharedWords':1,'Quotas':{'alice':18,'bob':18}}
    d['ClosedPorts']=ports
    for port in ports:
        with socket.socket() as probe:assert probe.connect_ex(('127.0.0.1',port))!=0

@pytest.mark.parametrize('entry',['json','sse'])
@pytest.mark.parametrize('document',['missing','other'])
async def test_native_orchestration_source_guard_precedes_provider(language_flow,monkeypatch,entry,document):
    f=language_flow;d=f.evidence;d['BackendHTTP']=[];d['ProviderHTTP']=[];ports=[]
    async with f.factory() as db:
        db.add(Document(id='other',filename='other.txt',file_type='txt',sha256='native-source-guard'));await db.commit()
    monkeypatch.setattr(orchestrator,'_generate_coverage_report',AsyncMock(return_value={'explicit_simulation':True}))
    d['AI']='explicit localhost simulation; coverage explicitly mocked'
    provider=FastAPI()
    @provider.post('/v1/chat/completions')
    async def simulation(req:Request):
        d['ProviderHTTP'].append({'URL':str(req.url),'Payload':await req.json(),'Simulation':True})
        return {'choices':[{'message':{'content':json.dumps(VALID)}}],'usage':{'prompt_tokens':7,'completion_tokens':11}}
    backend=FastAPI();backend.include_router(agents.router)
    async def db():
        async with f.factory() as session:yield session
    backend.dependency_overrides[get_db]=db
    async with server(provider) as remote:
        ports.append(remote.base_url.port);f.api._adapters.clear()
        f.api._adapters['deepseek-chat_alice']=OpenAICompatAdapter('source-guard-local-key',str(remote.base_url).rstrip('/')+'/v1','local-test')
        async with server(backend) as client:
            ports.append(client.base_url.port)
            r=await client.post('/api/v1/agents/orchestrate'+('/stream' if entry=='sse' else ''),headers=f.alice,
                json={'document_id':document,'summary_id':'summary','agents':['language'],'language_type':'english'})
            d['BackendHTTP'].append({'URL':str(r.request.url),'Method':'POST','Status':r.status_code,'Body':r.text})
            if document=='missing':
                assert r.status_code==404  # current source-access route rejects before Agent
            else:
                assert r.status_code==200
                if entry=='json':assert r.json()['results']['language']['status']=='error'
                else:assert 'event: agent_error' in r.text and 'event: agent_complete' not in r.text
            assert d['ProviderHTTP']==[]
            assert await rows(f,APICache)==[] and await rows(f,LanguageVocabulary)==[]
            assert await rows(f,APICallLog)==[] and await rows(f,APIQuota)==[]
    d['ClosedPorts']=ports
    for port in ports:
        with socket.socket() as probe:assert probe.connect_ex(('127.0.0.1',port))!=0
