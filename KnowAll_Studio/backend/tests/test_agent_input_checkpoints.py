"""Round07 input persistence: disposable SQL, private key, real HTTP/JWT; no paid calls."""
import asyncio
import importlib
import json
from pathlib import Path

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api import agent_runs
from app.core import crypto
from app.models import AgentRun, AgentBackgroundTask, Conversation, Message
from tests.test_agent_readonly_http import native, configure, PATH
from tests.test_agent_background_runs import provider, terminal
from tests.test_reading_native_stream_http_flow import server


@pytest.fixture
async def checkpoint(native, tmp_path, monkeypatch):
    monkeypatch.setattr(crypto, '_KEY_FILE', tmp_path / 'private-fernet-key')
    from app.core.agent_runtime.background import BackgroundRunManager
    monkeypatch.setattr(agent_runs,'background_manager',BackgroundRunManager())
    async with native.factory.kw['bind'].begin() as conn:
        await conn.run_sync(AgentBackgroundTask.__table__.create)
        try:
            model = importlib.import_module('app.models.agent_input_checkpoint').AgentInputCheckpoint
        except ModuleNotFoundError:
            model = None  # RED should fail the requested HTTP behavior, not collection.
        if model:
            await conn.run_sync(model.__table__.create)
    native.checkpoint_model = model
    yield native
    await agent_runs.background_manager.close()


async def submit(n, **extra):
    response = await n.client.post(PATH + '/runs', headers=n.headers('alice'),
        json={'message': '解释细胞呼吸', 'model': 'openai', 'persist_input': True, **extra})
    assert response.status_code == 202, response.text
    assert response.json()['input_saved'] is True
    return response.json()['run_id']


async def fetch(n, run_id, user='alice', method='get'):
    return await getattr(n.client, method)(PATH + '/runs/' + run_id + '/input', headers=n.headers(user))


async def test_explicit_input_is_encrypted_and_retrieved_without_execution(checkpoint):
    n = checkpoint; calls = []
    async with n.factory() as db:
        db.add(Conversation(id='original', user_id='alice', role_preset='tutor'))
        db.add(Message(conversation_id='original', role='user', content='过去的问题'))
        await db.commit()
    async with server(provider(calls)) as remote:
        await configure(n, remote, 'openai'); run_id = await submit(n, conversation_id='original')
        row = await terminal(n, run_id)
        before = len(calls)
        response = await fetch(n, run_id)
        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == {'message', 'requested_model', 'run_id', 'created_at', 'version'}
        assert body['message'] == '解释细胞呼吸' and body['requested_model'] == 'openai' and body['version'] == 1
        assert len(calls) == before == 2
    async with n.factory() as db:
        cp = await db.get(n.checkpoint_model, run_id)
        assert '解释细胞呼吸' not in cp.input_ciphertext and '过去的问题' not in cp.input_ciphertext
        payload = json.loads(crypto.get_fernet(create=False).decrypt(cp.input_ciphertext.encode()))
        assert payload['history'] == [{'role': 'user', 'content': '过去的问题'}]
        assert payload['original_conversation_id'] == 'original'
        assert payload['run_id'] == run_id and payload['user_id'] == 'alice' and payload['scope'] == 'readonly_documents'
        assert 'explicit-simulated-no-paid-key' not in json.dumps(payload)
    receipt = (await n.client.get(PATH + '/runs/' + run_id, headers=n.headers('alice'))).json()
    listing = (await n.client.get(PATH + '/runs', headers=n.headers('alice'))).json()
    assert receipt['input_saved'] is True and listing['runs'][0]['input_saved'] is True
    assert '解释细胞呼吸' not in json.dumps(listing, ensure_ascii=False)
    assert row['status'] == 'completed'


@pytest.mark.parametrize('value', [1, 'true', None, []])
async def test_persist_input_is_strict_boolean(checkpoint, value):
    response = await checkpoint.client.post(PATH + '/runs', headers=checkpoint.headers('alice'),
        json={'message': '解释', 'persist_input': value})
    assert response.status_code == 422


async def test_default_off_and_plain_answer_contract_are_unchanged(checkpoint):
    n = checkpoint; calls = []
    async with server(provider(calls)) as remote:
        await configure(n, remote, 'openai')
        response = await n.client.post(PATH + '/runs', headers=n.headers('alice'), json={'message': '解释', 'model': 'openai'})
        assert response.status_code == 202
        run_id = response.json()['run_id']; await terminal(n, run_id)
    assert (await fetch(n, run_id)).status_code == 404
    assert (await n.client.get(PATH + '/runs/' + run_id, headers=n.headers('alice'))).json()['input_saved'] is False
    assert (await n.client.post(PATH + '/answer', headers=n.headers('alice'),
        json={'message': '解释', 'persist_input': True})).status_code == 422
    assert not crypto._KEY_FILE.exists()


@pytest.mark.parametrize('method', ['get', 'delete'])
async def test_other_account_cannot_read_or_delete_input(checkpoint, method):
    n = checkpoint; calls = []
    async with server(provider(calls)) as remote:
        await configure(n, remote, 'openai'); run_id = await submit(n); await terminal(n, run_id)
    assert (await fetch(n, run_id, 'bob', method)).status_code == 404
    assert (await fetch(n, run_id)).status_code == 200


@pytest.mark.parametrize('change', ['transfer', 'delete'])
@pytest.mark.parametrize('method', ['get', 'delete'])
async def test_original_conversation_rechecked_even_if_completed_run_points_elsewhere(checkpoint, change, method):
    n = checkpoint; calls = []
    async with n.factory() as db:
        db.add(Conversation(id='original', user_id='alice', role_preset='tutor')); await db.commit()
    async with server(provider(calls)) as remote:
        await configure(n, remote, 'openai'); run_id = await submit(n, conversation_id='original'); await terminal(n, run_id)
    async with n.factory() as db:
        db.add(Conversation(id='current', user_id='alice', role_preset='tutor')); await db.flush()
        row = await db.get(AgentRun, run_id); row.conversation_id = 'current'
        original = await db.get(Conversation, 'original')
        if change == 'transfer': original.user_id = 'bob'
        else: await db.delete(original)
        await db.commit()
    assert (await fetch(n, run_id, method=method)).status_code == 404


@pytest.mark.parametrize('damage', ['corrupt', 'swapped', 'wrong-binding', 'wrong-version', 'missing-key'])
async def test_unreadable_input_fails_closed_and_missing_key_is_not_created(checkpoint, damage):
    n = checkpoint; calls = []
    async with server(provider(calls)) as remote:
        await configure(n, remote, 'openai'); run_id = await submit(n); await terminal(n, run_id)
        second = await submit(n); await terminal(n, second)
    async with n.factory() as db:
        cp = await db.get(n.checkpoint_model, run_id)
        if damage == 'corrupt': cp.input_ciphertext = 'broken-private-value'
        if damage == 'swapped': cp.input_ciphertext = (await db.get(n.checkpoint_model, second)).input_ciphertext
        if damage in ('wrong-binding', 'wrong-version'):
            key = crypto.get_fernet(create=False)
            payload = json.loads(key.decrypt(cp.input_ciphertext.encode()))
            payload['scope' if damage == 'wrong-binding' else 'version'] = 'write' if damage == 'wrong-binding' else 2
            cp.input_ciphertext = key.encrypt(json.dumps(payload).encode()).decode()
        await db.commit()
    if damage == 'missing-key': crypto._KEY_FILE.unlink()
    response = await fetch(n, run_id)
    assert response.status_code == 503 and 'broken-private' not in response.text
    # Metadata discovery never decrypts input, so it remains observable.
    assert (await n.client.get(PATH + '/runs/' + run_id, headers=n.headers('alice'))).json()['input_saved'] is True
    if damage == 'missing-key': assert not crypto._KEY_FILE.exists()


async def test_checkpoint_commit_failure_is_atomic_and_launches_nothing(checkpoint):
    n = checkpoint; calls = []
    def fail(session):
        if any(type(row).__name__ == 'AgentInputCheckpoint' for row in session.new):
            raise SQLAlchemyError('private checkpoint failure')
    event.listen(Session, 'before_commit', fail)
    try:
        async with server(provider(calls)) as remote:
            await configure(n, remote, 'openai')
            response = await n.client.post(PATH + '/runs', headers=n.headers('alice'),
                json={'message': '解释', 'model': 'openai', 'persist_input': True})
            assert response.status_code == 503 and calls == [] and 'private' not in response.text
        async with n.factory() as db:
            for model in (AgentRun, AgentBackgroundTask, n.checkpoint_model):
                assert await db.scalar(select(func.count()).select_from(model)) == 0
    finally: event.remove(Session, 'before_commit', fail)


async def test_delete_active_input_only_does_not_cancel_or_remove_result(checkpoint):
    n = checkpoint; calls = []; arrived = asyncio.Event(); release = asyncio.Event()
    async with server(provider(calls, arrived=arrived, release=release)) as remote:
        await configure(n, remote, 'openai'); run_id = await submit(n); await asyncio.wait_for(arrived.wait(), 3)
        try:
            response = await fetch(n, run_id, method='delete')
            assert response.status_code == 204, response.text
            assert (await fetch(n, run_id)).status_code == 404
            receipt = (await n.client.get(PATH + '/runs/' + run_id, headers=n.headers('alice'))).json()
            assert receipt['status'] == 'running' and receipt['input_saved'] is False and receipt['cancel_requested'] is False
            assert (await fetch(n, run_id, method='delete')).status_code == 204
        finally: release.set()
        assert (await terminal(n, run_id))['status'] == 'completed'
    assert (await n.client.get(PATH + '/runs/' + run_id + '/result', headers=n.headers('alice'))).status_code == 200


async def test_missing_optional_table_keeps_legacy_runs_readable_and_optin_fails_before_launch(checkpoint):
    n = checkpoint; calls = []
    async with n.factory.kw['bind'].begin() as conn:
        if n.checkpoint_model: await conn.run_sync(n.checkpoint_model.__table__.drop)
    async with server(provider(calls)) as remote:
        await configure(n, remote, 'openai')
        response = await n.client.post(PATH + '/runs', headers=n.headers('alice'), json={'message': '解释', 'model': 'openai'})
        assert response.status_code == 202
        run_id = response.json()['run_id']; await terminal(n, run_id)
        receipt = await n.client.get(PATH + '/runs/' + run_id, headers=n.headers('alice'))
        assert receipt.status_code == 200 and receipt.json()['input_saved'] is False
        assert (await fetch(n, run_id)).status_code == 404
        before = len(calls)
        response = await n.client.post(PATH + '/runs', headers=n.headers('alice'),
            json={'message': '解释', 'model': 'openai', 'persist_input': True})
        assert response.status_code == 503 and len(calls) == before


@pytest.mark.parametrize('endpoint', ['/models', '/capabilities?model=unknown'])
@pytest.mark.parametrize('missing', ['control', 'input', 'none', 'closed'])
async def test_http_capabilities_report_actual_schema_without_provider_or_key(checkpoint, endpoint, missing):
    n=checkpoint
    if missing in ('control','input'):
        model=AgentBackgroundTask if missing=='control' else n.checkpoint_model
        async with n.factory.kw['bind'].begin() as conn:await conn.run_sync(model.__table__.drop)
    if missing=='closed':await agent_runs.background_manager.close()
    response=await n.client.get(PATH+endpoint,headers=n.headers('alice'))
    assert response.status_code==200,response.text
    body=response.json()
    assert body['durable_runs'] is True and body['streaming_events'] is True
    assert body['background_runs'] is (missing not in ('control','closed'))
    assert body['input_checkpoints'] is (missing not in ('control','input'))
    assert body['execution_resume'] is False and body['streaming_tools'] is False
    if endpoint.startswith('/capabilities'):assert body['available'] is False
    assert not crypto._KEY_FILE.exists()


@pytest.mark.parametrize('endpoint', ['/models', '/capabilities'])
async def test_http_capabilities_sql_outage_is_503_not_false_success(checkpoint, monkeypatch, endpoint):
    async def failed(*args,**kwargs):raise SQLAlchemyError('private SQL capabilities outage')
    monkeypatch.setattr(agent_runs,'runtime_capabilities',failed)
    response=await checkpoint.client.get(PATH+endpoint,headers=checkpoint.headers('alice'))
    assert response.status_code==503 and 'private' not in response.text


@pytest.mark.parametrize('endpoint', ['list', 'get', 'input', 'delete'])
async def test_input_sql_outage_returns_503(checkpoint, endpoint):
    n=checkpoint;calls=[]
    async with server(provider(calls)) as remote:
        await configure(n,remote,'openai');run_id=await submit(n);await terminal(n,run_id)
    def failed(conn,cursor,statement,parameters,context,executemany):
        if 'agent_input_checkpoints' in statement:raise SQLAlchemyError('private SQL input outage')
    engine=n.factory.kw['bind'].sync_engine
    event.listen(engine,'before_cursor_execute',failed)
    try:
        if endpoint in ('input','delete'):response=await fetch(n,run_id,method='delete' if endpoint=='delete' else 'get')
        else:response=await n.client.get(PATH+'/runs'+('/'+run_id if endpoint=='get' else ''),headers=n.headers('alice'))
        assert response.status_code==503 and 'private' not in response.text
    finally:event.remove(engine,'before_cursor_execute',failed)


async def test_delete_commit_failure_preserves_input(checkpoint):
    n=checkpoint;calls=[]
    async with server(provider(calls)) as remote:
        await configure(n,remote,'openai');run_id=await submit(n);await terminal(n,run_id)
    def failed(session):
        if any(isinstance(row,n.checkpoint_model) for row in session.deleted):raise SQLAlchemyError('private delete failure')
    event.listen(Session,'before_commit',failed)
    try:assert (await fetch(n,run_id,method='delete')).status_code==503
    finally:event.remove(Session,'before_commit',failed)
    assert (await fetch(n,run_id)).status_code==200


@pytest.mark.parametrize('change', ['owner', 'scope', 'current-conversation'])
async def test_current_run_binding_rechecked(checkpoint, change):
    n=checkpoint;calls=[]
    async with server(provider(calls)) as remote:
        await configure(n,remote,'openai');run_id=await submit(n);await terminal(n,run_id)
    async with n.factory() as db:
        row=await db.get(AgentRun,run_id)
        if change=='owner':row.user_id='bob'
        elif change=='scope':row.scope='write_documents'
        else:
            conv=await db.get(Conversation,row.conversation_id);conv.user_id='bob'
        await db.commit()
    assert (await fetch(n,run_id)).status_code==(503 if change=='scope' else 404)


def test_history_snapshot_is_bounded_text_only_and_payload_validation_is_strict(tmp_path,monkeypatch):
    from app.core.agent_runtime.input_checkpoint import create_checkpoint,InputPayload
    from pydantic import ValidationError
    monkeypatch.setattr(crypto,'_KEY_FILE',tmp_path/'private-key')
    history=[{'role':'user','content':'x'*10000,'api_key':'credential-never-serialized'} for _ in range(50)]
    history.extend([{'role':'tool','content':'private tool output'},{'role':'system','content':'private system output'}])
    cp=create_checkpoint('r','u',None,'m','question',history)
    payload=json.loads(crypto.get_fernet(create=False).decrypt(cp.input_ciphertext.encode()))
    assert len(payload['history'])<=20 and sum(len(item['content']) for item in payload['history'])<=32000
    assert all(len(item['content'])<=8000 and set(item)=={'role','content'} for item in payload['history'])
    assert 'credential-never' not in json.dumps(payload) and 'private tool' not in json.dumps(payload)
    payload['api_key']='bad'
    with pytest.raises(ValidationError):InputPayload.model_validate(payload)

