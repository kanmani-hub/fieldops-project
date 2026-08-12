"""
Tests for the FieldOps AI Agent Health Monitoring system.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.services.ai.FieldOpsAI.agents.base import AgentState, BaseAgent
from app.services.ai.FieldOpsAI.runtime.agent_pool import AgentPool
from app.services.ai.FieldOpsAI.runtime.agent_health_monitor import (
    AgentHealthMonitor,
    create_agent_health_monitor,
)
from app.services.ai.FieldOpsAI.runtime.lifecycle import AgentLifecycle
from app.services.ai.FieldOpsAI.schemas.agent_config import AgentConfig
from app.services.ai.FieldOpsAI.schemas.agent_health import (
    AgentHeartbeat,
    AgentHealthSnapshot,
    HealthStatus,
    HealthSummary,
)
from app.services.ai.FieldOpsAI.schemas.agent_result import AgentResultStatus
from app.services.ai.FieldOpsAI.schemas.ai_task import AITask


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class DummyTestAgent(BaseAgent[dict[str, Any]]):
    """Simple agent for testing lifecycle health integrations."""
    async def run(self, context: dict[str, Any]) -> dict[str, Any]:
        if context.get("raise_error"):
            raise ValueError("Test execution failure")
        if context.get("hang"):
            await asyncio.sleep(100)
        return {"success": True, "tenant_id": context["tenant_id"], "tokens_used": 5}


# ---------------------------------------------------------------------------
# Schema Validation Tests
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_schema_valid_heartbeat():
    # 1. Valid AgentHeartbeat
    hb = AgentHeartbeat(
        agent_id=uuid4(),
        tenant_id="tenant-1",
        agent_type=AITask.PLANNING,
        state=AgentState.IDLE,
        observed_at=datetime.now(timezone.utc),
        correlation_id="corr-1",
        result_status=AgentResultStatus.SUCCESS,
        latency_ms=12.5,
        safe_error_code=None,
        metadata={"step": 1}
    )
    assert hb.tenant_id == "tenant-1"
    assert hb.latency_ms == 12.5


@pytest.mark.anyio
async def test_schema_uuid_required():
    # 2. UUID4 required
    with pytest.raises(ValidationError):
        AgentHeartbeat(
            agent_id="not-a-uuid",  # invalid UUID
            tenant_id="tenant-1",
            agent_type=AITask.PLANNING,
            state=AgentState.IDLE,
            observed_at=datetime.now(timezone.utc),
        )


@pytest.mark.anyio
async def test_schema_blank_tenant_rejected():
    # 3. Blank tenant rejected
    with pytest.raises(ValidationError):
        AgentHeartbeat(
            agent_id=uuid4(),
            tenant_id="   ",  # blank string
            agent_type=AITask.PLANNING,
            state=AgentState.IDLE,
            observed_at=datetime.now(timezone.utc),
        )
    # Non-string tenant rejected
    with pytest.raises(ValidationError):
        AgentHeartbeat(
            agent_id=uuid4(),
            tenant_id=123,  # type: ignore[arg-type]
            agent_type=AITask.PLANNING,
            state=AgentState.IDLE,
            observed_at=datetime.now(timezone.utc),
        )


@pytest.mark.anyio
async def test_schema_tenant_over_50_rejected():
    # 4. Tenant over 50 rejected
    with pytest.raises(ValidationError):
        AgentHeartbeat(
            agent_id=uuid4(),
            tenant_id="a" * 51,  # 51 chars
            agent_type=AITask.PLANNING,
            state=AgentState.IDLE,
            observed_at=datetime.now(timezone.utc),
        )


@pytest.mark.anyio
async def test_schema_naive_observed_at_rejected():
    # 5. Naive observed_at rejected
    with pytest.raises(ValidationError):
        AgentHeartbeat(
            agent_id=uuid4(),
            tenant_id="tenant-1",
            agent_type=AITask.PLANNING,
            state=AgentState.IDLE,
            observed_at=datetime.now(),  # naive datetime
        )


@pytest.mark.anyio
async def test_schema_negative_latency_rejected():
    # 6. Negative latency rejected
    with pytest.raises(ValidationError):
        AgentHeartbeat(
            agent_id=uuid4(),
            tenant_id="tenant-1",
            agent_type=AITask.PLANNING,
            state=AgentState.IDLE,
            observed_at=datetime.now(timezone.utc),
            latency_ms=-1.0,
        )


@pytest.mark.anyio
async def test_schema_nan_latency_rejected():
    # 7. NaN latency rejected
    with pytest.raises(ValidationError):
        AgentHeartbeat(
            agent_id=uuid4(),
            tenant_id="tenant-1",
            agent_type=AITask.PLANNING,
            state=AgentState.IDLE,
            observed_at=datetime.now(timezone.utc),
            latency_ms=float("nan"),
        )


@pytest.mark.anyio
async def test_schema_infinity_latency_rejected():
    # 8. Infinity latency rejected
    with pytest.raises(ValidationError):
        AgentHeartbeat(
            agent_id=uuid4(),
            tenant_id="tenant-1",
            agent_type=AITask.PLANNING,
            state=AgentState.IDLE,
            observed_at=datetime.now(timezone.utc),
            latency_ms=float("inf"),
        )


@pytest.mark.anyio
async def test_schema_blank_correlation_id_rejected():
    # 9. Blank correlation ID rejected
    with pytest.raises(ValidationError):
        AgentHeartbeat(
            agent_id=uuid4(),
            tenant_id="tenant-1",
            agent_type=AITask.PLANNING,
            state=AgentState.IDLE,
            observed_at=datetime.now(timezone.utc),
            correlation_id="   ",
        )
    with pytest.raises(ValidationError):
        AgentHeartbeat(
            agent_id=uuid4(),
            tenant_id="tenant-1",
            agent_type=AITask.PLANNING,
            state=AgentState.IDLE,
            observed_at=datetime.now(timezone.utc),
            correlation_id=123,  # type: ignore[arg-type]
        )


@pytest.mark.anyio
async def test_schema_blank_safe_error_code_rejected():
    # 10. Blank safe error code rejected
    with pytest.raises(ValidationError):
        AgentHeartbeat(
            agent_id=uuid4(),
            tenant_id="tenant-1",
            agent_type=AITask.PLANNING,
            state=AgentState.IDLE,
            observed_at=datetime.now(timezone.utc),
            safe_error_code="   ",
        )
    with pytest.raises(ValidationError):
        AgentHeartbeat(
            agent_id=uuid4(),
            tenant_id="tenant-1",
            agent_type=AITask.PLANNING,
            state=AgentState.IDLE,
            observed_at=datetime.now(timezone.utc),
            safe_error_code=123,  # type: ignore[arg-type]
        )


@pytest.mark.anyio
async def test_schema_metadata_deep_copied():
    # 11. Metadata deep copied
    orig_metadata = {"nest": {"key": "val"}}
    hb = AgentHeartbeat(
        agent_id=uuid4(),
        tenant_id="tenant-1",
        agent_type=AITask.PLANNING,
        state=AgentState.IDLE,
        observed_at=datetime.now(timezone.utc),
        metadata=orig_metadata,
    )
    # Modify original metadata
    orig_metadata["nest"]["key"] = "modified"
    assert hb.metadata["nest"]["key"] == "val"


@pytest.mark.anyio
async def test_schema_non_json_metadata_rejected():
    # 12. Non-JSON metadata rejected
    class CustomObj:
        pass
    with pytest.raises(ValidationError):
        AgentHeartbeat(
            agent_id=uuid4(),
            tenant_id="tenant-1",
            agent_type=AITask.PLANNING,
            state=AgentState.IDLE,
            observed_at=datetime.now(timezone.utc),
            metadata={"obj": CustomObj()},
        )
    with pytest.raises(ValidationError):
        AgentHeartbeat(
            agent_id=uuid4(),
            tenant_id="tenant-1",
            agent_type=AITask.PLANNING,
            state=AgentState.IDLE,
            observed_at=datetime.now(timezone.utc),
            metadata="not-a-dict",  # type: ignore[arg-type]
        )
    with pytest.raises(ValidationError):
        AgentHeartbeat(
            agent_id=uuid4(),
            tenant_id="tenant-1",
            agent_type=AITask.PLANNING,
            state=AgentState.IDLE,
            observed_at=datetime.now(timezone.utc),
            metadata={123: "val"},  # non-string key
        )


@pytest.mark.anyio
async def test_schema_sensitive_keys_rejected():
    # 13. Sensitive top-level key rejected
    # 14. Sensitive nested key rejected
    # Test top-level forbidden key
    with pytest.raises(ValidationError) as exc_info:
        AgentHeartbeat(
            agent_id=uuid4(),
            tenant_id="tenant-1",
            agent_type=AITask.PLANNING,
            state=AgentState.IDLE,
            observed_at=datetime.now(timezone.utc),
            metadata={"api_key": "somekey"},
        )
    err_msg = exc_info.value.errors()[0]["msg"]
    assert "Forbidden key" in err_msg
    assert "somekey" not in err_msg  # Ensure value not included in error message

    # Test nested forbidden key
    with pytest.raises(ValidationError) as exc_info:
        AgentHeartbeat(
            agent_id=uuid4(),
            tenant_id="tenant-1",
            agent_type=AITask.PLANNING,
            state=AgentState.IDLE,
            observed_at=datetime.now(timezone.utc),
            metadata={"config": {"secret": "mysecret"}},
        )
    err_msg = exc_info.value.errors()[0]["msg"]
    assert "Forbidden key" in err_msg
    assert "mysecret" not in err_msg


@pytest.mark.anyio
async def test_schema_extra_fields_rejected():
    # 15. Extra fields rejected
    with pytest.raises(ValidationError):
        AgentHeartbeat(
            agent_id=uuid4(),
            tenant_id="tenant-1",
            agent_type=AITask.PLANNING,
            state=AgentState.IDLE,
            observed_at=datetime.now(timezone.utc),
            extra_field="rejected",  # type: ignore[call-arg]
        )


@pytest.mark.anyio
async def test_schema_snapshot_validation():
    # 16. HealthSnapshot count validation
    # Check that invalid values (like negative counts or infinite age) raise ValidationError
    with pytest.raises(ValidationError):
        AgentHealthSnapshot(
            agent_id=uuid4(),
            tenant_id="tenant-1",
            agent_type=AITask.PLANNING,
            state=AgentState.IDLE,
            status=HealthStatus.HEALTHY,
            last_seen_at=datetime.now(timezone.utc),
            age_seconds=-10.0,  # invalid negative
            consecutive_failures=0,
            total_heartbeats=0,
            total_successes=0,
            total_failures=0,
            total_timeouts=0,
        )


@pytest.mark.anyio
async def test_schema_summary_consistency():
    # 17. HealthSummary count consistency
    # sum of counts != total_agents must fail
    with pytest.raises(ValidationError):
        HealthSummary(
            status=HealthStatus.HEALTHY,
            checked_at=datetime.now(timezone.utc),
            tenant_id="tenant-1",
            total_agents=10,
            healthy=5,
            degraded=2,
            unhealthy=1,
            unknown=1,  # sum = 9 != 10
            by_agent_type={"planning": 9},
        )


# ---------------------------------------------------------------------------
# Monitor Construction Tests
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_monitor_construction_thresholds():
    # 18. Default thresholds
    m = AgentHealthMonitor()
    assert m._degraded_after_seconds == 30.0
    assert m._unhealthy_after_seconds == 120.0
    assert m._latency_window_size == 20

    # 19. Custom thresholds
    m2 = AgentHealthMonitor(degraded_after_seconds=50.5, unhealthy_after_seconds=200.0, latency_window_size=10)
    assert m2._degraded_after_seconds == 50.5
    assert m2._unhealthy_after_seconds == 200.0
    assert m2._latency_window_size == 10

    # 20. Bool degraded threshold rejected
    with pytest.raises(TypeError):
        AgentHealthMonitor(degraded_after_seconds=True)  # type: ignore[arg-type]

    # 21. Zero degraded threshold rejected
    with pytest.raises(ValueError):
        AgentHealthMonitor(degraded_after_seconds=0)

    # 22. unhealthy threshold must exceed degraded threshold
    with pytest.raises(ValueError):
        AgentHealthMonitor(degraded_after_seconds=40, unhealthy_after_seconds=30)

    # 23. unhealthy threshold maximum enforced
    with pytest.raises(ValueError):
        AgentHealthMonitor(unhealthy_after_seconds=90000)

    # 24. Bool latency window rejected
    with pytest.raises(TypeError):
        AgentHealthMonitor(latency_window_size=True)  # type: ignore[arg-type]

    # 25. Invalid latency window rejected (must be between 1 and 1000)
    with pytest.raises(ValueError):
        AgentHealthMonitor(latency_window_size=0)
    with pytest.raises(ValueError):
        AgentHealthMonitor(latency_window_size=1001)

    # 26. Non-callable clock rejected
    with pytest.raises(TypeError):
        AgentHealthMonitor(clock="not-callable")  # type: ignore[arg-type]

    # 27. Falsey callable clock retained
    # A mock class that evaluates to false (having __bool__ return False)
    class FalseyClock:
        def __bool__(self) -> bool:
            return False
        def __call__(self) -> datetime:
            return datetime.now(timezone.utc)
    fc = FalseyClock()
    m_fc = AgentHealthMonitor(clock=fc)
    assert m_fc._clock is fc

    # 28. Naive clock result rejected
    m_naive = AgentHealthMonitor(clock=lambda: datetime.now())  # naive clock
    with pytest.raises(ValueError):
        m_naive._now()

    # 29. Factory returns independent monitors
    m_a = create_agent_health_monitor()
    m_b = create_agent_health_monitor()
    assert m_a is not m_b


# ---------------------------------------------------------------------------
# Heartbeat Tracking Tests
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_heartbeat_tracking_basics():
    m = AgentHealthMonitor()
    agent_id = uuid4()
    hb = AgentHeartbeat(
        agent_id=agent_id,
        tenant_id="tenant-1",
        agent_type=AITask.PLANNING,
        state=AgentState.IDLE,
        observed_at=datetime.now(timezone.utc),
    )

    # 30. First heartbeat creates record
    snap = await m.record_heartbeat(hb)
    assert snap.agent_id == agent_id
    assert snap.tenant_id == "tenant-1"
    assert snap.total_heartbeats == 1

    # 31. Same agent ID in different tenants creates separate records
    hb_t2 = AgentHeartbeat(
        agent_id=agent_id,
        tenant_id="tenant-2",
        agent_type=AITask.PLANNING,
        state=AgentState.IDLE,
        observed_at=datetime.now(timezone.utc),
    )
    snap_t2 = await m.record_heartbeat(hb_t2)
    assert snap_t2.tenant_id == "tenant-2"
    assert await m.tracked_count(tenant_id="tenant-1") == 1
    assert await m.tracked_count(tenant_id="tenant-2") == 1
    assert await m.tracked_count() == 2


@pytest.mark.anyio
async def test_heartbeat_tracking_counters():
    m = AgentHealthMonitor()
    agent_id = uuid4()
    base_time = datetime.now(timezone.utc)

    # 32. Heartbeat increments total count
    # 33. Success increments success count
    # 34. Success resets consecutive failures
    hb1 = AgentHeartbeat(
        agent_id=agent_id,
        tenant_id="tenant-1",
        agent_type=AITask.PLANNING,
        state=AgentState.IDLE,
        observed_at=base_time + timedelta(seconds=1),
        result_status=AgentResultStatus.FAILED,
    )
    snap = await m.record_heartbeat(hb1)
    assert snap.total_heartbeats == 1
    assert snap.total_failures == 1
    assert snap.consecutive_failures == 1

    # 35. Failure increments failure count
    # 36. Failure increments consecutive failures
    hb2 = AgentHeartbeat(
        agent_id=agent_id,
        tenant_id="tenant-1",
        agent_type=AITask.PLANNING,
        state=AgentState.IDLE,
        observed_at=base_time + timedelta(seconds=2),
        result_status=AgentResultStatus.FAILED,
    )
    snap = await m.record_heartbeat(hb2)
    assert snap.total_heartbeats == 2
    assert snap.total_failures == 2
    assert snap.consecutive_failures == 2

    # 37. Timeout increments timeout count
    # 38. Timeout increments consecutive failures
    hb3 = AgentHeartbeat(
        agent_id=agent_id,
        tenant_id="tenant-1",
        agent_type=AITask.PLANNING,
        state=AgentState.IDLE,
        observed_at=base_time + timedelta(seconds=3),
        result_status=AgentResultStatus.TIMEOUT,
    )
    snap = await m.record_heartbeat(hb3)
    assert snap.total_heartbeats == 3
    assert snap.total_timeouts == 1
    assert snap.consecutive_failures == 3

    # Success resets consecutive failures
    hb4 = AgentHeartbeat(
        agent_id=agent_id,
        tenant_id="tenant-1",
        agent_type=AITask.PLANNING,
        state=AgentState.IDLE,
        observed_at=base_time + timedelta(seconds=4),
        result_status=AgentResultStatus.SUCCESS,
    )
    snap = await m.record_heartbeat(hb4)
    assert snap.total_successes == 1
    assert snap.consecutive_failures == 0

    # 39. None result does not alter result counters
    hb5 = AgentHeartbeat(
        agent_id=agent_id,
        tenant_id="tenant-1",
        agent_type=AITask.PLANNING,
        state=AgentState.RUNNING,
        observed_at=base_time + timedelta(seconds=5),
        result_status=None,
    )
    snap = await m.record_heartbeat(hb5)
    assert snap.total_successes == 1
    assert snap.total_failures == 2
    assert snap.total_timeouts == 1
    assert snap.consecutive_failures == 0


@pytest.mark.anyio
async def test_heartbeat_latency():
    m = AgentHealthMonitor(latency_window_size=3)
    agent_id = uuid4()
    base_time = datetime.now(timezone.utc)

    # 40. Last latency stored
    # 41. Average latency calculated
    # 42. Latency deque respects configured maximum
    hb1 = AgentHeartbeat(
        agent_id=agent_id,
        tenant_id="tenant-1",
        agent_type=AITask.PLANNING,
        state=AgentState.IDLE,
        observed_at=base_time + timedelta(seconds=1),
        latency_ms=10.0,
    )
    snap = await m.record_heartbeat(hb1)
    assert snap.last_latency_ms == 10.0
    assert snap.average_latency_ms == 10.0

    hb2 = AgentHeartbeat(
        agent_id=agent_id,
        tenant_id="tenant-1",
        agent_type=AITask.PLANNING,
        state=AgentState.IDLE,
        observed_at=base_time + timedelta(seconds=2),
        latency_ms=20.0,
    )
    snap = await m.record_heartbeat(hb2)
    assert snap.last_latency_ms == 20.0
    assert snap.average_latency_ms == 15.0

    hb3 = AgentHeartbeat(
        agent_id=agent_id,
        tenant_id="tenant-1",
        agent_type=AITask.PLANNING,
        state=AgentState.IDLE,
        observed_at=base_time + timedelta(seconds=3),
        latency_ms=30.0,
    )
    snap = await m.record_heartbeat(hb3)
    assert snap.last_latency_ms == 30.0
    assert snap.average_latency_ms == 20.0

    # 42. Window size is 3, so 10.0 is popped, average should be (20+30+40)/3 = 30.0
    hb4 = AgentHeartbeat(
        agent_id=agent_id,
        tenant_id="tenant-1",
        agent_type=AITask.PLANNING,
        state=AgentState.IDLE,
        observed_at=base_time + timedelta(seconds=4),
        latency_ms=40.0,
    )
    snap = await m.record_heartbeat(hb4)
    assert snap.last_latency_ms == 40.0
    assert snap.average_latency_ms == 30.0


@pytest.mark.anyio
async def test_heartbeat_ordering_and_replay():
    m = AgentHealthMonitor()
    agent_id = uuid4()
    base_time = datetime.now(timezone.utc)

    hb1 = AgentHeartbeat(
        agent_id=agent_id,
        tenant_id="tenant-1",
        agent_type=AITask.PLANNING,
        state=AgentState.IDLE,
        observed_at=base_time,
    )
    snap1 = await m.record_heartbeat(hb1)
    assert snap1.total_heartbeats == 1

    # 43. Stale heartbeat ignored
    hb_stale = AgentHeartbeat(
        agent_id=agent_id,
        tenant_id="tenant-1",
        agent_type=AITask.PLANNING,
        state=AgentState.RUNNING,
        observed_at=base_time - timedelta(seconds=1),
    )
    snap_stale = await m.record_heartbeat(hb_stale)
    assert snap_stale.total_heartbeats == 1
    assert snap_stale.state == AgentState.IDLE  # unchanged

    # 44. Duplicate heartbeat replay ignored
    snap_replay = await m.record_heartbeat(hb1)
    assert snap_replay.total_heartbeats == 1

    # 45. Newer heartbeat accepted
    hb_new = AgentHeartbeat(
        agent_id=agent_id,
        tenant_id="tenant-1",
        agent_type=AITask.PLANNING,
        state=AgentState.RUNNING,
        observed_at=base_time + timedelta(seconds=1),
    )
    snap_new = await m.record_heartbeat(hb_new)
    assert snap_new.total_heartbeats == 2
    assert snap_new.state == AgentState.RUNNING  # 46. Agent state updated


@pytest.mark.anyio
async def test_heartbeat_safe_error_and_metadata_privacy():
    m = AgentHealthMonitor()
    agent_id = uuid4()
    base_time = datetime.now(timezone.utc)

    hb = AgentHeartbeat(
        agent_id=agent_id,
        tenant_id="tenant-1",
        agent_type=AITask.PLANNING,
        state=AgentState.ERROR,
        observed_at=base_time,
        safe_error_code="AGENT_EXECUTION_FAILED",
        metadata={"step": 2},
    )
    # 47. Safe error code updated
    snap = await m.record_heartbeat(hb)
    assert snap.safe_error_code == "AGENT_EXECUTION_FAILED"

    # 48. Metadata is not exposed in health snapshot
    assert not hasattr(snap, "metadata")


# ---------------------------------------------------------------------------
# Status Calculation Tests
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_status_calculation():
    # We will control the clock in the monitor
    current_time = datetime.now(timezone.utc)
    m = AgentHealthMonitor(
        degraded_after_seconds=30.0,
        unhealthy_after_seconds=120.0,
        clock=lambda: current_time
    )

    agent_id = uuid4()
    # Helper to send a heartbeat (uses the current_time)
    async def send_hb(state=AgentState.IDLE, result_status=None):
        hb = AgentHeartbeat(
            agent_id=agent_id,
            tenant_id="tenant-1",
            agent_type=AITask.PLANNING,
            state=state,
            observed_at=current_time,
            result_status=result_status,
        )
        return await m.record_heartbeat(hb)

    # 49. Recent idle agent is healthy
    snap = await send_hb(AgentState.IDLE)
    assert snap.status == HealthStatus.HEALTHY

    # Advance clock to prevent replay rejection
    current_time += timedelta(seconds=1)

    # 50. Recent running agent is healthy
    snap = await send_hb(AgentState.RUNNING)
    assert snap.status == HealthStatus.HEALTHY

    current_time += timedelta(seconds=1)

    # 51. Recent terminated agent is healthy
    snap = await send_hb(AgentState.TERMINATED)
    assert snap.status == HealthStatus.HEALTHY

    current_time += timedelta(seconds=1)

    # 52. Error state is unhealthy
    snap = await send_hb(AgentState.ERROR)
    assert snap.status == HealthStatus.UNHEALTHY

    current_time += timedelta(seconds=1)

    # 53. Failed result is unhealthy
    snap = await send_hb(AgentState.IDLE, result_status=AgentResultStatus.FAILED)
    assert snap.status == HealthStatus.UNHEALTHY

    current_time += timedelta(seconds=1)

    # 54. Timeout result is unhealthy
    snap = await send_hb(AgentState.IDLE, result_status=AgentResultStatus.TIMEOUT)
    assert snap.status == HealthStatus.UNHEALTHY

    current_time += timedelta(seconds=1)

    # Reset to healthy
    await send_hb(AgentState.IDLE, result_status=AgentResultStatus.SUCCESS)

    # 55. Stale beyond degraded threshold is degraded
    # Advance clock by 35 seconds
    current_time += timedelta(seconds=35)
    snap = await m.get_agent_health(agent_id=agent_id, tenant_id="tenant-1")
    assert snap is not None
    assert snap.status == HealthStatus.DEGRADED

    # 56. Stale beyond unhealthy threshold is unhealthy
    # Advance clock by another 100 seconds (total 135)
    current_time += timedelta(seconds=100)
    snap = await m.get_agent_health(agent_id=agent_id, tenant_id="tenant-1")
    assert snap is not None
    assert snap.status == HealthStatus.UNHEALTHY

    # 57. Consecutive failure produces degraded when not otherwise unhealthy
    # We use a new monitor to avoid clock backward ordering issues
    current_time_fresh = datetime.now(timezone.utc)
    m_fresh = AgentHealthMonitor(
        degraded_after_seconds=30.0,
        unhealthy_after_seconds=120.0,
        clock=lambda: current_time_fresh
    )
    
    async def send_hb_fresh(state=AgentState.IDLE, result_status=None):
        hb = AgentHeartbeat(
            agent_id=agent_id,
            tenant_id="tenant-1",
            agent_type=AITask.PLANNING,
            state=state,
            observed_at=current_time_fresh,
            result_status=result_status,
        )
        return await m_fresh.record_heartbeat(hb)

    # Send a failure first (results in UNHEALTHY because of result_status=FAILED)
    snap = await send_hb_fresh(AgentState.IDLE, result_status=AgentResultStatus.FAILED)
    assert snap.status == HealthStatus.UNHEALTHY
    
    # Advance clock and send a heartbeat with result_status=None, keeping consecutive_failures = 1
    # State is IDLE, age is recent. But consecutive_failures > 0, so it should be DEGRADED.
    current_time_fresh += timedelta(seconds=1)
    snap = await send_hb_fresh(AgentState.IDLE, result_status=None)
    assert snap.status == HealthStatus.DEGRADED

    # 58. Status changes as injected clock advances
    # 59. Status is calculated dynamically
    current_time_fresh += timedelta(seconds=1)
    # Record a clean success, should be HEALTHY
    await send_hb_fresh(AgentState.IDLE, result_status=AgentResultStatus.SUCCESS)
    snap = await m_fresh.get_agent_health(agent_id=agent_id, tenant_id="tenant-1")
    assert snap is not None
    assert snap.status == HealthStatus.HEALTHY

    # Dynamically advance clock, check status without new heartbeat
    current_time_fresh += timedelta(seconds=40)
    snap = await m_fresh.get_agent_health(agent_id=agent_id, tenant_id="tenant-1")
    assert snap is not None
    assert snap.status == HealthStatus.DEGRADED

    current_time_fresh += timedelta(seconds=100)
    snap = await m_fresh.get_agent_health(agent_id=agent_id, tenant_id="tenant-1")
    assert snap is not None
    assert snap.status == HealthStatus.UNHEALTHY

    # Negative age returns age = 0.0
    clock_past = lambda: current_time_fresh - timedelta(seconds=200)
    m_past = AgentHealthMonitor(clock=clock_past)
    hb_now = AgentHeartbeat(
        agent_id=agent_id,
        tenant_id="tenant-1",
        agent_type=AITask.PLANNING,
        state=AgentState.IDLE,
        observed_at=current_time_fresh,
    )
    await m_past.record_heartbeat(hb_now)
    snap_past = await m_past.get_agent_health(agent_id=agent_id, tenant_id="tenant-1")
    assert snap_past is not None
    assert snap_past.age_seconds == 0.0


# ---------------------------------------------------------------------------
# Tenant-Safe Operations Tests
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_tenant_safe_operations():
    m = AgentHealthMonitor()
    agent_id = uuid4()
    base_time = datetime.now(timezone.utc)

    hb = AgentHeartbeat(
        agent_id=agent_id,
        tenant_id="tenant-1",
        agent_type=AITask.PLANNING,
        state=AgentState.IDLE,
        observed_at=base_time,
    )
    await m.record_heartbeat(hb)

    # 60. Correct tenant returns snapshot
    snap = await m.get_agent_health(agent_id=agent_id, tenant_id="tenant-1")
    assert snap is not None
    assert snap.tenant_id == "tenant-1"

    # 61. Wrong tenant returns None
    snap_wrong = await m.get_agent_health(agent_id=agent_id, tenant_id="tenant-2")
    assert snap_wrong is None

    # Let's add more agents
    agent_id2 = uuid4()
    hb2 = AgentHeartbeat(
        agent_id=agent_id2,
        tenant_id="tenant-1",
        agent_type=AITask.DISPATCH,
        state=AgentState.RUNNING,
        observed_at=base_time,
    )
    await m.record_heartbeat(hb2)

    hb3 = AgentHeartbeat(
        agent_id=uuid4(),
        tenant_id="tenant-2",
        agent_type=AITask.PLANNING,
        state=AgentState.ERROR,
        observed_at=base_time,
    )
    await m.record_heartbeat(hb3)

    # 62. List all
    all_snaps = await m.list_agent_health()
    assert len(all_snaps) == 3

    # 63. List by tenant
    t1_snaps = await m.list_agent_health(tenant_id="tenant-1")
    assert len(t1_snaps) == 2

    # 64. List by agent type
    planning_snaps = await m.list_agent_health(agent_type=AITask.PLANNING)
    assert len(planning_snaps) == 2

    # 65. List by health status
    unhealthy_snaps = await m.list_agent_health(status=HealthStatus.UNHEALTHY)
    assert len(unhealthy_snaps) == 1
    assert unhealthy_snaps[0].tenant_id == "tenant-2"

    # 66. Deterministic list ordering (sorted by tenant_id, agent_type, agent_id)
    # tenant-1 planning, tenant-1 dispatch, tenant-2 planning
    ordered = await m.list_agent_health()
    assert ordered[0].tenant_id == "tenant-1"
    assert ordered[0].agent_type == AITask.DISPATCH  # "dispatch" < "planning"
    assert ordered[1].tenant_id == "tenant-1"
    assert ordered[1].agent_type == AITask.PLANNING
    assert ordered[2].tenant_id == "tenant-2"

    # 67. Remove existing returns True
    assert await m.remove_agent(agent_id=agent_id, tenant_id="tenant-1") is True
    # Verify removed
    assert await m.get_agent_health(agent_id=agent_id, tenant_id="tenant-1") is None

    # 68. Remove missing returns False
    assert await m.remove_agent(agent_id=agent_id, tenant_id="tenant-1") is False

    # 69. Clear tenant removes only matching tenant
    removed_count = await m.clear_tenant("tenant-2")
    assert removed_count == 1
    assert await m.tracked_count() == 1  # Only tenant-1 agent2 remains

    # 70. tracked_count all
    assert await m.tracked_count() == 1

    # 71. tracked_count by tenant
    assert await m.tracked_count(tenant_id="tenant-1") == 1
    assert await m.tracked_count(tenant_id="tenant-2") == 0

    # 72. Invalid tenant filters rejected
    with pytest.raises(TypeError):
        await m.list_agent_health(tenant_id=123)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        await m.list_agent_health(tenant_id="  ")
    with pytest.raises(ValueError):
        await m.clear_tenant(tenant_id="a" * 52)
    with pytest.raises(TypeError):
        await m.remove_agent(agent_id="not-a-uuid", tenant_id="tenant-1")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        await m.get_agent_health(agent_id="not-a-uuid", tenant_id="tenant-1")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Summarize Tests
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_summarize():
    m = AgentHealthMonitor()
    base_time = datetime.now(timezone.utc)

    # Verify summary on empty monitor returns UNKNOWN
    sum_empty = await m.summarize()
    assert sum_empty.total_agents == 0
    assert sum_empty.status == HealthStatus.UNKNOWN

    # Add healthy agent
    hb1 = AgentHeartbeat(
        agent_id=uuid4(),
        tenant_id="tenant-1",
        agent_type=AITask.PLANNING,
        state=AgentState.IDLE,
        observed_at=base_time,
    )
    await m.record_heartbeat(hb1)

    # Add unhealthy agent
    hb2 = AgentHeartbeat(
        agent_id=uuid4(),
        tenant_id="tenant-1",
        agent_type=AITask.DISPATCH,
        state=AgentState.ERROR,
        observed_at=base_time,
    )
    await m.record_heartbeat(hb2)

    # Add degraded agent (PAUSED state)
    hb3 = AgentHeartbeat(
        agent_id=uuid4(),
        tenant_id="tenant-2",
        agent_type=AITask.PLANNING,
        state=AgentState.PAUSED,
        observed_at=base_time,
    )
    await m.record_heartbeat(hb3)

    # Summarize all
    s_all = await m.summarize()
    assert s_all.total_agents == 3
    assert s_all.healthy == 1
    assert s_all.unhealthy == 1
    assert s_all.degraded == 1
    assert s_all.by_agent_type == {"planning": 2, "dispatch": 1}
    assert s_all.status == HealthStatus.UNHEALTHY

    # Summarize tenant-1 only
    s_t1 = await m.summarize(tenant_id="tenant-1")
    assert s_t1.total_agents == 2
    assert s_t1.healthy == 1
    assert s_t1.unhealthy == 1
    assert s_t1.degraded == 0
    assert s_t1.by_agent_type == {"planning": 1, "dispatch": 1}
    assert s_t1.status == HealthStatus.UNHEALTHY

    # Summarize tenant-2 only
    s_t2 = await m.summarize(tenant_id="tenant-2")
    assert s_t2.total_agents == 1
    assert s_t2.healthy == 0
    assert s_t2.unhealthy == 0
    assert s_t2.degraded == 1
    assert s_t2.status == HealthStatus.DEGRADED


# ---------------------------------------------------------------------------
# Lifecycle Integration Tests
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_lifecycle_integration(anyio_backend):
    pool = AgentPool()
    agent_config = AgentConfig(
        agent_type=AITask.PLANNING,
        tenant_id="tenant-1",
        timeout_seconds=2.0,
        enabled=True,
    )
    agent = DummyTestAgent(config=agent_config)

    # 73. Lifecycle without monitor has no regression
    lifecycle_no_mon = AgentLifecycle(agent=agent, pool=pool)
    await lifecycle_no_mon.initialize()
    res = await lifecycle_no_mon.execute(context={"tenant_id": "tenant-1"})
    assert res.status == AgentResultStatus.SUCCESS
    await lifecycle_no_mon.teardown()

    # Re-setup fresh agent & pool for monitor testing
    pool = AgentPool()
    agent = DummyTestAgent(config=agent_config)
    m = AgentHealthMonitor()

    lifecycle = AgentLifecycle(
        agent=agent,
        pool=pool,
        health_monitor=m,
    )

    # 74. Initialize records IDLE heartbeat
    await lifecycle.initialize(correlation_id="init-corr")
    snap = await m.get_agent_health(agent_id=agent.agent_id, tenant_id="tenant-1")
    assert snap is not None
    assert snap.state == AgentState.IDLE
    assert snap.total_heartbeats == 1
    assert snap.consecutive_failures == 0

    # 75, 76. Execution records RUNNING heartbeat followed by SUCCESS and IDLE
    res = await lifecycle.execute(context={"tenant_id": "tenant-1"}, correlation_id="exec-corr")
    assert res.status == AgentResultStatus.SUCCESS
    snap = await m.get_agent_health(agent_id=agent.agent_id, tenant_id="tenant-1")
    assert snap is not None
    assert snap.state == AgentState.IDLE
    assert snap.total_heartbeats == 3  # init IDLE, exec RUNNING, exec IDLE (SUCCESS)
    assert snap.total_successes == 1
    assert snap.last_result_status == AgentResultStatus.SUCCESS
    assert snap.last_latency_ms is not None

    # Setup another agent for failure test so we don't pollute the IDLE success state
    agent_fail = DummyTestAgent(config=agent_config)
    lifecycle_fail = AgentLifecycle(agent=agent_fail, pool=pool, health_monitor=m)
    await lifecycle_fail.initialize(correlation_id="init-fail-corr")

    # 77. Failed execution records FAILED and ERROR
    res = await lifecycle_fail.execute(
        context={"tenant_id": "tenant-1", "raise_error": True},
        correlation_id="fail-corr"
    )
    assert res.status == AgentResultStatus.FAILED
    snap = await m.get_agent_health(agent_id=agent_fail.agent_id, tenant_id="tenant-1")
    assert snap is not None
    assert snap.state == AgentState.ERROR
    assert snap.total_heartbeats == 3  # init IDLE, exec RUNNING, exec ERROR (FAILED)
    assert snap.total_failures == 1
    assert snap.last_result_status == AgentResultStatus.FAILED
    assert snap.safe_error_code == "AGENT_EXECUTION_FAILED"

    # Setup another agent for timeout test
    agent_timeout = DummyTestAgent(config=agent_config)
    lifecycle_timeout = AgentLifecycle(
        agent=agent_timeout,
        pool=pool,
        health_monitor=m,
        run_timeout_seconds=0.01,
    )
    await lifecycle_timeout.initialize(correlation_id="init-timeout-corr")

    # 78. Timeout records TIMEOUT and ERROR
    res = await lifecycle_timeout.execute(
        context={"tenant_id": "tenant-1", "hang": True},
        correlation_id="timeout-corr"
    )
    assert res.status == AgentResultStatus.TIMEOUT
    snap = await m.get_agent_health(agent_id=agent_timeout.agent_id, tenant_id="tenant-1")
    assert snap is not None
    assert snap.state == AgentState.ERROR
    assert snap.total_heartbeats == 3  # init IDLE, exec RUNNING, exec ERROR (TIMEOUT)
    assert snap.total_timeouts == 1
    assert snap.last_result_status == AgentResultStatus.TIMEOUT
    assert snap.safe_error_code == "AGENT_EXECUTION_TIMEOUT"

    # 79. Teardown records TERMINATED
    await lifecycle.teardown(correlation_id="tear-corr")
    snap = await m.get_agent_health(agent_id=agent.agent_id, tenant_id="tenant-1")
    assert snap is not None
    assert snap.state == AgentState.TERMINATED
    assert snap.total_heartbeats == 4  # +1 TERMINATED

    # 80. Lifecycle correlation ID is preserved
    rec = m._records.get(("tenant-1", agent.agent_id))
    assert rec is not None
    assert rec.last_heartbeat is not None
    assert rec.last_heartbeat.correlation_id == "tear-corr"

    # 81. Context and result payload are not placed in metadata
    assert rec.last_heartbeat.metadata == {}


@pytest.mark.anyio
async def test_lifecycle_monitor_failure_tolerance(anyio_backend):
    # 82. Monitor failure during initialize does not interrupt
    # 83. Monitor failure during execute does not interrupt
    # 84. Monitor failure during teardown does not interrupt
    class FailingMonitor(AgentHealthMonitor):
        async def record_heartbeat(self, heartbeat: AgentHeartbeat) -> AgentHealthSnapshot:
            raise RuntimeError("Database connection lost")

    m = FailingMonitor()
    pool = AgentPool()
    agent_config = AgentConfig(
        agent_type=AITask.PLANNING,
        tenant_id="tenant-1",
        timeout_seconds=2.0,
        enabled=True,
    )
    agent = DummyTestAgent(config=agent_config)

    lifecycle = AgentLifecycle(
        agent=agent,
        pool=pool,
        health_monitor=m,
    )

    # Initialize must succeed despite failing monitor
    await lifecycle.initialize()
    assert lifecycle.initialized is True

    # Execute must succeed despite failing monitor
    res = await lifecycle.execute(context={"tenant_id": "tenant-1"})
    assert res.status == AgentResultStatus.SUCCESS

    # Teardown must succeed despite failing monitor
    await lifecycle.teardown()
    assert lifecycle.initialized is False


@pytest.mark.anyio
async def test_lifecycle_persistence_beside_health(anyio_backend):
    # 85. Persistent-state integration still operates beside health monitoring
    # 86. AgentPool behavior remains unchanged
    class MockStateManager:
        def __init__(self) -> None:
            self.saved = []
        def save_agent(self, agent, **kwargs):
            self.saved.append(agent.agent_id)

    sm = MockStateManager()
    m = AgentHealthMonitor()
    pool = AgentPool()
    agent_config = AgentConfig(
        agent_type=AITask.PLANNING,
        tenant_id="tenant-1",
        timeout_seconds=2.0,
        enabled=True,
    )
    agent = DummyTestAgent(config=agent_config)

    lifecycle = AgentLifecycle(
        agent=agent,
        pool=pool,
        state_manager=sm,
        health_monitor=m,
    )

    await lifecycle.initialize()
    # Check pool contains agent
    assert await pool.contains(agent_id=agent.agent_id, tenant_id="tenant-1") is True
    # Check persistence was called (after setup)
    assert agent.agent_id in sm.saved

    await lifecycle.execute(context={"tenant_id": "tenant-1"})
    await lifecycle.teardown()
    # Check pool does not contain agent
    assert await pool.contains(agent_id=agent.agent_id, tenant_id="tenant-1") is False


# ---------------------------------------------------------------------------
# Concurrency Tests
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_concurrency():
    # 87. Concurrently record heartbeats for 50 distinct agents.
    # 88. Verify 50 records exist and each has one heartbeat.
    m = AgentHealthMonitor()
    base_time = datetime.now(timezone.utc)
    agent_ids = [uuid4() for _ in range(50)]

    async def send_concurrent(agent_id: UUID):
        hb = AgentHeartbeat(
            agent_id=agent_id,
            tenant_id="tenant-1",
            agent_type=AITask.PLANNING,
            state=AgentState.RUNNING,
            observed_at=base_time,
            result_status=AgentResultStatus.SUCCESS,
        )
        await m.record_heartbeat(hb)

    await asyncio.gather(*(send_concurrent(aid) for aid in agent_ids))
    
    # Verify 50 records exist
    assert await m.tracked_count() == 50
    
    # Verify each has exactly one heartbeat
    for aid in agent_ids:
        snap = await m.get_agent_health(agent_id=aid, tenant_id="tenant-1")
        assert snap is not None
        assert snap.total_heartbeats == 1
        assert snap.total_successes == 1


# ---------------------------------------------------------------------------
# Extra Coverage Edge Cases
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_coverage_edge_cases():
    m = AgentHealthMonitor()
    # Test metadata with list, bool, None
    hb = AgentHeartbeat(
        agent_id=uuid4(),
        tenant_id="tenant-1",
        agent_type=AITask.PLANNING,
        state=AgentState.IDLE,
        observed_at=datetime.now(timezone.utc),
        correlation_id=None,
        safe_error_code=None,
        metadata={
            "list": [1, True, None],
            "bool": False,
            "none": None,
        }
    )
    assert hb.metadata["list"] == [1, True, None]
    assert hb.metadata["bool"] is False
    assert hb.metadata["none"] is None

    # Test get_agent_health with invalid type for agent_id
    with pytest.raises(TypeError):
        await m.get_agent_health(agent_id="not-a-uuid", tenant_id="tenant-1")  # type: ignore[arg-type]

    # Test list_agent_health with invalid status type
    with pytest.raises(TypeError):
        await m.list_agent_health(status="not-a-health-status")  # type: ignore[arg-type]

    # Test list_agent_health with invalid agent_type type
    with pytest.raises(TypeError):
        await m.list_agent_health(agent_type="not-an-ai-task")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Story Corrections Tests
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_extremely_large_int_metadata_accepted():
    # Large integer should not throw OverflowError
    hb = AgentHeartbeat(
        agent_id=uuid4(),
        tenant_id="tenant-1",
        agent_type=AITask.PLANNING,
        state=AgentState.IDLE,
        observed_at=datetime.now(timezone.utc),
        metadata={"large_int": 10**100}
    )
    assert hb.metadata["large_int"] == 10**100


@pytest.mark.anyio
async def test_non_finite_float_metadata_rejected():
    with pytest.raises(ValidationError):
        AgentHeartbeat(
            agent_id=uuid4(),
            tenant_id="tenant-1",
            agent_type=AITask.PLANNING,
            state=AgentState.IDLE,
            observed_at=datetime.now(timezone.utc),
            metadata={"nan_float": float("nan")}
        )
    with pytest.raises(ValidationError):
        AgentHeartbeat(
            agent_id=uuid4(),
            tenant_id="tenant-1",
            agent_type=AITask.PLANNING,
            state=AgentState.IDLE,
            observed_at=datetime.now(timezone.utc),
            metadata={"inf_float": float("inf")}
        )


@pytest.mark.anyio
async def test_monitor_heartbeat_metadata_stripped_and_mutation():
    m = AgentHealthMonitor()
    agent_id = uuid4()
    hb = AgentHeartbeat(
        agent_id=agent_id,
        tenant_id="tenant-1",
        agent_type=AITask.PLANNING,
        state=AgentState.IDLE,
        observed_at=datetime.now(timezone.utc),
        metadata={"key": "val"},
    )
    await m.record_heartbeat(hb)
    
    # Mutating original heartbeat metadata dict has no effect on stored state
    hb.metadata["key"] = "mutated"
    rec = m._records[("tenant-1", agent_id)]
    assert rec.last_heartbeat.metadata == {}


@pytest.mark.anyio
async def test_clock_validations():
    # clock returning None rejected
    m_none = AgentHealthMonitor(clock=lambda: None)  # type: ignore[return-value]
    with pytest.raises(TypeError):
        m_none._now()

    # clock returning string rejected
    m_str = AgentHealthMonitor(clock=lambda: "not-a-datetime")  # type: ignore[return-value]
    with pytest.raises(TypeError):
        m_str._now()


@pytest.mark.anyio
async def test_snapshot_validations_counters():
    # total_successes + total_failures + total_timeouts > total_heartbeats must raise ValidationError
    with pytest.raises(ValidationError):
        AgentHealthSnapshot(
            agent_id=uuid4(),
            tenant_id="tenant-1",
            agent_type=AITask.PLANNING,
            state=AgentState.IDLE,
            status=HealthStatus.HEALTHY,
            last_seen_at=datetime.now(timezone.utc),
            age_seconds=10.0,
            consecutive_failures=0,
            total_heartbeats=10,
            total_successes=5,
            total_failures=5,
            total_timeouts=1,
        )


@pytest.mark.anyio
async def test_summary_by_agent_type_mismatch():
    # Sum of counts in by_agent_type must match total_agents
    with pytest.raises(ValidationError):
        HealthSummary(
            status=HealthStatus.HEALTHY,
            checked_at=datetime.now(timezone.utc),
            tenant_id="tenant-1",
            total_agents=10,
            healthy=5,
            degraded=2,
            unhealthy=2,
            unknown=1,
            by_agent_type={"planning": 9},  # 9 != 10
        )


@pytest.mark.anyio
async def test_naive_snapshot_and_summary_timestamps():
    with pytest.raises(ValidationError):
        AgentHealthSnapshot(
            agent_id=uuid4(),
            tenant_id="tenant-1",
            agent_type=AITask.PLANNING,
            state=AgentState.IDLE,
            status=HealthStatus.HEALTHY,
            last_seen_at=datetime.now(),  # naive
            age_seconds=10.0,
            consecutive_failures=0,
            total_heartbeats=10,
            total_successes=5,
            total_failures=5,
            total_timeouts=0,
        )
    with pytest.raises(ValidationError):
        HealthSummary(
            status=HealthStatus.HEALTHY,
            checked_at=datetime.now(),  # naive
            tenant_id="tenant-1",
            total_agents=10,
            healthy=5,
            degraded=2,
            unhealthy=2,
            unknown=1,
            by_agent_type={"planning": 10},
        )


@pytest.mark.anyio
async def test_snapshot_fields_strengthened():
    # tenant_id blank
    with pytest.raises(ValidationError):
        AgentHealthSnapshot(
            agent_id=uuid4(),
            tenant_id="   ",
            agent_type=AITask.PLANNING,
            state=AgentState.IDLE,
            status=HealthStatus.HEALTHY,
            last_seen_at=datetime.now(timezone.utc),
            age_seconds=10.0,
            consecutive_failures=0,
            total_heartbeats=10,
            total_successes=5,
            total_failures=5,
            total_timeouts=0,
        )
    # safe_error_code blank
    with pytest.raises(ValidationError):
        AgentHealthSnapshot(
            agent_id=uuid4(),
            tenant_id="tenant-1",
            agent_type=AITask.PLANNING,
            state=AgentState.IDLE,
            status=HealthStatus.HEALTHY,
            last_seen_at=datetime.now(timezone.utc),
            age_seconds=10.0,
            consecutive_failures=0,
            total_heartbeats=10,
            total_successes=5,
            total_failures=5,
            total_timeouts=0,
            safe_error_code="   ",
        )
    # NaN age
    with pytest.raises(ValidationError):
        AgentHealthSnapshot(
            agent_id=uuid4(),
            tenant_id="tenant-1",
            agent_type=AITask.PLANNING,
            state=AgentState.IDLE,
            status=HealthStatus.HEALTHY,
            last_seen_at=datetime.now(timezone.utc),
            age_seconds=float("nan"),
            consecutive_failures=0,
            total_heartbeats=10,
            total_successes=5,
            total_failures=5,
            total_timeouts=0,
        )


# ---------------------------------------------------------------------------
# New Corrections Tests
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_nan_and_infinity_thresholds_rejected():
    with pytest.raises(ValueError):
        AgentHealthMonitor(degraded_after_seconds=float("nan"))
    with pytest.raises(ValueError):
        AgentHealthMonitor(degraded_after_seconds=float("inf"))
    with pytest.raises(ValueError):
        AgentHealthMonitor(unhealthy_after_seconds=float("nan"))
    with pytest.raises(ValueError):
        AgentHealthMonitor(unhealthy_after_seconds=float("inf"))
    def test_extremely_large_thresholds_rejected():
        """
        Extremely large integer thresholds must be rejected with
        ValueError instead of causing OverflowError.
        """

        with pytest.raises(ValueError):
            AgentHealthMonitor(
                degraded_after_seconds=10**1000,
            )

        with pytest.raises(ValueError):
            AgentHealthMonitor(
                unhealthy_after_seconds=10**1000,
            )


@pytest.mark.anyio
async def test_health_monitor_failure_logs_no_raw_exception():
    class FailingMonitor(AgentHealthMonitor):
        async def record_heartbeat(self, heartbeat: AgentHeartbeat) -> AgentHealthSnapshot:
            raise RuntimeError("Database connection lost")

    m = FailingMonitor()
    pool = AgentPool()
    agent_config = AgentConfig(
        agent_type=AITask.PLANNING,
        tenant_id="tenant-1",
        timeout_seconds=2.0,
        enabled=True,
    )
    agent = DummyTestAgent(config=agent_config)

    lifecycle = AgentLifecycle(
        agent=agent,
        pool=pool,
        health_monitor=m,
    )

    class MockLogger:
        def __init__(self):
            self.warnings = []
            self.exceptions = []
        def bind(self, **kwargs):
            return self
        def warning(self, event, **kwargs):
            self.warnings.append((event, kwargs))
        def exception(self, event, **kwargs):
            self.exceptions.append((event, kwargs))
        def debug(self, event, **kwargs):
            pass
        def info(self, event, **kwargs):
            pass

    mock_logger = MockLogger()
    lifecycle._logger = mock_logger

    await lifecycle.initialize()

    # Verify no traceback log, only warning call
    assert len(mock_logger.exceptions) == 0
    assert len(mock_logger.warnings) == 1
    event, kwargs = mock_logger.warnings[0]
    assert event == "agent_lifecycle_health_record_failed"
    assert "agent_id" in kwargs
    assert "tenant_id" in kwargs
    assert "agent_type" in kwargs
    assert "state" in kwargs
    assert "correlation_id" in kwargs
    assert "exc_info" not in kwargs
    assert "exception" not in kwargs


@pytest.mark.anyio
async def test_complete_nested_forbidden_key_path():
    with pytest.raises(ValidationError) as exc_info:
        AgentHeartbeat(
            agent_id=uuid4(),
            tenant_id="tenant-1",
            agent_type=AITask.PLANNING,
            state=AgentState.IDLE,
            observed_at=datetime.now(timezone.utc),
            metadata={"config": {"secret": "mysecret"}},
        )
    err_msg = exc_info.value.errors()[0]["msg"]
    assert "metadata.config.secret" in err_msg
    assert "mysecret" not in err_msg


@pytest.mark.anyio
async def test_health_monitor_additional_coverage():
    # 1. Constructor type check for unhealthy_after_seconds
    with pytest.raises(TypeError, match="unhealthy_after_seconds must be a float or int"):
        AgentHealthMonitor(unhealthy_after_seconds="not-a-float")

    # 2. _make_snapshot ValueError when record has no heartbeat
    from app.services.ai.FieldOpsAI.runtime.agent_health_monitor import _AgentHealthRecord
    m = AgentHealthMonitor()
    record = _AgentHealthRecord(latency_window_size=20)
    with pytest.raises(ValueError, match="Record has no heartbeat"):
        m._make_snapshot(record, datetime.now(timezone.utc))

    # 3. record_heartbeat invalid type TypeError
    with pytest.raises(TypeError, match="heartbeat must be an AgentHeartbeat instance"):
        await m.record_heartbeat("not-a-heartbeat")

    # 4 & 5. Heartbeat timeout tracking (first and subsequent)
    m2 = AgentHealthMonitor()
    agent_id = uuid4()
    hb_timeout = AgentHeartbeat(
        agent_id=agent_id,
        tenant_id="tenant-1",
        agent_type=AITask.PLANNING,
        state=AgentState.IDLE,
        observed_at=datetime.now(timezone.utc),
        result_status=AgentResultStatus.TIMEOUT
    )
    # First heartbeat as timeout
    snap = await m2.record_heartbeat(hb_timeout)
    assert snap.total_timeouts == 1
    assert snap.consecutive_failures == 1

    # Subsequent heartbeat as timeout
    hb_timeout2 = hb_timeout.model_copy(update={"observed_at": hb_timeout.observed_at + timedelta(seconds=1)})
    snap2 = await m2.record_heartbeat(hb_timeout2)
    assert snap2.total_timeouts == 2
    assert snap2.consecutive_failures == 2

    # 6. list_agent_health skips record with no heartbeat
    m3 = AgentHealthMonitor()
    m3._records[("tenant-1", agent_id)] = record # record has last_heartbeat=None
    snaps = await m3.list_agent_health(tenant_id="tenant-1")
    assert len(snaps) == 0

    # 7. summarize skips record with no heartbeat
    summary = await m3.summarize(tenant_id="tenant-1")
    assert summary.total_agents == 0

    # 8. summarize aggregates UNKNOWN status
    from unittest.mock import patch
    m4 = AgentHealthMonitor()
    hb = AgentHeartbeat(
        agent_id=agent_id,
        tenant_id="tenant-1",
        agent_type=AITask.PLANNING,
        state=AgentState.IDLE,
        observed_at=datetime.now(timezone.utc)
    )
    await m4.record_heartbeat(hb)
    # Mock make_snapshot to return a snapshot with UNKNOWN status
    fake_snap = AgentHealthSnapshot(
        agent_id=agent_id,
        tenant_id="tenant-1",
        agent_type=AITask.PLANNING,
        state=AgentState.IDLE,
        status=HealthStatus.UNKNOWN,
        last_seen_at=datetime.now(timezone.utc),
        age_seconds=0.0,
        consecutive_failures=0,
        total_heartbeats=1,
        total_successes=1,
        total_failures=0,
        total_timeouts=0
    )
    with patch.object(m4, "_make_snapshot", return_value=fake_snap):
        summary4 = await m4.summarize(tenant_id="tenant-1")
        assert summary4.unknown == 1
        assert summary4.status == HealthStatus.HEALTHY
