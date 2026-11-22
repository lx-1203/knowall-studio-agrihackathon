"""Real localhost book HTTP/JWT/SQL; provider explicitly simulated."""
import asyncio,json
from contextlib import asynccontextmanager
import pytest
from fastapi import FastAPI,Request
from app.api import books
from app.database import get_db
from app.core.api_scheduler.adapters.openai import OpenAICompatAdapter
from app.models import APICache,APICallLog,APIQuota
from tests.test_book_structured_cache_flow import book_flow,cache_flow,flow,rows,OUTLINE,QUIZ,dump,outline,question
from tests.test_reading_native_stream_http_flow import server

@asynccontextmanager
async def endpoints(f,monkeypatch):
    d=f.evidence;d.update(AI='explicit localhost AI simulation; real book/JWT/SQL/cache',BackendHTTP=[],ProviderHTTP=[])
    provider=FastAPI()
    @provider.post('/v1/chat/completions')
    async def simulation(req:Request):
        body=await req.json();prompt=body['messages'][-1]['content'];kind=f.kind(prompt)
        d['ProviderHTTP'].append({'URL':str(req.url),'Kind':kind,'Payload':body,'Simulation':True,'User':req.headers['authorization']})
        return {'choices':[{'message':{'content':f.outputs[kind]}}],'usage':{'prompt_tokens':7,'completion_tokens':11}}
    backend=FastAPI();backend.include_router(books.router)
    async def db():
        async with f.factory() as session:yield session
    backend.dependency_overrides[get_db]=db
    async with server(provider) as remote:
        for user in ('alice','bob'):f.api._adapters['primary_'+user]=OpenAICompatAdapter(user+'-explicit-local-key',str(remote.base_url).rstrip('/')+'/v1','book-test')
        async with server(backend) as client:
            async def send(method,path,user='alice',**kwargs):
                r=await client.request(method,path,headers=getattr(f,user),**kwargs)
                d['BackendHTTP'].append({'Method':method,'URL':str(r.request.url),'User':user,'Status':r.status_code,'Body':r.text});return r
            try:yield send
            finally:
                await f.compiler.close();d['ClosedPorts']=[remote.base_url.port,client.base_url.port]

async def create_and_finish(f,send,user='alice'):
    r=await send('POST','/api/v1/books/create',user,json={'title':'相同教材','source_doc_ids':['source']});assert r.status_code==200,r.text
    identity=r.json()['id'];job=f.compiler._compilations.get(identity)
    if job:await asyncio.wait_for(asyncio.shield(job),10)
    detail=await send('GET','/api/v1/books/'+identity,user);assert detail.status_code==200,detail.text
    return identity,detail.json()

@pytest.mark.parametrize('kind,bad',[('outline',dump({'chapters':[None]})),('outline',dump(outline(subtitle=[]))),('quiz',dump({'questions':[None]})),('quiz',dump(question(options={})))])
async def test_native_bad_response_not_cached_and_same_book_can_recover(book_flow,monkeypatch,kind,bad):
    f=book_flow;f.outputs[kind]=bad
    async with endpoints(f,monkeypatch) as send:
        identity,first=await create_and_finish(f,send)
        assert first['status']=='completed' and first['progress']==1,first
        caches=await rows(f,APICache);assert len(caches)==3,'invalid outline/quiz was cached'
        logs=await rows(f,APICallLog);assert len(logs)==4 and sum(not x.success for x in logs)==1
        f.outputs[kind]=dump(OUTLINE if kind=='outline' else QUIZ)
        identity,recovered=await create_and_finish(f,send)
        assert recovered['status']=='completed' and recovered['subtitle']=='模拟教材'
        sections=recovered['chapters'][0]['sections'];assert sections[0]['title']=='能量转换'
        assert json.loads(sections[0]['blocks'][1]['content'])==QUIZ
        export=await send('GET',f'/api/v1/books/{identity}/export');assert export.status_code==200 and '哪种细胞器' in export.text and '明确模拟教材正文' in export.text
        await create_and_finish(f,send)
        expected=8 if kind=='outline' else 5
        assert len(f.evidence['ProviderHTTP'])==expected
        assert (await rows(f,APIQuota))[0].used_today==expected*18
        assert len(await rows(f,APICache))==(7 if kind=='outline' else 4)
        listing=await send('GET','/api/v1/books');assert len(listing.json()['books'])==3
        alien=await send('GET',f'/api/v1/books/{identity}','bob');assert alien.status_code==404
        alien_export=await send('GET',f'/api/v1/books/{identity}/export','bob');assert alien_export.status_code==404
        f.evidence['SQL']={'Cache':len(await rows(f,APICache)),'Logs':[x.success for x in await rows(f,APICallLog)],'Used':expected*18,'Provider':expected}

@pytest.mark.parametrize('kind',['outline','quiz'])
async def test_native_old_bad_cache_is_replaced_without_invalid_export(book_flow,monkeypatch,kind):
    f=book_flow
    async with endpoints(f,monkeypatch) as send:
        await create_and_finish(f,send);assert len(f.evidence['ProviderHTTP'])==4
        caches=await rows(f,APICache)
        chosen=next(c for c in caches if ('chapters' if kind=='outline' else 'questions') in c.response_content)
        async with f.factory() as db:
            row=await db.get(APICache,chosen.cache_key);row.response_content=dump({'chapters':[None]} if kind=='outline' else {'questions':[None]});await db.commit()
        identity,data=await create_and_finish(f,send);assert data['status']=='completed' and data['subtitle']=='模拟教材'
        export=await send('GET',f'/api/v1/books/{identity}/export');assert export.status_code==200 and '哪种细胞器' in export.text
        await create_and_finish(f,send)
        assert len(f.evidence['ProviderHTTP'])==5 and len(await rows(f,APICache))==4
        assert (await rows(f,APIQuota))[0].used_today==90
        f.evidence['SQL']={'Cache':4,'Logs':[x.success for x in await rows(f,APICallLog)],'Used':90,'Provider':5}

async def test_native_empty_sections_error_export_conflict_and_delete(book_flow,monkeypatch):
    f=book_flow;f.outputs['outline']=dump({'chapters':[{'title':'无節','sections':[]}]})
    async with endpoints(f,monkeypatch) as send:
        identity,data=await create_and_finish(f,send);assert data['status']=='error' and '没有可编译' in data['error'] and data['progress']<1
        export=await send('GET',f'/api/v1/books/{identity}/export');assert export.status_code==409
        deleted=await send('DELETE',f'/api/v1/books/{identity}');assert deleted.status_code==200
        missing=await send('GET',f'/api/v1/books/{identity}');assert missing.status_code==404
        assert len(f.evidence['ProviderHTTP'])==1
