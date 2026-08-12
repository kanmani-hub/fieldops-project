# Agent Configuration Management (Story 1.4)

## Purpose

The Agent Configuration Manager acts as the dynamic resolution controller for FieldOps Commander AI agent settings. It compiles global default configurations, agent-type overrides, and trusted runtime parameters into a validated, immutable `AgentConfig` instance for a specific tenant and task context.

---

## Architecture Position

Within the 4-layer architecture of the FieldOps AI platform, the configuration manager sits in **Layer 3 (AI Integration Layer)**:
* It is instantiated and queried by **Layer 2 (Business Services)** (e.g., `PlanningService`, `DispatchService`) or integration adapters.
* It wraps raw configuration inputs into validated Pydantic models required to initialize **Layer 4 (AI Runtime)** agents.

```
+--------------------------+
|  Layer 2: Business Serv  |
+-------------+------------+
              |
              | 1. resolve(agent_type, tenant_id, overrides)
              v
+-------------+------------+
|    AgentConfigManager    |<---> [ ai.yaml / ConfigLoader ]
+-------------+------------+
              |
              | 2. Returns validated AgentConfig
              v
+-------------+------------+
|  Layer 4: AI Agents/Pool |
+--------------------------+
```

---

## Existing Components Reused

* `app/services/ai/FieldOpsAI/config/config_loader.py`: Exposes `ai.yaml` config dictionaries via the public `get_config_snapshot()` method.
* `app/services/ai/FieldOpsAI/schemas/agent_config.py`: Validates configurations and provides schema-level defaults.
* `app/services/ai/FieldOpsAI/schemas/ai_task.py`: Restricts configurations to the 6 authoritative agent tasks (`AITask`).

---

## Configuration Precedence

When resolving settings, priorities are evaluated from highest to lowest:
1. **Runtime Overrides**: Trusted parameters supplied directly to the `resolve()` call (e.g. `overrides={"timeout_seconds": 10}`).
2. **Agent-Specific Overrides**: YAML values set for a particular agent (e.g., `planning: {timeout_seconds: 15.0}`).
3. **Shared Platform Defaults**: Global values declared under the `agents.defaults` YAML block.
4. **Schema Defaults**: Hardcoded Pydantic defaults in the `AgentConfig` class itself.

---

## Supported Override Fields

Only the following transient runtime fields can be overridden:
* `agent_version` (string)
* `timeout_seconds` (float)
* `max_retries` (integer)
* `enabled` (strict boolean)

---

## Tenant and Agent Identity Protection

To prevent security exploits or cross-tenant contamination:
* The identity parameters `agent_type` and `tenant_id` are authoritative parameters of `resolve()`.
* **Attempts to supply `agent_type` or `tenant_id` in the overrides mapping are strictly rejected** with an `AgentConfigurationOverrideError`.
* Unknown, misspelled, or unsupported override keys are rejected to avoid silent failures.

---

## YAML Structure

The new dynamic runtime configuration sections are declared under the `agents` block in `app/services/ai/FieldOpsAI/config/ai.yaml`:

```yaml
agents:
  defaults:
    agent_version: "1.0"
    timeout_seconds: 30.0
    max_retries: 2
    enabled: true

  planning:
    timeout_seconds: 15.0  # Agent-specific override
  dispatch: {}
  monitoring: {}
  sentiment: {}
  communication: {}
  closure: {}
```

---

## Validation Behavior

* **Input Validation**: Ensures `agent_type` belongs to `AITask`, `tenant_id` is a non-blank string, and `overrides` is any `Mapping`.
* **YAML Validation**: Ensures `agents`, `defaults`, and agent-specific sections in YAML are valid mappings.
* **Immutability Validation**: Instantiates an immutable `AgentConfig` model using `frozen=True` and strict boolean coercion for `enabled`.

---

## Error Behavior

Explicit configuration exception classes are defined:
* `AgentConfigurationError`: Base class for configuration failures. Raised on input validation errors, YAML malformations, or Pydantic validation failures. Preserves the original validation error as its cause (`__cause__`).
* `AgentConfigurationNotFoundError`: Raised when the configuration dictionary from the config loader is invalid, or if loader fails during instantiation or snapshot retrieval.
* `AgentConfigurationOverrideError`: Raised when the client passes blocked keys (`agent_type`, `tenant_id`) or unknown keys in the overrides mapping.

---

## Security Rules

* Sensitive values, API keys, secrets, or configuration dumps are **never** logged or embedded inside exception messages.
* Tenant identifiers are passed explicitly in memory and are never written to `ai.yaml`.

---

## Example Usage

```python
from app.services.ai.FieldOpsAI.config.agent_config_manager import AgentConfigManager
from app.services.ai.FieldOpsAI.schemas.ai_task import AITask

config_manager = AgentConfigManager()

# Resolving configuration with a runtime override
config = config_manager.resolve(
    agent_type=AITask.PLANNING,
    tenant_id="tenant-alpha",
    overrides={"timeout_seconds": 12.5}
)

print(config.timeout_seconds)  # Output: 12.5
```

---

## Current Limitations

All configuration data must be present locally within `ai.yaml` or provided as in-memory overrides. Dynamic discovery or database polling is not supported in this story.

---

## Future Database or Redis Extensions

The composition pattern allows a subclass or helper to inject configuration mappings dynamically. For example, a database override client could fetch values from a `tenant_agent_configurations` SQL table and pass them as a custom mapping to `resolve()`, without modifying `AgentConfigManager` itself.

---

## Testing Coverage

The test suite `tests/test_agent_config_manager.py` verifies 23 distinct scenarios including priority resolution, identity protection, override rejection, typing strictness, safe fallback when YAML blocks are missing, and repeated resolution determinism.

---

## Story 1.4 Completion Criteria

* [x] Create `app/services/ai/FieldOpsAI/config/agent_config_manager.py`.
* [x] Extend `app/services/ai/FieldOpsAI/config/ai.yaml` with default agents configuration block.
* [x] Achieve 100% statement and branch coverage of `AgentConfigManager` using `tests/test_agent_config_manager.py`.
* [x] Verify existing agent base, pool, and lifecycle tests run and pass without modification.
