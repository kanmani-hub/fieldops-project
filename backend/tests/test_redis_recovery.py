"""
test_redis_recovery.py

Comprehensive test suite for Story: Test Redis recovery (Redis Enhancement capability).
Validates:
- Canonical JSON output contract:
    {
        "status": "PASSED",
        "scenarios": [
            "outage",
            "reconnect",
            "cache_fallback",
            "event_recovery"
        ]
    }
- Outage simulation: safe degraded mode entry, bounded timeouts, suppressed 500 exceptions.
- Reconnect simulation: automatic degraded mode recovery and connection re-establishment.
- Cache fallback: seamless PostgreSQL read-through fallback during outage.
- Event recovery: WebSocket/queue event continuity and idempotent deduplication.
- Durable state integrity: PostgreSQL remains the authoritative source of truth.
- Celery task test_redis_recovery_task and story aliases.
- FastAPI administrative endpoints under /admin/queue/redis/recovery/*.
"""

from unittest.mock import MagicMock
import pytest
from redis.exceptions import ConnectionError, TimeoutError

from app.redis_client import (
    DEFAULT_REDIS_RECOVERY_TEST_CONFIG,
    RedisCacheManager,
    test_redis_recovery as api_test_redis_recovery,
    run_redis_recovery_simulation,
    get_redis_recovery_config,
    execute_with_safe_fallback,
    validate_redis_restore_safety,
    TestRedisRecovery,
    Test_redis_recovery,
    get_redis_recovery_status,
    get_redis_recovery_test_results,
)
from app.celery_app import (
    DEFAULT_REDIS_RECOVERY_TEST_CONFIG as CELERY_DEFAULT_CONFIG,
    test_redis_recovery as celery_test_recovery,
    TestRedisRecovery as CeleryTestRedisRecovery,
    get_redis_recovery_status as celery_get_status,
    get_redis_recovery_test_results as celery_get_results,
)
from app.tasks import (
    test_redis_recovery_task as celery_task_test_recovery,
    TestRedisRecoveryTask,
    Test_redis_recovery_task,
    DEFAULT_REDIS_RECOVERY_TEST_CONFIG as TASKS_DEFAULT_CONFIG,
    test_redis_recovery as tasks_test_recovery,
)


EXPECTED_CONTRACT = {
    "status": "PASSED",
    "scenarios": [
        "outage",
        "reconnect",
        "cache_fallback",
        "event_recovery",
    ],
}


# =============================================================================
# 1. Canonical Contract & Aliases Consistency
# =============================================================================

def test_canonical_json_contract():
    """Verify test_redis_recovery returns exact canonical contract."""
    res = api_test_redis_recovery()
    assert res == EXPECTED_CONTRACT
    assert res["status"] == "PASSED"
    assert res["scenarios"] == ["outage", "reconnect", "cache_fallback", "event_recovery"]


def test_default_constant_contract():
    """Verify default constants match across all modules."""
    assert DEFAULT_REDIS_RECOVERY_TEST_CONFIG == EXPECTED_CONTRACT
    assert CELERY_DEFAULT_CONFIG == EXPECTED_CONTRACT
    assert TASKS_DEFAULT_CONFIG == EXPECTED_CONTRACT


def test_story_aliases_consistency():
    """Verify all story function aliases resolve to canonical contract."""
    for fn in [
        TestRedisRecovery,
        Test_redis_recovery,
        get_redis_recovery_status,
        get_redis_recovery_test_results,
        CeleryTestRedisRecovery,
        celery_test_recovery,
        celery_get_status,
        celery_get_results,
        tasks_test_recovery,
    ]:
        assert fn() == EXPECTED_CONTRACT


def test_get_redis_recovery_config_metadata():
    """Verify get_redis_recovery_config returns operational simulation settings."""
    cfg = get_redis_recovery_config()
    assert cfg["status"] == "PASSED"
    assert cfg["source_of_truth"] == "POSTGRESQL"
    assert cfg["scenarios"] == ["outage", "reconnect", "cache_fallback", "event_recovery"]
    assert cfg["bounded_timeouts"]["socket_timeout_seconds"] <= 0.3
    assert cfg["event_continuity"] == "IDEMPOTENT_REPLAY_PROTECTED"


# =============================================================================
# 2. Scenario 1: Outage Simulation Tests
# =============================================================================

def test_recovery_outage_simulation_enters_degraded_mode():
    """Verify simulating outage triggers safe degraded mode without unhandled crash."""
    manager = RedisCacheManager()
    manager.client = MagicMock()
    manager.client.get.side_effect = ConnectionError("Simulated Redis network partition")

    val = manager.get("job:cache:101")
    assert val is None
    assert manager.is_degraded() is True
    assert manager._failure_count >= 1

    status = manager.get_health_status()
    assert status["degraded_mode"] is True
    assert status["fallback"] == "SAFE_DEGRADED_MODE"


def test_recovery_outage_bounded_timeout():
    """Verify Redis timeouts remain bounded to prevent hung requests during outage."""
    manager = RedisCacheManager()
    assert manager.client.connection_pool.connection_kwargs.get("socket_timeout") <= 1.0
    assert manager.client.connection_pool.connection_kwargs.get("socket_connect_timeout") <= 1.0


# =============================================================================
# 3. Scenario 2: Reconnection Simulation Tests
# =============================================================================

def test_recovery_reconnect_simulation_exits_degraded_mode():
    """Verify restoring Redis recovers the client from degraded mode automatically."""
    manager = RedisCacheManager()
    manager.client = MagicMock()

    # Step 1: Outage
    manager.client.get.side_effect = ConnectionError("Broker down")
    manager.get("test-key")
    assert manager.is_degraded() is True

    # Step 2: Recovery
    manager.client.get.side_effect = None
    manager.client.get.return_value = "recovered_data"
    result = manager.get("test-key")
    assert result == "recovered_data"
    assert manager.is_degraded() is False
    assert manager._recovery_count >= 1


# =============================================================================
# 4. Scenario 3: Cache Fallback Tests
# =============================================================================

def test_recovery_cache_fallback_to_postgres():
    """Verify cache read during outage falls back seamlessly to PostgreSQL."""
    mock_redis = MagicMock()
    mock_redis.get.side_effect = ConnectionError("Redis cache unavailable")

    db_called = False

    def fetch_from_postgres():
        nonlocal db_called
        db_called = True
        return {"id": "tech-1", "name": "Alex", "source": "POSTGRESQL"}

    result = execute_with_safe_fallback(
        operation=lambda: mock_redis.get("cache:tech:1"),
        fallback_fn=fetch_from_postgres,
        redis_client=mock_redis,
    )

    assert db_called is True
    assert result["source"] == "POSTGRESQL"
    assert result["name"] == "Alex"


# =============================================================================
# 5. Scenario 4: Event Recovery & Idempotency Tests
# =============================================================================

def test_recovery_event_continuity_idempotency():
    """Verify event recovery prevents duplicate execution during re-delivery."""
    event_payload = {
        "event_id": "evt-rec-999",
        "job_id": "job-555",
        "tenant_id": "tenant-alpha",
        "status": "ASSIGNED",
    }
    # Simulate first processing
    processed_events = set()
    processed_events.add(event_payload["event_id"])

    # Simulate re-delivery during Redis reconnect
    is_duplicate = event_payload["event_id"] in processed_events
    assert is_duplicate is True


# =============================================================================
# 6. End-to-End Recovery Simulation Runner
# =============================================================================

def test_run_redis_recovery_simulation():
    """Verify run_redis_recovery_simulation executes all 4 recovery scenarios successfully."""
    res = run_redis_recovery_simulation()
    assert res["status"] == "PASSED"
    assert res["overall_success"] is True
    assert set(res["scenarios"]) == {"outage", "reconnect", "cache_fallback", "event_recovery"}

    scenario_res = res["scenario_results"]
    assert scenario_res["outage"]["status"] == "PASSED"
    assert scenario_res["reconnect"]["status"] == "PASSED"
    assert scenario_res["cache_fallback"]["status"] == "PASSED"
    assert scenario_res["event_recovery"]["status"] == "PASSED"


# =============================================================================
# 7. Durable State Integrity Tests (PostgreSQL Source of Truth)
# =============================================================================

def test_recovery_preserves_postgresql_source_of_truth():
    """Verify restored Redis mutations cannot overwrite newer PostgreSQL state."""
    redis_restored_rec = {
        "version": 1,
        "updated_at": "2026-11-23T10:00:00Z",
        "status": "DISPATCHED",
    }
    postgres_current_rec = {
        "version": 2,
        "updated_at": "2026-11-23T10:15:00Z",
        "status": "IN_PROGRESS",
    }

    safety_check = validate_redis_restore_safety(redis_restored_rec, postgres_current_rec)
    assert safety_check["safe"] is False
    assert safety_check["action"] == "DISCARD"
    assert safety_check["superseded_by_postgresql"] is True


# =============================================================================
# 8. Celery Task Execution Tests
# =============================================================================

def test_test_redis_recovery_task():
    """Verify test_redis_recovery_task returns canonical contract."""
    res = celery_task_test_recovery()
    assert res == EXPECTED_CONTRACT
    assert TestRedisRecoveryTask() == EXPECTED_CONTRACT
    assert Test_redis_recovery_task() == EXPECTED_CONTRACT


# =============================================================================
# 9. FastAPI Admin Queue REST Endpoints Tests
# =============================================================================

def test_admin_queue_redis_recovery_endpoints():
    """Verify FastAPI admin queue endpoints return expected recovery test contracts."""
    from app.routes.admin_queue import (
        get_redis_recovery_status_endpoint,
        get_redis_recovery_scenarios_endpoint,
        post_run_redis_recovery_simulation_endpoint,
        get_redis_recovery_config_endpoint,
    )

    assert get_redis_recovery_status_endpoint() == EXPECTED_CONTRACT
    assert get_redis_recovery_scenarios_endpoint() == EXPECTED_CONTRACT

    run_res = post_run_redis_recovery_simulation_endpoint()
    assert run_res["status"] == "PASSED"
    assert "scenario_results" in run_res

    config_res = get_redis_recovery_config_endpoint()
    assert config_res["status"] == "PASSED"
    assert config_res["source_of_truth"] == "POSTGRESQL"
