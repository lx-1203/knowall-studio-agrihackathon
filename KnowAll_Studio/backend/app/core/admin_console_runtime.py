"""Apply persisted administrator settings without making model/network requests."""
import json
import logging
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool
from app.config import settings
from app.core.api_scheduler import api_client
from app.core.crypto import decrypt_api_key
from app.models import APIKey
from app.models.system_config import SystemConfig

logger = logging.getLogger(__name__)
PROVIDERS = {
    "openai": ("https://api.openai.com/v1", {"openai": "gpt-4o", "gpt-4o": "gpt-4o", "gpt-4o-mini": "gpt-4o-mini"}),
    "anthropic": ("https://api.anthropic.com/v1", {"anthropic": "claude-sonnet-4-6", "claude-opus-4-6": "claude-opus-4-6", "claude-sonnet-4-6": "claude-sonnet-4-6"}),
    "deepseek": ("https://api.deepseek.com/v1", {"deepseek": "deepseek-chat", "deepseek-chat": "deepseek-chat", "deepseek-reasoner": "deepseek-reasoner"}),
    "qwen": ("https://dashscope.aliyuncs.com/compatible-mode/v1", {"qwen": "qwen-turbo", "qwen-turbo": "qwen-turbo", "qwen-plus": "qwen-plus", "qwen-max": "qwen-max"}),
    "zhipu": ("https://open.bigmodel.cn/api/paas/v4", {"zhipu": "glm-4", "glm-4": "glm-4", "glm-4-flash": "glm-4-flash"}),
    "ollama": ("http://localhost:11434/v1", {"ollama": "qwen2.5:7b"}),
}


async def sync_provider_keys(db, provider: str, owner_user_id: str | None = None):
    """Newest enabled stored key wins per provider/owner; other owners are untouched."""
    if provider not in PROVIDERS:
        raise ValueError("不支持的模型服务商")
    base_url, aliases = PROVIDERS[provider]
    active = await db.scalar(select(APIKey).where(
        APIKey.permission_level != "model_connection", APIKey.provider == provider, APIKey.user_id == owner_user_id, APIKey.is_active.is_(True)
    ).order_by(APIKey.created_at.desc(), APIKey.id.desc()).limit(1))
    # Decrypt before updating the registry so corrupt storage cannot partially clear it.
    plain = await run_in_threadpool(decrypt_api_key, active.key_encrypted) if active else None
    suffix = f"_{owner_user_id}" if owner_user_id else ""
    for alias in aliases:
        registration = alias + suffix
        if api_client._adapter_owners.get(registration) == owner_user_id:
            api_client._adapters.pop(registration, None)
            api_client._adapter_configs.pop(registration, None)
            api_client._adapter_owners.pop(registration, None)
    if plain:
        for alias, model in aliases.items():
            api_client.configure_adapter(alias + suffix, plain, base_url=base_url,
                                         model_name=model, owner_user_id=owner_user_id)


async def apply_model_config(db):
    row = await db.scalar(select(SystemConfig).where(SystemConfig.key == "model_config"))
    if row is None:
        api_client.set_default_configuration(settings.default_model, [])
        return
    value = json.loads(row.value)
    if not isinstance(value, dict):
        raise ValueError("模型配置记录必须是对象")
    model, fallbacks, limit = value.get("model"), value.get("fallback_models", []), value.get("daily_token_limit")
    if (not isinstance(model, str) or not model.strip() or len(model) > 100
            or not isinstance(fallbacks, list) or len(fallbacks) > 20
            or any(not isinstance(item, str) or not item.strip() or len(item) > 100 for item in fallbacks)
            or isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 2147483647):
        raise ValueError("模型配置记录格式无效")
    api_client.set_default_configuration(model.strip(), [item.strip() for item in fallbacks])
    settings.daily_token_limit = limit


async def restore_console_runtime(db):
    """Called after environment setup, including disabled providers to remove stale aliases."""
    try:
        await apply_model_config(db)
    except (ValueError, TypeError):
        logger.warning("Invalid saved model configuration; using environment defaults")
        api_client.set_default_configuration(settings.default_model, [])
    providers = (await db.scalars(select(APIKey.provider).where(APIKey.user_id.is_(None)).distinct())).all()
    for provider in providers:
        if provider not in PROVIDERS:
            continue
        try:
            await sync_provider_keys(db, provider)
        except Exception:
            logger.warning("Stored console key could not be restored for provider %s", provider)


def scheduler_status():
    return {"status": "ok" if api_client._adapters else "unconfigured",
            "configured_adapters": len(api_client._adapters),
            "default_model": api_client.get_default_configuration().model}
