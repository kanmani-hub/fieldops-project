"""
test_provider_cache.py

Unit tests for ProviderCache.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock
import pytest
from pydantic import ValidationError
import fakeredis.aioredis
from app.services.ai.pii_sanitizer import (
    PlaceholderMap,
    SanitizationResult,
)

from app.services.ai.FieldOpsAI.providers.cache import (
    CachedProviderResponse,
    ProviderCache,
    ProviderCacheConfig,
    ProviderCacheRequest,
    CacheTTLPolicy,
)
from app.services.ai.FieldOpsAI.schemas.provider import UsageStats


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def fake_redis():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.flushall()

def make_sanitization_result(
    data: object,
) -> SanitizationResult:
    """
    Build typed sanitizer output for cache unit tests.
    """

    return SanitizationResult(
        sanitized_data=data,
        placeholder_map=PlaceholderMap(),
        replacement_count=0,
    )

# 1. Deterministic SHA-256 key independent of dictionary ordering

def test_cache_key_generation_order_independence() -> None:
    cache = ProviderCache(None)

    msg_dict1 = [{"content": "hello", "role": "user"}]
    msg_dict2 = [{"role": "user", "content": "hello"}]

    req1 = ProviderCacheRequest(
        tenant_id="tenant-1",
        provider="Groq",
        model="llama-3.3-70b-versatile",
        sanitized_messages=msg_dict1,
        temperature=0.0,
        max_tokens=100,
        ttl_policy=CacheTTLPolicy.STATIC,
        explicit_safety_verification=True,
    )

    req2 = ProviderCacheRequest(
        tenant_id="tenant-1",
        provider="Groq",
        model="llama-3.3-70b-versatile",
        sanitized_messages=msg_dict2,
        temperature=0.0,
        max_tokens=100,
        ttl_policy=CacheTTLPolicy.STATIC,
        explicit_safety_verification=True,
    )

    key1 = cache.generate_cache_key(req1)
    key2 = cache.generate_cache_key(req2)

    assert key1 == key2


# 2. Key Privacy (No raw PII/Tenant IDs)

def test_keys_contain_no_pii_or_raw_tenant_ids() -> None:
    cache = ProviderCache(None)
    raw_tenant = "highly_sensitive_tenant_id_123"

    req = ProviderCacheRequest(
        tenant_id=raw_tenant,
        provider="Groq",
        model="llama-3.3-70b-versatile",
        sanitized_messages=[{"role": "user", "content": "some info"}],
        temperature=0.0,
        max_tokens=100,
        ttl_policy=CacheTTLPolicy.STATIC,
        explicit_safety_verification=True,
    )

    key = cache.generate_cache_key(req)
    assert raw_tenant not in key
    assert key.startswith("fieldops:cache:v1:")


# 3. Tenant Isolation

def test_tenant_isolation() -> None:
    cache = ProviderCache(None)

    req_a = ProviderCacheRequest(
        tenant_id="tenantA",
        provider="Groq",
        model="llama-3.3-70b-versatile",
        sanitized_messages=[{"role": "user", "content": "hello"}],
        temperature=0.0,
        max_tokens=100,
        ttl_policy=CacheTTLPolicy.STATIC,
        explicit_safety_verification=True,
    )

    req_b = ProviderCacheRequest(
        tenant_id="tenantB",
        provider="Groq",
        model="llama-3.3-70b-versatile",
        sanitized_messages=[{"role": "user", "content": "hello"}],
        temperature=0.0,
        max_tokens=100,
        ttl_policy=CacheTTLPolicy.STATIC,
        explicit_safety_verification=True,
    )

    key_tenant_a = cache.generate_cache_key(req_a)
    key_tenant_b = cache.generate_cache_key(req_b)
    assert key_tenant_a != key_tenant_b


# 4. Cache hit/miss/store flow

@pytest.mark.anyio
async def test_cache_store_hit_miss(fake_redis) -> None:
    cache = ProviderCache(fake_redis)

    req = ProviderCacheRequest(
        tenant_id="tenant-1",
        provider="groq",
        model="llama-3.3-70b-versatile",
        sanitized_messages=[{"role": "user", "content": "hello"}],
        temperature=0.0,
        max_tokens=100,
        ttl_policy=CacheTTLPolicy.STATIC,
        explicit_safety_verification=True,
    )

    usage = UsageStats(prompt_tokens=5, completion_tokens=5, total_tokens=10, request_count=1, latency_ms=10.0, cost_usd=0.0)
    response = CachedProviderResponse(text="Hello cache world", usage=usage)

    # Cache miss
    cached_get = await cache.get(req)
    assert cached_get is None
    assert cache.statistics().misses == 1

    # Store in cache
    success = await cache.set(req, response)
    assert success is True
    assert cache.statistics().writes == 1

    # Cache hit
    cached_get = await cache.get(req)
    assert cached_get is not None
    assert cached_get.text == "Hello cache world"
    assert cached_get.usage.total_tokens == 10
    assert cache.statistics().hits == 1


# 5. Invalidation without Redis KEYS using SCAN

@pytest.mark.anyio
async def test_invalidation_without_keys_command(fake_redis) -> None:
    cache = ProviderCache(fake_redis)

    req1 = ProviderCacheRequest(
        tenant_id="tenant1",
        provider="groq",
        model="llama-3.3-70b-versatile",
        sanitized_messages=[{"role": "user", "content": "msg1"}],
        temperature=0.0,
        max_tokens=100,
        ttl_policy=CacheTTLPolicy.STATIC,
        explicit_safety_verification=True,
    )

    req2 = ProviderCacheRequest(
        tenant_id="tenant1",
        provider="groq",
        model="llama-3.3-70b-versatile",
        sanitized_messages=[{"role": "user", "content": "msg2"}],
        temperature=0.0,
        max_tokens=100,
        ttl_policy=CacheTTLPolicy.STATIC,
        explicit_safety_verification=True,
    )

    usage = UsageStats(prompt_tokens=5, completion_tokens=5, total_tokens=10, request_count=1, latency_ms=10.0, cost_usd=0.0)
    response = CachedProviderResponse(text="cached response", usage=usage)

    await cache.set(req1, response)
    await cache.set(req2, response)

    key1 = cache.generate_cache_key(req1)
    key2 = cache.generate_cache_key(req2)

    assert await fake_redis.get(key1) is not None
    assert await fake_redis.get(key2) is not None

    deleted = await cache.invalidate_provider_namespace(provider="groq", tenant_id="tenant1")
    assert deleted == 2

    assert await fake_redis.get(key1) is None
    assert await fake_redis.get(key2) is None
    assert cache.statistics().invalidations == 2


# 6. TTL: dynamic vs static

@pytest.mark.anyio
async def test_ttl_selection(fake_redis) -> None:
    config = ProviderCacheConfig(dynamic_ttl_seconds=10, static_ttl_seconds=100)
    cache = ProviderCache(fake_redis, config=config)

    usage = UsageStats(prompt_tokens=5, completion_tokens=5, total_tokens=10, request_count=1, latency_ms=10.0, cost_usd=0.0)
    response = CachedProviderResponse(text="cached response", usage=usage)

    req_static = ProviderCacheRequest(
        tenant_id="tenant-1",
        provider="groq",
        model="llama-3.3-70b-versatile",
        sanitized_messages=[{"role": "user", "content": "hello"}],
        temperature=0.0,
        max_tokens=100,
        ttl_policy=CacheTTLPolicy.STATIC,
        explicit_safety_verification=True,
    )

    req_dynamic = ProviderCacheRequest(
        tenant_id="tenant-1",
        provider="groq",
        model="llama-3.3-70b-versatile",
        sanitized_messages=[{"role": "user", "content": "hello"}],
        temperature=0.0,
        max_tokens=100,
        ttl_policy=CacheTTLPolicy.DYNAMIC,
        explicit_safety_verification=True,
    )

    # Static TTL
    await cache.set(req_static, response)
    key_static = cache.generate_cache_key(req_static)
    ttl_static = await fake_redis.ttl(key_static)
    assert 90 <= ttl_static <= 100

    # Dynamic TTL
    await cache.set(req_dynamic, response)
    key_dynamic = cache.generate_cache_key(req_dynamic)
    ttl_dynamic = await fake_redis.ttl(key_dynamic)
    assert 5 <= ttl_dynamic <= 10


# 7. Safe JSON Serialization and NO Pickle

@pytest.mark.anyio
async def test_safe_serialization(fake_redis) -> None:
    cache = ProviderCache(fake_redis)

    req = ProviderCacheRequest(
        tenant_id="tenant-1",
        provider="groq",
        model="llama-3.3-70b-versatile",
        sanitized_messages=[{"role": "user", "content": "hello"}],
        temperature=0.0,
        max_tokens=100,
        ttl_policy=CacheTTLPolicy.STATIC,
        explicit_safety_verification=True,
    )

    usage = UsageStats(prompt_tokens=5, completion_tokens=5, total_tokens=10, request_count=1, latency_ms=10.0, cost_usd=0.0)
    response = CachedProviderResponse(text="serialization check", usage=usage)

    await cache.set(req, response)
    key = cache.generate_cache_key(req)
    raw_val = await fake_redis.get(key)

    assert isinstance(raw_val, str)
    parsed = json.loads(raw_val)
    assert parsed["text"] == "serialization check"


# 8. Oversized response byte limit rejection

@pytest.mark.anyio
async def test_oversized_response_rejected(fake_redis) -> None:
    config = ProviderCacheConfig(max_response_bytes=65)
    cache = ProviderCache(fake_redis, config=config)

    req = ProviderCacheRequest(
        tenant_id="tenant-1",
        provider="groq",
        model="llama-3.3-70b-versatile",
        sanitized_messages=[{"role": "user", "content": "hello"}],
        temperature=0.0,
        max_tokens=100,
        ttl_policy=CacheTTLPolicy.STATIC,
        explicit_safety_verification=True,
    )

    usage = UsageStats(prompt_tokens=5, completion_tokens=5, total_tokens=10, request_count=1, latency_ms=10.0, cost_usd=0.0)
    
    # Let's generate a string of known length to verify the boundary limits
    # Max bytes = 65. The metadata for CachedProviderResponse (JSON keys + usage values) takes ~80 bytes.
    # So even a short response will exceed 65 bytes and get rejected.
    response = CachedProviderResponse(text="short", usage=usage)

    success = await cache.set(req, response)
    assert success is False
    key = cache.generate_cache_key(req)
    assert await fake_redis.get(key) is None


# 9. Sanitization verification check

def test_unsanitized_input_rejected() -> None:
    with pytest.raises(ValidationError):
        ProviderCacheRequest(
            tenant_id="tenant-1",
            provider="groq",
            model="llama-3.3-70b-versatile",
            sanitized_messages=[{"role": "user", "content": "hello"}],
            temperature=0.0,
            max_tokens=100,
            ttl_policy=CacheTTLPolicy.STATIC,
            explicit_safety_verification=False,
        )


# 10. Fail-open safety policy on Redis error

@pytest.mark.anyio
async def test_redis_failure_fails_open() -> None:
    mock_redis = MagicMock()
    mock_redis.get = MagicMock(side_effect=Exception("Redis timeout"))
    mock_redis.set = MagicMock(side_effect=Exception("Redis connection lost"))

    cache = ProviderCache(mock_redis)

    req = ProviderCacheRequest(
        tenant_id="tenant-1",
        provider="groq",
        model="llama-3.3-70b-versatile",
        sanitized_messages=[{"role": "user", "content": "hello"}],
        temperature=0.0,
        max_tokens=100,
        ttl_policy=CacheTTLPolicy.STATIC,
        explicit_safety_verification=True,
    )

    cached_res = await cache.get(req)
    assert cached_res is None
    assert cache.statistics().misses == 1
    assert cache.statistics().errors == 1

    usage = UsageStats(prompt_tokens=5, completion_tokens=5, total_tokens=10, request_count=1, latency_ms=10.0, cost_usd=0.0)
    response = CachedProviderResponse(text="some text", usage=usage)
    success = await cache.set(req, response)
    assert success is False
    assert cache.statistics().errors == 2


@pytest.mark.anyio
async def test_cache_config_validation() -> None:
    config = ProviderCacheConfig(
        enabled=True,
        dynamic_ttl_seconds=10,
        static_ttl_seconds=100,
        max_response_bytes=1000,
        namespace_version="v2"
    )
    assert config.enabled is True

    with pytest.raises(ValidationError):
        ProviderCacheConfig(dynamic_ttl_seconds=0)
    with pytest.raises(ValidationError):
        ProviderCacheConfig(static_ttl_seconds=-1)
    with pytest.raises(ValidationError):
        ProviderCacheConfig(max_response_bytes=0)
    with pytest.raises(ValidationError):
        ProviderCacheConfig(namespace_version="")


@pytest.mark.anyio
async def test_blank_tenant_id_rejected() -> None:
    with pytest.raises(ValidationError):
        ProviderCacheRequest(
            tenant_id="   ",
            provider="groq",
            model="llama-3.3-70b-versatile",
            sanitized_messages=[],
            temperature=0.0,
            max_tokens=10,
            ttl_policy=CacheTTLPolicy.STATIC,
            explicit_safety_verification=True,
        )


@pytest.mark.anyio
async def test_redis_failure_on_delete_and_invalidate() -> None:
    mock_redis = MagicMock()
    mock_redis.delete = MagicMock(side_effect=Exception("Redis delete failed"))
    mock_redis.scan = MagicMock(side_effect=Exception("Redis scan failed"))
    
    cache = ProviderCache(mock_redis)

    req = ProviderCacheRequest(
        tenant_id="tenant-1",
        provider="groq",
        model="llama-3.3-70b-versatile",
        sanitized_messages=[{"role": "user", "content": "hello"}],
        temperature=0.0,
        max_tokens=100,
        ttl_policy=CacheTTLPolicy.STATIC,
        explicit_safety_verification=True,
    )

    res_del = await cache.delete(req)
    assert res_del is False
    assert cache.statistics().errors == 1

    res_inv = await cache.invalidate_provider_namespace(provider="groq")
    assert res_inv == 0
    assert cache.statistics().errors == 2


@pytest.mark.anyio
async def test_exact_byte_boundary_and_one_byte_over(fake_redis) -> None:
    cache_temp = ProviderCache(fake_redis)
    req = ProviderCacheRequest.from_sanitized_payload(
        sanitized_result=make_sanitization_result(data={"data": "test"}),
        provider="groq",
        model="llama-3.3-70b-versatile",
        temperature=0.0,
        max_tokens=100,
        ttl_policy=CacheTTLPolicy.STATIC,
        tenant_id="tenant-1",
    )
    usage = UsageStats(prompt_tokens=5, completion_tokens=5, total_tokens=10, request_count=1, latency_ms=10.0, cost_usd=0.0)
    response = CachedProviderResponse(text="Exact boundary check string", usage=usage)

    exact_length = len(cache_temp.serialize_response(response))

    # 1. Exact byte boundary accepted
    config_exact = ProviderCacheConfig(max_response_bytes=exact_length)
    cache_exact = ProviderCache(fake_redis, config=config_exact)
    assert await cache_exact.set(req, response) is True

    await fake_redis.flushall()

    # 2. One byte less rejected
    config_under = ProviderCacheConfig(max_response_bytes=exact_length - 1)
    cache_under = ProviderCache(fake_redis, config=config_under)
    assert await cache_under.set(req, response) is False


def test_from_sanitized_payload_factory() -> None:
    from app.services.ai.pii_sanitizer import SanitizationResult, PlaceholderMap
    res = SanitizationResult(
        sanitized_data={"customer_name": "{{customer_name}}"},
        placeholder_map=PlaceholderMap(),
        replacement_count=1,
    )
    req = ProviderCacheRequest.from_sanitized_payload(
        sanitized_result=res,
        provider="groq",
        model="llama-3.3-70b-versatile",
        temperature=0.0,
        max_tokens=100,
        ttl_policy=CacheTTLPolicy.STATIC,
        tenant_id="tenant-1",
    )
    assert req.explicit_safety_verification is True
    assert len(req.sanitized_messages) == 1

    message = req.sanitized_messages[0]

    assert message["role"] == "user"

    assert json.loads(message["content"]) == {
        "customer_name": "{{customer_name}}"
    }


def test_provider_model_generation_isolation() -> None:
    cache = ProviderCache(None)

    req1 = ProviderCacheRequest.from_sanitized_payload(
        sanitized_result=make_sanitization_result(
            data="test"
        ),
        provider="groq",
        model="llama-3.3-70b-versatile",
        temperature=0.0,
        max_tokens=100,
        ttl_policy=CacheTTLPolicy.STATIC,
    )

    req2 = ProviderCacheRequest.from_sanitized_payload(
        sanitized_result=make_sanitization_result(
            data="test"
        ),
        provider="groq",
        model="different-model",
        temperature=0.0,
        max_tokens=100,
        ttl_policy=CacheTTLPolicy.STATIC,
    )

    req3 = ProviderCacheRequest.from_sanitized_payload(
        sanitized_result=make_sanitization_result(
            data="test"
        ),
        provider="openai",
        model="llama-3.3-70b-versatile",
        temperature=0.0,
        max_tokens=100,
        ttl_policy=CacheTTLPolicy.STATIC,
    )

    req4 = ProviderCacheRequest.from_sanitized_payload(
        sanitized_result=make_sanitization_result(
            data="test"
        ),
        provider="groq",
        model="llama-3.3-70b-versatile",
        temperature=0.5,
        max_tokens=100,
        ttl_policy=CacheTTLPolicy.DYNAMIC,
    )

    key1 = cache.generate_cache_key(req1)
    key2 = cache.generate_cache_key(req2)
    key3 = cache.generate_cache_key(req3)
    key4 = cache.generate_cache_key(req4)

    assert len(
        {
            key1,
            key2,
            key3,
            key4,
        }
    ) == 4

@pytest.mark.parametrize(
    "raw_value",
    [
        "raw text",
        {"customer": "Ruby"},
        ["raw", "values"],
    ],
)
def test_cache_factory_rejects_raw_values(
    raw_value: object,
) -> None:
    with pytest.raises(
        TypeError,
        match="must be a SanitizationResult",
    ):
        ProviderCacheRequest.from_sanitized_payload(
            sanitized_result=raw_value,  # type: ignore[arg-type]
            provider="groq",
            model="llama-3.3-70b-versatile",
            temperature=0.0,
            max_tokens=100,
            ttl_policy=CacheTTLPolicy.STATIC,
        )
