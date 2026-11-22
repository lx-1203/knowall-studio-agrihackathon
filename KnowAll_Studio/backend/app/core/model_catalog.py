"""Fetch model IDs from effective configured providers; never claim tool support."""
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx

from app.core.api_scheduler.adapters.anthropic import AnthropicAdapter


async def discover_models(api_client, user_id, *, http=None, providers=None):
    if http is None:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as session:
            return await discover_models(api_client, user_id, http=session, providers=providers)
    providers = providers if providers is not None else sorted({m['value'].split('/', 1)[0] for m in api_client.get_agent_models(user_id)})
    models, errors = [], []
    for provider in providers:
        adapter = api_client.get_agent_adapter(provider, user_id)
        base = adapter.base_url.rstrip('/')
        headers = {'Authorization': f'Bearer {adapter.api_key}'}
        if isinstance(adapter, AnthropicAdapter):
            headers.update({'x-api-key': adapter.api_key, 'anthropic-version': '2023-06-01'})
        # DeepSeek exposes its catalogue on the OpenAI-compatible root even
        # when generation uses the separate Anthropic-compatible endpoint.
        if urlsplit(base).hostname == 'api.deepseek.com' and base.endswith('/anthropic'):
            base = base[:-len('/anthropic')]
        found = {}
        try:
            params = {}
            for _ in range(10):
                response = await http.get(base + '/models', headers=headers, params=params, timeout=15)
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, dict) or not isinstance(data.get('data'), list):
                    raise ValueError('invalid catalogue')
                for item in data['data']:
                    name = item.get('id') if isinstance(item, dict) else None
                    if not isinstance(name, str) or not name.strip() or len(name) > 100 or not name.isprintable() or any(c.isspace() for c in name):
                        continue
                    display = item.get('name') or item.get('display_name') or name
                    if not isinstance(display, str) or not display.isprintable() or len(display) > 200:
                        display = name
                    value = provider + '/' + name
                    title = display + f' ({name})' if display != name else name
                    found[value] = {'value': value, 'label': provider + ' / ' + title,
                                    'display_name': display, 'model_id': name,
                                    'endpoint_host': urlsplit(base).hostname,
                                    'model_tool_support': 'unverified'}
                if not data.get('has_more'):
                    break
                cursor = data.get('last_id')
                if not isinstance(cursor, str) or not cursor or cursor == params.get('after_id'):
                    raise ValueError('invalid pagination')
                params = {'after_id': cursor}
            else:
                raise ValueError('catalogue exceeds pagination limit')
            if not found:
                raise ValueError('empty catalogue')
            choices = [found[key] for key in sorted(found)]
            api_client._discovered_models[(user_id, provider)] = (api_client._adapter_cache_identity(adapter), choices)
            models.extend(choices)
        except (httpx.HTTPError, ValueError):
            api_client._discovered_models.pop((user_id, provider), None)
            errors.append({'provider': provider, 'message': '模型列表获取失败，请检查密钥、服务地址和网络；也可手动填写模型 ID。'})
    return {'models': models, 'source': 'provider', 'fetched_at': datetime.now(timezone.utc).isoformat(), 'errors': errors}
