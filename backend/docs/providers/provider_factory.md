# Provider Factory (`ProviderFactory`)

This document describes the design, dynamic registration rules, provider loading, fallback ordering, hot config reloading, and architectural boundaries for `ProviderFactory`.

---

## Overview

The `ProviderFactory` serves as the centralized, thread-safe provider registry and instantiation engine for FieldOps Commander AI. Components such as `GroqClient` request AI providers through `ProviderFactory.create_provider()`, insulating the rest of the application from specific provider implementations.

---

## Registry Responsibilities & Rules

1. **Thread Safety**: All registry mutations and lookups are protected using a reentrant lock (`threading.RLock`).
2. **Stable Sorted Output**: `registered_names()` returns provider names in sorted alphabetical order.
3. **Name Normalization & Strict Validation**:
   - Provider names are automatically converted to lowercase with surrounding whitespace stripped (`name.strip().lower()`).
   - Explicit blank names (`""`, `"   "`) or non-string values (e.g. integers) are strictly rejected with `ProviderConfigurationError`.
   - Missing, non-string, or blank `config.provider_name` configuration values are rejected with `ProviderConfigurationError` (fails closed; does not default malformed configuration to `"groq"`).
4. **Subclass Enforcement**: Registered classes must extend `BaseAIProvider`. Non-subclasses are rejected with `ProviderConfigurationError`.
5. **Idempotent Registration**: Registering the same name with the exact same provider class is safe and idempotent.
6. **Conflict Prevention**: Attempting to register an existing name with a *different* class raises `ProviderConfigurationError` unless `replace=True` is explicitly passed.
7. **No Decorator Auto-Registration**: Providers are explicitly registered (such as the built-in `GroqProvider` registered as `"groq"`) to prevent circular import side-effects.

---

## Provider Creation & Configuration

- `create_provider(config=None, name=None, provider_kwargs=None)`:
  - If `config` is omitted, reads a fresh `ConfigLoader()` instance.
  - If `name` is omitted, requires and uses `config.provider_name`.
  - Looks up the class in the shared registry and instantiates `provider_class(config=config, **provider_kwargs)`.
  - **No Singleton Caching**: `create_provider()` creates new provider instances on demand to ensure thread isolation and prevent state bleed across callers.
  - **Safe Error Mapping**: Unknown provider names or constructor exceptions raise `ProviderConfigurationError` with clean fixed error messages. Internal stack traces or raw details are never exposed to public error messages or logs.

---

## Fallback Ordering (`get_fallback_chain`)

- Reads `provider.fallback_order` from `ai.yaml` via `ConfigLoader.provider_fallback_order`.
- If `fallback_order` property is missing or `None`, defaults to `[provider_name]`.
- **Fails Closed on Malformed Configuration**: Invalid or malformed fallback configurations re-raise `ProviderConfigurationError` rather than swallowing errors.
- Normalizes provider names and removes duplicates while preserving configuration order.
- `get_fallback_chain()` instantiates each configured provider in order and verifies `health_check()`. Unhealthy or uncreatable providers are safely logged with fixed messages and skipped.
- Currently, production configuration specifies:
  ```yaml
  provider:
    name: groq
    fallback_order:
      - groq
  ```

---

## Hot Configuration Reloading

- `reload_config()`: Loads a fresh `ConfigLoader` instance and instantiates and returns a new `BaseAIProvider` instance selected by the newly loaded `ai.yaml`.
- Returns `BaseAIProvider`, not `ConfigLoader`. Does not modify existing provider instances or create singletons.

---

## Safe Health Logging

- In `get_healthy_providers()` and `get_fallback_chain()`, initialization and health check failures log fixed warning messages containing only the normalized provider name (`logger.warning(...)`).
- Raw exception text and stack traces are never logged.

---

## Boundary Between Task 4.3 and Task 4.4

- **Task 4.3 (This Component)**: Provides the registry, strict provider validation, provider instantiation, synchronous health checks, and fallback chain inspection API (`get_fallback_chain()`).
- **Task 4.4 (Deferred)**: Will implement automatic runtime execution failover across providers, background 30-second health monitoring schedulers, and Redis health status persistence.
- **Current Status**: Only `GroqProvider` (`"groq"`) is built-in. OpenAI, Anthropic, and Ollama providers remain deferred to future tasks.
