# FieldOps AI Agent Framework: Remaining Stories Audit

This document presents a comprehensive read-only architecture and implementation audit of the **FieldOps Commander AI Agent Framework**. It evaluates the existing foundations (Stories 1.1 and 1.2), analyzes the six target agents, examines database/Redis support, and details gap analyses and implementation plans for the remaining stories (Stories 1.3 to 1.7).

---

## 1. Executive Summary

The FieldOps Commander backend possesses a strong foundation for its AI Agent Framework. Story 1.1 (`BaseAgent` contract) and Story 1.2 (local `AgentLifecycle` and `AgentPool`) are fully implemented and verified by unit tests. However, the six existing operational agents:
1. `PlanningAgent`
2. `DispatchAgent`
3. `MonitoringAgent`
4. `SentimentAgent`
5. `CommunicationAgent`
6. `ClosureAgent`

currently function as simple orchestrator wrappers. None of these agents inherit from [base.py](file:///c:/Users/rubyr/OneDrive/Documents/GitHub/fieldops-project/backend/app/services/ai/FieldOpsAI/agents/base.py)'s `BaseAgent` or are managed by [lifecycle.py](file:///c:/Users/rubyr/OneDrive/Documents/GitHub/fieldops-project/backend/app/services/ai/FieldOpsAI/runtime/lifecycle.py)'s `AgentLifecycle`.

Moving forward, the primary goals are to migrate these six agents to the unified `BaseAgent` standard and implement the remaining infrastructure stories. The recommended order of implementation aligns with system dependencies, starting with **Agent Configuration (Story 1.4)** and the **Agent Migration**, followed by **Persistent State (Story 1.5)**, the **Agent Registry (Story 1.3)**, the **Communication Bus (Story 1.6)**, and **Health Monitoring (Story 1.7)**.

---

## 2. Current FieldOpsAI File Structure

Below is the layout of the authoritative AI package directory [app/services/ai/FieldOpsAI/](file:///c:/Users/rubyr/OneDrive/Documents/GitHub/fieldops-project/backend/app/services/ai/FieldOpsAI/):

```
app/services/ai/FieldOpsAI/
├── IDENTITY.md
├── SOUL.md
├── __init__.py
├── agents/
│   ├── base.py
│   ├── closure_agent.py
│   ├── communication_agent.py
│   ├── dispatch_agent.py
│   ├── monitoring_agent.py
│   ├── planning_agent.py
│   └── sentiment_agent.py
├── config/
│   ├── ai.yaml
│   └── config_loader.py
├── generators/
├── knowledge/
│   ├── business_rules.md
│   ├── lifecycle.md
│   ├── roles.md
│   └── validation.md
├── prompts/
│   ├── channels/
│   │   ├── email.md
│   │   ├── push.md
│   │   └── sms.md
│   ├── closure.md
│   ├── communication.md
│   ├── dispatch.md
│   ├── monitoring.md
│   ├── planning.md
│   └── sentiment.md
├── providers/
│   ├── __init__.py
│   ├── base_provider.py
│   └── groq_provider.py
├── repositories/
│   ├── job_assignment_repository.py
│   ├── job_repository.py
│   └── technician_repository.py
├── runtime/
│   ├── agent_pool.py
│   ├── lifecycle.py
│   ├── orchestrator.py
│   ├── prompt_builder.py
│   ├── response_parser.py
│   ├── runtime_interface.py
│   └── token_tracker.py
├── schemas/
│   ├── agent_config.py
│   ├── agent_lifecycle.py
│   ├── agent_messages.py
│   ├── agent_result.py
│   ├── ai_task.py
│   ├── closure.py
│   ├── communication.py
│   ├── dispatch.py
│   ├── monitoring.py
│   ├── planning.py
│   └── sentiment.py
└── services/
    ├── closure_service.py
    ├── communication_service.py
    ├── dispatch_service.py
    ├── monitoring_service.py
    ├── planning_service.py
    └── sentiment_service.py
```

---

## 3. Story 1.1 Verification

* **Implementation Files**:
  * [base.py](file:///c:/Users/rubyr/OneDrive/Documents/GitHub/fieldops-project/backend/app/services/ai/FieldOpsAI/agents/base.py): Implements `BaseAgent` (abstract class), `AgentState` enum (`IDLE`, `RUNNING`, `PAUSED`, `ERROR`, `TERMINATED`), and exceptions (`BaseAgentError`, `AgentLifecycleError`, `AgentDisabledError`, `TenantIsolationError`).
  * [agent_config.py](file:///c:/Users/rubyr/OneDrive/Documents/GitHub/fieldops-project/backend/app/services/ai/FieldOpsAI/schemas/agent_config.py): Implements `AgentConfig` validation using Pydantic (defines `agent_type`, `tenant_id`, `agent_version`, `timeout_seconds`, `max_retries`, and `enabled`).
* **Test Coverage**:
  * [test_base_agent.py](file:///c:/Users/rubyr/OneDrive/Documents/GitHub/fieldops-project/backend/tests/test_base_agent.py): 100% passing tests covering initialization, validation of config rules, context validation (requiring `tenant_id`), state transition constraints, execution safety wrapper (`execute()` -> `run()`), error logging, and context managers (`__aenter__`/`__aexit__`).
* **Documentation**:
  * `docs/agents/base_agent.md`: **Missing** from the workspace docs folder.
* **Assessment**: The foundational contracts for `BaseAgent` and `AgentConfig` are robust, handle tenant isolation correctly, and provide clear lifecycle hook slots.

---

## 4. Story 1.2 Verification

* **Implementation Files**:
  * [agent_pool.py](file:///c:/Users/rubyr/OneDrive/Documents/GitHub/fieldops-project/backend/app/services/ai/FieldOpsAI/runtime/agent_pool.py): Local, thread-safe asynchronous cache (`AgentPool`) for active agent instances within a single Python process. Enforces tenant checks.
  * [lifecycle.py](file:///c:/Users/rubyr/OneDrive/Documents/GitHub/fieldops-project/backend/app/services/ai/FieldOpsAI/runtime/lifecycle.py): Implements `AgentLifecycle` runner. Enforces timeouts (max 30s run, 5s teardown), runs pre/post execution hooks, manages transitions, and structures execution outputs.
  * [agent_result.py](file:///c:/Users/rubyr/OneDrive/Documents/GitHub/fieldops-project/backend/app/services/ai/FieldOpsAI/schemas/agent_result.py): Implements `AgentResult` Pydantic models.
  * [agent_lifecycle.py](file:///c:/Users/rubyr/OneDrive/Documents/GitHub/fieldops-project/backend/app/services/ai/FieldOpsAI/schemas/agent_lifecycle.py): Implements schemas for events and lifecycle hooks.
* **Test Coverage**:
  * [test_agent_pool.py](file:///c:/Users/rubyr/OneDrive/Documents/GitHub/fieldops-project/backend/tests/test_agent_pool.py) and [test_agent_lifecycle.py](file:///c:/Users/rubyr/OneDrive/Documents/GitHub/fieldops-project/backend/tests/test_agent_lifecycle.py): Passing tests verifying registration, search, removal, timeout execution, hooks sequencing, state persistence during lifecycle, and error isolation.
* **Documentation**:
  * `docs/agents/agent_lifecycle.md`: **Missing** from the workspace docs folder.
* **Assessment**: Operational and fully functional in-process lifecycle orchestration. Ready to support persistent database logs and cluster-wide Redis registry.

---

## 5. Six-Agent Inventory

The following table documents the properties and current implementation status of the six agents:

| Agent Property | Planning Agent | Dispatch Agent | Monitoring Agent | Sentiment Agent | Communication Agent | Closure Agent |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Exact File Path** | [planning_agent.py](file:///c:/Users/rubyr/OneDrive/Documents/GitHub/fieldops-project/backend/app/services/ai/FieldOpsAI/agents/planning_agent.py) | [dispatch_agent.py](file:///c:/Users/rubyr/OneDrive/Documents/GitHub/fieldops-project/backend/app/services/ai/FieldOpsAI/agents/dispatch_agent.py) | [monitoring_agent.py](file:///c:/Users/rubyr/OneDrive/Documents/GitHub/fieldops-project/backend/app/services/ai/FieldOpsAI/agents/monitoring_agent.py) | [sentiment_agent.py](file:///c:/Users/rubyr/OneDrive/Documents/GitHub/fieldops-project/backend/app/services/ai/FieldOpsAI/agents/sentiment_agent.py) | [communication_agent.py](file:///c:/Users/rubyr/OneDrive/Documents/GitHub/fieldops-project/backend/app/services/ai/FieldOpsAI/agents/communication_agent.py) | [closure_agent.py](file:///c:/Users/rubyr/OneDrive/Documents/GitHub/fieldops-project/backend/app/services/ai/FieldOpsAI/agents/closure_agent.py) |
| **Current Class Name** | `PlanningAgent` | `DispatchAgent` | `MonitoringAgent` | `SentimentAgent` | `CommunicationAgent` | `ClosureAgent` |
| **Constructor Params** | `orchestrator: AIOrchestrator \| None` | `orchestrator: AIOrchestrator \| None` | `orchestrator: AIOrchestrator \| None` | `orchestrator: AIOrchestrator \| None` | `orchestrator: AIOrchestrator \| None` | `orchestrator: AIOrchestrator \| None` |
| **Public Methods** | `plan(context: PlanningContext)` | `dispatch(context: DispatchContext)` | `monitor(context: MonitoringContext)` | `analyze(context: SentimentContext)` | `generate(context: CommunicationContext)` | `generate(context: ClosureContext)` |
| **Input Schema** | `PlanningContext` | `DispatchContext` | `MonitoringContext` | `SentimentContext` | `CommunicationContext` | `ClosureContext` |
| **Output Schema** | `PlanningDecision` | `DispatchDecision` | `MonitoringDecision` | `SentimentDecision` | `CommunicationDecision` | `ClosureDecision` |
| **Calls (Services/Repos)**| `AIOrchestrator` | `AIOrchestrator` | `AIOrchestrator` | `AIOrchestrator` | `AIOrchestrator` | `AIOrchestrator` |
| **AI Provider Usage** | Llama 3.3 via Groq | Llama 3.3 via Groq | Llama 3.3 via Groq | Llama 3.3 via Groq | Llama 3.3 via Groq | Llama 3.3 via Groq |
| **Inherits BaseAgent** | ❌ No | ❌ No | ❌ No | ❌ No | ❌ No | ❌ No |
| **Implements `run()`** | ❌ No | ❌ No | ❌ No | ❌ No | ❌ No | ❌ No |
| **Setup/Teardown** | ❌ No | ❌ No | ❌ No | ❌ No | ❌ No | ❌ No |
| **Tenant Isolation** | ❌ No (no verification) | ❌ No (no verification) | ❌ No (no verification) | ❌ No (no verification) | ❌ No (no verification) | ❌ No (no verification) |
| **DB-Writing Logic** | ❌ No | ❌ No | ❌ No | ❌ No | ❌ No | ❌ No |
| **Covering Tests** | None (only general logs in `test_cooldown.py`) | None | None | None | None | None |
| **Migration Risks** | High: Must rewrite constructor to accept `AgentConfig`, implement `async def run(context: dict)`, unpack parameters to `PlanningContext`, and enforce tenant checks. | High: Constructor config integration, implement `run(dict)` instead of `dispatch(DispatchContext)`. | High: Constructor config integration, implement `run(dict)` instead of `monitor(MonitoringContext)`. | High: Constructor config integration, implement `run(dict)` instead of `analyze(SentimentContext)`. | High: Constructor config integration, implement `run(dict)` instead of `generate(CommunicationContext)`. | High: Constructor config integration, implement `run(dict)` instead of `generate(ClosureContext)`. |

---

## 6. Shared Schemas and Enums

* **[ai_task.py](file:///c:/Users/rubyr/OneDrive/Documents/GitHub/fieldops-project/backend/app/services/ai/FieldOpsAI/schemas/ai_task.py)**: Defines `AITask` enum (`PLANNING`, `DISPATCH`, `MONITORING`, `COMMUNICATION`, `CLOSURE`, `SENTIMENT`). This is already utilized by `AgentConfig.agent_type` and matches the 6 agents. No other duplicate `AgentType` or `AgentTask` enum exists.
* **[agent_messages.py](file:///c:/Users/rubyr/OneDrive/Documents/GitHub/fieldops-project/backend/app/services/ai/FieldOpsAI/schemas/agent_messages.py)**: Defines the schemas for inter-agent communication (addresses, messages, commands, events, queries, responses, and error envelopes).
* **Gaps**: None. The schemas are standardized and frozen.

---

## 7. Existing Database Support

* **Database Schema ([app/models.py](file:///c:/Users/rubyr/OneDrive/Documents/GitHub/fieldops-project/backend/app/models.py))**:
  * Exists: `JobAssignment` (stores AI recommendations for jobs), `AIGuardrailViolation` (guardrail breach audits), `AIBrandSafetyRule` (brand safety configuration), `AuditEvent` (job transitions), `OverrideAuditEvent` (manual scheduler bypasses).
  * Gaps:
    * **No tables exist** for storing persistent agent configuration overrides (Story 1.4).
    * **No tables exist** for storing persistent agent state, running execution records, latency metrics, or status transition history (Story 1.5).
* **Repositories**:
  * [job_assignment_repository.py](file:///c:/Users/rubyr/OneDrive/Documents/GitHub/fieldops-project/backend/app/services/ai/FieldOpsAI/repositories/job_assignment_repository.py): Saves planning agent technician recommendation ranks.
  * [job_repository.py](file:///c:/Users/rubyr/OneDrive/Documents/GitHub/fieldops-project/backend/app/services/ai/FieldOpsAI/repositories/job_repository.py) and [technician_repository.py](file:///c:/Users/rubyr/OneDrive/Documents/GitHub/fieldops-project/backend/app/services/ai/FieldOpsAI/repositories/technician_repository.py): Perform basic domain mappings.
  * Gaps: **No repositories exist** to query/write agent execution records or state histories.

---

## 8. Existing Redis Support

* **Synchronous Client ([app/redis_client.py](file:///c:/Users/rubyr/OneDrive/Documents/GitHub/fieldops-project/backend/app/redis_client.py))**:
  * Exposes `RedisCacheManager` and `get_redis_client()`. It uses synchronous, blocking commands with retry and timeout rules.
* **Asynchronous Client ([app/main.py](file:///c:/Users/rubyr/OneDrive/Documents/GitHub/fieldops-project/backend/app/main.py))**:
  * Initializes `redis_async_client` of type `redis.asyncio.Redis` inside the FastAPI lifespan, which is passed to the `BroadcastScheduler`.
* **Gap Analysis**:
  * The async agents platform, distributed registry (Story 1.3), and agent communication bus (Story 1.6) run in an `asyncio` context loop. Performing blocking calls using the synchronous Redis client could cause latency issues under load.
  * *Architectural Inference*: Stories 1.3, 1.6, and 1.7 must utilize the async Redis client (`redis_async_client`) or establish an async connection pool in `app/redis_client.py` rather than using the synchronous client.

---

## 9. Existing FastAPI Lifespan Integration

* **Lifespan Manager ([app/main.py](file:///c:/Users/rubyr/OneDrive/Documents/GitHub/fieldops-project/backend/app/main.py))**:
  * Startup: Connects `redis_async_client` and `redis_pubsub_client` (async), spawns the `redis_gps_listener` task, initializes the `BroadcastScheduler`, and seeds templates.
  * Shutdown: Stops the scheduler, cancels the listener task, and closes all async Redis connection pools.
  * Gaps: **No hooks exist** to clear/prune registered agents in Redis on application shutdown. Story 1.3 must add registry-cleanup calls here.

---

## 10. Story 1.3 Gap Analysis — Agent Registry

* **Requirements**: Implement a Redis-backed discovery registry allowing multi-process agent registration, state-updates, queries, heartbeats, and cleanup.
* **Reusable Foundations**:
  * `AgentPool` tracks local agent objects, while `AgentAddress` from `agent_messages.py` defines the addressing schema (`agent_type:agent_id:tenant_id`).
* **Missing Gaps**:
  * Registry module that maps agents in Redis (using hashes or set keys) and registers/unregisters them dynamically.
  * Heartbeat update logic to automatically mark stale or disconnected agents as inactive.
* **Expected Files to Create**:
  * `app/services/ai/FieldOpsAI/runtime/agent_registry.py`
  * `tests/test_agent_registry.py`
* **Files to Modify**:
  * `app/services/ai/FieldOpsAI/runtime/lifecycle.py` (call registry during setup and teardown)
  * `app/main.py` (perform registry clear on shutdown)
* **Dependencies**: Story 1.1, Story 1.2
* **Testing Requirements**: Mock Redis to verify TTL pings, cross-process discovery, and tenant isolation filtering.
* **Integration Risks**: Key expiration handling and heartbeat timeouts in Redis.

---

## 11. Story 1.4 Gap Analysis — Agent Configuration

* **Requirements**: Dynamically configure agents (enabled, timeout, max_retries, version) per tenant and agent type.
* **Reusable Foundations**:
  * `AgentConfig` schema, `ConfigLoader` loads `ai.yaml` default settings.
* **Missing Gaps**:
  * A configuration manager to resolve configurations using defaults from `ai.yaml` and overriding them with database or Redis values.
* **Expected Files to Create**:
  * `app/services/ai/FieldOpsAI/config/agent_config_manager.py`
  * `tests/test_agent_config_manager.py`
* **Files to Modify**:
  * `app/models.py` and `update_db.py` (define tenant configurations database table)
* **Dependencies**: Story 1.1
* **Testing Requirements**: Verify YAML fallbacks, validation check rules, and tenant override precedence.
* **Integration Risks**: Config schema updates breaking validated Pydantic models.

---

## 12. Story 1.5 Gap Analysis — Persistent Agent State

* **Requirements**: Persist agent execution history, latencies, state changes, error rates, and lifecycle transitions to database records.
* **Reusable Foundations**:
  * `AgentState` enum, `AgentLifecycle` hooks, correlation IDs, and `AuditEvent` structures.
* **Missing Gaps**:
  * Database models to store agent execution audits, inputs, outputs, errors, and transition trails.
  * Repository classes to save/retrieve these audits.
* **Expected Files to Create**:
  * `app/services/ai/FieldOpsAI/repositories/agent_state_repository.py`
  * `tests/test_agent_state_persistence.py`
* **Files to Modify**:
  * `app/models.py` (add `AgentExecutionRecord` and `AgentStateTransition` tables)
  * `update_db.py` (register database tables schema update)
  * `app/services/ai/FieldOpsAI/runtime/lifecycle.py` (execute repository writes on events and transitions)
* **Dependencies**: Story 1.1, Story 1.2
* **Testing Requirements**: Verify transactional commit safety, query indexing performance, and rollback constraints.
* **Integration Risks**: High database write pressure under heavy agent usage.

---

## 13. Story 1.6 Gap Analysis — Agent Communication Bus

* **Requirements**: Distribute messages (commands, events, responses) between agents across processes using Redis Pub/Sub.
* **Reusable Foundations**:
  * `BaseMessage` and envelopes in `agent_messages.py`.
* **Missing Gaps**:
  * Message router/bus logic mapping Redis subscriptions to agent addresses, resolving recipient endpoints, executing request-response calls with timeouts, and preserving correlation IDs.
* **Expected Files to Create**:
  * `app/services/ai/FieldOpsAI/runtime/communication_bus.py`
  * `tests/test_agent_communication_bus.py`
* **Files to Modify**:
  * `app/main.py` (initialize and spin up background pub-sub subscription listeners)
* **Dependencies**: Story 1.1, Story 1.2, Story 1.3
* **Testing Requirements**: Test Redis mock connections, routing delays, and error serialization.
* **Integration Risks**: Message loss on fire-and-forget events, and subscription socket leaks.

---

## 14. Story 1.7 Gap Analysis — Agent Health Monitoring

* **Requirements**: Expose overall platform health (agent states, Redis connectivity, AI provider availability) via a `/api/v1/ai/health` endpoint.
* **Reusable Foundations**:
  * `BaseAgent.health_check()` local dict summary.
* **Missing Gaps**:
  * Expose an endpoint that aggregates local agent health, queries Redis for remote registered agents' heartbeats, tests Groq provider ping APIs, and formats status codes.
* **Expected Files to Create**:
  * `app/routes/ai_health.py`
  * `tests/test_agent_health_monitoring.py`
* **Files to Modify**:
  * `app/main.py` (mount health routers)
* **Dependencies**: Story 1.1 - Story 1.5
* **Testing Requirements**: Test response contracts for degraded/offline dependencies.
* **Integration Risks**: Health checks blocking the main thread if pings are synchronous.

---

## 15. Duplicate and Conflict Risks

1. **`DispatchAgent` Duplicate Definition**:
   * **[app/services/dispatch_agent.py](file:///c:/Users/rubyr/OneDrive/Documents/GitHub/fieldops-project/backend/app/services/dispatch_agent.py)** defines a mock `DispatchAgent` class used to trigger the re-dispatch scheduler.
   * **[app/services/ai/FieldOpsAI/agents/dispatch_agent.py](file:///c:/Users/rubyr/OneDrive/Documents/GitHub/fieldops-project/backend/app/services/ai/FieldOpsAI/agents/dispatch_agent.py)** defines the AI dispatch decision agent.
   * *Risk*: High import conflicts and confusion in worker tasks.
   * *Resolution recommendation*: Consolidate or rename the mock service to `RedispatchTriggerService` or similar, ensuring the AI agent remains the sole `DispatchAgent` owner in `FieldOpsAI`.
2. **`CommsAgent` vs `CommunicationAgent`**:
   * **[app/services/comms_agent.py](file:///c:/Users/rubyr/OneDrive/Documents/GitHub/fieldops-project/backend/app/services/comms_agent.py)** defines a simple synchronous notification router helper.
   * **[app/services/ai/FieldOpsAI/agents/communication_agent.py](file:///c:/Users/rubyr/OneDrive/Documents/GitHub/fieldops-project/backend/app/services/ai/FieldOpsAI/agents/communication_agent.py)** is the AI generation agent.
   * *Risk*: Poor namespace separation. Keep these well-delineated.
3. **`AgentLifecycleError` Duplicate Definition**:
   * Defined both in [base.py:50](file:///c:/Users/rubyr/OneDrive/Documents/GitHub/fieldops-project/backend/app/services/ai/FieldOpsAI/agents/base.py#L50) and [lifecycle.py:47](file:///c:/Users/rubyr/OneDrive/Documents/GitHub/fieldops-project/backend/app/services/ai/FieldOpsAI/runtime/lifecycle.py#L47).
   * *Risk*: Importing the wrong error class will cause `except` blocks to fail.
   * *Resolution recommendation*: Import `AgentLifecycleError` from `base.py` inside `lifecycle.py` and delete the duplicate declaration.
4. **Redis Clients**:
   * Sync Redis cache (`redis_client.py`) vs. Async Redis client (`main.py`).
   * *Risk*: Blocking calls in async agents runtime.
   * *Resolution recommendation*: Establish clear guidelines to only use `redis.asyncio` clients inside the agent platform.

---

## 16. Recommended Dependency Order

The proposed implementation order is **highly accurate** and aligns with the codebase architecture:

1. **Story 1.4 — Agent Configuration**: Set up configuration loader parameters so that agents can be created with tenant-specific and global configurations from the outset.
2. **Migrate the six agents to BaseAgent**: Align constructors, inputs, outputs, and test coverage with the base agent standard.
3. **Story 1.5 — Persistent Agent State**: Track execution history locally inside `AgentLifecycle` hooks.
4. **Story 1.3 — Agent Registry**: Register the migrated base agents in Redis.
5. **Story 1.6 — Agent Communication Bus**: Execute inter-process message subscriptions once registries are functional.
6. **Story 1.7 — Agent Health Monitoring**: Check Redis heartbeats and status logs.
7. **Final integration tests**: Execute end-to-end integration scenarios.

---

## 17. Proposed File-by-File Implementation Plan

### Story 1.4 — Agent Configuration
* **[NEW]** `app/services/ai/FieldOpsAI/config/agent_config_manager.py` (resolves defaults with database overrides)
* **[NEW]** `tests/test_agent_config_manager.py`
* **[MODIFY]** `app/models.py` (add `TenantAgentConfig` model)
* **[MODIFY]** `update_db.py` (add schema table generation script)

### Six-Agent Migration
* **[MODIFY]** Six agent files in `app/services/ai/FieldOpsAI/agents/` (inherit from `BaseAgent`, constructor config mapping, implement `run(dict)`)
* **[NEW]** Unit tests for all six agents under `tests/`
* **[MODIFY]** Services and integrations in `app/services/ai/FieldOpsAI/services/` and `app/services/ai/integrations/` (update calls to execute using dict context)

### Story 1.5 — Persistent Agent State
* **[NEW]** `app/services/ai/FieldOpsAI/repositories/agent_state_repository.py`
* **[NEW]** `tests/test_agent_state_persistence.py`
* **[MODIFY]** `app/models.py` (add `AgentExecutionLog`, `AgentStateTransition` tables)
* **[MODIFY]** `update_db.py` (add SQL alterations)
* **[MODIFY]** `app/services/ai/FieldOpsAI/runtime/lifecycle.py` (hook state repository saves on status updates)

### Story 1.3 — Agent Registry
* **[NEW]** `app/services/ai/FieldOpsAI/runtime/agent_registry.py` (Redis client state sets and deletes)
* **[NEW]** `tests/test_agent_registry.py`
* **[MODIFY]** `app/services/ai/FieldOpsAI/runtime/lifecycle.py` (update setup/teardown events to trigger registration)
* **[MODIFY]** `app/main.py` (trigger clear methods)

### Story 1.6 — Agent Communication Bus
* **[NEW]** `app/services/ai/FieldOpsAI/runtime/communication_bus.py` (message listener channels and callbacks)
* **[NEW]** `tests/test_agent_communication_bus.py`
* **[MODIFY]** `app/main.py` (initialize communication task on startup)

### Story 1.7 — Agent Health Monitoring
* **[NEW]** `app/routes/ai_health.py` (FastAPI router status endpoints)
* **[NEW]** `tests/test_agent_health_monitoring.py`
* **[MODIFY]** `app/main.py` (mount endpoints)

---

## 18. Current Test Results

* **Baseline Tests Run Command**:
  ```powershell
  python -m pytest tests/test_base_agent.py tests/test_agent_pool.py tests/test_agent_lifecycle.py -q
  ```
  * **Result**: **121 passed** in 1.06s. No failures or skips.
* **Full Test Suite Run Command**:
  ```powershell
  python -m pytest -q
  ```
  * **Result**: **687 passed** in 86.37s. No failures or skips.
* **Analysis**: Stories 1.1 and 1.2 unit tests are 100% healthy.

---

## 19. Questions or Missing Requirements

1. **Database Schema Strategy**: Should we configure database migrations using Alembic, or continue appending DDL scripts inside `update_db.py`?
2. **Registry Heartbeat Frequency**: What is the expected TTL duration and ping interval for agent heartbeats in Redis? (Recommended: 30s TTL, 10s ping).
3. **RPC Timeout Enforcement**: When wait-request calls time out in `AgentCommunicationBus`, should the recipient agent be terminated or transitioned to `ERROR`?
4. **Missing Story 1.1/1.2 Documentation**: The files `docs/agents/base_agent.md` and `docs/agents/agent_lifecycle.md` are referenced in stories but are missing from the workspace. Should they be created or updated?

---

## 20. Files Inspected

* `app/services/ai/FieldOpsAI/agents/base.py`
* `app/services/ai/FieldOpsAI/agents/planning_agent.py`
* `app/services/ai/FieldOpsAI/agents/dispatch_agent.py`
* `app/services/ai/FieldOpsAI/agents/monitoring_agent.py`
* `app/services/ai/FieldOpsAI/agents/sentiment_agent.py`
* `app/services/ai/FieldOpsAI/agents/communication_agent.py`
* `app/services/ai/FieldOpsAI/agents/closure_agent.py`
* `app/services/ai/FieldOpsAI/schemas/agent_config.py`
* `app/services/ai/FieldOpsAI/schemas/ai_task.py`
* `app/services/ai/FieldOpsAI/schemas/agent_lifecycle.py`
* `app/services/ai/FieldOpsAI/schemas/agent_result.py`
* `app/services/ai/FieldOpsAI/schemas/agent_messages.py`
* `app/services/ai/FieldOpsAI/runtime/agent_pool.py`
* `app/services/ai/FieldOpsAI/runtime/lifecycle.py`
* `app/services/ai/FieldOpsAI/config/ai.yaml`
* `app/services/ai/FieldOpsAI/config/config_loader.py`
* `app/services/ai/FieldOpsAI/services/planning_service.py`
* `app/services/ai/FieldOpsAI/services/dispatch_service.py`
* `app/services/ai/FieldOpsAI/services/monitoring_service.py`
* `app/services/ai/FieldOpsAI/services/sentiment_service.py`
* `app/services/ai/FieldOpsAI/services/closure_service.py`
* `app/services/ai/FieldOpsAI/services/communication_service.py`
* `app/services/ai/integrations/planning_integration.py`
* `app/services/ai/integrations/communication_integration.py`
* `app/redis_client.py`
* `app/main.py`
* `app/context.py`
* `app/models.py`
* `update_db.py`
* `tests/test_base_agent.py`
* `tests/test_agent_pool.py`
* `tests/test_agent_lifecycle.py`
