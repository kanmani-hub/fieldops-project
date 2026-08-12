import pytest
from typing import Any
from uuid import UUID
from app.services.ai.FieldOpsAI.agents.base import (
    BaseAgent,
    AgentState,
    AgentLifecycleError,
    TenantIsolationError
)
from app.services.ai.FieldOpsAI.schemas.agent_config import AgentConfig
from app.services.ai.FieldOpsAI.schemas.ai_task import AITask

# 1. BaseAgent cannot be instantiated directly.
def test_base_agent_cannot_be_instantiated_directly(valid_config: AgentConfig) -> None:
    """BaseAgent is abstract and cannot be instantiated directly."""
    with pytest.raises(TypeError):
         BaseAgent(valid_config)  # type: ignore

# 2. A subclass missing required abstract methods cannot be instantiated.
def test_incomplete_subclass_cannot_be_instantiated(valid_config: AgentConfig) -> None:
    """Subclass without the run method raises TypeError on instantiation."""
    class IncompleteAgent(BaseAgent[dict[str, Any]]):
        pass

    with pytest.raises(TypeError):
        IncompleteAgent(valid_config)  # type: ignore

# 3. Valid concrete subclass starts in the expected initial state.
def test_initial_state(valid_config: AgentConfig, successful_agent_class: type[BaseAgent]) -> None:
    """A fresh concrete agent starts in IDLE and is not set up."""
    agent = successful_agent_class(valid_config)
    assert agent.state == AgentState.IDLE
    assert agent.is_setup is False
    assert isinstance(agent.agent_id, UUID)
    assert agent.tenant_id == "tenant-001"

# 4. Valid setup succeeds.
@pytest.mark.anyio
async def test_setup_succeeds(valid_config: AgentConfig, successful_agent_class: type[BaseAgent]) -> None:
    """Setup prepares the agent and keeps it in IDLE state."""
    agent = successful_agent_class(valid_config)
    await agent.setup()
    assert agent.is_setup is True
    assert agent.state == AgentState.IDLE

# 5. Execution requires correct tenant context.
@pytest.mark.anyio
async def test_execute_checks_tenant_context(valid_config: AgentConfig, successful_agent_class: type[BaseAgent]) -> None:
    """Agent execution must match the configured tenant_id."""
    agent = successful_agent_class(valid_config)
    await agent.setup()
    
    # Missing tenant_id in context
    with pytest.raises(TenantIsolationError):
        await agent.execute({})
        
    # Mismatched tenant_id in context
    with pytest.raises(TenantIsolationError):
        await agent.execute({"tenant_id": "tenant-002"})

# 6. Successful execute calls the concrete run implementation once.
@pytest.mark.anyio
async def test_execute_calls_run_once(valid_config: AgentConfig, successful_agent_class: type[BaseAgent]) -> None:
    """A successful execute calls the concrete run logic and returns result."""
    agent = successful_agent_class(valid_config)
    await agent.setup()
    result = await agent.execute({"tenant_id": "tenant-001", "payload": "test_data"})
    assert result["status"] == "success"
    assert result["payload"] == "test_data"
    assert agent.state == AgentState.IDLE

# 7. Agent exception produces the expected safe state/error behavior.
@pytest.mark.anyio
async def test_execute_exception_safe_state(valid_config: AgentConfig, failing_agent_class: type[BaseAgent]) -> None:
    """An execution error transitions the agent to ERROR state and raises the exception."""
    agent = failing_agent_class(valid_config)
    await agent.setup()
    
    with pytest.raises(RuntimeError, match="Simulated agent execution failure"):
        await agent.execute({"tenant_id": "tenant-001"})
        
    assert agent.state == AgentState.ERROR

# 8. Teardown reaches TERMINATED and prevents invalid reuse.
@pytest.mark.anyio
async def test_teardown_prevents_reuse(valid_config: AgentConfig, successful_agent_class: type[BaseAgent]) -> None:
    """Teardown marks the agent as terminated and blocks further setup or execution."""
    agent = successful_agent_class(valid_config)
    await agent.setup()
    await agent.teardown()
    
    assert agent.state == AgentState.TERMINATED
    assert agent.is_setup is False
    
    with pytest.raises(AgentLifecycleError):
        await agent.setup()
        
    with pytest.raises(AgentLifecycleError):
        await agent.execute({"tenant_id": "tenant-001"})
