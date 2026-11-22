"""Real private SQLite/JWT/book pipeline; AI and fallback search explicitly simulated."""
import asyncio
import hashlib
import json
import os
import socket
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy import event, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import books
from app.config import settings
from app.core.api_scheduler.client import _current_user_id
from app.core.auth import create_access_token
from app.core.book.compiler import BookCompiler
from app.core.book.models import Book
from app.database import Base, get_db
from app.models import APIKey, Document, DocumentChunk
from app.models.user import User
from tests.test_reading_native_stream_http_flow import server


@pytest.fixture
async def book_sources(tmp_path, monkeypatch, request):
    import app.database as database
    import app.core.book.compiler as module
    import app.core.tools as tools
    from app.core.api_scheduler import api_client
    path=tmp_path/'private-source.sqlite'
    engine=create_async_engine('sqlite+aiosqlite:///'+path.as_posix())
    async with engine.begin() as conn:
        await conn.run_sync(lambda c:Base.metadata.create_all(c,tables=[User.__table__,Document.__table__,DocumentChunk.__table__,APIKey.__table__]))
    factory=async_sessionmaker(engine,expire_on_commit=False)
    async with factory() as db:
        for account in ('alice','bob','disabled','local_user'):
            db.add(User(id=account,username=account,email=account+'@example.invalid',password_hash='unused',is_active=account!='disabled'))
        for identity, owner in [('alice-doc','alice'),('bob-doc','bob'),('empty-private','bob'),('local-private','local_user'),('legacy',None)]:
            db.add(Document(id=identity,filename=identity+'.txt',file_type='txt',sha256=identity,metadata_={'knowledge_owner_id':owner} if owner else {}))
        await db.flush()
        for identity in ('alice-doc','bob-doc','local-private','legacy'):
            db.add(DocumentChunk(id=identity+'-chunk',doc_id=identity,chunk_index=0,text_content='SOURCE_'+identity+'_CONFIDENTIAL'))
        await db.commit()
    monkeypatch.setattr(database,'async_session',factory)
    monkeypatch.setattr(settings,'desktop_require_login',False)
    monkeypatch.setenv('NO_PROXY','127.0.0.1,localhost')
    monkeypatch.setenv('no_proxy','127.0.0.1,localhost')
    compiler=BookCompiler()
    monkeypatch.setattr(module,'book_compiler',compiler)
    prompts=[]; searches=[]; sql=[]; requests=[]; closed_ports=[]
    async def simulated(prompt,**kwargs):
        prompts.append({'prompt':prompt,'owner':_current_user_id.get(),'simulation':True})
        if '教材策划' in prompt:return json.dumps({'chapters':[{'title':'模拟章节','sections':[{'title':'模拟小节'}]}]})
        if '测试题' in prompt:return json.dumps({'questions':[{'question':'明确模拟题','answer':'明确模拟答案'}]})
        if '一句话概括' in prompt:return '明确模拟重点'
        return '明确模拟教材正文'
    async def fallback(name,**kwargs):
        searches.append({'name':name,'owner':_current_user_id.get(),'simulation':True})
        return SimpleNamespace(success=True,output={'results':[]})
    monkeypatch.setattr(api_client,'chat',simulated)
    monkeypatch.setattr(tools,'execute_tool',fallback)
    @event.listens_for(engine.sync_engine,'before_cursor_execute')
    def observe(conn,cursor,statement,parameters,context,executemany):
        sql.append(statement)
    app=FastAPI();app.include_router(books.router)
    async def db_override():
        async with factory() as db:yield db
    app.dependency_overrides[get_db]=db_override
    headers={u:{'Authorization':'Bearer '+create_access_token(u)} for u in ('alice','bob','disabled','local_user')}
    token=_current_user_id.set('ambient-untrusted')
    start=datetime.now().astimezone().isoformat()
    sources={p:hashlib.sha256((Path(__file__).resolve().parents[1]/p).read_bytes()).hexdigest() for p in ['app/api/books.py','app/core/book/compiler.py','app/core/documents.py','app/core/auth.py','app/core/generation_access.py','app/core/book/block_generators.py']}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://book-source') as client:
            yield SimpleNamespace(factory=factory,engine=engine,compiler=compiler,app=app,headers=headers,prompts=prompts,searches=searches,sql=sql,requests=requests,closed_ports=closed_ports,client=client,path=path)
    finally:
        await compiler.close();_current_user_id.reset(token)
        await engine.dispose()
        for suffix in ('','-wal','-shm'):
            p=Path(str(path)+suffix)
            assert p.parent.resolve()==tmp_path.resolve()
            if p.exists():p.unlink()
        proof={'test':request.node.nodeid,'start':start,'end':datetime.now().astimezone().isoformat(),'sources':sources,'testSource':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'prompts':prompts,'searches':searches,'requests':requests,'closedPorts':closed_ports,'SQL':sql,'database':str(path),'databaseRemoved':not path.exists(),'liveAI':0,'simulation':'same-process AI and knowledge fallback only; real SQLite/JWT/book routes'}
        out=Path(os.environ.get('KNOWALL_BOOK_SOURCE_EVIDENCE',str(tmp_path/'evidence')));out.mkdir(parents=True,exist_ok=True)
        (out/(hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:16]+'.json')).write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf-8')
        request.node.user_properties.append(('book_source',json.dumps(proof,ensure_ascii=False)))


async def finish(f,book):
    job=f.compiler._compilations.get(book.id)
    if job:await asyncio.wait_for(asyncio.shield(job),5)
    await asyncio.sleep(0)
    return book


@pytest.mark.parametrize('owner,sources',[('alice',['bob-doc']),('bob',['alice-doc']),('alice',['legacy','bob-doc']),('alice',['empty-private']),('local_user',['bob-doc'])])
async def test_direct_pipeline_denies_before_model_or_fallback(book_sources,owner,sources):
    f=book_sources
    book=await f.compiler.create_book('源权限教材',source_doc_ids=sources,user_id=owner)
    await finish(f,book)
    assert book.status.value=='error',f.prompts
    assert '私有资料' in book.error and book.progress<1
    assert not f.prompts and not f.searches and not book.chapters


@pytest.mark.parametrize('owner,source',[('alice','alice-doc'),('bob','bob-doc'),('local_user','legacy'),('alice','legacy')])
async def test_gather_owner_and_legacy_sources_keep_real_content(book_sources,owner,source):
    f=book_sources
    context=await f.compiler._gather_context(Book(id='gather',title='合法教材',source_doc_ids=[source],user_id=owner))
    assert context=='SOURCE_'+source+'_CONFIDENTIAL'
    assert not f.searches and not f.prompts


async def test_consumed_first_ten_only_and_twenty_chunks_limit(book_sources):
    f=book_sources
    async with f.factory() as db:
        for i in range(25):db.add(DocumentChunk(id='extra-'+str(i),doc_id='alice-doc',chunk_index=i+1,text_content='A'*600))
        await db.commit()
    context=await f.compiler._gather_context(Book(id='limits',title='合法教材',source_doc_ids=['alice-doc']*10+['bob-doc'],user_id='alice'))
    chunks=context.split('\n\n')
    assert len(chunks)==20 and all(len(c)<=500 for c in chunks)
    assert 'bob-doc' not in context and not f.searches
    selects=[s for s in f.sql if s.lstrip().upper().startswith('SELECT')]
    assert len(selects)<=2,selects


async def test_missing_source_keeps_existing_fallback(book_sources):
    f=book_sources
    context=await f.compiler._gather_context(Book(id='missing',title='合法教材',source_doc_ids=['missing'],user_id='alice'))
    assert context=='合法教材' and len(f.searches)==1 and f.searches[0]['owner']=='ambient-untrusted'


async def test_deleted_source_cannot_leave_orphan_content_in_context(book_sources):
    f=book_sources
    async with f.factory() as db:
        db.add(DocumentChunk(id='orphan-chunk',doc_id='deleted-source',chunk_index=0,text_content='ORPHAN_PRIVATE_CONTENT'))
        await db.commit()
    context=await f.compiler._gather_context(Book(id='orphan',title='合法教材',source_doc_ids=['deleted-source'],user_id='alice'))
    assert context=='合法教材' and len(f.searches)==1


@pytest.mark.parametrize('empty',[True,False])
async def test_bulk_check_preserves_order_missing_duplicate_and_query_budget(book_sources,empty):
    from app.core import documents
    f=book_sources
    async with f.factory() as db:
        docs=await documents.assert_documents_access(db,[] if empty else ['legacy','missing','alice-doc','legacy'],user_id='alice')
    assert [d.id for d in docs]==([] if empty else ['legacy','alice-doc','legacy'])
    assert len([s for s in f.sql if s.lstrip().upper().startswith('SELECT')])==(0 if empty else 1)


async def test_bulk_check_refreshes_existing_session_metadata(book_sources):
    from app.core import documents
    f=book_sources
    async with f.factory() as reader:
        old=await reader.get(Document,'legacy');assert old.metadata_=={}
        async with f.factory() as writer:
            await writer.execute(update(Document).where(Document.id=='legacy').values(metadata_={'knowledge_owner_id':'bob'}));await writer.commit()
        with pytest.raises(HTTPException) as failure:
            await documents.assert_documents_access(reader,['legacy'],user_id='alice')
        assert failure.value.status_code==403


@asynccontextmanager
async def transport(f,native):
    async def send(client,method,path,user='alice',**kwargs):
        response=await client.request(method,path,headers=f.headers.get(user,{}),**kwargs)
        f.requests.append({'method':method,'path':path,'user':user,'status':response.status_code,'body':response.text,'native':native})
        return response
    if native:
        port=None
        try:
            async with server(f.app) as client:
                port=client.base_url.port
                yield lambda *args,**kw:send(client,*args,**kw)
        finally:
            if port is not None:
                with socket.socket() as listener:closed=listener.connect_ex(('127.0.0.1',port))!=0
                f.closed_ports.append({'port':port,'closed':closed})
                assert closed
    else:yield lambda *args,**kw:send(f.client,*args,**kw)


@pytest.mark.parametrize('native',[False,True])
@pytest.mark.parametrize('user,sources',[('alice',['bob-doc']),('bob',['alice-doc']),('anonymous',['alice-doc']),('anonymous',['local-private']),('alice',['legacy','bob-doc']),('alice',['empty-private'])])
async def test_create_rejects_unauthorized_sources_before_job(book_sources,native,user,sources):
    f=book_sources
    async with transport(f,native) as send:
        response=await send('POST','/api/v1/books/create',user,json={'title':'权限教材','source_doc_ids':sources,'user_id':'bob'})
        if response.status_code==200:
            await finish(f,f.compiler.get_book(response.json()['id']))
        assert response.status_code==403,response.text
        assert not f.compiler._books and not f.prompts and not f.searches


@pytest.mark.parametrize('native',[False,True])
@pytest.mark.parametrize('user,source',[('alice','alice-doc'),('bob','bob-doc'),('anonymous','legacy')])
async def test_allowed_source_completed_exported_and_result_private(book_sources,native,user,source):
    f=book_sources
    async with transport(f,native) as send:
        response=await send('POST','/api/v1/books/create',user,json={'title':'合法教材','source_doc_ids':[source],'user_id':'forged'})
        assert response.status_code==200,response.text
        identity=response.json()['id'];book=f.compiler.get_book(identity);await finish(f,book)
        detail=await send('GET','/api/v1/books/'+identity,user);assert detail.status_code==200 and detail.json()['status']=='completed',detail.text
        export=await send('GET',f'/api/v1/books/{identity}/export',user);assert export.status_code==200 and '明确模拟教材正文' in export.text
        forbidden=await send('GET',f'/api/v1/books/{identity}/export','bob' if user!='bob' else 'alice');assert forbidden.status_code==404
        assert len(f.prompts)==4 and not f.searches
        assert all('SOURCE_'+source+'_CONFIDENTIAL' in p['prompt'] for p in f.prompts)
        assert {p['owner'] for p in f.prompts}=={user if user!='anonymous' else 'local_user'}


@pytest.mark.parametrize('native',[False,True])
async def test_queued_owner_change_fails_before_model_and_later_request_recovers(book_sources,monkeypatch,native):
    f=book_sources;entered=asyncio.Event();release=asyncio.Event();gather=f.compiler._gather_context
    async def gate(book):
        entered.set();await release.wait();return await gather(book)
    monkeypatch.setattr(f.compiler,'_gather_context',gate)
    async with transport(f,native) as send:
        r=await send('POST','/api/v1/books/create',json={'title':'排队教材','source_doc_ids':['alice-doc']});assert r.status_code==200,r.text
        book=f.compiler.get_book(r.json()['id']);await asyncio.wait_for(entered.wait(),5)
        async with f.factory() as db:
            await db.execute(update(Document).where(Document.id=='alice-doc').values(metadata_={'knowledge_owner_id':'bob'}));await db.commit()
        release.set();await finish(f,book)
        detail=await send('GET','/api/v1/books/'+book.id);assert detail.status_code==200 and detail.json()['status']=='error'
        export=await send('GET',f'/api/v1/books/{book.id}/export');assert export.status_code==409
        assert not f.prompts and not f.searches
        r=await send('POST','/api/v1/books/create','bob',json={'title':'合法恢复教材','source_doc_ids':['alice-doc']});assert r.status_code==200
        recovered=await finish(f,f.compiler.get_book(r.json()['id']));assert recovered.status.value=='completed'
        assert len(f.prompts)==4 and {p['owner'] for p in f.prompts}=={'bob'}
