"""Read-only local accounting and separately sourced provider balances."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException
from sqlalchemy import select, func, case
from starlette.concurrency import run_in_threadpool

from app.models import APICallLog, APICache
from app.core.utc_time import utc_iso
from app.core import model_connections
from app.core.crypto import decrypt_api_key


async def usage_snapshot(db, user_id, utc_offset_minutes):
    now = datetime.now(timezone.utc)
    local = now.astimezone(timezone(timedelta(minutes=utc_offset_minutes)))
    start = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).replace(tzinfo=None)
    end = start + timedelta(days=1)
    metered = APICallLog.tokens_input.is_not(None) & APICallLog.tokens_output.is_not(None)
    row = (await db.execute(select(
        func.coalesce(func.sum(APICallLog.tokens_input), 0),
        func.coalesce(func.sum(APICallLog.tokens_output), 0), func.count(),
        func.coalesce(func.sum(case((metered, 0), else_=1)), 0),
    ).where(APICallLog.user_id == user_id, APICallLog.created_at >= start,
            APICallLog.created_at < end, APICallLog.from_cache.is_not(True)))).one()
    incoming, outgoing, calls, unmetered = map(int, row)
    return dict(tokens=incoming + outgoing, input_tokens=incoming, output_tokens=outgoing,
        calls=calls, unmetered_calls=unmetered, day=str(local.date()), utc_offset_minutes=utc_offset_minutes,
        period_start=utc_iso(start), period_end=utc_iso(end), source='local_call_logs', scope='account',
        checked_at=now.isoformat())


async def cache_snapshot(db):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = (await db.execute(select(APICache.created_at, APICache.ttl_days))).all()
    active, expired, invalid = 0, 0, 0
    for created, ttl in rows:
        try:
            if created is None or not isinstance(ttl, int) or ttl < 0:
                raise ValueError('invalid cache lifetime')
            if now <= created + timedelta(days=ttl):
                active += 1
            else:
                expired += 1
        except (ValueError, OverflowError):
            invalid += 1
    return dict(active_entries=active, stored_entries=len(rows), expired_entries=expired,
        invalid_entries=invalid, scope='server', source='cache_database', checked_at=utc_iso(now))


async def account_calls(db, user_id, limit):
    rows = (await db.scalars(select(APICallLog).where(APICallLog.user_id == user_id)
        .order_by(APICallLog.created_at.desc(), APICallLog.id.desc()).limit(limit))).all()
    return [dict(id=row.id, task_type=row.task_type, model_name=row.model_name,
        tokens_input=row.tokens_input, tokens_output=row.tokens_output, from_cache=row.from_cache,
        success=row.success, duration_ms=row.duration_ms, created_at=utc_iso(row.created_at)) for row in rows]


def parse_balances(data):
    if not isinstance(data, dict) or type(data.get('is_available')) is not bool:
        raise ValueError('invalid availability')
    rows = data.get('balance_infos')
    if not isinstance(rows, list) or not 1 <= len(rows) <= 5:
        raise ValueError('missing balance')
    balances = []
    for row in rows:
        if not isinstance(row, dict) or row.get('currency') not in {'CNY', 'USD'}:
            raise ValueError('invalid currency')
        value = row.get('total_balance')
        if not isinstance(value, str) or len(value) > 80:
            raise ValueError('invalid balance')
        amount = Decimal(value)
        if not amount.is_finite() or abs(amount) > Decimal('1e18') or not -30 <= amount.as_tuple().exponent <= 18:
            raise ValueError('invalid balance')
        balances.append(dict(currency=row['currency'], total_balance=format(amount, 'f')))
    return balances


async def provider_balance(db, user_id):
    result = dict(status='unavailable', balances=[], source='provider_api', checked_at=datetime.now(timezone.utc).isoformat())
    selected = await model_connections.default_model(db, user_id)
    if not selected.startswith('connection-'):
        return {**result, 'message': '请先选择个人默认连接，再查询对应服务商余额。'}
    key_id = selected.partition('/')[0][len('connection-'):]
    key, meta = await model_connections.owned(db, key_id, user_id)
    result['connection_name'] = meta['name']
    if not key.is_active:
        return {**result, 'message': '默认连接已停用，请先启用或重新选择。'}
    address = urlsplit(meta['base_url'])
    if (address.scheme != 'https' or address.hostname != 'api.deepseek.com'
            or address.port not in (None, 443) or address.path.rstrip('/') not in ('', '/v1', '/anthropic')):
        return {**result, 'status': 'unsupported', 'message': '此接口暂不支持余额查询，请在对应服务商控制台查看；系统不会推算余额。'}
    plain = await run_in_threadpool(decrypt_api_key, key.key_encrypted)
    stamp = model_connections.network_stamp(key, meta)
    await db.rollback()  # No database transaction is held over the network.
    result['provider'] = 'deepseek'
    try:
        # Only the originally configured official origin is eligible. A gateway
        # labelled DeepSeek must never send its key to a different destination.
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as http:
            response = await http.get('https://api.deepseek.com/user/balance', headers={'Authorization': 'Bearer '+plain})
            response.raise_for_status()
            data = response.json()
            balances = parse_balances(data)
        result.update(status='available', balances=balances, is_available=data['is_available'])
    except (httpx.HTTPError, ValueError, InvalidOperation):
        result.update(status='error', message='服务商余额未能获取，请检查连接或稍后重试；未将失败当作零余额。')
    key, current = await model_connections.owned(db, key_id, user_id)
    if stamp != model_connections.network_stamp(key, current) or await model_connections.default_model(db, user_id) != selected:
        raise HTTPException(409, '查询期间默认连接已变化，请刷新后重查余额')
    result['checked_at'] = datetime.now(timezone.utc).isoformat()
    return result
