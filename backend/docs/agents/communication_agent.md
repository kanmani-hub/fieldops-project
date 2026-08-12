# CommunicationAgent Migration Documentation

This document describes the design, constructor contract, lifecycle management, and architectural responsibilities of the migrated `CommunicationAgent` within the FieldOps AI agent framework.

## BaseAgent Inheritance & Task Configuration
The `CommunicationAgent` inherits from the abstract base class `BaseAgent[CommunicationDecision]`.
- It validates that the configuration is specifically configured for communication tasks. A configuration whose `agent_type` is not `AITask.COMMUNICATION` is explicitly rejected in the constructor with a `ValueError`.
- It overrides the standard `timeout_seconds` settings via the YAML configuration defaults (configured to `5.0` seconds in `ai.yaml`).

## Constructor Contract
The constructor signature is:
```python
def __init__(
    self,
    config: AgentConfig,
    orchestrator: Optional[AIOrchestrator] = None,
) -> None
```
- It explicitly propagates the `AgentConfig` to `super().__init__(config)`.
- It preserves orchestrator dependency injection. The existing global `ai_orchestrator` is used only if `orchestrator` is `None` (using strict `is None` checking).

## Tenant Isolation & Validation
The agent runtime strictly enforces tenant isolation:
- `BaseAgent` validates that `tenant_id` is present in the execution dictionary and matches the configured tenant before `run()` is executed. Any mismatch raises a `TenantIsolationError`.
- `CommunicationContext` enforces `extra="forbid"`. Because `tenant_id` is lifecycle metadata, it must not be sent to the AI domain schema.
- In `run()`, the agent copies the context dictionary, safely removes `tenant_id`, and validates the remaining data with `CommunicationContext.model_validate(...)` before executing the orchestrator.

## Async Run Flow
The asynchronous run logic executes via:
```python
async def run(
    self,
    context: dict[str, Any],
) -> CommunicationDecision
```
- It converts context dictionary to a validated `CommunicationContext` schema.
- Because `AIOrchestrator.execute` is a synchronous call, it is offloaded using `asyncio.to_thread` to prevent blocking the async event loop.
- It invokes the orchestrator exactly once, passing:
  - `task=AITask.COMMUNICATION`
  - `context=validated_context.model_dump(mode="json")`
  - `response_schema=CommunicationDecision`
- It validates that the returned value is a `CommunicationDecision`.

## Synchronous Compatibility Limitation
The agent preserves a synchronous compatibility method:
```python
def generate(
    self,
    context: CommunicationContext,
) -> CommunicationDecision
```
- It rejects execution from an active event loop, raising a `RuntimeError` if called within one.
- It is operation-scoped and single-use: since lifecycle execution terminates the agent on exit, a terminated agent cannot be silently reused. Re-invoking `generate()` on a terminated agent raises an `AgentLifecycleError`.
- It adds the authoritative agent `tenant_id` to the execution dictionary.
- It executes through `AgentLifecycle` and `AgentPool`.
- It verifies `AgentResultStatus.SUCCESS` and checks that the result output is a `CommunicationDecision`.
- It makes exactly one orchestrator call and always tears down the agent through the lifecycle context manager.

## Operational Logging
To protect sensitive data, the agent enforces a zero-PII logging policy:
- **Never log**: Customer names, technician names, phone numbers, email addresses, locations, prompts, generated messages, raw provider responses, API keys, or exception text containing those values.
- **Allowed logging**: Safe operational metadata such as `agent_id`, `agent_type`, `channel`, `notification_type`, elapsed time, and `correlation_id`.

## Service & Architectural Boundaries
The communication subsystem enforces a strict division of responsibilities:
- **No side effects**: The `CommunicationAgent` never connects to databases, sends SMS/emails, makes real Groq/external API calls directly, or invokes WebSockets.
- **Service Ownership**: The `CommunicationService` remains the sole owner of PII sanitization (before calling the agent), guardrails pipeline validation, fallback rendering (Jinja2), placeholder restoration, and audit row persistence. The agent remains focused purely on producing a recommendation.
