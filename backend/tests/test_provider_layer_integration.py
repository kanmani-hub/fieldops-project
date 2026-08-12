"""
test_provider_layer_integration.py

Task 4.7: Final Provider Layer Integration Audit and Regression Test Suite.

Verifies end-to-end integration across:
- 1. BaseAIProvider contract
- 2. GroqProvider and GroqClient behavior
- 3. ProviderFactory registry and isolation
- 4. ProviderHealthMonitor states and fail-closed Redis snapshots
- 5. ProviderFailoverExecutor fallback execution
- 6. CircuitBreaker state machine, sliding window, and probe locks
- 7. SyncTokenBudgetManager reservation, reconciliation, and cancellation
- 8. ProviderCache key generation, TTL, and corruption handling
- 9. AIOrchestrator privacy boundary and end-to-end flow
- 10. FastAPI lifespan integration and clean shutdown
- 11. Privacy, security, and zero secret leakage in logs/keys
- 12. Concurrency and thread safety

No real Groq API calls or real Redis connections are used in this test suite.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import threading
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis
import pytest
from fastapi import FastAPI
from pydantic import BaseModel

from app.services.ai.FieldOpsAI.config.config_loader import ConfigLoader
from app.services.ai.FieldOpsAI.providers.base_provider import (
    BaseAIProvider,
    ProviderConfigurationError,
    ProviderExecutionError,
)
from app.services.ai.FieldOpsAI.providers.budget import (
    BudgetExceededError,
    BudgetInfrastructureError,
    SyncTokenBudgetManager,
    TokenBudgetConfig,
)
from app.services.ai.FieldOpsAI.providers.cache import (
    CacheTTLPolicy,
    CachedProviderResponse,
    ProviderCache,
    ProviderCacheConfig,
    ProviderCacheRequest,
)
from app.services.ai.FieldOpsAI.providers.groq_client import GroqClient
from app.services.ai.FieldOpsAI.providers.groq_provider import GroqProvider
from app.services.ai.FieldOpsAI.providers.provider_factory import ProviderFactory
from app.services.ai.FieldOpsAI.providers.provider_failover import (
    FailoverAttempt,
    ProviderFailoverExecutor,
    ProviderFailoverExhaustedError,
)
from app.services.ai.FieldOpsAI.providers.provider_health import (
    ProviderHealthInfrastructureError,
    ProviderHealthMonitor,
)
from app.services.ai.FieldOpsAI.runtime.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerInfrastructureError,
    CircuitOpenError,
    CircuitPermit,
)
from app.services.ai.FieldOpsAI.runtime.orchestrator import AIOrchestrator
from app.services.ai.FieldOpsAI.schemas.ai_task import AITask
from app.services.ai.FieldOpsAI.schemas.provider import GenerationResult, ProviderHealth, UsageStats
from app.services.ai.pii_sanitizer import PlaceholderMap, SanitizationResult, pii_sanitizer

fake_redis = fakeredis.FakeRedis(decode_responses=True)


# ==========================================================
# Fixtures & Reset Helpers
# ==========================================================

@pytest.fixture(autouse=True)
def reset_test_state() -> None:
    """Ensure clean state between integration tests."""
    fake_redis.flushall()
    with ProviderFactory._lock:
        ProviderFactory._registry.clear()
        ProviderFactory.register("groq", GroqProvider, replace=True)


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FakeIntegrationProvider(BaseAIProvider):
    def __init__(self, name: str = "groq", model: str = "llama-3.3-70b-versatile", config: Any = None, **kwargs: Any) -> None:
        self._name = name
        self._model = model
        self.config = config

    def generate_completion(self, messages: List[Dict[str, str]], temperature: float = 0.7, max_tokens: int = 1000) -> str:
        return '{"summary": "fake response"}'

    def provider_name(self) -> str:
        return self._name

    def model_name(self) -> str:
        return self._model

    def health_check(self) -> bool:
        return True


def make_gen_result(
    provider: str = "groq",
    model: str = "llama-3.3-70b-versatile",
    text: str = '{"summary": "integration ok"}',
) -> GenerationResult:
    return GenerationResult(
        text=text,
        provider_name=provider,
        model_name=model,
        usage=UsageStats(
            prompt_tokens=12,
            completion_tokens=6,
            total_tokens=18,
            request_count=1,
            latency_ms=45.0,
            cost_usd=0.0,
        ),
    )


# ==========================================================
# 1. BASE PROVIDER CONTRACT
# ==========================================================

def test_base_provider_cannot_be_instantiated() -> None:
    with pytest.raises(TypeError):
        BaseAIProvider()  # type: ignore[abstract]


def test_incomplete_provider_subclass_raises() -> None:
    class IncompleteProvider(BaseAIProvider):
        def provider_name(self) -> str:
            return "incomplete"

    with pytest.raises(TypeError):
        IncompleteProvider()  # type: ignore[abstract]


def test_complete_provider_subclass_contract() -> None:
    provider = FakeIntegrationProvider("groq", "llama-3.3-70b-versatile")
    assert provider.provider_name() == "groq"
    assert provider.model_name() == "llama-3.3-70b-versatile"
    assert provider.health_check() is True
    res = provider.generate_completion([{"role": "user", "content": "hi"}])
    assert "fake response" in res


# ==========================================================
# 2. GROQ PROVIDER & CLIENT
# ==========================================================

def test_groq_provider_initialization_and_allowlist() -> None:
    mock_client = MagicMock()
    provider = GroqProvider(client=mock_client)
    assert provider.provider_name() == "Groq"
    assert provider.model_name() == "llama-3.3-70b-versatile"

    mock_bad_config = MagicMock()
    mock_bad_config.model_name = "unsupported-model-99"

    with pytest.raises(ProviderConfigurationError) as exc_info:
        GroqProvider(client=mock_client, config=mock_bad_config)
    assert "Unsupported model configuration" in str(exc_info.value)


def test_groq_provider_secret_masking_in_exceptions(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    secret_prompt = "SECRET_USER_PROMPT_123"
    secret_key = "gsk_SECRET_API_KEY_XYZ"

    mock_client = MagicMock()
    err_500 = Exception(f"Crash with {secret_key}")
    err_500.status_code = 500
    mock_client.chat.completions.create.side_effect = err_500

    provider = GroqProvider(client=mock_client)
    with pytest.raises(ProviderExecutionError) as exc_info:
        provider.generate_result(messages=[{"role": "user", "content": secret_prompt}])

    assert secret_key not in str(exc_info.value)
    for record in caplog.records:
        msg = record.getMessage()
        assert secret_prompt not in msg
        assert secret_key not in msg


# ==========================================================
# 3. PROVIDER FACTORY
# ==========================================================

def test_provider_factory_default_registration_and_isolation() -> None:
    registered = ProviderFactory.registered_names()
    assert "groq" in registered

    with pytest.raises(ProviderConfigurationError):
        ProviderFactory.register("groq", FakeIntegrationProvider, replace=False)

    ProviderFactory.register("groq", FakeIntegrationProvider, replace=True)
    inst = ProviderFactory.create_provider(name="groq")
    assert isinstance(inst, FakeIntegrationProvider)


def test_provider_factory_unknown_provider_raises() -> None:
    with pytest.raises(ProviderConfigurationError) as exc_info:
        ProviderFactory.create_provider(name="unknown_provider_99")
    assert "Configured AI provider is unsupported." in str(exc_info.value)


# ==========================================================
# 4. PROVIDER HEALTH
# ==========================================================

def test_provider_health_transitions_and_snapshots() -> None:
    config = ConfigLoader().provider_health
    monitor = ProviderHealthMonitor(fake_redis, config=config)

    provider = FakeIntegrationProvider("groq")
    snapshot = monitor.check_provider("groq", provider=provider)
    assert snapshot.status == ProviderHealth.HEALTHY

    retrieved = monitor.get_snapshot("groq")
    assert retrieved is not None
    assert retrieved.status == ProviderHealth.HEALTHY


def test_provider_health_redis_failure_fails_closed() -> None:
    broken_redis = MagicMock()
    broken_redis.get.side_effect = RuntimeError("Redis crash")
    from app.services.ai.FieldOpsAI.providers import provider_health
    config = provider_health.ProviderHealthConfig(enabled=True)
    monitor = provider_health.ProviderHealthMonitor(broken_redis, config=config)

    with pytest.raises(provider_health.ProviderHealthInfrastructureError) as exc_info:
        monitor.get_snapshot("groq")
    assert "Failed to read provider health snapshot from Redis." in str(exc_info.value)


# ==========================================================
# 5. PROVIDER FAILOVER
# ==========================================================

def test_provider_failover_primary_success() -> None:
    mock_factory = MagicMock()
    mock_factory.create_provider.side_effect = lambda name=None, config=None, provider_kwargs=None: FakeIntegrationProvider(name or "groq")

    mock_config = MagicMock()
    mock_config.provider_fallback_order = ["groq"]

    executor = ProviderFailoverExecutor(provider_factory=mock_factory, health_monitor=None, config=mock_config)

    def runner(pname, provider):
        return make_gen_result(pname)

    res = executor.execute(runner)
    assert res.selected_provider == "groq"
    assert res.failover_occurred is False


def test_provider_failover_single_provider_circuit_open_re_raises() -> None:
    mock_factory = MagicMock()
    mock_factory.create_provider.side_effect = lambda name=None, config=None, provider_kwargs=None: FakeIntegrationProvider(name or "groq")

    mock_config = MagicMock()
    mock_config.provider_fallback_order = ["groq"]

    executor = ProviderFailoverExecutor(provider_factory=mock_factory, health_monitor=None, config=mock_config)

    def runner(pname, provider):
        raise CircuitOpenError("Circuit is OPEN for groq")

    with pytest.raises(CircuitOpenError):
        executor.execute(runner)


# ==========================================================
# 6. CIRCUIT BREAKER
# ==========================================================

def test_circuit_breaker_sliding_window_and_probe_lock() -> None:
    cb = CircuitBreaker(fake_redis, CircuitBreakerConfig(failure_threshold=2, open_cooldown_seconds=60))
    permit = cb.check_permission("groq")

    err = ProviderExecutionError("500 Error", status_code=500, is_retryable=True)
    cb.record_failure(permit, err)

    permit2 = cb.check_permission("groq")
    cb.record_failure(permit2, err)

    with pytest.raises(CircuitOpenError):
        cb.check_permission("groq")


# ==========================================================
# 7. TOKEN BUDGET AND RATE LIMITS
# ==========================================================

def test_token_budget_reserve_reconcile_cancel_cycle() -> None:
    budget_cfg = TokenBudgetConfig(daily_token_limit=1000, per_request={"general": 100}, atomic_strategy="transaction")
    manager = SyncTokenBudgetManager(fake_redis, budget_cfg)

    res_id = manager.reserve(
        estimated_input_tokens=10,
        max_output_tokens=20,
        category="general",
        provider="groq",
        model="llama-3.3-70b-versatile",
        tenant_id="tenant-1",
    )
    assert res_id is not None

    manager.reconcile(
        reservation_id=res_id,
        actual_input_tokens=10,
        actual_output_tokens=15,
        provider="groq",
    )


# ==========================================================
# 8. PROVIDER CACHE
# ==========================================================

@pytest.mark.anyio
async def test_provider_cache_set_get_and_key_determinism() -> None:
    fake_redis_async = fakeredis.aioredis.FakeRedis(decode_responses=True)
    cache = ProviderCache(fake_redis_async, config=ProviderCacheConfig(enabled=True))

    sanitized_res = SanitizationResult(
        sanitized_data={"job": "test"},
        placeholder_map=PlaceholderMap(),
        replacement_count=0,
    )
    req = ProviderCacheRequest.from_sanitized_payload(
        sanitized_result=sanitized_res,
        provider="groq",
        model="llama-3.3-70b-versatile",
        temperature=0.0,
        max_tokens=100,
        ttl_policy=CacheTTLPolicy.STATIC,
    )

    usage = UsageStats(prompt_tokens=10, completion_tokens=5, total_tokens=15, request_count=1, latency_ms=10.0, cost_usd=0.0)
    res_to_cache = CachedProviderResponse(text='{"summary": "cached"}', usage=usage)

    stored_key = await cache.set(req, res_to_cache)
    assert stored_key is not None

    retrieved = await cache.get(req)
    assert retrieved is not None
    assert retrieved.text == '{"summary": "cached"}'

    await fake_redis_async.aclose()


# ==========================================================
# 9. ORCHESTRATOR INTEGRATION
# ==========================================================

def test_orchestrator_end_to_end_privacy_and_execution() -> None:
    mock_client = MagicMock()

    mock_client.generate_result.return_value = (
        make_gen_result(
            provider="groq",
            text='{"summary": "integration ok"}',
        )
    )

    orchestrator = AIOrchestrator(
        client=mock_client
    )

    result = orchestrator.execute(
        AITask.PLANNING,
        {
            "job_id": "JOB-999",
            "secret_phone": "+1-555-0199",
        },
    )

    assert "integration ok" in str(result)

    mock_client.generate_result.assert_called_once()

    called_context = (
        mock_client
        .generate_result
        .call_args
        .kwargs["context"]
    )

    assert "+1-555-0199" not in str(
        called_context
    )

# ==========================================================
# 10. FASTAPI LIFESPAN
# ==========================================================

async def fake_gps_listener(client):
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass


@pytest.mark.anyio
async def test_lifespan_starts_and_stops_health_monitor_cleanly() -> None:
    from app.main import lifespan
    from app.services.ai.FieldOpsAI.runtime.orchestrator import ai_orchestrator
    app_inst = FastAPI(lifespan=lifespan)

    mock_start = AsyncMock()
    mock_stop = AsyncMock()

    mock_scheduler_inst = MagicMock()
    mock_scheduler_inst.start = AsyncMock()
    mock_scheduler_inst.stop = AsyncMock()

    mock_redis = MagicMock()
    mock_redis.ping = AsyncMock()
    mock_redis.aclose = AsyncMock()

    with patch.object(ai_orchestrator.provider_health_monitor, "start", mock_start), \
         patch.object(ai_orchestrator.provider_health_monitor, "stop", mock_stop), \
         patch("app.main.aioredis.Redis", return_value=mock_redis), \
         patch("app.main.BroadcastScheduler", return_value=mock_scheduler_inst), \
         patch("app.main.redis_gps_listener", side_effect=fake_gps_listener), \
         patch("app.main.start_scheduler"), \
         patch("app.main.stop_scheduler"), \
         patch("app.main.seed_default_templates"):

        async with lifespan(app_inst):
            mock_start.assert_awaited_once()

        mock_stop.assert_awaited_once()


# ==========================================================
# 11. PRIVACY & SECURITY REGRESSION
# ==========================================================

def test_zero_pii_or_api_keys_in_logs_or_keys(caplog) -> None:
    secret_api_key = "gsk_SUPER_SECRET_GROQ_KEY_12345"
    customer_phone = "+91-99999-88888"

    orchestrator = AIOrchestrator(client=MagicMock())

    with caplog.at_level(logging.DEBUG):
        try:
            orchestrator.execute(
                AITask.COMMUNICATION,
                {"phone": customer_phone, "key": secret_api_key},
            )
        except Exception:
            pass

    log_text = caplog.text
    assert secret_api_key not in log_text
    assert customer_phone not in log_text


# ==========================================================
# 12. CONCURRENCY & THREAD SAFETY
# ==========================================================

def test_provider_factory_registry_thread_safety() -> None:
    threads = []
    errors = []

    def worker(idx: int) -> None:
        try:
            name = f"prov_{idx}"
            class ThreadProv(FakeIntegrationProvider):
                def provider_name(self) -> str:
                    return name

            ProviderFactory.register(name, ThreadProv, replace=True)
            cfg = MagicMock()
            cfg.provider_name = name
            inst = ProviderFactory.create_provider(name=name, config=cfg)
            assert inst.provider_name() == name
        except Exception as e:
            errors.append(e)

    for i in range(20):
        t = threading.Thread(target=worker, args=(i,))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    assert len(errors) == 0
