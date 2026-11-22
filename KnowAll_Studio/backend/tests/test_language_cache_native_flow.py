"""Real localhost language HTTP/SQLite/JWT/adapter; explicit simulated provider."""
import json,socket,hashlib
from pathlib import Path
import pytest
from fastapi import FastAPI,Request
from app.api import language
from app.database import get_db
from app.models import APICache,APICallLog,APIQuota,LanguageVocabulary
from app.core.api_scheduler.adapters.openai import OpenAICompatAdapter
from tests.test_language_cache_recovery_flow import language_flow,cache_flow,flow,rows,VALID
from tests.test_reading_native_stream_http_flow import server

@pytest.mark.parametrize('mode',['bad-json','bad-type','empty','long-word','old-cache','valid'])
async def test_native_vocabulary_same_request_recovery_and_mastery(language_flow,monkeypatch,mode):
    f=language_flow;d=f.evidence;d['AI']='explicit localhost HTTP simulation; no live AI'
    d['BackendHTTP']=[];d['ProviderHTTP']=[];ports=[];phase={'bad':mode not in ('old-cache','valid')}
    root=Path(__file__).resolve().parents[1]
    extra=['app/core/api_scheduler/adapters/openai.py','app/core/api_scheduler/rate_limiter.py','app/core/auth.py','tests/test_language_cache_native_flow.py','tests/test_language_cache_recovery_flow.py']
    d['NativeSourceBefore']={p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in extra}
    provider=FastAPI()
    @provider.post('/v1/chat/completions')
    async def simulation(req:Request):
        assert req.headers['authorization']=='Bearer explicit-local-language-key'
        payload=await req.json()
        assert payload['model']=='explicit-language-simulation'
        body={'bad-json':'not JSON','bad-type':'[{"word":false}]','empty':'[]',
            'long-word':json.dumps([{'word':'x'*129}])}.get(mode) if phase['bad'] else None
        if body is None:body='说明：\n'+json.dumps([*VALID,{'word':' energy ','definition':'duplicate'},{'word':' '}],ensure_ascii=False)+'\n结束'
        d['ProviderHTTP'].append({'URL':str(req.url),'Payload':payload,'Content':body,'Status':200,'Simulation':True})
        return {'model':'explicit-language-simulation','choices':[{'message':{'content':body},'finish_reason':'stop'}],
                'usage':{'prompt_tokens':7,'completion_tokens':11}}
    backend=FastAPI();backend.include_router(language.router)
    async def db():
        async with f.factory() as session:yield session
    backend.dependency_overrides[get_db]=db
    async with server(provider) as remote:
        ports.append(remote.base_url.port)
        f.api._adapters.clear()
        adapter=OpenAICompatAdapter('explicit-local-language-key',str(remote.base_url).rstrip('/')+'/v1','explicit-language-simulation')
        f.api._adapters['deepseek-chat_alice']=adapter
        async with server(backend) as client:
            ports.append(client.base_url.port)
            async def sample(method,url,**kwargs):
                r=await client.request(method,url,headers=f.alice,**kwargs)
                d['BackendHTTP'].append({'URL':str(r.request.url),'Method':method,'Status':r.status_code,'Body':r.text})
                return r
            route='/api/v1/language/vocabulary';args={'document_id':'source','summary_id':'summary'}
            first=await sample('POST',route+'/generate',json=args)
            if phase['bad']:
                assert first.status_code==500,first.text
                assert await rows(f,APICache)==[] and await rows(f,LanguageVocabulary)==[]
                assert not (await rows(f,APICallLog))[0].success
                phase['bad']=False
                first=await sample('POST',route+'/generate',json=args)
            assert first.status_code==200,first.text
            word=first.json()['result']['words'][0];assert first.json()['result']['total_words']==1
            marked=await sample('PATCH',route+'/'+word['id'],json={'mastered':True});assert marked.status_code==200
            if mode=='old-cache':
                async with f.factory() as session:
                    c=await session.get(APICache,(await rows(f,APICache))[0].cache_key)
                    c.response_content='[{"word":1}]';await session.commit()
            second=await sample('POST',route+'/generate',json=args)
            assert second.status_code==200 and second.json()['result']['words'][0]['id']==word['id']
            third=await sample('POST',route+'/generate',json=args);assert third.status_code==200
            listed=await sample('GET',route+'?document_id=source&mastered=true')
            assert listed.status_code==200 and listed.json()['total']==1
            assert listed.json()['words'][0]['mastered'] is True
            expected=1 if mode=='valid' else 2
            assert len(d['ProviderHTTP'])==expected
            caches=await rows(f,APICache);assert len(caches)==1 and 'energy' in caches[0].response_content
            words=await rows(f,LanguageVocabulary);assert len(words)==1 and words[0].mastered
            logs=await rows(f,APICallLog);quota=(await rows(f,APIQuota))[0]
            assert quota.user_id=='alice' and quota.used_today==expected*18
            d['SQL']={'Cache':len(caches),'Words':[{'ID':w.id,'Word':w.word,'Mastered':w.mastered} for w in words],
                      'QuotaUser':quota.user_id,'QuotaUsed':quota.used_today,'Logs':[{'Success':l.success,'Input':l.tokens_input,'Output':l.tokens_output} for l in logs]}
    d['ClosedPorts']=ports
    for port in ports:
        with socket.socket() as probe:assert probe.connect_ex(('127.0.0.1',port))!=0
    d['NativeSourceAfter']={p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in extra}
