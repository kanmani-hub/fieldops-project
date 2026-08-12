# Hardened Groq AI Provider (`GroqProvider`)

This document describes the design, configuration, retry behavior, deadline management, usage tracking, health checks, and architectural boundaries for `GroqProvider`.

---

## Overview

The `GroqProvider` is a concrete implementation of `BaseAIProvider` connecting to the Groq LPU Infrastructure. It is hardened for production reliability with strict model restrictions, logical deadlines, 429 rate limit backoff retries, thread-safe usage metrics, and typed exception mapping.

---

## Model Restriction

- **Allowed Model**: `llama-3.3-70b-versatile`
- Requests or configurations specifying any other model raise `ProviderConfigurationError`.
- Defined via module-level constant `ALLOWED_MODEL = "llama-3.3-70b-versatile"`.

---

## Deadline Management & Timeout

- Enforces a **5-second overall logical call deadline**.
- All internal SDK requests, 429 retry attempts, and backoff delays must fit within the overall 5-second deadline.
- Remaining deadline is passed to the SDK request via `timeout=remaining_time`.
- If the 5-second deadline expires before completion, `ProviderExecutionError("AI provider execution timed out.", is_retryable=True)` is raised.

---

## Retry Strategy (HTTP 429 Rate Limits)

Retries are strictly limited to **HTTP 429 (Rate Limit Exceeded)** responses:
1. **Initial Attempt**: Executed at $t = 0$.
2. **First Retry**: If 429 received, sleeps 1.0 second and retries (if remaining deadline $> 1.0$s).
3. **Second Retry**: If 429 received again, sleeps 2.0 seconds and retries (if remaining deadline $> 2.0$s).
- **Maximum total HTTP attempts**: 3.
- Non-429 errors (400, 401, 403, 5xx, timeouts, configuration/validation errors) are **NOT** internally retried by `GroqProvider`.

---

## Error Mapping & Typing

| Exception / Condition | Status Code | `is_retryable` | Exception Raised |
|---|---|---|---|
| Missing `GROQ_API_KEY` or invalid model | None | False | `ProviderConfigurationError` |
| HTTP 401 Unauthorized | 401 | False | `ProviderExecutionError` |
| HTTP 400 Bad Request, HTTP 403 Forbidden | 400/403 | False | `ProviderExecutionError` |
| HTTP 429 Rate Limit Exceeded (exhausted) | 429 | True | `ProviderExecutionError` |
| HTTP 5xx Server Errors | 500-599 | True | `ProviderExecutionError` |
| Timeout / Connection Failure | None | True | `ProviderExecutionError` |
| Empty or malformed response | None | False | `ProviderExecutionError` |

*Logging Safety:* Logs use fixed warning messages (`logger.warning`). Raw exception strings, stack tracebacks (`logger.exception`), API keys, prompts, messages, and customer PII are never logged.

---

## Usage Metrics & Thread Safety

- **Per-Call Usage** (`GenerationResult.usage`):
  - `prompt_tokens`: Tokens in input prompt.
  - `completion_tokens`: Tokens in output completion.
  - `total_tokens`: Total tokens.
  - `request_count`: Total HTTP attempts performed during this logical call (1..3).
  - `latency_ms`: Measured logical call duration in milliseconds.
  - `cost_usd`: Fixed at `0.0`.
- **Cumulative Usage** (`GroqProvider.get_usage()`):
  - Returns thread-safe snapshot of total tokens and total HTTP request attempts across all calls on the provider instance using a `threading.Lock`.
- **Fallback**: If Groq API response omits usage details, tokens default safely to 0 without raising errors.

---

## Health Check & Models Endpoint

- `health_check()` queries the Groq models endpoint (`client.models.list()`) instead of running expensive chat completions.
- `get_models()` returns available model IDs from the endpoint.
- `health_check()` returns `True` only when `llama-3.3-70b-versatile` is present in the models endpoint response. Returns `False` on any network failure or missing model.

---

## Architectural Boundaries

- **PII Sanitization**: Performed by `AIOrchestrator` and `PIISanitizer` before reaching `GroqProvider`. `GroqProvider` receives sanitized messages only.
- **Placeholder Restoration**: Handled locally by `AIOrchestrator`.
- **Token Budget & Quotas**: Enforced globally by `TokenBudgetManager`. Actual token counts from `GenerationResult.usage` are passed to `reconcile()`.
- **Circuit Breaker**: `CircuitBreaker` receives `ProviderExecutionError` from `AIOrchestrator` to track circuit health.
- **Fallback Engine**: CommsAgent / `CommunicationService` owns Jinja2 fallback logic. `GroqProvider` contains no Jinja2 rendering.
- **Deferred Integrations**: `ProviderCache` and multi-provider failover (Tasks 4.3 & 4.4) remain outside `GroqProvider`.
