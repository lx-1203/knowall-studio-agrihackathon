"""Observe actual planning/compiling/completed HTTP states while local AI waits."""
import asyncio,hashlib
from pathlib import Path
import pytest
from fastapi import FastAPI,Request
from app.api import books
from app.database import get_db
from app.models import APICache,APIQuota
from app.core.api_scheduler.adapters.openai import OpenAICompatAdapter
from tests.test_book_structured_cache_flow import book_flow,cache_flow,flow,rows
from tests.test_reading_native_stream_http_flow import server

async def test_native_compiling_progress_and_export_conflict_until_body_finishes(book_flow):
    f=book_flow;d=f.evidence;path=Path(__file__).resolve()
    d.update(AI='explicit localhost AI simulation; real book progress/HTTP/JWT/SQL',BackendHTTP=[],ProviderHTTP=[],PhaseTestSourceBefore=hashlib.sha256(path.read_bytes()).hexdigest())
    entered=asyncio.Event();release=asyncio.Event();provider=FastAPI();backend=FastAPI();backend.include_router(books.router)
    @provider.post('/v1/chat/completions')
    async def simulation(req:Request):
        payload=await req.json();kind=f.kind(payload['messages'][-1]['content'])
        d['ProviderHTTP'].append({'URL':str(req.url),'Kind':kind,'Payload':payload,'Simulation':True})
        if kind=='text':entered.set();await release.wait()
        return {'choices':[{'message':{'content':f.outputs[kind]}}],'usage':{'prompt_tokens':7,'completion_tokens':11}}
    async def db():
        async with f.factory() as session:yield session
    backend.dependency_overrides[get_db]=db
    try:
        async with server(provider) as remote:
            f.api._adapters['primary_alice']=OpenAICompatAdapter('alice-explicit-local-key',str(remote.base_url).rstrip('/')+'/v1','book-test')
            async with server(backend) as client:
                async def send(method,url,**kwargs):
                    r=await client.request(method,url,headers=f.alice,**kwargs)
                    d['BackendHTTP'].append({'Method':method,'URL':str(r.request.url),'Status':r.status_code,'Body':r.text});return r
                try:
                    created=await send('POST','/api/v1/books/create',json={'title':'可观察教材','source_doc_ids':['source']});assert created.status_code==200 and created.json()['status']=='planning'
                    identity=created.json()['id'];await asyncio.wait_for(entered.wait(),5)
                    progress=await send('GET',f'/api/v1/books/{identity}');assert progress.status_code==200 and progress.json()['status']=='compiling' and progress.json()['progress']==.2
                    unavailable=await send('GET',f'/api/v1/books/{identity}/export');assert unavailable.status_code==409
                    listing=await send('GET','/api/v1/books');assert listing.json()['books'][0]['status']=='compiling'
                    release.set();await asyncio.wait_for(asyncio.shield(f.compiler._compilations[identity]),10)
                    complete=await send('GET',f'/api/v1/books/{identity}');assert complete.status_code==200 and complete.json()['status']=='completed' and complete.json()['progress']==1
                    export=await send('GET',f'/api/v1/books/{identity}/export');assert export.status_code==200 and '哪种细胞器' in export.text
                    assert len(d['ProviderHTTP'])==4 and len(await rows(f,APICache))==4 and (await rows(f,APIQuota))[0].used_today==72
                    d['ObservedStates']=['planning','compiling','completed'];d['SQL']={'Cache':4,'Provider':4,'Used':72}
                finally:release.set();await f.compiler.close();d['ClosedPorts']=[remote.base_url.port,client.base_url.port]
    finally:release.set();d['PhaseTestSourceAfter']=hashlib.sha256(path.read_bytes()).hexdigest()
