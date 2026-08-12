"""
test_communication_agent.py

Unit tests for CommunicationAgent under the BaseAgent framework.
"""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import MagicMock, patch
from pydantic import ValidationError

from app.services.ai.FieldOpsAI.agents.communication_agent import CommunicationAgent
from app.services.ai.FieldOpsAI.agents.base import (
    BaseAgent,
    AgentState,
    AgentLifecycleError,
    TenantIsolationError,
)
from app.services.ai.FieldOpsAI.schemas.agent_config import AgentConfig
from app.services.ai.FieldOpsAI.schemas.ai_task import AITask
from app.services.ai.FieldOpsAI.schemas.communication import CommunicationContext, CommunicationDecision
from app.services.ai.FieldOpsAI.runtime.orchestrator import AIOrchestrator
from app.services.ai.FieldOpsAI.runtime.lifecycle import AgentLifecycle
from app.services.ai.FieldOpsAI.runtime.agent_pool import AgentPool
from app.services.ai.FieldOpsAI.schemas.agent_result import AgentResultStatus

@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"

def build_config(
    *,
    agent_type: AITask = AITask.COMMUNICATION,
    tenant_id: str = "tenant-abc",
    enabled: bool = True,
) -> AgentConfig:
    return AgentConfig(
        agent_type=agent_type,
        tenant_id=tenant_id,
        enabled=enabled,
        timeout_seconds=5.0,
    )

def test_subclass_check() -> None:
    config = build_config()
    agent = CommunicationAgent(config=config)
    assert isinstance(agent, BaseAgent)

def test_correct_config_accepted() -> None:
    config = build_config()
    # Should not raise any error
    _ = CommunicationAgent(config=config)

def test_non_communication_config_rejected() -> None:
    config = build_config(agent_type=AITask.PLANNING)
    with pytest.raises(ValueError, match="requires an AITask.COMMUNICATION configuration"):
        _ = CommunicationAgent(config=config)

@pytest.mark.anyio
async def test_run_validates_context() -> None:
    config = build_config()
    mock_orchestrator = MagicMock(spec=AIOrchestrator)
    agent = CommunicationAgent(config=config, orchestrator=mock_orchestrator)
    
    # Missing required fields like channel/recipient_type/job_status/job_id
    invalid_context = {"tenant_id": "tenant-abc"}
    
    await agent.setup()
    with pytest.raises(ValidationError):
        await agent.run(invalid_context)
    
    mock_orchestrator.execute.assert_not_called()

@pytest.mark.anyio
async def test_tenant_id_removed_before_validation() -> None:
    config = build_config()
    mock_orchestrator = MagicMock(spec=AIOrchestrator)
    
    expected_decision = CommunicationDecision(
        channel="SMS",
        message="Test message",
        tone="PROFESSIONAL",
        confidence=0.95,
    )
    mock_orchestrator.execute.return_value = expected_decision
    
    agent = CommunicationAgent(config=config, orchestrator=mock_orchestrator)
    await agent.setup()
    
    valid_context = {
        "tenant_id": "tenant-abc",
        "job_id": "job-123",
        "notification_type": "job_assigned",
        "recipient_type": "CUSTOMER",
        "channel": "SMS",
        "job_status": "ASSIGNED",
    }
    
    decision = await agent.run(valid_context)
    assert decision == expected_decision
    
    mock_orchestrator.execute.assert_called_once()
    called_args = mock_orchestrator.execute.call_args[1]
    assert "tenant_id" not in called_args["context"]

@pytest.mark.anyio
async def test_tenant_mismatch_rejected_by_base_agent() -> None:
    config = build_config(tenant_id="tenant-abc")
    mock_orchestrator = MagicMock(spec=AIOrchestrator)
    agent = CommunicationAgent(config=config, orchestrator=mock_orchestrator)
    await agent.setup()
    
    mismatch_context = {
        "tenant_id": "tenant-xyz",
        "job_id": "job-123",
        "notification_type": "job_assigned",
        "recipient_type": "CUSTOMER",
        "channel": "SMS",
        "job_status": "ASSIGNED",
    }
    
    with pytest.raises(TenantIsolationError):
        await agent.execute(mismatch_context)

@pytest.mark.anyio
async def test_orchestrator_execute_invoked_exactly_once() -> None:
    config = build_config()
    mock_orchestrator = MagicMock(spec=AIOrchestrator)
    expected_decision = CommunicationDecision(
        channel="SMS",
        message="Test message",
        tone="PROFESSIONAL",
        confidence=0.95,
    )
    mock_orchestrator.execute.return_value = expected_decision
    agent = CommunicationAgent(config=config, orchestrator=mock_orchestrator)
    await agent.setup()
    
    valid_context = {
        "tenant_id": "tenant-abc",
        "job_id": "job-123",
        "notification_type": "job_assigned",
        "recipient_type": "CUSTOMER",
        "channel": "SMS",
        "job_status": "ASSIGNED",
    }
    
    decision = await agent.execute(valid_context)
    assert decision == expected_decision
    mock_orchestrator.execute.assert_called_once()

@pytest.mark.anyio
async def test_orchestrator_execution_is_offloaded() -> None:
    config = build_config()
    mock_orchestrator = MagicMock(spec=AIOrchestrator)
    
    with patch("asyncio.to_thread", wraps=asyncio.to_thread) as mock_to_thread:
        expected_decision = CommunicationDecision(
            channel="SMS",
            message="Test message",
            tone="PROFESSIONAL",
            confidence=0.95,
        )
        mock_orchestrator.execute.return_value = expected_decision
        agent = CommunicationAgent(config=config, orchestrator=mock_orchestrator)
        await agent.setup()
        
        valid_context = {
            "tenant_id": "tenant-abc",
            "job_id": "job-123",
            "notification_type": "job_assigned",
            "recipient_type": "CUSTOMER",
            "channel": "SMS",
            "job_status": "ASSIGNED",
        }
        
        decision = await agent.run(valid_context)
        assert decision == expected_decision
        mock_to_thread.assert_called_once()

@pytest.mark.anyio
async def test_invalid_output_type_rejected() -> None:
    config = build_config()
    mock_orchestrator = MagicMock(spec=AIOrchestrator)
    mock_orchestrator.execute.return_value = {"channel": "SMS", "message": "hello"}
    
    agent = CommunicationAgent(config=config, orchestrator=mock_orchestrator)
    await agent.setup()
    
    valid_context = {
        "tenant_id": "tenant-abc",
        "job_id": "job-123",
        "notification_type": "job_assigned",
        "recipient_type": "CUSTOMER",
        "channel": "SMS",
        "job_status": "ASSIGNED",
    }
    
    with pytest.raises(TypeError, match="Returned object is not a CommunicationDecision"):
        await agent.execute(valid_context)
        
    assert agent.state == AgentState.ERROR
    mock_orchestrator.execute.assert_called_once()

@pytest.mark.anyio
async def test_orchestrator_failure_follows_base_agent_error_behavior() -> None:
    config = build_config()
    mock_orchestrator = MagicMock(spec=AIOrchestrator)
    mock_orchestrator.execute.side_effect = RuntimeError("API Limit Reached")
    
    agent = CommunicationAgent(config=config, orchestrator=mock_orchestrator)
    await agent.setup()
    
    valid_context = {
        "tenant_id": "tenant-abc",
        "job_id": "job-123",
        "notification_type": "job_assigned",
        "recipient_type": "CUSTOMER",
        "channel": "SMS",
        "job_status": "ASSIGNED",
    }
    
    with pytest.raises(RuntimeError, match="API Limit Reached"):
        await agent.execute(valid_context)
        
    assert agent.state == AgentState.ERROR
    mock_orchestrator.execute.assert_called_once()

def test_synchronous_generate_works_outside_event_loop() -> None:
    config = build_config()
    mock_orchestrator = MagicMock(spec=AIOrchestrator)
    expected_decision = CommunicationDecision(
        channel="SMS",
        message="Test message",
        tone="PROFESSIONAL",
        confidence=0.95,
    )
    mock_orchestrator.execute.return_value = expected_decision
    agent = CommunicationAgent(config=config, orchestrator=mock_orchestrator)
    
    context = CommunicationContext(
        job_id="job-123",
        notification_type="job_assigned",
        recipient_type="CUSTOMER",
        channel="SMS",
        job_status="ASSIGNED",
    )
    
    decision = agent.generate(context)
    assert decision == expected_decision
    mock_orchestrator.execute.assert_called_once()
    assert agent.state == AgentState.TERMINATED

@pytest.mark.anyio
async def test_synchronous_generate_rejects_active_event_loop() -> None:
    config = build_config()
    agent = CommunicationAgent(config=config)
    context = CommunicationContext(
        job_id="job-123",
        notification_type="job_assigned",
        recipient_type="CUSTOMER",
        channel="SMS",
        job_status="ASSIGNED",
    )
    
    with pytest.raises(RuntimeError, match="cannot be called from an active event loop"):
        agent.generate(context)

def test_terminated_agents_not_reused() -> None:
    config = build_config()
    mock_orchestrator = MagicMock(spec=AIOrchestrator)
    expected_decision = CommunicationDecision(
        channel="SMS",
        message="Test message",
        tone="PROFESSIONAL",
        confidence=0.95,
    )
    mock_orchestrator.execute.return_value = expected_decision
    agent = CommunicationAgent(config=config, orchestrator=mock_orchestrator)
    
    context = CommunicationContext(
        job_id="job-123",
        notification_type="job_assigned",
        recipient_type="CUSTOMER",
        channel="SMS",
        job_status="ASSIGNED",
    )
    
    decision = agent.generate(context)
    assert decision == expected_decision
    assert agent.state == AgentState.TERMINATED
    
    with pytest.raises(AgentLifecycleError, match="A terminated agent cannot execute work"):
        agent.generate(context)

@pytest.mark.anyio
async def test_lifecycle_clean_teardown() -> None:
    config = build_config()
    mock_orchestrator = MagicMock(spec=AIOrchestrator)
    expected_decision = CommunicationDecision(
        channel="SMS",
        message="Test message",
        tone="PROFESSIONAL",
        confidence=0.95,
    )
    mock_orchestrator.execute.return_value = expected_decision
    agent = CommunicationAgent(config=config, orchestrator=mock_orchestrator)
    
    pool = AgentPool()
    async with AgentLifecycle(agent=agent, pool=pool) as lifecycle:
        assert agent.state == AgentState.IDLE
        assert agent.is_setup
        assert await pool.contains(agent_id=agent.agent_id, tenant_id=agent.tenant_id)
        
        valid_context = {
            "tenant_id": "tenant-abc",
            "job_id": "job-123",
            "notification_type": "job_assigned",
            "recipient_type": "CUSTOMER",
            "channel": "SMS",
            "job_status": "ASSIGNED",
        }
        result = await lifecycle.execute(valid_context)
        assert result.status == AgentResultStatus.SUCCESS
        assert result.output == expected_decision
        
    assert agent.state == AgentState.TERMINATED
    assert not agent.is_setup
    assert not await pool.contains(agent_id=agent.agent_id, tenant_id=agent.tenant_id)


def test_synchronous_generate_fails_on_unsuccessful_status() -> None:
    config = build_config()
    mock_orchestrator = MagicMock(spec=AIOrchestrator)
    mock_orchestrator.execute.side_effect = RuntimeError("API execution failed")
    agent = CommunicationAgent(config=config, orchestrator=mock_orchestrator)
    
    context = CommunicationContext(
        job_id="job-123",
        notification_type="job_assigned",
        recipient_type="CUSTOMER",
        channel="SMS",
        job_status="ASSIGNED",
    )
    
    with pytest.raises(RuntimeError, match="Communication agent execution failed with status: AgentResultStatus.FAILED"):
        agent.generate(context)
        
    mock_orchestrator.execute.assert_called_once()
    assert agent.state == AgentState.TERMINATED


def test_synchronous_generate_fails_on_invalid_output_type() -> None:
    config = build_config()
    agent = CommunicationAgent(config=config)
    
    context = CommunicationContext(
        job_id="job-123",
        notification_type="job_assigned",
        recipient_type="CUSTOMER",
        channel="SMS",
        job_status="ASSIGNED",
    )
    
    from app.services.ai.FieldOpsAI.schemas.agent_result import AgentResult
    
    fake_result = AgentResult(
        output="not a decision",
        status=AgentResultStatus.SUCCESS,
        latency_ms=10.0,
        tokens_used=0,
        agent_id=str(agent.agent_id),
        correlation_id="corr-123",
    )
    
    with patch.object(AgentLifecycle, "execute", return_value=fake_result):
        with pytest.raises(TypeError, match="Communication agent returned an invalid output type"):
            agent.generate(context)
    assert agent.state == AgentState.TERMINATED


def test_falsey_injected_orchestrator_is_preserved() -> None:
    config = build_config()
    
    class FalseyOrchestrator:
        def __bool__(self) -> bool:
            return False
            
    falsey_orchestrator = FalseyOrchestrator()
    agent = CommunicationAgent(config=config, orchestrator=falsey_orchestrator)
    assert agent.orchestrator is falsey_orchestrator


def test_communication_timeout_configuration() -> None:
    from app.services.ai.FieldOpsAI.config.agent_config_manager import AgentConfigManager
    config = AgentConfigManager().resolve(
        agent_type=AITask.COMMUNICATION,
        tenant_id="tenant-abc",
    )
    assert config.timeout_seconds == 5.0

