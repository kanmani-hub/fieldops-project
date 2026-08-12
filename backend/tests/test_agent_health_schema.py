import math
import pytest
from datetime import datetime, timezone
from uuid import uuid4
from pydantic import ValidationError

from app.services.ai.FieldOpsAI.agents.base import AgentState
from app.services.ai.FieldOpsAI.schemas.ai_task import AITask
from app.services.ai.FieldOpsAI.schemas.agent_result import AgentResultStatus
from app.services.ai.FieldOpsAI.schemas.agent_health import (
    AgentHeartbeat,
    AgentHealthSnapshot,
    HealthStatus,
    HealthSummary,
)

# Line 70 (float metadata) and 163 (None metadata)
def test_heartbeat_metadata_float_and_none():
    hb_float = AgentHeartbeat(
        agent_id=uuid4(),
        tenant_id="tenant-1",
        agent_type=AITask.PLANNING,
        state=AgentState.IDLE,
        observed_at=datetime.now(timezone.utc),
        metadata={"cost": 1.23}
    )
    assert hb_float.metadata["cost"] == 1.23

    # Pass metadata=None directly as kwargs
    hb_none = AgentHeartbeat(
        agent_id=uuid4(),
        tenant_id="tenant-1",
        agent_type=AITask.PLANNING,
        state=AgentState.IDLE,
        observed_at=datetime.now(timezone.utc),
        metadata=None  # type: ignore
    )
    assert hb_none.metadata == {}

# Line 131: correlation_id length > 100
def test_heartbeat_correlation_id_too_long():
    with pytest.raises(ValidationError, match="correlation_id must be at most 100 characters"):
        AgentHeartbeat(
            agent_id=uuid4(),
            tenant_id="tenant-1",
            agent_type=AITask.PLANNING,
            state=AgentState.IDLE,
            observed_at=datetime.now(timezone.utc),
            correlation_id="a" * 101
        )

# Line 156: safe_error_code length > 100
def test_heartbeat_safe_error_code_too_long():
    with pytest.raises(ValidationError, match="safe_error_code must be at most 100 characters"):
        AgentHeartbeat(
            agent_id=uuid4(),
            tenant_id="tenant-1",
            agent_type=AITask.PLANNING,
            state=AgentState.IDLE,
            observed_at=datetime.now(timezone.utc),
            safe_error_code="a" * 101
        )

# Line 197 & 202: AgentHealthSnapshot tenant_id validators
def test_health_snapshot_tenant_id_validation():
    now = datetime.now(timezone.utc)
    # Non-string tenant_id
    with pytest.raises(ValidationError, match="tenant_id must be a string"):
        AgentHealthSnapshot(
            agent_id=uuid4(),
            tenant_id=123,  # type: ignore
            agent_type=AITask.PLANNING,
            state=AgentState.IDLE,
            status=HealthStatus.HEALTHY,
            last_seen_at=now,
            age_seconds=0.0,
            consecutive_failures=0,
            total_heartbeats=1,
            total_successes=1,
            total_failures=0,
            total_timeouts=0
        )
    
    # Too long tenant_id
    with pytest.raises(ValidationError, match="tenant_id must be at most 50 characters"):
        AgentHealthSnapshot(
            agent_id=uuid4(),
            tenant_id="a" * 51,
            agent_type=AITask.PLANNING,
            state=AgentState.IDLE,
            status=HealthStatus.HEALTHY,
            last_seen_at=now,
            age_seconds=0.0,
            consecutive_failures=0,
            total_heartbeats=1,
            total_successes=1,
            total_failures=0,
            total_timeouts=0
        )

# Line 218 & 223: AgentHealthSnapshot safe_error_code validators
def test_health_snapshot_safe_error_code_validation():
    now = datetime.now(timezone.utc)
    # Non-string safe_error_code
    with pytest.raises(ValidationError, match="safe_error_code must be a string"):
        AgentHealthSnapshot(
            agent_id=uuid4(),
            tenant_id="tenant-1",
            agent_type=AITask.PLANNING,
            state=AgentState.IDLE,
            status=HealthStatus.HEALTHY,
            last_seen_at=now,
            age_seconds=0.0,
            consecutive_failures=0,
            total_heartbeats=1,
            total_successes=1,
            total_failures=0,
            total_timeouts=0,
            safe_error_code=123  # type: ignore
        )

    # Too long safe_error_code
    with pytest.raises(ValidationError, match="safe_error_code must be at most 100 characters"):
        AgentHealthSnapshot(
            agent_id=uuid4(),
            tenant_id="tenant-1",
            agent_type=AITask.PLANNING,
            state=AgentState.IDLE,
            status=HealthStatus.HEALTHY,
            last_seen_at=now,
            age_seconds=0.0,
            consecutive_failures=0,
            total_heartbeats=1,
            total_successes=1,
            total_failures=0,
            total_timeouts=0,
            safe_error_code="a" * 101
        )

# Line 232 & 234: AgentHealthSnapshot non-finite and negative floats
def test_health_snapshot_floats_validation():
    now = datetime.now(timezone.utc)
    # Non-finite age_seconds
    with pytest.raises(ValidationError):
        AgentHealthSnapshot(
            agent_id=uuid4(),
            tenant_id="tenant-1",
            agent_type=AITask.PLANNING,
            state=AgentState.IDLE,
            status=HealthStatus.HEALTHY,
            last_seen_at=now,
            age_seconds=float("inf"),
            consecutive_failures=0,
            total_heartbeats=1,
            total_successes=1,
            total_failures=0,
            total_timeouts=0
        )

    # Negative age_seconds
    with pytest.raises(ValidationError):
        AgentHealthSnapshot(
            agent_id=uuid4(),
            tenant_id="tenant-1",
            agent_type=AITask.PLANNING,
            state=AgentState.IDLE,
            status=HealthStatus.HEALTHY,
            last_seen_at=now,
            age_seconds=-5.0,
            consecutive_failures=0,
            total_heartbeats=1,
            total_successes=1,
            total_failures=0,
            total_timeouts=0
        )
    with pytest.raises(ValueError, match="Value must be non-negative"):
        AgentHealthSnapshot.validate_finite_non_negative_floats(-5.0)

# Line 273, 276, 278: HealthSummary tenant_id validators
def test_health_summary_tenant_id_validation():
    now = datetime.now(timezone.utc)
    # Non-string tenant_id
    with pytest.raises(ValidationError, match="tenant_id must be a string"):
        HealthSummary(
            status=HealthStatus.HEALTHY,
            checked_at=now,
            tenant_id=123,  # type: ignore
            total_agents=0,
            healthy=0,
            degraded=0,
            unhealthy=0,
            unknown=0,
            by_agent_type={}
        )

    # Blank tenant_id
    with pytest.raises(ValidationError, match="tenant_id must not be blank"):
        HealthSummary(
            status=HealthStatus.HEALTHY,
            checked_at=now,
            tenant_id="   ",
            total_agents=0,
            healthy=0,
            degraded=0,
            unhealthy=0,
            unknown=0,
            by_agent_type={}
        )

    # Too long tenant_id
    with pytest.raises(ValidationError, match="tenant_id must be at most 50 characters"):
        HealthSummary(
            status=HealthStatus.HEALTHY,
            checked_at=now,
            tenant_id="a" * 51,
            total_agents=0,
            healthy=0,
            degraded=0,
            unhealthy=0,
            unknown=0,
            by_agent_type={}
        )

# Line 287 & 289: HealthSummary by_agent_type validators
def test_health_summary_by_agent_type_validation():
    now = datetime.now(timezone.utc)
    # Invalid agent type string
    with pytest.raises(ValidationError, match="Invalid agent type in summary"):
        HealthSummary(
            status=HealthStatus.HEALTHY,
            checked_at=now,
            tenant_id="tenant-1",
            total_agents=1,
            healthy=1,
            degraded=0,
            unhealthy=0,
            unknown=0,
            by_agent_type={"invalid_agent": 1}
        )

    # Negative count
    with pytest.raises(ValidationError, match="Agent count must be non-negative"):
        HealthSummary(
            status=HealthStatus.HEALTHY,
            checked_at=now,
            tenant_id="tenant-1",
            total_agents=0,
            healthy=0,
            degraded=0,
            unhealthy=0,
            unknown=0,
            by_agent_type={"planning": -1}
        )
