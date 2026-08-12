# Provider Failover Executor (Task 4.4B)

## Overview

The `ProviderFailoverExecutor` service provides provider selection and fallback iteration across configured AI providers when AI generation calls experience retryable failures.

## Architectural Boundaries & Separation

| Component | Responsibility | Boundary in Task 4.4B |
| :--- | :--- | :--- |
| **`ProviderFactory`** | Class registration and provider instantiation | Used by executor to create provider instances by name. |
| **`ProviderHealthMonitor`** | Background health checking & Redis state tracking | Used to filter out `UNHEALTHY` candidate providers. |
| **`CircuitBreaker`** | Per-provider request permit gating & sliding failure tracking | Integrated in `attempt_runner` callback in Task 4.4C. |
| **`ProviderFailoverExecutor`**| Fallback ordering iteration & retryable failover execution | Focuses strictly on candidate resolution & failover iteration. |

## Fallback Validation Rules

- `provider_fallback_order` must be a list or tuple of non-blank strings.
- Non-string elements (e.g. integers, `None`, booleans) or blank strings raise `ProviderConfigurationError`.
- Duplicate normalized names are removed while preserving order.

## Candidate Selection & Health Integration

1. **Configured Order**: Reads `provider.fallback_order` from `ai.yaml` via `ConfigLoader`.
2. **Eligibility Filtering**:
   - `HEALTHY` and `DEGRADED` providers are eligible for execution.
   - `UNHEALTHY` providers are skipped unless `should_probe(name)` returns `True`.
3. **Skipped Unhealthy Metric**: `skipped_unhealthy_providers` increments ONLY when an `UNHEALTHY` provider is skipped (or fails its recovery probe). It does NOT increment for unknown providers, configuration errors, or constructor failures.
4. **Fail-Closed Policy**: If `ProviderHealthMonitor` encounters a `ProviderHealthInfrastructureError` (Redis failure), execution fails closed immediately and re-raises the error.

## Execution Rules: Retryable vs. Non-Retryable Failures

- **Retryable Failure** (`is_retryable=True`):
  - Recorded as a failed attempt.
  - Failover continues to the next candidate provider in the fallback chain.
  - If all candidate providers fail retryably, `ProviderFailoverExhaustedError` is raised.
- **Non-Retryable Failure** (`is_retryable=False`):
  - Recorded as a failed attempt.
  - **Execution stops immediately**.
  - Re-raises a fixed safe `ProviderExecutionError` without attempting subsequent providers.
  - Increments `total_executions` once via centralized completed-execution metrics helper.
- **Unexpected Exceptions**:
  - Mapped to non-retryable `ProviderExecutionError("AI provider execution failed.")`.
  - **Execution stops immediately**.

## Completed Execution Metrics

- Every completed execution (primary success, failover success, exhaustion, non-retryable failure, unexpected failure) records `total_executions`, `total_attempts`, and `total_execution_latency_ms` exactly once.
- `ProviderConfigurationError` and `ProviderHealthInfrastructureError` propagate before execution completion without updating outcome metrics.

## Attempt Validation Rules

- `FailoverAttempt` enforces consistency:
  - `attempted` and `skipped` cannot both be `True`.
  - `succeeded` requires `attempted=True` and `skipped=False`.
  - `status_code` must be an integer between 100 and 599 (booleans rejected).

## Alerts & Metrics

- **Failover Alerts**: Optional `alert_callback` is invoked when:
  1) A secondary provider succeeds after primary failure/skip (`event_type="failover_success"`).
  2) All providers are exhausted (`event_type="failover_exhausted"`).
- **Metrics**: `get_metrics()` returns `ProviderFailoverMetrics`.
