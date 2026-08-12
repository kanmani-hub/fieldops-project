# Provider Response Cache

This document describes the design, key generation, serialization, and invalidation strategies for the `ProviderCache`.

---

## Overview

The `ProviderCache` is an async-safe cache layer for AI provider responses. It caches successful completions to minimize latency and token expenditure, while enforcing strict tenant isolation and input sanitization policies.

---

## Configuration Schema

Cache parameters are defined via the Pydantic v2 `ProviderCacheConfig` schema:

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | `bool` | `True` | Master enable/disable switch |
| `dynamic_ttl_seconds` | `int > 0` | `60` | TTL when `ttl_policy == CacheTTLPolicy.DYNAMIC` |
| `static_ttl_seconds` | `int > 0` | `3600` | TTL when `ttl_policy == CacheTTLPolicy.STATIC` |
| `max_response_bytes` | `int > 0` | `10,485,760` | Maximum cached payload size (10 MB) |
| `namespace_version` | `str` | `"v1"` | Redis key namespace version |

---

## TTL Policy (`CacheTTLPolicy`)

TTL is explicitly selected via `CacheTTLPolicy`:
- `CacheTTLPolicy.DYNAMIC`: Uses `dynamic_ttl_seconds` (default 60s) for non-deterministic or frequent updates.
- `CacheTTLPolicy.STATIC`: Uses `static_ttl_seconds` (default 3600s) for static, highly deterministic responses.

---

## Controlled Request Construction (`ProviderCacheRequest`)

To guarantee safety, cache requests are represented by the immutable `ProviderCacheRequest` schema.

Preferred construction is via the controlled factory:
```python
request = ProviderCacheRequest.from_sanitized_payload(
    sanitized_result=sanitization_result,
    provider="groq",
    model="llama-3.3-70b-versatile",
    temperature=0.0,
    max_tokens=4096,
    ttl_policy=CacheTTLPolicy.STATIC,
    tenant_id="tenant-123",
)
```

The factory accepts a typed `SanitizationResult` or safe sanitized data wrapper, ensuring raw user inputs cannot bypass PII verification.

---

## Key Generation & Privacy

Cache keys are generated deterministically using a SHA-256 hash of canonical sorted serialized parameters:

- Cache schema version
- Safe tenant hash (SHA-256 of `tenant_id`, or `"global"`)
- Provider name
- Model name
- Sanitized prompt messages list (JSON-serialized, keys sorted)
- Temperature
- Max tokens
- TTL Policy

Dict parameter sorting ensures key order independence — the same logical request always maps to the same cache key regardless of dict insertion order.

**Privacy guarantees:**
- No raw prompts, messages, PII, API keys, or raw tenant values are placed in Redis keys.
- Tenant isolation is fully enforced (each tenant has a unique SHA-256 sub-key).
- Blank `tenant_id` values are rejected with a `ValueError`.

---

## Fail-Open Policy

To prevent cache backend failures from blocking core provider functionality:

- All cache lookups, storage, deletion, and invalidations **fail open**.
- If a Redis error occurs during `get` or `set`, it is caught, recorded in the cache statistics counter, and treated as a cache miss — the provider call proceeds normally.
- Redis exception details are never surfaced to the caller or logged raw.

---

## Oversized Response Rejection

If a response payload exceeds `max_response_bytes`, the `set` operation is rejected silently (logged as a warning) and returns `False`. Payloads equal to or under `max_response_bytes` are accepted.

---

## Invalidation & SCAN Strategy

To remove cached items cleanly without degrading Redis performance:

- `invalidate_provider_namespace(provider, tenant_id)` removes all cache keys for a given provider/tenant combination.
- Keys are scanned using Redis `SCAN` (with a count of 100 per iteration) instead of `KEYS`, avoiding server locking in production workloads.
- This operation is incremental (non-blocking) and non-atomic.

---

## Redis Key Format

```
fieldops:cache:{version}:{tenant_hash}:{provider}:{payload_hash}
```

All segments are lowercase and contain no raw user data.
