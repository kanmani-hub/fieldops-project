"""
test_circuit_breaker.py

Unit test suite for CircuitBreaker, CircuitBreakerConfig, CircuitPermit, and AIOrchestrator integration.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock
import pytest
import fakeredis
from pydantic import ValidationError

from app.services.ai.FieldOpsAI.config.config_loader import ConfigLoader
from app.services.ai.FieldOpsAI.providers.base_provider import (
    ProviderConfigurationError,
    ProviderExecutionError,
)
from app.services.ai.FieldOpsAI.providers.budget import (
    SyncTokenBudgetManager,
    TokenBudgetConfig,
    BudgetExceededError,
)
from app.services.ai.FieldOpsAI.runtime.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerInfrastructureError,
    CircuitError,
    CircuitOpenError,
    CircuitPermit,
    CircuitState,
)
from app.services.ai.FieldOpsAI.runtime.orchestrator import AIOrchestrator
from app.services.ai.FieldOpsAI.schemas.ai_task import AITask


class FakeClock:
    def __init__(self, start_time: float = 1000.0) -> None:
        self._time = start_time

    def __call__(self) -> float:
        return self._time

    def advance(self, seconds: float) -> None:
        self._time += seconds


@pytest.fixture
def fake_redis():
    client = fakeredis.FakeRedis(decode_responses=True)
    yield client
    client.flushall()


@pytest.fixture
def fake_clock():
    return FakeClock(1000.0)


# 1. Configuration Validation & YAML Loading

def test_circuit_breaker_config_validation() -> None:
    with pytest.raises(ValidationError):
        CircuitBreakerConfig(   
            half_open_probe_ttl_seconds=0
        )

    # Valid config
    config = CircuitBreakerConfig(
        enabled=True,
        failure_threshold=5,
        failure_window_seconds=60,
        open_cooldown_seconds=300,
        half_open_success_threshold=3,
        half_open_max_concurrent_probes=1,
        half_open_probe_ttl_seconds=180,
    )
    assert config.half_open_probe_ttl_seconds == 180

    # Invalid thresholds
    with pytest.raises(ValidationError):
        CircuitBreakerConfig(failure_threshold=0)

    with pytest.raises(ValidationError):
        CircuitBreakerConfig(failure_window_seconds=-10)

    # Probes other than 1 rejected
    with pytest.raises(ValidationError, match="half_open_max_concurrent_probes must be exactly 1"):
        CircuitBreakerConfig(half_open_max_concurrent_probes=2)

    # Extra fields forbidden
    with pytest.raises(ValidationError):
        CircuitBreakerConfig(unknown_field=123)  # type: ignore[call-arg]


def test_config_loader_provider_circuit_breaker() -> None:
    loader = ConfigLoader()
    cfg_dict = loader.provider_circuit_breaker
    assert isinstance(cfg_dict, dict)
    assert cfg_dict.get("enabled") is True
    assert cfg_dict.get("failure_threshold") == 5

    # Defensive copy check
    cfg_dict["failure_threshold"] = 999
    assert loader.provider_circuit_breaker.get("failure_threshold") == 5


# 2. CircuitPermit & State Machine Transitions

def test_check_permission_returns_circuit_permit(fake_redis, fake_clock) -> None:
    cb = CircuitBreaker(fake_redis, clock=fake_clock)
    permit = cb.check_permission("groq")
    assert isinstance(permit, CircuitPermit)
    assert isinstance(permit.provider_scope, str)
    assert permit.is_half_open_probe is False
    assert permit.probe_token is None


def test_default_closed_state(fake_redis, fake_clock) -> None:
    cb = CircuitBreaker(fake_redis, clock=fake_clock)
    cb.check_permission("groq")
    snap = cb.snapshot("groq")
    assert snap.state == CircuitState.CLOSED
    assert snap.failure_count == 0


def test_fewer_than_threshold_failures_remains_closed(fake_redis, fake_clock) -> None:
    cb = CircuitBreaker(fake_redis, clock=fake_clock)
    permit = cb.check_permission("groq")
    err = ProviderExecutionError("500 Error", status_code=500, is_retryable=True)

    for _ in range(4):
        cb.record_failure(permit, err)

    cb.check_permission("groq")
    snap = cb.snapshot("groq")
    assert snap.state == CircuitState.CLOSED
    assert snap.failure_count == 4


def test_five_failures_within_window_opens_circuit(fake_redis, fake_clock) -> None:
    cb = CircuitBreaker(fake_redis, clock=fake_clock)
    permit = cb.check_permission("groq")
    err = ProviderExecutionError("503 Unavailable", status_code=503, is_retryable=True)

    for _ in range(5):
        cb.record_failure(permit, err)

    with pytest.raises(CircuitOpenError, match="AI circuit breaker is OPEN"):
        cb.check_permission("groq")

    snap = cb.snapshot("groq")
    assert snap.state == CircuitState.OPEN


def test_failures_outside_window_are_pruned(fake_redis, fake_clock) -> None:
    cb = CircuitBreaker(fake_redis, clock=fake_clock)
    permit = cb.check_permission("groq")
    err = ProviderExecutionError("Timeout", is_retryable=True)

    # 4 failures at t=1000
    for _ in range(4):
        cb.record_failure(permit, err)

    # Advance clock by 65s (window is 60s)
    fake_clock.advance(65.0)

    # 1 failure at t=1065
    cb.record_failure(permit, err)

    cb.check_permission("groq")
    snap = cb.snapshot("groq")
    assert snap.state == CircuitState.CLOSED
    assert snap.failure_count == 1


def test_non_retryable_failures_do_not_count(fake_redis, fake_clock) -> None:
    cb = CircuitBreaker(fake_redis, clock=fake_clock)
    permit = cb.check_permission("groq")
    non_retryable_err = ProviderConfigurationError("Invalid API Key")

    for _ in range(10):
        cb.record_failure(permit, non_retryable_err)

    cb.check_permission("groq")
    snap = cb.snapshot("groq")
    assert snap.state == CircuitState.CLOSED
    assert snap.failure_count == 0


def test_cooldown_transitions_open_to_half_open(fake_redis, fake_clock) -> None:
    cb = CircuitBreaker(fake_redis, clock=fake_clock)
    permit = cb.check_permission("groq")
    err = ProviderExecutionError("500 Internal", status_code=500, is_retryable=True)

    for _ in range(5):
        cb.record_failure(permit, err)

    # Rejects during cooldown
    fake_clock.advance(200.0)
    with pytest.raises(CircuitOpenError):
        cb.check_permission("groq")

    # Cooldown expires at 300s -> t=1305
    fake_clock.advance(105.0)

    # Next check transitions to HALF_OPEN and returns probe permit
    probe_permit = cb.check_permission("groq")
    assert probe_permit.is_half_open_probe is True
    assert isinstance(probe_permit.probe_token, str)
    snap = cb.snapshot("groq")
    assert snap.state == CircuitState.HALF_OPEN


def test_only_one_concurrent_half_open_probe(fake_redis, fake_clock) -> None:
    cb = CircuitBreaker(fake_redis, clock=fake_clock)
    permit = cb.check_permission("groq")
    err = ProviderExecutionError("500 Internal", status_code=500, is_retryable=True)

    for _ in range(5):
        cb.record_failure(permit, err)

    fake_clock.advance(305.0)

    # First probe acquires lock
    p1 = cb.check_permission("groq")
    assert p1.is_half_open_probe is True

    # Second concurrent probe is rejected
    with pytest.raises(CircuitOpenError, match="probe is in progress"):
        cb.check_permission("groq")


def test_atomic_compare_and_delete_probe_lock(fake_redis, fake_clock) -> None:
    cb = CircuitBreaker(fake_redis, clock=fake_clock)
    permit = cb.check_permission("groq")
    err = ProviderExecutionError("500 Internal", status_code=500, is_retryable=True)
    for _ in range(5):
        cb.record_failure(permit, err)

    fake_clock.advance(305.0)
    p1 = cb.check_permission("groq")

    # Construct wrong permit matching same scope but different token
    wrong_permit = CircuitPermit(provider_scope=p1.provider_scope, is_half_open_probe=True, probe_token="wrongtoken123")

    # Attempting to release with wrong permit does NOT delete lock
    cb.release_probe_lock(wrong_permit)

    # Concurrent probe is still rejected (lock remained active)
    with pytest.raises(CircuitOpenError):
        cb.check_permission("groq")

    # Releasing with correct permit deletes lock
    cb.release_probe_lock(p1)

    # Now a new probe can be acquired
    p2 = cb.check_permission("groq")
    assert p2.is_half_open_probe is True


def test_non_retryable_probe_failure_releases_lock_without_reopening_circuit(fake_redis, fake_clock) -> None:
    cb = CircuitBreaker(fake_redis, clock=fake_clock)
    permit = cb.check_permission("groq")
    err = ProviderExecutionError("500 Internal", status_code=500, is_retryable=True)
    for _ in range(5):
        cb.record_failure(permit, err)

    fake_clock.advance(305.0)
    p1 = cb.check_permission("groq")

    non_retryable = ProviderConfigurationError("Invalid Key")
    # Non-retryable failure in HALF_OPEN releases lock but does NOT reopen circuit (stays HALF_OPEN)
    cb.record_failure(p1, non_retryable)

    snap = cb.snapshot("groq")
    assert snap.state == CircuitState.HALF_OPEN

    # New probe can acquire lock
    p2 = cb.check_permission("groq")
    assert p2.is_half_open_probe is True


def test_retryable_probe_failure_reopens_circuit(fake_redis, fake_clock) -> None:
    cb = CircuitBreaker(fake_redis, clock=fake_clock)
    permit = cb.check_permission("groq")
    err = ProviderExecutionError("500 Internal", status_code=500, is_retryable=True)
    for _ in range(5):
        cb.record_failure(permit, err)

    fake_clock.advance(305.0)
    p1 = cb.check_permission("groq")

    # Retryable failure in HALF_OPEN releases lock AND reopens circuit to OPEN
    cb.record_failure(p1, err)

    snap = cb.snapshot("groq")
    assert snap.state == CircuitState.OPEN


def test_three_consecutive_successes_close_circuit(fake_redis, fake_clock) -> None:
    cb = CircuitBreaker(fake_redis, clock=fake_clock)
    permit = cb.check_permission("groq")
    err = ProviderExecutionError("500 Internal", status_code=500, is_retryable=True)

    for _ in range(5):
        cb.record_failure(permit, err)

    fake_clock.advance(305.0)

    # Probe 1
    p1 = cb.check_permission("groq")
    cb.record_success(p1)
    assert cb.snapshot("groq").consecutive_successes == 1

    # Probe 2
    p2 = cb.check_permission("groq")
    cb.record_success(p2)
    assert cb.snapshot("groq").consecutive_successes == 2

    # Probe 3 -> Closes circuit
    p3 = cb.check_permission("groq")
    cb.record_success(p3)

    snap = cb.snapshot("groq")
    assert snap.state == CircuitState.CLOSED
    assert snap.consecutive_successes == 0


def test_redis_key_privacy(fake_redis, fake_clock) -> None:
    cb = CircuitBreaker(fake_redis, clock=fake_clock)
    permit = cb.check_permission("groq")
    err = ProviderExecutionError("500 Internal", status_code=500, is_retryable=True)
    cb.record_failure(permit, err)

    keys = fake_redis.keys("*")
    assert len(keys) > 0
    for key in keys:
        assert "groq" not in key
        assert "fieldops:circuit:v1:" in key


def test_provider_isolation_and_no_tenant_splitting(fake_redis, fake_clock) -> None:
    cb = CircuitBreaker(fake_redis, clock=fake_clock)
    permit = cb.check_permission("groq")
    err = ProviderExecutionError("500 Internal", status_code=500, is_retryable=True)

    # Trip groq circuit
    for _ in range(5):
        cb.record_failure(permit, err)

    # Groq is OPEN
    with pytest.raises(CircuitOpenError):
        cb.check_permission("groq")

    # OpenAI remains CLOSED
    cb.check_permission("openai")
    assert cb.snapshot("openai").state == CircuitState.CLOSED


# 3. AIOrchestrator Integration Tests

def test_circuit_open_error_reaches_caller_unchanged(fake_redis) -> None:
    budget_manager = SyncTokenBudgetManager(fake_redis, TokenBudgetConfig(atomic_strategy="transaction"))
    cb = CircuitBreaker(fake_redis)
    permit = cb.check_permission("groq")
    err = ProviderExecutionError("500 Error", status_code=500, is_retryable=True)
    for _ in range(5):
        cb.record_failure(permit, err)

    mock_client = MagicMock()

    orchestrator = AIOrchestrator(
        client=mock_client,
        budget_manager=budget_manager,
        circuit_breaker=cb,
    )
    orchestrator._load_task_prompt = MagicMock(return_value="Prompt")

    # CircuitOpenError propagates unchanged to caller
    with pytest.raises(CircuitOpenError):
        orchestrator.execute(
            task=AITask.SENTIMENT,
            context={"tenant_id": "tenant-1"},
        )


def test_circuit_open_causes_no_budget_reservation_or_provider_call(fake_redis) -> None:
    budget_manager = SyncTokenBudgetManager(fake_redis, TokenBudgetConfig(atomic_strategy="transaction"))
    budget_manager.reserve = MagicMock()

    mock_client = MagicMock()
    mock_client.generate = MagicMock()

    cb = CircuitBreaker(fake_redis)
    permit = cb.check_permission("groq")
    err = ProviderExecutionError("500 Error", status_code=500, is_retryable=True)
    for _ in range(5):
        cb.record_failure(permit, err)

    orchestrator = AIOrchestrator(
        client=mock_client,
        budget_manager=budget_manager,
        circuit_breaker=cb,
    )
    orchestrator._load_task_prompt = MagicMock(return_value="Prompt")

    with pytest.raises(CircuitOpenError):
        orchestrator.execute(
            task=AITask.SENTIMENT,
            context={"tenant_id": "tenant-1"},
        )

    # Neither budget reservation nor provider call took place
    budget_manager.reserve.assert_not_called()
    mock_client.generate.assert_not_called()


def test_budget_failure_releases_half_open_probe(fake_redis, fake_clock) -> None:
    budget_config = TokenBudgetConfig(daily_token_limit=5, per_request={"sentiment": 10}, atomic_strategy="transaction")
    budget_manager = SyncTokenBudgetManager(fake_redis, budget_config)

    cb = CircuitBreaker(fake_redis, clock=fake_clock)
    permit = cb.check_permission("groq")
    err = ProviderExecutionError("500 Error", status_code=500, is_retryable=True)
    for _ in range(5):
        cb.record_failure(permit, err)

    # Advance cooldown to HALF_OPEN
    fake_clock.advance(305.0)

    mock_client = MagicMock()

    orchestrator = AIOrchestrator(
        client=mock_client,
        budget_manager=budget_manager,
        circuit_breaker=cb,
    )
    orchestrator._load_task_prompt = MagicMock(return_value="Prompt")

    # Request fails due to budget limit (exceeds 5 token limit)
    with pytest.raises(ProviderExecutionError):
        orchestrator.execute(
            task=AITask.SENTIMENT,
            context={"tenant_id": "tenant-1"},
        )

    # Probe lock was safely released on budget failure! Next call acquires probe cleanly
    probe_permit = cb.check_permission("groq")
    assert probe_permit.is_half_open_probe is True


def test_provider_and_redis_exception_text_absent_from_logs(fake_redis, caplog) -> None:
    caplog.set_level(logging.DEBUG)

    secret_key = "SECRET_REDIS_KEY_XYZ_999"
    secret_error_msg = "SENSITIVE_DB_EXCEPTION_MESSAGE_ABC"

    mock_redis = MagicMock()
    mock_redis.get = MagicMock(side_effect=Exception(secret_error_msg))

    cb = CircuitBreaker(mock_redis)

    with pytest.raises(CircuitBreakerInfrastructureError):
        cb.check_permission(secret_key)

    # Verify secret error message and key are NOT present in log records
    for record in caplog.records:
        assert secret_error_msg not in record.getMessage()
        assert secret_key not in record.getMessage()

def test_stale_probe_success_cannot_change_state(
    fake_redis,
    fake_clock,
) -> None:
    cb = CircuitBreaker(
        fake_redis,
        clock=fake_clock,
    )

    closed_permit = cb.check_permission(
        "groq"
    )

    error = ProviderExecutionError(
        "Provider unavailable",
        status_code=503,
        is_retryable=True,
    )

    for _ in range(5):
        cb.record_failure(
            closed_permit,
            error,
        )

    fake_clock.advance(305.0)

    stale_permit = cb.check_permission(
        "groq"
    )

    (
        _,
        _,
        successes_key,
        _,
        probe_lock_key,
    ) = cb._get_keys(
        stale_permit.provider_scope
    )

    fake_redis.set(
        probe_lock_key,
        "new-owner-token",
        ex=180,
    )

    cb.record_success(
        stale_permit
    )

    assert (
        fake_redis.get(probe_lock_key)
        == "new-owner-token"
    )

    assert int(
        fake_redis.get(successes_key) or 0
    ) == 0

    assert (
        cb.snapshot("groq").state
        == CircuitState.HALF_OPEN
    )

def test_stale_probe_failure_cannot_reopen_circuit(
    fake_redis,
    fake_clock,
) -> None:
    cb = CircuitBreaker(
        fake_redis,
        clock=fake_clock,
    )

    closed_permit = cb.check_permission(
        "groq"
    )

    error = ProviderExecutionError(
        "Provider unavailable",
        status_code=503,
        is_retryable=True,
    )

    for _ in range(5):
        cb.record_failure(
            closed_permit,
            error,
        )

    fake_clock.advance(305.0)

    stale_permit = cb.check_permission(
        "groq"
    )

    probe_lock_key = cb._get_keys(
        stale_permit.provider_scope
    )[4]

    fake_redis.set(
        probe_lock_key,
        "new-owner-token",
        ex=180,
    )

    cb.record_failure(
        stale_permit,
        error,
    )

    assert (
        fake_redis.get(probe_lock_key)
        == "new-owner-token"
    )

    assert (
        cb.snapshot("groq").state
        == CircuitState.HALF_OPEN
    )

def test_configured_probe_ttl_is_used(
    fake_redis,
    fake_clock,
) -> None:
    config = CircuitBreakerConfig(
        half_open_probe_ttl_seconds=180
    )

    cb = CircuitBreaker(
        fake_redis,
        config=config,
        clock=fake_clock,
    )

    closed_permit = cb.check_permission(
        "groq"
    )

    error = ProviderExecutionError(
        "Provider unavailable",
        status_code=503,
        is_retryable=True,
    )

    for _ in range(5):
        cb.record_failure(
            closed_permit,
            error,
        )

    fake_clock.advance(305.0)

    probe_permit = cb.check_permission(
        "groq"
    )

    probe_lock_key = cb._get_keys(
        probe_permit.provider_scope
    )[4]

    ttl = fake_redis.ttl(
        probe_lock_key
    )

    assert 0 < ttl <= 180
