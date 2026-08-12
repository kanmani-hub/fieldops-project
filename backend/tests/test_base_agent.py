"""
Tests for the foundational FieldOps AI BaseAgent contract.
"""

from __future__ import annotations

import asyncio

from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.context import correlation_id_ctx
from app.services.ai.FieldOpsAI.agents.base import (
    AgentDisabledError,
    AgentLifecycleError,
    AgentState,
    BaseAgent,
    TenantIsolationError,
)
from app.services.ai.FieldOpsAI.schemas.agent_config import AgentConfig
from app.services.ai.FieldOpsAI.schemas.ai_task import AITask

@pytest.fixture
def anyio_backend() -> str:
    """
    Force AnyIO tests to use Python's asyncio backend.
    """

    return "asyncio"

class SuccessfulAgent(BaseAgent[dict[str, Any]]):
    """
    Test agent that returns its supplied context.
    """

    async def run(
        self,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "success": True,
            "tenant_id": context["tenant_id"],
            "correlation_id": correlation_id_ctx.get(),
        }


class FailingAgent(BaseAgent[dict[str, Any]]):
    """
    Test agent that always raises an execution error.
    """

    async def run(
        self,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        raise RuntimeError("Test execution failure.")
class DefaultRunAgent(BaseAgent[dict[str, Any]]):
    """
    Test agent that invokes BaseAgent's default abstract run body.
    """

    async def run(
        self,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        return await super().run(context)


class BlockingAgent(BaseAgent[dict[str, Any]]):
    """
    Test agent that remains running until the test releases it.
    """

    def __init__(
        self,
        config: AgentConfig,
    ) -> None:
        super().__init__(config)

        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def run(
        self,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        self.started.set()

        await self.release.wait()

        return {
            "success": True,
        }

def build_config(
    *,
    tenant_id: str = "tenant-001",
    enabled: bool = True,
) -> AgentConfig:
    """
    Create a valid AgentConfig for BaseAgent tests.
    """

    return AgentConfig(
        agent_type=AITask.PLANNING,
        tenant_id=tenant_id,
        agent_version="1.0",
        timeout_seconds=30,
        max_retries=2,
        enabled=enabled,
    )


def test_base_agent_cannot_be_instantiated_directly() -> None:
    """
    BaseAgent must remain abstract because run is not implemented.
    """

    with pytest.raises(TypeError):
        BaseAgent(build_config())  # type: ignore[abstract]


def test_agent_config_accepts_valid_values() -> None:
    """
    A valid common configuration should be accepted.
    """

    config = build_config()

    assert config.agent_type is AITask.PLANNING
    assert config.tenant_id == "tenant-001"
    assert config.agent_version == "1.0"
    assert config.timeout_seconds == 30
    assert config.max_retries == 2
    assert config.enabled is True


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("tenant_id", "   "),
        ("agent_version", "   "),
        ("timeout_seconds", 0),
        ("timeout_seconds", -1),
        ("max_retries", -1),
        ("enabled", "yes"),
    ],
)
def test_agent_config_rejects_invalid_values(
    field_name: str,
    invalid_value: Any,
) -> None:
    """
    Invalid common configuration values must be rejected.
    """

    values: dict[str, Any] = {
        "agent_type": AITask.PLANNING,
        "tenant_id": "tenant-001",
        "agent_version": "1.0",
        "timeout_seconds": 30,
        "max_retries": 2,
        "enabled": True,
    }

    values[field_name] = invalid_value

    with pytest.raises(ValidationError):
        AgentConfig(**values)


def test_agent_config_rejects_unknown_agent_type() -> None:
    """
    AgentConfig should reuse and enforce the existing AITask enum.
    """

    with pytest.raises(ValidationError):
        AgentConfig(
            agent_type="unknown-agent",  # type: ignore[arg-type]
            tenant_id="tenant-001",
        )


def test_agent_config_is_immutable() -> None:
    """
    Agent identity and tenant configuration cannot change at runtime.
    """

    config = build_config()

    with pytest.raises(ValidationError):
        config.tenant_id = "tenant-002"


def test_agent_initial_properties() -> None:
    """
    A new agent should have a UUID4 identity and IDLE state.
    """

    agent = SuccessfulAgent(build_config())

    assert isinstance(agent.agent_id, UUID)
    assert agent.agent_id.version == 4

    assert agent.tenant_id == "tenant-001"
    assert agent.config.agent_type is AITask.PLANNING
    assert agent.state is AgentState.IDLE
    assert agent.is_setup is False

    assert agent.created_at.tzinfo is not None


@pytest.mark.anyio
async def test_setup_marks_agent_ready() -> None:
    """
    Setup should prepare the agent while leaving it IDLE.
    """

    agent = SuccessfulAgent(build_config())

    await agent.setup()

    assert agent.is_setup is True
    assert agent.state is AgentState.IDLE


@pytest.mark.anyio
async def test_setup_is_idempotent() -> None:
    """
    Calling setup more than once should not fail.
    """

    agent = SuccessfulAgent(build_config())

    await agent.setup()
    await agent.setup()

    assert agent.is_setup is True
    assert agent.state is AgentState.IDLE


@pytest.mark.anyio
async def test_execute_requires_setup() -> None:
    """
    An agent cannot execute before setup completes.
    """

    agent = SuccessfulAgent(build_config())

    with pytest.raises(
        AgentLifecycleError,
        match="setup must complete",
    ):
        await agent.execute(
            {
                "tenant_id": "tenant-001",
            }
        )

    assert agent.state is AgentState.IDLE


@pytest.mark.anyio
async def test_execute_success_returns_result_and_restores_idle() -> None:
    """
    Successful execution should return output and restore IDLE state.
    """

    agent = SuccessfulAgent(build_config())

    await agent.setup()

    result = await agent.execute(
        {
            "tenant_id": "tenant-001",
        },
        correlation_id="correlation-test-001",
    )

    assert result["success"] is True
    assert result["tenant_id"] == "tenant-001"
    assert result["correlation_id"] == "correlation-test-001"

    assert agent.state is AgentState.IDLE


@pytest.mark.anyio
async def test_execute_restores_previous_correlation_context() -> None:
    """
    Agent execution must not leak its correlation ID to later work.
    """

    token = correlation_id_ctx.set(
        "outer-correlation-id"
    )

    try:
        agent = SuccessfulAgent(build_config())
        await agent.setup()

        result = await agent.execute(
            {
                "tenant_id": "tenant-001",
            },
            correlation_id="agent-correlation-id",
        )

        assert (
            result["correlation_id"]
            == "agent-correlation-id"
        )

        assert (
            correlation_id_ctx.get()
            == "outer-correlation-id"
        )

    finally:
        correlation_id_ctx.reset(token)


@pytest.mark.anyio
async def test_execute_uses_existing_correlation_id() -> None:
    """
    Existing request correlation IDs should be preserved.
    """

    token = correlation_id_ctx.set(
        "existing-request-id"
    )

    try:
        agent = SuccessfulAgent(build_config())
        await agent.setup()

        result = await agent.execute(
            {
                "tenant_id": "tenant-001",
            }
        )

        assert (
            result["correlation_id"]
            == "existing-request-id"
        )

    finally:
        correlation_id_ctx.reset(token)


@pytest.mark.anyio
async def test_execute_rejects_missing_tenant() -> None:
    """
    Every agent execution context must identify its tenant.
    """

    agent = SuccessfulAgent(build_config())
    await agent.setup()

    with pytest.raises(
        TenantIsolationError,
        match="must include tenant_id",
    ):
        await agent.execute({})

    assert agent.state is AgentState.IDLE


@pytest.mark.anyio
async def test_execute_rejects_cross_tenant_context() -> None:
    """
    An agent must never process another tenant's context.
    """

    agent = SuccessfulAgent(
        build_config(tenant_id="tenant-001")
    )

    await agent.setup()

    with pytest.raises(
        TenantIsolationError,
        match="does not belong",
    ):
        await agent.execute(
            {
                "tenant_id": "tenant-999",
            }
        )

    assert agent.state is AgentState.IDLE


@pytest.mark.anyio
async def test_disabled_agent_cannot_execute() -> None:
    """
    Disabled agents should reject execution.
    """

    agent = SuccessfulAgent(
        build_config(enabled=False)
    )

    await agent.setup()

    with pytest.raises(
        AgentDisabledError,
        match="disabled",
    ):
        await agent.execute(
            {
                "tenant_id": "tenant-001",
            }
        )

    assert agent.state is AgentState.IDLE


@pytest.mark.anyio
async def test_execution_failure_changes_state_to_error() -> None:
    """
    Unhandled execution errors should place the agent in ERROR.
    """

    agent = FailingAgent(build_config())
    await agent.setup()

    with pytest.raises(
        RuntimeError,
        match="Test execution failure",
    ):
        await agent.execute(
            {
                "tenant_id": "tenant-001",
            }
        )

    assert agent.state is AgentState.ERROR

    health = await agent.health_check()

    assert health["healthy"] is False
    assert health["error_count"] == 1
    assert health["last_error_at"] is not None


@pytest.mark.anyio
async def test_error_agent_cannot_run_again() -> None:
    """
    An ERROR agent requires recovery before another execution.
    """

    agent = FailingAgent(build_config())
    await agent.setup()

    with pytest.raises(RuntimeError):
        await agent.execute(
            {
                "tenant_id": "tenant-001",
            }
        )

    with pytest.raises(
        AgentLifecycleError,
        match="ERROR state",
    ):
        await agent.execute(
            {
                "tenant_id": "tenant-001",
            }
        )


@pytest.mark.anyio
async def test_teardown_terminates_agent() -> None:
    """
    Teardown should terminate the agent and clear setup status.
    """

    agent = SuccessfulAgent(build_config())

    await agent.setup()
    await agent.teardown()

    assert agent.is_setup is False
    assert agent.state is AgentState.TERMINATED


@pytest.mark.anyio
async def test_teardown_is_idempotent() -> None:
    """
    Calling teardown more than once should be safe.
    """

    agent = SuccessfulAgent(build_config())

    await agent.setup()
    await agent.teardown()
    await agent.teardown()

    assert agent.state is AgentState.TERMINATED


@pytest.mark.anyio
async def test_terminated_agent_cannot_be_set_up_again() -> None:
    """
    A terminated agent cannot be restarted through normal setup.
    """

    agent = SuccessfulAgent(build_config())

    await agent.setup()
    await agent.teardown()

    with pytest.raises(
        AgentLifecycleError,
        match="terminated agent",
    ):
        await agent.setup()


@pytest.mark.anyio
async def test_async_context_manager_sets_up_and_tears_down() -> None:
    """
    Async context management should automate setup and teardown.
    """

    agent = SuccessfulAgent(build_config())

    async with agent as active_agent:
        assert active_agent is agent
        assert agent.is_setup is True
        assert agent.state is AgentState.IDLE

        result = await agent.execute(
            {
                "tenant_id": "tenant-001",
            }
        )

        assert result["success"] is True

    assert agent.is_setup is False
    assert agent.state is AgentState.TERMINATED


@pytest.mark.anyio
async def test_context_manager_does_not_suppress_exception() -> None:
    """
    Exceptions inside async with must still reach the caller.
    """

    agent = SuccessfulAgent(build_config())

    with pytest.raises(
        ValueError,
        match="Context block failure",
    ):
        async with agent:
            raise ValueError("Context block failure.")

    assert agent.state is AgentState.TERMINATED

    health = await agent.health_check()

    assert health["healthy"] is False
    assert health["error_count"] == 1


@pytest.mark.anyio
async def test_health_check_returns_safe_agent_status() -> None:
    """
    Health information should reflect setup and lifecycle state.
    """

    agent = SuccessfulAgent(build_config())

    before_setup = await agent.health_check()

    assert before_setup["healthy"] is False
    assert before_setup["state"] == "idle"
    assert before_setup["is_setup"] is False

    await agent.setup()

    after_setup = await agent.health_check()

    assert after_setup["healthy"] is True
    assert after_setup["state"] == "idle"
    assert after_setup["is_setup"] is True
    assert after_setup["tenant_id"] == "tenant-001"
    assert after_setup["agent_type"] == "planning"

@pytest.mark.anyio
async def test_default_run_raises_not_implemented() -> None:
    """
    BaseAgent's default run body must not execute agent logic.
    """

    agent = DefaultRunAgent(build_config())
    await agent.setup()

    with pytest.raises(NotImplementedError):
        await agent.execute(
            {
                "tenant_id": "tenant-001",
            }
        )

    assert agent.state is AgentState.ERROR

@pytest.mark.anyio
async def test_execute_generates_correlation_id() -> None:
    """
    Execution should generate a UUID4 correlation ID when none exists.
    """

    assert correlation_id_ctx.get() == ""

    agent = SuccessfulAgent(build_config())
    await agent.setup()

    result = await agent.execute(
        {
            "tenant_id": "tenant-001",
        }
    )

    generated_id = UUID(
        result["correlation_id"]
    )

    assert generated_id.version == 4
    assert correlation_id_ctx.get() == ""

@pytest.mark.anyio
async def test_execute_rejects_non_dictionary_context() -> None:
    """
    Agent execution context must be a dictionary.
    """

    agent = SuccessfulAgent(build_config())
    await agent.setup()

    with pytest.raises(
        TypeError,
        match="must be a dictionary",
    ):
        await agent.execute(
            ["tenant-001"]  # type: ignore[arg-type]
        )

    assert agent.state is AgentState.IDLE

@pytest.mark.anyio
async def test_execute_rejects_blank_context_tenant() -> None:
    """
    A blank tenant ID must be rejected.
    """

    agent = SuccessfulAgent(build_config())
    await agent.setup()

    with pytest.raises(
        TenantIsolationError,
        match="must not be blank",
    ):
        await agent.execute(
            {
                "tenant_id": "   ",
            }
        )

    assert agent.state is AgentState.IDLE


@pytest.mark.anyio
async def test_paused_agent_cannot_execute() -> None:
    """
    A paused agent must reject new work.
    """

    agent = SuccessfulAgent(build_config())
    await agent.setup()

    agent._transition_to(
        AgentState.PAUSED,
        event="test_agent_paused",
    )

    with pytest.raises(
        AgentLifecycleError,
        match="paused agent",
    ):
        await agent.execute(
            {
                "tenant_id": "tenant-001",
            }
        )

    assert agent.state is AgentState.PAUSED


@pytest.mark.anyio
async def test_running_agent_rejects_second_execution() -> None:
    """
    An agent must not execute two tasks concurrently.
    """

    agent = BlockingAgent(build_config())
    await agent.setup()

    first_execution = asyncio.create_task(
        agent.execute(
            {
                "tenant_id": "tenant-001",
            }
        )
    )

    await agent.started.wait()

    assert agent.state is AgentState.RUNNING

    with pytest.raises(
        AgentLifecycleError,
        match="already running",
    ):
        await agent.execute(
            {
                "tenant_id": "tenant-001",
            }
        )

    agent.release.set()

    result = await first_execution

    assert result["success"] is True
    assert agent.state is AgentState.IDLE


@pytest.mark.anyio
async def test_terminated_agent_cannot_execute() -> None:
    """
    An agent cannot execute after teardown.
    """

    agent = SuccessfulAgent(build_config())

    await agent.setup()
    await agent.teardown()

    with pytest.raises(
        AgentLifecycleError,
        match="terminated agent",
    ):
        await agent.execute(
            {
                "tenant_id": "tenant-001",
            }
        )

    assert agent.state is AgentState.TERMINATED

@pytest.mark.anyio
async def test_disabled_agent_health_is_unhealthy() -> None:
    """
    A disabled agent must not report itself as healthy.
    """

    agent = SuccessfulAgent(
        build_config(enabled=False)
    )

    await agent.setup()

    health = await agent.health_check()

    assert health["enabled"] is False
    assert health["is_setup"] is True
    assert health["healthy"] is False

@pytest.mark.anyio
async def test_cross_tenant_rejection_uses_existing_correlation_id() -> None:
    """
    Tenant rejection logs should reuse the request correlation ID.
    """

    token = correlation_id_ctx.set(
        "tenant-rejection-correlation"
    )

    try:
        agent = SuccessfulAgent(build_config())
        await agent.setup()

        with pytest.raises(TenantIsolationError):
            await agent.execute(
                {
                    "tenant_id": "tenant-999",
                }
            )

        assert (
            correlation_id_ctx.get()
            == "tenant-rejection-correlation"
        )

    finally:
        correlation_id_ctx.reset(token)

@pytest.mark.anyio
async def test_context_manager_does_not_count_error_twice() -> None:
    """
    An execution error must not be counted again during context exit.
    """

    agent = FailingAgent(build_config())

    with pytest.raises(
        RuntimeError,
        match="Test execution failure",
    ):
        async with agent:
            await agent.execute(
                {
                    "tenant_id": "tenant-001",
                }
            )

    assert agent.state is AgentState.TERMINATED

    health = await agent.health_check()

    assert health["error_count"] == 1
    assert health["healthy"] is False