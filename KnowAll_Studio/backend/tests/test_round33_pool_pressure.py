"""Native pool pressure across real JWT authentication and export dependencies."""
import asyncio
import json
import time
from pathlib import Path
import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.api import flashcards
from app.core.auth import create_access_token, hash_password, get_optional_user
from app.database import Base, get_db
from app.models import User, Deck, Flashcard

@pytest.fixture
async def pool_app(tmp_path, monkeypatch, request):
    engine = create_async_engine('sqlite+aiosqlite:///' + (tmp_path/'pool.sqlite').as_posix(), pool_size=1, max_overflow=0, pool_timeout=getattr(request, "param", .15))
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add_all([User(id='A', username='pool_a', email='a@pool.invalid', password_hash=hash_password('Temporary-pass-33')), User(id='B', username='pool_b', email='b@pool.invalid', password_hash=hash_password('Temporary-pass-33'))])
        db.add(Deck(id='private-pool-deck', name='池恢复牌组', user_id='A', card_count=1))
        db.add(Flashcard(id='pool-card', deck_id='private-pool-deck', front='连接恢复 $x_1$', back='持久答案', card_type='qa'))
        await db.commit()
    dependency_entered = asyncio.Event()
    async def database():
        async with factory() as db:
            dependency_entered.set()
            yield db
    app = FastAPI()
    app.include_router(flashcards.router)
    app.dependency_overrides[get_db] = database
    paths = []
    original = flashcards.tempfile.mkstemp
    def capture(*args, **kwargs):
        descriptor, path = original(*args, **kwargs)
        paths.append(Path(path))
        return descriptor, path
    monkeypatch.setattr(flashcards.tempfile, 'mkstemp', capture)
    headers = {'Authorization':'Bearer ' + create_access_token('A')}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url='http://pool') as client:
        yield client, engine, factory, app, headers, paths, dependency_entered
    await engine.dispose()

URL = '/api/v1/flashcards/export/anki/private-pool-deck'

@pytest.mark.parametrize('authenticated', [True, False])
async def test_native_pool_exhaustion_is_503_in_auth_or_deck_lookup(pool_app, authenticated):
    client, engine, factory, _, headers, paths, _ = pool_app
    async with engine.connect():
        started = time.monotonic()
        response = await client.get(URL, headers=headers if authenticated else {})
        elapsed = time.monotonic() - started
    print('ROUND33_POOL_BUSY ' + json.dumps({'authenticated':authenticated, 'elapsed':elapsed, 'status':response.status_code}))
    assert response.status_code == 503, response.text
    assert response.headers['retry-after'] == '1'
    assert not any(word in response.text.lower() for word in ['queuepool', 'sqlalchemy', 'select ', 'private-pool-deck', 'pool_a'])
    assert elapsed < 1 and paths == [] and engine.pool.checkedout() == 0
    async with factory() as db:
        card = await db.get(Flashcard, 'pool-card')
        assert card.anki_export_version is None and card.anki_export_mtime is None
    recovered = await client.get(URL, headers=headers if authenticated else {})
    assert recovered.status_code == (200 if authenticated else 404), recovered.text
    if authenticated:
        assert recovered.content[:2] == b'PK'
        assert paths and all(not path.exists() for path in paths)

@pytest.mark.parametrize("pool_app", [5], indirect=True)
async def test_pool_checkout_cancel_preserves_task_and_does_not_leak(pool_app):
    client, engine, _, _, headers, paths, entered = pool_app
    # Long native pool wait proves cancellation, not completion of the .15s timeout.
    async with engine.connect():
        entered.clear()
        task = asyncio.create_task(client.get(URL, headers=headers))
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.sleep(.02)
        started = time.monotonic()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert time.monotonic() - started < .5
        assert paths == [] and engine.pool.checkedout() == 1
    assert engine.pool.checkedout() == 0
    response = await client.get(URL, headers=headers)
    assert response.status_code == 200, response.text
    assert engine.pool.checkedout() == 0

async def test_foreign_and_invalid_token_remain_private_404(pool_app):
    client, engine, _, _, _, paths, _ = pool_app
    for token in [create_access_token('B'), 'invalid-token']:
        response = await client.get(URL, headers={'Authorization':'Bearer '+token})
        assert response.status_code == 404 and 'retry-after' not in response.headers
    assert paths == [] and engine.pool.checkedout() == 0

async def test_non_export_route_keeps_existing_pool_failure(pool_app):
    client, engine, _, _, headers, paths, _ = pool_app
    async with engine.connect():
        response = await client.get('/api/v1/flashcards/decks', headers=headers)
    assert response.status_code == 500 and 'retry-after' not in response.headers
    assert paths == []

@pytest.mark.parametrize('detail', ['authorization-denied', 'upstream-limited'])
async def test_auth_http_exceptions_preserve_original_status_and_headers(pool_app, detail):
    from fastapi import HTTPException
    client, _, _, app, headers, paths, _ = pool_app
    status = 403 if detail == 'authorization-denied' else 429
    async def rejected():
        raise HTTPException(status, detail, headers={'X-Boundary':'preserved'})
    app.dependency_overrides[get_optional_user] = rejected
    response = await client.get(URL, headers=headers)
    assert response.status_code == status and response.headers['x-boundary'] == 'preserved'
    assert 'retry-after' not in response.headers and paths == []

async def test_registration_has_one_export_and_native_security_schema(pool_app):
    _, _, _, app, _, _, _ = pool_app
    routes = [route for route in app.routes if getattr(route, 'path', '') == '/api/v1/flashcards/export/anki/{deck_id}']
    assert len(routes) == 1
    operation = app.openapi()['paths']['/api/v1/flashcards/export/anki/{deck_id}']['get']
    assert operation['security'] == [{'HTTPBearer': []}]


async def test_second_checkout_inside_export_preserves_native_busy_type(pool_app, monkeypatch):
    from contextlib import asynccontextmanager
    client, engine, _, _, headers, paths, _ = pool_app
    original = flashcards.anki_export_session
    @asynccontextmanager
    async def contention_after_authorization(db):
        # Authorization has succeeded. Another real lease holds the only slot
        # while the export's explicit native connection performs its checkout.
        await db.rollback()
        async with engine.connect():
            async with original(db) as export_db:
                yield export_db
    monkeypatch.setattr(flashcards, 'anki_export_session', contention_after_authorization)
    response = await client.get(URL, headers=headers)
    assert response.status_code == 503 and response.headers['retry-after'] == '1'
    assert paths == [] and engine.pool.checkedout() == 0
    monkeypatch.setattr(flashcards, 'anki_export_session', original)
    recovered = await client.get(URL, headers=headers)
    assert recovered.status_code == 200 and recovered.content[:2] == b'PK'

async def test_real_auth_sql_failure_is_not_pool_retry(pool_app):
    client, engine, factory, _, headers, paths, _ = pool_app
    # Real missing-table driver error, not a thrown SQLAlchemy mock exception.
    async with factory() as db:
        await db.execute(text('DROP TABLE users'))
        await db.commit()
    response = await client.get(URL, headers=headers)
    assert response.status_code == 500 and 'retry-after' not in response.headers
    assert paths == [] and engine.pool.checkedout() == 0
