"""Loopback-only isolated real-PDF/JWT/SQL fixture; no production DB or model calls."""
import argparse
import hashlib
import os
import sys
import mimetypes
from pathlib import Path
from contextlib import asynccontextmanager
parser=argparse.ArgumentParser();parser.add_argument('--db',required=True);parser.add_argument('--assets',required=True);parser.add_argument('--port',type=int,required=True)
args=parser.parse_args();root=Path(__file__).resolve().parents[3]
os.environ['DATABASE_URL']='sqlite+aiosqlite:///'+Path(args.db).resolve().as_posix()
os.environ['JWT_SECRET']=os.environ['CROSS_TEST_JWT'];os.environ['DEBUG']='false'
sys.path.insert(0,str(root/'KnowAll_Studio/backend'))
import fitz
from fastapi import FastAPI,Depends,HTTPException,Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from app.database import Base,engine,async_session,get_db
from app.models import User,Document
from app.core.auth import get_current_user,hash_password
from app.api import auth,documents,learning,document_preview
from app.core.api_scheduler import api_client
requests=[];ai_calls=0
async def forbid(*args,**kwargs):
    global ai_calls
    ai_calls+=1;raise AssertionError('Real AI forbidden')
api_client.generate=forbid
pdf_path=Path(args.db).parent/'original.pdf'
async def seed():
    async with engine.begin() as connection:await connection.run_sync(Base.metadata.create_all)
    async with async_session() as db:
        if await db.get(User,'cross-a'):return
        with fitz.open() as pdf:
            for number in range(3):pdf.new_page().insert_text((40,60),'Original page '+str(number+1))
            pdf.save(pdf_path)
        for suffix in ['a','b']:
            db.add(User(id='cross-'+suffix,username='cross_'+suffix,email='cross_'+suffix+'@example.invalid',password_hash=hash_password('LocalTestOnly_2026!'),is_active=True))
            db.add(Document(id='pdf-'+suffix,filename='Original.pdf',file_type='pdf',local_path=str(pdf_path),sha256='namespace-'+suffix,page_count=99,status='ready',metadata_={'knowledge_owner_id':'cross-'+suffix}))
        db.add(Document(id='pdf-offline',filename='Offline.pdf',file_type='pdf',local_path=str(pdf_path),sha256='namespace-offline',page_count=99,status='ready',metadata_={'knowledge_owner_id':'cross-a'}))
        await db.commit()
@asynccontextmanager
async def lifespan(app):
    await seed();yield
    await engine.dispose()
app=FastAPI(lifespan=lifespan)
mimetypes.add_type('application/javascript','.mjs')  # Windows registry otherwise serves workers as text/plain.
for router in [auth.router,documents.router,learning.router,document_preview.router]:app.include_router(router)
app.mount('/__client',StaticFiles(directory=args.assets),name='client')
app.mount('/pdfjs',StaticFiles(directory=root/'KnowAll_Studio/frontend/public/pdfjs'),name='pdfjs')
@app.middleware('http')
async def trace(request:Request,call_next):
    response=await call_next(request)
    if request.url.path.startswith('/api/'):
        requests.append(dict(method=request.method,path=request.url.path,status=response.status_code,versioned='source_version' in request.query_params))
    return response
@app.get('/__test/health')
async def health():return dict(pid=os.getpid(),ai_calls=ai_calls)
@app.get('/__test/page/{kind}',response_class=HTMLResponse)
async def page(kind:str):
    if kind not in ['mobile','desktop']:raise HTTPException(404)
    return '<!doctype html><meta charset="utf-8"><title>PDF synchronization validation</title><link rel="stylesheet" href="/__client/'+kind+'.css"><div id="app"></div><script type="module">import * as client from "/__client/'+kind+'.js";window.client=client;</script>'
@app.get('/__test/trace')
async def read_trace(user=Depends(get_current_user)):
    if user.id!='cross-a':raise HTTPException(403)
    return dict(pid=os.getpid(),requests=requests,ai_calls=ai_calls,worker_pids=list((document_preview._renderer._processes or {}).keys()) if document_preview._renderer else [])
@app.post('/__test/source-change')
async def change_source(user=Depends(get_current_user)):
    if user.id!='cross-a':raise HTTPException(403)
    with fitz.open(pdf_path) as pdf:
        pdf.set_metadata({'title':'new version'});pdf.saveIncr()
    return dict(version=hashlib.sha256(pdf_path.read_bytes()).hexdigest())
@app.post('/__test/close-renderer')
async def close_renderer(request:Request):
    if request.headers.get('x-fixture-secret')!=os.environ['CROSS_TEST_JWT']:raise HTTPException(403)
    closed_pids=[]
    if document_preview._renderer:
        renderer=document_preview._renderer;document_preview._renderer=None
        closed_pids=list((renderer._processes or {}).keys())
        import asyncio
        await asyncio.to_thread(renderer.shutdown,wait=True,cancel_futures=True)
    return dict(closed=True,worker_pids=closed_pids)
if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=args.port,log_level='warning')
