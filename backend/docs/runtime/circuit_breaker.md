# Distributed AI Provider Circuit Breaker

This document describes the design, state machine, Redis schema, failure classification, and integration workflow for the `CircuitBreaker`.

---

## Overview

The `CircuitBreaker` protects the FieldOps backend and external AI providers (such as Groq) from cascading failures and wasted resource consumption during provider outages. It is implemented as a shared, Redis-backed state machine.

---

## State Machine & Transitions

```
               [CLOSED]
               /      ^
    5 failures/        \ 3 consecutive
   in 60s    /          \ successes
            v            \
         [OPEN] --------> [HALF_OPEN]
               300s cooldown
               (1 probe lock)
```

1. **CLOSED**:
   - Provider calls are permitted.
   - Failures are recorded in a sliding 60-second window.
   - Prunes failure history older than 60 seconds.
   - Transition to **OPEN** occurs atomically when 5 retryable failures occur within the 60-second window.
   - Successful completions prune stale failure history.

2. **OPEN**:
   - Rejects all provider calls immediately with a `CircuitOpenError`.
   - **Does not reserve token budget** or execute provider calls.
   - `CircuitOpenError` propagates directly to the caller without wrapping.
   - Remains OPEN for a 300-second (5 minute) cooldown.
   - Transition to **HALF_OPEN** occurs automatically after the 300-second cooldown expires.

3. **HALF_OPEN**:
   - Permits exactly **one concurrent probe request** at a time using a cryptographically random token (`token = secrets.token_hex(16)`).
   - `check_permission()` returns an immutable `CircuitPermit` holding `is_half_open_probe=True` and `probe_token`.
   - Lock release uses atomic compare-and-delete (Lua script):
     ```lua
     if redis.call("get", KEYS[1]) == ARGV[1] then
         return redis.call("del", KEYS[1])
     else
         return 0
     end
     ```
   - Lock is safely released on **every execution path**:
     - Budget reservation failure
     - Retryable provider failure (reopens to OPEN)
     - Non-retryable provider failure (lock released, remains HALF_OPEN)
     - Invalid provider response
     - Success (increments consecutive successes towards CLOSED)
     - Unexpected exception
   - Rejects all other incoming calls with a `CircuitOpenError` while the probe lock is active.
   - If **3 consecutive probe requests succeed**, the circuit transitions back to **CLOSED**.

---

## Configuration Schema (`CircuitBreakerConfig`)

Configured under `provider.circuit_breaker` in `ai.yaml`:

| Parameter | Type | Default | Description |
|---|---|---|---|
| `enabled` | `bool` | `True` | Master toggle switch for circuit breaker enforcement |
| `failure_threshold` | `int > 0` | `5` | Retryable failures required within window to trip circuit |
| `failure_window_seconds` | `int > 0` | `60` | Sliding window duration for counting failures |
| `open_cooldown_seconds` | `int > 0` | `300` | Cooldown period before transitioning from OPEN to HALF_OPEN |
| `half_open_success_threshold` | `int > 0` | `3` | Consecutive successes required in HALF_OPEN to close circuit |
| `half_open_max_concurrent_probes` | `int == 1` | `1` | Must be exactly 1; validated on schema initialization |
| `namespace_version` | `str` | `"v1"` | Version tag for Redis keys |
| `half_open_probe_ttl_seconds` | `int > 0` | `180` | TTL of the owned HALF_OPEN probe lock |

---

## Retryable Failure Classification

Failures are filtered using `is_retryable_provider_error()` from `base_provider.py`:

**Counted as Circuit Failures:**
- `TimeoutError` and `ConnectionError`
- HTTP 429 (Rate Limit Exceeded)
- HTTP 5xx (Internal Server / Bad Gateway / Service Unavailable)
- `ProviderExecutionError` with `is_retryable=True`

**Not Counted (Do Not Trip Circuit):**
- Configuration errors (`ProviderConfigurationError`)
- Capability errors (`ProviderCapabilityError`)
- Input validation errors (`ValidationError`)
- HTTP 400, 401, 403
- Circuit-open rejections (`CircuitOpenError`)
- Budget exceeded / infrastructure failures (`BudgetExceededError`, `BudgetInfrastructureError`)
- Cache failures
- Response parsing or PII restoration failures occurring *after* a successful provider response

*Note:* In `HALF_OPEN` state, non-retryable errors still release the active probe lock, but do not reopen the circuit.

---

## Token Budget & Orchestrator Execution Ordering

The circuit breaker check occurs **before** token budget reservation in `AIOrchestrator`:

1. Build static system instructions and sanitize context.
2. Build sanitized user prompt.
3. **Check CircuitBreaker Permission** (`permit = circuit_breaker.check_permission(provider)`).
   - *If OPEN or HALF_OPEN probe locked:* raises `CircuitOpenError` directly to caller. **Token budget is NOT reserved.**
4. **Reserve Token Budget** (`token_budget_manager.reserve(...)`).
   - *If budget reservation fails:* probe lock is safely released via `release_probe_lock(permit)`.
5. **Call Provider** (`client.generate(...)`).
6. **On Provider Response:**
   - On Success: record circuit success (`record_success(permit)`), then reconcile token budget.
   - On Provider Failure: record circuit failure (`record_failure(permit, provider_err)`), then cancel active token reservation.

---

## Redis Design & Privacy Guarantees

Redis keys are constructed using SHA-256 hashes of provider names:
```
fieldops:circuit:{version}:{provider_hash}:state
fieldops:circuit:{version}:{provider_hash}:failures
fieldops:circuit:{version}:{provider_hash}:successes
fieldops:circuit:{version}:{provider_hash}:opened_at
fieldops:circuit:{version}:{provider_hash}:probe_lock
```

**Privacy & Isolation Rules:**
- Circuit state is global across tenants and models for a given provider (e.g. `groq`).
- Keys contain NO prompts, messages, PII, API keys, raw tenant IDs, or raw provider names.
- Probe locks are owned by specific permit tokens (`token_hex(16)`). Lock release compares token to prevent releasing another request's lock.

---

## Safe Logging Policy

All log outputs from `CircuitBreaker` use fixed string templates (`logger.warning` / `logger.error`):
- Log messages never contain raw exception text (`str(exc)`), stack tracebacks (`logger.exception`), Redis keys, connection details, or customer data.
- Exceptions rely on bare `raise` or `from None` chaining to prevent leaking internal context.

---

## Fallback Boundary & Deferred Integrations

- If the circuit is OPEN, `AIOrchestrator` raises `CircuitOpenError` directly. Callers can inspect the exception and execute existing fallback / manual-review workflows.
- Integration with `ProviderCache` and Story 3.4 Metrics collector is deferred to subsequent stories.



A stale or expired permit cannot release a newer probe lock,
increment HALF_OPEN successes, or reopen the circuit.
Probe completion and ownership validation are performed
atomically in Redis using Lua.