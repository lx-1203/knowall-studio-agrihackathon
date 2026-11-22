"""Actual localhost research SSE, private SQLite/JWT; AI/search explicitly simulated."""
import asyncio
import hashlib
import json
import os
import socket
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime
from pathlib import Path

import pytest
import httpx
from sqlalchemy import select

from app.config import settings
from app.core.api_scheduler.client import _current_user_id
from app.models.user import User
from tests.test_generation_identity_flow import flow, identity


@asynccontextmanager
async def server(app):
    """Direct local TCP, independent of the machine's HTTP proxy settings."""
    import uvicorn
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    uv = uvicorn.Server(uvicorn.Config(app, log_level='critical', lifespan='off', timeout_graceful_shutdown=2))
    job = asyncio.create_task(uv.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(5):
            while not uv.started:
                if job.done():
                    await job
                    raise AssertionError('server did not start')
                await asyncio.sleep(.01)
        async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{listener.getsockname()[1]}',
                                     timeout=30, trust_env=False) as client:
            yield client
    finally:
        uv.should_exit = True
        try:
            await asyncio.wait_for(job, 5)
        finally:
            listener.close()
            app.state.research_stream_listener = {'SocketClosed': listener.fileno() == -1,
                                                 'ServerJobDone': job.done()}


@pytest.fixture
def evidence(request, tmp_path):
    data = {'Test': request.node.nodeid, 'Start': datetime.now().astimezone().isoformat(),
            'ExternalAI': 'explicit in-process simulation', 'Search': 'explicit in-process simulation',
            'BackendSamples': [], 'Events': [], 'AIUsers': [], 'ActiveSubscriptions': 0}
    yield data
    data['End'] = datetime.now().astimezone().isoformat()
    root = Path(__file__).resolve().parents[1]
    paths = ['app/core/agents/research_agent.py', 'app/api/research.py', 'app/core/auth.py',
             'app/core/generation_access.py', 'app/database.py', 'app/models/user.py']
    data['SourceHashes'] = {p: hashlib.sha256((root/p).read_bytes()).hexdigest() for p in paths}
    out = Path(os.environ.get('KNOWALL_RESEARCH_STREAM_EVIDENCE_DIR', str(tmp_path/'evidence')))
    out.mkdir(parents=True, exist_ok=True)
    (out/('http-'+hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:16]+'.json')).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding='utf8')


def record(evidence, response, account):
    sample = {'URL': str(response.request.url), 'Method': response.request.method,
              'Status': response.status_code, 'Account': account}
    if response.is_stream_consumed:
        sample['Body'] = response.text
    else:
        sample['ContentType'] = response.headers.get('content-type')
    evidence['BackendSamples'].append(sample)


async def read_event(lines, evidence, name):
    event = data = None
    async with asyncio.timeout(3):
        async for line in lines:
            if line.startswith('event: '):
                event = line[7:]
            elif line.startswith('data: '):
                data = json.loads(line[6:])
            elif not line and event:
                value = {'event': event, 'data': data}
                evidence['Events'].append({'Subscriber': name, **value})
                return value
    raise AssertionError('stream ended without expected event')


async def subscribers(evidence, count):
    async with asyncio.timeout(3):
        while evidence['ActiveSubscriptions'] != count:
            await asyncio.sleep(.01)


@pytest.mark.parametrize('outcome', ['completed', 'provider-error', 'service-close'])
async def test_real_sse_same_phase_disconnect_reconnect_owner_and_terminal(identity, monkeypatch, evidence, outcome):
    monkeypatch.setattr(settings, 'desktop_require_login', True)
    entered = [asyncio.Event() for _ in range(3)]
    release = [asyncio.Event() for _ in range(3)]
    tool = identity.search.side_effect
    count = 0

    async def simulated_tool(name, **kwargs):
        nonlocal count
        if name == 'knowledge_search':
            index = count
            count += 1
            entered[index].set()
            await release[index].wait()
        return await tool(name, **kwargs)

    async def simulated_model(prompt, **kwargs):
        evidence['AIUsers'].append(_current_user_id.get())
        if '分解为' in prompt:
            return json.dumps([{'title': f'明确模拟子课题{i}', 'description': '受控多阶段'} for i in range(3)])
        if '撰写一份结构化' in prompt and outcome == 'provider-error':
            raise RuntimeError('明确模拟报告服务失败')
        return await identity.chat(prompt, **kwargs)

    identity.model.side_effect = simulated_model
    identity.search.side_effect = simulated_tool
    original = identity.agent.stream_progress

    async def tracked(task_id):
        evidence['ActiveSubscriptions'] += 1
        try:
            async for event in original(task_id):
                yield event
        finally:
            evidence['ActiveSubscriptions'] -= 1

    monkeypatch.setattr(identity.agent, 'stream_progress', tracked)
    async with identity.factory() as db:
        users = (await db.scalars(select(User))).all()
        evidence['SQLUsers'] = sorted((u.id, u.is_active) for u in users)
        evidence['DatabaseURL'] = str(db.bind.url)
        assert len(users) == 3 and 'source.sqlite' in str(db.bind.url)

    try:
        async with server(identity.client._transport.app) as client:
            response = await client.post('/api/v1/research/start', headers=identity.headers['alice'],
                                         json={'topic': '明确模拟持续研究', 'max_subtopics': 3, 'user_id': 'bob'})
            record(evidence, response, 'alice')
            assert response.status_code == 200
            task_id = response.json()['task_id']
            task = identity.agent.get_task(task_id, 'alice')
            assert task and task.user_id == 'alice'
            await asyncio.wait_for(entered[0].wait(), 3)
            path = f'/api/v1/research/{task_id}/stream'
            for account, status in [('bob', 404), ('disabled', 403), ('missing', 401), ('anonymous', 401)]:
                response = await client.get(path, headers=identity.headers.get(account, {}))
                record(evidence, response, account)
                assert response.status_code == status
            assert evidence['ActiveSubscriptions'] == 0

            async with AsyncExitStack() as stack:
                first = await stack.enter_async_context(client.stream('GET', path, headers=identity.headers['alice']))
                second = await stack.enter_async_context(client.stream('GET', path, headers=identity.headers['alice']))
                for response in (first, second):
                    record(evidence, response, 'alice')
                    assert response.status_code == 200
                    assert response.headers['content-type'].startswith('text/event-stream')
                first_lines, second_lines = first.aiter_lines(), second.aiter_lines()
                initial = await read_event(first_lines, evidence, 'disconnected')
                assert await read_event(second_lines, evidence, 'persistent') == initial
                assert initial['data']['status'] == 'researching'
                await first.aclose()
                await subscribers(evidence, 1)
                assert task.status.value == 'researching' and identity.agent._running
                release[0].set()
                await asyncio.wait_for(entered[1].wait(), 3)
                middle = await read_event(second_lines, evidence, 'persistent')
                assert middle['data']['status'] == initial['data']['status']
                assert middle['data']['progress'] == pytest.approx(.4)
                assert [s['status'] for s in middle['data']['sub_topics']] == ['done', 'researching', 'pending']
                reconnect = await stack.enter_async_context(client.stream('GET', path, headers=identity.headers['alice']))
                record(evidence, reconnect, 'alice')
                reconnect_lines = reconnect.aiter_lines()
                assert await read_event(reconnect_lines, evidence, 'reconnected') == middle

                if outcome == 'service-close':
                    await identity.agent.close()
                else:
                    release[1].set()
                    await asyncio.wait_for(entered[2].wait(), 3)
                    late = await read_event(second_lines, evidence, 'persistent')
                    assert late['data']['progress'] == pytest.approx(.6)
                    assert await read_event(reconnect_lines, evidence, 'reconnected') == late
                    release[2].set()
                expected = 'complete' if outcome == 'completed' else 'error'
                for name, lines in [('persistent', second_lines), ('reconnected', reconnect_lines)]:
                    final = await read_event(lines, evidence, name)
                    assert final['event'] == expected
                    if expected == 'complete':
                        assert '研究结果' in final['data']['report'] and final['data']['sources']
                    else:
                        assert final['data'] and task.progress < 1
                    with pytest.raises(StopAsyncIteration):
                        await asyncio.wait_for(anext(lines), 3)
                await subscribers(evidence, 0)
            response = await client.get(f'/api/v1/research/{task_id}/status', headers=identity.headers['alice'])
            record(evidence, response, 'alice')
            assert response.json()['status'] == ('completed' if outcome == 'completed' else 'error')
            response = await client.get(path, headers=identity.headers['alice'])
            record(evidence, response, 'alice')
            assert response.text.count('event: ') == 1 and f'event: {expected}' in response.text
            assert set(evidence['AIUsers']) == {'alice'}
            evidence['FinalState'] = task.status.value
            origin = str(client.base_url)
        # A different connection after context teardown cannot reach our listener.
        async with httpx.AsyncClient(timeout=2, trust_env=False) as probe:
            with pytest.raises((httpx.ConnectError, httpx.ConnectTimeout)) as closed:
                await probe.get(origin.rstrip('/') + '/api/v1/research/tasks')
        evidence['ClosedProbeException'] = type(closed.value).__name__
        evidence['ListenerState'] = identity.client._transport.app.state.research_stream_listener
        assert all(evidence['ListenerState'].values())
        evidence['ListenerClosed'] = True
        assert evidence['ActiveSubscriptions'] == 0
        assert not identity.agent._running
    finally:
        for gate in release:
            gate.set()
        await identity.agent.close()
