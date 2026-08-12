"""
agent_health_monitor.py

In-memory, tenant-safe health monitor for live FieldOps AI agents.
"""

from __future__ import annotations

import asyncio
from collections import deque
import math
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import UUID

import structlog

from app.services.ai.FieldOpsAI.agents.base import AgentState
from app.services.ai.FieldOpsAI.schemas.agent_health import (
    AgentHealthSnapshot,
    AgentHeartbeat,
    HealthStatus,
    HealthSummary,
)
from app.services.ai.FieldOpsAI.schemas.agent_result import AgentResultStatus
from app.services.ai.FieldOpsAI.schemas.ai_task import AITask

_logger = structlog.get_logger("fieldops.ai.agent_health_monitor")


class _AgentHealthRecord:
    """
    Private internal record to store operational counters.
    Does not store BaseAgent or execution context/payloads.
    """
    __slots__ = (
        "last_heartbeat",
        "total_heartbeats",
        "consecutive_failures",
        "total_successes",
        "total_failures",
        "total_timeouts",
        "latency_samples",
    )

    def __init__(self, latency_window_size: int) -> None:
        self.last_heartbeat: AgentHeartbeat | None = None
        self.total_heartbeats: int = 0
        self.consecutive_failures: int = 0
        self.total_successes: int = 0
        self.total_failures: int = 0
        self.total_timeouts: int = 0
        self.latency_samples: deque[float] = deque(maxlen=latency_window_size)


class AgentHealthMonitor:
    """
    Tracks liveness and operational health for live FieldOps AI agents.
    """

    def __init__(
        self,
        *,
        degraded_after_seconds: float = 30.0,
        unhealthy_after_seconds: float = 120.0,
        latency_window_size: int = 20,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if isinstance(degraded_after_seconds, bool) or not isinstance(degraded_after_seconds, (int, float)):
            raise TypeError("degraded_after_seconds must be a float or int.")
        if (isinstance(degraded_after_seconds, float)and not math.isfinite(degraded_after_seconds)):
            raise ValueError("degraded_after_seconds must be finite.")
        if degraded_after_seconds <= 0 or degraded_after_seconds > 3600:
            raise ValueError("degraded_after_seconds must be > 0 and <= 3600.")

        if isinstance(unhealthy_after_seconds, bool) or not isinstance(unhealthy_after_seconds, (int, float)):
            raise TypeError("unhealthy_after_seconds must be a float or int.")
        if (isinstance(unhealthy_after_seconds, float)and not math.isfinite(unhealthy_after_seconds)):
            raise ValueError(
                "unhealthy_after_seconds must be finite."
            )
        if unhealthy_after_seconds <= degraded_after_seconds or unhealthy_after_seconds > 86400:
            raise ValueError("unhealthy_after_seconds must be > degraded_after_seconds and <= 86400.")

        if isinstance(latency_window_size, bool) or not isinstance(latency_window_size, int):
            raise TypeError("latency_window_size must be an int.")
        if latency_window_size < 1 or latency_window_size > 1000:
            raise ValueError("latency_window_size must be between 1 and 1000.")

        if clock is not None and not callable(clock):
            raise TypeError("clock must be callable.")

        self._degraded_after_seconds = float(degraded_after_seconds)
        self._unhealthy_after_seconds = float(unhealthy_after_seconds)
        self._latency_window_size = latency_window_size
        self._clock = clock if clock is not None else lambda: datetime.now(timezone.utc)

        self._records: dict[tuple[str, UUID], _AgentHealthRecord] = {}
        self._lock = asyncio.Lock()

        _logger.debug(
            "agent_health_monitor_created",
            degraded_after_seconds=self._degraded_after_seconds,
            unhealthy_after_seconds=self._unhealthy_after_seconds,
            latency_window_size=self._latency_window_size,
        )

    def _now(self) -> datetime:
        """
        Invoke the external clock and validate that it returns a timezone-aware datetime.
        """
        now = self._clock()
        if not isinstance(now, datetime):
            raise TypeError("Clock must return a datetime instance.")
        if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
            raise ValueError("Clock must return a timezone-aware datetime.")
        return now

    def _normalize_tenant_id(self, tenant_id: Any) -> str:
        """
        Validate and normalize tenant_id consistently.
        """
        if not isinstance(tenant_id, str):
            raise TypeError("tenant_id must be a string.")
        normalized = tenant_id.strip()
        if not normalized:
            raise ValueError("tenant_id must not be blank.")
        if len(normalized) > 50:
            raise ValueError("tenant_id must be at most 50 characters.")
        return normalized

    def _make_snapshot(self, record: _AgentHealthRecord, now: datetime) -> AgentHealthSnapshot:
        """
        Constructs an AgentHealthSnapshot from the internal record (called outside the lock).
        """
        hb = record.last_heartbeat
        if hb is None:
            raise ValueError("Record has no heartbeat.")

        age = (now - hb.observed_at).total_seconds()
        if age < 0:
            age = 0.0

        # Health status rules in order:
        # UNHEALTHY:
        # - Current AgentState is ERROR.
        # - Last result status is FAILED.
        # - Last result status is TIMEOUT.
        # - Heartbeat age is greater than or equal to unhealthy_after_seconds.
        if (
            age >= self._unhealthy_after_seconds
            or hb.state is AgentState.ERROR
            or hb.result_status is AgentResultStatus.FAILED
            or hb.result_status is AgentResultStatus.TIMEOUT
        ):
            status = HealthStatus.UNHEALTHY
        # DEGRADED:
        # - Heartbeat age is greater than or equal to degraded_after_seconds.
        # - consecutive_failures is greater than zero.
        # - Current AgentState is PAUSED.
        # But wait! A recent TERMINATED agent is HEALTHY, not degraded.
        elif hb.state is AgentState.TERMINATED and age < self._degraded_after_seconds:
            status = HealthStatus.HEALTHY
        elif (
            age >= self._degraded_after_seconds
            or record.consecutive_failures > 0
            or hb.state is AgentState.PAUSED
        ):
            status = HealthStatus.DEGRADED
        else:
            status = HealthStatus.HEALTHY

        last_latency = record.latency_samples[-1] if record.latency_samples else None
        avg_latency = (
            sum(record.latency_samples) / len(record.latency_samples)
            if record.latency_samples
            else None
        )

        return AgentHealthSnapshot(
            agent_id=hb.agent_id,
            tenant_id=hb.tenant_id,
            agent_type=hb.agent_type,
            state=hb.state,
            status=status,
            last_seen_at=hb.observed_at,
            age_seconds=age,
            consecutive_failures=record.consecutive_failures,
            total_heartbeats=record.total_heartbeats,
            total_successes=record.total_successes,
            total_failures=record.total_failures,
            total_timeouts=record.total_timeouts,
            last_result_status=hb.result_status,
            last_latency_ms=last_latency,
            average_latency_ms=avg_latency,
            safe_error_code=hb.safe_error_code,
        )

    async def record_heartbeat(
        self,
        heartbeat: AgentHeartbeat,
    ) -> AgentHealthSnapshot:
        if not isinstance(heartbeat, AgentHeartbeat):
            raise TypeError("heartbeat must be an AgentHeartbeat instance.")

        now = self._now()

        operational_hb = heartbeat.model_copy(
            update={"metadata": {}},
            deep=True,
        )

        tenant_id = operational_hb.tenant_id
        agent_id = operational_hb.agent_id
        key = (tenant_id, agent_id)

        async with self._lock:
            record = self._records.get(key)
            if record is None:
                record = _AgentHealthRecord(self._latency_window_size)
                self._records[key] = record

            # Heartbeat ordering and replay protection
            if record.last_heartbeat is not None:
                if operational_hb.observed_at <= record.last_heartbeat.observed_at:
                    # Stale or duplicate replay, ignore completely
                    # But construct snapshot and return it
                    last_hb = record.last_heartbeat
                    last_successes = record.total_successes
                    last_failures = record.total_failures
                    last_timeouts = record.total_timeouts
                    last_consec = record.consecutive_failures
                    last_total = record.total_heartbeats
                    last_samples = list(record.latency_samples)

                    # We copy values so we can return snapshot without holding the lock
                    temp_record = _AgentHealthRecord(self._latency_window_size)
                    temp_record.last_heartbeat = last_hb
                    temp_record.total_successes = last_successes
                    temp_record.total_failures = last_failures
                    temp_record.total_timeouts = last_timeouts
                    temp_record.consecutive_failures = last_consec
                    temp_record.total_heartbeats = last_total
                    temp_record.latency_samples.extend(last_samples)

                    to_snapshot = temp_record
                else:
                    # New heartbeat: update counters
                    record.last_heartbeat = operational_hb
                    record.total_heartbeats += 1

                    if operational_hb.result_status is AgentResultStatus.SUCCESS:
                        record.total_successes += 1
                        record.consecutive_failures = 0
                    elif operational_hb.result_status is AgentResultStatus.FAILED:
                        record.total_failures += 1
                        record.consecutive_failures += 1
                    elif operational_hb.result_status is AgentResultStatus.TIMEOUT:
                        record.total_timeouts += 1
                        record.consecutive_failures += 1

                    if operational_hb.latency_ms is not None:
                        record.latency_samples.append(operational_hb.latency_ms)

                    to_snapshot = record
            else:
                # First heartbeat
                record.last_heartbeat = operational_hb
                record.total_heartbeats += 1

                if operational_hb.result_status is AgentResultStatus.SUCCESS:
                    record.total_successes += 1
                    record.consecutive_failures = 0
                elif operational_hb.result_status is AgentResultStatus.FAILED:
                    record.total_failures += 1
                    record.consecutive_failures += 1
                elif operational_hb.result_status is AgentResultStatus.TIMEOUT:
                    record.total_timeouts += 1
                    record.consecutive_failures += 1

                if operational_hb.latency_ms is not None:
                    record.latency_samples.append(operational_hb.latency_ms)

                to_snapshot = record

            # Copy state for construction outside lock
            last_hb = to_snapshot.last_heartbeat
            total_hb = to_snapshot.total_heartbeats
            consec = to_snapshot.consecutive_failures
            successes = to_snapshot.total_successes
            failures = to_snapshot.total_failures
            timeouts = to_snapshot.total_timeouts
            samples = list(to_snapshot.latency_samples)

        # Outside the lock, construct snapshot
        temp_record = _AgentHealthRecord(self._latency_window_size)
        temp_record.last_heartbeat = last_hb
        temp_record.total_heartbeats = total_hb
        temp_record.consecutive_failures = consec
        temp_record.total_successes = successes
        temp_record.total_failures = failures
        temp_record.total_timeouts = timeouts
        temp_record.latency_samples.extend(samples)

        return self._make_snapshot(temp_record, now)

    async def get_agent_health(
        self,
        *,
        agent_id: UUID,
        tenant_id: str,
    ) -> AgentHealthSnapshot | None:
        if not isinstance(agent_id, UUID):
            raise TypeError("agent_id must be a UUID instance.")
        tenant_id = self._normalize_tenant_id(tenant_id)
        now = self._now()
        key = (tenant_id, agent_id)

        async with self._lock:
            record = self._records.get(key)
            if record is None:
                return None

            last_hb = record.last_heartbeat
            total_hb = record.total_heartbeats
            consec = record.consecutive_failures
            successes = record.total_successes
            failures = record.total_failures
            timeouts = record.total_timeouts
            samples = list(record.latency_samples)

        temp_record = _AgentHealthRecord(self._latency_window_size)
        temp_record.last_heartbeat = last_hb
        temp_record.total_heartbeats = total_hb
        temp_record.consecutive_failures = consec
        temp_record.total_successes = successes
        temp_record.total_failures = failures
        temp_record.total_timeouts = timeouts
        temp_record.latency_samples.extend(samples)

        return self._make_snapshot(temp_record, now)

    async def list_agent_health(
        self,
        *,
        tenant_id: str | None = None,
        agent_type: AITask | None = None,
        status: HealthStatus | None = None,
    ) -> tuple[AgentHealthSnapshot, ...]:
        if tenant_id is not None:
            tenant_id = self._normalize_tenant_id(tenant_id)

        if agent_type is not None and not isinstance(agent_type, AITask):
            raise TypeError("agent_type must be an AITask instance.")

        if status is not None and not isinstance(status, HealthStatus):
            raise TypeError("status must be a HealthStatus instance.")

        now = self._now()
        matched_records = []

        async with self._lock:
            for key, record in self._records.items():
                if record.last_heartbeat is None:
                    continue

                if tenant_id is not None and key[0] != tenant_id:
                    continue

                if agent_type is not None and record.last_heartbeat.agent_type is not agent_type:
                    continue

                # Copy details for dynamic snapshot calculation
                last_hb = record.last_heartbeat
                total_hb = record.total_heartbeats
                consec = record.consecutive_failures
                successes = record.total_successes
                failures = record.total_failures
                timeouts = record.total_timeouts
                samples = list(record.latency_samples)

                matched_records.append((last_hb, total_hb, consec, successes, failures, timeouts, samples))

        snapshots = []
        for last_hb, total_hb, consec, successes, failures, timeouts, samples in matched_records:
            temp_record = _AgentHealthRecord(self._latency_window_size)
            temp_record.last_heartbeat = last_hb
            temp_record.total_heartbeats = total_hb
            temp_record.consecutive_failures = consec
            temp_record.total_successes = successes
            temp_record.total_failures = failures
            temp_record.total_timeouts = timeouts
            temp_record.latency_samples.extend(samples)

            snapshot = self._make_snapshot(temp_record, now)
            if status is None or snapshot.status is status:
                snapshots.append(snapshot)

        # Deterministic list ordering: (tenant_id, agent_type, agent_id)
        snapshots.sort(key=lambda s: (s.tenant_id, s.agent_type.value, str(s.agent_id)))
        return tuple(snapshots)

    async def summarize(
        self,
        *,
        tenant_id: str | None = None,
    ) -> HealthSummary:
        if tenant_id is not None:
            tenant_id = self._normalize_tenant_id(tenant_id)

        now = self._now()
        matched_records = []

        async with self._lock:
            for key, record in self._records.items():
                if record.last_heartbeat is None:
                    continue

                if tenant_id is not None and key[0] != tenant_id:
                    continue

                last_hb = record.last_heartbeat
                total_hb = record.total_heartbeats
                consec = record.consecutive_failures
                successes = record.total_successes
                failures = record.total_failures
                timeouts = record.total_timeouts
                samples = list(record.latency_samples)

                matched_records.append((last_hb, total_hb, consec, successes, failures, timeouts, samples))

        healthy_count = 0
        degraded_count = 0
        unhealthy_count = 0
        unknown_count = 0
        by_agent_type: dict[str, int] = {}

        for last_hb, total_hb, consec, successes, failures, timeouts, samples in matched_records:
            temp_record = _AgentHealthRecord(self._latency_window_size)
            temp_record.last_heartbeat = last_hb
            temp_record.total_heartbeats = total_hb
            temp_record.consecutive_failures = consec
            temp_record.total_successes = successes
            temp_record.total_failures = failures
            temp_record.total_timeouts = timeouts
            temp_record.latency_samples.extend(samples)

            snapshot = self._make_snapshot(temp_record, now)
            if snapshot.status is HealthStatus.HEALTHY:
                healthy_count += 1
            elif snapshot.status is HealthStatus.DEGRADED:
                degraded_count += 1
            elif snapshot.status is HealthStatus.UNHEALTHY:
                unhealthy_count += 1
            elif snapshot.status is HealthStatus.UNKNOWN:
                unknown_count += 1

            t_val = snapshot.agent_type.value
            by_agent_type[t_val] = by_agent_type.get(t_val, 0) + 1

        total_agents = healthy_count + degraded_count + unhealthy_count + unknown_count

        # Dynamic overall health calculation
        if total_agents == 0:
            status = HealthStatus.UNKNOWN
        elif unhealthy_count > 0:
            status = HealthStatus.UNHEALTHY
        elif degraded_count > 0:
            status = HealthStatus.DEGRADED
        else:
            status = HealthStatus.HEALTHY

        return HealthSummary(
            status=status,
            checked_at=now,
            tenant_id=tenant_id,
            total_agents=total_agents,
            healthy=healthy_count,
            degraded=degraded_count,
            unhealthy=unhealthy_count,
            unknown=unknown_count,
            by_agent_type=by_agent_type,
        )

    async def remove_agent(
        self,
        *,
        agent_id: UUID,
        tenant_id: str,
    ) -> bool:
        if not isinstance(agent_id, UUID):
            raise TypeError("agent_id must be a UUID instance.")
        tenant_id = self._normalize_tenant_id(tenant_id)
        key = (tenant_id, agent_id)

        async with self._lock:
            if key in self._records:
                self._records.pop(key)
                return True
            return False

    async def clear_tenant(
        self,
        tenant_id: str,
    ) -> int:
        tenant_id = self._normalize_tenant_id(tenant_id)

        async with self._lock:
            to_remove = [k for k in self._records.keys() if k[0] == tenant_id]
            for k in to_remove:
                self._records.pop(k)
            return len(to_remove)

    async def tracked_count(
        self,
        *,
        tenant_id: str | None = None,
    ) -> int:
        if tenant_id is not None:
            tenant_id = self._normalize_tenant_id(tenant_id)

        async with self._lock:
            if tenant_id is not None:
                return sum(1 for k in self._records.keys() if k[0] == tenant_id)
            return len(self._records)


def create_agent_health_monitor(
    *,
    degraded_after_seconds: float = 30.0,
    unhealthy_after_seconds: float = 120.0,
    latency_window_size: int = 20,
    clock: Callable[[], datetime] | None = None,
) -> AgentHealthMonitor:
    """
    Factory function to create a new AgentHealthMonitor instance.
    """
    return AgentHealthMonitor(
        degraded_after_seconds=degraded_after_seconds,
        unhealthy_after_seconds=unhealthy_after_seconds,
        latency_window_size=latency_window_size,
        clock=clock,
    )
