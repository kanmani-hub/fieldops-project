import pytest
import math
import time
from datetime import timedelta
from uuid import uuid4
from app.services.ai.FieldOpsAI.agents.base import AgentState
from app.services.ai.FieldOpsAI.schemas.ai_task import AITask
from app.services.ai.FieldOpsAI.schemas.agent_result import AgentResultStatus
from app.services.ai.FieldOpsAI.schemas.agent_health import AgentHeartbeat, HealthStatus
from app.services.ai.FieldOpsAI.runtime.agent_health_monitor import AgentHealthMonitor

# 1. First heartbeat creates a tracked record.
@pytest.mark.anyio
async def test_first_heartbeat_creates_record(health_monitor: AgentHealthMonitor, clock) -> None:
    """Recording the first heartbeat creates a tracked record in the monitor."""
    agent_id = uuid4()
    hb = AgentHeartbeat(
        agent_id=agent_id,
        tenant_id="tenant-001",
        agent_type=AITask.PLANNING,
        state=AgentState.IDLE,
        observed_at=clock()
    )
    
    snapshot = await health_monitor.record_heartbeat(hb)
    assert snapshot.agent_id == agent_id
    assert snapshot.tenant_id == "tenant-001"
    assert snapshot.status == HealthStatus.HEALTHY
    
    # Check that it's tracked
    count = await health_monitor.tracked_count(tenant_id="tenant-001")
    assert count == 1

# 2. Same agent ID remains isolated across tenants.
@pytest.mark.anyio
async def test_cross_tenant_health_isolation(health_monitor: AgentHealthMonitor, clock) -> None:
    """Same agent_id registered across different tenants are treated as separate tracked entities."""
    agent_id = uuid4()
    hb1 = AgentHeartbeat(
        agent_id=agent_id,
        tenant_id="tenant-001",
        agent_type=AITask.PLANNING,
        state=AgentState.IDLE,
        observed_at=clock()
    )
    hb2 = AgentHeartbeat(
        agent_id=agent_id,
        tenant_id="tenant-002",
        agent_type=AITask.PLANNING,
        state=AgentState.ERROR,
        observed_at=clock()
    )
    
    await health_monitor.record_heartbeat(hb1)
    await health_monitor.record_heartbeat(hb2)
    
    h1 = await health_monitor.get_agent_health(agent_id=agent_id, tenant_id="tenant-001")
    h2 = await health_monitor.get_agent_health(agent_id=agent_id, tenant_id="tenant-002")
    
    assert h1 is not None
    assert h2 is not None

    assert h1.status == HealthStatus.HEALTHY
    assert h2.status == HealthStatus.UNHEALTHY

# 3. Success/failure/timeout counters are correct.
@pytest.mark.anyio
async def test_heartbeat_counters(health_monitor: AgentHealthMonitor, clock) -> None:
    """Recording heartbeats with different result statuses updates internal counters correctly."""
    agent_id = uuid4()
    
    # Success
    hb1 = AgentHeartbeat(
        agent_id=agent_id, tenant_id="tenant-001", agent_type=AITask.PLANNING,
        state=AgentState.IDLE, observed_at=clock(), result_status=AgentResultStatus.SUCCESS
    )
    # Failure
    clock.advance(1)
    hb2 = AgentHeartbeat(
        agent_id=agent_id, tenant_id="tenant-001", agent_type=AITask.PLANNING,
        state=AgentState.ERROR, observed_at=clock(), result_status=AgentResultStatus.FAILED
    )
    # Timeout
    clock.advance(1)
    hb3 = AgentHeartbeat(
        agent_id=agent_id, tenant_id="tenant-001", agent_type=AITask.PLANNING,
        state=AgentState.ERROR, observed_at=clock(), result_status=AgentResultStatus.TIMEOUT
    )
    
    await health_monitor.record_heartbeat(hb1)
    await health_monitor.record_heartbeat(hb2)
    snapshot = await health_monitor.record_heartbeat(hb3)
    
    assert snapshot.total_successes == 1
    assert snapshot.total_failures == 1
    assert snapshot.total_timeouts == 1

# 4. Duplicate and stale heartbeat is ignored.
@pytest.mark.anyio
async def test_duplicate_and_stale_heartbeat_ignored(health_monitor: AgentHealthMonitor, clock) -> None:
    """Heartbeats with observed_at <= the last observed time are ignored (counters aren't incremented)."""
    agent_id = uuid4()
    t1 = clock()
    
    hb1 = AgentHeartbeat(
        agent_id=agent_id, tenant_id="tenant-001", agent_type=AITask.PLANNING,
        state=AgentState.IDLE, observed_at=t1
    )
    # Duplicate (same timestamp)
    hb2 = AgentHeartbeat(
        agent_id=agent_id, tenant_id="tenant-001", agent_type=AITask.PLANNING,
        state=AgentState.IDLE, observed_at=t1
    )
    # Stale (earlier timestamp)
    hb3 = AgentHeartbeat(
        agent_id=agent_id, tenant_id="tenant-001", agent_type=AITask.PLANNING,
        state=AgentState.IDLE, observed_at=t1 - timedelta(seconds=1)
    )
    
    await health_monitor.record_heartbeat(hb1)
    await health_monitor.record_heartbeat(hb2)
    snapshot = await health_monitor.record_heartbeat(hb3)
    
    # Total heartbeats should be 1, because 2 and 3 were ignored
    assert snapshot.total_heartbeats == 1

# 5. Metadata is discarded from monitor storage.
@pytest.mark.anyio
async def test_metadata_discarded_from_monitor(health_monitor: AgentHealthMonitor, clock) -> None:
    """Heartbeat metadata dict must be discarded to keep storage footprint minimal and privacy-safe."""
    agent_id = uuid4()
    hb = AgentHeartbeat(
        agent_id=agent_id,
        tenant_id="tenant-001",
        agent_type=AITask.PLANNING,
        state=AgentState.IDLE,
        observed_at=clock(),
        metadata={"safe_counter": 42}
    )
    
    await health_monitor.record_heartbeat(hb)
    # Check monitor internal storage
    key = ("tenant-001", agent_id)
    record = health_monitor._records[key]
    assert not record.last_heartbeat.metadata

# 6. No heartbeat for at least 120 seconds becomes UNHEALTHY.
@pytest.mark.anyio
async def test_missing_heartbeat_becomes_unhealthy(health_monitor: AgentHealthMonitor, clock) -> None:
    """If no heartbeat is received within 120 seconds, status transitions to UNHEALTHY."""
    agent_id = uuid4()
    hb = AgentHeartbeat(
        agent_id=agent_id,
        tenant_id="tenant-001",
        agent_type=AITask.PLANNING,
        state=AgentState.IDLE,
        observed_at=clock()
    )
    await health_monitor.record_heartbeat(hb)
    
    # Advance clock by 119 seconds -> should still be HEALTHY (or degraded if > 30s)
    # At 119 seconds, the heartbeat is stale enough to be degraded,
# but it has not yet reached the unhealthy threshold.
    clock.advance(119)

    degraded = await health_monitor.get_agent_health(
        agent_id=agent_id,
        tenant_id="tenant-001",
    )

    assert degraded is not None
    assert degraded.status == HealthStatus.DEGRADED

    # The unhealthy threshold is inclusive:
    # age >= 120 seconds means UNHEALTHY.
    clock.advance(1)

    unhealthy = await health_monitor.get_agent_health(
        agent_id=agent_id,
        tenant_id="tenant-001",
    )

    assert unhealthy is not None
    assert unhealthy.status == HealthStatus.UNHEALTHY
        
    # Advance clock to 121 seconds -> should be UNHEALTHY
    clock.advance(2)
    h_unhealthy = await health_monitor.get_agent_health(agent_id=agent_id, tenant_id="tenant-001")
    assert h_unhealthy.status == HealthStatus.UNHEALTHY

# 7. Empty monitor summary is UNKNOWN.
@pytest.mark.anyio
async def test_empty_monitor_summary_is_unknown(
    health_monitor: AgentHealthMonitor,
) -> None:
    """
    A monitor with no tracked agents returns an UNKNOWN summary.
    """

    summary = await health_monitor.summarize()

    assert summary.total_agents == 0
    assert summary.healthy == 0
    assert summary.degraded == 0
    assert summary.unhealthy == 0
    assert summary.unknown == 0
    assert summary.status == HealthStatus.UNKNOWN   

# 8. Health lookup performance meets the health SLA.
@pytest.mark.performance
@pytest.mark.anyio
async def test_health_lookup_performance_sla(health_monitor: AgentHealthMonitor, clock) -> None:
    """get_agent_health lookup SLA is under 5 ms (median or p95)."""
    agent_id = uuid4()
    hb = AgentHeartbeat(
        agent_id=agent_id,
        tenant_id="tenant-001",
        agent_type=AITask.PLANNING,
        state=AgentState.IDLE,
        observed_at=clock()
    )
    await health_monitor.record_heartbeat(hb)
    
    # Warm up
    for _ in range(5):
        await health_monitor.get_agent_health(agent_id=agent_id, tenant_id="tenant-001")
        
    # Measure
    durations = []
    iterations = 50
    for _ in range(iterations):
        start = time.perf_counter_ns()
        await health_monitor.get_agent_health(agent_id=agent_id, tenant_id="tenant-001")
        durations.append(time.perf_counter_ns() - start)
        
    durations.sort()

    p95_index = (
        math.ceil(len(durations) * 0.95)
        - 1
    )

    p95_ns = durations[p95_index]
    p95_ms = p95_ns / 1_000_000.0
    
    assert p95_ms < 5.0, f"Health lookup p95 latency is {p95_ms:.3f} ms, which exceeds the 5 ms SLA limit."

