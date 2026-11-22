"""Private summary/document ACL runs before agent keys or paid orchestration."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker


@pytest.fixture
async def agent_boundary(tmp_path, monkeypatch):
    from app.database import Base, get_db
    from app.models import Document, KnowledgeSummary
    from app.api import agents
    engine = create_async_engine('sqlite+aiosqlite:///' + (tmp_path / 'agents.sqlite').as_posix())
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all, tables=[Document.__table__, KnowledgeSummary.__table__])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add_all([Document(id='private', filename='private.md', file_type='md', sha256='p', metadata_={'knowledge_owner_id': 'alice'}),
                    Document(id='legacy', filename='legacy.md', file_type='md', sha256='q')])
        db.add_all([KnowledgeSummary(id='private-summary', document_id='private', document_ids=['private'], content_md='private'),
                    KnowledgeSummary(id='legacy-summary', document_id='legacy', document_ids=['legacy'], content_md='legacy')])
        await db.commit()
    calls = []
    async def keys(*args):
        calls.append('keys')
    async def orchestrate(**kwargs):
        calls.append('paid')
        return {'status': 'simulated'}
    async def stream(**kwargs):
        calls.append('paid')
        yield 'data: simulated\n\n'
    monkeypatch.setattr(agents, 'load_user_api_keys', keys)
    monkeypatch.setattr(agents.orchestrator, 'orchestrate', orchestrate)
    monkeypatch.setattr(agents.orchestrator, 'orchestrate_sse', stream)
    app = FastAPI()
    app.include_router(agents.router)
    async def database():
        async with factory() as db:
            yield db
    account = {'id': 'bob'}
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[agents.study_user] = lambda: SimpleNamespace(id=account['id']) if account['id'] else None
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://agents-boundary') as client:
        yield SimpleNamespace(client=client, calls=calls, account=account)
    await engine.dispose()


@pytest.mark.parametrize('endpoint', ['/orchestrate', '/orchestrate/stream'])
@pytest.mark.parametrize('account', [None, 'alice', 'bob'])
@pytest.mark.parametrize('body', [
    {'summary_id': 'private-summary', 'document_id': 'private'},
    {'summary_id': 'private-summary', 'document_id': 'legacy'},
    {'summary_id': 'legacy-summary', 'document_id': 'private'},
])
async def test_foreign_private_sources_rejected_before_key_or_paid_call(agent_boundary, endpoint, body, account):
    agent_boundary.account['id'] = account
    response = await agent_boundary.client.post('/api/v1/agents' + endpoint, json=body)
    assert response.status_code in (403, 404), response.text
    assert agent_boundary.calls == []


@pytest.mark.parametrize('endpoint', ['/orchestrate', '/orchestrate/stream'])
@pytest.mark.parametrize('account,source', [('alice', 'legacy'), ('bob', 'legacy'), (None, 'legacy')])
async def test_owner_and_legacy_agent_flows_remain_available(agent_boundary, endpoint, account, source):
    agent_boundary.account['id'] = account
    response = await agent_boundary.client.post('/api/v1/agents' + endpoint,
                                                json={'summary_id': source + '-summary', 'document_id': source})
    assert response.status_code == 200, response.text
    assert agent_boundary.calls == ['keys', 'paid']


@pytest.mark.parametrize('endpoint', ['/orchestrate', '/orchestrate/stream'])
async def test_missing_summary_fails_before_orchestration(agent_boundary, endpoint):
    response = await agent_boundary.client.post('/api/v1/agents' + endpoint,
                                                json={'summary_id': 'missing', 'document_id': 'legacy'})
    assert response.status_code == 404, response.text
    assert agent_boundary.calls == []
