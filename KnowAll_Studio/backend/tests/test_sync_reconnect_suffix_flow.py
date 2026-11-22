"""Native JWT/SQLite/WS reconnect: only complete edit suffixes can recover."""
import asyncio
import hashlib
import json
import os
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import pytest
from sqlalchemy import select, delete
from app.models import SyncOpLog
from app.websocket import sync_server
from tests.test_websocket_protocol_flow import wire, member
from tests.test_sync_text_native_flow import receive


@pytest.fixture
async def proof(wire, request):
    project = Path(__file__).resolve().parents[2]
    sources = ['backend/app/websocket/sync_server.py', 'backend/app/core/sync/__init__.py',
               'backend/app/models/__init__.py', 'backend/tests/test_websocket_protocol_flow.py',
               'backend/tests/test_sync_reconnect_suffix_flow.py']
    p = {'test': request.node.nodeid, 'start': datetime.now().astimezone().isoformat(),
         'database': wire.factory.kw['bind'].url.database, 'port': urlparse(wire.url()).port,
         'sources': {s: hashlib.sha256((project/s).read_bytes()).hexdigest() for s in sources},
         'frames': [], 'sql': [], 'simulation': [], 'external_ai_calls': 0}
    try:
        yield p
    finally:
        p['end'] = datetime.now().astimezone().isoformat()
        if dest := os.environ.get('SYNC_SUFFIX_PROOF_DIR'):
            path = Path(dest); path.mkdir(parents=True, exist_ok=True)
            (path/(hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:16]+'.json')).write_text(
                json.dumps(p, ensure_ascii=False, indent=2), encoding='utf-8')


def edit(value='x'):
    return {'operation': 'insert', 'position': 0, 'value': value, 'path': ['body']}


async def seed(wire, version, missing=()):
    # True SQL fixture setup, not simulated get_ops_since. Emulate retained suffix.
    async with wire.factory() as db:
        db.add_all([SyncOpLog(room_id='document', version=v, user_id='host',
                             operation='insert', data=edit())
                    for v in range(max(1, version-99), version+1) if v not in missing])
        await db.commit()


async def sql(wire, proof):
    async with wire.factory() as db:
        rows = (await db.scalars(select(SyncOpLog).order_by(SyncOpLog.version))).all()
        data = [{'version': r.version, 'user_id': r.user_id, 'data': r.data} for r in rows]
        proof['sql'].append(deepcopy(data))
        return data


async def exchange(ws, payload, proof):
    proof['frames'].append({'sent': deepcopy(payload)})
    await ws.send(json.dumps(payload))
    result = await receive(ws)
    proof['frames'].append({'received': result})
    return result


async def recovery(ws, local, proof):
    return await exchange(ws, {'type': 'reconnect', 'local_version': local, 'data': {}}, proof)


BOUNDARIES = [(0,0,'ack'), (1,0,'sync_diff'), (49,0,'sync_diff'), (50,0,'sync_diff'),
              (51,0,'sync_diff'), (100,0,'sync_diff'), (101,1,'sync_diff'),
              (101,0,'SYNC_HISTORY_UNAVAILABLE'), (1,2,'INVALID_VERSION'), (0,1,'INVALID_VERSION')]


@pytest.mark.parametrize('version,local,expected', BOUNDARIES)
async def test_reconnect_requires_complete_suffix(wire, proof, version, local, expected):
    await seed(wire, version)
    before = await sql(wire, proof)
    async with member(wire) as ws:
        msg = await recovery(ws, local, proof)
        if expected in ('ack', 'sync_diff'):
            assert msg['type'] == expected, msg
            if expected == 'sync_diff':
                data = msg['data']
                assert data['version'] == version
                assert [x['version'] for x in data['ops']] == list(range(local+1, version+1))
                # A consumer applies the actual received edits, not document metadata.
                text = 'x'*local
                for op in data['ops']:
                    assert op['data'] == edit()
                    text = op['data']['value'] + text
                assert text == 'x'*version
        else:
            assert msg['type'] == 'error' and msg['data']['code'] == expected, msg
        assert (await exchange(ws, {'type':'heartbeat','data':{}}, proof))['type'] == 'heartbeat_ack'
    assert await sql(wire, proof) == before


@pytest.mark.parametrize('version,local,missing', [(51,0,1),(51,0,25),(51,0,51),(4,0,2),(101,1,50)])
async def test_missing_sql_history_never_claims_recovered(wire, proof, version, local, missing):
    await seed(wire, version)
    async with member(wire) as ws:
        # Initialize the actual room head before deleting its persisted history.
        # Otherwise deleting the highest record also lowers cold-cache MAX(version).
        async with wire.factory() as db:
            await db.execute(delete(SyncOpLog).where(SyncOpLog.room_id=='document', SyncOpLog.version==missing))
            await db.commit()
        proof['simulation'].append('true fixture SQL deletion after room head initialization; not real disk corruption')
        before = await sql(wire, proof)
        result = await recovery(ws, local, proof)
        assert result['type'] == 'error' and result['data']['code'] == 'SYNC_HISTORY_UNAVAILABLE', result
    assert await sql(wire, proof) == before


async def test_storage_error_is_explicit_and_retry_keeps_connection(wire, proof, monkeypatch):
    await seed(wire, 3)
    before = await sql(wire, proof)
    original = sync_server.sync_store.get_ops_since
    async def fail(*args):
        raise OSError('explicit simulated unavailable log read')
    async with member(wire) as ws:
        monkeypatch.setattr(sync_server.sync_store, 'get_ops_since', fail)
        proof['simulation'].append('single storage read failure; SQL and network real')
        result = await recovery(ws, 0, proof)
        assert result['type'] == 'error' and result['data']['code'] == 'PERSISTENCE_FAILED', result
        monkeypatch.setattr(sync_server.sync_store, 'get_ops_since', original)
        result = await recovery(ws, 0, proof)
        assert result['type'] == 'sync_diff' and result['data']['version'] == 3
    assert await sql(wire, proof) == before


async def test_recovery_snapshot_does_not_race_same_process_edit(wire, proof, monkeypatch):
    await seed(wire, 1)
    original = sync_server.sync_store.get_ops_since
    entered, release = asyncio.Event(), asyncio.Event()
    async def gated(*args):
        entered.set()
        await release.wait()
        return await original(*args)
    monkeypatch.setattr(sync_server.sync_store, 'get_ops_since', gated)
    proof['simulation'].append('event gate pauses log read; true edit/SQL/native sockets')
    async with member(wire,'host') as a, member(wire,'student') as b:
        await receive(a,'presence')
        await a.send(json.dumps({'type':'reconnect','local_version':0,'data':{}}))
        await asyncio.wait_for(entered.wait(), 10)
        lock = sync_server._room_locks['document']
        try:
            # The same lock used by real operations must protect recovery.
            assert lock.locked(), 'reconnect does not protect version/log snapshot'
            await b.send(json.dumps({'type':'operation','version':1,'data':edit('y')}))
        finally:
            release.set()
        diff = await receive(a,'sync_diff'); proof['frames'].append({'received':diff})
        assert diff['data']['version'] == 1 and [o['version'] for o in diff['data']['ops']] == [1]
        ack = await receive(b,'ack'); proof['frames'].append({'received':ack})
        assert ack['data']['new_version'] == 2
        op = await receive(a,'operation'); proof['frames'].append({'received':op})
        assert op['version'] == 2 and op['data'] == edit('y')
    assert [r['version'] for r in await sql(wire,proof)] == [1,2]


async def test_real_edits_trim_to_100_and_closed_socket_can_recover(wire, proof):
    async with member(wire) as ws:
        assert (await recovery(ws,0,proof))['type'] == 'ack'
        for v in range(101):
            msg = await exchange(ws, {'type':'operation','version':v,'data':edit()}, proof)
            assert msg['type'] == 'ack' and msg['data']['new_version'] == v+1
    assert [r['version'] for r in await sql(wire,proof)] == list(range(2,102))
    # A genuine new socket; cache loss alone is explicit simulation, not process restart.
    sync_server._room_versions.clear()
    proof['simulation'].append('clear version cache only; no process restart')
    async with member(wire) as ws:
        diff = await recovery(ws,1,proof)
        assert diff['type'] == 'sync_diff', diff
        assert [r['version'] for r in diff['data']['ops']] == list(range(2,102))
        rejected = await recovery(ws,0,proof)
        assert rejected['type'] == 'error' and rejected['data']['code'] == 'SYNC_HISTORY_UNAVAILABLE', rejected
        assert (await recovery(ws,101,proof)) == {'type':'ack','data':{'new_version':101}}
