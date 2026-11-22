"""Native HTTPX SSE framing through real reading/cache routes, simulated AI."""
import json
import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from app.api import reading
from app.models import ReadingConversionCache
from app.database import get_db
from tests.test_reading_native_stream_flow import disk, ARTICLE
from tests.test_reading_native_stream_http_flow import server


def payload(kind):
    start = 'data:{"choices":\ndata:[{"delta":{"content":"完整 world"},\ndata:"finish_reason":"stop"}]}\n\n'
    if kind == 'error':
        return start + 'event:error\ndata:{"error":{"message":"explicit simulation"}}\n\n'
    if kind == 'incomplete':
        return start.replace(',\ndata:"finish_reason":"stop"', '')
    if kind == 'length':
        return start.replace('"stop"', '"length"') + 'data:[DONE]\n\n'
    if kind == 'malformed':
        return start + 'data:{broken}\n\ndata:[DONE]\n\n'
    body = ': comment\n\n' + start + 'data:{"choices":[],"usage":{"total_tokens":3}}\n\ndata:[DONE]\n\n'
    return body.replace('\n', '\r\n') if kind == 'crlf' else body


@pytest.mark.parametrize('kind', ['multiline', 'crlf'])
async def test_reading_native_decoder_joins_data_and_fragmented_utf8(monkeypatch, kind):
    original = httpx.AsyncClient; responses = []; clients = []
    class Bytes(httpx.AsyncByteStream):
        async def __aiter__(self):
            body = payload(kind).encode()
            for pos in range(0, len(body), 3): yield body[pos:pos + 3]
    def handler(req):
        response = httpx.Response(200, stream=Bytes(), headers={'content-type': 'text/event-stream'})
        responses.append(response); return response
    def client(*args, **kwargs):
        result = original(*args, transport=httpx.MockTransport(handler), **kwargs); clients.append(result); return result
    monkeypatch.setattr(httpx, 'AsyncClient', client)
    events = [json.loads(e[6:]) async for e in reading.stream_deepseek(ARTICLE, [], ['world'])]
    assert ''.join(e.get('token', '') for e in events) == '完整 world'
    assert events[-1]['done'] and events[-1]['result'] == '完整 world'
    assert responses[0].is_closed and clients[0].is_closed


@pytest.mark.parametrize('kind', ['multiline', 'crlf', 'incomplete', 'length', 'malformed', 'error'])
async def test_reading_actual_tcp_multiline_cache_or_complete_fallback(disk, monkeypatch, kind):
    provider = FastAPI(); calls = []
    @provider.post('/v1/chat/completions')
    async def simulation(req: Request):
        calls.append(await req.json())
        async def bytes_stream():
            raw = payload(kind).encode()
            for pos in range(0, len(raw), 7): yield raw[pos:pos + 7]
        return StreamingResponse(bytes_stream(), media_type='text/event-stream')
    app = FastAPI(); app.include_router(reading.router)
    async def database():
        async with disk.factory() as db: yield db
    app.dependency_overrides[get_db] = database
    monkeypatch.setattr(reading, 'DEEPSEEK_API_KEY', 'explicit-simulation')
    async with server(provider) as remote:
        monkeypatch.setattr(reading, 'DEEPSEEK_API_URL', str(remote.base_url).rstrip('/') + '/v1/chat/completions')
        async with server(app) as client:
            first = await client.post('/api/v1/reading/convert/stream', json={'text': ARTICLE, 'level': 2})
            events = [json.loads(l[6:]) for l in first.text.splitlines() if l.startswith('data: ')]
            final = events[-1]
            assert final['done'] and sum(e.get('done', False) for e in events) == 1
            if kind in ('multiline', 'crlf'):
                assert 'source' not in final and final['result'] == '完整 world'
                assert ''.join(e.get('token', '') for e in events) == '完整 world'
            else:
                assert final.get('source') == 'dictionary' and final['result'] != '完整 world'
            async with disk.factory() as db:
                rows = (await db.scalars(select(ReadingConversionCache))).all()
                assert len(rows) == 1 and rows[0].result == final['result']
                assert json.loads(rows[0].vocabulary) == final['vocabulary']
            second = await client.post('/api/v1/reading/convert/stream', json={'text': ARTICLE, 'level': 2})
            cached = [json.loads(l[6:]) for l in second.text.splitlines() if l.startswith('data: ')]
            assert len(cached) == 1 and cached[0]['cached'] and cached[0]['result'] == final['result']
    assert len(calls) == 1
