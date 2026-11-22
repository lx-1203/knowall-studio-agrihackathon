"""Real localhost reading/cache endpoints; only model provider bytes are simulated."""
import hashlib, json
import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from app.api import reading
from app.models import ReadingConversionCache
from tests.test_reading_native_stream_flow import disk, ARTICLE, delta
from tests.test_reading_native_stream_http_flow import server, app_for, evidence, record, events

def frame(value):
    return 'data: ' + json.dumps(value, ensure_ascii=False) + '\n\n'

def body(kind):
    begin = delta('完整 world')
    stop = delta('', 'stop')
    done = 'data: [DONE]\n\n'
    bad = {
        'error-empty': {'error': {}, 'choices': [{'delta': {}}]},
        'error-null': {'error': None, 'choices': [{'delta': {}}]},
        'error-false': {'error': False, 'choices': [{'delta': {}}]},
        'usage-invalid': {'choices': [], 'usage': 'invalid'},
        'usage-absent': {'choices': []},
        'choices-string': {'choices': 'not a choice list'},
        'choice-null': {'choices': [None]},
        'delta-array': {'choices': [{'delta': []}]},
        'tool-call': {'choices': [{'delta': {'tool_calls': [{'id': 'tool'}]}}]},
        'function-call': {'choices': [{'delta': {'function_call': {'name': 'unsupported'}}}]},
    }
    if kind in bad:
        return begin + frame(bad[kind]) + stop + done
    if kind == 'after-stop':
        return begin + stop + delta('追加非法') + done
    if kind == 'not-object':
        return begin + frame([]) + stop + done
    if kind == 'marker-only':
        return delta('<<   >>', 'stop') + done
    good = {
        'stop-only': delta('完整 world', 'stop'),
        'done-only': begin + done,
        'stop-and-usage': begin + stop + frame({'choices': [], 'usage': {'total_tokens': 3}}) + done,
        'role-then-stop': frame({'choices': [{'delta': {'role': 'assistant'}}]}) + delta('完整 world', 'stop') + done,
        'same-frame-stop': delta('完整 world', 'stop') + done,
    }
    return good[kind]

BAD = ['error-empty', 'error-null', 'error-false', 'usage-invalid', 'usage-absent',
       'choices-string', 'choice-null', 'delta-array', 'tool-call', 'function-call', 'after-stop', 'not-object', 'marker-only']

@pytest.mark.parametrize('kind', BAD + ['stop-only', 'done-only', 'stop-and-usage', 'role-then-stop', 'same-frame-stop'])
async def test_native_reading_terminal_pair_and_cache(disk, monkeypatch, evidence, kind):
    provider = FastAPI()
    calls = []
    @provider.post('/v1/chat/completions')
    async def simulation(request: Request):
        data = await request.json()
        calls.append({'payload': data, 'accept': request.headers.get('accept'), 'cache_control': request.headers.get('cache-control')})
        evidence['ProviderRequests'].append(calls[-1])
        async def fragmented():
            raw = body(kind).encode()
            for i in range(0, len(raw), 5):
                yield raw[i:i + 5]
        return StreamingResponse(fragmented(), media_type='text/event-stream')
    monkeypatch.setattr(reading, 'DEEPSEEK_API_KEY', 'explicit-simulation')
    async with server(provider) as remote:
        monkeypatch.setattr(reading, 'DEEPSEEK_API_URL', str(remote.base_url).rstrip('/') + '/v1/chat/completions')
        async with server(app_for(disk)) as client:
            request = {'text': ARTICLE, 'level': 2}
            first = await client.post('/api/v1/reading/convert/stream', json=request)
            record(evidence, first)
            values = events(first)
            terminal = values[-1]
            assert terminal.get('done') and sum(v.get('done', False) for v in values) == 1
            if kind in BAD:
                assert terminal.get('source') == 'dictionary', terminal
                assert '追加非法' not in terminal['result'] and terminal['result'] != '完整 world'
            else:
                assert terminal['result'] == '完整 world' and 'source' not in terminal
            async with disk.factory() as db:
                rows = (await db.scalars(select(ReadingConversionCache))).all()
                assert len(rows) == 1 and rows[0].result == terminal['result']
                assert json.loads(rows[0].vocabulary) == terminal['vocabulary']
            second = await client.post('/api/v1/reading/convert/stream', json=request)
            record(evidence, second)
            assert events(second) == [{'cached': True, 'result': terminal['result'], 'vocabulary': terminal['vocabulary'], 'max_new': 3}]
    assert len(calls) == 1
    assert calls[0]['accept'] == 'text/event-stream' and calls[0]['cache_control'] == 'no-store'


@pytest.mark.parametrize('kind', ['stream', 'json'])
async def test_real_old_v3_bad_cache_cannot_bypass_completion(disk, monkeypatch, evidence, kind):
    from tests.test_reading_native_stream_http_flow import endpoints
    legacy = hashlib.md5(json.dumps({'version': 3, 'text': ARTICLE, 'level': 1, 'known': [], 'seen': []},
        ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    async with disk.factory() as db:
        db.add(ReadingConversionCache(cache_key=legacy, result='旧版追加非法', vocabulary='[]'))
        await db.commit()
    async with endpoints(disk, monkeypatch, evidence, 'complete') as net:
        response = await net.client.post('/api/v1/reading/convert' + ('/stream' if kind == 'stream' else ''), json={'text': ARTICLE})
        record(evidence, response)
        final = events(response)[-1] if kind == 'stream' else response.json()
        async with disk.factory() as db:
            rows = (await db.scalars(select(ReadingConversionCache))).all()
            evidence['CacheRows'] = [{'key': r.cache_key, 'result': r.result} for r in rows]
        assert final['result'] == '完整 world' and len(evidence['ProviderRequests']) == 1
        assert len(rows) == 2 and any(r.cache_key == legacy and r.result == '旧版追加非法' for r in rows)


@pytest.mark.parametrize('reason,content', [('length', '半截 partial'), ('content_filter', '半截 partial'),
    ('tool_calls', '半截 partial'), ('stop', '   ')])
async def test_json_bad_provider_cannot_poison_stream_cache(disk, monkeypatch, evidence, reason, content):
    provider = FastAPI()
    calls = []
    @provider.post('/v1/chat/completions')
    async def simulation(request: Request):
        payload = await request.json()
        calls.append(payload)
        if payload.get('stream'):
            return StreamingResponse(iter([body('same-frame-stop')]), media_type='text/event-stream')
        return {'choices': [{'message': {'content': content}, 'finish_reason': reason}]}
    monkeypatch.setattr(reading, 'DEEPSEEK_API_KEY', 'explicit-simulation')
    async with server(provider) as remote:
        monkeypatch.setattr(reading, 'DEEPSEEK_API_URL', str(remote.base_url).rstrip('/') + '/v1/chat/completions')
        async with server(app_for(disk)) as client:
            request = {'text': ARTICLE, 'level': 2}
            response = await client.post('/api/v1/reading/convert', json=request)
            record(evidence, response)
            async with disk.factory() as db:
                rows = (await db.scalars(select(ReadingConversionCache))).all()
                evidence['AfterJSONCache'] = [{'result': r.result} for r in rows]
            assert len(calls) == 1 and response.json()['source'] == 'dictionary', response.text
            assert not rows
            recovered = await client.post('/api/v1/reading/convert/stream', json=request)
            record(evidence, recovered)
            assert events(recovered)[-1]['result'] == '完整 world' and len(calls) == 2
            async with disk.factory() as db:
                rows = (await db.scalars(select(ReadingConversionCache))).all()
                assert len(rows) == 1 and rows[0].result == '完整 world'
