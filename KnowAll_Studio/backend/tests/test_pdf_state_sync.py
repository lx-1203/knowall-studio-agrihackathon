"""Real PDF bytes, authenticated state routes and SQLite, isolated from production."""
import asyncio
import hashlib
import fitz
import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.database import Base, get_db
from app.models import User, Document
from app.core.auth import create_access_token
from app.api import learning, document_preview

@pytest.fixture
async def pdf_client(tmp_path):
    path=tmp_path/'original.pdf'
    with fitz.open() as source:
        for number in range(3): source.new_page().insert_text((40,60),'PAGE '+str(number+1))
        source.save(path)
    engine=create_async_engine('sqlite+aiosqlite:///'+(tmp_path/'state.sqlite').as_posix())
    sessions=async_sessionmaker(engine,expire_on_commit=False)
    async with engine.begin() as connection: await connection.run_sync(Base.metadata.create_all)
    async with sessions() as db:
        for user in ['a','b']: db.add(User(id=user,username=user,email=user+'@test.invalid',password_hash='unused',is_active=True))
        db.add(Document(id='pdf',filename='original.pdf',file_type='pdf',sha256='namespaced-not-raw',local_path=str(path),page_count=99,status='ready',metadata_={'knowledge_owner_id':'a'}))
        await db.commit()
    async def database():
        async with sessions() as db: yield db
    app=FastAPI();app.include_router(learning.router);app.include_router(document_preview.router)
    app.dependency_overrides[get_db]=database
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer '+create_access_token('a')}) as client:
        yield client,path,sessions
    await engine.dispose()

URL='/api/v1/learning/documents/pdf/state/pdf_progress'
def request(source,revision=0,operation='first',page=2,zoom=1.7):
    return dict(account_id='a',base_revision=revision,operation_id=operation,source_version=source,
                payload=dict(page_number=page,zoom=zoom,zoom_basis='fit_width'))

async def test_pdf_head_uses_raw_bytes_actual_count_and_independent_revision(pdf_client):
    client,path,_=pdf_client
    head=await client.get(URL);assert head.status_code==200
    value=head.json();assert value['current_source_version']==hashlib.sha256(path.read_bytes()).hexdigest()
    assert value['current_page_count']==3 and value['revision']==0 and value['payload'] is None
    written=await client.put(URL,json=request(value['current_source_version']));assert written.status_code==200
    assert written.json()['payload']==dict(page_number=2,zoom=1.7,zoom_basis='fit_width')
    assert (await client.get(URL.replace('pdf_progress','note'))).json()['revision']==0

async def test_pdf_boundaries_source_and_account_never_write_bad_state(pdf_client):
    client,_,_=pdf_client;head=(await client.get(URL)).json();source=head.get('current_source_version','missing')
    for page,zoom in [(0,1),(4,1),(True,1),(1,True),(1,0.49),(1,3.01),(1,'1')]:
        response=await client.put(URL,json=request(source,page=page,zoom=zoom));assert response.status_code==422
    bad=request(source);bad['payload']['zoom_basis']='pixels';assert (await client.put(URL,json=bad)).status_code==422
    assert (await client.put(URL,json=request('0'*64))).status_code==409
    bad=request(source);bad['account_id']='b';assert (await client.put(URL,json=bad)).status_code==403
    assert (await client.get(URL,headers={'Authorization':'Bearer '+create_access_token('b')})).status_code==404
    assert (await client.get(URL)).json()['revision']==0

async def test_pdf_bytes_change_without_parsed_text_marks_old_position_stale(pdf_client):
    client,path,_=pdf_client;source=(await client.get(URL)).json().get('current_source_version','missing')
    assert (await client.put(URL,json=request(source))).status_code==200
    # Change only metadata in the original bytes; page count and parsed chunks remain unchanged.
    with fitz.open(path) as pdf:
        pdf.set_metadata({'title':'new original version'});pdf.saveIncr()
    head=(await client.get(URL)).json();assert head['stale'] is True and head['current_page_count']==3
    assert head['source_version']==source and head['current_source_version']!=source
    response=await client.put(URL,json=request(source,1,'late',page=3));assert response.status_code==409
    assert response.json()['detail']['current']['payload']['page_number']==2

async def test_pdf_cas_and_replay_preserve_latest_state(pdf_client):
    client,_,_=pdf_client;source=(await client.get(URL)).json().get('current_source_version','missing')
    first=request(source)
    results=await asyncio.gather(client.put(URL,json=first),client.put(URL,json=request(source,operation='other',page=3)))
    assert sorted(r.status_code for r in results)==[200,409]
    winner=(await client.get(URL)).json()
    assert (await client.put(URL,json=request(source,1,'next',page=1,zoom=.5))).status_code==200
    initial=first if winner['payload']['page_number']==2 else request(source,operation='other',page=3)
    replay=await client.put(URL,json=initial);assert replay.status_code==200 and replay.json()['revision']==2

async def test_pdf_preview_version_matches_state_and_rejects_changed_page_bytes(pdf_client):
    client,path,_=pdf_client
    meta=(await client.get('/api/v1/documents/pdf/preview')).json()
    assert meta.get('source_version')==hashlib.sha256(path.read_bytes()).hexdigest()
    url='/api/v1/documents/pdf/preview/2/image'
    assert (await client.get(url,params={'source_version':meta['source_version']})).status_code==200
    with fitz.open(path) as pdf:
        pdf.set_metadata({'title':'replacement'});pdf.saveIncr()
    assert (await client.get(url,params={'source_version':meta['source_version']})).status_code==412
