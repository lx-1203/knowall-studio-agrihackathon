"""Real routes/SQLite and native ASGI cancellation. Provider/retrieval explicitly simulated."""
import asyncio
import json
from types import SimpleNamespace

import anyio
import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.api import reading, chat
from app.core import rag_assistant as rag_module
from app.database import Base
from app.models import ReadingConversionCache, Conversation, Message


@pytest.fixture
async def disk(tmp_path, monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///' + (tmp_path / 'test.sqlite').as_posix())
    async with engine.begin() as connection:
        await connection.run_sync(lambda c: Base.metadata.create_all(c, tables=[ReadingConversionCache.__table__, Conversation.__table__, Message.__table__]))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    import app.database
    monkeypatch.setattr(app.database, 'async_session', factory)
    yield factory
    await engine.dispose()


def tracked(values):
    state = {'closed': False}
    async def generator():
        state['task'] = asyncio.current_task()
        try:
            for value in values:
                yield value
        finally:
            # A real asynchronous cleanup checkpoint proves level cancellation shielding.
            await anyio.lowlevel.checkpoint()
            state['closed'] = True
            state['close_task'] = asyncio.current_task()
    stream = generator()  # Keep a strong reference so GC cannot make a leak pass.
    return stream, state


@pytest.mark.parametrize('kind', ['done', 'error', 'token'])
async def test_reading_early_exit_closes_upstream(disk, monkeypatch, kind):
    event = {'done': True, 'result': '完整 world', 'vocabulary': []} if kind == 'done' else {kind: 'explicit simulation'}
    upstream, state = tracked(['data: ' + json.dumps(event) + '\n\n'])
    monkeypatch.setattr(reading, 'DEEPSEEK_API_KEY', 'explicit-simulation')
    monkeypatch.setattr(reading, 'stream_deepseek', lambda *a: upstream)
    async with disk() as db:
        response = await reading.convert_stream(reading.ConvertStreamRequest(text='世界很大，学习知识帮助我们理解世界。', skip_cache=True), db)
        await anext(response.body_iterator)
        await response.body_iterator.aclose()
        try:
            assert state['closed'] and state['task'] is state['close_task']
            rows = (await db.execute(select(ReadingConversionCache))).scalars().all()
            assert len(rows) == (0 if kind == 'token' else 1)
        finally:
            await upstream.aclose()


@pytest.mark.parametrize('endpoint,request_type', [(chat.chat_stream, chat.ChatRequest), (chat.chat_rag_stream, chat.RAGChatRequest), (chat.chat_graphrag_stream, chat.GraphRAGChatRequest)])
async def test_chat_discarded_token_closes_without_saving_assistant(disk, monkeypatch, endpoint, request_type):
    upstream, state = tracked(['partial'])
    monkeypatch.setattr(chat.ai_assistant, 'chat_stream', lambda *a, **kw: upstream)
    monkeypatch.setattr(chat.rag_assistant, 'chat_stream_with_rag', lambda *a, **kw: upstream)
    async with disk() as db:
        response = await endpoint(request_type(message='question'), db, None)
        assert 'partial' in await anext(response.body_iterator)
        await response.body_iterator.aclose()
        try:
            assert state['closed'] and state['task'] is state['close_task']
            assert [m.role for m in (await db.execute(select(Message))).scalars()] == ['user']
        finally:
            await upstream.aclose()


@pytest.mark.parametrize('mode,context', [('rag', ''), ('rag', 'reference'), ('graphrag', ''), ('graphrag', 'reference'), ('pipeline', ''), ('pipeline', 'reference'), ('broken', '')])
async def test_nested_retrieval_forwarders_close(mode, context, monkeypatch):
    upstream, state = tracked(['partial'])
    monkeypatch.setattr(rag_module.base_assistant, 'chat_stream', lambda *a, **kw: upstream)
    monkeypatch.setattr(rag_module, 'rag_query', lambda *a: context)
    import app.core.graph_rag
    async def graph(**kw):
        assert kw['top_k_vector'] == 7
        return context, []
    monkeypatch.setattr(app.core.graph_rag, 'graph_rag_query', graph)
    async def search(*a, **kw):
        assert kw['top_k'] == 7
        return SimpleNamespace(context=context)
    def pipeline(name):
        if name == 'broken':
            raise ValueError('explicit simulated missing provider')
        return SimpleNamespace(search=search)
    monkeypatch.setattr(rag_module, 'get_pipeline_by_name', pipeline)
    outer = rag_module.RAGAssistant().chat_stream_with_rag('question', top_k=7, mode=mode)
    try:
        assert await anext(outer) == 'partial'
        await outer.aclose()
        assert state['closed'] and state['task'] is state['close_task']
    finally:
        await outer.aclose()
        await upstream.aclose()


@pytest.mark.parametrize('spec', ['2.4', '2.0'])
async def test_native_asgi_send_disconnect_closes_suspended_iterator(spec):
    stream, state = tracked(['partial', 'remaining'])
    response = chat.StreamingResponse(stream, media_type='text/event-stream')
    sent = asyncio.Event()
    async def send(message):
        if message['type'] == 'http.response.body':
            sent.set()
            if spec == '2.4':
                raise OSError('explicit simulated client disconnect')
            await anyio.sleep_forever()
    async def receive():
        await sent.wait()
        return {'type': 'http.disconnect'}
    try:
        if spec == '2.4':
            from starlette.requests import ClientDisconnect
            with pytest.raises(ClientDisconnect):
                await response({'type': 'http', 'asgi': {'spec_version': spec}}, receive, send)
        else:
            await response({'type': 'http', 'asgi': {'spec_version': spec}}, receive, send)
        assert state['closed'] and state['task'] is state['close_task']
    finally:
        await stream.aclose()


@pytest.mark.parametrize('endpoint,request_type', [(chat.chat_stream, chat.ChatRequest), (chat.chat_rag_stream, chat.RAGChatRequest), (chat.chat_graphrag_stream, chat.GraphRAGChatRequest), (reading.convert_stream, reading.ConvertStreamRequest)])
async def test_chat_native_level_cancel_during_next_token_cleans_nested_stream(disk, monkeypatch, endpoint, request_type):
    state = {'closed': False}
    waiting = asyncio.Event()
    class Provider(httpx.AsyncByteStream):
        async def __aiter__(self):
            state['task'] = asyncio.current_task()
            yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
            waiting.set()
            await anyio.sleep_forever()
        async def aclose(self):
            await anyio.lowlevel.checkpoint()
            state['closed'] = True
            state['close_task'] = asyncio.current_task()
    clients, responses = [], []
    def simulation(req):
        response = httpx.Response(200, stream=Provider(), headers={'content-type': 'text/event-stream'})
        responses.append(response)
        return response
    original = httpx.AsyncClient
    def client(**kwargs):
        instance = original(transport=httpx.MockTransport(simulation), **kwargs)
        clients.append(instance)
        return instance
    monkeypatch.setattr(httpx, 'AsyncClient', client)
    from app.core.assistant import api_client
    monkeypatch.setattr(api_client, 'get_adapter', lambda *a, **kw: SimpleNamespace(base_url='http://explicit-simulation/v1', model_name='simulation', _build_headers=lambda: {}))
    monkeypatch.setattr(reading, 'DEEPSEEK_API_KEY', 'explicit-simulation')
    monkeypatch.setattr(rag_module, 'rag_query', lambda *a: '')
    import app.core.graph_rag
    async def graph(**kw):
        return '', []
    monkeypatch.setattr(app.core.graph_rag, 'graph_rag_query', graph)
    async with disk() as db:
        response = await endpoint(request_type(text='世界很大，学习知识帮助我们理解世界。', skip_cache=True), db) if endpoint is reading.convert_stream else await endpoint(request_type(message='question'), db, None)
        async def send(message):
            pass
        async def receive():
            await waiting.wait()
            return {'type': 'http.disconnect'}
        try:
            await asyncio.wait_for(response({'type': 'http', 'asgi': {'spec_version': '2.0'}}, receive, send), 3)
            assert state['closed'] and state['task'] is state['close_task']
            assert clients[0].is_closed and responses[0].is_closed
            if endpoint is reading.convert_stream:
                assert not (await db.execute(select(ReadingConversionCache))).scalars().all()
            else:
                assert [m.role for m in (await db.execute(select(Message))).scalars()] == ['user']
        finally:
            for instance in clients:
                await instance.aclose()


@pytest.mark.parametrize('endpoint,request_type', [(chat.chat_stream, chat.ChatRequest), (chat.chat_rag_stream, chat.RAGChatRequest), (chat.chat_graphrag_stream, chat.GraphRAGChatRequest)])
async def test_normal_chat_completion_closes_and_saves_once(disk, monkeypatch, endpoint, request_type):
    upstream, state = tracked(['first', 'second'])
    monkeypatch.setattr(chat.ai_assistant, 'chat_stream', lambda *a, **kw: upstream)
    monkeypatch.setattr(chat.rag_assistant, 'chat_stream_with_rag', lambda *a, **kw: upstream)
    async with disk() as db:
        response = await endpoint(request_type(message='question'), db, None)
        events = [json.loads(event[6:]) async for event in response.body_iterator]
        assert state['closed'] and len([event for event in events if event.get('done')]) == 1
        messages = (await db.execute(select(Message))).scalars().all()
        assert sorted((m.role, m.content) for m in messages) == [('assistant', 'firstsecond'), ('user', 'question')]


async def test_context_forwarder_closes_delegated_stream(monkeypatch):
    from app.core.assistant import Assistant
    upstream, state = tracked(['partial'])
    assistant = Assistant()
    monkeypatch.setattr(assistant, 'chat_stream', lambda *a, **kw: upstream)
    outer = assistant.chat_stream_with_context('question', 'reference')
    try:
        assert await anext(outer) == 'partial'
        await outer.aclose()
        assert state['closed'] and state['task'] is state['close_task']
    finally:
        await outer.aclose()
        await upstream.aclose()
