"""
test_redis_connection_monitoring.py

Comprehensive test suite for Story: Monitor Redis connection usage (Redis Enhancement capability).
Validates:
- Canonical JSON output contract:
    {
        "status": "MONITORED",
        "metrics": [
            "connections",
            "pool_pressure",
            "connection_errors"
        ]
    }
- Active and available connection metrics collection.
- Connection pool pressure and capacity indicators.
- Connection error tracking and alert thresholds.
- Tenant privacy preservation (no tenant-sensitive values in metric labels).
- Celery background task monitor_redis_connection_usage_task and story aliases.
- FastAPI administrative endpoints under /admin/queue/redis/connections/*.
- WebSocket server Redis subscriber health tracking.
"""

from pathlib import Path
from unittest.mock import MagicMock
import pytest
from redis.exceptions import ConnectionError, TimeoutError

from app.redis_client import (
    DEFAULT_REDIS_CONNECTION_MONITORING_CONFIG,
    monitor_redis_connection_usage,
    get_redis_connection_metrics,
    evaluate_redis_connection_pressure,
    get_redis_connection_monitoring_config,
    MonitorRedisConnectionUsage,
    Monitor_redis_connection_usage,
    get_redis_connection_monitoring_status,
    get_redis_connection_status,
)
from app.celery_app import (
    DEFAULT_REDIS_CONNECTION_MONITORING_CONFIG as CELERY_DEFAULT_CONFIG,
    monitor_redis_connection_usage as celery_monitor_connections,
    MonitorRedisConnectionUsage as CeleryMonitorRedisConnectionUsage,
    get_redis_connection_monitoring_status as celery_get_status,
    get_redis_connection_status as celery_get_conn_status,
)
from app.tasks import (
    monitor_redis_connection_usage_task,
    MonitorRedisConnectionUsageTask,
    Monitor_redis_connection_usage_task,
    DEFAULT_REDIS_CONNECTION_MONITORING_CONFIG as TASKS_DEFAULT_CONFIG,
    monitor_redis_connection_usage as tasks_monitor_connections,
)


EXPECTED_CONTRACT = {
    "status": "MONITORED",
    "metrics": [
        "connections",
        "pool_pressure",
        "connection_errors",
    ],
}


# =============================================================================
# 1. Canonical Contract & Aliases Consistency
# =============================================================================

def test_canonical_json_contract():
    """Verify monitor_redis_connection_usage returns exact canonical contract."""
    res = monitor_redis_connection_usage()
    assert res == EXPECTED_CONTRACT
    assert res["status"] == "MONITORED"
    assert res["metrics"] == ["connections", "pool_pressure", "connection_errors"]


def test_default_constant_contract():
    """Verify default constants match across all modules."""
    assert DEFAULT_REDIS_CONNECTION_MONITORING_CONFIG == EXPECTED_CONTRACT
    assert CELERY_DEFAULT_CONFIG == EXPECTED_CONTRACT
    assert TASKS_DEFAULT_CONFIG == EXPECTED_CONTRACT


def test_story_aliases_consistency():
    """Verify all story function aliases resolve to canonical contract."""
    for fn in [
        MonitorRedisConnectionUsage,
        Monitor_redis_connection_usage,
        get_redis_connection_monitoring_status,
        get_redis_connection_status,
        CeleryMonitorRedisConnectionUsage,
        celery_monitor_connections,
        celery_get_status,
        celery_get_conn_status,
        tasks_monitor_connections,
    ]:
        assert fn() == EXPECTED_CONTRACT


def test_get_redis_connection_monitoring_config_metadata():
    """Verify get_redis_connection_monitoring_config returns complete operational rules."""
    cfg = get_redis_connection_monitoring_config()
    assert cfg["status"] == "MONITORED"
    assert cfg["metrics"] == ["connections", "pool_pressure", "connection_errors"]
    assert cfg["connection_pool_strategy"] == "SINGLETON_MANAGED_POOL"
    assert cfg["alerting_rules"]["warning_pressure_percent"] == 75.0
    assert cfg["alerting_rules"]["critical_pressure_percent"] == 90.0
    assert cfg["alerting_rules"]["alert_on_connection_errors"] is True
    assert cfg["tenant_label_policy"] == "NO_TENANT_IDENTIFIERS_IN_LABELS"


# =============================================================================
# 2. Redis Connection Metrics Collection Tests
# =============================================================================

def test_get_redis_connection_metrics_with_mock_redis():
    """Verify get_redis_connection_metrics extracts connections, pool pressure, and errors."""
    mock_redis = MagicMock()
    mock_redis.info.side_effect = lambda section="": (
        {
            "connected_clients": 45,
            "blocked_clients": 2,
            "maxclients": 100,
        }
        if section == "clients"
        else {"rejected_connections": 3}
    )

    metrics = get_redis_connection_metrics(redis_client=mock_redis)
    assert metrics["status"] == "MONITORED"
    assert metrics["metrics"] == ["connections", "pool_pressure", "connection_errors"]
    assert metrics["connections"] == 45
    assert metrics["blocked_clients"] == 2
    assert metrics["pool_pressure"] == 0.45
    assert metrics["connection_errors"] == 3
    assert metrics["healthy"] is True
    assert metrics["degraded_mode"] is False
    assert metrics["websocket_subscriber"]["status"] == "ready"


def test_get_redis_connection_metrics_none_client_fallback():
    """Verify get_redis_connection_metrics safely handles None or disabled client."""
    metrics = get_redis_connection_metrics(redis_client=False)
    assert metrics["status"] == "MONITORED"
    assert metrics["metrics"] == ["connections", "pool_pressure", "connection_errors"]
    assert metrics["connections"] == 0
    assert metrics["pool_pressure"] == 0.0
    assert metrics["healthy"] is False
    assert metrics["degraded_mode"] is True


def test_get_redis_connection_metrics_exception_fallback():
    """Verify get_redis_connection_metrics catches connection errors gracefully."""
    mock_redis = MagicMock()
    mock_redis.info.side_effect = ConnectionError("Connection refused by Redis broker")

    metrics = get_redis_connection_metrics(redis_client=mock_redis)
    assert metrics["status"] == "MONITORED"
    assert metrics["healthy"] is False
    assert metrics["degraded_mode"] is True
    assert "error" in metrics


# =============================================================================
# 3. Connection Pressure Evaluation & Alert Threshold Tests
# =============================================================================

def test_evaluate_connection_pressure_normal():
    """Verify evaluate_redis_connection_pressure indicates OK when pressure is low."""
    res = evaluate_redis_connection_pressure(
        connections=20,
        max_connections=100,
        connection_errors=0,
    )
    assert res["status"] == "MONITORED"
    assert res["level"] == "OK"
    assert res["pool_pressure"] == 0.20
    assert len(res["alerts"]) == 0


def test_evaluate_connection_pressure_warning():
    """Verify evaluate_redis_connection_pressure triggers WARNING when pressure >= 75%."""
    res = evaluate_redis_connection_pressure(
        connections=78,
        max_connections=100,
        connection_errors=0,
    )
    assert res["level"] == "WARNING"
    assert res["pressure_percent"] == 78.0
    assert any(a["code"] == "REDIS_HIGH_CONNECTION_PRESSURE" for a in res["alerts"])


def test_evaluate_connection_pressure_critical():
    """Verify evaluate_redis_connection_pressure triggers CRITICAL when pressure >= 90%."""
    res = evaluate_redis_connection_pressure(
        connections=95,
        max_connections=100,
        connection_errors=0,
    )
    assert res["level"] == "CRITICAL"
    assert res["pressure_percent"] == 95.0
    assert any(a["code"] == "REDIS_CONNECTION_POOL_EXHAUSTION_RISK" for a in res["alerts"])


def test_evaluate_connection_pressure_connection_errors_alert():
    """Verify repeated connection errors generate alerts to protect subscriber/queues."""
    res = evaluate_redis_connection_pressure(
        connections=10,
        max_connections=100,
        connection_errors=5,
    )
    assert res["level"] == "WARNING"
    assert any(a["code"] == "REDIS_CONNECTION_FAILURES_DETECTED" for a in res["alerts"])


# =============================================================================
# 4. Tenant Privacy & Label Sanitization Tests
# =============================================================================

def test_connection_metric_labels_contain_no_tenant_identifiers():
    """Verify metric labels and alerts do not contain tenant-sensitive values."""
    res = evaluate_redis_connection_pressure(
        connections=88,
        max_connections=100,
        connection_errors=2,
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

def test_monitor_redis_connection_usage_task():
    """Verify monitor_redis_connection_usage_task returns canonical contract."""
    res = monitor_redis_connection_usage_task()
    assert res == EXPECTED_CONTRACT
    assert MonitorRedisConnectionUsageTask() == EXPECTED_CONTRACT
    assert Monitor_redis_connection_usage_task() == EXPECTED_CONTRACT


# =============================================================================
# 6. FastAPI Admin Queue REST Endpoints Tests
# =============================================================================

def test_admin_queue_redis_connection_endpoints():
    """Verify FastAPI admin queue endpoints return expected connection monitoring responses."""
    from app.routes.admin_queue import (
        get_redis_connection_status_endpoint,
        get_redis_connection_metrics_endpoint,
        post_redis_connection_monitor_endpoint,
        get_redis_connection_pressure_endpoint,
        get_redis_connection_config_endpoint,
    )

    assert get_redis_connection_status_endpoint() == EXPECTED_CONTRACT
    assert post_redis_connection_monitor_endpoint() == EXPECTED_CONTRACT

    metrics_res = get_redis_connection_metrics_endpoint()
    assert metrics_res["status"] == "MONITORED"
    assert "connections" in metrics_res
    assert "pool_pressure" in metrics_res
    assert "connection_errors" in metrics_res

    pressure_res = get_redis_connection_pressure_endpoint()
    assert pressure_res["status"] == "MONITORED"
    assert "level" in pressure_res

    config_res = get_redis_connection_config_endpoint()
    assert config_res["status"] == "MONITORED"
    assert "connection_pool_strategy" in config_res


# =============================================================================
# 7. WebSocket Server Redis Subscriber Monitoring Tests
# =============================================================================

def test_websocket_server_exposes_redis_subscriber_metrics():
    """Verify ws-server/server.js tracks and exposes Redis subscriber connection metrics."""
    ws_server_path = Path(__file__).resolve().parent.parent.parent / "ws-server" / "server.js"
    assert ws_server_path.exists(), f"server.js not found at {ws_server_path}"

    content = ws_server_path.read_text(encoding="utf-8")
    assert "redis_subscriber" in content
    assert "redisErrorsCount" in content
    assert "redisReconnectCount" in content
    assert "connection_errors" in content
