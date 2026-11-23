"""
test_send_sla_alerts.py

Automated test suite for the story:
Send SLA alerts as an integrated part of the
WebSocket Real-Time Events capability.

Expected Output (JSON):
{
    "event": "sla.alert",
    "tenant_id": "tenant-1",
    "job_id": "123",
    "severity": "AT_RISK"
}
"""

import json
import os
import pytest
from unittest.mock import MagicMock, patch

from app.services.sla_alert_broadcaster import (
    DEFAULT_SLA_ALERT_CONFIG,
    VALID_SEVERITIES,
    SLAAlertBroadcaster,
    broadcast_sla_alert,
    get_sla_alert_config,
    get_sla_alert_status,
    send_sla_alerts,
    SendSLAAlerts,
    Send_sla_alerts,
)


class TestSLAAlertContract:
    """Verifies canonical JSON contract and configuration."""

    def test_canonical_contract_defaults(self):
        result = send_sla_alerts()
        assert isinstance(result, dict)
        assert result.get("event") == "sla.alert"
        assert result.get("tenant_id") == "tenant-1"
        assert result.get("job_id") == "123"
        assert result.get("severity") == "AT_RISK"

    def test_canonical_contract_custom_values(self):
        result = send_sla_alerts(
            job_id="job-999",
            severity="BREACHED",
            tenant_id="tenant-custom",
        )
        assert result["event"] == "sla.alert"
        assert result["tenant_id"] == "tenant-custom"
        assert result["job_id"] == "job-999"
        assert result["severity"] == "BREACHED"

    def test_default_config_constants(self):
        assert DEFAULT_SLA_ALERT_CONFIG["event"] == "sla.alert"
        assert DEFAULT_SLA_ALERT_CONFIG["tenant_id"] == "tenant-1"
        assert DEFAULT_SLA_ALERT_CONFIG["job_id"] == "123"
        assert DEFAULT_SLA_ALERT_CONFIG["severity"] == "AT_RISK"

    def test_valid_severities_set(self):
        assert "AT_RISK" in VALID_SEVERITIES
        assert "BREACHED" in VALID_SEVERITIES
        assert "WARNING" in VALID_SEVERITIES
        assert "CRITICAL" in VALID_SEVERITIES
        assert "INFO" in VALID_SEVERITIES
        assert "RESOLVED" in VALID_SEVERITIES

    def test_operational_config(self):
        config = get_sla_alert_config()
        assert config["event"] == "sla.alert"
        assert config["transport"] == "REDIS_PUB_SUB_TO_WEBSOCKET"
        assert config["namespace"] == "/dispatch-dashboard"
        assert config["source_of_truth"] == "POSTGRESQL"
        assert config["redis_channel"] == "dispatch_events"
        assert config["sla_channel"] == "sla_events"
        assert config["deduplication_ttl_seconds"] == 3600
        assert "BREACHED" in config["valid_severities"]
        assert config["default_contract"]["severity"] == "AT_RISK"


class TestAuthoritativeStoryAliases:
    """Verifies story aliases across all modules."""

    def test_aliases_in_broadcaster_module(self):
        assert SendSLAAlerts == send_sla_alerts
        assert Send_sla_alerts == send_sla_alerts
        assert broadcast_sla_alert == send_sla_alerts
        assert get_sla_alert_status == send_sla_alerts
        assert SendSLAAlerts()["event"] == "sla.alert"

    def test_reexports_in_event_publisher(self):
        import app.services.event_publisher as ep
        assert hasattr(ep, "DEFAULT_SLA_ALERT_CONFIG")
        assert hasattr(ep, "send_sla_alerts")
        assert hasattr(ep, "SendSLAAlerts")
        assert hasattr(ep, "broadcast_sla_alert")
        assert ep.SendSLAAlerts()["severity"] == "AT_RISK"

    def test_reexports_in_redis_client(self):
        import app.redis_client as rc
        assert hasattr(rc, "DEFAULT_SLA_ALERT_CONFIG")
        assert hasattr(rc, "send_sla_alerts")
        assert hasattr(rc, "SendSLAAlerts")
        assert hasattr(rc, "broadcast_sla_alert")
        assert rc.SendSLAAlerts()["event"] == "sla.alert"

    def test_reexports_in_celery_app(self):
        import app.celery_app as ca
        assert hasattr(ca, "DEFAULT_SLA_ALERT_CONFIG")
        assert hasattr(ca, "send_sla_alerts")
        assert hasattr(ca, "SendSLAAlerts")
        assert hasattr(ca, "get_sla_alert_config")
        assert ca.SendSLAAlerts()["severity"] == "AT_RISK"

    def test_reexports_and_task_in_tasks(self):
        import app.tasks as tasks
        assert hasattr(tasks, "send_sla_alerts_task")
        assert hasattr(tasks, "SendSLAAlertsTask")
        assert hasattr(tasks, "Send_sla_alerts_task")
        assert hasattr(tasks, "SendSLAAlerts")
        res = tasks.send_sla_alerts_task()
        assert res["severity"] == "AT_RISK"
        assert res["event"] == "sla.alert"


class TestValidationAndDeduplication:
    """Verifies validation and deduplication."""

    def test_validation_rejects_missing_mandatory_fields(self):
        broadcaster = SLAAlertBroadcaster()
        res1 = broadcaster.broadcast_sla_alert(job_id="", severity="AT_RISK", tenant_id="t1")
        assert res1["status_code"] == "REJECTED"

        res2 = broadcaster.broadcast_sla_alert(job_id="123", severity="AT_RISK", tenant_id="")
        assert res2["status_code"] == "REJECTED"

    def test_unknown_severity_falls_back_to_at_risk(self):
        res = send_sla_alerts(job_id="123", severity="UNKNOWN_SEVERITY", tenant_id="tenant-1")
        assert res["severity"] == "AT_RISK"

    def test_duplicate_sla_alert_deduplication(self):
        mock_redis = MagicMock()
        mock_redis.get.return_value = b"1"  # Already seen

        broadcaster = SLAAlertBroadcaster(redis_client=mock_redis)
        res = broadcaster.broadcast_sla_alert(
            job_id="job-dup",
            severity="BREACHED",
            tenant_id="tenant-dup",
            event_id="stable-dup-sla-id",
            redis_client=mock_redis,
        )

        assert res["event"] == "sla.alert"
        assert res["duplicate"] is True
        assert res["status_code"] == "SKIPPED"
        mock_redis.publish.assert_not_called()


class TestRedisPublishAndFailureSafety:
    """Verifies wire payload structure and bounded failure behavior."""

    def test_publish_payload_structure(self):
        mock_redis = MagicMock()
        mock_redis.get.return_value = None

        broadcaster = SLAAlertBroadcaster(redis_client=mock_redis)
        res = broadcaster.broadcast_sla_alert(
            job_id="job-789",
            severity="CRITICAL",
            tenant_id="tenant-sla",
            sla_type="RESOLUTION",
            time_remaining_seconds=180,
            target_time="2026-11-23T17:00:00Z",
            customer_tier="PLATINUM",
            correlation_id="corr-sla-1",
            timestamp="2026-11-23T16:30:00Z",
            redis_client=mock_redis,
        )

        assert res["event"] == "sla.alert"
        assert res["job_id"] == "job-789"
        assert res["severity"] == "CRITICAL"
        assert res["tenant_id"] == "tenant-sla"

        assert mock_redis.publish.call_count >= 1
        calls = mock_redis.publish.call_args_list
        channels = [call[0][0] for call in calls]
        assert "dispatch_events" in channels

        raw_payload = calls[0][0][1]
        payload = json.loads(raw_payload)
        assert payload["event"] == "sla.alert"
        assert payload["tenant_id"] == "tenant-sla"
        assert payload["job_id"] == "job-789"
        assert payload["severity"] == "CRITICAL"
        assert payload["sla_type"] == "RESOLUTION"
        assert payload["time_remaining_seconds"] == 180
        assert payload["target_time"] == "2026-11-23T17:00:00Z"
        assert payload["customer_tier"] == "PLATINUM"
        assert payload["correlation_id"] == "corr-sla-1"
        assert payload["timestamp"] == "2026-11-23T16:30:00Z"

    def test_bounded_failure_safety_on_redis_error(self):
        mock_redis = MagicMock()
        mock_redis.publish.side_effect = ConnectionError("Redis down")

        broadcaster = SLAAlertBroadcaster(redis_client=mock_redis)
        res = broadcaster.broadcast_sla_alert(
            job_id="job-fail",
            severity="BREACHED",
            tenant_id="tenant-fail",
            redis_client=mock_redis,
        )

        assert res["event"] == "sla.alert"
        assert res["job_id"] == "job-fail"
        assert res["tenant_id"] == "tenant-fail"
        assert res["severity"] == "BREACHED"


class TestEventPublisherIntegration:
    """Verifies publish_sla_event triggers send_sla_alerts."""

    def test_publish_sla_event_triggers_broadcaster(self):
        from app.services.event_publisher import publish_sla_event

        mock_redis = MagicMock()
        mock_redis.get.return_value = None

        publish_sla_event(
            redis_client=mock_redis,
            event_type="SLA_BREACHED",
            job_id="job-ep-1",
            tenant_id="tenant-ep",
            payload={
                "remaining_seconds": -60,
                "sla_deadline": "2026-11-23T16:00:00Z",
                "customer_tier": "GOLD",
            },
        )

        assert mock_redis.publish.call_count >= 1


class TestAdminQueueRestEndpoints:
    """Verifies REST endpoints in admin_queue."""

    def test_admin_queue_status_and_config(self):
        from app.routes.admin_queue import (
            get_sla_alerts_status_endpoint,
            get_sla_alerts_config_endpoint,
            post_send_sla_alert_endpoint,
        )

        status = get_sla_alerts_status_endpoint()
        assert status["status"] == "READY"
        assert status["event"] == "sla.alert"

        config = get_sla_alerts_config_endpoint()
        assert config["event"] == "sla.alert"
        assert "valid_severities" in config

        mock_user = MagicMock()
        mock_user.tenant_id = "tenant-user-1"
        post_res = post_send_sla_alert_endpoint(
            payload={"job_id": "job-api-1", "severity": "WARNING"},
            current_user=mock_user,
        )
        assert post_res["event"] == "sla.alert"
        assert post_res["job_id"] == "job-api-1"
        assert post_res["tenant_id"] == "tenant-user-1"
        assert post_res["severity"] == "WARNING"


class TestWebSocketServerIntegration:
    """Verifies ws-server/server.js contains SLA alert event and endpoints."""

    def test_ws_server_contains_sla_alert_logic(self):
        server_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "ws-server", "server.js")
        )
        assert os.path.exists(server_path), f"ws-server/server.js not found at {server_path}"

        with open(server_path, "r", encoding="utf-8") as f:
            content = f.read()

        assert "sla.alert" in content
        assert "/sla-alerts/config" in content
        assert "/sla-alerts/broadcast" in content
        assert "sla_events" in content
