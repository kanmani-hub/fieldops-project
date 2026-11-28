"""
test_redis_memory_monitoring.py

Comprehensive test suite for Story: Monitor Redis memory usage (Redis Enhancement capability).
Validates:
- Canonical JSON output contract:
    {
        "status": "MONITORED",
        "metrics": [
            "used_memory",
            "memory_limit",
            "evictions"
        ]
    }
- Used memory, memory limit, and eviction metrics collection.
- Eviction pressure and abnormal growth indicators.
- Memory threshold alerting (Warning at 80%, Critical at 90%, Active evictions).
- Tenant privacy preservation (no tenant-sensitive values in metric labels).
- Celery background task monitor_redis_memory_usage_task and story aliases.
- FastAPI administrative endpoints under /admin/queue/redis/memory/*.
"""

from unittest.mock import MagicMock
import pytest
from redis.exceptions import ConnectionError, TimeoutError

from app.redis_client import (
    DEFAULT_REDIS_MEMORY_MONITORING_CONFIG,
    monitor_redis_memory_usage,
    get_redis_memory_metrics,
    evaluate_redis_memory_pressure,
    get_redis_memory_monitoring_config,
    MonitorRedisMemoryUsage,
    Monitor_redis_memory_usage,
    get_redis_memory_monitoring_status,
    get_redis_memory_status,
)
from app.celery_app import (
    DEFAULT_REDIS_MEMORY_MONITORING_CONFIG as CELERY_DEFAULT_CONFIG,
    monitor_redis_memory_usage as celery_monitor_memory,
    MonitorRedisMemoryUsage as CeleryMonitorRedisMemoryUsage,
    get_redis_memory_monitoring_status as celery_get_status,
    get_redis_memory_status as celery_get_mem_status,
)
from app.tasks import (
    monitor_redis_memory_usage_task,
    MonitorRedisMemoryUsageTask,
    Monitor_redis_memory_usage_task,
    DEFAULT_REDIS_MEMORY_MONITORING_CONFIG as TASKS_DEFAULT_CONFIG,
    monitor_redis_memory_usage as tasks_monitor_memory,
)


EXPECTED_CONTRACT = {
    "status": "MONITORED",
    "metrics": [
        "used_memory",
        "memory_limit",
        "evictions",
    ],
}


# =============================================================================
# 1. Canonical Contract & Aliases Consistency
# =============================================================================

def test_canonical_json_contract():
    """Verify monitor_redis_memory_usage returns exact canonical contract."""
    res = monitor_redis_memory_usage()
    assert res == EXPECTED_CONTRACT
    assert res["status"] == "MONITORED"
    assert res["metrics"] == ["used_memory", "memory_limit", "evictions"]


def test_default_constant_contract():
    """Verify default constants match across all modules."""
    assert DEFAULT_REDIS_MEMORY_MONITORING_CONFIG == EXPECTED_CONTRACT
    assert CELERY_DEFAULT_CONFIG == EXPECTED_CONTRACT
    assert TASKS_DEFAULT_CONFIG == EXPECTED_CONTRACT


def test_story_aliases_consistency():
    """Verify all story function aliases resolve to canonical contract."""
    for fn in [
        MonitorRedisMemoryUsage,
        Monitor_redis_memory_usage,
        get_redis_memory_monitoring_status,
        get_redis_memory_status,
        CeleryMonitorRedisMemoryUsage,
        celery_monitor_memory,
        celery_get_status,
        celery_get_mem_status,
        tasks_monitor_memory,
    ]:
        assert fn() == EXPECTED_CONTRACT


def test_get_redis_memory_monitoring_config_metadata():
    """Verify get_redis_memory_monitoring_config returns alert rules and configuration."""
    cfg = get_redis_memory_monitoring_config()
    assert cfg["status"] == "MONITORED"
    assert cfg["metrics"] == ["used_memory", "memory_limit", "evictions"]
    assert cfg["alerting_rules"]["warning_threshold_percent"] == 80.0
    assert cfg["alerting_rules"]["critical_threshold_percent"] == 90.0
    assert cfg["alerting_rules"]["eviction_alert_enabled"] is True
    assert cfg["tenant_label_policy"] == "NO_TENANT_IDENTIFIERS_IN_LABELS"


# =============================================================================
# 2. Redis Metrics Collection Tests (Live and Mocked)
# =============================================================================

def test_get_redis_memory_metrics_with_mock_redis():
    """Verify get_redis_memory_metrics extracts used memory, limit, and evictions."""
    mock_redis = MagicMock()
    mock_redis.info.side_effect = lambda section="": (
        {
            "used_memory": 209715200,          # 200 MB
            "maxmemory": 268435456,            # 256 MB
            "used_memory_peak": 220000000,
            "used_memory_human": "200M",
            "maxmemory_human": "256M",
            "mem_fragmentation_ratio": 1.15,
        }
        if section == "memory"
        else {"evicted_keys": 42}
    )

    metrics = get_redis_memory_metrics(redis_client=mock_redis)
    assert metrics["status"] == "MONITORED"
    assert metrics["metrics"] == ["used_memory", "memory_limit", "evictions"]
    assert metrics["used_memory"] == 209715200
    assert metrics["memory_limit"] == 268435456
    assert metrics["evictions"] == 42
    assert metrics["usage_percent"] > 70.0
    assert metrics["healthy"] is True
    assert metrics["degraded_mode"] is False


def test_get_redis_memory_metrics_none_client_fallback():
    """Verify get_redis_memory_metrics safely handles None or disabled client without throwing errors."""
    metrics = get_redis_memory_metrics(redis_client=False)
    assert metrics["status"] == "MONITORED"
    assert metrics["metrics"] == ["used_memory", "memory_limit", "evictions"]
    assert metrics["used_memory"] == 0
    assert metrics["evictions"] == 0
    assert metrics["healthy"] is False
    assert metrics["degraded_mode"] is True


def test_get_redis_memory_metrics_exception_fallback():
    """Verify get_redis_memory_metrics gracefully catches ConnectionError / TimeoutError."""
    mock_redis = MagicMock()
    mock_redis.info.side_effect = ConnectionError("Redis server unreachable")

    metrics = get_redis_memory_metrics(redis_client=mock_redis)
    assert metrics["status"] == "MONITORED"
    assert metrics["healthy"] is False
    assert metrics["degraded_mode"] is True
    assert "error" in metrics


# =============================================================================
# 3. Memory Pressure Evaluation & Threshold Alerting Tests
# =============================================================================

def test_evaluate_redis_memory_pressure_normal():
    """Verify evaluate_redis_memory_pressure indicates OK when below 80% with 0 evictions."""
    res = evaluate_redis_memory_pressure(
        used_memory=50_000_000,    # ~50 MB
        memory_limit=100_000_000,  # ~100 MB
        evictions=0,
    )
    assert res["status"] == "MONITORED"
    assert res["level"] == "OK"
    assert res["usage_percent"] == 50.0
    assert len(res["alerts"]) == 0


def test_evaluate_redis_memory_pressure_warning_threshold():
    """Verify evaluate_redis_memory_pressure alerts WARNING when usage >= 80%."""
    res = evaluate_redis_memory_pressure(
        used_memory=82_000_000,
        memory_limit=100_000_000,
        evictions=0,
    )
    assert res["level"] == "WARNING"
    assert res["usage_percent"] == 82.0
    assert any(a["code"] == "REDIS_HIGH_MEMORY_PRESSURE" for a in res["alerts"])


def test_evaluate_redis_memory_pressure_critical_threshold():
    """Verify evaluate_redis_memory_pressure alerts CRITICAL when usage >= 90%."""
    res = evaluate_redis_memory_pressure(
        used_memory=95_000_000,
        memory_limit=100_000_000,
        evictions=0,
    )
    assert res["level"] == "CRITICAL"
    assert res["usage_percent"] == 95.0
    assert any(a["code"] == "REDIS_MEMORY_EXHAUSTION_RISK" for a in res["alerts"])


def test_evaluate_redis_memory_pressure_active_evictions():
    """Verify active key evictions generate dedicated warning to protect queues."""
    res = evaluate_redis_memory_pressure(
        used_memory=40_000_000,
        memory_limit=100_000_000,
        evictions=150,
    )
    assert res["level"] == "WARNING"
    assert any(a["code"] == "REDIS_ACTIVE_KEY_EVICTIONS" for a in res["alerts"])


# =============================================================================
# 4. Tenant Privacy & Security Isolation Tests
# =============================================================================

def test_metric_labels_contain_no_tenant_identifiers():
    """Verify metric labels and alerts do not leak tenant_id or customer-sensitive values."""
    res = evaluate_redis_memory_pressure(
        used_memory=92_000_000,
        memory_limit=100_000_000,
        evictions=5,
    )
    for alert in res["alerts"]:
        labels = alert.get("metric_labels", {})
        label_keys = [str(k).lower() for k in labels.keys()]
        label_vals = [str(v).lower() for v in labels.values()]

        assert "tenant_id" not in label_keys
        assert "customer_id" not in label_keys
        assert "user_id" not in label_keys

        for v in label_vals:
            assert "tenant" not in v


# =============================================================================
# 5. Celery Background Task Tests
# =============================================================================

def test_monitor_redis_memory_usage_task():
    """Verify monitor_redis_memory_usage_task returns canonical contract."""
    res = monitor_redis_memory_usage_task()
    assert res == EXPECTED_CONTRACT
    assert MonitorRedisMemoryUsageTask() == EXPECTED_CONTRACT
    assert Monitor_redis_memory_usage_task() == EXPECTED_CONTRACT


# =============================================================================
# 6. FastAPI Admin Queue REST Endpoints Tests
# =============================================================================

def test_admin_queue_redis_memory_endpoints():
    """Verify FastAPI admin queue endpoints return expected memory monitoring responses."""
    from app.routes.admin_queue import (
        get_redis_memory_status_endpoint,
        get_redis_memory_metrics_endpoint,
        post_redis_memory_monitor_endpoint,
        get_redis_memory_pressure_endpoint,
        get_redis_memory_config_endpoint,
    )

    assert get_redis_memory_status_endpoint() == EXPECTED_CONTRACT
    assert post_redis_memory_monitor_endpoint() == EXPECTED_CONTRACT

    metrics_res = get_redis_memory_metrics_endpoint()
    assert metrics_res["status"] == "MONITORED"
    assert "used_memory" in metrics_res
    assert "memory_limit" in metrics_res
    assert "evictions" in metrics_res

    pressure_res = get_redis_memory_pressure_endpoint()
    assert pressure_res["status"] == "MONITORED"
    assert "level" in pressure_res

    config_res = get_redis_memory_config_endpoint()
    assert config_res["status"] == "MONITORED"
    assert "alerting_rules" in config_res
