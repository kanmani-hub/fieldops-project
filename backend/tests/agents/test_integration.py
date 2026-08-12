import asyncio
import time
import math
import pytest
from uuid import uuid4
from sqlalchemy.orm import Session
from app.services.ai.FieldOpsAI.agents.base import BaseAgent, AgentState
from app.services.ai.FieldOpsAI.schemas.ai_task import AITask
from app.services.ai.FieldOpsAI.schemas.agent_config import AgentConfig
from app.services.ai.FieldOpsAI.runtime.agent_pool import AgentPool
from app.services.ai.FieldOpsAI.runtime.agent_registry import AgentRegistry, AgentRegistration
from app.services.ai.FieldOpsAI.runtime.lifecycle import AgentLifecycle
from app.services.ai.FieldOpsAI.schemas.agent_result import AgentResultStatus
from app.services.ai.FieldOpsAI.repositories.agent_state_repository import AgentStateRepository
from app.services.ai.FieldOpsAI.runtime.agent_state_manager import AgentStateManager
from app.services.ai.FieldOpsAI.runtime.agent_health_monitor import AgentHealthMonitor
from app.services.ai.FieldOpsAI.schemas.agent_health import HealthStatus
from app.services.ai.FieldOpsAI.runtime.agent_bus import AgentBus
from app.services.ai.FieldOpsAI.schemas.agent_messages import (
    AgentAddress,
    MessageEnvelope,
    MessageType
)

class MockConfigManager:
    """Mock config manager that resolves standard configs."""
    def __init__(self, is_enabled: bool = True) -> None:
        self.is_enabled = is_enabled

    def resolve(self, *, agent_type: AITask, tenant_id: str, overrides: dict | None = None) -> AgentConfig:
        return AgentConfig(
            agent_type=agent_type,
            tenant_id=tenant_id,
            agent_version="1.0",
            timeout_seconds=30.0,
            max_retries=2,
            enabled=self.is_enabled
        )

# 1. Registry creates an agent from resolved configuration.
def test_registry_creates_agent_from_config(agent_registry: AgentRegistry, successful_agent_class: type[BaseAgent]) -> None:
    """AgentRegistry creates a fresh uninitialized agent from resolved configuration."""
    reg = AgentRegistration(agent_type=AITask.PLANNING, agent_class=successful_agent_class, version="1.0")
    agent_registry.register(registration=reg)
    
    config_manager = MockConfigManager()
    agent = agent_registry.create(agent_type=AITask.PLANNING, tenant_id="tenant-001", config_manager=config_manager)
    assert isinstance(agent, successful_agent_class)
    assert agent.tenant_id == "tenant-001"
    assert agent.state == AgentState.IDLE
    assert not agent.is_setup

# 2. Lifecycle initializes a registry-created agent.
@pytest.mark.anyio
async def test_lifecycle_initializes_registry_created_agent(
    agent_registry: AgentRegistry,
    successful_agent_class: type[BaseAgent],
    agent_pool: AgentPool
) -> None:
    """AgentLifecycle can successfully initialize an agent created via AgentRegistry."""
    reg = AgentRegistration(agent_type=AITask.PLANNING, agent_class=successful_agent_class, version="1.0")
    agent_registry.register(registration=reg)
    
    config_manager = MockConfigManager()
    agent = agent_registry.create(agent_type=AITask.PLANNING, tenant_id="tenant-001", config_manager=config_manager)
    
    lifecycle = AgentLifecycle(agent=agent, pool=agent_pool)
    await lifecycle.initialize()
    assert lifecycle.initialized
    assert agent.is_setup
    assert agent.state == AgentState.IDLE

# 3. Initialized agent appears in AgentPool.
@pytest.mark.anyio
async def test_initialized_agent_in_pool(
    agent_registry: AgentRegistry,
    successful_agent_class: type[BaseAgent],
    agent_pool: AgentPool
) -> None:
    """An initialized agent is automatically registered and present in the AgentPool."""
    reg = AgentRegistration(agent_type=AITask.PLANNING, agent_class=successful_agent_class, version="1.0")
    agent_registry.register(registration=reg)
    
    config_manager = MockConfigManager()
    agent = agent_registry.create(agent_type=AITask.PLANNING, tenant_id="tenant-001", config_manager=config_manager)
    
    lifecycle = AgentLifecycle(agent=agent, pool=agent_pool)
    await lifecycle.initialize()
    
    assert await agent_pool.contains(agent_id=agent.agent_id, tenant_id="tenant-001")
    retrieved = await agent_pool.get(agent_id=agent.agent_id, tenant_id="tenant-001")
    assert retrieved == agent

# 4. Lifecycle initialization persists a state snapshot.
@pytest.mark.anyio
async def test_lifecycle_init_persists_state(
    agent_registry: AgentRegistry,
    successful_agent_class: type[BaseAgent],
    agent_pool: AgentPool,
    state_manager: AgentStateManager
) -> None:
    """AgentLifecycle persists a state snapshot in the database upon initialization."""
    reg = AgentRegistration(agent_type=AITask.PLANNING, agent_class=successful_agent_class, version="1.0")
    agent_registry.register(registration=reg)
    
    config_manager = MockConfigManager()
    agent = agent_registry.create(agent_type=AITask.PLANNING, tenant_id="tenant-001", config_manager=config_manager)
    
    lifecycle = AgentLifecycle(agent=agent, pool=agent_pool, state_manager=state_manager)
    await lifecycle.initialize()
    
    snapshot = state_manager.load(agent_id=agent.agent_id, tenant_id="tenant-001")
    assert snapshot is not None
    assert snapshot.state == AgentState.IDLE

# 5. Lifecycle initialization records a health heartbeat.
@pytest.mark.anyio
async def test_lifecycle_init_records_health(
    agent_registry: AgentRegistry,
    successful_agent_class: type[BaseAgent],
    agent_pool: AgentPool,
    health_monitor: AgentHealthMonitor
) -> None:
    """AgentLifecycle records a health heartbeat in the health monitor upon initialization."""
    reg = AgentRegistration(agent_type=AITask.PLANNING, agent_class=successful_agent_class, version="1.0")
    agent_registry.register(registration=reg)
    
    config_manager = MockConfigManager()
    agent = agent_registry.create(agent_type=AITask.PLANNING, tenant_id="tenant-001", config_manager=config_manager)
    
    lifecycle = AgentLifecycle(agent=agent, pool=agent_pool, health_monitor=health_monitor)
    await lifecycle.initialize()
    
    health = await health_monitor.get_agent_health(tenant_id="tenant-001", agent_id=agent.agent_id)
    assert health is not None
    assert health.status == HealthStatus.HEALTHY

# 6. Successful execution updates state and health.
@pytest.mark.anyio
async def test_successful_execution_updates_state_and_health(
    agent_registry: AgentRegistry,
    successful_agent_class: type[BaseAgent],
    agent_pool: AgentPool,
    state_manager: AgentStateManager,
    health_monitor: AgentHealthMonitor
) -> None:
    """Executing an agent successfully updates both the persisted database state and health metrics."""
    reg = AgentRegistration(agent_type=AITask.PLANNING, agent_class=successful_agent_class, version="1.0")
    agent_registry.register(registration=reg)
    
    config_manager = MockConfigManager()
    agent = agent_registry.create(agent_type=AITask.PLANNING, tenant_id="tenant-001", config_manager=config_manager)
    
    lifecycle = AgentLifecycle(
        agent=agent,
        pool=agent_pool,
        state_manager=state_manager,
        health_monitor=health_monitor
    )
    await lifecycle.initialize()
    
    result = await lifecycle.execute({"tenant_id": "tenant-001"})
    assert result.status == AgentResultStatus.SUCCESS
    
    # Verify state updated (remains IDLE, but updated_at/counters incremented if any)
    snapshot = state_manager.load(agent_id=agent.agent_id, tenant_id="tenant-001")
    assert snapshot.state == AgentState.IDLE
    
    # Verify health has 1 success heartbeat
    health = await health_monitor.get_agent_health(tenant_id="tenant-001", agent_id=agent.agent_id)
    assert health.total_successes == 1

# 7. Failed execution updates state and health safely.
@pytest.mark.anyio
async def test_failed_execution_updates_state_and_health(
    agent_registry: AgentRegistry,
    failing_agent_class: type[BaseAgent],
    agent_pool: AgentPool,
    state_manager: AgentStateManager,
    health_monitor: AgentHealthMonitor
) -> None:
    """A crashed agent execution updates state to ERROR and records a failure heartbeat."""
    reg = AgentRegistration(agent_type=AITask.PLANNING, agent_class=failing_agent_class, version="1.0")
    agent_registry.register(registration=reg)
    
    config_manager = MockConfigManager()
    agent = agent_registry.create(agent_type=AITask.PLANNING, tenant_id="tenant-001", config_manager=config_manager)
    
    lifecycle = AgentLifecycle(
        agent=agent,
        pool=agent_pool,
        state_manager=state_manager,
        health_monitor=health_monitor
    )
    await lifecycle.initialize()
    
    result = await lifecycle.execute({"tenant_id": "tenant-001"})
    assert result.status == AgentResultStatus.FAILED
    
    # State should be ERROR
    snapshot = state_manager.load(agent_id=agent.agent_id, tenant_id="tenant-001")
    assert snapshot.state == AgentState.ERROR
    
    # Health should record failure
    health = await health_monitor.get_agent_health(tenant_id="tenant-001", agent_id=agent.agent_id)
    assert health.total_failures == 1

# 8. Timed-out execution updates state and health safely.
@pytest.mark.anyio
async def test_timed_out_execution_updates_state_and_health(
    agent_registry: AgentRegistry,
    slow_agent_class: type[BaseAgent],
    agent_pool: AgentPool,
    state_manager: AgentStateManager,
    health_monitor: AgentHealthMonitor
) -> None:
    """A timed out agent execution updates state to ERROR and records a timeout heartbeat."""
    reg = AgentRegistration(agent_type=AITask.PLANNING, agent_class=slow_agent_class, version="1.0")
    agent_registry.register(registration=reg, factory=lambda cfg, orch=None: slow_agent_class(cfg, sleep_seconds=0.5))
    
    config_manager = MockConfigManager()
    agent = agent_registry.create(agent_type=AITask.PLANNING, tenant_id="tenant-001", config_manager=config_manager)
    
    lifecycle = AgentLifecycle(
        agent=agent,
        pool=agent_pool,
        run_timeout_seconds=0.1,
        state_manager=state_manager,
        health_monitor=health_monitor
    )
    await lifecycle.initialize()
    
    result = await lifecycle.execute({"tenant_id": "tenant-001"})
    assert result.status == AgentResultStatus.TIMEOUT
    
    # State should be ERROR
    snapshot = state_manager.load(agent_id=agent.agent_id, tenant_id="tenant-001")
    assert snapshot.state == AgentState.ERROR
    
    # Health should record timeout
    health = await health_monitor.get_agent_health(tenant_id="tenant-001", agent_id=agent.agent_id)
    assert health.total_timeouts == 1

# 9. Targeted bus delivery between two agent addresses.
@pytest.mark.anyio
async def test_targeted_bus_delivery_integration(agent_bus: AgentBus) -> None:
    """Two agents can communicate using targeted AgentAddress pub/sub routing."""
    sender = AgentAddress(tenant_id="tenant-001", agent_type=AITask.PLANNING, agent_id=str(uuid4()))
    recipient = AgentAddress(tenant_id="tenant-001", agent_type=AITask.DISPATCH, agent_id=str(uuid4()))
    
    received_msgs = []
    async def handler(env: MessageEnvelope) -> None:
        received_msgs.append(env)
        
    await agent_bus.subscribe(
        tenant_id="tenant-001",
        topic="agent.integration.direct",
        handler=handler,
        subscriber=recipient
    )
    
    msg = MessageEnvelope(
        sender=sender,
        recipient=recipient,
        message_type=MessageType.COMMAND,
        topic="agent.integration.direct",
        payload={"task": "dispatch_job"}
    )
    
    result = await agent_bus.publish(msg)
    assert result.delivered == 1
    assert len(received_msgs) == 1
    assert received_msgs[0].recipient == recipient

# 10. Three dummy agents communicate end-to-end through AgentBus.
@pytest.mark.anyio
async def test_three_agent_communication_chain(agent_bus: AgentBus) -> None:
    """Three dummy agents can communicate in sequence planning -> dispatch -> monitoring."""
    agent_a = AgentAddress(tenant_id="tenant-001", agent_type=AITask.PLANNING, agent_id=str(uuid4()))
    agent_b = AgentAddress(tenant_id="tenant-001", agent_type=AITask.DISPATCH, agent_id=str(uuid4()))
    agent_c = AgentAddress(tenant_id="tenant-001", agent_type=AITask.MONITORING, agent_id=str(uuid4()))
    
    corr_id = "integration-corr-id"
    completed_event = asyncio.Event()
    message_sequence = []
    
    # Agent B handler
    async def agent_b_handler(env: MessageEnvelope) -> None:
        message_sequence.append(("AgentB", env.topic, env.correlation_id))
        # Publish event for Agent C
        evt = MessageEnvelope(
            sender=agent_b,
            recipient=agent_c,
            message_type=MessageType.EVENT,
            topic="agent.dispatch.event",
            payload={"status": "dispatched"},
            correlation_id=env.correlation_id
        )
        await agent_bus.publish(evt)
        
    # Agent C handler
    async def agent_c_handler(env: MessageEnvelope) -> None:
        message_sequence.append(("AgentC", env.topic, env.correlation_id))
        completed_event.set()
        
    # Subscribe Agent B
    await agent_bus.subscribe(
        tenant_id="tenant-001",
        topic="agent.planning.command",
        handler=agent_b_handler,
        subscriber=agent_b
    )
    
    # Subscribe Agent C
    await agent_bus.subscribe(
        tenant_id="tenant-001",
        topic="agent.dispatch.event",
        handler=agent_c_handler,
        subscriber=agent_c
    )
    
    # Agent A publishes the initial command to Agent B
    cmd = MessageEnvelope(
        sender=agent_a,
        recipient=agent_b,
        message_type=MessageType.COMMAND,
        topic="agent.planning.command",
        payload={"action": "start"},
        correlation_id=corr_id
    )
    
    await agent_bus.publish(cmd)
    
    # Bounded wait for chain completion
    try:
        await asyncio.wait_for(completed_event.wait(), timeout=2.0)
    except asyncio.TimeoutError:
        pytest.fail("Three-agent communication chain failed to complete in time.")
        
    assert len(message_sequence) == 2
    assert message_sequence[0] == ("AgentB", "agent.planning.command", corr_id)
    assert message_sequence[1] == ("AgentC", "agent.dispatch.event", corr_id)

# 11. Cross-tenant agent cannot receive another tenant’s message.
@pytest.mark.anyio
async def test_cross_tenant_message_isolation_integration(agent_bus: AgentBus) -> None:
    """AgentBus ensures cross-tenant messages are never leaked or routed to other tenants."""
    sender_t1 = AgentAddress(tenant_id="tenant-001", agent_type=AITask.PLANNING, agent_id=str(uuid4()))
    recipient_t2 = AgentAddress(tenant_id="tenant-002", agent_type=AITask.DISPATCH, agent_id=str(uuid4()))
    
    received_msgs = []
    async def handler(env: MessageEnvelope) -> None:
        received_msgs.append(env)
        
    # Attempting to subscribe on tenant-002 with recipient_t2 address
    await agent_bus.subscribe(
        tenant_id="tenant-002",
        topic="agent.shared.topic",
        handler=handler,
        subscriber=recipient_t2
    )
    
    # Attempting to construct envelope targeted at recipient_t2 but with sender_t1 (cross-tenant)
    # The MessageEnvelope schema validates that recipient's tenant must match sender's tenant
    with pytest.raises(ValueError):
        MessageEnvelope(
            sender=sender_t1,
            recipient=recipient_t2,
            message_type=MessageType.COMMAND,
            topic="agent.shared.topic"
        )
        
    # Also publish broadcast on tenant-001 and check that tenant-002 subscriber doesn't receive it
    msg = MessageEnvelope(
        sender=sender_t1,
        message_type=MessageType.EVENT,
        topic="agent.shared.topic"
    )
    
    result = await agent_bus.publish(msg)
    assert result.delivered == 0
    assert len(received_msgs) == 0

# 12. Simulated process crash can load the last persisted state snapshot.
@pytest.mark.anyio
async def test_simulated_process_crash_recovery_integration(
    agent_registry: AgentRegistry,
    successful_agent_class: type[BaseAgent],
    agent_pool: AgentPool,
    db_session: Session
) -> None:
    """Complete integration crash recovery scenario verifying state load from database."""
    reg = AgentRegistration(agent_type=AITask.PLANNING, agent_class=successful_agent_class, version="1.0")
    agent_registry.register(registration=reg)
    
    repo = AgentStateRepository(db_session)
    state_mgr = AgentStateManager(repo)
    config_mgr = MockConfigManager()
    
    agent = agent_registry.create(agent_type=AITask.PLANNING, tenant_id="tenant-001", config_manager=config_mgr)
    lifecycle = AgentLifecycle(agent=agent, pool=agent_pool, state_manager=state_mgr)
    
    await lifecycle.initialize()
    await lifecycle.execute({"tenant_id": "tenant-001"})
    
    agent_id = agent.agent_id
    
    # Discard live objects (simulating crash)
    del agent
    del lifecycle
    
    # Restart registry and recover state manager
    new_repo = AgentStateRepository(db_session)
    new_state_mgr = AgentStateManager(new_repo)
    
    recovered = new_state_mgr.load(agent_id=agent_id, tenant_id="tenant-001")
    assert recovered is not None
    assert recovered.agent_id == agent_id
    assert recovered.tenant_id == "tenant-001"
    assert recovered.state == AgentState.IDLE

# 13. Teardown removes the agent from AgentPool and records TERMINATED health.
@pytest.mark.anyio
async def test_teardown_removes_from_pool_and_records_terminated_health(
    agent_registry: AgentRegistry,
    successful_agent_class: type[BaseAgent],
    agent_pool: AgentPool,
    health_monitor: AgentHealthMonitor
) -> None:
    """Tearing down an agent removes it from the AgentPool and records its TERMINATED health state."""
    reg = AgentRegistration(agent_type=AITask.PLANNING, agent_class=successful_agent_class, version="1.0")
    agent_registry.register(registration=reg)
    
    config_mgr = MockConfigManager()
    agent = agent_registry.create(agent_type=AITask.PLANNING, tenant_id="tenant-001", config_manager=config_mgr)
    
    lifecycle = AgentLifecycle(agent=agent, pool=agent_pool, health_monitor=health_monitor)
    await lifecycle.initialize()
    
    assert await agent_pool.contains(agent_id=agent.agent_id, tenant_id="tenant-001")
    
    await lifecycle.teardown()
    
    assert not await agent_pool.contains(agent_id=agent.agent_id, tenant_id="tenant-001")
    health = await health_monitor.get_agent_health(tenant_id="tenant-001", agent_id=agent.agent_id)
    assert health.state == AgentState.TERMINATED

# 14. Lifecycle initialization performance meets the 100 ms SLA.
@pytest.mark.performance
@pytest.mark.anyio
async def test_lifecycle_initialization_performance_sla(
    agent_registry: AgentRegistry,
    successful_agent_class: type[BaseAgent]
) -> None:
    """Lifecycle initialization SLA is under 100 ms (median or p95)."""
    reg = AgentRegistration(agent_type=AITask.PLANNING, agent_class=successful_agent_class, version="1.0")
    agent_registry.register(registration=reg)
    config_mgr = MockConfigManager()
    
    # Warm up
    for _ in range(5):
        pool = AgentPool()
        agent = agent_registry.create(agent_type=AITask.PLANNING, tenant_id="tenant-001", config_manager=config_mgr)
        lifecycle = AgentLifecycle(agent=agent, pool=pool)
        await lifecycle.initialize()
        await lifecycle.teardown()
        
    # Measure
    durations = []
    iterations = 20
    for _ in range(iterations):
        pool = AgentPool()
        agent = agent_registry.create(agent_type=AITask.PLANNING, tenant_id="tenant-001", config_manager=config_mgr)
        lifecycle = AgentLifecycle(agent=agent, pool=pool)
        
        start = time.perf_counter_ns()
        await lifecycle.initialize()
        durations.append(time.perf_counter_ns() - start)
        
        await lifecycle.teardown()
        
    durations.sort()

    p95_index = (
        math.ceil(len(durations) * 0.95)
        - 1
    )

    p95_ns = durations[p95_index]
    p95_ms = p95_ns / 1_000_000.0
    
    assert p95_ms < 100.0, f"Lifecycle initialization p95 latency is {p95_ms:.3f} ms, which exceeds the 100 ms SLA limit."
