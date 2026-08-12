"""
test_provider_health.py

Unit test suite for ProviderHealthMonitor, ProviderHealthConfig, ProviderHealthSnapshot,
and ProviderHealthMetrics (Task 4.4A Corrections).
"""

from __future__ import annotations

import asyncio
import importlib
import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import fakeredis
import pytest
from pydantic import ValidationError

from app.services.ai.FieldOpsAI.providers.base_provider import BaseAIProvider
from app.services.ai.FieldOpsAI.providers.provider_health import (
    ProviderHealthConfig,
    ProviderHealthInfrastructureError,
    ProviderHealthMetrics,
    ProviderHealthMonitor,
    ProviderHealthSnapshot,
)
from app.services.ai.FieldOpsAI.schemas.provider import ProviderHealth


class FakeClock:
    def __init__(self, start_dt: datetime | None = None) -> None:
        if start_dt is None:
            self._dt = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        else:
            self._dt = start_dt

    def __call__(self) -> datetime:
        return self._dt

    def advance(self, seconds: float) -> None:
        self._dt += timedelta(seconds=seconds)


class FakeHealthyProvider(BaseAIProvider):
    def __init__(self, name: str = "groq") -> None:
        self._name = name

    def generate_completion(self, messages, temperature=None, max_tokens=None) -> str:
        return "ok"

    def provider_name(self) -> str:
        return self._name

    def model_name(self) -> str:
        return "fake-model"

    def health_check(self) -> bool:
        return True


class FakeFailingProvider(BaseAIProvider):
    def __init__(self, name: str = "groq", error: Exception | None = None) -> None:
        self._name = name
        self.error = error or Exception("gsk_secret_api_key_should_not_leak_12345")

    def generate_completion(self, messages, temperature=None, max_tokens=None) -> str:
        raise self.error

    def provider_name(self) -> str:
        return self._name

    def model_name(self) -> str:
        return "fake-model"

    def health_check(self) -> bool:
        raise self.error


@pytest.fixture
def fake_redis():
    client = fakeredis.FakeRedis(decode_responses=True)
    yield client
    client.flushall()


@pytest.fixture
def fake_clock():
    return FakeClock()


# 1. Health configuration validation
def test_health_config_validation() -> None:
    config = ProviderHealthConfig()
    assert config.check_interval_seconds == 30
    assert config.recovery_probe_seconds == 300
    assert config.degraded_after_failures == 1
    assert config.unhealthy_after_failures == 3
    assert config.state_ttl_seconds == 900
    assert config.namespace_version == "v1"

    # check_interval_seconds must be > 0
    with pytest.raises(ValidationError):
        ProviderHealthConfig(check_interval_seconds=0)

    # unhealthy_after_failures must be > degraded_after_failures
    with pytest.raises(ValidationError):
        ProviderHealthConfig(degraded_after_failures=3, unhealthy_after_failures=2)

    # state_ttl_seconds must be > recovery_probe_seconds
    with pytest.raises(ValidationError):
        ProviderHealthConfig(recovery_probe_seconds=300, state_ttl_seconds=200)

    # Extra fields forbidden
    with pytest.raises(ValidationError):
        ProviderHealthConfig(invalid_param=123)  # type: ignore[call-arg]


# 2. Provider name normalization
def test_provider_name_normalization(fake_redis, fake_clock) -> None:
    monitor = ProviderHealthMonitor(redis_client=fake_redis, clock=fake_clock)
    provider = FakeHealthyProvider("  Groq  ")
    snap = monitor.check_provider("  Groq  ", provider=provider)
    assert snap.provider_name == "groq"
    restored = monitor.get_snapshot("GROQ")
    assert restored is not None
    assert restored.provider_name == "groq"


# 3. Initial healthy check
def test_initial_healthy_check(fake_redis, fake_clock) -> None:
    monitor = ProviderHealthMonitor(redis_client=fake_redis, clock=fake_clock)
    provider = FakeHealthyProvider("groq")
    snap = monitor.check_provider("groq", provider=provider)
    assert snap.status == ProviderHealth.HEALTHY
    assert snap.consecutive_successes == 1
    assert snap.consecutive_failures == 0
    assert snap.total_checks == 1
    assert snap.total_successes == 1
    assert snap.total_failures == 0
    assert snap.last_healthy_at == fake_clock()


# 4. Initial failed check
def test_initial_failed_check(fake_redis, fake_clock) -> None:
    monitor = ProviderHealthMonitor(redis_client=fake_redis, clock=fake_clock)
    provider = FakeFailingProvider("groq")
    snap = monitor.check_provider("groq", provider=provider)
    assert snap.status == ProviderHealth.DEGRADED
    assert snap.consecutive_failures == 1
    assert snap.total_checks == 1
    assert snap.total_failures == 1
    assert snap.safe_error_code == "PROVIDER_HEALTH_CHECK_FAILED"


# 5. HEALTHY state
def test_healthy_state(fake_redis, fake_clock) -> None:
    monitor = ProviderHealthMonitor(redis_client=fake_redis, clock=fake_clock)
    provider = FakeHealthyProvider("groq")
    monitor.check_provider("groq", provider=provider)
    snap = monitor.check_provider("groq", provider=provider)
    assert snap.status == ProviderHealth.HEALTHY
    assert snap.consecutive_successes == 2
    assert snap.total_successes == 2


# 6. DEGRADED after configured failures
def test_degraded_after_configured_failures(fake_redis, fake_clock) -> None:
    config = ProviderHealthConfig(degraded_after_failures=1, unhealthy_after_failures=3)
    monitor = ProviderHealthMonitor(redis_client=fake_redis, config=config, clock=fake_clock)
    provider = FakeFailingProvider("groq")
    snap = monitor.check_provider("groq", provider=provider)
    assert snap.status == ProviderHealth.DEGRADED


# 7. UNHEALTHY after configured failures
def test_unhealthy_after_configured_failures(fake_redis, fake_clock) -> None:
    config = ProviderHealthConfig(degraded_after_failures=1, unhealthy_after_failures=3)
    monitor = ProviderHealthMonitor(redis_client=fake_redis, config=config, clock=fake_clock)
    provider = FakeFailingProvider("groq")
    monitor.check_provider("groq", provider=provider)
    monitor.check_provider("groq", provider=provider)
    snap = monitor.check_provider("groq", provider=provider)
    assert snap.status == ProviderHealth.UNHEALTHY
    assert snap.consecutive_failures == 3
    assert snap.next_recovery_probe_at == fake_clock() + timedelta(seconds=300)


# 8. Success resets consecutive failures
def test_success_resets_consecutive_failures(fake_redis, fake_clock) -> None:
    monitor = ProviderHealthMonitor(redis_client=fake_redis, clock=fake_clock)
    failing = FakeFailingProvider("groq")
    healthy = FakeHealthyProvider("groq")

    monitor.check_provider("groq", provider=failing)
    monitor.check_provider("groq", provider=failing)
    snap = monitor.check_provider("groq", provider=healthy)

    assert snap.status == ProviderHealth.HEALTHY
    assert snap.consecutive_failures == 0
    assert snap.consecutive_successes == 1


# 9. Recovery transition to HEALTHY
def test_recovery_transition_to_healthy(fake_redis, fake_clock) -> None:
    monitor = ProviderHealthMonitor(redis_client=fake_redis, clock=fake_clock)
    failing = FakeFailingProvider("groq")
    healthy = FakeHealthyProvider("groq")

    # Trip to UNHEALTHY
    for _ in range(3):
        monitor.check_provider("groq", provider=failing)

    fake_clock.advance(301)
    snap = monitor.check_provider("groq", provider=healthy)
    assert snap.status == ProviderHealth.HEALTHY
    assert snap.next_recovery_probe_at is None


# 10. Total recovery count
def test_total_recovery_count(fake_redis, fake_clock) -> None:
    monitor = ProviderHealthMonitor(redis_client=fake_redis, clock=fake_clock)
    failing = FakeFailingProvider("groq")
    healthy = FakeHealthyProvider("groq")

    monitor.check_provider("groq", provider=failing)  # DEGRADED
    fake_clock.advance(10)
    snap = monitor.check_provider("groq", provider=healthy)  # RECOVERED

    assert snap.total_recoveries == 1


# 11. Five-minute recovery time
def test_five_minute_recovery_time(fake_redis, fake_clock) -> None:
    monitor = ProviderHealthMonitor(redis_client=fake_redis, clock=fake_clock)
    failing = FakeFailingProvider("groq")

    for _ in range(3):
        monitor.check_provider("groq", provider=failing)

    snap = monitor.get_snapshot("groq")
    assert snap is not None
    assert snap.next_recovery_probe_at == fake_clock() + timedelta(seconds=300)


# 12. Unhealthy provider not probed early
def test_unhealthy_provider_not_probed_early(fake_redis, fake_clock) -> None:
    monitor = ProviderHealthMonitor(redis_client=fake_redis, clock=fake_clock)
    failing = FakeFailingProvider("groq")

    for _ in range(3):
        monitor.check_provider("groq", provider=failing)

    fake_clock.advance(100)  # Only 100s passed (cooldown is 300s)
    assert monitor.should_probe("groq") is False

    # Calling check_provider early returns current snapshot without checking
    healthy = FakeHealthyProvider("groq")
    snap = monitor.check_provider("groq", provider=healthy)
    assert snap.status == ProviderHealth.UNHEALTHY
    assert snap.total_checks == 3


# 13. Unhealthy provider probed when due
def test_unhealthy_provider_probed_when_due(fake_redis, fake_clock) -> None:
    monitor = ProviderHealthMonitor(redis_client=fake_redis, clock=fake_clock)
    failing = FakeFailingProvider("groq")

    for _ in range(3):
        monitor.check_provider("groq", provider=failing)

    fake_clock.advance(300)
    assert monitor.should_probe("groq") is True

    healthy = FakeHealthyProvider("groq")
    snap = monitor.check_provider("groq", provider=healthy)
    assert snap.status == ProviderHealth.HEALTHY


# 14. Failed recovery schedules next probe
def test_failed_recovery_schedules_next_probe(fake_redis, fake_clock) -> None:
    monitor = ProviderHealthMonitor(redis_client=fake_redis, clock=fake_clock)
    failing = FakeFailingProvider("groq")

    for _ in range(3):
        monitor.check_provider("groq", provider=failing)

    fake_clock.advance(300)
    snap = monitor.check_provider("groq", provider=failing)
    assert snap.status == ProviderHealth.UNHEALTHY
    assert snap.next_recovery_probe_at == fake_clock() + timedelta(seconds=300)


# 15. Redis snapshot persisted
def test_redis_snapshot_persisted(fake_redis, fake_clock) -> None:
    monitor = ProviderHealthMonitor(redis_client=fake_redis, clock=fake_clock)
    provider = FakeHealthyProvider("groq")
    monitor.check_provider("groq", provider=provider)

    key = monitor._get_redis_key("groq")
    assert fake_redis.exists(key) == 1


# 16. Redis TTL applied
def test_redis_ttl_applied(fake_redis, fake_clock) -> None:
    config = ProviderHealthConfig(state_ttl_seconds=900)
    monitor = ProviderHealthMonitor(redis_client=fake_redis, config=config, clock=fake_clock)
    provider = FakeHealthyProvider("groq")
    monitor.check_provider("groq", provider=provider)

    key = monitor._get_redis_key("groq")
    ttl = fake_redis.ttl(key)
    assert 0 < ttl <= 900


# 17. Snapshot restored from Redis
def test_snapshot_restored_from_redis(fake_redis, fake_clock) -> None:
    monitor = ProviderHealthMonitor(redis_client=fake_redis, clock=fake_clock)
    provider = FakeHealthyProvider("groq")
    original = monitor.check_provider("groq", provider=provider)

    restored = monitor.get_snapshot("groq")
    assert restored is not None
    assert restored.provider_name == original.provider_name
    assert restored.status == original.status
    assert restored.checked_at == original.checked_at


# 18. Malformed Redis snapshot fails closed
def test_malformed_redis_snapshot_fails_closed(fake_redis, fake_clock) -> None:
    monitor = ProviderHealthMonitor(redis_client=fake_redis, clock=fake_clock)
    key = monitor._get_redis_key("groq")
    fake_redis.set(key, "{corrupted_json: true")

    with pytest.raises(ProviderHealthInfrastructureError):
        monitor.get_snapshot("groq")


# 19. Redis read failure
def test_redis_read_failure(fake_redis, fake_clock) -> None:
    monitor = ProviderHealthMonitor(redis_client=fake_redis, clock=fake_clock)
    with patch.object(fake_redis, "get", side_effect=Exception("Redis connection refused")):
        with pytest.raises(ProviderHealthInfrastructureError):
            monitor.get_snapshot("groq")


# 20. Redis write failure
def test_redis_write_failure(fake_redis, fake_clock) -> None:
    monitor = ProviderHealthMonitor(redis_client=fake_redis, clock=fake_clock)
    provider = FakeHealthyProvider("groq")
    with patch.object(fake_redis, "setex", side_effect=Exception("Redis write timeout")):
        with pytest.raises(ProviderHealthInfrastructureError):
            monitor.check_provider("groq", provider=provider)


# 21. Raw exception absent from Redis
def test_raw_exception_absent_from_redis(fake_redis, fake_clock) -> None:
    monitor = ProviderHealthMonitor(redis_client=fake_redis, clock=fake_clock)
    provider = FakeFailingProvider("groq", error=Exception("SECRET_API_KEY_gsk_99999"))
    monitor.check_provider("groq", provider=provider)

    key = monitor._get_redis_key("groq")
    raw_payload = fake_redis.get(key)
    assert "SECRET_API_KEY" not in raw_payload
    assert "gsk_99999" not in raw_payload


# 22. Raw exception absent from logs
def test_raw_exception_absent_from_logs(fake_redis, fake_clock, caplog) -> None:
    monitor = ProviderHealthMonitor(redis_client=fake_redis, clock=fake_clock)
    provider = FakeFailingProvider("groq", error=Exception("SECRET_API_KEY_gsk_88888"))

    with caplog.at_level(logging.INFO):
        monitor.check_provider("groq", provider=provider)

    log_text = caplog.text
    assert "SECRET_API_KEY" not in log_text
    assert "gsk_88888" not in log_text


# 23. Metrics counts accurate (Correction 3 - Per-provider metrics)
def test_metrics_counts_accurate(fake_redis, fake_clock) -> None:
    monitor = ProviderHealthMonitor(redis_client=fake_redis, clock=fake_clock)
    healthy = FakeHealthyProvider("groq")
    failing = FakeFailingProvider("groq")

    # 1. Healthy check -> 1 healthy transition
    monitor.check_provider("groq", provider=healthy)
    # 2. Failing check -> 1 degraded transition
    monitor.check_provider("groq", provider=failing)
    # 3. Failing check -> 0 transition (remains degraded)
    monitor.check_provider("groq", provider=failing)
    # 4. Failing check -> 1 unhealthy transition
    monitor.check_provider("groq", provider=failing)
    # 5. Advance clock and recover -> 1 recovery, 1 healthy transition
    fake_clock.advance(301)
    monitor.check_provider("groq", provider=healthy)

    metrics = monitor.get_metrics("groq")
    assert metrics.total_checks == 5
    assert metrics.successful_checks == 2
    assert metrics.failed_checks == 3
    assert metrics.healthy_transitions == 2
    assert metrics.degraded_transitions == 1
    assert metrics.unhealthy_transitions == 1
    assert metrics.recoveries == 1
    assert metrics.average_latency >= 0.0

    # Unknown provider returns zero metrics
    unknown_metrics = monitor.get_metrics("unknown_provider")
    assert unknown_metrics.total_checks == 0


# 24. Latency finite and non-negative
def test_latency_finite_and_non_negative(fake_redis, fake_clock) -> None:
    monitor = ProviderHealthMonitor(redis_client=fake_redis, clock=fake_clock)
    provider = FakeHealthyProvider("groq")
    snap = monitor.check_provider("groq", provider=provider)

    assert isinstance(snap.latency_ms, float)
    assert snap.latency_ms >= 0.0


# 25. check_registered_providers uses ProviderFactory
def test_check_registered_providers_uses_provider_factory(fake_redis, fake_clock) -> None:
    mock_factory = MagicMock()
    mock_factory.registered_names.return_value = ["groq"]
    mock_factory.create_provider.return_value = FakeHealthyProvider("groq")

    monitor = ProviderHealthMonitor(
        redis_client=fake_redis, clock=fake_clock, provider_factory=mock_factory
    )
    snapshots = monitor.check_registered_providers()

    assert len(snapshots) == 1
    assert snapshots[0].provider_name == "groq"
    assert snapshots[0].status == ProviderHealth.HEALTHY


# 26. One failed provider does not stop checking the others
def test_one_failed_provider_does_not_stop_checking_others(fake_redis, fake_clock) -> None:
    class MultiFactory:
        @classmethod
        def registered_names(cls):
            return ["groq", "failing_p", "openai"]

        @classmethod
        def create_provider(cls, name):
            if name == "groq":
                return FakeHealthyProvider("groq")
            if name == "failing_p":
                return FakeFailingProvider("failing_p")
            return FakeHealthyProvider("openai")

    monitor = ProviderHealthMonitor(
        redis_client=fake_redis, clock=fake_clock, provider_factory=MultiFactory
    )
    snapshots = monitor.check_registered_providers()

    assert len(snapshots) == 3
    names = [s.provider_name for s in snapshots]
    assert "groq" in names
    assert "failing_p" in names
    assert "openai" in names


# CORRECTION 1: Honor config.enabled
def test_honor_config_disabled(fake_redis, fake_clock) -> None:
    config = ProviderHealthConfig(enabled=False)
    mock_factory = MagicMock()

    monitor = ProviderHealthMonitor(
        redis_client=fake_redis, config=config, clock=fake_clock, provider_factory=mock_factory
    )

    # check_registered_providers returns [] and creates no providers
    assert monitor.check_registered_providers() == []
    mock_factory.create_provider.assert_not_called()

    # list_snapshots returns snapshots if called or empty if no registered names
    # Manual check_provider still works
    healthy = FakeHealthyProvider("groq")
    snap = monitor.check_provider("groq", provider=healthy)
    assert snap.status == ProviderHealth.HEALTHY


# CORRECTION 2: Infrastructure Error Propagation
def test_infrastructure_error_propagation(fake_redis, fake_clock) -> None:
    monitor = ProviderHealthMonitor(redis_client=fake_redis, clock=fake_clock)

    # 1. Redis failure during check_registered_providers raises ProviderHealthInfrastructureError
    with patch.object(fake_redis, "get", side_effect=Exception("Redis dead")):
        with pytest.raises(ProviderHealthInfrastructureError):
            monitor.check_registered_providers()

    # 2. Redis failure during list_snapshots raises ProviderHealthInfrastructureError
    mock_factory = MagicMock()
    mock_factory.registered_names.return_value = ["groq"]
    monitor2 = ProviderHealthMonitor(
        redis_client=fake_redis, clock=fake_clock, provider_factory=mock_factory
    )
    with patch.object(fake_redis, "get", side_effect=Exception("Redis dead")):
        with pytest.raises(ProviderHealthInfrastructureError):
            monitor2.list_snapshots()

    # 3. registered_names failure raises ProviderHealthInfrastructureError
    failing_factory = MagicMock()
    failing_factory.registered_names.side_effect = Exception("Registry error")
    monitor3 = ProviderHealthMonitor(
        redis_client=fake_redis, clock=fake_clock, provider_factory=failing_factory
    )
    with pytest.raises(ProviderHealthInfrastructureError, match="Provider registry lookup failed."):
        monitor3.check_registered_providers()


# CORRECTION 4: Strict UTC Timestamps
def test_strict_utc_timestamps(fake_redis, fake_clock) -> None:
    # 1. Valid UTC datetime passes
    valid_dt = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    snap = ProviderHealthSnapshot(
        provider_name="groq",
        status=ProviderHealth.HEALTHY,
        checked_at=valid_dt,
    )
    assert snap.checked_at == valid_dt

    # 2. Naive datetime raises ValidationError
    naive_dt = datetime(2026, 1, 1, 12, 0, 0)
    with pytest.raises(ValidationError):
        ProviderHealthSnapshot(
            provider_name="groq",
            status=ProviderHealth.HEALTHY,
            checked_at=naive_dt,
        )

    # 3. Non-UTC timezone (e.g. UTC+5) raises ValidationError
    non_utc_dt = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone(timedelta(hours=5)))
    with pytest.raises(ValidationError):
        ProviderHealthSnapshot(
            provider_name="groq",
            status=ProviderHealth.HEALTHY,
            checked_at=non_utc_dt,
        )

    # 4. Metrics last_checked_time strict UTC
    with pytest.raises(ValidationError):
        ProviderHealthMetrics(last_checked_time=naive_dt)


# CORRECTION 5: Safe Error Code Validation
def test_safe_error_code_validation() -> None:
    dt = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    # Valid code
    snap = ProviderHealthSnapshot(
        provider_name="groq",
        status=ProviderHealth.HEALTHY,
        checked_at=dt,
        safe_error_code="PROVIDER_HEALTH_CHECK_FAILED",
    )
    assert snap.safe_error_code == "PROVIDER_HEALTH_CHECK_FAILED"

    # Lowercase, space, punctuation or raw exception text raises ValidationError
    with pytest.raises(ValidationError):
        ProviderHealthSnapshot(
            provider_name="groq",
            status=ProviderHealth.HEALTHY,
            checked_at=dt,
            safe_error_code="invalid error code!",
        )

    with pytest.raises(ValidationError):
        ProviderHealthSnapshot(
            provider_name="groq",
            status=ProviderHealth.HEALTHY,
            checked_at=dt,
            safe_error_code="Exception: secret_key",
        )


# CORRECTION 6: Alert Only on Meaningful Transitions
def test_alert_only_on_meaningful_transitions(fake_redis, fake_clock) -> None:
    alert_mock = MagicMock()
    monitor = ProviderHealthMonitor(
        redis_client=fake_redis, clock=fake_clock, alert_callback=alert_mock
    )
    healthy = FakeHealthyProvider("groq")
    failing = FakeFailingProvider("groq")

    # 1. Initial HEALTHY check -> NO alert (no transition from DEGRADED/UNHEALTHY)
    monitor.check_provider("groq", provider=healthy)
    alert_mock.assert_not_called()

    # 2. Transition to DEGRADED -> Alert #1
    monitor.check_provider("groq", provider=failing)
    assert alert_mock.call_count == 1
    assert alert_mock.call_args[0][0].status == ProviderHealth.DEGRADED

    # 3. Repeated DEGRADED check -> NO new alert
    monitor.check_provider("groq", provider=failing)
    assert alert_mock.call_count == 1

    # 4. Transition to UNHEALTHY -> Alert #2
    monitor.check_provider("groq", provider=failing)
    assert alert_mock.call_count == 2
    assert alert_mock.call_args[0][0].status == ProviderHealth.UNHEALTHY

    # 5. Advance clock and recover -> Alert #3
    fake_clock.advance(301)
    monitor.check_provider("groq", provider=healthy)
    assert alert_mock.call_count == 3
    assert alert_mock.call_args[0][0].status == ProviderHealth.HEALTHY

    # 6. Callback exception does not crash health check
    bad_alert = MagicMock(side_effect=Exception("Alert crash"))
    monitor_bad = ProviderHealthMonitor(
        redis_client=fake_redis, clock=fake_clock, alert_callback=bad_alert
    )
    snap = monitor_bad.check_provider("groq", provider=failing)
    assert snap.status == ProviderHealth.DEGRADED


# CORRECTION 7: AnyIO Async Tests
@pytest.mark.anyio
async def test_start_is_idempotent(fake_redis, fake_clock) -> None:
    monitor = ProviderHealthMonitor(redis_client=fake_redis, clock=fake_clock)
    await monitor.start()
    task1 = monitor._monitor_task
    await monitor.start()
    task2 = monitor._monitor_task
    assert task1 is task2
    await monitor.stop()


@pytest.mark.anyio
async def test_stop_is_idempotent(fake_redis, fake_clock) -> None:
    monitor = ProviderHealthMonitor(redis_client=fake_redis, clock=fake_clock)
    await monitor.start()
    await monitor.stop()
    assert monitor._running is False
    await monitor.stop()


@pytest.mark.anyio
async def test_run_once_performs_one_cycle(fake_redis, fake_clock) -> None:
    mock_factory = MagicMock()
    mock_factory.registered_names.return_value = ["groq"]
    mock_factory.create_provider.return_value = FakeHealthyProvider("groq")

    monitor = ProviderHealthMonitor(
        redis_client=fake_redis, clock=fake_clock, provider_factory=mock_factory
    )
    snapshots = await monitor.run_once()

    assert len(snapshots) == 1
    assert snapshots[0].provider_name == "groq"


@pytest.mark.anyio
async def test_disabled_start_does_not_create_task(fake_redis, fake_clock) -> None:
    config = ProviderHealthConfig(enabled=False)
    monitor = ProviderHealthMonitor(redis_client=fake_redis, config=config, clock=fake_clock)
    await monitor.start()
    assert monitor._monitor_task is None
    assert monitor._running is False


# CORRECTION 8: Strong Import Side-Effect Test
def test_no_background_task_created_on_import_or_reload() -> None:
    with patch("asyncio.create_task") as mock_create_task:
        import app.services.ai.FieldOpsAI.providers.provider_health as ph
        importlib.reload(ph)
        mock_create_task.assert_not_called()
