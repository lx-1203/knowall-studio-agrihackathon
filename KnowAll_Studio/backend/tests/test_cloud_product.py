"""Cloud workspace isolation without live providers."""
import asyncio
from types import SimpleNamespace
import pytest
from sqlalchemy import create_engine, inspect, text
from app.core.cloud_workspaces import workspace_directory, worker_environment, initialize_schema, WorkspacePool


def test_workspace_paths_cannot_escape_root(tmp_path):
    one = workspace_directory(tmp_path, '../sensitive')
    two = workspace_directory(tmp_path, 'second')
    assert one.parent == tmp_path.resolve() and one != two
    assert workspace_directory(tmp_path, '../sensitive') == one


def test_worker_environment_never_inherits_developer_provider_keys(tmp_path):
    env = worker_environment(tmp_path, 'u1', 'secret', {'OPENAI_API_KEY': 'private', 'ANTHROPIC_AUTH_TOKEN': 'private', 'PATH': 'keep'})
    assert env['PATH'] == 'keep'
    assert 'OPENAI_API_KEY' not in env and 'ANTHROPIC_AUTH_TOKEN' not in env
    assert env['LOCAL_AUTO_LOGIN'] == 'false' and env['DEBUG'] == 'false'
    assert env['KNOWALL_WORKSPACE_OWNER'] == 'u1'
    assert tmp_path.as_posix() in env['DATABASE_URL']


def test_fresh_schema_initialization_and_repeat_preserve_user_data(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'db.sqlite'))
    with engine.begin() as connection:
        initialize_schema(connection)
        connection.execute(text("INSERT INTO documents (id, filename, file_type, sha256) VALUES ('keep', 'book.md', 'md', 'abc')"))
    with engine.begin() as connection:
        initialize_schema(connection)
        assert connection.scalar(text('SELECT id FROM documents')) == 'keep'
        assert 'study_packs' in inspect(connection).get_table_names()
    engine.dispose()


async def test_concurrent_requests_reuse_one_worker_and_users_are_isolated(tmp_path):
    launched = []
    async def launch(user):
        await asyncio.sleep(.01)
        launched.append(user.id)
        return SimpleNamespace(origin='http://127.0.0.1:' + str(8100 + len(launched)), process=SimpleNamespace(poll=lambda: None, terminate=lambda: None))
    pool = WorkspacePool(tmp_path, 'secret', launcher=launch)
    u1 = SimpleNamespace(id='u1')
    a, b = await asyncio.gather(pool.get(u1), pool.get(u1))
    c = await pool.get(SimpleNamespace(id='u2'))
    assert a is b and a is not c
    assert launched == ['u1', 'u2']
    await pool.close()


def test_unversioned_database_failure_preserves_rows(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'legacy.sqlite'))
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE keep (id INTEGER PRIMARY KEY)'))
        connection.execute(text('INSERT INTO keep VALUES (7)'))
        with pytest.raises(RuntimeError, match='Unversioned'):
            initialize_schema(connection)
        assert connection.scalar(text('SELECT id FROM keep')) == 7
    engine.dispose()


async def test_worker_capacity_does_not_terminate_active_owner(tmp_path):
    async def launch(user):
        return SimpleNamespace(origin='http://127.0.0.1:8100', process=SimpleNamespace(poll=lambda: None, terminate=lambda: None))
    from fastapi import HTTPException
    pool = WorkspacePool(tmp_path, 'secret', launcher=launch, max_workers=1)
    async with pool.lease(SimpleNamespace(id='u1')):
        with pytest.raises(HTTPException) as exc:
            await pool.get(SimpleNamespace(id='u2'))
        assert exc.value.status_code == 503
    await pool.close()


@pytest.mark.parametrize('kind', ['http', 'websocket'])
async def test_private_worker_rejects_other_owner_before_any_route(kind):
    from app.core.workspace_guard import WorkspaceOwnerGuard
    invoked = []
    async def downstream(scope, receive, send):
        invoked.append(scope['path'])
    guard = WorkspaceOwnerGuard(downstream, 'u1', lambda token: token)
    messages = []
    async def send(message):
        messages.append(message)
    async def receive():
        return {'type': 'http.disconnect'}
    await guard({'type': kind, 'path': '/api/v1/documents', 'headers': [(b'authorization', b'Bearer u2')], 'query_string': b''}, receive, send)
    assert not invoked
    assert messages[0].get('status', messages[0].get('code')) in {401, 4401}
    await guard({'type': kind, 'path': '/api/v1/documents', 'headers': [(b'authorization', b'Bearer u1')], 'query_string': b''}, receive, send)
    assert invoked == ['/api/v1/documents']


async def test_saved_provider_key_is_loaded_without_live_api(tmp_path, monkeypatch):
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.models import APIKey
    from app.core import crypto, auth
    from app.core.api_scheduler import api_client
    from unittest.mock import Mock
    monkeypatch.setattr(crypto, '_KEY_FILE', tmp_path / 'encryption-key')
    configured = Mock()
    monkeypatch.setattr(api_client, 'configure_adapter', configured)
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(__import__('app.database', fromlist=['Base']).Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        db.add(APIKey(provider='openai', user_id='u1', key_alias='mine', key_encrypted=crypto.encrypt_api_key('test-key-only'), is_active=True))
        await db.commit()
        await auth.load_user_api_keys('u1', db)
    await engine.dispose()
    assert configured.call_count >= 1
    assert configured.call_args.args[1] == 'test-key-only'


def test_private_workspace_paths_include_snapshots_and_encryption_keys(tmp_path):
    import subprocess, sys, json
    directory = tmp_path / 'worker'
    directory.mkdir()
    env = worker_environment(directory, 'u1', 'temporary-test-secret-abcdefghijklmnopqrstuvwxyz')
    code = "from app.config import DATA_DIR; from app.core.kaf_service import KAF_DIR,_KEY_DIR; from app.core.crypto import _KEY_FILE; import json; print(json.dumps([str(DATA_DIR),str(KAF_DIR),str(_KEY_DIR),str(_KEY_FILE)]))"
    process = subprocess.run([sys.executable, '-c', code], env=env, cwd=__import__('pathlib').Path(__file__).parents[1], capture_output=True, text=True, timeout=30)
    assert process.returncode == 0, process.stderr
    paths = json.loads(process.stdout.splitlines()[-1])
    assert all(__import__('pathlib').Path(path).is_relative_to(directory) for path in paths)


async def test_private_service_does_not_inherit_controller_stdin(tmp_path, monkeypatch):
    import subprocess
    from app.core import cloud_workspaces
    launched = {}

    class Process:
        stopped = False
        def poll(self):
            return 0 if self.stopped else None
        def terminate(self):
            self.stopped = True
        def wait(self, timeout):
            return 0

    def spawn(args, **kwargs):
        launched.update(kwargs)
        return Process()

    class HealthClient:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def get(self, *args, **kwargs):
            return SimpleNamespace(status_code=200)

    monkeypatch.setattr(cloud_workspaces.subprocess, 'Popen', spawn)
    monkeypatch.setattr(cloud_workspaces.httpx, 'AsyncClient', HealthClient)
    pool = WorkspacePool(tmp_path, 'test-signing-secret')
    worker = await pool._launch(SimpleNamespace(id='owner', username='owner', email='owner@example.test'))
    try:
        assert launched.get('stdin') == subprocess.PIPE
    finally:
        pool._stop(worker)
