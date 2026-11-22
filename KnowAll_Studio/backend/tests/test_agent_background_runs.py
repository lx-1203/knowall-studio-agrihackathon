"""Background runs: real TCP/JWT/SQLite/native provider; no paid inference."""
import asyncio
import hashlib
import importlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import event, func, select, create_engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from alembic import command
from alembic.config import Config

from app.api import agent_runs
from app.core.agent_runtime import store
from app.models import AgentRun, Conversation, Message
from app.models.agent_run import utc_now
from tests.test_agent_readonly_http import native, configure, provider_reply, PATH
from tests.test_reading_native_stream_http_flow import server

OUT = Path(__file__).resolve().parents[2] / 'docs/open-source-integration/native-agent-background-2026-10-11/backend'


@pytest.fixture
async def background(native):
    native.debug_errors=[];native.sql_trace=[]
    engine=native.factory.kw['bind'].sync_engine
    def transaction(kind):
        def log(connection):native.sql_trace.append([kind,id(connection),datetime.now(timezone.utc).isoformat()])
        return log
    hooks={kind:transaction(kind) for kind in ('begin','commit','rollback')}
    for kind,hook in hooks.items():event.listen(engine,kind,hook)
    @native.app.exception_handler(HTTPException)
    async def diagnose(req,error):
        if error.__cause__ is not None:
            native.debug_errors.append(str(error.__cause__))
            native.debug_errors.append(json.dumps(native.sql_trace[-30:]))
            OUT.mkdir(parents=True,exist_ok=True)
            (OUT/('sql-error-'+datetime.now(timezone.utc).strftime('%H%M%S%f')+'.json')).write_text(
                json.dumps({'error':str(error.__cause__),'trace':native.sql_trace},indent=2),encoding='utf8')
        return JSONResponse({'detail':error.detail},status_code=error.status_code)
    try:
        module = importlib.import_module('app.models.agent_background_task')
        async with native.factory.kw['bind'].begin() as db:
            await db.run_sync(module.AgentBackgroundTask.__table__.create)
    except ModuleNotFoundError:
        pass  # RED exercises the missing HTTP contract, not an import error.
    yield native
    manager = getattr(agent_runs, 'background_manager', None)
    if manager is not None:
        await manager.close()
    for kind,hook in hooks.items():event.remove(engine,kind,hook)


def record(request, **data):
    OUT.mkdir(parents=True, exist_ok=True)
    data.update(test=request.node.nodeid, at=datetime.now(timezone.utc).isoformat(),
                auth='real JWT', database='disposable SQLite',
                provider='explicit localhost/native HTTP adapter', paid_calls=0)
    (OUT / (hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:16] + '.json')).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding='utf8')


def provider(calls, *, arrived=None, release=None, fail=False, protocol='openai'):
    app = FastAPI()
    @app.post('/v1/chat/completions')
    @app.post('/v1/messages')
    async def model(req: Request):
        calls.append(await req.json())
        if len(calls) == 1:
            if arrived:
                arrived.set(); await release.wait()
            return provider_reply(protocol, name='read_document_chunk', args={'chunk_id': 'a-chunk'})
        return provider_reply(protocol, text='失实 [S99]' if fail else '消耗氧气 [S1]')
    return app


async def submit(n, **extra):
    response = await n.client.post(PATH + '/runs', headers=n.headers('alice'),
                                   json={'message': '解释细胞呼吸', 'model': 'openai', **extra})
    assert response.status_code == 202, response.text
    assert response.json()['execution_mode'] == 'background'
    return response.json()['run_id']


async def terminal(n, run_id):
    async with asyncio.timeout(6):
        while True:
            response = await n.client.get(PATH + '/runs/' + run_id, headers=n.headers('alice'))
            assert response.status_code == 200, response.text
            row = response.json()
            if row['status'] in ('completed', 'failed', 'cancelled'):
                return row
            await asyncio.sleep(.02)


@pytest.mark.parametrize('protocol', ['openai', 'anthropic'])
async def test_accepted_run_survives_observation_connection_close_and_result_is_actual_message(background, request, protocol):
    n = background; calls = []; arrived = asyncio.Event(); release = asyncio.Event()
    async with server(provider(calls, arrived=arrived, release=release, protocol=protocol)) as remote:
        await configure(n, remote, protocol)
        response = await n.client.post(PATH + '/runs', headers=n.headers('alice'), json={'message': '解释细胞呼吸', 'model': protocol})
        assert response.status_code == 202, response.text
        run_id = response.json()['run_id']; await asyncio.wait_for(arrived.wait(), 3)
        async with n.factory() as db:
            control = await db.get(importlib.import_module('app.models.agent_background_task').AgentBackgroundTask, run_id)
            row = await db.get(AgentRun, run_id)
            assert row.status == 'running' and control.user_id == 'alice' and not control.cancel_requested
        early = await n.client.get(PATH + '/runs/' + run_id + '/result', headers=n.headers('alice'))
        assert early.status_code == 409
        reader, writer = await asyncio.open_connection(n.client.base_url.host, n.client.base_url.port)
        writer.write((f'GET {PATH}/runs/{run_id}/events HTTP/1.1\r\nHost: localhost\r\nAuthorization: {n.headers("alice")["Authorization"]}\r\n\r\n').encode())
        await writer.drain(); assert b'200 OK' in await reader.read(4096)
        writer.close(); await writer.wait_closed()
        release.set(); row = await terminal(n, run_id)
    assert row['status'] == 'completed' and row['execution_mode'] == 'background' and row['cancel_requested'] is False
    result = await n.client.get(PATH + '/runs/' + run_id + '/result', headers=n.headers('alice'))
    assert result.status_code == 200, result.text
    body = result.json()
    async with n.factory() as db:
        message = await db.get(Message, row['assistant_message_id'])
        assert message.content == body['message'] == '消耗氧气 [S1]'
        assert message.conversation_id == body['conversation_id'] and message.role == 'assistant'
        assert await db.scalar(select(func.count()).select_from(Message)) == 2
    assert body['run_id'] == run_id and body['steps'] == row['steps'] and body['sources'][0]['text'] == ''
    assert body['usage'] == {'input_tokens': 14, 'output_tokens': 22} and len(calls) == 2
    record(request, receipt=row, result=body, observation_socket_closed=True, provider_calls=len(calls))


@pytest.mark.parametrize('other_manager', [False, True])
async def test_stop_is_committed_before_cancel_and_waits_for_tool_finally(background, monkeypatch, request, other_manager):
    n = background; calls = []; arrived = asyncio.Event(); cleanup = asyncio.Event(); release = asyncio.Event()
    async def held_tool(self, *args):
        arrived.set()
        try: await asyncio.Event().wait()
        finally:
            async with n.factory() as db:
                control = (await db.scalars(select(importlib.import_module('app.models.agent_background_task').AgentBackgroundTask))).one()
                assert control.cancel_requested is True
            cleanup.set(); await release.wait()
    monkeypatch.setattr(agent_runs.DocumentToolBridge, 'execute', held_tool)
    async with server(provider(calls)) as remote:
        await configure(n, remote, 'openai'); run_id = await submit(n)
        await asyncio.wait_for(arrived.wait(), 3)
        original = agent_runs.background_manager
        if other_manager:
            monkeypatch.setattr(agent_runs, 'background_manager', importlib.import_module('app.core.agent_runtime.background').BackgroundRunManager())
        try:
            stopped = await n.client.post(PATH + '/runs/' + run_id + '/cancel', headers=n.headers('alice'))
            assert stopped.status_code == 202, stopped.text
            assert stopped.json()['cancel_requested'] is True and stopped.json()['status'] == 'running'
            await asyncio.wait_for(cleanup.wait(), 3)
            repeated = await n.client.post(PATH + '/runs/' + run_id + '/cancel', headers=n.headers('alice'))
            assert repeated.status_code == 202
            async with n.factory() as db:
                row = await db.get(AgentRun, run_id)
                assert row.status == 'running' and row.steps[-1]['type'] == 'tool_started'
            release.set(); row = await terminal(n, run_id)
            assert row['status'] == 'cancelled' and row['cancel_requested'] is True
            assert row['steps'][-1]['type'] == 'run_cancelled' and len(calls) == 1
            async with n.factory() as db: assert await db.scalar(select(func.count()).select_from(Message)) == 0
            record(request, receipt=row, other_manager=other_manager, cleanup_before_terminal=True, provider_calls=len(calls))
        finally:
            release.set(); await original.close()


async def test_stop_commit_failure_does_not_cancel_running_executor(background, request):
    n = background; calls = []; arrived = asyncio.Event(); release = asyncio.Event()
    async with server(provider(calls, arrived=arrived, release=release)) as remote:
        await configure(n, remote, 'openai'); run_id = await submit(n); await asyncio.wait_for(arrived.wait(), 3)
        def fail_stop(session):
            if any(type(row).__name__ == 'AgentBackgroundTask' and row.cancel_requested for row in session.dirty):
                raise SQLAlchemyError('private stop commit failure')
        event.listen(Session, 'before_commit', fail_stop)
        try:
            response = await n.client.post(PATH + '/runs/' + run_id + '/cancel', headers=n.headers('alice'))
            assert response.status_code == 503 and 'private' not in response.text
            row = (await n.client.get(PATH + '/runs/' + run_id, headers=n.headers('alice'))).json()
            assert row['status'] == 'running' and row['cancel_requested'] is False
        finally: event.remove(Session, 'before_commit', fail_stop); release.set()
        row = await terminal(n, run_id)
    assert row['status'] == 'completed' and len(calls) == 2
    record(request, stop_status=response.status_code, receipt=row)


@pytest.mark.parametrize('mode', ['transfer', 'delete'])
async def test_conversation_changed_while_model_waits_never_receives_answer(background, mode):
    n = background; calls = []; arrived = asyncio.Event(); release = asyncio.Event()
    async with n.factory() as db: db.add(Conversation(id='owned', user_id='alice', role_preset='tutor')); await db.commit()
    async with server(provider(calls, arrived=arrived, release=release)) as remote:
        await configure(n, remote, 'openai'); run_id = await submit(n, conversation_id='owned'); await asyncio.wait_for(arrived.wait(), 3)
        async with n.factory() as db:
            conv = await db.get(Conversation, 'owned')
            if mode == 'transfer': conv.user_id = 'bob'
            else: await db.delete(conv)
            await db.commit()
        release.set()
        async with asyncio.timeout(5):
            while True:
                async with n.factory() as db: row = await db.get(AgentRun, run_id)
                if row.status == 'failed': break
                await asyncio.sleep(.02)
    assert row.error_code == 'CONVERSATION_UNAVAILABLE'
    async with n.factory() as db: assert await db.scalar(select(func.count()).select_from(Message)) == 0
    for suffix in ('', '/result', '/events'):
        assert (await n.client.get(PATH + '/runs/' + run_id + suffix, headers=n.headers('alice'))).status_code == 404
    assert (await n.client.post(PATH + '/runs/' + run_id + '/cancel', headers=n.headers('alice'))).status_code == 404
    assert (await n.client.get(PATH + '/runs', headers=n.headers('alice'))).json() == {'runs': [], 'has_more': False}


@pytest.mark.parametrize('mode', ['missing-model', 'foreign-conversation', 'invalid-body', 'unauthenticated'])
async def test_background_preflight_has_no_run_or_control_or_provider(background, mode):
    n = background; body = {'message': '解释', 'model': 'openai'}; headers = n.headers('alice')
    if mode == 'foreign-conversation':
        async with n.factory() as db: db.add(Conversation(id='foreign', user_id='bob', role_preset='tutor')); await db.commit()
        body['conversation_id'] = 'foreign'
    if mode == 'invalid-body': body['message'] = ''
    if mode == 'unauthenticated': headers = {}
    response = await n.client.post(PATH + '/runs', headers=headers, json=body)
    assert response.status_code == {'missing-model': 400, 'foreign-conversation': 404, 'invalid-body': 422, 'unauthenticated': 401}[mode]
    async with n.factory() as db: assert await db.scalar(select(func.count()).select_from(AgentRun)) == 0


@pytest.mark.parametrize('failure', ['start', 'control', 'final', 'event'])
async def test_background_sql_failure_is_atomic_and_blocks_uncommitted_work(background, failure):
    n = background; calls = []
    def fail_commit(session):
        if failure == 'start' and any(isinstance(row, AgentRun) for row in session.new): raise SQLAlchemyError('private start fail')
        if failure == 'control' and any(type(row).__name__ == 'AgentBackgroundTask' for row in session.new): raise SQLAlchemyError('private control fail')
        if failure == 'final' and (any(isinstance(row, Message) for row in session.new) or any(isinstance(row,AgentRun) and row.status=='completed' for row in session.dirty)): raise SQLAlchemyError('private final fail')
        if failure == 'event' and any(isinstance(row, AgentRun) and row.steps[-1]['type'] == 'tool_started' for row in session.dirty): raise SQLAlchemyError('private event fail')
    event.listen(Session, 'before_commit', fail_commit)
    try:
        async with server(provider(calls)) as remote:
            await configure(n, remote, 'openai')
            response = await n.client.post(PATH + '/runs', headers=n.headers('alice'), json={'message': '解释', 'model': 'openai'})
            if failure in ('start', 'control'):
                assert response.status_code == 503 and calls == []
                async with n.factory() as db: assert await db.scalar(select(func.count()).select_from(AgentRun)) == 0
            else:
                assert response.status_code == 202, response.text
                row = await terminal(n, response.json()['run_id'])
                assert row['status'] == 'failed' and row['error_code'] == 'PERSISTENCE_FAILED'
                assert len(calls) == (1 if failure == 'event' else 2)
            assert 'private' not in response.text
            async with n.factory() as db: assert await db.scalar(select(func.count()).select_from(Message)) == 0
    finally: event.remove(Session, 'before_commit', fail_commit)


async def test_failed_background_has_result_conflict_and_repeat_stop_keeps_terminal(background):
    n = background; calls = []
    async with server(provider(calls, fail=True)) as remote:
        await configure(n, remote, 'openai'); run_id = await submit(n); row = await terminal(n, run_id)
    assert row['status'] == 'failed' and row['error_code'] == 'UNVERIFIED_SOURCES'
    assert (await n.client.get(PATH + '/runs/' + run_id + '/result', headers=n.headers('alice'))).status_code == 409
    response = await n.client.post(PATH + '/runs/' + run_id + '/cancel', headers=n.headers('alice'))
    assert response.status_code == 202 and response.json()['status'] == 'failed', (response.text,n.debug_errors)
    assert (await n.client.get(PATH + '/runs/' + run_id, headers=n.headers('alice'))).json()['steps'] == row['steps']


async def test_success_repeat_stop_preserves_message_and_result_rechecks_owner(background):
    n = background; calls = []
    async with server(provider(calls)) as remote:
        await configure(n, remote, 'openai'); run_id = await submit(n); row = await terminal(n, run_id)
    for _ in range(2):
        response = await n.client.post(PATH + '/runs/' + run_id + '/cancel', headers=n.headers('alice'))
        assert response.status_code == 202 and response.json()['status'] == 'completed', (response.text,n.debug_errors)
    for suffix, verb in [('', 'get'), ('/result', 'get'), ('/cancel', 'post')]:
        assert (await getattr(n.client, verb)(PATH + '/runs/' + run_id + suffix, headers=n.headers('bob'))).status_code == 404
    assert (await n.client.get(PATH + '/runs/' + run_id + '/result', headers=n.headers('alice'))).status_code == 200
    async with n.factory() as db:
        conv = await db.get(Conversation, row['conversation_id']); conv.user_id = 'bob'; await db.commit()
    assert (await n.client.get(PATH + '/runs/' + run_id + '/result', headers=n.headers('alice'))).status_code == 404


async def test_list_paginates_owned_runs_hides_deleted_or_transferred_conversations_and_old_rows(background):
    n = background; base = utc_now()
    async with n.factory() as db:
        db.add_all([Conversation(id='mine', user_id='alice', role_preset='tutor'), Conversation(id='foreign', user_id='bob', role_preset='tutor')])
        for i in range(5): db.add(AgentRun(id=f'old-{i}', user_id='alice', conversation_id='mine' if i % 2 else None, status='failed', model='legacy', steps=[], sources=[{'text': 'private excerpt'}], created_at=base + timedelta(seconds=i)))
        for id_, owner, conv in [('other', 'bob', None), ('deleted', 'alice', 'missing'), ('transferred', 'alice', 'foreign')]:
            db.add(AgentRun(id=id_, user_id=owner, conversation_id=conv, status='running', model='legacy', steps=[], sources=[], created_at=base + timedelta(seconds=20)))
        await db.commit()
    first = await n.client.get(PATH + '/runs?limit=2', headers=n.headers('alice'))
    assert first.status_code == 200, first.text
    body = first.json(); assert body['has_more'] is True and [r['run_id'] for r in body['runs']] == ['old-4', 'old-3']
    assert all(r['execution_mode'] == 'request' and r['cancel_requested'] is False and r['sources'][0]['text'] == '' for r in body['runs'])
    last = (await n.client.get(PATH + '/runs?limit=2&offset=4', headers=n.headers('alice'))).json()
    assert last['has_more'] is False and [r['run_id'] for r in last['runs']] == ['old-0']
    assert (await n.client.get(PATH + '/runs?offset=99', headers=n.headers('alice'))).json() == {'runs': [], 'has_more': False}
    assert (await n.client.post(PATH + '/runs/old-4/cancel', headers=n.headers('alice'))).status_code == 409
    assert (await n.client.get(PATH + '/runs/old-4', headers=n.headers('alice'))).json()['execution_mode'] == 'request'


@pytest.mark.parametrize('query', ['limit=0', 'limit=51', 'offset=-1', 'limit=x', 'offset=x'])
async def test_list_query_validation(background, query):
    assert (await background.client.get(PATH + '/runs?' + query, headers=background.headers('alice'))).status_code == 422


async def test_models_background_flag_does_not_change_original_array(background):
    n = background; expected = n.unified.get_agent_models('alice')
    response = await n.client.get(PATH + '/models', headers=n.headers('alice'))
    assert response.json() == {'models': expected, 'streaming_events': True, 'background_runs': True}


@pytest.mark.parametrize('target', ['tool_started', 'tool_completed'])
async def test_persisted_stop_at_action_boundary_prevents_next_tool_or_model_without_watcher(background, monkeypatch, target):
    n=background;calls=[];executed=[];original=store.append_event
    module=importlib.import_module('app.core.agent_runtime.background')
    monkeypatch.setattr(agent_runs,'background_manager',module.BackgroundRunManager(poll_seconds=60))
    real_execute=agent_runs.DocumentToolBridge.execute
    async def observe(self,*args):
        executed.append(args[0]);return await real_execute(self,*args)
    monkeypatch.setattr(agent_runs.DocumentToolBridge,'execute',observe)
    async def stop_after_event(factory,run_id,user_id,item):
        saved=await original(factory,run_id,user_id,item)
        if item['type']==target:
            async with factory() as db:
                control=await store.background_control(db,run_id,user_id)
                control.cancel_requested=True;await db.commit()
        return saved
    monkeypatch.setattr(store,'append_event',stop_after_event)
    async with server(provider(calls)) as remote:
        await configure(n,remote,'openai');run_id=await submit(n);row=await terminal(n,run_id)
    assert row['status']=='cancelled' and row['cancel_requested'] is True and len(calls)==1
    assert len(executed)==(0 if target=='tool_started' else 1)


async def test_registration_failure_is_not_accepted_and_preserves_failed_receipt(background,monkeypatch):
    n=background;calls=[]
    def fail_launch(*args):raise RuntimeError('explicit local launch failure')
    monkeypatch.setattr(agent_runs.background_manager,'launch',fail_launch)
    async with server(provider(calls)) as remote:
        await configure(n,remote,'openai')
        response=await n.client.post(PATH+'/runs',headers=n.headers('alice'),json={'message':'解释','model':'openai'})
    assert response.status_code==503 and calls==[]
    async with n.factory() as db:
        row=(await db.scalars(select(AgentRun))).one()
        assert row.status=='failed' and row.error_code=='PERSISTENCE_FAILED'
        assert await store.background_control(db,row.id,'alice') is not None


async def test_background_close_awaits_cleanup_and_no_completion_is_fabricated(background,monkeypatch):
    n=background;calls=[];arrived=asyncio.Event();cleaned=asyncio.Event()
    async def hold(self,*args):
        arrived.set()
        try:await asyncio.Event().wait()
        finally:await asyncio.sleep(.03);cleaned.set()
    monkeypatch.setattr(agent_runs.DocumentToolBridge,'execute',hold)
    async with server(provider(calls)) as remote:
        await configure(n,remote,'openai');run_id=await submit(n);await asyncio.wait_for(arrived.wait(),3)
        await agent_runs.background_manager.close()
        row=await terminal(n,run_id)
    assert cleaned.is_set() and row['status']=='cancelled' and row['cancel_requested'] is False and len(calls)==1


async def test_result_does_not_return_unrelated_assistant_message(background):
    n=background
    async with n.factory() as db:
        db.add_all([Conversation(id='mine',user_id='alice',role_preset='tutor'),Conversation(id='other',user_id='bob',role_preset='tutor')])
        await db.flush()
        db.add(Message(id='foreign-answer',conversation_id='other',role='assistant',content='private answer'))
        db.add(AgentRun(id='bad-link',user_id='alice',conversation_id='mine',assistant_message_id='foreign-answer',status='completed',model='legacy',steps=[],sources=[]))
        await db.commit()
    response=await n.client.get(PATH+'/runs/bad-link/result',headers=n.headers('alice'))
    assert response.status_code==409 and 'private answer' not in response.text


async def test_persistent_stop_without_executor_keeps_running_without_replay(background):
    n=background
    Control=importlib.import_module('app.models.agent_background_task').AgentBackgroundTask
    async with n.factory() as db:
        db.add(AgentRun(id='orphan',user_id='alice',status='running',model='unconfigured',steps=[],sources=[],created_at=utc_now()-timedelta(minutes=6)))
        db.add(Control(run_id='orphan',user_id='alice',cancel_requested=False));await db.commit()
    response=await n.client.post(PATH+'/runs/orphan/cancel',headers=n.headers('alice'))
    assert response.status_code==202 and response.json()=={'run_id':'orphan','status':'running','cancel_requested':True}
    observed=(await n.client.get(PATH+'/runs/orphan',headers=n.headers('alice'))).json()
    assert observed['status']=='running' and observed['recovery_state']=='unconfirmed' and observed['cancel_requested'] is True
    async with n.factory() as db: assert await db.scalar(select(func.count()).select_from(Message))==0
    assert (await n.client.get(PATH+'/runs')).status_code==401


async def test_supervisor_does_not_interrupt_an_inflight_stop_database_read_on_normal_completion(background,monkeypatch):
    n=background;calls=[];reading=asyncio.Event();release=asyncio.Event();interrupted=asyncio.Event()
    original=store.check_background_stop
    async def held_read(*args):
        if asyncio.current_task().get_coro().__qualname__.endswith('.watch'):
            reading.set()
            try:await release.wait()
            except asyncio.CancelledError:interrupted.set();raise
        return await original(*args)
    monkeypatch.setattr(store,'check_background_stop',held_read)
    try:
        async with server(provider(calls)) as remote:
            await configure(n,remote,'openai');run_id=await submit(n)
            await asyncio.wait_for(reading.wait(),3);row=await terminal(n,run_id)
            await asyncio.sleep(.03)
            assert row['status']=='completed' and not interrupted.is_set()
    finally:release.set()


def test_background_migration_preserves_old_run_and_downgrade_only_removes_control(tmp_path, request):
    engine = create_engine('sqlite:///' + (tmp_path / 'old.sqlite').as_posix())
    with engine.begin() as db:
        cfg = Config('alembic.ini'); cfg.attributes['connection'] = db
        command.stamp(cfg, '202610102130'); command.upgrade(cfg, '202610112145')
        db.execute(text("INSERT INTO agent_runs (id,user_id,status,scope,model,sources,steps,created_at) VALUES ('legacy','alice','failed','readonly_documents','legacy','[]','[]','2026-10-11 00:00:00')"))
        command.upgrade(cfg, 'head')
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == '202610112250'
        columns = {c['name'] for c in inspect(db).get_columns('agent_background_tasks')}
        assert columns == {'run_id', 'user_id', 'cancel_requested', 'created_at'}
        assert db.execute(text('SELECT model FROM agent_runs WHERE id=\'legacy\'')).scalar_one() == 'legacy'
        command.upgrade(cfg, 'head'); command.downgrade(cfg, '202610112145')
        assert 'agent_background_tasks' not in inspect(db).get_table_names()
        assert db.execute(text('SELECT id FROM agent_runs')).scalar_one() == 'legacy'
        record(request, upgraded_head='202610112250', downgraded_head='202610112145', preserved_old_run=True)
    engine.dispose()
