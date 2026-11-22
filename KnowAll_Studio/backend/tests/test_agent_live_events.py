"""Real TCP/JWT/temporary SQL/native adapters; explicit local simulation, paid calls=0."""
import asyncio
import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from sqlalchemy import event, select, func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api import agent_runs
from app.core.agent_runtime import store
from app.core.agent_runtime.runner import run_readonly
from app.core.agent_runtime.contracts import AgentExecutionError
from app.core.api_scheduler.adapters.base import ToolCall, ToolResponse
from app.models import AgentRun, Conversation, Message
from app.models.agent_run import utc_now
from tests.test_agent_readonly_http import native, configure, provider_reply, PATH
from tests.test_agent_readonly_runner import Bridge
from tests.test_reading_native_stream_http_flow import server

OUT = Path(__file__).resolve().parents[2] / 'docs/open-source-integration/native-agent-events-2026-10-11/backend'


def record(request, **data):
    OUT.mkdir(parents=True, exist_ok=True)
    data.update(test=request.node.nodeid, at=datetime.now(timezone.utc).isoformat(),
                auth='real JWT', database='disposable SQLite',
                provider='explicit localhost simulation/native HTTP adapter', paid_calls=0)
    (OUT / (hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:16] + '.json')).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding='utf8')


def simulation(calls, *, arrived=None, release=None, fail=False):
    app = FastAPI()
    @app.post('/v1/chat/completions')
    @app.post('/v1/messages')
    async def model(req: Request):
        payload = await req.json(); calls.append(payload)
        protocol = 'anthropic' if req.url.path.endswith('messages') else 'openai'
        if len(calls) == 1:
            return provider_reply(protocol, name='read_document_chunk', args={'chunk_id': 'a-chunk'})
        if arrived:
            arrived.set(); await release.wait()
        return provider_reply(protocol, text='消耗氧气 [S99]' if fail else '消耗氧气 [S1]')
    return app


def parse(response):
    assert response.status_code == 200, response.text
    assert response.headers['content-type'].startswith('text/event-stream')
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]


def validate(events):
    assert [e['seq'] for e in events] == list(range(1, len(events) + 1))
    for e in events:
        assert datetime.fromisoformat(e['at'].replace('Z', '+00:00')).utcoffset() == timedelta(0)
        assert not {'text', 'content', 'arguments', 'prompt', 'raw_content'} & e.keys()


async def post(n, **extra):
    return await n.client.post(PATH + '/answer/stream', headers=n.headers('alice'),
                               json={'message': '解释细胞呼吸', 'model': 'openai', **extra})


@pytest.mark.parametrize('protocol', ['openai', 'anthropic'])
async def test_live_steps_are_committed_before_blocked_model_finishes_and_final_is_atomic(native, request, protocol):
    n = native; calls = []; arrived = asyncio.Event(); release = asyncio.Event(); received = []
    async with server(simulation(calls, arrived=arrived, release=release)) as remote:
        await configure(n, remote, protocol)
        async with n.client.stream('POST', PATH + '/answer/stream', headers=n.headers('alice'),
                                   json={'message': '解释细胞呼吸', 'model': protocol}) as response:
            assert response.status_code == 200, await response.aread()
            try:
                async with asyncio.timeout(5):
                    async for line in response.aiter_lines():
                        if not line.startswith('data: '): continue
                        item = json.loads(line[6:]); received.append(item)
                        if item['kind'] == 'step':
                            async with n.factory() as db:
                                row = await db.get(AgentRun, item['run_id'])
                                assert row.steps[item['event']['seq'] - 1] == item['event']
                                if item['event']['type'] == 'run_completed':
                                    message = await db.get(Message, row.assistant_message_id)
                                    assert row.status == 'completed' and message.role == 'assistant'
                                else:
                                    assert row.status == 'running'
                            if item['event']['type'] == 'step_started' and item['event'].get('step') == 2:
                                await asyncio.wait_for(arrived.wait(), 3)
                                assert not release.is_set()
                                snapshot = await n.client.get(PATH + '/runs/' + item['run_id'] + '/events?after=2', headers=n.headers('alice'))
                                assert snapshot.status_code == 200
                                assert all(e['seq'] > 2 for e in snapshot.json()['events'])
                                assert snapshot.json()['recovery_state'] == 'active'
                                release.set()
            finally: release.set()
    assert received[-1]['kind'] == 'result'
    steps = [e['event'] for e in received if e['kind'] == 'step']; validate(steps)
    result = received[-1]['result']; assert result['steps'] == steps
    assert result['run_id'] == received[-1]['run_id'] and result['assistant_message_id']
    assert steps[-1]['type'] == 'run_completed' and len(calls) == 2
    snapshot = (await n.client.get(PATH + '/runs/' + result['run_id'] + '/events?after=999', headers=n.headers('alice'))).json()
    assert snapshot['events'] == [] and snapshot['last_seq'] == len(steps) and snapshot['recovery_state'] == 'terminal'
    record(request, packets=received, live_before_provider_complete=True, final_message_atomic=True, provider_calls=len(calls))


async def test_tcp_disconnect_keeps_committed_tools_then_cancelled_without_saved_answer(native, request):
    n = native; calls = []; arrived = asyncio.Event(); release = asyncio.Event(); run_id = None
    async with server(simulation(calls, arrived=arrived, release=release)) as remote:
        await configure(n, remote, 'openai')
        try:
            async with n.client.stream('POST', PATH + '/answer/stream', headers=n.headers('alice'), json={'message': '解释', 'model': 'openai'}) as response:
                assert response.status_code == 200
                async with asyncio.timeout(5):
                    async for line in response.aiter_lines():
                        if not line.startswith('data: '): continue
                        item = json.loads(line[6:]); run_id = item['run_id']
                        if item['event']['type'] == 'step_started' and item['event'].get('step') == 2:
                            await asyncio.wait_for(arrived.wait(), 3); break
            async with asyncio.timeout(5):
                while True:
                    async with n.factory() as db:
                        row = await db.get(AgentRun, run_id)
                        if row.status == 'cancelled': break
                    await asyncio.sleep(.02)
        finally: release.set()
    assert row.assistant_message_id is None
    assert [e['type'] for e in row.steps] == ['run_started', 'step_started', 'tool_started', 'tool_completed', 'step_started', 'run_cancelled']
    validate(row.steps)
    async with n.factory() as db: assert await db.scalar(select(func.count()).select_from(Message)) == 0
    record(request, steps=row.steps, status=row.status, provider_calls=len(calls))


@pytest.mark.parametrize('target', ['tool_started', 'tool_completed'])
async def test_event_commit_failure_stops_new_tools_and_model_turns(native, request, monkeypatch, target):
    n = native; calls = []; executed = []; original = agent_runs.DocumentToolBridge.execute
    async def observe(self, *args): executed.append(args[0]); return await original(self, *args)
    monkeypatch.setattr(agent_runs.DocumentToolBridge, 'execute', observe)
    def fail_event(session):
        for row in session.dirty:
            if isinstance(row, AgentRun) and row.steps and row.steps[-1]['type'] == target:
                raise SQLAlchemyError('private injected commit failure MUST NOT LEAK')
    event.listen(Session, 'before_commit', fail_event)
    try:
        async with server(simulation(calls)) as remote:
            await configure(n, remote, 'openai'); response = await post(n)
    finally: event.remove(Session, 'before_commit', fail_event)
    packets = parse(response)
    assert packets[-1]['kind'] == 'error' and packets[-1]['code'] == 'PERSISTENCE_FAILED'
    assert 'private injected' not in response.text and 'run_completed' not in response.text
    assert len(calls) == 1 and len(executed) == (0 if target == 'tool_started' else 1)
    async with n.factory() as db:
        row = (await db.scalars(select(AgentRun))).one()
        assert row.status == 'failed' and row.assistant_message_id is None
        assert await db.scalar(select(func.count()).select_from(Message)) == 0
    record(request, target=target, packets=packets, executed=executed, provider_calls=len(calls), saved=row.steps)


async def test_final_transaction_failure_has_no_fake_completed_event_or_message(native, request):
    n = native; calls = []
    def fail_final(session):
        if any(isinstance(r, Message) for r in session.new) or any(isinstance(r, AgentRun) and r.status == 'completed' for r in session.dirty):
            raise SQLAlchemyError('explicit final commit failure')
    event.listen(Session, 'before_commit', fail_final)
    try:
        async with server(simulation(calls)) as remote:
            await configure(n, remote, 'openai'); response = await post(n)
    finally: event.remove(Session, 'before_commit', fail_final)
    packets = parse(response)
    assert packets[-1]['code'] == 'PERSISTENCE_FAILED'
    assert not any(p['kind'] == 'result' or p.get('event', {}).get('type') == 'run_completed' for p in packets)
    async with n.factory() as db:
        row = (await db.scalars(select(AgentRun))).one()
        assert row.status == 'failed' and all(e['type'] != 'run_completed' for e in row.steps)
        assert await db.scalar(select(func.count()).select_from(Message)) == 0
        assert await db.scalar(select(func.count()).select_from(Conversation)) == 0
    record(request, packets=packets, saved=row.steps, messages=0)


async def test_events_permission_is_current_and_excerpts_never_enter_events(native):
    n = native; calls = []
    async with server(simulation(calls)) as remote:
        await configure(n, remote, 'openai'); packets = parse(await post(n))
    result = packets[-1]['result']; url = PATH + '/runs/' + result['run_id'] + '/events'
    assert (await n.client.get(url, headers=n.headers('bob'))).status_code == 404
    assert (await n.client.get(url)).status_code == 401
    snapshot = (await n.client.get(url, headers=n.headers('alice'))).json()
    assert '消耗氧气' not in json.dumps(snapshot, ensure_ascii=False)
    async with n.factory() as db:
        conv = await db.get(Conversation, result['conversation_id']); conv.user_id = 'bob'; await db.commit()
    assert (await n.client.get(url, headers=n.headers('alice'))).status_code == 404


@pytest.mark.parametrize('status,age,recovery', [('running', 6, 'unconfirmed'), ('queued', 6, 'unconfirmed'), ('running', 1, 'active'), ('completed', 20, 'terminal'), ('failed', 20, 'terminal'), ('cancelled', 20, 'terminal')])
async def test_recovery_is_readonly_and_uses_recent_event_or_creation(native, status, age, recovery):
    n = native; old = utc_now() - timedelta(minutes=20)
    last = datetime.now(timezone.utc) - timedelta(minutes=age)
    async with n.factory() as db:
        db.add(AgentRun(id='observed', user_id='alice', status=status, model='simulated', created_at=old,
                        steps=[{'seq': 1, 'type': 'run_started', 'at': last.isoformat()}], sources=[])); await db.commit()
    run = (await n.client.get(PATH + '/runs/observed', headers=n.headers('alice'))).json()
    snapshot = (await n.client.get(PATH + '/runs/observed/events', headers=n.headers('alice'))).json()
    assert run['recovery_state'] == snapshot['recovery_state'] == recovery
    assert run['created_at'].endswith('+00:00') and run['last_event_at'] == last.isoformat()
    async with n.factory() as db:
        row = await db.get(AgentRun, 'observed'); assert row.status == status and row.completed_at is None


async def test_empty_old_steps_recovery_falls_back_to_created_at_and_negative_after_rejected(native):
    n = native
    async with n.factory() as db:
        db.add(AgentRun(id='old', user_id='alice', status='running', model='simulated', created_at=utc_now()-timedelta(minutes=6), steps=[], sources=[])); await db.commit()
    response = await n.client.get(PATH + '/runs/old', headers=n.headers('alice'))
    assert response.json()['recovery_state'] == 'unconfirmed' and response.json()['last_event_at'] is None
    assert (await n.client.get(PATH + '/runs/old/events?after=-1', headers=n.headers('alice'))).status_code == 422


async def test_models_advertise_stream_without_changing_model_array(native):
    expected = native.unified.get_agent_models('alice')
    body = (await native.client.get(PATH + '/models', headers=native.headers('alice'))).json()
    assert body['streaming_events'] is True and body['models'] == expected


@pytest.mark.parametrize('mode', ['missing-model', 'foreign-conversation', 'invalid-body', 'unauthenticated'])
async def test_stream_preflight_rejects_before_sse_and_before_any_run(native, mode):
    n = native; body = {'message': '解释', 'model': 'openai'}; headers = n.headers('alice')
    if mode == 'foreign-conversation':
        async with n.factory() as db: db.add(Conversation(id='foreign', user_id='bob', role_preset='tutor')); await db.commit()
        body['conversation_id'] = 'foreign'
    if mode == 'invalid-body': body['message'] = ''
    if mode == 'unauthenticated': headers = {}
    response = await n.client.post(PATH + '/answer/stream', headers=headers, json=body)
    assert response.status_code == {'missing-model': 400, 'foreign-conversation': 404, 'invalid-body': 422, 'unauthenticated': 401}[mode]
    assert 'text/event-stream' not in response.headers['content-type']
    async with n.factory() as db: assert await db.scalar(select(func.count()).select_from(AgentRun)) == 0


async def test_runner_sink_timeout_keeps_real_steps_and_does_not_publish_completion():
    bridge = Bridge(); received = []
    async def sink(step): received.append(copy.deepcopy(step))
    async def turn(*args, **kwargs): await asyncio.Event().wait()
    with pytest.raises(AgentExecutionError, match='RUN_TIMEOUT') as error:
        await run_readonly(SimpleNamespace(agent_turn=turn), bridge, '解释', 'simulated', timeout=.03, event_sink=sink)
    assert received == error.value.steps and received[-1]['type'] == 'run_failed'
    validate(received)


async def test_runner_sink_failure_prevents_tool_execution():
    bridge = Bridge()
    async def turn(*args, **kwargs): return ToolResponse('', '{}', 'simulated', [ToolCall('tool', 'search_documents', {})])
    async def sink(step):
        if step['type'] == 'tool_started': raise SQLAlchemyError('explicit injected sink failure')
    with pytest.raises(AgentExecutionError, match='PERSISTENCE_FAILED'):
        await run_readonly(SimpleNamespace(agent_turn=turn), bridge, '解释', 'simulated', event_sink=sink)
    assert bridge.calls == []


async def test_runner_completion_is_returned_but_not_sent_to_sink_before_sql_transaction():
    bridge = Bridge(); received = []
    async def verify(refs): return [{'citation_id': 'S1'}]
    bridge.verified_sources = verify
    async def turn(*args, **kwargs): return ToolResponse('已验证 [S1]', '{}', 'simulated', [])
    async def sink(step): received.append(copy.deepcopy(step))
    result = await run_readonly(SimpleNamespace(agent_turn=turn), bridge, '解释', 'simulated', event_sink=sink)
    assert [e['type'] for e in received] == ['run_started', 'step_started']
    assert result.steps[-1]['type'] == 'run_completed'; validate(result.steps)


async def test_cancel_waits_for_tool_cleanup_before_committing_cancelled(native, monkeypatch, request):
    n = native; calls = []; arrived = asyncio.Event(); cleanup = asyncio.Event(); release = asyncio.Event()
    async def held_tool(self, *args):
        arrived.set()
        try: await asyncio.Event().wait()
        finally: cleanup.set(); await release.wait()
    monkeypatch.setattr(agent_runs.DocumentToolBridge, 'execute', held_tool)
    async with server(simulation(calls)) as remote:
        await configure(n, remote, 'openai')
        try:
            async with n.client.stream('POST', PATH + '/answer/stream', headers=n.headers('alice'), json={'message': '解释', 'model': 'openai'}) as response:
                async for line in response.aiter_lines():
                    if line.startswith('data: '):
                        packet = json.loads(line[6:]); run_id = packet['run_id']
                        if packet['event']['type'] == 'tool_started':
                            await asyncio.wait_for(arrived.wait(), 3); break
            await asyncio.wait_for(cleanup.wait(), 3)
            async with n.factory() as db:
                row = await db.get(AgentRun, run_id)
                assert row.status == 'running' and row.steps[-1]['type'] == 'tool_started'
            release.set()
            async with asyncio.timeout(5):
                while True:
                    async with n.factory() as db:
                        row = await db.get(AgentRun, run_id)
                        if row.status == 'cancelled': break
                    await asyncio.sleep(.02)
        finally: release.set()
    assert len(calls) == 1 and row.steps[-1]['type'] == 'run_cancelled'
    assert all(s['type'] not in ('tool_completed', 'run_completed') for s in row.steps)
    record(request, waited_for_cleanup=True, steps=row.steps, status=row.status)


async def test_disconnect_during_committed_start_waits_then_cancels_without_model(native, monkeypatch, request):
    n = native; calls = []; arrived = asyncio.Event(); release = asyncio.Event(); original = store.start
    async def held_start(*args):
        run_id = await original(*args); arrived.set(); await release.wait(); return run_id
    monkeypatch.setattr(store, 'start', held_start)
    async with server(simulation(calls)) as remote:
        await configure(n, remote, 'openai')
        reader, writer = await asyncio.open_connection(n.client.base_url.host, n.client.base_url.port)
        body = json.dumps({'message': '解释', 'model': 'openai'}).encode()
        writer.write((f'POST {PATH}/answer/stream HTTP/1.1\r\nHost: localhost\r\nAuthorization: {n.headers("alice")["Authorization"]}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n').encode() + body); await writer.drain()
        try:
            await asyncio.wait_for(arrived.wait(), 5); writer.close(); await writer.wait_closed()
            await asyncio.sleep(.05); release.set()
            async with asyncio.timeout(5):
                while True:
                    async with n.factory() as db:
                        row = (await db.scalars(select(AgentRun))).one()
                        if row.status == 'cancelled': break
                    await asyncio.sleep(.02)
        finally: release.set(); writer.close()
    assert calls == [] and row.steps[-1]['type'] == 'run_cancelled'; validate(row.steps)
    record(request, startup_commit_cancelled=True, steps=row.steps, provider_calls=0)


async def test_failed_grounding_publishes_committed_terminal_then_safe_error(native, request):
    n = native; calls = []
    async with server(simulation(calls, fail=True)) as remote:
        await configure(n, remote, 'openai'); response = await post(n)
    packets = parse(response); steps = [p['event'] for p in packets if p['kind'] == 'step']
    validate(steps)
    assert packets[-1]['kind'] == 'error' and packets[-1]['code'] == 'UNVERIFIED_SOURCES'
    assert packets[-1]['steps'] == steps and steps[-1]['type'] == 'run_failed'
    async with n.factory() as db:
        row = (await db.scalars(select(AgentRun))).one()
        assert row.status == 'failed' and row.steps == steps
        assert await db.scalar(select(func.count()).select_from(Message)) == 0
    record(request, packets=packets, saved_terminal=row.status, provider_calls=len(calls))


async def test_stream_missing_table_fails_without_model_and_without_leaking_sql(native):
    n = native; calls = []
    async with n.factory.kw['bind'].begin() as db: await db.run_sync(AgentRun.__table__.drop)
    async with server(simulation(calls)) as remote:
        await configure(n, remote, 'openai'); response = await post(n)
    packets = parse(response)
    assert packets == [{'kind': 'error', 'code': 'PERSISTENCE_FAILED', 'steps': []}]
    assert calls == []


async def test_real_main_mounts_stream_and_recovery_routes_without_formal_database(native, monkeypatch):
    from app.main import app
    from app.database import get_db
    async def disposable_database():
        async with native.factory() as db: yield db
    monkeypatch.setitem(app.dependency_overrides, get_db, disposable_database)
    async with server(app) as main:
        schema = (await main.get('/openapi.json')).json()
        assert PATH + '/answer/stream' in schema['paths'] and PATH + '/runs/{run_id}/events' in schema['paths']
        response = await main.post(PATH + '/answer/stream', json={'message': '解释'})
        assert response.status_code == 401


async def test_start_event_read_failure_still_publishes_contiguous_committed_terminal(native, monkeypatch, request):
    n = native; calls = []; original = store.append_event
    async def fail_start_read(factory, run_id, user_id, item):
        if item['type'] == 'run_started': raise SQLAlchemyError('explicit startup event read failure')
        return await original(factory, run_id, user_id, item)
    monkeypatch.setattr(store, 'append_event', fail_start_read)
    async with server(simulation(calls)) as remote:
        await configure(n, remote, 'openai'); response = await post(n)
    packets = parse(response); steps = [p['event'] for p in packets if p['kind'] == 'step']
    validate(steps)
    assert [p['type'] for p in steps] == ['run_started', 'run_failed']
    assert packets[-1]['code'] == 'PERSISTENCE_FAILED' and calls == []
    async with n.factory() as db:
        row = (await db.scalars(select(AgentRun))).one(); assert row.steps == steps and row.status == 'failed'
    record(request, startup_read_failure=True, packets=packets)
