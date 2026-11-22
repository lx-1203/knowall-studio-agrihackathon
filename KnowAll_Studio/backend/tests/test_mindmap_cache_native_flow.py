"""Real TCP FastAPI/JWT/SQLite/cache, explicit localhost AI and coverage simulation."""
import json
from unittest.mock import AsyncMock
import pytest
from fastapi import FastAPI,Request
from app.api import agents
from app.database import get_db
from app.models import APICache,APICallLog,APIQuota
from app.core.agents.orchestrator import orchestrator
from app.core.api_scheduler.adapters.openai import OpenAICompatAdapter
from tests.test_mindmap_cache_recovery_flow import mindmap_flow,cache_flow,flow,rows,VALID,node,tree
from tests.test_reading_native_stream_http_flow import server

@pytest.mark.parametrize('entry',['json','sse'])
@pytest.mark.parametrize('bad',['not JSON',tree(),tree(node(level='2')),tree(node(label=3))])
async def test_native_mindmap_fallback_recovery_and_cache(mindmap_flow,monkeypatch,entry,bad):
    f=mindmap_flow;d=f.evidence;d['BackendHTTP']=[];d['ProviderHTTP']=[];d['AI']='localhost explicit AI and coverage simulation'
    monkeypatch.setattr(orchestrator,'_generate_coverage_report',AsyncMock(return_value={'explicit_simulation':True}))
    phase={'content':bad};provider=FastAPI()
    @provider.post('/v1/chat/completions')
    async def simulation(req:Request):
        assert req.headers['authorization']=='Bearer mindmap-native-test-key'
        d['ProviderHTTP'].append({'URL':str(req.url),'Payload':await req.json(),'Content':phase['content'],'Simulation':True})
        return {'choices':[{'message':{'content':phase['content']}}],'usage':{'prompt_tokens':7,'completion_tokens':11}}
    backend=FastAPI();backend.include_router(agents.router)
    async def db():
        async with f.factory() as session:yield session
    backend.dependency_overrides[get_db]=db
    async with server(provider) as remote:
        f.api._adapters.clear();f.api._adapters['deepseek-chat_alice']=OpenAICompatAdapter('mindmap-native-test-key',str(remote.base_url).rstrip('/')+'/v1','local-test')
        async with server(backend) as client:
            async def send():
                r=await client.post('/api/v1/agents/orchestrate'+('/stream' if entry=='sse' else ''),headers=f.alice,
                    json={'summary_id':'summary','document_id':'source','agents':['mindmap'],'config':{'force_restructure':True}})
                d['BackendHTTP'].append({'URL':str(r.request.url),'Status':r.status_code,'Body':r.text})
                assert r.status_code==200,r.text
                if entry=='json':return r.json()['results']['mindmap']
                events=[json.loads(s.split('\ndata: ',1)[1]) for s in r.text.split('\n\n') if s.startswith('event: agent_complete')]
                assert len(events)==1 and 'event: agent_error' not in r.text
                return {'status':'success','result':events[0]['result']}
            first=await send();assert first['status']=='success' and not first['result']['llm_restructured']
            assert first['result']['total_nodes']==2 and await rows(f,APICache)==[]
            phase['content']=json.dumps(VALID,ensure_ascii=False)
            recovered=await send();assert recovered['status']=='success' and recovered['result']['llm_restructured']
            cached=await send();assert cached==recovered
            assert len(d['ProviderHTTP'])==2 and len(await rows(f,APICache))==1
            assert [x.success for x in await rows(f,APICallLog)]==[False,True]
            assert (await rows(f,APIQuota))[0].used_today==36
            d['SQL']={'Logs':[False,True],'Cache':1,'Quota':36};d['ClosedPorts']=[remote.base_url.port,client.base_url.port]

@pytest.mark.parametrize('entry',['json','sse'])
async def test_native_mindmap_existing_bad_cache_and_source_guard(mindmap_flow,monkeypatch,entry):
    f=mindmap_flow;d=f.evidence;d['BackendHTTP']=[];d['ProviderHTTP']=[];d['AI']='localhost explicit AI and coverage simulation'
    monkeypatch.setattr(orchestrator,'_generate_coverage_report',AsyncMock(return_value={'explicit_simulation':True}))
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
        f.api._adapters.clear();f.api._adapters['deepseek-chat_alice']=OpenAICompatAdapter('mindmap-private-local-key',str(remote.base_url).rstrip('/')+'/v1','local-test')
        async with server(backend) as client:
            path='/api/v1/agents/orchestrate'+('/stream' if entry=='sse' else '')
            async def send(document='source',summary='summary'):
                r=await client.post(path,headers=f.alice,json={'summary_id':summary,'document_id':document,'agents':['mindmap'],'config':{'force_restructure':True}})
                d['BackendHTTP'].append({'URL':str(r.request.url),'Status':r.status_code,'Body':r.text});return r
            for args in [('missing','summary'),('source','missing')]:
                assert (await send(*args)).status_code==404
            assert d['ProviderHTTP']==[]
            first=await send();assert first.status_code==200
            assert len(d['ProviderHTTP'])==1
            async with f.factory() as db:
                c=await db.get(APICache,(await rows(f,APICache))[0].cache_key);c.response_content=tree(node(children=[node()]));await db.commit()
            for _ in range(2):
                r=await send();assert r.status_code==200
                assert '"llm_restructured": true' in r.text or '"llm_restructured":true' in r.text
            assert len(d['ProviderHTTP'])==2 and len(await rows(f,APICache))==1
            assert (await rows(f,APIQuota))[0].used_today==36
            d['ClosedPorts']=[remote.base_url.port,client.base_url.port]
