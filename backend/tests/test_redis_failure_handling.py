"""
test_redis_failure_handling.py

Comprehensive test suite for Story: Add Redis failure handling (Redis Enhancement capability).
Validates:
- Canonical JSON output contract:
    {
        "status": "HARDENED",
        "fallback": "SAFE_DEGRADED_MODE"
    }
- Bounded connection and operation timeouts (socket_timeout, connect_timeout).
- Safe fallback for cache reads (seamless database fallback without 500 exceptions).
- Circuit breaker degraded mode state transitions (entering / exiting SAFE_DEGRADED_MODE).
- Strict source-of-truth protection (PostgreSQL durable state, fail-closed locks).
- Failure and recovery metrics / logging.
- Celery task add_redis_failure_handling_task and Celery app broker configuration.
- FastAPI administrative endpoints under /admin/queue/redis/failure-handling/*.
"""

from unittest.mock import MagicMock, patch
import pytest
from redis.exceptions import ConnectionError, TimeoutError

from app.redis_client import (
    DEFAULT_REDIS_FAILURE_HANDLING_CONFIG,
    RedisCacheManager,
    add_redis_failure_handling,
    get_redis_failure_handling_config,
    check_redis_health,
    execute_with_safe_fallback,
    AddRedisFailureHandling,
    Add_redis_failure_handling,
    get_redis_failure_handling_status,
    get_redis_fallback_mode,
    acquire_distributed_lock,
)
from app.celery_app import (
    celery_app,
    DEFAULT_REDIS_FAILURE_HANDLING_CONFIG as CELERY_DEFAULT_CONFIG,
    add_redis_failure_handling as celery_add_failure_handling,
    AddRedisFailureHandling as CeleryAddFailureHandling,
    get_redis_failure_handling_status as celery_get_status,
)
from app.tasks import (
    add_redis_failure_handling_task,
    AddRedisFailureHandlingTask,
    Add_redis_failure_handling_task,
    DEFAULT_REDIS_FAILURE_HANDLING_CONFIG as TASKS_DEFAULT_CONFIG,
    add_redis_failure_handling as tasks_add_failure_handling,
)


EXPECTED_CONTRACT = {
    "status": "HARDENED",
    "fallback": "SAFE_DEGRADED_MODE",
}


# =============================================================================
# 1. Canonical Contract & Configuration Validation
# =============================================================================

def test_canonical_json_contract():
    """Verify add_redis_failure_handling returns exact canonical contract."""
    res = add_redis_failure_handling()
    assert res == EXPECTED_CONTRACT
    assert res["status"] == "HARDENED"
    assert res["fallback"] == "SAFE_DEGRADED_MODE"


def test_default_constant_contract():
    """Verify default constants match across all modules."""
    assert DEFAULT_REDIS_FAILURE_HANDLING_CONFIG == EXPECTED_CONTRACT
    assert CELERY_DEFAULT_CONFIG == EXPECTED_CONTRACT
    assert TASKS_DEFAULT_CONFIG == EXPECTED_CONTRACT


def test_story_aliases_consistency():
    """Verify all story function aliases resolve to canonical contract."""
    for fn in [
        AddRedisFailureHandling,
        Add_redis_failure_handling,
        get_redis_failure_handling_status,
        get_redis_fallback_mode,
        CeleryAddFailureHandling,
        celery_add_failure_handling,
        celery_get_status,
        tasks_add_failure_handling,
    ]:
        assert fn() == EXPECTED_CONTRACT


def test_get_redis_failure_handling_config_metadata():
    """Verify get_redis_failure_handling_config returns full operational details."""
    cfg = get_redis_failure_handling_config()
    assert cfg["status"] == "HARDENED"
    assert cfg["fallback"] == "SAFE_DEGRADED_MODE"
    assert cfg["connection_timeout_seconds"] <= 1.0
    assert cfg["operation_timeout_seconds"] <= 1.0
    assert cfg["circuit_breaker"] == "ACTIVE"
    assert cfg["source_of_truth_protection"] == "STRICT_POSTGRESQL"


# =============================================================================
# 2. Bounded Timeouts & RedisCacheManager Initialization Tests
# =============================================================================

def test_redis_cache_manager_bounded_timeouts():
    """Verify RedisCacheManager initializes with bounded connection and socket timeouts."""
    manager = RedisCacheManager()
    assert manager.client.connection_pool.connection_kwargs.get("socket_timeout") <= 1.0
    assert manager.client.connection_pool.connection_kwargs.get("socket_connect_timeout") <= 1.0


def test_celery_broker_transport_bounded_timeouts():
    """Verify Celery app broker configuration includes bounded transport options."""
    transport_opts = celery_app.conf.broker_transport_options or {}
    assert "socket_timeout" in transport_opts
    assert transport_opts["socket_timeout"] <= 5.0
    assert celery_app.conf.broker_connection_retry_on_startup is True


# =============================================================================
# 3. Safe Read Fallback & Degraded Mode Mechanics
# =============================================================================

def test_execute_with_safe_fallback_executes_database_fetcher():
    """Verify execute_with_safe_fallback invokes database fallback on Redis failure."""
    mock_redis = MagicMock()
    mock_redis.get.side_effect = ConnectionError("Redis server connection refused")
    fallback_called = False

    def database_fallback():
        nonlocal fallback_called
        fallback_called = True
        return {"data": "fresh_from_postgres", "status": 200}

    result = execute_with_safe_fallback(
        operation=lambda: mock_redis.get("cache:key"),
        fallback_fn=database_fallback,
        redis_client=mock_redis,
    )

    assert fallback_called is True
    assert result["data"] == "fresh_from_postgres"


def test_execute_with_safe_fallback_returns_default_when_no_fallback_fn():
    """Verify execute_with_safe_fallback returns default value when fallback_fn is None."""
    mock_redis = MagicMock()
    mock_redis.get.side_effect = TimeoutError("Redis socket read timed out")

    result = execute_with_safe_fallback(
        operation=lambda: mock_redis.get("cache:timeout"),
        default={"safe_default": True},
        redis_client=mock_redis,
    )

    assert result == {"safe_default": True}


def test_redis_cache_manager_degraded_mode_transitions():
    """Verify RedisCacheManager tracks degraded mode entry, exit, and counters."""
    manager = RedisCacheManager()
    manager.client = MagicMock()

    # Simulate failure
    manager.client.get.side_effect = ConnectionError("Redis unreachable")
    val = manager.get("test-key")
    assert val is None
    assert manager.is_degraded() is True
    assert manager._failure_count >= 1

    # Status check indicates degraded mode
    status = manager.get_health_status()
    assert status["status"] == "HARDENED"
    assert status["fallback"] == "SAFE_DEGRADED_MODE"
    assert status["degraded_mode"] is True

    # Simulate recovery
    manager.client.get.side_effect = None
    manager.client.get.return_value = "recovered_value"
    val2 = manager.get("test-key")
    assert val2 == "recovered_value"
    assert manager.is_degraded() is False
    assert manager._recovery_count >= 1


# =============================================================================
# 4. Strict Source-of-Truth & Fail-Closed Concurrency Protection
# =============================================================================

def test_source_of_truth_locks_fail_closed_on_redis_outage():
    """Verify distributed locks fail closed on Redis failure to prevent split-brain execution."""
    mock_redis = MagicMock()
    mock_redis.client.set.side_effect = ConnectionError("Redis connection failed")

    acquired, token = acquire_distributed_lock("critical:job:123", redis_client=mock_redis)
    assert acquired is False
    assert token is None


def test_check_redis_health_live_and_mocked():
    """Verify check_redis_health returns health metrics without raising unhandled errors."""
    mock_healthy = MagicMock()
    mock_healthy.ping.return_value = True
    status_healthy = check_redis_health(redis_client=mock_healthy)
    assert status_healthy["status"] == "HARDENED"
    assert status_healthy["healthy"] is True

    mock_unhealthy = MagicMock()
    mock_unhealthy.ping.side_effect = ConnectionError("Connection refused")
    status_unhealthy = check_redis_health(redis_client=mock_unhealthy)
    assert status_unhealthy["status"] == "HARDENED"
    assert status_unhealthy["healthy"] is False
    assert status_unhealthy["degraded_mode"] is True


# =============================================================================
# 5. Celery Task Execution Tests
# =============================================================================

def test_add_redis_failure_handling_task():
    """Verify add_redis_failure_handling_task returns canonical contract."""
    res = add_redis_failure_handling_task()
    assert res == EXPECTED_CONTRACT

    # Test aliases
    assert AddRedisFailureHandlingTask() == EXPECTED_CONTRACT
    assert Add_redis_failure_handling_task() == EXPECTED_CONTRACT


# =============================================================================
# 6. FastAPI Admin Queue REST Endpoints Tests
# =============================================================================

def test_admin_queue_redis_failure_handling_endpoints():
    """Verify FastAPI admin queue endpoints return expected failure handling contract."""
    from app.routes.admin_queue import (
        get_redis_failure_handling_status_endpoint,
        get_redis_failure_handling_fallback_endpoint,
        post_enable_redis_failure_handling_endpoint,
        get_redis_failure_handling_config_endpoint,
        get_redis_health_endpoint,
    )

    assert get_redis_failure_handling_status_endpoint() == EXPECTED_CONTRACT
    assert get_redis_failure_handling_fallback_endpoint() == EXPECTED_CONTRACT
    assert post_enable_redis_failure_handling_endpoint() == EXPECTED_CONTRACT

    cfg = get_redis_failure_handling_config_endpoint()
    assert cfg["status"] == "HARDENED"
    assert cfg["fallback"] == "SAFE_DEGRADED_MODE"
    assert cfg["circuit_breaker"] == "ACTIVE"

    health = get_redis_health_endpoint()
    assert health["status"] == "HARDENED"
    assert "healthy" in health
    assert "degraded_mode" in health
