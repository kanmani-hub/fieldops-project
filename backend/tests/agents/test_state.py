import pytest
from uuid import uuid4
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from app.services.ai.FieldOpsAI.agents.base import BaseAgent, AgentState
from app.services.ai.FieldOpsAI.schemas.agent_config import AgentConfig
from app.services.ai.FieldOpsAI.runtime.agent_pool import AgentPool
from app.services.ai.FieldOpsAI.runtime.lifecycle import AgentLifecycle
from app.services.ai.FieldOpsAI.schemas.agent_result import AgentResultStatus
from app.services.ai.FieldOpsAI.repositories.agent_state_repository import AgentStateRepository
from app.services.ai.FieldOpsAI.runtime.agent_state_manager import AgentStateManager

# 1. Save current agent snapshot.
@pytest.mark.anyio
async def test_save_current_agent_snapshot(
    valid_config: AgentConfig,
    successful_agent_class: type[BaseAgent],
    state_manager: AgentStateManager
) -> None:
    """AgentStateManager can save a correct snapshot of an agent."""
    agent = successful_agent_class(valid_config)
    await agent.setup()
    
    snapshot = state_manager.save_agent(agent, correlation_id="corr-1", metadata={"step": 1})
    assert snapshot.agent_id == agent.agent_id
    assert snapshot.tenant_id == "tenant-001"
    assert snapshot.state == AgentState.IDLE
    assert snapshot.correlation_id == "corr-1"
    assert snapshot.metadata == {"step": 1}

# 2. Load saved snapshot.
@pytest.mark.anyio
async def test_load_saved_snapshot(
    valid_config: AgentConfig,
    successful_agent_class: type[BaseAgent],
    state_manager: AgentStateManager
) -> None:
    """AgentStateManager can retrieve a previously saved snapshot."""
    agent = successful_agent_class(valid_config)
    await agent.setup()
    state_manager.save_agent(agent, correlation_id="corr-2")
    
    loaded = state_manager.load(agent_id=agent.agent_id, tenant_id="tenant-001")
    assert loaded is not None
    assert loaded.agent_id == agent.agent_id
    assert loaded.correlation_id == "corr-2"

# 3. Unknown snapshot returns None.
def test_unknown_snapshot_returns_none(state_manager: AgentStateManager) -> None:
    """Loading a non-existent snapshot returns None."""
    loaded = state_manager.load(agent_id=uuid4(), tenant_id="tenant-001")
    assert loaded is None

# 4. Wrong tenant cannot load another tenant’s snapshot.
@pytest.mark.anyio
async def test_cross_tenant_load_isolation(
    valid_config: AgentConfig,
    successful_agent_class: type[BaseAgent],
    state_manager: AgentStateManager
) -> None:
    """A tenant cannot retrieve another tenant's agent state snapshot."""
    agent = successful_agent_class(valid_config)
    await agent.setup()
    state_manager.save_agent(agent)
    
    # Try loading with a different tenant_id
    loaded = state_manager.load(agent_id=agent.agent_id, tenant_id="tenant-002")
    assert loaded is None

# 5. Saving again updates the current persisted snapshot.
@pytest.mark.anyio
async def test_saving_again_updates_snapshot(
    valid_config: AgentConfig,
    successful_agent_class: type[BaseAgent],
    state_manager: AgentStateManager,
) -> None:
    """
    Saving the same agent again updates its current persisted
    snapshot instead of inserting a duplicate record.
    """

    agent = successful_agent_class(valid_config)
    await agent.setup()

    first = state_manager.save_agent(
        agent,
        correlation_id="corr-first",
        metadata={"version": 1},
    )

    second = state_manager.save_agent(
        agent,
        correlation_id="corr-second",
        metadata={"version": 2},
    )

    loaded = state_manager.load(
        agent_id=agent.agent_id,
        tenant_id=agent.tenant_id,
    )

    assert loaded is not None

    # The same agent record was updated.
    assert loaded.agent_id == first.agent_id
    assert loaded.agent_id == second.agent_id

    # The latest values replaced the previous values.
    assert loaded.correlation_id == "corr-second"
    assert loaded.metadata == {"version": 2}

# 6. Delete existing snapshot.
@pytest.mark.anyio
async def test_delete_existing_snapshot(
    valid_config: AgentConfig,
    successful_agent_class: type[BaseAgent],
    state_manager: AgentStateManager
) -> None:
    """delete() removes an existing snapshot and returns True."""
    agent = successful_agent_class(valid_config)
    await agent.setup()
    state_manager.save_agent(agent)
    
    deleted = state_manager.delete(agent_id=agent.agent_id, tenant_id="tenant-001")
    assert deleted is True
    assert state_manager.load(agent_id=agent.agent_id, tenant_id="tenant-001") is None

# 7. Delete missing snapshot.
def test_delete_missing_snapshot(state_manager: AgentStateManager) -> None:
    """delete() on a non-existent snapshot returns False."""
    deleted = state_manager.delete(agent_id=uuid4(), tenant_id="tenant-001")
    assert deleted is False

# 8. Metadata is copied and privacy-safe.
@pytest.mark.anyio
async def test_metadata_is_copied(
    valid_config: AgentConfig,
    successful_agent_class: type[BaseAgent],
    state_manager: AgentStateManager
) -> None:
    """State manager deep copies metadata to prevent mutation of the original dictionary."""
    agent = successful_agent_class(valid_config)
    await agent.setup()
    
    meta = {"items": [1, 2]}
    snapshot = state_manager.save_agent(agent, metadata=meta)
    
    meta["items"].append(3)  # Mutate original dictionary
    assert snapshot.metadata == {"items": [1, 2]}

# 9. Repository failure does not interrupt lifecycle execution.
@pytest.mark.anyio
async def test_repository_failure_does_not_interrupt_lifecycle(
    valid_config: AgentConfig,
    successful_agent_class: type[BaseAgent],
    agent_pool: AgentPool
) -> None:
    """Lifecycle handles DB / repository write errors via log-and-continue without throwing exception."""
    # Mock a repository that fails on writes
    from unittest.mock import MagicMock
    mock_repo = MagicMock(spec=AgentStateRepository)
    mock_repo.upsert.side_effect = SQLAlchemyError("Database connection timed out")
    
    failing_manager = AgentStateManager(mock_repo)
    agent = successful_agent_class(valid_config)
    
    lifecycle = AgentLifecycle(
        agent=agent,
        pool=agent_pool,
        state_manager=failing_manager
    )
    
    # Initialize and execute should complete successfully
    await lifecycle.initialize()
    result = await lifecycle.execute({"tenant_id": "tenant-001"})
    assert result.status == AgentResultStatus.SUCCESS

# 10. Simulated crash recovery loads the last persisted snapshot.
@pytest.mark.anyio
async def test_simulated_crash_recovery(
    valid_config: AgentConfig,
    successful_agent_class: type[BaseAgent],
    agent_pool: AgentPool,
    db_session: Session
) -> None:
    """Crash recovery successfully retrieves the state of the agent after a process crash."""
    # 1. Create and initialize dummy agent with state persistence
    repo = AgentStateRepository(db_session)
    mgr = AgentStateManager(repo)
    agent = successful_agent_class(valid_config)
    lifecycle = AgentLifecycle(agent=agent, pool=agent_pool, state_manager=mgr)
    await lifecycle.initialize()
    
    # 2. Execute it
    await lifecycle.execute({"tenant_id": "tenant-001"})
    
    # 3. Discard the live Python agent objects (simulating process crash)
    agent_id = agent.agent_id
    tenant_id = agent.tenant_id
    del agent
    del lifecycle
    
    # 4. Create new manager/recovery context using the same test database
    new_repo = AgentStateRepository(db_session)
    new_mgr = AgentStateManager(new_repo)
    
    # 5. Load previously persisted snapshot and verify fields
    recovered = new_mgr.load(agent_id=agent_id, tenant_id=tenant_id)
    assert recovered is not None
    assert recovered.agent_id == agent_id
    assert recovered.tenant_id == tenant_id
    assert recovered.state == AgentState.IDLE

