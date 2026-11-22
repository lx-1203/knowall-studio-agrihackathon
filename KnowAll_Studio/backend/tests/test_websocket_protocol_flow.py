"""Real localhost WebSocket protocol against isolated SQLite and real sync routes."""
import asyncio
import json
import socket
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import uvicorn
from fastapi import FastAPI
from jose import jwt
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from app.database import Base
from app.models import Document, LiveParticipant, LiveSession, User
from app.websocket import sync_server


@pytest.fixture
async def wire(tmp_path, monkeypatch):
    import app.database
    import app.core.sync
    engine = create_async_engine('sqlite+aiosqlite:///' + (tmp_path/'wire.sqlite').as_posix())
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(app.database, 'async_session', factory)
    monkeypatch.setattr(app.core.sync, 'async_session', factory)
    config = SimpleNamespace(jwt_secret='isolated-wire-test-secret', jwt_algorithm='HS256', debug=False,
                             local_auto_login=False, desktop_require_login=True)
    monkeypatch.setattr(sync_server, 'settings', config)
    for attr in ['_rooms', '_room_versions', '_room_locks', '_last_heartbeat']:
        monkeypatch.setattr(sync_server, attr, {})
    monkeypatch.setattr(sync_server, '_background_tasks', set())
    async with factory() as db:
        for uid in ['host', 'student', 'stranger', 'disabled', 'admin']:
            db.add(User(id=uid, username=uid, email=uid+'@test.invalid', password_hash='!test',
                        is_active=uid != 'disabled', is_admin=uid == 'admin'))
        db.add(Document(id='document', filename='test.txt', file_type='txt', sha256='a'*64))
        db.add(LiveSession(id='exam', host_id='host', title='Exam', room_code='111111', status='active'))
        db.add(LiveSession(id='ended', host_id='host', title='Ended', room_code='222222', status='ended'))
        for uid in ['host', 'student']:
            db.add(LiveParticipant(id='p-'+uid, session_id='exam', user_id=uid, user_name=uid))
        await db.commit()
    app = FastAPI()
    app.include_router(sync_server.router)
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', lifespan='off', ws='websockets-sansio'))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    for _ in range(100):
        if server.started:
            break
        if task.done():
            await task
        await asyncio.sleep(0.02)
    assert server.started
    def token(uid, expired=False):
        return jwt.encode({'sub': uid, 'exp': datetime.now(timezone.utc)+timedelta(seconds=-10 if expired else 600)}, config.jwt_secret, algorithm='HS256')
    def url(uid='host', room='document', bearer=None, query_uid=None):
        from urllib.parse import urlencode
        params = {'doc_id': room, 'user_id': query_uid or uid, 'user_name': 'untrusted-name', 'token': token(uid) if bearer is None else bearer}
        return f'ws://127.0.0.1:{port}/ws/sync?' + urlencode(params)
    try:
        yield SimpleNamespace(url=url, token=token, factory=factory, config=config)
    finally:
        await sync_server.stop_background_tasks()
        server.should_exit = True
        try:
            await asyncio.wait_for(task, 5)
        except TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        listener.close()
        await engine.dispose()


async def receive(ws, kind=None):
    for _ in range(15):
        data = json.loads(await asyncio.wait_for(ws.recv(), 3))
        if kind is None or data.get('type') == kind:
            return data
    raise AssertionError('Expected message was not received: '+str(kind))


@asynccontextmanager
async def member(wire, uid='host', room='document'):
    async with connect(wire.url(uid, room), proxy=None) as ws:
        assert (await receive(ws, 'presence'))['data']['total_online'] >= 1
        yield ws


@pytest.mark.parametrize('identity', ['missing-token', 'invalid-token', 'expired', 'disabled', 'nonexistent', 'spoof'])
async def test_auth_rejection_closes_connection_before_room_presence(wire, identity):
    bearer = {'missing-token': '', 'invalid-token': 'invalid', 'expired': wire.token('host', True)}.get(identity)
    uid = identity if identity in ['disabled', 'nonexistent'] else 'host'
    async with connect(wire.url(uid, bearer=bearer, query_uid='student' if identity == 'spoof' else None), proxy=None) as ws:
        reply = await receive(ws)
        assert reply['type'] == 'error', reply
        assert reply['data']['code'] in ['AUTH_FAILED', 'FORBIDDEN']
        with pytest.raises(ConnectionClosed):
            await asyncio.wait_for(ws.recv(), 2)
    assert not sync_server._rooms


@pytest.mark.parametrize('uid,room', [('stranger', 'exam'), ('host', 'ended'), ('host', 'missing-room')])
async def test_room_membership_and_existence_are_enforced(wire, uid, room):
    async with connect(wire.url(uid, room), proxy=None) as ws:
        reply = await receive(ws)
        assert reply['type'] == 'error', reply
        assert reply['data']['code'] in ['FORBIDDEN', 'ROOM_NOT_FOUND']
    assert not sync_server._rooms


async def test_explicit_local_mode_binds_anonymous_identity(wire):
    wire.config.debug, wire.config.local_auto_login, wire.config.desktop_require_login = True, True, False
    async with connect(wire.url(bearer='', query_uid='admin'), proxy=None) as ws:
        users = (await receive(ws, 'presence'))['data']['online_users']
        assert users == [{'user_id': 'local_user', 'user_name': '本地用户'}]


@pytest.mark.parametrize('event', ['live_question_push', 'live_spotlight', 'live_ended', 'system_notify'])
async def test_student_cannot_emit_privileged_events(wire, event):
    async with member(wire, 'student', 'exam') as ws:
        await ws.send(json.dumps({'type': event, 'data': {'host_user_id': 'student'}}))
        reply = await receive(ws)
        assert reply['type'] == 'error' and reply['data']['code'] == 'FORBIDDEN', reply
        await ws.send('{"type":"heartbeat","data":{}}')
        assert (await receive(ws))['type'] == 'heartbeat_ack'


@pytest.mark.parametrize('payload', ['[]', 'null', '{"type":"cursor","data":[]}', '{"type":"operation","version":"x","data":{}}', '{"type":"reconnect","local_version":-1,"data":{}}',
    '{"type":"operation","version":0,"data":{"operation":"insert","position":-1,"value":"A"}}',
    '{"type":"operation","version":0,"data":{"operation":"insert","value":{}}}'])
async def test_bad_message_is_rejected_without_disconnect(wire, payload):
    async with member(wire) as ws:
        await ws.send(payload)
        reply = await receive(ws)
        assert reply['type'] == 'error' and reply['data']['code'] == 'INVALID_MESSAGE', reply
        await ws.send('{"type":"heartbeat","data":{}}')
        assert (await receive(ws))['type'] == 'heartbeat_ack'


async def test_two_clients_operation_reconnect_and_persistent_version(wire):
    op = {'operation': 'insert', 'position': 0, 'value': '你好', 'path': ['text']}
    async with member(wire, 'host') as a:
        async with member(wire, 'student') as b:
            presence = await receive(a, 'presence')
            assert {u['user_id'] for u in presence['data']['online_users']} == {'host', 'student'}
            await a.send(json.dumps({'type': 'operation', 'version': 0, 'data': op}))
            assert (await receive(a, 'ack'))['data']['new_version'] == 1
            broadcast = await receive(b, 'operation')
            assert broadcast['data'] == op and broadcast['user_id'] == 'host'
            await b.send('{"type":"reconnect","local_version":0,"data":{}}')
            diff = (await receive(b, 'sync_diff'))['data']
            assert diff['version'] == 1 and diff['ops'][0]['data'] == op
        await receive(a, 'presence')
    for _ in range(100):
        if not sync_server._rooms:
            break
        await asyncio.sleep(.01)
    assert not sync_server._rooms
    sync_server._room_versions.clear()  # Simulate a lost process cache; DB remains real.
    async with member(wire, 'student') as ws:
        await ws.send('{"type":"reconnect","local_version":0,"data":{}}')
        diff = (await receive(ws, 'sync_diff'))['data']
        assert diff['version'] == 1 and diff['ops'][0]['data'] == op


async def test_concurrent_operations_commit_in_version_order(wire, monkeypatch):
    original = sync_server._room_op_log_append
    async def delayed(room, op):
        if op['version'] == 1:
            await asyncio.sleep(.06)
        await original(room, op)
    monkeypatch.setattr(sync_server, '_room_op_log_append', delayed)
    async with member(wire, 'host') as a, member(wire, 'student') as b:
        await receive(a, 'presence')
        await asyncio.gather(*[ws.send(json.dumps({'type': 'operation', 'version': 0, 'data': {
            'operation': 'insert', 'position': 0, 'value': value, 'path': ['text']}})) for ws, value in [(a, 'A'), (b, 'B')]])
        ack_a, ack_b = await asyncio.gather(receive(a, 'ack'), receive(b, 'ack'))
        assert sorted([ack_a['data']['new_version'], ack_b['data']['new_version']]) == [1, 2]
        ops = await sync_server.sync_store.get_ops_since('document', 0)
        assert [op['version'] for op in ops] == [1, 2]
        assert ops[1]['data']['position'] == 1, ops


async def test_late_connection_initialization_cannot_reset_committed_version(wire, monkeypatch):
    original = sync_server.sync_store.get_room_version
    calls = 0
    first_returned = asyncio.Event()
    async def slow_first(room):
        nonlocal calls
        calls += 1
        call_number = calls
        value = await original(room)
        if call_number == 1:
            await asyncio.sleep(.15)
            first_returned.set()
        return value
    monkeypatch.setattr(sync_server.sync_store, 'get_room_version', slow_first)
    async with connect(wire.url('host'), proxy=None) as a, connect(wire.url('student'), proxy=None) as b:
        await receive(b, 'presence')
        await b.send('{"type":"operation","version":0,"data":{"operation":"insert","position":0,"value":"A"}}')
        assert (await receive(b, 'ack'))['data']['new_version'] == 1
        await receive(a, 'presence')
        await asyncio.wait_for(first_returned.wait(), 2)
        assert sync_server._room_versions['document'] == 1
        assert await sync_server.sync_store.get_room_version('document') == 1


async def test_persistence_failure_does_not_ack_or_advance_version_and_can_retry(wire, monkeypatch):
    original = sync_server._room_op_log_append
    calls = 0
    async def fail_first(*args):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError('controlled storage outage')
        await original(*args)
    monkeypatch.setattr(sync_server, '_room_op_log_append', fail_first)
    async with member(wire) as ws:
        operation = '{"type":"operation","version":0,"data":{"operation":"insert","position":0,"value":"A"}}'
        await ws.send(operation)
        result = await receive(ws)
        assert result['type'] == 'error' and result['data']['code'] == 'PERSISTENCE_FAILED', result
        assert sync_server._room_versions['document'] == 0
        assert await sync_server.sync_store.get_room_version('document') == 0
        await ws.send(operation)
        assert (await receive(ws, 'ack'))['data']['new_version'] == 1


async def test_host_event_and_admin_notification_are_authorized(wire):
    async with member(wire, 'host', 'exam') as host, member(wire, 'student', 'exam') as student:
        await receive(host, 'presence')
        await host.send('{"type":"live_question_push","data":{"questions":[]}}')
        result = await receive(student, 'live_question_push')
        assert result['data']['from_user_id'] == 'host'
    async with member(wire, 'admin', 'space_default') as ws:
        await ws.send('{"type":"system_notify","data":{"content":"Maintenance"}}')
        assert (await receive(ws, 'system_notify'))['data']['content'] == 'Maintenance'


@pytest.mark.parametrize('method', ['get_room_version', 'pop_offline_messages'])
async def test_initial_storage_failure_rejects_without_leaking_presence(wire, monkeypatch, method):
    from unittest.mock import AsyncMock
    monkeypatch.setattr(sync_server.sync_store, method, AsyncMock(side_effect=RuntimeError('controlled storage outage')))
    async with connect(wire.url(), proxy=None) as ws:
        result = await receive(ws)
        assert result['type'] == 'error' and result['data']['code'] == 'PERSISTENCE_FAILED'
        with pytest.raises(ConnectionClosed):
            await asyncio.wait_for(ws.recv(), 2)
    assert not sync_server._rooms and not sync_server._last_heartbeat


async def test_student_cannot_forge_broadcast_leaderboard_scores(wire):
    async with member(wire, 'host', 'exam') as host, member(wire, 'student', 'exam') as student:
        await receive(host, 'presence')
        await student.send('{"type":"live_leaderboard_update","data":{"leaderboard":[{"user_name":"student","score":9999}]}}')
        result = await receive(host, 'live_leaderboard_update')
        leaderboard = result['data']['leaderboard']
        assert len(leaderboard) == 2 and all(row['score'] == 0 for row in leaderboard), result


async def test_unpersisted_answer_event_is_rejected(wire):
    async with member(wire, 'student', 'exam') as student:
        await student.send('{"type":"live_answer_submitted","data":{"question_id":"missing","is_correct":true}}')
        result = await receive(student)
        assert result['type'] == 'error' and result['data']['code'] == 'INVALID_EVENT', result


async def test_answer_event_uses_persisted_result_for_authenticated_student(wire):
    from app.models import LiveAnswer, LiveQuestion, QuestionBank
    async with wire.factory() as db:
        db.add(QuestionBank(id='question', question_text='Question', question_type='choice', correct_answer='A'))
        db.add(LiveQuestion(id='live-question', session_id='exam', question_id='question', question_index=1, status='active'))
        db.add(LiveAnswer(id='answer', live_question_id='live-question', participant_id='p-student', is_correct=True, user_answer='A'))
        await db.commit()
    async with member(wire, 'host', 'exam') as host, member(wire, 'student', 'exam') as student:
        await receive(host, 'presence')
        await student.send('{"type":"live_answer_submitted","data":{"question_id":"live-question","is_correct":false}}')
        result = await receive(host, 'live_answer_submitted')
        assert result['data']['is_correct'] is True and result['data']['from_user_id'] == 'student'
        assert 'user_answer' not in result['data'] and 'correct_answer' not in result['data']


async def test_spotlight_resolves_saved_participant_and_delivers_to_host_and_target(wire):
    async with member(wire, 'host', 'exam') as host, member(wire, 'student', 'exam') as student:
        await receive(host, 'presence')
        # This is the current REST response shape: participant ID, without user IDs.
        await host.send('{"type":"live_spotlight","data":{"target_participant_id":"p-student","question_text":"Question"}}')
        a, b = await asyncio.gather(receive(host, 'live_spotlight'), receive(student, 'live_spotlight'))
        assert a['data']['host_user_id'] == 'host' and b['data']['target_user_id'] == 'student'


async def test_spotlight_cannot_use_an_unknown_participant(wire):
    async with member(wire, 'host', 'exam') as host:
        await host.send('{"type":"live_spotlight","data":{"target_participant_id":"missing","target_user_id":"student"}}')
        result = await receive(host)
        assert result['type'] == 'error' and result['data']['code'] == 'INVALID_EVENT', result


@pytest.mark.parametrize('path', ['/ws/rooms', '/ws/rooms/exam/members', '/ws/sse/exam'])
async def test_sync_http_helpers_cannot_bypass_authentication(wire, path):
    import httpx
    from urllib.parse import urlparse
    origin = 'http://' + urlparse(wire.url()).netloc
    async with httpx.AsyncClient(base_url=origin, trust_env=False) as client:
        async with client.stream('GET', path) as response:
            assert response.status_code == 401


async def test_sync_http_room_metadata_and_sse_use_room_membership(wire):
    import httpx
    from urllib.parse import urlparse
    origin = 'http://' + urlparse(wire.url()).netloc
    async with member(wire, 'host', 'exam'):
        async with httpx.AsyncClient(base_url=origin, trust_env=False) as client:
            auth = {'Authorization': 'Bearer ' + wire.token('stranger')}
            rooms = await client.get('/ws/rooms', headers=auth)
            assert rooms.status_code == 200 and rooms.json()['rooms'] == []
            assert (await client.get('/ws/rooms/exam/members', headers=auth)).status_code == 403
            async with client.stream('GET', '/ws/sse/exam', headers=auth) as response:
                assert response.status_code == 403
            auth = {'Authorization': 'Bearer ' + wire.token('student')}
            members = await client.get('/ws/rooms/exam/members', headers=auth)
            assert members.status_code == 200 and members.json()['members'][0]['user_id'] == 'host'
            async with client.stream('GET', '/ws/sse/exam', headers=auth) as response:
                assert response.status_code == 200
                async for line in response.aiter_lines():
                    if line.startswith('data: '):
                        assert json.loads(line[6:])['type'] == 'sync_full'
                        break


async def test_real_heartbeat_timeout_closes_and_cleans_room(wire, monkeypatch):
    monkeypatch.setattr(sync_server, 'HEARTBEAT_CHECK_PERIOD', .01)
    monkeypatch.setattr(sync_server, 'HEARTBEAT_TIMEOUT', .06)
    async with member(wire) as ws:
        await ws.send('{"type":"heartbeat","data":{}}')
        assert (await receive(ws))['type'] == 'heartbeat_ack'
        sync_server.start_background_tasks()
        with pytest.raises(ConnectionClosed) as closed:
            await asyncio.wait_for(ws.recv(), 2)
        assert closed.value.rcvd.code == 4002
    for _ in range(100):
        if not sync_server._rooms and not sync_server._last_heartbeat:
            break
        await asyncio.sleep(.01)
    assert not sync_server._rooms and not sync_server._last_heartbeat
