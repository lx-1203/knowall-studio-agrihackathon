"""Real original-image bytes and JWT/SQLite state/preview contracts; no model calls."""
import hashlib
from io import BytesIO
import httpx
import pytest
from PIL import Image
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.database import Base, get_db
from app.models import User, Document
from app.core.auth import create_access_token
from app.api import learning, document_preview
from app.core.parsing.document_preview import preview_metadata, render_preview, PreviewError

@pytest.fixture
async def image_client(tmp_path):
    path=tmp_path/'original.png';Image.new('RGB',(80,160),'red').save(path)
    engine=create_async_engine('sqlite+aiosqlite:///'+(tmp_path/'state.sqlite').as_posix())
    sessions=async_sessionmaker(engine,expire_on_commit=False)
    async with engine.begin() as c:await c.run_sync(Base.metadata.create_all)
    async with sessions() as db:
        for user in ['a','b']:db.add(User(id=user,username=user,email=user+'@test.invalid',password_hash='unused',is_active=True))
        db.add(Document(id='image',filename='Original.png',file_type='png',sha256='namespace-is-not-source',local_path=str(path),page_count=99,status='ready',metadata_={'knowledge_owner_id':'a'}))
        await db.commit()
    async def database():
        async with sessions() as db:yield db
    app=FastAPI();app.include_router(learning.router);app.include_router(document_preview.router)
    app.dependency_overrides[get_db]=database
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer '+create_access_token('a')}) as client:
        yield client,path,sessions
    await engine.dispose()

URL='/api/v1/learning/documents/image/state/image_progress'
def request(source,revision=0,operation='first',**payload):
    return dict(account_id='a',base_revision=revision,operation_id=operation,source_version=source,
                payload=dict(page_number=1,zoom=1.7,zoom_basis='fit_width',**payload))

async def test_image_get_is_read_only_and_uses_actual_original_bytes(image_client):
    client,path,sessions=image_client
    response=await client.get(URL);assert response.status_code==200
    value=response.json();assert value['revision']==0 and value['payload'] is None
    assert value['current_page_count']==1 and value['current_source_version']==hashlib.sha256(path.read_bytes()).hexdigest()
    from app.models.material_state import MaterialState
    async with sessions() as db:assert await db.get(MaterialState,('a','image','image_progress')) is None

async def test_image_cas_receipt_and_account_isolation(image_client):
    client,_,_=image_client;source=(await client.get(URL)).json()['current_source_version']
    body=request(source);assert (await client.put(URL,json=body)).status_code==200
    second=request(source,1,'second');second['payload']['zoom']=2
    assert (await client.put(URL,json=second)).json()['revision']==2
    replay=await client.put(URL,json=body);assert replay.status_code==200 and replay.json()['payload']['zoom']==2
    assert (await client.put(URL,json=request(source,0,'late'))).status_code==409
    body['payload']['zoom']=2;assert (await client.put(URL,json=body)).status_code==409
    bad=request(source,2,'foreign');bad['account_id']='b';assert (await client.put(URL,json=bad)).status_code==403
    assert (await client.get(URL,headers={'Authorization':'Bearer '+create_access_token('b')})).status_code==404

async def test_image_replacement_refuses_old_preview_and_old_state(image_client):
    client,path,_=image_client;source=(await client.get(URL)).json()['current_source_version']
    assert (await client.put(URL,json=request(source))).status_code==200
    Image.new('RGB',(80,160),'blue').save(path)
    value=(await client.get(URL)).json();assert value['stale'] and value['source_version']==source
    assert value['current_source_version']!=source
    response=await client.get('/api/v1/documents/image/preview/1/image',params={'source_version':source});assert response.status_code==412
    assert (await client.put(URL,json=request(source,1,'late-source'))).status_code==409
    fresh=request(value['current_source_version'],1,'explicit-reanchor');assert (await client.put(URL,json=fresh)).json()['stale'] is False

async def test_invalid_image_positions_and_scales_are_rejected(image_client):
    client,_,_=image_client;source=(await client.get(URL)).json()['current_source_version']
    for field,value in [('page_number',2),('page_number',True),('zoom',0),('zoom',3.1),('zoom','1.5'),('zoom_basis','natural')]:
        body=request(source);body['payload'][field]=value
        assert (await client.put(URL,json=body)).status_code==422

def test_image_metadata_and_preview_share_first_frame_snapshot(tmp_path):
    path=tmp_path/'frames.gif';first=Image.new('RGB',(12,20),'red');second=Image.new('RGB',(12,20),'blue')
    first.save(path,save_all=True,append_images=[second],duration=100,loop=0)
    meta=preview_metadata(str(path),'gif');assert meta['page_count']==1
    assert meta['source_version']==hashlib.sha256(path.read_bytes()).hexdigest()
    png=render_preview(str(path),'gif',1,source_version=meta['source_version'])
    with Image.open(BytesIO(png)) as shown:assert shown.getpixel((0,0))==(255,0,0)
    with pytest.raises(PreviewError) as error:render_preview(str(path),'gif',2,source_version=meta['source_version'])
    assert error.value.status==404

def test_image_exif_orientation_and_transparency_keep_raw_source_version(tmp_path):
    path=tmp_path/'camera.jpg';image=Image.new('RGB',(12,20),'red');exif=image.getexif();exif[274]=6;image.save(path,exif=exif)
    meta=preview_metadata(str(path),'jpg')
    with Image.open(BytesIO(render_preview(str(path),'jpg',1,source_version=meta['source_version']))) as shown:assert shown.size==(20,12)
    assert meta['source_version']==hashlib.sha256(path.read_bytes()).hexdigest()
    transparent=tmp_path/'transparent.png';Image.new('RGBA',(10,10),(0,0,0,0)).save(transparent)
    meta=preview_metadata(str(transparent),'png')
    with Image.open(BytesIO(render_preview(str(transparent),'png',1,source_version=meta['source_version']))) as shown:assert shown.getpixel((0,0))==(255,255,255)
