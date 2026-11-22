"""TXT byte contracts, real disposable SQL and actual localhost multipart recovery.

No external AI calls. Evidence defaults to pytest's temporary directory.
"""
import hashlib
import json
import os
import shutil
import socket
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from tempfile import mkdtemp

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import reading
from app.database import Base, get_db
from app.models import ReadingArticle, ReadingConversionCache

BODY = '学习需要坚持每天练习，通过阅读理解新的知识并及时复习才能逐步掌握内容。'
ROUTE = '/api/v1/reading/articles/upload-file'


@pytest.fixture
async def upload_flow(tmp_path, request):
    backend = Path(__file__).resolve().parents[1]
    private_parent = backend / '.tmp'
    private_parent.mkdir(exist_ok=True)
    private = Path(mkdtemp(prefix='test-round-05a4-db-', dir=private_parent))
    database_path = private / 'reading.sqlite'
    engine = create_async_engine('sqlite+aiosqlite:///' + database_path.as_posix())
    record = {'Test': request.node.nodeid, 'Start': datetime.now().astimezone().isoformat(),
              'ExternalAI': 'none: upload route does not invoke AI', 'Samples': [],
              'PrivateRoot': str(private), 'Native': False}
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Base.metadata.create_all(
            sync, tables=[ReadingArticle.__table__, ReadingConversionCache.__table__]))
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def db():
        async with factory() as session:
            yield session

    app = FastAPI()
    app.include_router(reading.router)
    app.dependency_overrides[get_db] = db
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                                     base_url='http://private-reading') as client:
            yield app, client, factory, record
    finally:
        await engine.dispose()
        # Exact, self-created root validated within the private parent before removal.
        assert private.resolve().parent == private_parent.resolve()
        assert private.name.startswith('test-round-05a4-db-')
        record['DatabaseSHA256'] = hashlib.sha256(database_path.read_bytes()).hexdigest()
        shutil.rmtree(private)
        record['PrivateRootRemoved'] = not private.exists()
        record['End'] = datetime.now().astimezone().isoformat()
        paths = ['app/api/reading.py', 'app/database.py', 'app/models/__init__.py',
                 'tests/test_reading_file_upload_flow.py']
        record['SourceHashes'] = {p: hashlib.sha256((backend/p).read_bytes()).hexdigest() for p in paths}
        output = Path(os.environ.get('KNOWALL_READING_FILE_EVIDENCE_DIR', str(tmp_path/'evidence')))
        output.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:16]
        (output/('upload-'+key+'.json')).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf8')


async def sample(client, record, method, path, **kwargs):
    response = await client.request(method, path, **kwargs)
    record['Samples'].append({'Method': method, 'URL': str(response.request.url),
                              'Status': response.status_code, 'Body': response.text})
    return response


async def rows(factory):
    async with factory() as db:
        return list((await db.execute(select(ReadingArticle).order_by(ReadingArticle.id))).scalars())


@pytest.mark.parametrize('filename,separator,bom', [
    ('lesson.txt', '\n', False), ('lesson.txt', '\r\n', False),
    ('lesson.txt', '\n', True), ('lesson.TXT', '\r\n', True),
    ('LESSON.TxT', '\n', False), ('中文资料.txt', '\n', True),
])
async def test_utf8_file_keeps_exact_title_body_and_sql(upload_flow, filename, separator, bom):
    _, client, factory, evidence = upload_flow
    title = '练习文章'
    body = BODY + separator + '第二段：保留英文 energy 与汉字。'
    raw = ('  '+title+'  '+separator+' '+body+' \n').encode('utf-8-sig' if bom else 'utf8')
    response = await sample(client, evidence, 'POST', ROUTE, files={'file': (filename, raw, 'text/plain')})
    assert response.status_code == 200, response.text
    article = response.json()['article']
    assert article['title'] == title and article['content'] == body
    saved, = await rows(factory)
    assert saved.title == title and saved.content == body
    assert saved.char_count == reading.count_chinese_chars(body)
    assert article['id'] == f'u{saved.id}'


@pytest.mark.parametrize('raw', [b'\xff\xfeinvalid', b'\xe4\xb8',
    ('标题\n'+BODY).encode('gb18030'), ('标题\n'+BODY).encode('utf-16'),
    b'title\n'+b'\xff'+BODY.encode('utf8')])
async def test_invalid_encoding_is_actionable_400_without_writes(upload_flow, raw):
    _, client, factory, evidence = upload_flow
    response = await sample(client, evidence, 'POST', ROUTE, files={'file': ('lesson.txt', raw, 'text/plain')})
    assert response.status_code == 400, response.text
    assert 'UTF-8' in response.json()['detail']
    assert await rows(factory) == []


@pytest.mark.parametrize('text', ['', '标题', '标题\n', '\n标题\n'+BODY,
    '  \r\n标题\r\n'+BODY, '\ufeff\n标题\n'+BODY, '标题\n短文'])
async def test_bad_layout_does_not_shift_first_line_or_write(upload_flow, text):
    _, client, factory, evidence = upload_flow
    response = await sample(client, evidence, 'POST', ROUTE, files={'file': ('lesson.txt', text.encode('utf8'), 'text/plain')})
    assert response.status_code == 400, response.text
    assert await rows(factory) == []


@pytest.mark.parametrize('filename', ['lesson.pdf', 'lesson.txt.exe', 'lesson', 'lesson.txt '])
async def test_wrong_extension_is_rejected_before_writes(upload_flow, filename):
    _, client, factory, evidence = upload_flow
    response = await sample(client, evidence, 'POST', ROUTE,
                            files={'file': (filename, ('标题\n'+BODY).encode('utf8'), 'text/plain')})
    assert response.status_code == 400, response.text
    assert await rows(factory) == []


@pytest.mark.parametrize('title,content,status', [
    ('边界标题', '学'*19, 400), ('边界标题', '学'*20, 200),
    ('题'*205, BODY, 200), ('📚 Reading 标题', BODY+' emoji 🌞 café', 200),
])
async def test_existing_length_contract_and_unicode_are_preserved(upload_flow, title, content, status):
    _, client, factory, evidence = upload_flow
    response = await sample(client, evidence, 'POST', ROUTE,
        files={'file': ('edge.txt', (title+'\n'+content).encode('utf8'), 'text/plain')})
    assert response.status_code == status, response.text
    saved = await rows(factory)
    if status == 200:
        assert len(saved) == 1 and saved[0].title == title[:200]
        assert saved[0].content == content
    else:
        assert saved == []


@pytest.mark.parametrize('payload', ['missing', 'empty-filename'])
async def test_multipart_validation_leaves_database_empty(upload_flow, payload):
    _, client, factory, evidence = upload_flow
    kwargs = {} if payload == 'missing' else {'files': {'file': ('', BODY.encode('utf8'), 'text/plain')}}
    response = await sample(client, evidence, 'POST', ROUTE, **kwargs)
    assert response.status_code == 422, response.text
    assert await rows(factory) == []


async def test_json_upload_contract_remains_compatible(upload_flow):
    _, client, factory, evidence = upload_flow
    response = await sample(client, evidence, 'POST', '/api/v1/reading/articles/upload',
                            json={'title': ' JSON标题 ', 'content': ' '+BODY+' '})
    assert response.status_code == 200, response.text
    saved, = await rows(factory)
    assert saved.title == 'JSON标题' and saved.content == BODY


@asynccontextmanager
async def native(app, evidence):
    import asyncio
    import uvicorn
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    uv = uvicorn.Server(uvicorn.Config(app, log_level='critical', lifespan='off', timeout_graceful_shutdown=2))
    job = asyncio.create_task(uv.serve(sockets=[listener]))
    evidence['Native'] = True
    try:
        async with asyncio.timeout(5):
            while not uv.started:
                if job.done():
                    await job
                    raise AssertionError('server did not start')
                await asyncio.sleep(.01)
        async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{listener.getsockname()[1]}',
                                     timeout=30, trust_env=False) as client:
            yield client
    finally:
        uv.should_exit = True
        try:
            await asyncio.wait_for(job, 5)
        finally:
            listener.close()
            evidence['ListenerClosed'] = listener.fileno() == -1
            evidence['ServerJobDone'] = job.done()


@pytest.mark.parametrize('failure', ['encoding', 'blank-title', 'short-body'])
async def test_real_http_failure_retry_persist_read_delete(upload_flow, failure):
    app, _, factory, evidence = upload_flow
    bad = {'encoding': b'\xff', 'blank-title': ('\n标题\n'+BODY).encode('utf8'),
           'short-body': '标题\n短文'.encode('utf8')}[failure]
    async with native(app, evidence) as client:
        rejected = await sample(client, evidence, 'POST', ROUTE, files={'file': ('lesson.txt', bad, 'text/plain')})
        assert rejected.status_code == 400, rejected.text
        assert await rows(factory) == []
        accepted = await sample(client, evidence, 'POST', ROUTE,
            files={'file': ('retry.TXT', ('恢复文章\r\n'+BODY).encode('utf-8-sig'), 'application/octet-stream')})
        assert accepted.status_code == 200, accepted.text
        article = accepted.json()['article']
        assert article['title'] == '恢复文章' and article['content'] == BODY
        assert len(await rows(factory)) == 1
        listed = await sample(client, evidence, 'GET', '/api/v1/reading/articles')
        assert listed.status_code == 200
        assert sum(item['id'] == article['id'] for item in listed.json()) == 1
        path = '/api/v1/reading/articles/' + article['id']
        detail = await sample(client, evidence, 'GET', path)
        assert detail.status_code == 200 and detail.json()['content'] == BODY
        removed = await sample(client, evidence, 'DELETE', path)
        assert removed.status_code == 200
        missing = await sample(client, evidence, 'GET', path)
        assert missing.status_code == 404 and await rows(factory) == []
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(ReadingConversionCache)) == 0
    assert evidence['ListenerClosed'] and evidence['ServerJobDone']
