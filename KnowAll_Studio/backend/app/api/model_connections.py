"""Signed model configuration API; secrets stay on the server."""
import asyncio
import time
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.auth import get_current_user, load_user_api_keys
from app.database import get_db
from app.core import model_connections as service

class ConfigurationRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()
        async def handler(request):
            try:
                return await original(request)
            except RequestValidationError as error:
                # Validation errors must not echo credentials or pasted URLs.
                return JSONResponse(status_code=422, content={'detail': [
                    {'loc': list(item['loc']), 'msg': item['msg'], 'type': item['type']}
                    for item in error.errors()]})
        return handler


router = APIRouter(prefix='/api/v1/ai', tags=['model-configuration'], route_class=ConfigurationRoute)


@router.get('/usage')
async def usage(utc_offset_minutes: int = Query(0, ge=-840, le=840),
                db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    from app.core.account_metrics import usage_snapshot
    return await usage_snapshot(db, user.id, utc_offset_minutes)


@router.get('/cache/stats')
async def cache_statistics(db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    from app.core.account_metrics import cache_snapshot
    return await cache_snapshot(db)


@router.get('/balance')
async def balance(db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    from app.core.account_metrics import provider_balance
    return await provider_balance(db, user.id)


@router.get('/calls')
async def calls(limit: int = Query(20, ge=1, le=500), db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    from app.core.account_metrics import account_calls
    return await account_calls(db, user.id, limit)


class DefaultInput(BaseModel):
    connection_id: str = Field(min_length=1, max_length=36)
    model: str = Field(min_length=1, max_length=100)

    @field_validator('model')
    @classmethod
    def printable_model(cls, value):
        return service.ConnectionInput.printable(value)


@router.get('/config')
async def configuration(db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    return await service.list_configuration(db, user)


@router.post('/connections', status_code=201)
async def create(req: service.ConnectionInput, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    return await service.save_connection(db, user.id, req)


@router.post('/connections/{key_id}/copy', status_code=201)
async def copy_legacy(key_id: str, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    from app.models import APIKey
    from app.core.crypto import decrypt_api_key
    from starlette.concurrency import run_in_threadpool
    key = await db.get(APIKey, key_id)
    if key is None or key.permission_level == service.TAG or not (key.user_id == user.id or (key.user_id is None and (user.id == 'local_user' or user.is_admin))):
        raise HTTPException(404, '历史配置不存在或无权复制')
    preset = next((p for p in service.PRESETS if p['provider'] == key.provider), None)
    if not preset or not preset['base_url']:
        raise HTTPException(422, '旧配置缺少可恢复的接口地址，请手动添加连接')
    plain = await run_in_threadpool(decrypt_api_key, key.key_encrypted)
    req = service.ConnectionInput(name=(key.key_alias or key.provider)[:85] + '（个人副本）',
        provider=key.provider, protocol=preset['protocol'], base_url=preset['base_url'], api_key=plain)
    return await service.save_connection(db, user.id, req)


@router.put('/connections/{key_id}')
async def update(key_id: str, req: service.ConnectionInput, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    return await service.save_connection(db, user.id, req, key_id)


@router.delete('/connections/{key_id}')
async def remove(key_id: str, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    key, meta = await service.owned(db, key_id, user.id)
    key.is_active = False
    meta['archived'] = True
    await service.write_value(db, service.PREFIX + key.id, meta)
    # Keep a blocked default reference until the user selects another connection.
    # Deletion must never silently switch billing to a server/environment key.
    await db.commit()
    await load_user_api_keys(user.id, db, True)
    return {'status': 'removed'}


@router.put('/default')
async def choose_default(req: DefaultInput, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    key, meta = await service.owned(db, req.connection_id, user.id)
    if not key.is_active:
        raise HTTPException(422, '请先启用该连接')
    values = service.ConnectionInput(name=meta['name'], provider=key.provider, protocol=meta['protocol'],
                                     base_url=meta['base_url'], model=req.model)
    if meta.get('model') != values.model:
        meta['last_test'] = None
    meta['model'] = values.model
    selected = service.reference(key.id, values.model)
    await service.write_value(db, service.PREFIX + key.id, meta)
    await service.write_value(db, service.DEFAULT_PREFIX + user.id, {'model': selected})
    await db.commit()
    await load_user_api_keys(user.id, db, True)
    return {'model': selected}


async def active_connection(db, user, key_id):
    key, meta = await service.owned(db, key_id, user.id)
    if not key.is_active:
        raise HTTPException(422, '连接已停用，请先启用')
    await load_user_api_keys(user.id, db, True)
    from app.core.api_scheduler import api_client
    adapter = api_client.get_agent_adapter('connection-' + key.id, user.id)
    return key, meta, adapter


@router.post('/connections/{key_id}/models')
async def fetch_models(key_id: str, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    user_id = user.id
    key, meta, _ = await active_connection(db, user, key_id)
    stamp = service.network_stamp(key, meta)
    await db.rollback()
    from app.core.api_scheduler import api_client
    from app.core.model_catalog import discover_models
    try:
        async with asyncio.timeout(25):
            result = await discover_models(api_client, user_id, providers=['connection-' + key_id])
    except TimeoutError:
        return {'models': [], 'errors': [{'message': '模型列表查询超时，可以稍后重试或手动填写模型 ID。'}], 'fetched_at': None}
    if not result['errors']:
        await service.finish_network(db, key_id, user_id, stamp, {'models': result['models'], 'fetched_at': result['fetched_at']})
    return result


@router.post('/connections/{key_id}/test')
async def test_connection(key_id: str, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    user_id = user.id
    key, meta, adapter = await active_connection(db, user, key_id)
    if not meta.get('model'):
        raise HTTPException(422, '请先选择或填写模型 ID 并保存')
    stamp = service.network_stamp(key, meta)
    await db.rollback()
    from app.core.api_scheduler.adapters.base import AdapterConfig
    started = time.monotonic()
    result = {'checked_at': datetime.now(timezone.utc).isoformat(), 'model': meta['model']}
    try:
        async with asyncio.timeout(25):
            response = await adapter.chat_completion([{'role': 'user', 'content': 'Reply with OK only.'}], AdapterConfig(max_tokens=128, timeout=20, temperature=0))
        if not response.content or not response.content.strip():
            raise ValueError('empty completion')
        result.update(status='success', message='模型已返回有效文本，连接和生成测试通过。', latency_ms=int((time.monotonic()-started)*1000))
    except Exception as error:
        detail = str(error)
        reason = next((label for marker, label in [('401', '密钥无效或已失效'), ('402', '服务余额或支付状态不足'), ('403', '该密钥无权使用所选模型'), ('404', '模型 ID 或接口地址不存在'), ('429', '服务限流，请稍后再试')] if marker in detail), '无法完成生成，请核对地址、协议、模型及网络后重试')
        result.update(status='failed', message=reason, latency_ms=int((time.monotonic()-started)*1000))
    await service.finish_network(db, key_id, user_id, stamp, {'last_test': result})
    return result
