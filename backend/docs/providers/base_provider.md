# BaseAIProvider Hardening & Interface Documentation

This document describes the design, configuration schemas, error classifications, and adapter logic for the production-hardened `BaseAIProvider` framework within the FieldOps AI architecture.

## Overview
The existing `BaseAIProvider` abstract class is the authoritative interface for all external AI models (such as Groq) in FieldOps Commander. By communicating exclusively through this interface, the system achieves provider independence and testability.

### Preservation of the Synchronous Contract
The legacy synchronous contract (`generate_completion`, `provider_name`, `model_name`, `health_check`) is fully preserved and remains compatible with all existing implementations. This ensures that:
- `GroqProvider`, `GroqClient`, and `ProviderFactory` do not require breaking code changes or direct modification.
- Unit tests mocking or implementing these methods continue to run successfully.

## Asynchronous Adapter
To prevent blocking execution in async environments, `BaseAIProvider` implements a production-grade async wrapper:
```python
async def generate(
    self,
    messages: Sequence[Dict[str, Any]],
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> GenerationResult
```
- **Thread Offloading**: Executes the synchronous `generate_completion` inside `asyncio.to_thread`.
- **Latency Measurement**: Measures the total execution duration in milliseconds and binds it to `UsageStats`.
- **Output Validation**: Rejects empty strings or non-string results by raising a `ProviderExecutionError`.

## Provider Configuration & Health States
Provider configuration is defined as an immutable Pydantic v2 schema `ProviderConfig` supporting:
- `provider_name`, `model_name`, and `api_key_env` (non-blank references).
- `timeout_seconds` and `max_tokens` (positive bounds).
- `temperature` (bounded within `[0.0, 1.0]`).
- `max_retries` (non-negative integer).

### Health Enum
Health status checking is mapped to the async adapter `get_health() -> ProviderHealth` which maps:
- `True -> ProviderHealth.HEALTHY`
- `False -> ProviderHealth.UNHEALTHY`
- Errors/exceptions during check `-> ProviderHealth.UNHEALTHY`

## Usage Stats Contract
`UsageStats` models standard completion usage metadata including prompt/completion/total token counts, request count, latency, and cost. It validates that:
- All fields are non-negative.
- The total tokens count matches exactly `prompt_tokens + completion_tokens`.
- Default implementation in `BaseAIProvider` returns validated zero usage.

## Error & Retry Classification

### Centralized Classification Behavior
All classification is executed deterministically by a single, reusable classifier:
```python
def is_retryable_provider_error(error: BaseException) -> bool
```
- It inspects the error types (e.g. `TimeoutError`, `ConnectionError`, Pydantic `ValidationError`) and HTTP status codes (`error.status_code` or `error.response.status_code`).
- **Unknown exceptions** (e.g. `RuntimeError`, `ValueError`) default to **non-retryable** (`False`).

### Retryability Table
| Error / Exception Case | Retryable? | Classification Rules / Actions |
|---|---|---|
| Timeout / Network Connection Errors | **Yes** | Classified as retryable |
| HTTP 429 (Rate Limit) | **Yes** | Classified as retryable |
| HTTP 5xx (Server Error) | **Yes** | Classified as retryable |
| HTTP 400 (Bad Request) | **No** | Non-retryable |
| HTTP 401 (Unauthorized) | **No** | Non-retryable |
| HTTP 403 (Forbidden) | **No** | Non-retryable |
| Pydantic ValidationError | **No** | Non-retryable |
| ProviderCapabilityError | **No** | Non-retryable |
| ProviderConfigurationError | **No** | Non-retryable |
| Unknown Exception / Errors | **No** | Non-retryable |

### Safe Exception Chaining Policy
To protect private data and backend internals, `BaseAIProvider.generate()` does not chain the original exception to the public `ProviderExecutionError`. It explicitly uses `from None`:
```python
raise ProviderExecutionError(
    "AI provider execution failed.",
    status_code=status_code,
    is_retryable=is_retryable,
) from None
```

### No Sensitive Exception Text Policy
To prevent leaks of key values or customer PII, logs and public error messages must never contain:
- Original provider exception text
- User prompts or prompt templates
- Generated model outputs
- API keys, secrets, or tokens
- Request headers or authentication meta
- Raw provider response payloads

## Unsupported Embedding Behavior
Because the Groq chat provider does not support embeddings, the async `embed()` default adapter raises a typed `ProviderCapabilityError` rather than returning fake vectors.

## Factory and Future Hardening
- `ProviderFactory` remains authoritative in `provider_factory.py`.
- Concrete Groq provider integration and usage tracking will be hardened in Task 4.2.
