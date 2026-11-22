"""Two-client classroom, share ownership and isolated chunk upload contracts."""
from types import SimpleNamespace
import asyncio

import httpx
import pytest
from fastapi import FastAPI, Request
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import live_session, share, sync_upload
from app.core.auth import get_optional_user
from app.database import Base, get_db
from app.models import KnowledgeTree, QuestionBank


@pytest.fixture
async def classroom():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add(QuestionBank(id='q-energy', question_type='single_choice', question_text='Which organelle produces energy?',
                            options=[{'label': 'A', 'text': 'Mitochondria'}, {'label': 'B', 'text': 'Cell wall'}], correct_answer='A'))
        db.add(KnowledgeTree(id='tree', name='Biology', tree_data={'nodes': []}))
        await db.commit()
    async def database():
        async with factory() as db:
            yield db
    async def user(request: Request):
        return SimpleNamespace(id=request.headers.get('x-test-user', 'host'))
    app = FastAPI()
    app.include_router(live_session.router)
    app.include_router(share.router)
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_optional_user] = user
    share._view_rate_limit.clear()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://class', headers={'x-test-user': 'host'}) as host:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://class', headers={'x-test-user': 'student'}) as student:
            yield host, student
    await engine.dispose()


async def room(host, student):
    created = await host.post('/api/v1/live/sessions', json={'title': 'Biology practice'})
    assert created.status_code == 200
    session = created.json()
    joined = await student.post(f"/api/v1/live/sessions/{session['session_id']}/join", json={'user_name': 'Student'})
    assert joined.status_code == 200
    pushed = await host.post(f"/api/v1/live/sessions/{session['session_id']}/push", json={'question_ids': ['q-energy']})
    assert pushed.status_code == 200
    return session, pushed.json()['questions'][0]['live_question_id']


async def test_host_student_question_answer_and_leaderboard(classroom):
    host, student = classroom
    session, qid = await room(host, student)
    prefix = f"/api/v1/live/sessions/{session['session_id']}"
    questions = (await student.get(prefix + '/questions')).json()['questions']
    assert questions[0]['correct_answer'] is None
    forbidden = await student.post(prefix + '/push', json={'question_ids': ['q-energy']})
    assert forbidden.status_code == 403
    answered = await student.post(prefix + '/answer', json={'live_question_id': qid, 'user_answer': 'A'})
    assert answered.status_code == 200 and answered.json()['is_correct']
    duplicate = await student.post(prefix + '/answer', json={'live_question_id': qid, 'user_answer': 'A'})
    assert duplicate.status_code == 400
    for client in [host, student]:
        rankings = (await client.get(prefix + '/leaderboard')).json()['leaderboard']
        assert rankings[0]['user_name'] == 'Student' and rankings[0]['score'] == 10
        assert rankings[0]['total_answered'] == 1
    assert (await host.post(prefix + '/end')).status_code == 200
    assert (await student.get('/api/v1/live/sessions/' + session['room_code'])).status_code == 404


async def test_rejoining_room_reuses_the_existing_participant(classroom):
    host, student = classroom
    session, _ = await room(host, student)
    repeated = await student.post(f"/api/v1/live/sessions/{session['session_id']}/join", json={'user_name': 'Student'})
    assert repeated.status_code == 200 and repeated.json()['already_joined']
    view = (await host.get('/api/v1/live/sessions/' + session['room_code'])).json()
    assert len([p for p in view['participants'] if p['user_id'] == 'student']) == 1


@pytest.mark.parametrize('answer', ['', ' '])
async def test_blank_answer_never_receives_correct_credit(classroom, answer):
    host, student = classroom
    session, qid = await room(host, student)
    result = await student.post(f"/api/v1/live/sessions/{session['session_id']}/answer", json={'live_question_id': qid, 'user_answer': answer})
    assert result.status_code in {400, 422} or (result.status_code == 200 and not result.json()['is_correct'])


async def test_question_from_different_room_cannot_be_answered(classroom):
    host, student = classroom
    first, qid = await room(host, student)
    second, _ = await room(host, student)
    response = await student.post(f"/api/v1/live/sessions/{second['session_id']}/answer", json={'live_question_id': qid, 'user_answer': 'A'})
    assert response.status_code == 404, response.text


async def test_ended_room_cannot_receive_new_answers(classroom):
    host, student = classroom
    session, qid = await room(host, student)
    prefix = f"/api/v1/live/sessions/{session['session_id']}"
    await host.post(prefix + '/end')
    response = await student.post(prefix + '/answer', json={'live_question_id': qid, 'user_answer': 'A'})
    assert response.status_code in {400, 409}, response.text


async def test_share_access_code_revocation_and_owner(classroom):
    host, student = classroom
    created = await host.post('/api/v1/share/create', json={'resource_type': 'knowledge_tree', 'resource_id': 'tree'})
    assert created.status_code == 200
    link = created.json()
    view = '/api/v1/share/view/' + link['share_id']
    assert (await student.get(view, params={'access_code': 'wrong'})).status_code == 403
    assert (await student.get(view, params={'access_code': link['access_code']})).json()['data']['name'] == 'Biology'
    denied = await student.delete('/api/v1/share/' + link['share_id'])
    assert denied.status_code in {403, 404}, denied.text
    assert (await host.delete('/api/v1/share/' + link['share_id'])).status_code == 200
    assert (await student.get(view, params={'access_code': link['access_code']})).status_code == 404


@pytest.fixture
async def chunks(tmp_path, monkeypatch):
    from app.core import sync
    upload_dir = tmp_path / 'uploads'
    chunk_dir = upload_dir / 'chunks'
    chunk_dir.mkdir(parents=True)
    monkeypatch.setattr(sync_upload, 'UPLOAD_DIR', upload_dir)
    monkeypatch.setattr(sync_upload, 'CHUNK_DIR', chunk_dir)
    monkeypatch.setattr(sync_upload, 'CHUNK_SIZE', 4)
    monkeypatch.setattr(sync_upload, '_upload_sessions', {})
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(sync, 'async_session', async_sessionmaker(engine, expire_on_commit=False))
    app = FastAPI()
    app.include_router(sync_upload.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://chunks') as client:
        yield client, upload_dir
    await engine.dispose()


async def start_upload(client, filename='biology.txt', size=8):
    response = await client.post('/api/upload/init', json={'filename': filename, 'file_size': size})
    assert response.status_code == 200
    return response.json()['data']['upload_id']


async def test_out_of_order_duplicate_chunks_complete_and_download(chunks):
    client, _ = chunks
    uid = await start_upload(client)
    base = '/api/upload/' + uid
    await client.put(base + '/chunk/1', content=b'EFGH')
    duplicate = await client.put(base + '/chunk/1', content=b'EFGH')
    assert duplicate.json()['data']['received'] == 4
    assert (await client.post(base + '/complete')).status_code == 400
    await client.put(base + '/chunk/0', content=b'ABCD')
    complete = await client.post(base + '/complete')
    assert complete.status_code == 200
    download = await client.get(complete.json()['data']['url'])
    assert download.status_code == 200 and download.content == b'ABCDEFGH'
    repeated = await client.post(base + '/complete')
    assert repeated.json()['data']['file_id'] == complete.json()['data']['file_id']
    # Download mapping survives losing the in-memory upload session.
    sync_upload._upload_sessions.clear()
    assert (await client.get(complete.json()['data']['url'])).content == b'ABCDEFGH'


@pytest.mark.parametrize('index', [-1, 2])
async def test_chunk_index_outside_declared_range_is_rejected(chunks, index):
    client, _ = chunks
    uid = await start_upload(client)
    response = await client.put(f'/api/upload/{uid}/chunk/{index}', content=b'ABCD')
    assert response.status_code in {400, 422}


async def test_bad_hash_is_rejected_before_writing(chunks):
    client, directory = chunks
    uid = await start_upload(client)
    response = await client.put(f'/api/upload/{uid}/chunk/0', content=b'ABCD', headers={'X-Chunk-Hash': 'sha256=' + '0'*64})
    assert response.status_code == 400
    assert list((directory / 'chunks').iterdir()) == []


async def test_wrong_chunk_size_and_unknown_hash_algorithm_are_readable_errors(chunks):
    client, directory = chunks
    uid = await start_upload(client)
    assert (await client.put(f'/api/upload/{uid}/chunk/0', content=b'ABC')).status_code == 400
    invalid = await client.put(f'/api/upload/{uid}/chunk/0', content=b'ABCD', headers={'X-Chunk-Hash': 'invalid=abcd'})
    assert invalid.status_code == 400
    assert list((directory / 'chunks').iterdir()) == []


async def test_download_does_not_accept_filename_substrings(chunks):
    client, directory = chunks
    (directory / 'secret-not-a-file-id.txt').write_bytes(b'private')
    assert (await client.get('/api/download/secret')).status_code == 404


async def test_concurrent_completion_returns_the_same_saved_file(chunks):
    client, directory = chunks
    uid = await start_upload(client, size=4)
    await client.put(f'/api/upload/{uid}/chunk/0', content=b'ABCD')
    first, second = await asyncio.gather(client.post(f'/api/upload/{uid}/complete'), client.post(f'/api/upload/{uid}/complete'))
    assert first.status_code == second.status_code == 200
    assert first.json()['data']['file_id'] == second.json()['data']['file_id']
    assert len([file for file in directory.iterdir() if file.is_file()]) == 1


@pytest.mark.parametrize('filename', ['../escape.txt', 'C:\\escape.txt', '..\\escape.txt'])
async def test_upload_filename_cannot_escape_private_directory(chunks, filename):
    client, _ = chunks
    response = await client.post('/api/upload/init', json={'filename': filename, 'file_size': 4})
    assert response.status_code in {400, 422}
