# Planning Agent Migration Documentation

## Previous PlanningAgent Design

Before migration, the `PlanningAgent` was a standalone, unmanaged class with the following characteristics:
* **Constructor**: `__init__(self, orchestrator=None)` accepted an optional orchestrator and fell back to a shared default `ai_orchestrator`.
* **Execution**: Provided a synchronous `plan(self, context)` method that directly invoked the orchestrator.
* **Identity / Isolation**: Had no built-in knowledge of tenants, versions, execution states, or settings overrides.
* **Exception Handling**: Caught all exceptions in a broad `except Exception` block and re-raised them as `RuntimeError`.

---

## New BaseAgent Inheritance

The migrated `PlanningAgent` now inherits from `BaseAgent[PlanningDecision]`:
* **Generics**: Explicitly declares `PlanningDecision` as the Generic return type of its execution wrapper.
* **State Control**: Offloads active states (`idle`, `running`, `paused`, `error`, `terminated`) to the `BaseAgent` and `AgentLifecycle` controllers.
* **Universal Contract**: Complies with the platform's standardized async `setup()`, `execute()`, and `teardown()` interfaces.

---

## Constructor Contract

```python
def __init__(
    self,
    config: AgentConfig,
    orchestrator: AIOrchestrator | None = None,
) -> None:
    ...
```

* **Constraints**:
  * Must be supplied with an explicit `AgentConfig` resolving from the caller service.
  * Rejects configuration objects whose `agent_type` is not exactly `AITask.PLANNING` with a clear `ValueError("PlanningAgent requires an AITask.PLANNING configuration.")`.
  * Preserves dependency injection of `AIOrchestrator` for testability, falling back safely to `ai_orchestrator` if omitted using an explicit `None` check.

---

## Configuration Resolution

The `PlanningService` resolves configuration dynamically on a per-request basis:
```python
config = config_manager.resolve(
    agent_type=AITask.PLANNING,
    tenant_id=job.tenant_id,
)
```
This merges default values with agent overrides and prevents hardcoding timeout/retry policies.

---

## Execution Flow

```
   PlanningService
          │
          ▼
   AgentLifecycle (initialize & execute)
          │
          ▼
   BaseAgent.execute (pre-flight checks, state changes, isolation)
          │
          ▼
   PlanningAgent.run (validation, offloaded orchestrator task)
```

---

## Prevent Event Loop Blocking and Timeout responsive design

* `AIOrchestrator.execute` is a synchronous method. To prevent it from blocking the main `asyncio` event loop thread, the execution is offloaded using `asyncio.to_thread`:
  ```python
  decision = await asyncio.to_thread(
      self.orchestrator.execute,
      task=AITask.PLANNING,
      context=planning_context.model_dump(mode="json"),
      response_schema=PlanningDecision,
  )
  ```
* This guarantees that other async tasks and `AgentLifecycle` timeout monitors remain responsive.
* **CancellationToken Limitation**: Note that `asyncio` cancellation cannot forcibly terminate a running OS thread or a blocking synchronous library call. Therefore, the provider-level or API client connection timeouts remain the hard bound for underlying requests.

---

## PlanningContext Validation

Context inputs passed during execution are validated against the Pydantic `PlanningContext` schema:
```python
planning_context = PlanningContext.model_validate(context)
```
Normal Pydantic validation is preserved. A validation failure raises a Pydantic `ValidationError` directly.

---

## Output Validation

`PlanningService.plan_async()` and `PlanningIntegration.recommend_async()` validate that successful output is a `PlanningDecision`:
```python
if not isinstance(decision, PlanningDecision):
    raise RuntimeError("Planning Agent returned an invalid decision.")
```
If validation fails, database persistence is aborted and the call fails.

---

## Tenant Isolation and Injected Agent Lifecycle

* Under the `BaseAgent` contract, tenant identity is validated on entry by checking `context.get("tenant_id")` against the configuration's `tenant_id`. Mismatched tenants raise a `TenantIsolationError` using the safe message: `"The injected PlanningAgent does not belong to the requested tenant."`.
* **Injected Agent Lifecycle Rules (Single-Use design)**:
  * Injected agents are operation-scoped and single-use.
  * Before execution, the integration checks the injected agent's state. If `agent.state == AgentState.TERMINATED`, it immediately raises an `AgentLifecycleError("The injected agent is already terminated.")` before sending any request to the orchestrator.
  * Cross-tenant calls remain blocked.

---

## Compatibility Adapter

The legacy `plan()` method remains as a compatibility adapter:
```python
def plan(self, context: PlanningContext) -> PlanningDecision:
    ...
```
* **Limitations**:
  * **Event Loop Mismatch**: It cannot be called from within an active asyncio event loop. Calling `plan()` inside an active loop raises a `RuntimeError` directing the caller to use the async lifecycle path.
  * **Bypasses AgentLifecycle**: This compatibility wrapper bypasses the standard `AgentLifecycle` (i.e. it does not support pool registration, timeouts, or lifecycle hooks).
  * **Setup**: Automatically runs `setup()` if the agent was not initialized.

---

## PlanningService and Integration Architecture

* **Service authoritative path**: `plan_async()`
* **Integration authoritative path**: `recommend_async()`
* **Synchronous wrappers**: `plan()` and `recommend()` check for active loops and raise errors directing to async methods.
* **No Platform features**: No Registry, Persistent State, Bus, or Health behavior is implemented at this stage.
