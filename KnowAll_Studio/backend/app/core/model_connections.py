"""Account-owned endpoints, using existing AES keys and SystemConfig storage.

Design reference: LibreChat custom endpoints (MIT). No upstream source copied.
"""
import json
import uuid
from urllib.parse import urlsplit
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, or_
from starlette.concurrency import run_in_threadpool
from app.models import APIKey
from app.models.system_config import SystemConfig
from app.core.crypto import encrypt_api_key, decrypt_api_key

TAG, PREFIX, DEFAULT_PREFIX = 'model_connection', 'ai_connection:', 'ai_default:'
PRESETS = [
    dict(provider='custom', name='自定义 / 中转服务', base_url='', protocol='openai'),
    dict(provider='deepseek', name='DeepSeek', base_url='https://api.deepseek.com/v1', protocol='openai'),
    dict(provider='openai', name='OpenAI', base_url='https://api.openai.com/v1', protocol='openai'),
    dict(provider='anthropic', name='Anthropic / Claude', base_url='https://api.anthropic.com/v1', protocol='anthropic'),
    dict(provider='qwen', name='通义千问', base_url='https://dashscope.aliyuncs.com/compatible-mode/v1', protocol='openai'),
    dict(provider='zhipu', name='智谱', base_url='https://open.bigmodel.cn/api/paas/v4', protocol='openai'),
    dict(provider='moonshot', name='Moonshot / Kimi', base_url='https://api.moonshot.cn/v1', protocol='openai'),
    dict(provider='openrouter', name='OpenRouter', base_url='https://openrouter.ai/api/v1', protocol='openai'),
    dict(provider='siliconflow', name='硅基流动', base_url='https://api.siliconflow.cn/v1', protocol='openai'),
    dict(provider='ollama', name='Ollama（本机）', base_url='http://localhost:11434/v1', protocol='openai'),
]


class ConnectionInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    provider: str = Field(default='custom', min_length=1, max_length=50, pattern=r'^[a-z0-9-]+$')
    protocol: str = Field(default='openai', pattern=r'^(openai|anthropic)$')
    base_url: str = Field(min_length=1, max_length=500)
    api_key: str = Field(default='', max_length=256)
    model: str = Field(default='', max_length=100)
    is_active: bool = True

    @field_validator('base_url')
    @classmethod
    def valid_url(cls, value):
        value = value.rstrip('/')
        p = urlsplit(value)
        try:
            p.port
        except ValueError:
            raise ValueError('接口地址的端口格式不正确') from None
        local = p.hostname in {'localhost', '127.0.0.1', '::1'}
        if (not p.hostname or p.username or p.password or p.query or p.fragment
                or p.scheme not in {'http', 'https'} or (p.scheme == 'http' and not local)):
            raise ValueError('请填写不含账号、密钥或查询参数的 HTTPS 基础地址；本机服务可使用 HTTP')
        if p.path.endswith(('/chat/completions', '/messages', '/models')):
            raise ValueError('请填写基础地址，例如 https://服务地址/v1，不要包含 /chat/completions、/messages 或 /models')
        return value

    @field_validator('api_key', 'model')
    @classmethod
    def printable(cls, value):
        if value and (not value.isprintable() or any(c.isspace() for c in value)):
            raise ValueError('密钥和模型 ID 不能包含空白或控制字符')
        return value


def reference(key_id, model):
    return 'connection-' + key_id + '/' + model if model else ''


async def write_value(db, key, value):
    row = await db.get(SystemConfig, key)
    encoded = json.dumps(value, ensure_ascii=False)
    if row is None:
        db.add(SystemConfig(key=key, value=encoded))
    else:
        row.value = encoded


async def metadata(db, keys):
    ids = [PREFIX + k.id for k in keys if k.permission_level == TAG]
    if not ids:
        return {}
    rows = (await db.scalars(select(SystemConfig).where(SystemConfig.key.in_(ids)).execution_options(populate_existing=True))).all()
    return {row.key[len(PREFIX):]: json.loads(row.value) for row in rows}


async def owned(db, key_id, user_id):
    key = await db.scalar(select(APIKey).where(APIKey.id == key_id, APIKey.user_id == user_id,
                                             APIKey.permission_level == TAG).execution_options(populate_existing=True))
    if key is None:
        raise HTTPException(404, '连接不存在或当前账号无权操作')
    meta = (await metadata(db, [key])).get(key.id)
    if meta is None or meta.get('archived'):
        raise HTTPException(404, '连接不存在')
    return key, meta


def public_connection(key, meta):
    return dict(id=key.id, name=meta['name'], provider=key.provider, protocol=meta['protocol'],
                base_url=meta['base_url'], model=meta.get('model', ''), model_ref=reference(key.id, meta.get('model', '')),
                is_active=bool(key.is_active), has_key=bool(key.key_encrypted),
                key_masked='已保存（加密）' if key.key_encrypted else '无需密钥',
                last_test=meta.get('last_test'), models=meta.get('models', []), fetched_at=meta.get('fetched_at'))


async def default_model(db, user_id):
    row = await db.get(SystemConfig, DEFAULT_PREFIX + user_id, populate_existing=True)
    return json.loads(row.value).get('model', '') if row else ''


async def effective_default(db, user_id):
    """Preserve an existing configured default until a personal one is chosen."""
    personal = await default_model(db, user_id)
    if personal:
        return personal, 'personal'
    from app.core.api_scheduler import api_client
    configured = api_client.get_default_configuration().model
    for item in api_client.get_agent_models(user_id):
        if item['value'].startswith('connection-'):
            continue
        if item['value'] == configured or item['value'].partition('/')[2] == configured:
            return item['value'], 'legacy'
    return '', 'none'


async def choose_request_model(req, user_id, db):
    if 'model' not in req.model_fields_set:
        selected, _ = await effective_default(db, user_id)
        if selected:
            req.model = selected
    return req


async def save_connection(db, user_id, req, key_id=None):
    if key_id:
        key, previous = await owned(db, key_id, user_id)
        if req.base_url != previous['base_url'] and not req.api_key and key.key_encrypted:
            raise HTTPException(422, '修改接口地址时请重新输入密钥，避免把旧密钥发送到其他服务')
    else:
        key = APIKey(id=str(uuid.uuid4()), user_id=user_id, provider=req.provider, permission_level=TAG)
        previous = {}
        db.add(key)
    if not req.api_key and not getattr(key, 'key_encrypted', None) and req.provider != 'ollama':
        raise HTTPException(422, '首次保存请填写 API Key')
    if req.api_key:
        key.key_encrypted = await run_in_threadpool(encrypt_api_key, req.api_key)
    elif not key.key_encrypted:
        key.key_encrypted = ''
    key.provider, key.key_alias, key.is_active = req.provider, req.name, req.is_active
    changed = req.api_key or any(previous.get(f) != getattr(req, f) for f in ('base_url', 'protocol'))
    meta = {**previous, 'name': req.name, 'base_url': req.base_url, 'protocol': req.protocol, 'model': req.model}
    if changed:
        meta.update(last_test=None, models=[], fetched_at=None)
    if req.model != previous.get('model'):
        meta['last_test'] = None
    await write_value(db, PREFIX + key.id, meta)
    if (await default_model(db, user_id)).startswith('connection-' + key.id + '/') and req.model:
        await write_value(db, DEFAULT_PREFIX + user_id, {'model': reference(key.id, req.model)})
    await db.commit()
    from app.core.auth import load_user_api_keys
    await load_user_api_keys(user_id, db, True)
    return public_connection(key, meta)


async def registrations(db, keys):
    configs = await metadata(db, keys)
    result = []
    for key in keys:
        if key.permission_level != TAG:
            continue
        meta = configs.get(key.id)
        if not meta or meta.get('archived'):
            continue
        plain = await run_in_threadpool(decrypt_api_key, key.key_encrypted) if key.key_encrypted else ''
        result.append(('connection-' + key.id + '_' + key.user_id, plain, meta['base_url'], meta.get('model', ''), meta['protocol']))
    return result


def network_stamp(key, meta):
    return (key.key_encrypted, bool(key.is_active), meta['base_url'], meta['protocol'], meta.get('model', ''))


async def finish_network(db, key_id, user_id, stamp, values):
    key, current = await owned(db, key_id, user_id)
    if network_stamp(key, current) != stamp:
        raise HTTPException(409, '检测期间连接配置已变化，结果未保存，请使用新配置重新检测')
    current.update(values)
    await write_value(db, PREFIX + key_id, current)
    await db.commit()


async def list_configuration(db, user):
    keys = (await db.scalars(select(APIKey).where(APIKey.user_id == user.id,
                            APIKey.permission_level == TAG).order_by(APIKey.created_at.desc()))).all()
    configs = await metadata(db, keys)
    connections = [public_connection(k, configs[k.id]) for k in keys if k.id in configs and not configs[k.id].get('archived')]
    from app.core.api_scheduler import api_client
    from app.core.auth import load_user_api_keys
    await load_user_api_keys(user.id, db, True)
    from app.core.admin_console_runtime import PROVIDERS
    query = select(APIKey).where(APIKey.permission_level != TAG)
    if user.id == 'local_user' or user.is_admin:
        query = query.where(or_(APIKey.user_id.is_(None), APIKey.user_id == user.id))
    else:
        query = query.where(APIKey.user_id == user.id)
    legacy = [dict(id=k.id, name=k.key_alias or k.provider, provider=k.provider,
                   base_url=PROVIDERS.get(k.provider, ('', {}))[0], is_active=bool(k.is_active),
                   scope='server' if k.user_id is None else 'legacy') for k in (await db.scalars(query.order_by(APIKey.created_at.desc(), APIKey.id.desc()))).all()]
    server, seen = [], set()
    for name, adapter in api_client._adapters.items():
        if api_client._adapter_owners.get(name) is not None or not api_client._is_agent_public_provider(name, user.id):
            continue
        signature = (adapter.base_url, type(adapter).__name__)
        if signature in seen:
            continue
        seen.add(signature)
        address = urlsplit(adapter.base_url)
        host = '[' + address.hostname + ']' if ':' in address.hostname else address.hostname
        if address.port:
            host += ':' + str(address.port)
        safe_url = address._replace(netloc=host, query='', fragment='').geturl()
        server.append(dict(base_url=safe_url, model=adapter.model_name,
                           protocol='anthropic' if 'Anthropic' in type(adapter).__name__ else 'openai'))
    selected, source = await effective_default(db, user.id)
    return dict(connections=connections, legacy_keys=legacy, server_connections=server,
                default_model=selected, default_source=source, presets=PRESETS)
