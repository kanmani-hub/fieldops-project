# Story 1.5 — Persistent Agent State

## Overview

Story 1.5 adds a persistence layer that safely snapshots `BaseAgent`
runtime state to the database across process restarts.

BaseAgent remains solely responsible for in-memory state transitions.
The persistence layer is a passive observer: it reads only public agent
properties and stores a safe operational snapshot.

---

## Architecture

```
BaseAgent (in-memory state authority)
    │
    │   reads public properties only
    ▼
AgentStateSnapshot (Pydantic v2 schema)
    │
    ▼
AgentStateManager (orchestration)
    │
    ▼
AgentStateRepository (synchronous SQLAlchemy)
    │
    ▼
AgentStateRecord (SQLAlchemy model → agent_state_records table)
```

`AgentLifecycle` holds an **optional** `state_manager` reference.
Persistence is triggered at three lifecycle points:

| Lifecycle point | State captured |
|---|---|
| After `initialize()` succeeds | `IDLE` |
| After `execute()` completes (success, failure, or timeout) | `IDLE` or `ERROR` |
| After `teardown()` completes | `TERMINATED` |

---

## Files

| File | Purpose |
|---|---|
| `app/services/ai/FieldOpsAI/schemas/agent_state.py` | `AgentStateSnapshot` Pydantic v2 schema |
| `app/models.py` | `AgentStateRecord` SQLAlchemy model |
| `app/services/ai/FieldOpsAI/repositories/agent_state_repository.py` | Synchronous CRUD repository |
| `app/services/ai/FieldOpsAI/runtime/agent_state_manager.py` | Orchestration layer |
| `app/services/ai/FieldOpsAI/runtime/lifecycle.py` | Optional `state_manager` + `_persist_state()` |
| `tests/test_agent_state_repository.py` | Schema + repository tests |
| `tests/test_agent_state_manager.py` | Manager + lifecycle integration tests |
| `alembic/versions/e3a1f7c920d4_add_agent_state_records.py` | Alembic migration |

---

## Database Model

**Table**: `agent_state_records`

| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | Auto-increment |
| `agent_id` | String(36) | UUID4 string |
| `agent_type` | String(50) | AITask value |
| `tenant_id` | String(50) | Owning tenant |
| `agent_version` | String(50) | Agent implementation version |
| `state` | String(30) | AgentState value |
| `correlation_id` | String(100) | Optional lifecycle correlation ID |
| `last_error` | String(500) | Safe error summary only |
| `safe_metadata` | JSON | Safe operational metadata only |
| `created_at` | DateTime(tz=True) | Server-generated on insert |
| `updated_at` | DateTime(tz=True) | Server-generated on update |

**Constraints and indexes**:

| Name | Type | Columns |
|---|---|---|
| `uq_agent_state_tenant_agent` | UNIQUE | (tenant_id, agent_id) |
| `idx_agent_state_tenant` | INDEX | tenant_id |
| `idx_agent_state_agent_id` | INDEX | agent_id |
| `idx_agent_state_tenant_state` | INDEX | (tenant_id, state) |

> **Note**: `index=True` is not set on the `agent_id` and `tenant_id` column
> definitions because the named indexes in `__table_args__` are the canonical
> indexes.  Using both would create duplicate indexes on some engines.

---

## Privacy Rules

The following are **strictly forbidden** from `AgentStateRecord` and `AgentStateSnapshot`:

- API keys, authentication secrets, tokens, or passwords
- AI provider prompts or completions
- Customer names, addresses, phone numbers, or email addresses
- Technician GPS, coordinates, or private information
- Full stack traces (safe error summaries only, max 500 chars)
- Message body contents or full job payloads

### Metadata privacy validation

The `AgentStateSnapshot.metadata` field is validated **recursively** at
every nesting depth.  A `ValidationError` is raised if:

1. **Any key** matches a forbidden name (case-insensitive).  Forbidden keys
   include: `api_key`, `apikey`, `secret`, `password`, `token`,
   `auth_token`, `access_token`, `refresh_token`, `prompt`, `response`,
   `completion`, `customer_name`, `email`, `phone`, `phone_number`, `gps`,
   `latitude`, `longitude`, `lat`, `lng`, and many more (see full list in
   `agent_state.py: _FORBIDDEN_METADATA_KEYS`).

2. **Any value** is not JSON-compatible (i.e. is not `str`, `int`, `float`,
   `bool`, `None`, `dict`, or `list`).

Error messages identify the forbidden key path but **never include the
value itself**.  Metadata contents are never logged.

---

## Deep-copy Behavior

Caller-supplied `metadata` dicts are deep-copied at two points:

1. **`AgentStateManager.save_agent()`** — deep-copies the caller's dict
   before passing it to `AgentStateSnapshot.from_agent()`.

2. **`AgentStateSnapshot.from_agent()`** — deep-copies the already-copied
   dict before storing it in the snapshot.

This means nested mutable objects inside the caller's metadata cannot be
mutated after the call returns, even if the caller holds a reference to
the same nested object.

---

## Persistence Failure Policy

> **Log and continue** — a persistence failure must not interrupt agent execution.

When `AgentLifecycle._persist_state()` is called and `save_agent()` raises,
the exception is caught, logged at `exception` level with structured fields,
and then **swallowed**.  The agent continues to execute normally.

This policy is tested explicitly in
`test_persistence_failure_does_not_interrupt_execution` and
`test_persistence_failure_during_execute_returns_valid_result`.

### Synchronous persistence limitation

Persistence is **synchronous**.  `AgentStateManager.save_agent()` and
`AgentStateRepository.upsert()` use a synchronous SQLAlchemy `Session`.
They are called from `AgentLifecycle._persist_state()` which runs inline
(not in a thread pool or `asyncio.to_thread`).

This means a slow database write will block the event loop briefly.  For
production deployments with latency-sensitive agents, consider offloading
persistence to a background thread or replacing with an async session.

---

## Tested State Sequence

| Scenario | State after execute | State after teardown |
|---|---|---|
| Success | `IDLE` (result_status=`success`) | `TERMINATED` |
| Failure (runtime exception) | `ERROR` (result_status=`failed`, last_error set) | `TERMINATED` |
| Timeout | `ERROR` (result_status=`timeout`) | `TERMINATED` |

All three scenarios are covered by dedicated lifecycle integration tests.

---

## Opt-in Integration

`AgentLifecycle` accepts an optional `state_manager` parameter:

```python
from app.services.ai.FieldOpsAI.runtime.agent_state_manager import AgentStateManager
from app.services.ai.FieldOpsAI.repositories.agent_state_repository import AgentStateRepository

repo = AgentStateRepository(db=session)
state_mgr = AgentStateManager(repository=repo)

lifecycle = AgentLifecycle(
    agent=agent,
    pool=pool,
    state_manager=state_mgr,   # optional — None by default
)
```

When `state_manager=None` (the default), no persistence is performed.
All existing callers are fully backward-compatible.

---

## Tenant Isolation

All repository operations enforce tenant isolation in the WHERE clause:

```python
.filter(
    AgentStateRecord.tenant_id == tenant_id,
    AgentStateRecord.agent_id == agent_id_str,
)
```

`get()`, `delete()`, and `list_by_tenant()` never return records from
a different tenant than the one supplied by the caller.

---

## Test Coverage

| File | Statements | Branches | Coverage |
|---|---|---|---|
| `agent_state.py` | 78 | 28 | 92% |
| `agent_state_repository.py` | 69 | 10 | 90% |
| `agent_state_manager.py` | 32 | 4 | **100%** |
| **Total** | **179** | **42** | **93%** |

| Test file | Cases | Scope |
|---|---|---|
| `test_agent_state_repository.py` | 25 | Schema validation (incl. privacy), CRUD, isolation, rollback |
| `test_agent_state_manager.py` | 24 | Manager unit, lifecycle success/failure/timeout, failure isolation |
| **Total** | **49** | |

---

## Alembic Migration

**Migration file**: `alembic/versions/e3a1f7c920d4_add_agent_state_records.py`

**Chain**: `6457b51379ff` → `e3a1f7c920d4`

Apply the migration:

```
alembic upgrade head
```

Roll back:

```
alembic downgrade -1
```

The downgrade removes all three named indexes before dropping the table,
making it safe on engines that do not automatically cascade index removal.
