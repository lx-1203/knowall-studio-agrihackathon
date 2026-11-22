"""Account metrics use real disposable SQL; provider balance explicitly simulated."""
from datetime import datetime, timedelta, timezone
import httpx
import pytest
from app.models import APICallLog, APICache, APIQuota
from tests.test_model_connections import connections, save


async def test_usage_is_account_scoped_local_day_ledger_not_quota_counter(connections):
    c = connections
    utc_now = datetime.now(timezone.utc).replace(tzinfo=None)
    local = utc_now + timedelta(hours=8)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=8)
    async with c.factory() as db:
        def log(id, **extra):
            return APICallLog(id=id, user_id='alice', task_type='chat', model_name='test-model',
                created_at=start+timedelta(seconds=1), tokens_input=10, tokens_output=20, **extra)
        db.add_all([
            log('success'), log('failed', success=False),
            APICallLog(id='missing', user_id='alice', task_type='chat', model_name='test-model', created_at=start+timedelta(seconds=2)),
            APICallLog(id='before', user_id='alice', task_type='chat', model_name='test-model', created_at=start-timedelta(microseconds=1), tokens_input=1000, tokens_output=2000),
            APICallLog(id='other', user_id='bob', task_type='chat', model_name='test-model', created_at=start, tokens_input=1000, tokens_output=2000),
            log('cached', from_cache=True),
            APIQuota(user_id='alice', daily_limit=1000000, used_today=999999, reset_at=local.date()),
        ])
        await db.commit()
    response = await c.http.get('/api/v1/ai/usage?utc_offset_minutes=480', headers=c.alice)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['tokens'] == 60 and data['calls'] == 3 and data['unmetered_calls'] == 1
    assert data['scope'] == 'account' and data['source'] == 'local_call_logs'
    assert data['day'] == str(local.date()) and data['utc_offset_minutes'] == 480
    assert 'remaining' not in data and 'balance' not in data and '999999' not in response.text
    assert (await c.http.get('/api/v1/ai/usage')).status_code == 401
    assert (await c.http.get('/api/v1/ai/usage?utc_offset_minutes=1000', headers=c.alice)).status_code == 422
    logs = await c.http.get('/api/v1/ai/calls', headers=c.bob)
    assert logs.status_code == 200 and [r['id'] for r in logs.json()] == ['other']


async def test_cache_counts_only_usable_rows_and_labels_server_scope(connections):
    c = connections; now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with c.factory() as db:
        db.add_all([APICache(cache_key='live', response_content='private-do-not-return', model_used='x', created_at=now, ttl_days=30),
            APICache(cache_key='expired', response_content='private-do-not-return', model_used='x', created_at=now-timedelta(days=2), ttl_days=1)])
        await db.commit()
    response = await c.http.get('/api/v1/ai/cache/stats', headers=c.alice)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['scope'] == 'server' and data['source'] == 'cache_database'
    assert data['active_entries'] == 1 and data['stored_entries'] == 2 and data['expired_entries'] == 1
    assert 'private-do-not-return' not in response.text


async def selected_deepseek(c):
    row = await save(c, name='我的 DeepSeek', base_url='https://api.deepseek.com/v1', provider='deepseek', model='deepseek-flash')
    result = await c.http.put('/api/v1/ai/default', headers=c.alice, json={'connection_id':row['id'], 'model':row['model']})
    assert result.status_code == 200
    return row


@pytest.mark.parametrize('amount,available', [('8.9000', True), ('0.0000', False)])
async def test_balance_reads_selected_personal_key_and_preserves_real_zero(connections, monkeypatch, amount, available):
    c = connections; await selected_deepseek(c)
    calls = []
    def upstream(request):
        assert str(request.url) == 'https://api.deepseek.com/user/balance'
        assert request.headers['authorization'] == 'Bearer synthetic-private-key'
        calls.append(request.url.path)
        return httpx.Response(200, json={'is_available':available,'balance_infos':[{'currency':'CNY','total_balance':amount,'granted_balance':'0','topped_up_balance':amount}]})
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(upstream), **kw))
    response = await c.http.get('/api/v1/ai/balance', headers=c.alice)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['status'] == 'available' and data['balances'][0]['total_balance'] == amount
    assert data['is_available'] is available and data['connection_name'] == '我的 DeepSeek'
    assert 'synthetic-private-key' not in response.text and calls == ['/user/balance']
    other = await c.http.get('/api/v1/ai/balance', headers=c.bob)
    assert other.json()['status'] == 'unavailable' and len(calls) == 1


@pytest.mark.parametrize('status,payload', [(401, {'error':'synthetic-private-key'}), (200, {'is_available':True,'balance_infos':[]}),
    (200, {'is_available':True,'balance_infos':[{'currency':'CNY','total_balance':'NaN'}]}), (302, {})])
async def test_failed_balance_never_fabricates_zero_or_echoes_secret(connections, monkeypatch, status, payload):
    c = connections; await selected_deepseek(c)
    original = httpx.AsyncClient
    def upstream(request):
        return httpx.Response(status, json=payload, headers={'location':'https://other.invalid/secret'})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(upstream), **kw))
    response = await c.http.get('/api/v1/ai/balance', headers=c.alice)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['status'] == 'error' and data['balances'] == []
    assert 'synthetic-private-key' not in response.text and 'other.invalid' not in response.text


async def test_gateway_label_does_not_authorize_sending_key_to_official_balance_api(connections, monkeypatch):
    c = connections
    row = await save(c, provider='deepseek') # Actual destination remains gateway.test
    await c.http.put('/api/v1/ai/default', headers=c.alice, json={'connection_id':row['id'],'model':row['model']})
    def forbidden(**kwargs):
        raise AssertionError('unsupported gateway must not trigger any external request')
    monkeypatch.setattr(httpx, 'AsyncClient', forbidden)
    data = (await c.http.get('/api/v1/ai/balance', headers=c.alice)).json()
    assert data['status'] == 'unsupported' and data['balances'] == []


@pytest.mark.parametrize('amount', ['NaN', 'Infinity', '0e9999999', '1e-9999999'])
def test_balance_validation_rejects_unusable_numeric_formats(amount):
    from app.core.account_metrics import parse_balances
    with pytest.raises(ValueError):
        parse_balances({'is_available':True,'balance_infos':[{'currency':'CNY','total_balance':amount}]})
