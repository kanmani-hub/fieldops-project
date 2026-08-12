# Story 1.3 — Agent Registry

## Overview

Story 1.3 adds an in-memory agent definition registry for the FieldOps
Commander AI platform.  The registry stores agent type definitions and
optional factories.  It creates fresh, uninitialized agent instances on
demand.

---

## Purpose

`AgentRegistry` answers two questions:

1. **Which agent types are available?** — Registration management.
2. **How do I get a fresh agent for tenant X?** — Tenant-safe creation.

The registry does not manage live agent execution, persistence, or
distributed discovery.

---

## Separation of Concerns

| Component | Responsibility |
|---|---|
| **AgentRegistry** | Stores agent type definitions; creates fresh instances |
| **AgentPool** | Stores live, initialized agent instances |
| **AgentLifecycle** | Manages execution flow (setup → execute → teardown) |
| **AgentStateManager** | Persists runtime state snapshots to the database |
| **AgentConfigManager** | Resolves validated `AgentConfig` from YAML |

These components are complementary.  `AgentRegistry` does not duplicate
any `AgentPool` responsibility.

---

## Files

| File | Purpose |
|---|---|
| `app/services/ai/FieldOpsAI/schemas/agent_registration.py` | Immutable registration schema |
| `app/services/ai/FieldOpsAI/runtime/agent_registry.py` | Registry, exceptions, bootstrap |
| `tests/test_agent_registry.py` | Full test suite (44 tests) |
| `docs/architecture/agent_registry.md` | This document |

No production files were modified.  No database model or migration was
created.  The registry is entirely in-memory.

---

## AgentRegistration Schema

```python
@dataclasses.dataclass(frozen=True)
class AgentRegistration:
    agent_type: AITask
    agent_class: type[BaseAgent]
    version: str
    enabled: bool = True
    description: str | None = None
```

`AgentRegistration` uses a **frozen dataclass** instead of Pydantic because:
- Pydantic v2 cannot safely validate `type[BaseAgent]` fields without
  `arbitrary_types_allowed`, which makes validation opaque.
- Frozen dataclasses provide immutability, `__eq__`, and `__hash__` for free.
- `__post_init__` validates all fields explicitly.

### Validation rules

| Field | Rule |
|---|---|
| `agent_type` | Must be an `AITask` member |
| `agent_class` | Must be a `BaseAgent` subclass; BaseAgent itself is rejected |
| `version` | Non-blank string |
| `enabled` | Must be `bool` |

### Forbidden content

No live instances, database sessions, API keys, secrets, tenant context,
prompts, or customer data may be stored in `AgentRegistration`.

---

## AgentRegistry Interface

```python
class AgentRegistry:
    def register(*, registration, factory=None, replace=False) -> None
    def unregister(agent_type) -> bool
    def get(agent_type) -> AgentRegistration
    def contains(agent_type) -> bool
    def list_registrations(*, enabled_only=False) -> tuple[AgentRegistration, ...]
    def create(*, agent_type, tenant_id, config_manager, orchestrator=None) -> BaseAgent
```

---

## Registration Lifecycle

```
register(registration=...) ──► stored in internal dict, keyed by AITask
      │
      │ replace=True      │ replace=False
      ▼                   ▼
  overwrites         AgentAlreadyRegisteredError
  silently
```

Disabled registrations remain discoverable via `get()` and `list_registrations()`.
They cannot be used to create agents.

---

## Duplicate Policy

| Condition | Behavior |
|---|---|
| First registration | Always succeeds |
| Duplicate (replace=False) | Raises `AgentAlreadyRegisteredError` |
| Duplicate (replace=True) | Silently replaces the existing definition |

---

## Disabled Registrations

Setting `enabled=False` on an `AgentRegistration`:

- The registration remains visible via `get()`.
- It is returned by `list_registrations()` (unless `enabled_only=True`).
- `create()` raises `AgentRegistrationDisabledError`.

---

## Tenant-Safe Agent Creation

`create()` executes the following pipeline:

```
1. Validate tenant_id is a non-blank string
2. Look up registration (raises AgentNotRegisteredError if missing)
3. Reject disabled registration (raises AgentRegistrationDisabledError)
4. config_manager.resolve(agent_type=..., tenant_id=...) → AgentConfig
5. Validate resolved config:
   - config.agent_type == requested agent_type
   - config.tenant_id == requested tenant_id
   - config.enabled == True
6. Construct agent:
   - factory(config, orchestrator)  ← if factory is registered
   - agent_class(config)            ← fallback (no orchestrator forwarding)
7. Validate constructed agent:
   - Result is a BaseAgent
   - Agent type matches
   - Tenant ID matches
   - is_setup is False
   - State is IDLE
   - UUID has not previously been issued

8. Record the issued UUID without storing the live agent instance.

9. Return the fresh uninitialized agent.
```

### What `create()` must NOT do

- Call `setup()`, `execute()`, `run()`, or `teardown()`
- Register the agent in `AgentPool`
- Access the database
- Reuse an existing agent instance

Each `create()` call returns a **new agent** with a fresh UUID.

---

## Custom Factories

A factory is a callable with the signature:

```python
AgentFactory = Callable[[AgentConfig, object | None], BaseAgent[Any]]
```

When a factory is registered, `create()` calls `factory(config, orchestrator)`.
When no factory is registered, `create()` calls `agent_class(config)`.

### Falsey factory safety

Factories are stored via **explicit None checks** (`factory if factory is not None else None`).
A factory whose `__bool__` returns `False` (e.g. a mock object) is still
retained.  The following pattern is explicitly avoided:

```python
# WRONG — silently discards a falsey factory
stored = factory or default_factory

# CORRECT — explicit None check
stored = factory if factory is not None else None
```

### Factory result validation

After calling the factory, `create()` verifies that the result:

1. Is a `BaseAgent` instance.
2. Has `config.agent_type` matching the requested type.
3. Has `tenant_id` matching the requested tenant.

Any mismatch raises `AgentRegistryError`.

---

## Thread Safety

All reads and writes of the registrations, factories, and issued UUID tracking are
guarded by a `threading.Lock`.

The lock is **not held** while:
- Resolving configuration via `AgentConfigManager.resolve()`
- Calling a custom factory
- Constructing an agent with `agent_class(config)`

This means multiple threads can safely call `register()`, `contains()`,
`get()`, `list_registrations()`, and `create()` concurrently without
corrupting the internal mapping.

The lock is a `threading.Lock` (not `asyncio.Lock`) because the registry
API is synchronous and may be called from non-async code paths.

---

## Default Registry

```python
registry = create_default_agent_registry()
```

`create_default_agent_registry()` returns a **new registry** each call.
There is no module-level global singleton.

### Registered types

| AITask | Class |
|---|---|
| `AITask.PLANNING` | `PlanningAgent` |
| `AITask.DISPATCH` | `DispatchAgent` |

Both registrations use explicit factories that forward the optional
`orchestrator` parameter.

### Excluded (not yet migrated)

| AITask | Reason |
|---|---|
| `AITask.MONITORING` | Not yet migrated to BaseAgent |
| `AITask.SENTIMENT` | Not yet migrated to BaseAgent |
| `AITask.COMMUNICATION` | Not yet migrated to BaseAgent |
| `AITask.CLOSURE` | Not yet migrated to BaseAgent |

These types must not be registered until their agents fully implement
the `BaseAgent` contract.

---

## Exceptions

| Exception | When raised |
|---|---|
| `AgentRegistryError` | Base exception for all registry errors |
| `AgentAlreadyRegisteredError` | Duplicate registration without `replace=True` |
| `AgentNotRegisteredError` | `get()` or `create()` for unregistered type |
| `AgentRegistrationDisabledError` | `create()` called for a disabled registration |

These exceptions are distinct from `AgentPool` exceptions because the
registry and pool represent different concepts.

---

## Test Coverage

| File | Statements | Branches | Coverage |
|---|---|---|---|
| `agent_registration.py` | 27 | 14 | 90% |
| `agent_registry.py` | 96 | 32 | 97% |
| **Total** | **123** | **46** | **95%** |

| Category | Tests |
|---|---|
| Schema validation | 7 |
| Registration management | 12 |
| Agent creation validation | 12 |
| Custom factory behavior | 5 |
| Default registry | 5 |
| Structural and concurrency | 3 |
| **Total** | **44** |

---

## Limitations

1. **No distributed discovery** — The registry is in-process only.
   Cross-process agent tracking requires a Redis-backed implementation
   (not part of Story 1.3).

2. **No automatic recovery** — The registry does not reload persisted
   agent state on startup.  That is a separate concern.

3. **No health integration** — The registry does not expose health
   status or liveness probes.  That belongs in Story 1.7 (Health Monitoring).

4. **Fallback construction is `agent_class(config)` only** — The
   no-factory fallback calls `agent_class(config)` because `BaseAgent`
   only requires `config`.  Orchestrator forwarding via the fallback
   path is intentionally not supported; register a factory if
   orchestrator injection is needed.

5. **No provider dependencies in bootstrap** — `create_default_agent_registry()`
   does not import `ai_orchestrator` or any other provider-related dependency.
   Factories receive `orchestrator=None` by default unless the caller supplies
   one at creation time.

---

## Future Integration

### Agent Communication Bus (Story 1.6)

When the Communication Bus is implemented, `create()` may optionally
accept a `bus` parameter to inject into agents that support it.

### Health Monitoring (Story 1.7)

The registry's `list_registrations()` method can feed the health monitor
with the set of known agent types.

### Agent Registry — Redis layer

A future Redis-backed registry adapter can implement the same interface
(`register`, `get`, `contains`, `create`) to enable cross-process agent
discovery without modifying callers.
