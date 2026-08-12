"""
Tests for the local FieldOps AI AgentPool.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.services.ai.FieldOpsAI.agents.base import (
    AgentState,
    BaseAgent,
)
from app.services.ai.FieldOpsAI.runtime.agent_pool import (
    AgentAlreadyRegisteredError,
    AgentNotRegisteredError,
    AgentPool,
    AgentPoolError,
    AgentPoolTenantError,
)
from app.services.ai.FieldOpsAI.schemas.agent_config import (
    AgentConfig,
)
from app.services.ai.FieldOpsAI.schemas.ai_task import AITask


@pytest.fixture
def anyio_backend() -> str:
    """
    Force AnyIO tests to use Python asyncio.
    """

    return "asyncio"


class PoolTestAgent(BaseAgent[dict[str, Any]]):
    """
    Minimal agent used only by AgentPool tests.
    """

    async def run(
        self,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "success": True,
            "tenant_id": context["tenant_id"],
        }


def build_agent(
    *,
    tenant_id: str = "tenant-001",
    agent_type: AITask = AITask.PLANNING,
) -> PoolTestAgent:
    """
    Create an agent for pool tests.
    """

    config = AgentConfig(
        agent_type=agent_type,
        tenant_id=tenant_id,
        agent_version="1.0",
        timeout_seconds=30,
        max_retries=2,
        enabled=True,
    )

    return PoolTestAgent(config)


@pytest.mark.anyio
async def test_new_pool_is_empty() -> None:
    """
    A newly created pool should contain no agents.
    """

    pool = AgentPool()

    assert await pool.count() == 0
    assert (
        await pool.count(
            tenant_id="tenant-001"
        )
        == 0
    )


@pytest.mark.anyio
async def test_register_agent_successfully() -> None:
    """
    A setup agent should be registered successfully.
    """

    pool = AgentPool()
    agent = build_agent()

    await agent.setup()
    await pool.register(agent)

    assert await pool.count() == 1

    assert await pool.contains(
        agent_id=agent.agent_id,
        tenant_id="tenant-001",
    )


@pytest.mark.anyio
async def test_register_requires_base_agent() -> None:
    """
    AgentPool must reject unrelated Python objects.
    """

    pool = AgentPool()

    with pytest.raises(
        TypeError,
        match="BaseAgent",
    ):
        await pool.register(
            object()  # type: ignore[arg-type]
        )


@pytest.mark.anyio
async def test_register_requires_setup() -> None:
    """
    An agent must complete setup before registration.
    """

    pool = AgentPool()
    agent = build_agent()

    with pytest.raises(
        AgentPoolError,
        match="setup must complete",
    ):
        await pool.register(agent)

    assert await pool.count() == 0


@pytest.mark.anyio
async def test_register_rejects_terminated_agent() -> None:
    """
    A terminated agent cannot return to the active pool.
    """

    pool = AgentPool()
    agent = build_agent()

    await agent.setup()
    await agent.teardown()

    with pytest.raises(
        AgentPoolError,
        match="terminated agent",
    ):
        await pool.register(agent)

    assert await pool.count() == 0


@pytest.mark.anyio
async def test_registration_is_idempotent_for_same_object() -> None:
    """
    Registering the same object twice should be safe.
    """

    pool = AgentPool()
    agent = build_agent()

    await agent.setup()

    await pool.register(agent)
    await pool.register(agent)

    assert await pool.count() == 1


@pytest.mark.anyio
async def test_duplicate_agent_id_for_another_object_is_rejected(
) -> None:
    """
    One UUID must never identify two different agent objects.
    """

    pool = AgentPool()

    first_agent = build_agent()
    second_agent = build_agent()

    await first_agent.setup()
    await second_agent.setup()

    second_agent._agent_id = first_agent.agent_id

    await pool.register(first_agent)

    with pytest.raises(
        AgentAlreadyRegisteredError,
        match="already registered",
    ):
        await pool.register(second_agent)

    assert await pool.count() == 1


@pytest.mark.anyio
async def test_get_returns_registered_agent() -> None:
    """
    A tenant should retrieve its own registered agent.
    """

    pool = AgentPool()
    agent = build_agent()

    await agent.setup()
    await pool.register(agent)

    result = await pool.get(
        agent_id=agent.agent_id,
        tenant_id="tenant-001",
    )

    assert result is agent


@pytest.mark.anyio
async def test_get_unknown_agent_raises_error() -> None:
    """
    Retrieving a missing agent should fail clearly.
    """

    pool = AgentPool()
    agent = build_agent()

    with pytest.raises(
        AgentNotRegisteredError,
        match="not registered",
    ):
        await pool.get(
            agent_id=agent.agent_id,
            tenant_id="tenant-001",
        )


@pytest.mark.anyio
async def test_get_enforces_tenant_isolation() -> None:
    """
    One tenant cannot retrieve another tenant's agent.
    """

    pool = AgentPool()
    agent = build_agent(
        tenant_id="tenant-001"
    )

    await agent.setup()
    await pool.register(agent)

    with pytest.raises(
        AgentPoolTenantError,
        match="does not belong",
    ):
        await pool.get(
            agent_id=agent.agent_id,
            tenant_id="tenant-999",
        )


@pytest.mark.anyio
async def test_contains_returns_true_for_owner() -> None:
    """
    contains should return true for the owning tenant.
    """

    pool = AgentPool()
    agent = build_agent()

    await agent.setup()
    await pool.register(agent)

    result = await pool.contains(
        agent_id=agent.agent_id,
        tenant_id="tenant-001",
    )

    assert result is True


@pytest.mark.anyio
async def test_contains_hides_cross_tenant_agent() -> None:
    """
    Cross-tenant agents should appear absent.
    """

    pool = AgentPool()
    agent = build_agent(
        tenant_id="tenant-001"
    )

    await agent.setup()
    await pool.register(agent)

    result = await pool.contains(
        agent_id=agent.agent_id,
        tenant_id="tenant-999",
    )

    assert result is False


@pytest.mark.anyio
async def test_contains_returns_false_for_missing_agent() -> None:
    """
    contains should return false for an unknown UUID.
    """

    pool = AgentPool()
    agent = build_agent()

    result = await pool.contains(
        agent_id=agent.agent_id,
        tenant_id="tenant-001",
    )

    assert result is False


@pytest.mark.anyio
async def test_unregister_returns_removed_agent() -> None:
    """
    unregister should remove and return the agent.
    """

    pool = AgentPool()
    agent = build_agent()

    await agent.setup()
    await pool.register(agent)

    removed_agent = await pool.unregister(
        agent_id=agent.agent_id,
        tenant_id="tenant-001",
    )

    assert removed_agent is agent
    assert await pool.count() == 0

    assert not await pool.contains(
        agent_id=agent.agent_id,
        tenant_id="tenant-001",
    )


@pytest.mark.anyio
async def test_unregister_unknown_agent_raises_error() -> None:
    """
    Removing an unknown agent should fail clearly.
    """

    pool = AgentPool()
    agent = build_agent()

    with pytest.raises(
        AgentNotRegisteredError,
        match="not registered",
    ):
        await pool.unregister(
            agent_id=agent.agent_id,
            tenant_id="tenant-001",
        )


@pytest.mark.anyio
async def test_unregister_enforces_tenant_isolation() -> None:
    """
    One tenant cannot remove another tenant's agent.
    """

    pool = AgentPool()
    agent = build_agent(
        tenant_id="tenant-001"
    )

    await agent.setup()
    await pool.register(agent)

    with pytest.raises(
        AgentPoolTenantError,
        match="does not belong",
    ):
        await pool.unregister(
            agent_id=agent.agent_id,
            tenant_id="tenant-999",
        )

    assert await pool.count() == 1


@pytest.mark.anyio
async def test_list_agents_returns_only_one_tenant() -> None:
    """
    Listing agents must enforce tenant isolation.
    """

    pool = AgentPool()

    tenant_one_agent = build_agent(
        tenant_id="tenant-001"
    )

    tenant_two_agent = build_agent(
        tenant_id="tenant-002"
    )

    await tenant_one_agent.setup()
    await tenant_two_agent.setup()

    await pool.register(tenant_one_agent)
    await pool.register(tenant_two_agent)

    results = await pool.list_agents(
        tenant_id="tenant-001",
    )

    assert results == (tenant_one_agent,)


@pytest.mark.anyio
async def test_list_agents_filters_by_agent_type() -> None:
    """
    Listing can filter by FieldOps AI agent type.
    """

    pool = AgentPool()

    planning_agent = build_agent(
        agent_type=AITask.PLANNING
    )

    communication_agent = build_agent(
        agent_type=AITask.COMMUNICATION
    )

    await planning_agent.setup()
    await communication_agent.setup()

    await pool.register(planning_agent)
    await pool.register(communication_agent)

    results = await pool.list_agents(
        tenant_id="tenant-001",
        agent_type="planning",
    )

    assert results == (planning_agent,)


@pytest.mark.anyio
async def test_list_agents_normalizes_agent_type() -> None:
    """
    Agent type filters should ignore whitespace and case.
    """

    pool = AgentPool()

    planning_agent = build_agent(
        agent_type=AITask.PLANNING
    )

    await planning_agent.setup()
    await pool.register(planning_agent)

    results = await pool.list_agents(
        tenant_id="tenant-001",
        agent_type="  PLANNING  ",
    )

    assert results == (planning_agent,)


@pytest.mark.anyio
async def test_list_agents_rejects_blank_agent_type() -> None:
    """
    A blank agent type filter is invalid.
    """

    pool = AgentPool()

    with pytest.raises(
        ValueError,
        match="agent_type must not be blank",
    ):
        await pool.list_agents(
            tenant_id="tenant-001",
            agent_type="   ",
        )


@pytest.mark.anyio
async def test_list_agents_filters_by_state() -> None:
    """
    Listing can filter by lifecycle state.
    """

    pool = AgentPool()

    idle_agent = build_agent()
    paused_agent = build_agent()

    await idle_agent.setup()
    await paused_agent.setup()

    paused_agent._transition_to(
        AgentState.PAUSED,
        event="test_agent_paused",
    )

    await pool.register(idle_agent)
    await pool.register(paused_agent)

    idle_results = await pool.list_agents(
        tenant_id="tenant-001",
        state=AgentState.IDLE,
    )

    paused_results = await pool.list_agents(
        tenant_id="tenant-001",
        state=AgentState.PAUSED,
    )

    assert idle_results == (idle_agent,)
    assert paused_results == (paused_agent,)


@pytest.mark.anyio
async def test_count_returns_system_total() -> None:
    """
    Internal count without tenant returns all agents.
    """

    pool = AgentPool()

    first_agent = build_agent(
        tenant_id="tenant-001"
    )

    second_agent = build_agent(
        tenant_id="tenant-002"
    )

    await first_agent.setup()
    await second_agent.setup()

    await pool.register(first_agent)
    await pool.register(second_agent)

    assert await pool.count() == 2


@pytest.mark.anyio
async def test_count_filters_by_tenant() -> None:
    """
    Tenant count should include only matching agents.
    """

    pool = AgentPool()

    first_agent = build_agent(
        tenant_id="tenant-001"
    )

    second_agent = build_agent(
        tenant_id="tenant-001"
    )

    third_agent = build_agent(
        tenant_id="tenant-002"
    )

    await first_agent.setup()
    await second_agent.setup()
    await third_agent.setup()

    await pool.register(first_agent)
    await pool.register(second_agent)
    await pool.register(third_agent)

    assert (
        await pool.count(
            tenant_id="tenant-001"
        )
        == 2
    )

    assert (
        await pool.count(
            tenant_id="tenant-002"
        )
        == 1
    )


@pytest.mark.anyio
async def test_clear_removes_and_returns_all_agents() -> None:
    """
    clear should return a snapshot and empty the pool.
    """

    pool = AgentPool()

    first_agent = build_agent()
    second_agent = build_agent()

    await first_agent.setup()
    await second_agent.setup()

    await pool.register(first_agent)
    await pool.register(second_agent)

    removed_agents = await pool.clear()

    assert removed_agents == (
        first_agent,
        second_agent,
    )

    assert await pool.count() == 0


@pytest.mark.anyio
async def test_clear_empty_pool_returns_empty_tuple() -> None:
    """
    Clearing an empty pool should be safe.
    """

    pool = AgentPool()

    result = await pool.clear()

    assert result == ()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "tenant_id",
    [
        "",
        "   ",
    ],
)
async def test_tenant_methods_reject_blank_tenant(
    tenant_id: str,
) -> None:
    """
    Pool operations require a non-blank tenant ID.
    """

    pool = AgentPool()
    agent = build_agent()

    with pytest.raises(
        ValueError,
        match="tenant_id must not be blank",
    ):
        await pool.contains(
            agent_id=agent.agent_id,
            tenant_id=tenant_id,
        )


@pytest.mark.anyio
async def test_tenant_methods_reject_non_string_tenant() -> None:
    """
    Pool tenant IDs must be strings.
    """

    pool = AgentPool()
    agent = build_agent()

    with pytest.raises(
        TypeError,
        match="tenant_id must be a string",
    ):
        await pool.get(
            agent_id=agent.agent_id,
            tenant_id=123,  # type: ignore[arg-type]
        )