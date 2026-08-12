import pytest
from unittest.mock import AsyncMock, MagicMock
from app.services.ai.FieldOpsAI.agents.base import AgentState, BaseAgent
from app.services.ai.FieldOpsAI.runtime.lifecycle import (
    AgentLifecycle,
    LifecycleNotInitializedError)
from app.services.ai.FieldOpsAI.schemas.agent_result import AgentResultStatus
from app.services.ai.FieldOpsAI.runtime.agent_pool import AgentPool
from app.services.ai.FieldOpsAI.schemas.agent_config import AgentConfig

# 1. Initialize registers the agent in AgentPool.
@pytest.mark.anyio
async def test_initialize_registers_in_pool(
    valid_config: AgentConfig,
    successful_agent_class: type[BaseAgent],
    agent_pool: AgentPool
) -> None:
    """Initializing a lifecycle registers the agent in the pool."""
    agent = successful_agent_class(valid_config)
    lifecycle = AgentLifecycle(agent=agent, pool=agent_pool)
    
    assert not await agent_pool.contains(agent_id=agent.agent_id, tenant_id=agent.tenant_id)
    await lifecycle.initialize()
    assert await agent_pool.contains(agent_id=agent.agent_id, tenant_id=agent.tenant_id)
    assert lifecycle.initialized

# 2. Initialize is idempotent.
@pytest.mark.anyio
async def test_initialize_is_idempotent(
    valid_config: AgentConfig,
    successful_agent_class: type[BaseAgent],
    agent_pool: AgentPool
) -> None:
    """Calling initialize multiple times is safe and doesn't duplicate registrations."""
    agent = successful_agent_class(valid_config)
    lifecycle = AgentLifecycle(agent=agent, pool=agent_pool)
    
    await lifecycle.initialize()
    await lifecycle.initialize()
    assert lifecycle.initialized

# 3. Successful execution follows the valid state flow.
@pytest.mark.anyio
async def test_execution_state_flow(
    valid_config: AgentConfig,
    successful_agent_class: type[BaseAgent],
    agent_pool: AgentPool
) -> None:
    """Execution moves state to RUNNING and back to IDLE upon completion."""
    agent = successful_agent_class(valid_config)
    lifecycle = AgentLifecycle(agent=agent, pool=agent_pool)
    await lifecycle.initialize()
    
    # We can capture state during execution if needed, but verifying it starts and ends in IDLE is key.
    # BaseAgent execute() handles the RUNNING transition internally.
    assert agent.state == AgentState.IDLE
    result = await lifecycle.execute({"tenant_id": "tenant-001"})
    assert result.status == AgentResultStatus.SUCCESS
    assert agent.state == AgentState.IDLE

# 4. Execute before initialize is rejected.
@pytest.mark.anyio
async def test_execute_before_initialize_rejected(
    valid_config: AgentConfig,
    successful_agent_class: type[BaseAgent],
    agent_pool: AgentPool
) -> None:
    """Cannot run execute on a lifecycle that hasn't been initialized."""
    agent = successful_agent_class(valid_config)
    lifecycle = AgentLifecycle(agent=agent, pool=agent_pool)
    
    with pytest.raises(LifecycleNotInitializedError):
        await lifecycle.execute({"tenant_id": "tenant-001"})

# 5. Successful execution returns AgentResultStatus.SUCCESS.
@pytest.mark.anyio
async def test_execute_returns_success_status(
    valid_config: AgentConfig,
    successful_agent_class: type[BaseAgent],
    agent_pool: AgentPool
) -> None:
    """Successful agent execution results in AgentResultStatus.SUCCESS."""
    agent = successful_agent_class(valid_config)
    lifecycle = AgentLifecycle(agent=agent, pool=agent_pool)
    await lifecycle.initialize()
    
    result = await lifecycle.execute({"tenant_id": "tenant-001"})
    assert result.status == AgentResultStatus.SUCCESS

# 6. Agent crash returns safe FAILED result and ERROR state.
@pytest.mark.anyio
async def test_agent_crash_returns_failed_result(
    valid_config: AgentConfig,
    failing_agent_class: type[BaseAgent],
    agent_pool: AgentPool
) -> None:
    """If an agent throws an exception, the lifecycle returns FAILED status and shifts state to ERROR."""
    agent = failing_agent_class(valid_config)
    lifecycle = AgentLifecycle(agent=agent, pool=agent_pool)
    await lifecycle.initialize()
    
    result = await lifecycle.execute({"tenant_id": "tenant-001"})
    assert result.status == AgentResultStatus.FAILED
    assert agent.state == AgentState.ERROR

# 7. Execution timeout returns TIMEOUT and ERROR state.
@pytest.mark.anyio
async def test_execution_timeout_returns_timeout(
    valid_config: AgentConfig,
    slow_agent_class: type[BaseAgent],
    agent_pool: AgentPool
) -> None:
    """If an execution exceeds timeout, it returns TIMEOUT status and transitions to ERROR."""
    # Use slow agent with 1 second delay but configure lifecycle with 0.1s timeout
    agent = slow_agent_class(valid_config, sleep_seconds=0.5)
    lifecycle = AgentLifecycle(agent=agent, pool=agent_pool, run_timeout_seconds=0.1)
    await lifecycle.initialize()
    
    result = await lifecycle.execute({"tenant_id": "tenant-001"})
    assert result.status == AgentResultStatus.TIMEOUT
    assert agent.state == AgentState.ERROR

# 8. Pause IDLE agent and resume PAUSED agent.
@pytest.mark.anyio
async def test_pause_and_resume(
    valid_config: AgentConfig,
    successful_agent_class: type[BaseAgent],
    agent_pool: AgentPool
) -> None:
    """Agent can be paused from IDLE and resumed back to IDLE."""
    agent = successful_agent_class(valid_config)
    lifecycle = AgentLifecycle(agent=agent, pool=agent_pool)
    await lifecycle.initialize()
    
    await lifecycle.pause()
    assert agent.state == AgentState.PAUSED
    
    # Cannot execute while paused
    res = await lifecycle.execute({"tenant_id": "tenant-001"})
    assert res.status == AgentResultStatus.FAILED
        
    await lifecycle.resume()
    assert agent.state == AgentState.IDLE

# 9. Invalid ERROR-to-RUNNING execution is rejected by the current state contract.
@pytest.mark.anyio
async def test_error_to_running_rejected(
    valid_config: AgentConfig,
    failing_agent_class: type[BaseAgent],
    agent_pool: AgentPool
) -> None:
    """An agent in ERROR state cannot be executed without recovery."""
    agent = failing_agent_class(valid_config)
    lifecycle = AgentLifecycle(agent=agent, pool=agent_pool)
    await lifecycle.initialize()
    
    # Put agent in ERROR state
    await lifecycle.execute({"tenant_id": "tenant-001"})
    assert agent.state == AgentState.ERROR
    
    # Try execution again - should return FAILED status and keep ERROR state
    res = await lifecycle.execute({"tenant_id": "tenant-001"})
    assert res.status == AgentResultStatus.FAILED

# 10. Teardown unregisters the agent and reaches TERMINATED.
@pytest.mark.anyio
async def test_teardown_unregisters_agent(
    valid_config: AgentConfig,
    successful_agent_class: type[BaseAgent],
    agent_pool: AgentPool
) -> None:
    """Teardown unregisters agent from pool and transitions agent to TERMINATED."""
    agent = successful_agent_class(valid_config)
    lifecycle = AgentLifecycle(agent=agent, pool=agent_pool)
    await lifecycle.initialize()
    
    await lifecycle.teardown()
    assert agent.state == AgentState.TERMINATED
    assert not await agent_pool.contains(agent_id=agent.agent_id, tenant_id=agent.tenant_id)

# 11. Persistent-state failure follows log-and-continue.
@pytest.mark.anyio
async def test_persistence_failure_log_and_continue(
    valid_config: AgentConfig,
    successful_agent_class: type[BaseAgent],
    agent_pool: AgentPool
) -> None:
    """SQLAlchemy or state manager failure does not block lifecycle execution."""
    agent = successful_agent_class(valid_config)
    
    # Create a mock state manager that raises an exception on save_agent
    mock_state_manager = MagicMock()
    mock_state_manager.save_agent.side_effect = RuntimeError("DB write error")
    
    lifecycle = AgentLifecycle(
        agent=agent,
        pool=agent_pool,
        state_manager=mock_state_manager
    )
    
    # Initialize should succeed despite save_agent throwing error
    await lifecycle.initialize()
    assert lifecycle.initialized
    
    # Execution should also succeed and complete log-and-continue policy
    result = await lifecycle.execute({"tenant_id": "tenant-001"})
    assert result.status == AgentResultStatus.SUCCESS

# 12. Health-monitor failure follows log-and-continue without raw exception logging.
@pytest.mark.anyio
async def test_health_monitor_failure_log_and_continue(
    valid_config: AgentConfig,
    successful_agent_class: type[BaseAgent],
    agent_pool: AgentPool,
) -> None:
    """
    Health-monitor failure must not interrupt lifecycle execution
    or expose the raw exception through lifecycle logs.
    """

    agent = successful_agent_class(valid_config)

    mock_health = MagicMock()
    mock_health.record_heartbeat = AsyncMock(
        side_effect=RuntimeError(
            "Health server down"
        )
    )

    lifecycle = AgentLifecycle(
        agent=agent,
        pool=agent_pool,
        health_monitor=mock_health,
    )

    mock_logger = MagicMock()
    lifecycle._logger = mock_logger

    await lifecycle.initialize()

    result = await lifecycle.execute(
        {"tenant_id": "tenant-001"}
    )

    assert lifecycle.initialized is True
    assert result.status == AgentResultStatus.SUCCESS

    assert mock_logger.warning.called
    mock_logger.exception.assert_not_called()

    for call in mock_logger.warning.call_args_list:
        rendered_call = str(call)

        assert "Health server down" not in rendered_call
        assert "RuntimeError" not in rendered_call
        assert "traceback" not in rendered_call.lower()
        assert "exc_info" not in call.kwargs