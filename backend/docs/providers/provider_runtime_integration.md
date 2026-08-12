# Provider Runtime Integration (Task 4.4C)

## Complete Runtime Execution Flow

```
API / Backend Caller
   │
   ▼
AIOrchestrator.execute()
   │
   ├─► 1. Build System & Task Prompts (ONCE)
   ├─► 2. Sanitize PII & Context (ONCE)
   ├─► 3. Build User Prompt from Sanitized Context (ONCE)
   ├─► 4. Run Final Prompt Scan & Initialize Placeholder Map (ONCE)
   │
   ▼
ProviderFailoverExecutor.execute(attempt_runner)
   │
   ├──► Attempt Candidate Provider (Order: fallback_order)
   │       │
   │       ├─► Check Provider Health Eligibility (ProviderHealthMonitor)
   │       │
   │       └─► Call Injected attempt_runner(provider_name, provider)
   │             │
   │             ├─► 1. Check Circuit Breaker Permission (CircuitBreaker)
   │             │      - CircuitOpenError -> retryable ProviderExecutionError (triggers failover)
   │             │
   │             ├─► 2. Reserve Token Budget (SyncTokenBudgetManager)
   │             │      - Reserve Failure -> release probe lock & stop / failover
   │             │
   │             ├─► 3. Instantiate Provider Client (GroqClient)
   │             │
   │             ├─► 4. Call BaseAIProvider.generate_result()
   │             │      - Provider Exception -> cancel reservation & record circuit failure
   │             │
   │             └─► 5. Provider Success -> record circuit success & reconcile token budget
   │
   ├──► Retryable Failure? -> Try Next Candidate Provider in fallback_order
   │
   └──► Failover Succeeded! (Returns GenerationResult)
   │
   ▼
AIOrchestrator Post-Processing
   │
   ├─► 5. Restore Placeholders Locally
   ├─► 6. Validate Response Schema (ResponseParser)
   └─► 7. Clear Placeholder Map (finally block)
```

## Fallback Configuration & Jinja2 Boundary

- **Production Configuration**: `ai.yaml` configures `fallback_order: ["groq"]`. Adding secondary fallback providers (e.g. OpenAI, Anthropic) requires registering the provider class in `ProviderFactory` and updating `provider.fallback_order`.
- **Jinja2 Fallback**: If all configured AI providers are exhausted (`ProviderFailoverExhaustedError`), template rendering fallback (e.g., Jinja2 static text fallback) is handled in the business workflow layer outside the AI provider and orchestration infrastructure.

## Health Monitor Lifespan Lifecycle

- The `ProviderHealthMonitor` background monitoring loop (`start()` / `stop()`) is managed via FastAPI's `@asynccontextmanager` `lifespan` handler in `app/main.py`.
- **Startup**: `await ai_orchestrator.provider_health_monitor.start()` executes after infrastructure services are ready.
- **Shutdown**: `await ai_orchestrator.provider_health_monitor.stop()` executes cleanly before closing Redis and closing database connections.
- Startup and shutdown are fully idempotent. Zero background tasks or event loops are created at module import.

## Permit and Reservation Finalization Rules

Every `CircuitPermit` and token budget reservation must be finalized **exactly once**:

1. **Successful Attempt**:
   - `circuit_breaker.record_success(permit)` finalizes the circuit permit.
   - `token_budget_manager.reconcile(reservation_id, ...)` finalizes the budget reservation.
   - Reservation is never cancelled after reconciliation.

2. **Failed Provider Attempt**:
   - `token_budget_manager.cancel(reservation_id, ...)` cancels the budget reservation.
   - `circuit_breaker.record_failure(permit, error)` records circuit failure and releases any HALF_OPEN probe lock.

3. **Pre-Execution Failures (e.g., Budget Reservation or Client Creation Error)**:
   - `circuit_breaker.release_probe_lock(permit)` safely releases probe lock without recording false circuit failures or double-releasing locks.
