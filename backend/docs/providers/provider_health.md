# Provider Health Monitor & Redis State (Task 4.4A)

## Overview

The `ProviderHealthMonitor` service monitors background availability and health state across configured AI providers (`Groq`, `OpenAI`, `Anthropic`, etc.) without interfering with live AI request execution. State is stored in Redis for distributed visibility across server nodes and workers.

## Architectural Boundaries

### Provider Health Monitor vs. CircuitBreaker

| Dimension | Provider Health Monitor (`ProviderHealthMonitor`) | Circuit Breaker (`CircuitBreaker`) |
| :--- | :--- | :--- |
| **Primary Scope** | Background periodic availability & health probing | Live generation request gating & fast failure prevention |
| **States** | `HEALTHY`, `DEGRADED`, `UNHEALTHY` | `CLOSED`, `OPEN`, `HALF_OPEN` |
| **Check Trigger** | Periodic check loop (30s default) or admin inspection | On every incoming user AI request (`check_permission`) |
| **Recovery Mechanism**| 5-minute recovery probe timer (`next_recovery_probe_at`) | Concurrent probe locks in `HALF_OPEN` state |
| **Responsibility** | Provider routing decisions & background health state | Instant execution permits & circuit trip protection |

## Configuration & Enablement (`config.enabled`)

- When `config.enabled` is `True` (default), automatic health monitoring runs every 30 seconds.
- When `config.enabled` is `False`:
  - `start()` returns immediately without creating background tasks.
  - `run_once()` and `check_registered_providers()` return empty lists without instantiating providers or writing to Redis.
  - Explicit administrative `check_provider(name)` calls remain available.

## Health States & Transitions

1. **`HEALTHY`**:
   - Provider is reachable and responding successfully to health checks.
   - Consecutive failures counter is `0`.
2. **`DEGRADED`**:
   - Provider has failed `degraded_after_failures` consecutive check(s) (default: `1`).
   - Provider may still handle requests, but alerts/logging track degraded performance.
3. **`UNHEALTHY`**:
   - Provider has reached `unhealthy_after_failures` consecutive checks (default: `3`).
   - `next_recovery_probe_at` is set to `now + 300 seconds` (5 minutes).
   - Probing is suppressed until `next_recovery_probe_at` is reached.

## Alert Callbacks

- Alerts trigger **only on meaningful state transitions**:
  - Transition to `DEGRADED`.
  - Transition to `UNHEALTHY`.
  - Recovery from `DEGRADED` or `UNHEALTHY` back to `HEALTHY`.
- Alerts do NOT trigger repeatedly for consecutive checks in the same state.

## Redis Key Strategy & Persistence

- **Key Format**:
  `fieldops:provider-health:{namespace_version}:{sha256_provider_name}`
- **Provider Name Hashing**: Provider names are normalized (lowercased & trimmed) and hashed with SHA-256 to guarantee key safety and avoid exposing environment details.
- **Payload Format**: Snapshots are serialized as immutable JSON payloads containing timestamp, counters, and health status.
- **TTL**: Snapshots are persisted with `state_ttl_seconds: 900` (15 minutes).

## Fail-Closed Infrastructure Policy

- If Redis infrastructure is unreachable or fails during operations, `ProviderHealthInfrastructureError` is raised immediately.
- `check_registered_providers()` and `list_snapshots()` re-raise `ProviderHealthInfrastructureError` rather than returning partial health data or ignoring database errors.
- Infrastructure warnings log fixed safe strings without exposing raw Redis stack traces or connection strings.

## Privacy & Security

Log entries and Redis snapshots strictly adhere to privacy rules:
- **Never Logged / Stored**: API keys, credentials, raw exception traces, prompts, customer data, or model responses.
- **Allowed Attributes**: Normalized provider name, safe error code (e.g. `PROVIDER_HEALTH_CHECK_FAILED`), health state enum, counters, and strict UTC timestamps.

## Lifecycle & Deferred Work

- **Scheduler Lifecycle**: Background monitoring loops (`start()`, `stop()`, `run_once()`) do NOT execute side-effects on module import. Loop startup is deferred to application lifespan handlers (Task 4.4C).
- **Runtime Generation Failover**: Execution flow failover and dynamic provider switching belong to **Task 4.4B**.
- **AIOrchestrator Integration**: Direct orchestration integration belongs to **Task 4.4C**.
