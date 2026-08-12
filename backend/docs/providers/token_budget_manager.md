# Token Budget and Rate Limit Manager

This document describes the design, configuration schemas, atomic concurrency handling, and exception logic for the `TokenBudgetManager` framework.

---

## Overview

The `TokenBudgetManager` (async) and `SyncTokenBudgetManager` (sync) are the authoritative classes responsible for checking, reserving, reconciling, and cancelling AI token budgets and request rate limits. They are designed for high-concurrency environments using Redis as the single source of truth.

---

## Configuration Schema

Budget constraints are defined via the immutable Pydantic v2 `TokenBudgetConfig` schema:

| Field | Type | Default | Description |
|---|---|---|---|
| `daily_token_limit` | `int > 0` | `1,400,000` | Max total tokens per day (global, all tenants) |
| `requests_per_minute` | `int > 0` | `20` | RPM hard limit |
| `daily_request_limit` | `int > 0` | `1,500` | Max requests per day |
| `namespace_version` | `str` | `"v1"` | Redis key namespace version |
| `atomic_strategy` | `"lua"` \| `"transaction"` | `"lua"` | Concurrency strategy |
| `reservation_ttl_seconds` | `int > 0` | `3600` | TTL for reservation records |
| `per_request` | `Dict[str, int]` | see below | Per-category max output token limits |

**Default `per_request` limits:**

| Category | Default Max Output Tokens |
|---|---|
| `sms` | 80 |
| `email` | 500 |
| `push` | 200 |
| `portal` | 200 |
| `sentiment` | 10 |
| `general` | 4,096 |

---

## API Reference

### `reserve(estimated_input_tokens, max_output_tokens, category, provider, model, tenant_id?)`

Atomically reserves tokens before an AI provider call. Returns a `reservation_id` string on success.

**Per-request limit check**: `max_output_tokens` is validated against `config.per_request[category]` *before* any Redis access. If `max_output_tokens > per_request_limit`, a `BudgetExceededError` is raised immediately (no Redis round-trip).

**Total tokens reserved**: `estimated_input_tokens + max_output_tokens`.

```python
reservation_id = await manager.reserve(
    estimated_input_tokens=120,
    max_output_tokens=80,
    category="sms",
    provider="groq",
    model="llama-3.3-70b-versatile",
    tenant_id="tenant-abc",
)
```

### `reconcile(reservation_id, actual_input_tokens, actual_output_tokens, provider=None)`

Closes a reservation by computing the delta between actual and reserved token counts and adjusting the daily counter atomically. Raises `BudgetExceededError` if actual usage exceeds the reservation. Idempotent for already-reconciled reservations. Derives daily counter window from the stored reservation record.

### `cancel(reservation_id, provider=None)`

Releases reserved tokens back to the daily budget. Idempotent for already-cancelled reservations. Raises `ValueError` if the reservation was already reconciled. Derives daily counter window from the stored reservation record.

### `check(estimated_input_tokens, max_output_tokens, category, provider, model, tenant_id?)`

Non-mutating budget availability check. Returns a `BudgetDecision` without incrementing any counters.

### `remaining(provider, model, tenant_id?)` → `Tuple[int, int]`

Returns `(remaining_daily_tokens, remaining_daily_requests)`.

### `usage(provider, model, tenant_id?)` → `BudgetUsage`

Returns a snapshot of `daily_tokens_used`, `daily_requests_used`, and `rpm_used`.

---

## Atomic Concurrency Strategies

Two atomic strategies are selectable via `atomic_strategy` in `TokenBudgetConfig`:

1. **`lua`** (default, production): All limit checks and counter increments are executed atomically on the Redis server via a single Lua script. Zero round-trips between check and increment.

2. **`transaction`** (test/fallback): Uses Redis `WATCH`/`MULTI`/`EXEC` optimistic locking with up to 10 automatic retries on `WatchError`. Required when the Redis environment does not support Lua evaluation (e.g. `fakeredis` without `lupa`).

---

## Redis Key Design

Global budget keys are scoped by **provider account** and **date/minute window**. They are intentionally shared across all tenants and models to enforce a true global quota:

```
fieldops:budget:{version}:{provider}:tokens:daily:{YYYYMMDD}
fieldops:budget:{version}:{provider}:requests:daily:{YYYYMMDD}
fieldops:budget:{version}:{provider}:requests:rpm:{YYYYMMDDHHmm}
fieldops:budget:{version}:reservation:{reservation_id}
```

- Daily keys expire at midnight UTC.
- RPM keys expire after 60 seconds.
- Reservation keys expire after `reservation_ttl_seconds`.
- No raw tenant IDs, PII, prompts, or model names are stored in Redis keys.

---

## Tenant Isolation & Privacy

- `tenant_id` (when provided) is SHA-256 hashed before use in reservation records.
- The global budget counters are intentionally **not** per-tenant — all tenants share the same daily limits.
- Raw tenant values are never persisted in any Redis key.

---

## Fail-Closed Infrastructure Policy

Any Redis connection or command error during `reserve()` or `check()` raises a typed `BudgetInfrastructureError`, blocking the AI call. No raw Redis exception details are exposed in public messages or logs.

---

## Exception Hierarchy

| Exception | When raised |
|---|---|
| `BudgetExceededError` | Daily token, RPM, or daily request limit exceeded; per-request category limit exceeded |
| `BudgetInfrastructureError` | Redis connection failure, script evaluation error, or transaction timeout |
| `ValueError` | Invalid parameters (blank provider, negative tokens, blank tenant, etc.) |
