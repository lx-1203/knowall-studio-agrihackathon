"""Actual localhost JWT/SQLite/WebSocket text edit and reconnect receipts; no AI."""
import asyncio
import hashlib
import json
import os
import time
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import pytest
from app.websocket import sync_server
from tests.test_websocket_protocol_flow import wire, member
from tests.test_sync_text_transform import apply, insert, delete


async def receive(ws, kind=None):
    # Functional assertions have no 3s production SLA; keep a bounded 10s budget
    # on this shared Windows workspace. Wait durations remain in every receipt.
    for _ in range(15):
        frame = json.loads(await asyncio.wait_for(ws.recv(),10))
        if kind is None or frame.get('type') == kind:
            return frame
    raise AssertionError('Expected native frame was not received: '+str(kind))


CASES = [
    ('partial-before', delete(0,4), delete(2,3), 'f'),
    ('partial-after', delete(2,3), delete(0,4), 'f'),
    ('same-start-longer', delete(0,5), delete(0,2), 'f'),
    ('same-start-shorter', delete(0,2), delete(0,5), 'f'),
    ('contained', delete(2,2), delete(1,4), 'af'),
    ('insert-interior', insert(3), delete(1,4), 'af'),
    ('delete-interior', delete(1,4), insert(3), 'af'),
    ('insert-end', insert(5), delete(1,4), 'aXYf'),
    ('separate-fields', insert(2,path=['title']), insert(0,'Q',path=['body']), None),
    ('dot-key', insert(2,path=['a.b']), insert(0,'Q',path=['a','b']), None),
]


@pytest.fixture
async def receipt(wire, request):
    project = Path(__file__).resolve().parents[2]
    sources = ['backend/app/websocket/sync_server.py', 'backend/app/core/sync/__init__.py',
               'backend/app/database.py', 'backend/app/config.py',
               'backend/tests/test_websocket_protocol_flow.py', 'backend/tests/test_sync_text_transform.py',
               'backend/tests/test_sync_text_native_flow.py']
    proof = {'test':request.node.nodeid, 'start':datetime.now().astimezone().isoformat(),
             'port':urlparse(wire.url()).port, 'database':wire.factory.kw['bind'].url.database,
             'source_hashes':{s:hashlib.sha256((project/s).read_bytes()).hexdigest() for s in sources},
             'frames':[], 'sql_checks':[], 'external_ai_calls':0, 'simulation':[], 'receive_budget_seconds':10}
    try:
        yield proof
    finally:
        proof['end'] = datetime.now().astimezone().isoformat()
        dest = os.environ.get('SYNC_TEXT_PROOF_DIR')
        if dest:
            path = Path(dest)
            path.mkdir(parents=True,exist_ok=True)
            name = hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:16]+'.json'
            (path/name).write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding='utf-8')


async def sent(ws, payload, proof):
    proof['frames'].append({'direction':'sent','payload':deepcopy(payload)})
    await ws.send(json.dumps(payload))


async def got(ws, kind, proof):
    started = time.monotonic()
    frame = await receive(ws, kind)
    proof['frames'].append({'direction':'received','payload':frame,'wait_seconds':time.monotonic()-started})
    return frame


async def sql(proof):
    ops = await sync_server.sync_store.get_ops_since('document',0)
    proof['sql_checks'].append(deepcopy(ops))
    return ops


async def commit_pair(wire, proof, a, b, fail_once=False, monkeypatch=None):
    """Commit b; a still carries original version 0, so the server transforms it."""
    async with member(wire,'host') as host, member(wire,'student') as student:
        await receive(host,'presence')
        await sent(host, {'type':'operation','version':0,'data':b}, proof)
        assert (await got(host,'ack',proof))['data']['new_version'] == 1
        first = await got(student,'operation',proof)
        assert first['data'] == b and first['version'] == 1 and first['user_id'] == 'host'
        if fail_once:
            original = sync_server._room_op_log_append
            failed = False
            async def fail_second_once(room, op):
                nonlocal failed
                if op['version'] == 2 and not failed:
                    failed = True
                    raise OSError('explicit simulated disk failure before second commit')
                await original(room,op)
            monkeypatch.setattr(sync_server,'_room_op_log_append',fail_second_once)
            proof['simulation'].append('single append failure injected before SQL commit; real SQL otherwise')
            await sent(student, {'type':'operation','version':0,'data':a}, proof)
            error = await got(student,'error',proof)
            assert error['data']['code'] == 'PERSISTENCE_FAILED'
            assert sync_server._room_versions['document'] == 1
            assert [op['version'] for op in await sql(proof)] == [1]
            # A heartbeat on the peer detects any unexpected operation broadcast.
            await sent(host, {'type':'heartbeat','data':{}}, proof)
            peer_frame = await got(host,None,proof)
            assert peer_frame['type'] == 'heartbeat_ack', peer_frame
        await sent(student, {'type':'operation','version':0,'data':a}, proof)
        assert (await got(student,'ack',proof))['data']['new_version'] == 2
        second = await got(host,'operation',proof)
        assert second['version'] == 2 and second['user_id'] == 'student'
        ops = await sql(proof)
        assert [op['version'] for op in ops] == [1,2]
        assert [op['user_id'] for op in ops] == ['host','student']
        assert ops[1]['data'] == second['data']
    # Both sockets really close; only the room version cache loss is simulated.
    sync_server._room_versions.clear()
    proof['simulation'].append('clear process-local version cache; no process restart')
    async with member(wire,'student') as reconnected:
        await sent(reconnected, {'type':'reconnect','local_version':0,'data':{}}, proof)
        diff = (await got(reconnected,'sync_diff',proof))['data']
        assert diff == {'version':2,'ops':ops}
        await sent(reconnected, {'type':'reconnect','local_version':2,'data':{}}, proof)
        assert (await got(reconnected,'ack',proof))['data']['new_version'] == 2
    return ops


@pytest.mark.parametrize('name,a,b,want', CASES, ids=[x[0] for x in CASES])
async def test_stale_edit_broadcast_sql_and_real_reconnect(wire,receipt,name,a,b,want):
    ops = await commit_pair(wire,receipt,a,b)
    texts = {tuple(a['path']):'abcdef', tuple(b['path']):'abcdef'}
    for op in ops:
        path = tuple(op['data']['path'])
        texts[path] = apply(texts[path],op['data'])
    if want is None:
        assert texts[tuple(a['path'])] == 'abXYcdef'
        assert texts[tuple(b['path'])] == 'Qabcdef'
    else:
        assert texts[tuple(a['path'])] == want
    receipt['replayed_texts'] = {str(k):v for k,v in texts.items()}


async def test_stale_delete_retries_after_explicit_persistence_failure(wire,receipt,monkeypatch):
    a,b = delete(0,4),delete(2,3)
    ops = await commit_pair(wire,receipt,a,b,True,monkeypatch)
    assert apply(apply('abcdef',ops[0]['data']),ops[1]['data']) == 'f'
    receipt['replayed_texts'] = {'body':'f'}
