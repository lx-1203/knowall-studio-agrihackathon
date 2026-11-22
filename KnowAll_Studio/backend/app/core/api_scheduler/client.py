"""Unified API Client - the single entry point for all LLM calls"""
import asyncio
import time
import re
import contextvars
import hashlib
import json
import copy
from typing import Callable
from enum import Enum
from dataclasses import dataclass, field
from .adapters.base import BaseModelAdapter, AdapterConfig, InvalidResponseError
from .adapters.openai import OpenAICompatAdapter, DeepSeekAdapter, OllamaAdapter
from .adapters.anthropic import AnthropicAdapter
from .cache import cache_manager
from .rate_limiter import rate_limiter

# Context variable to carry the current user's ID through the request lifecycle.
# Set by load_user_keys_for_request() in route handlers.
_current_user_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "api_current_user_id", default="local_user"
)


def set_current_user_id(user_id: str) -> None:
    """Set the current user ID for this request context."""
    _current_user_id.set(user_id)


def get_current_user_id() -> str:
    """Get the current user ID from the request context."""
    return _current_user_id.get()


class TaskType(str, Enum):
    KNOWLEDGE_TREE = "knowledge_tree"
    QUIZ_GEN = "quiz_gen"
    FLASHCARD_GEN = "flashcard_gen"
    GAME_GEN = "game_gen"
    OUTLINE_GEN = "outline_gen"
    CHAT = "chat"
    VARIANT_QUESTION = "variant_question"


@dataclass
class GenerationConfig:
    model: str = "claude-opus-4-6"
    fallback_models: list[str] = field(default_factory=list)
    temperature: float = 0.7
    max_tokens: int = 4096
    top_p: float = 1.0
    cache_ttl_days: int = 30
    max_retries: int = 2
    timeout: int = 120
    response_validator: Callable[[str], bool] | None = None


@dataclass
class RequestContext:
    user_id: str = "local_user"
    project_id: str = "default"
    api_key_level: str = "personal"  # teacher/student/personal


@dataclass
class GenerationResult:
    content: str
    raw_content: str
    model_used: str
    tokens_input: int
    tokens_output: int
    from_cache: bool
    cost_estimate: float
    duration_ms: int


class UnifiedAPIClient:
    """Unified entry point for all AI generation requests.

    Handles: caching, rate limiting, model routing, retry, response normalization.
    """

    def __init__(self):
        self._default_configuration = GenerationConfig()
        self._adapters: dict[str, BaseModelAdapter] = {}
        self._adapter_configs: dict[str, dict] = {}
        self._adapter_owners: dict[str, str | None] = {}
        self._discovered_models: dict = {}
        self._native_locks: dict[str, tuple[asyncio.Lock, int]] = {}
        # Per client/event loop, keyed by the full owned request identity.
        self._generation_locks: dict[str, tuple[asyncio.Lock, int]] = {}

    def set_default_configuration(self, model: str, fallback_models: list[str]):
        """Apply defaults only to requests which do not pass their own configuration."""
        self._default_configuration = GenerationConfig(model=model, fallback_models=list(fallback_models))

    def get_default_configuration(self) -> GenerationConfig:
        return copy.deepcopy(self._default_configuration)

    def configure_adapter(
        self,
        provider: str,
        api_key: str,
        base_url: str | None = None,
        model_name: str | None = None,
        owner_user_id: str | None = None,
        protocol: str | None = None,
    ):
        """Register an API adapter with credentials."""
        # Account-scoped registrations and known model aliases retain their
        # provider protocol; the registration key itself is not a model name.
        known_providers = ("openai", "gpt-4o", "gpt-4o-mini", "anthropic",
                           "claude-opus-4-6", "claude-sonnet-4-6", "deepseek",
                           "deepseek-chat", "deepseek-reasoner", "ollama")
        provider_kind = next((name for name in known_providers
                              if provider == name or provider.startswith(name + "_")), provider)
        if protocol is not None:
            provider_kind = protocol
        if provider_kind in ("openai", "gpt-4o", "gpt-4o-mini"):
            adapter = OpenAICompatAdapter(
                api_key=api_key,
                base_url=base_url or "https://api.openai.com/v1",
                model_name=model_name or "gpt-4o",
            )
        elif provider_kind in ("anthropic", "claude-opus-4-6", "claude-sonnet-4-6"):
            adapter = AnthropicAdapter(
                api_key=api_key,
                base_url=base_url or "https://api.anthropic.com/v1",
                model_name=model_name or (provider_kind if provider_kind.startswith("claude-") else "claude-sonnet-4-6"),
            )
        elif provider_kind in ("deepseek", "deepseek-chat", "deepseek-reasoner"):
            selected_model = model_name or ("deepseek-chat" if provider_kind == "deepseek" else provider_kind)
            if base_url and base_url.rstrip("/").endswith("/anthropic"):
                adapter = AnthropicAdapter(api_key=api_key, base_url=base_url, model_name=selected_model)
            else:
                adapter = DeepSeekAdapter(api_key=api_key)
                adapter.base_url = base_url or adapter.base_url
                adapter.model_name = selected_model
        elif provider_kind == "ollama":
            adapter = OllamaAdapter(
                model_name=model_name or "qwen2.5:7b",
                base_url=base_url or "http://localhost:11434/v1",
            )
        else:
            # Generic OpenAI-compatible
            adapter = OpenAICompatAdapter(
                api_key=api_key,
                base_url=base_url or "https://api.openai.com/v1",
                model_name=model_name or provider,
            )
        adapter.provider = provider
        if provider.startswith('connection-') and not model_name:
            adapter.model_name = ''
        self._adapters[provider] = adapter
        self._adapter_owners[provider] = owner_user_id
        self._adapter_configs[provider] = {
            "api_key": api_key,
            "base_url": base_url,
            "model_name": model_name,
        }

    def get_adapter(self, model_ref: str, user_id: str | None = None) -> BaseModelAdapter:
        """Get adapter by model reference. Supports 'provider/model_name' format.
        Falls back to any configured adapter if the exact provider isn't found.

        When user_id is provided and not "local_user", tries a user-scoped
        adapter first (keyed as 'provider_userid'), then falls back to global.
        """
        # Fall back to contextvar if no explicit user_id
        if not user_id or user_id == "local_user":
            ctxv = _current_user_id.get()
            if ctxv != "local_user":
                user_id = ctxv

        if model_ref.startswith('connection-'):
            return self.get_agent_adapter(model_ref, user_id or 'local_user')

        model_name = None
        if "/" in model_ref:
            provider, model_name = model_ref.split("/", 1)
        else:
            provider = model_ref

        # Try user-scoped adapter first
        if user_id and user_id != "local_user":
            user_provider = f"{provider}_{user_id}"
            if user_provider in self._adapters:
                return self._bind_model(self._adapters[user_provider], model_name)

        if provider not in self._adapters:
            # Try environment-configured key
            import os
            env_key_map = {
                "deepseek": "DEEPSEEK_API_KEY",
                "openai": "OPENAI_API_KEY",
                "gpt-4o": "OPENAI_API_KEY",
                "gpt-4o-mini": "OPENAI_API_KEY",
            }
            env_var = env_key_map.get(provider)
            if env_var and os.getenv(env_var):
                self.configure_adapter(provider, os.getenv(env_var))
            elif any(self._adapter_owners.get(name) in (None, user_id)
                     and not name.startswith('connection-') for name in self._adapters):
                # Fall back to first configured adapter (common case: single API key)
                fallback = next(name for name in self._adapters
                                if self._adapter_owners.get(name) in (None, user_id)
                                and not name.startswith('connection-'))
                logger = __import__("logging").getLogger("knowall")
                logger.info("Using fallback adapter '%s' for model '%s'", fallback, model_ref)
                return self._bind_model(self._adapters[fallback], model_name)
            else:
                raise ValueError(
                    f"No adapter configured for '{provider}'. "
                    f"Call configure_adapter() first or set environment variable."
                )

        return self._bind_model(self._adapters[provider], model_name)

    @staticmethod
    def _bind_model(adapter: BaseModelAdapter, model_name: str | None) -> BaseModelAdapter:
        """Bind an explicit model to a request without mutating shared adapters."""
        if model_name is None or not hasattr(adapter, "model_name"):
            return adapter
        bound = copy.copy(adapter)
        bound.model_name = model_name
        return bound

    @staticmethod
    def _adapter_cache_identity(adapter: BaseModelAdapter) -> list[str]:
        """Identify the effective provider without persisting its credential."""
        return [
            type(adapter).__module__ + "." + type(adapter).__qualname__,
            getattr(adapter, "provider", ""),
            (getattr(adapter, "base_url", "") or "").rstrip("/"),
            getattr(adapter, "model_name", "") or "",
            hashlib.sha256((getattr(adapter, "api_key", "") or "").encode("utf-8")).hexdigest(),
        ]

    @staticmethod
    def _response_is_valid(content: str, config: GenerationConfig) -> bool:
        if not isinstance(content, str) or not content.strip():
            return False
        if config.response_validator is None:
            return True
        try:
            return bool(config.response_validator(content))
        except Exception:
            return False

    async def generate(
        self,
        task_type: TaskType,
        messages: list[dict[str, str]],
        prompt_template_id: str,
        generation_content: str,
        config: GenerationConfig | None = None,
        context: RequestContext | None = None,
    ) -> GenerationResult:
        """Main generation entry point.

        Args:
            task_type: Type of generation task
            messages: Full chat messages (system + user)
            prompt_template_id: Identifier for the prompt template
            generation_content: The actual content text (for cache key computation)
            config: Generation configuration
            context: User/request context
        """
        # Queued callers must keep the same input/ownership that built their key.
        config = copy.copy(config if config is not None else self.get_default_configuration())
        config.fallback_models = list(config.fallback_models)
        context = copy.copy(context or RequestContext())
        messages = copy.deepcopy(messages)

        # If no explicit user_id, fall back to contextvar (set by route handler)
        if context.user_id == "local_user":
            ctxv_user_id = _current_user_id.get()
            if ctxv_user_id != "local_user":
                context.user_id = ctxv_user_id

        cache_hit = False

        # Key and completion must share one configuration snapshot. Registering
        # an updated provider while cache/quota awaits must not write its output
        # under a key built for the previous endpoint/model/credential.
        resolved_adapters = []
        provider_identities = []
        for model_ref in [config.model] + list(config.fallback_models):
            try:
                adapter = copy.copy(self.get_adapter(model_ref, user_id=context.user_id))
                identity = self._adapter_cache_identity(adapter)
                adapter_error = None
            except Exception as exc:
                adapter, identity, adapter_error = None, None, exc
            resolved_adapters.append((model_ref, adapter, adapter_error))
            provider_identities.append([model_ref, identity])

        # 1. Compute cache key
        # Preserve full message semantics and resolved request ownership. The
        # versioned structured key cannot match legacy unscoped cache entries.
        cache_request = {
            # Older entries did not retain a verified provider completion state.
            "version": 5,
            "messages": messages,
            "content": generation_content,
            "template": prompt_template_id,
            "task": task_type.value,
            "model": config.model,
            "fallback": config.fallback_models,
            "sampling": [config.temperature, config.top_p, config.max_tokens],
            "scope": [context.user_id, context.project_id, context.api_key_level],
            "providers": provider_identities,
        }
        cache_key = hashlib.sha256(json.dumps(
            cache_request, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")).hexdigest()

        entry = self._generation_locks.get(cache_key)
        lock, references = entry if entry is not None else (asyncio.Lock(), 0)
        self._generation_locks[cache_key] = (lock, references + 1)
        try:
            async with lock:
                # Each consumer rechecks the cache with its own validator. A
                # failed/cancelled leader leaves no successful shared result.
                return await self._generate_cached(
                    task_type, messages, generation_content, config, context,
                    cache_key, resolved_adapters,
                )
        finally:
            _, references = self._generation_locks[cache_key]
            if references == 1:
                del self._generation_locks[cache_key]
            else:
                self._generation_locks[cache_key] = (lock, references - 1)

    async def _generate_cached(
        self,
        task_type: TaskType,
        messages: list[dict[str, str]],
        generation_content: str,
        config: GenerationConfig,
        context: RequestContext,
        cache_key: str,
        resolved_adapters: list,
    ) -> GenerationResult:
        # 2. Check cache
        cached = await cache_manager.get_entry(cache_key)
        if cached and not self._response_is_valid(cached.response_content, config):
            await cache_manager.delete(cache_key)
            cached = None
        if cached:
            return GenerationResult(
                content=cached.response_content,
                raw_content="",
                model_used=cached.model_used + "(cache)",
                tokens_input=0,
                tokens_output=0,
                from_cache=True,
                cost_estimate=0,
                duration_ms=0,
            )

        # 3. Check quota
        allowed, msg = await rate_limiter.check_quota(context.user_id, len(generation_content) // 4)
        if not allowed:
            raise QuotaExceededError(msg)

        # 4. Acquire concurrency slot
        await rate_limiter.acquire(context.user_id)

        try:
            # 5. Try primary model, fall back on failure
            last_error = None
            start_time = time.time()

            for attempt, (model_ref, adapter, adapter_error) in enumerate(resolved_adapters):
                try:
                    if adapter_error is not None:
                        raise adapter_error
                    response = await adapter.chat_completion(
                        messages,
                        AdapterConfig(
                            temperature=config.temperature,
                            max_tokens=config.max_tokens,
                            top_p=config.top_p,
                            timeout=config.timeout,
                        ),
                    )

                    actual_model = response.model or model_ref
                    duration_ms = int((time.time() - start_time) * 1000)

                    # 7. Record usage
                    await rate_limiter.record_usage(
                        context.user_id, response.tokens_input, response.tokens_output
                    )

                    # Retain billed usage, but incomplete output is neither a
                    # successful cache entry nor a reason for another model call.
                    if (response.finish_reason not in ("stop", "end_turn", "stop_sequence")
                            or not isinstance(response.content, str) or not response.content.strip()):
                        error = IncompleteResponseError("Provider did not complete a nonempty text response")
                        await rate_limiter.log_call(
                            user_id=context.user_id, task_type=task_type.value,
                            model_name=actual_model, tokens_input=response.tokens_input,
                            tokens_output=response.tokens_output, from_cache=False,
                            success=False, duration_ms=duration_ms, error_message=str(error),
                        )
                        raise error

                    # 6. Normalize response using the existing consumer policy.
                    normalized = self._normalize_response(response.content, task_type)

                    # Consumption occurred even when the consumer cannot use
                    # the response. Keep its usage, but never cache it as success.
                    if not self._response_is_valid(normalized, config):
                        error = ResponseValidationError("Response failed consumer validation")
                        await rate_limiter.log_call(
                            user_id=context.user_id, task_type=task_type.value,
                            model_name=actual_model, tokens_input=response.tokens_input,
                            tokens_output=response.tokens_output, from_cache=False,
                            success=False, duration_ms=duration_ms, error_message=str(error),
                        )
                        raise error

                    # 8. Cache successful result
                    await cache_manager.set(
                        cache_key=cache_key,
                        response_content=normalized,
                        model_used=actual_model,
                        tokens_input=response.tokens_input,
                        tokens_output=response.tokens_output,
                        ttl_days=config.cache_ttl_days,
                    )

                    # 9. Log call
                    await rate_limiter.log_call(
                        user_id=context.user_id,
                        task_type=task_type.value,
                        model_name=actual_model,
                        tokens_input=response.tokens_input,
                        tokens_output=response.tokens_output,
                        from_cache=False,
                        success=True,
                        duration_ms=duration_ms,
                        content_summary=normalized[:200],
                    )

                    return GenerationResult(
                        content=normalized,
                        raw_content=response.raw_content,
                        model_used=actual_model,
                        tokens_input=response.tokens_input,
                        tokens_output=response.tokens_output,
                        from_cache=False,
                        cost_estimate=rate_limiter._estimate_cost(
                            model_ref, response.tokens_input, response.tokens_output
                        ),
                        duration_ms=duration_ms,
                    )

                except InvalidResponseError as e:
                    await rate_limiter.record_usage(context.user_id, e.tokens_input, e.tokens_output)
                    await rate_limiter.log_call(
                        user_id=context.user_id, task_type=task_type.value,
                        model_name=model_ref, tokens_input=e.tokens_input,
                        tokens_output=e.tokens_output, from_cache=False,
                        success=False, duration_ms=int((time.time() - start_time) * 1000),
                        error_message=str(e),
                    )
                    last_error = e
                    break
                except IncompleteResponseError as e:
                    last_error = e
                    break
                except Exception as e:
                    last_error = e
                    if attempt < config.max_retries:
                        wait_time = 2 ** attempt  # exponential backoff
                        await asyncio.sleep(wait_time)
                    continue

            # All models failed
            duration_ms = int((time.time() - start_time) * 1000)
            if not isinstance(last_error, (ResponseValidationError, InvalidResponseError)):
                await rate_limiter.log_call(
                    user_id=context.user_id,
                    task_type=task_type.value,
                    model_name=config.model,
                    tokens_input=0,
                    tokens_output=0,
                    from_cache=False,
                    success=False,
                    duration_ms=duration_ms,
                    error_message=str(last_error),
                )
            raise AllModelsFailedError(f"All models failed: {last_error}")

        finally:
            rate_limiter.release()

    def _is_agent_public_provider(self, provider: str, user_id: str) -> bool:
        """Public refs cannot address reserved account registration namespaces."""
        registered_protocols = ("openai", "gpt-4o", "gpt-4o-mini", "anthropic",
            "claude-opus-4-6", "claude-sonnet-4-6", "deepseek", "deepseek-chat",
            "deepseek-reasoner", "ollama", "qwen", "qwen-turbo", "qwen-plus", "qwen-max",
            "zhipu", "glm-4", "glm-4-flash")
        if any(provider.startswith(name + "_") for name in registered_protocols):
            return False
        if "_" not in provider:
            return True
        # Custom global provider names may contain underscores only when their
        # global registration is explicit. Legacy unannotated keys stay private.
        if provider in self._adapter_owners and self._adapter_owners[provider] is None:
            return True
        owned_key = provider + "_" + user_id
        return (owned_key in self._adapters
                and self._adapter_owners.get(owned_key) in (None, user_id))

    def get_agent_models(self, user_id: str) -> list[dict[str, str]]:
        """Export configured native choices, never credentials or other accounts.

        Discovery makes no provider request and therefore cannot verify whether
        the selected model implements tools. Legacy account registration keys
        without ownership metadata are conservatively kept account scoped.
        """
        if not user_id:
            return []
        import os
        for provider, env_name in (("deepseek", "DEEPSEEK_API_KEY"), ("openai", "OPENAI_API_KEY")):
            if provider not in self._adapters and os.getenv(env_name):
                self.configure_adapter(provider, os.environ[env_name])
        aliases = {"deepseek-chat":"deepseek", "deepseek-reasoner":"deepseek",
                   "gpt-4o":"openai", "gpt-4o-mini":"openai",
                   "claude-opus-4-6":"anthropic", "claude-sonnet-4-6":"anthropic",
                   "qwen-turbo":"qwen", "qwen-plus":"qwen", "qwen-max":"qwen",
                   "glm-4":"zhipu", "glm-4-flash":"zhipu"}
        registrations = []
        for key, adapter in self._adapters.items():
            owner = self._adapter_owners.get(key)
            if owner not in (None, user_id):
                continue
            suffix = "_" + user_id
            if key.endswith(suffix):
                provider = key[:-len(suffix)]
                personal = True
            elif owner is not None or not self._is_agent_public_provider(key, user_id):
                # Account namespaces predate explicit owner metadata. Never
                # expose an unowned foreign registration as a global provider.
                continue
            else:
                provider = key
                personal = False
            if not self._is_agent_public_provider(provider, user_id):
                continue
            provider = aliases.get(provider, provider)
            model = getattr(adapter, "model_name", None)
            if not provider or not isinstance(model, str) or not model:
                continue
            registrations.append((provider, model, personal))
        # Native resolution gives an account's provider precedence over global
        # credentials. Show the same effective choices rather than advertising
        # a global gateway model that would be sent to the personal gateway.
        personal_providers = {provider for provider, _, personal in registrations if personal}
        choices = {}
        for provider, model, personal in registrations:
            if not personal and provider in personal_providers:
                continue
            value = provider + "/" + model
            choices[value] = {"value":value, "label":provider + " / " + model,
                              "model_tool_support":"unverified"}
        for provider in {item[0] for item in registrations}:
            saved = self._discovered_models.get((user_id, provider))
            if saved is None:
                continue
            adapter = self.get_agent_adapter(provider, user_id)
            if saved[0] == self._adapter_cache_identity(adapter):
                choices = {value: item for value, item in choices.items() if value.split('/', 1)[0] != provider}
                choices.update((item['value'], dict(item)) for item in saved[1])
        return [choices[value] for value in sorted(choices)]

    def get_agent_adapter(self, model_ref: str, user_id: str) -> BaseModelAdapter:
        """Resolve an explicit native model without another account's fallback."""
        if not user_id or not model_ref:
            raise ValueError("An explicit user and configured model are required")
        provider, _, model_name = model_ref.partition("/")
        if not self._is_agent_public_provider(provider, user_id):
            raise ValueError("No adapter configured for the requested native model")
        alias_providers={"deepseek-chat":"deepseek","deepseek-reasoner":"deepseek",
            "gpt-4o":"openai","gpt-4o-mini":"openai",
            "claude-opus-4-6":"anthropic","claude-sonnet-4-6":"anthropic",
            "qwen-turbo":"qwen","qwen-plus":"qwen","qwen-max":"qwen",
            "glm-4":"zhipu","glm-4-flash":"zhipu"}
        base_provider=alias_providers.get(provider,provider)
        candidates=(f"{provider}_{user_id}",f"{base_provider}_{user_id}",provider,base_provider)
        if alias_providers.get(model_name)==base_provider:
            # Discovery normalizes aliases to provider/model. A standalone owned
            # alias registration must still beat a different global gateway.
            candidates=(f"{model_name}_{user_id}",*candidates)
        # Scope before matching models: a canonical alias may exist without the
        # base registration, and its configured model can be a custom name.
        suffix="_"+user_id
        owned_keys=[];global_keys=[]
        for candidate in dict.fromkeys((*candidates,*self._adapters)):
            if candidate not in self._adapters:
                continue
            owner=self._adapter_owners.get(candidate)
            if owner not in (None,user_id):
                continue
            if candidate.endswith(suffix):
                candidate_provider=candidate[:-len(suffix)]
                scope_keys=owned_keys
            elif self._is_agent_public_provider(candidate,user_id):
                candidate_provider=candidate
                scope_keys=owned_keys if owner==user_id else global_keys
            else:
                continue
            if alias_providers.get(candidate_provider,candidate_provider)==base_provider:
                scope_keys.append(candidate)
        applicable_keys=owned_keys or global_keys
        if model_name:
            for candidate in applicable_keys:
                adapter=self._adapters[candidate]
                if getattr(adapter,"model_name",None)==model_name:
                    return self._bind_model(adapter,model_name)
        key=applicable_keys[0] if applicable_keys else provider
        adapter=self._adapters.get(key)
        if adapter is not None and self._adapter_owners.get(key) not in (None,user_id):
            raise ValueError("No adapter configured for the requested native model")
        if adapter is None:
            import os
            env_name = {"deepseek": "DEEPSEEK_API_KEY", "openai": "OPENAI_API_KEY",
                        "gpt-4o": "OPENAI_API_KEY", "gpt-4o-mini": "OPENAI_API_KEY"}.get(base_provider)
            if env_name and os.getenv(env_name):
                adapter = self.get_adapter(base_provider, user_id=user_id)
            else:
                raise ValueError("No adapter configured for the requested native model")
        return self._bind_model(adapter, model_name or (provider if base_provider!=provider else None))

    async def agent_turn(self, messages: list[dict], tools: list[dict],
                         config: GenerationConfig, *, user_id: str):
        """Serialize native calls for one account through usage reconciliation.

        This is per client/event loop; it is not a cross-worker quota reservation.
        Cancelled waiters release their ownership without starting a provider.
        """
        lock, users = self._native_locks.get(user_id, (asyncio.Lock(), 0))
        self._native_locks[user_id] = (lock, users+1)
        try:
            async with lock:
                return await self._agent_turn_locked(messages, tools, config, user_id=user_id)
        finally:
            _, users = self._native_locks[user_id]
            if users == 1:
                self._native_locks.pop(user_id)
            else:
                self._native_locks[user_id] = (lock, users-1)

    async def _agent_turn_locked(self, messages: list[dict], tools: list[dict],
                                config: GenerationConfig, *, user_id: str):
        """One native tool-aware call through quota; no cache, retry or fallback.

        Tools are dispatched only by the caller after a validated provider reply.
        An interrupted HTTP thread may still be billed remotely; no completion or
        retry is inferred from cancellation (durable reconciliation is separate).
        """
        adapter = self.get_agent_adapter(config.model, user_id)
        owned_messages, owned_tools = copy.deepcopy(messages), copy.deepcopy(tools)
        estimated = adapter.count_tokens(json.dumps({"messages": owned_messages, "tools": owned_tools},
                                                    ensure_ascii=False)) + config.max_tokens
        allowed, reason = await rate_limiter.check_quota(user_id, estimated)
        if not allowed:
            raise QuotaExceededError(reason)
        await rate_limiter.acquire(user_id)
        started = time.monotonic()
        response = None
        try:
            response = await adapter.chat_completion_with_tools(owned_messages, owned_tools, AdapterConfig(
                temperature=config.temperature, max_tokens=config.max_tokens,
                top_p=config.top_p, timeout=config.timeout))
            if ((response.tool_calls and response.finish_reason not in ("tool_calls", "tool_use"))
                    or (not response.tool_calls and (response.finish_reason not in ("stop", "end_turn", "stop_sequence")
                        or not isinstance(response.content, str) or not response.content.strip()))):
                raise InvalidResponseError("Provider did not complete a native turn",
                                           response.tokens_input, response.tokens_output)
        except InvalidResponseError as error:
            await rate_limiter.record_usage(user_id, error.tokens_input, error.tokens_output)
            await rate_limiter.log_call(user_id=user_id, task_type="agent_readonly", model_name=config.model,
                tokens_input=error.tokens_input, tokens_output=error.tokens_output, from_cache=False,
                success=False, duration_ms=int((time.monotonic()-started)*1000), error_message=str(error))
            raise
        except (Exception, asyncio.CancelledError) as error:
            await rate_limiter.log_call(user_id=user_id, task_type="agent_readonly", model_name=config.model,
                tokens_input=0, tokens_output=0, from_cache=False, success=False,
                duration_ms=int((time.monotonic()-started)*1000),
                error_message="cancelled; provider usage unknown" if isinstance(error, asyncio.CancelledError) else str(error))
            raise
        else:
            await rate_limiter.record_usage(user_id, response.tokens_input, response.tokens_output)
            await rate_limiter.log_call(user_id=user_id, task_type="agent_readonly", model_name=response.model or config.model,
                tokens_input=response.tokens_input, tokens_output=response.tokens_output, from_cache=False,
                success=True, duration_ms=int((time.monotonic()-started)*1000))
            return response
        finally:
            rate_limiter.release()

    async def chat(
        self,
        prompt: str,
        system: str | None = None,
        task_type: str | TaskType = TaskType.CHAT,
        config: GenerationConfig | None = None,
        context: RequestContext | None = None,
        model: str | None = None,
        messages: list[dict[str, str]] | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Convenience wrapper for single-prompt chat calls.

        Builds a messages list from (optional system) + user prompt and delegates
        to generate() (which applies caching, quota, rate limiting, retry, and
        normalization). Returns the model's text content, stripped.

        ``task_type`` accepts either a ``TaskType`` or a string. Unknown string
        values (e.g. "research", "book_gen") fall back to ``TaskType.CHAT`` since
        those flows do not need JSON extraction.
        Single-call model/token overrides do not mutate the caller's config.
        """
        if isinstance(task_type, TaskType):
            task_enum = task_type
        else:
            try:
                task_enum = TaskType(task_type)
            except ValueError:
                task_enum = TaskType.CHAT

        config = copy.copy(config) if config is not None else self.get_default_configuration()
        if model:
            config.model = model
        if max_tokens is not None:
            config.max_tokens = max_tokens

        if messages is None:
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})

        result = await self.generate(
            task_type=task_enum,
            messages=messages,
            prompt_template_id=f"chat:{task_enum.value}",
            generation_content=prompt,
            config=config,
            context=context,
        )
        return result.content

    async def analyze_image(
        self,
        image_base64: str,
        image_type: str,
        prompt: str,
        context_text: str = "",
        model: str = "gpt-4o",
        user_id: str | None = None,
    ) -> str:
        """Analyze a single image using a vision-capable model.

        Returns the text description generated by the model.
        """
        # Fall back to contextvar if no explicit user_id
        if not user_id or user_id == "local_user":
            ctxv = _current_user_id.get()
            if ctxv != "local_user":
                user_id = ctxv

        adapter = self.get_adapter(model, user_id=user_id)

        # Build multimodal message
        content_blocks = []
        if context_text:
            content_blocks.append({
                "type": "text",
                "text": f"该图片在文档中的上下文信息：\n{context_text}",
            })
        content_blocks.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:{image_type};base64,{image_base64}",
                "detail": "low",
            },
        })
        content_blocks.append({"type": "text", "text": prompt})

        messages = [{"role": "user", "content": content_blocks}]

        response = await adapter.chat_completion(
            messages=messages,
            config=AdapterConfig(temperature=0.3, max_tokens=512),
        )
        return response.content

    def _normalize_response(self, content: str, task_type: TaskType) -> str:
        """Normalize model output: strip markdown code fences, validate JSON."""
        if task_type in (
            TaskType.KNOWLEDGE_TREE,
            TaskType.QUIZ_GEN,
            TaskType.FLASHCARD_GEN,
            TaskType.GAME_GEN,
        ):
            # Extract JSON from markdown code blocks
            content = self._extract_json(content)
        return content.strip()

    @staticmethod
    def _extract_json(text: str) -> str:
        """Extract JSON from text, stripping markdown fences."""
        # Try to find JSON block in markdown
        pattern = r"```(?:json)?\s*([\s\S]*?)```"
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
        # Try to find JSON object directly
        brace_start = text.find("{")
        bracket_start = text.find("[")
        if brace_start == -1 and bracket_start == -1:
            return text
        start = brace_start if brace_start != -1 and (bracket_start == -1 or brace_start < bracket_start) else bracket_start
        return text[start:].strip()


class QuotaExceededError(Exception):
    pass


class AllModelsFailedError(Exception):
    pass


class ResponseValidationError(ValueError):
    pass


class IncompleteResponseError(ResponseValidationError):
    pass


# Singleton instance
api_client = UnifiedAPIClient()
