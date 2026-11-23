"""
test_send_notification_updates.py

Automated test suite for the story:
Send notification updates as an integrated part of the
WebSocket Real-Time Events capability.

Expected Output (JSON):
{
    "event": "notification.updated",
    "tenant_id": "tenant-1",
    "notification_id": "n-1",
    "status": "SENT"
}
"""

import json
import os
import pytest
from unittest.mock import MagicMock, patch

from app.services.notification_update_broadcaster import (
    DEFAULT_NOTIFICATION_UPDATE_CONFIG,
    VALID_NOTIFICATION_STATUSES,
    NotificationUpdateBroadcaster,
    broadcast_notification_update,
    get_notification_update_config,
    get_notification_update_status,
    mask_recipient,
    normalize_notification_status,
    send_notification_updates,
    SendNotificationUpdates,
    Send_notification_updates,
)


class TestNotificationUpdateContract:
    """Verifies canonical JSON contract and configuration."""

    def test_canonical_contract_defaults(self):
        result = send_notification_updates()
        assert isinstance(result, dict)
        assert result.get("event") == "notification.updated"
        assert result.get("tenant_id") == "tenant-1"
        assert result.get("notification_id") == "n-1"
        assert result.get("status") == "SENT"

    def test_canonical_contract_custom_values(self):
        result = send_notification_updates(
            notification_id="notif-888",
            status="FAILED",
            tenant_id="tenant-custom",
        )
        assert result["event"] == "notification.updated"
        assert result["tenant_id"] == "tenant-custom"
        assert result["notification_id"] == "notif-888"
        assert result["status"] == "FAILED"

    def test_default_config_constants(self):
        assert DEFAULT_NOTIFICATION_UPDATE_CONFIG["event"] == "notification.updated"
        assert DEFAULT_NOTIFICATION_UPDATE_CONFIG["tenant_id"] == "tenant-1"
        assert DEFAULT_NOTIFICATION_UPDATE_CONFIG["notification_id"] == "n-1"
        assert DEFAULT_NOTIFICATION_UPDATE_CONFIG["status"] == "SENT"

    def test_valid_statuses_set(self):
        assert "QUEUED" in VALID_NOTIFICATION_STATUSES
        assert "SENT" in VALID_NOTIFICATION_STATUSES
        assert "FAILED" in VALID_NOTIFICATION_STATUSES
        assert "RETRIED" in VALID_NOTIFICATION_STATUSES
        assert "DELIVERED" in VALID_NOTIFICATION_STATUSES
        assert "PENDING" in VALID_NOTIFICATION_STATUSES
        assert "READ" in VALID_NOTIFICATION_STATUSES

    def test_operational_config(self):
        config = get_notification_update_config()
        assert config["event"] == "notification.updated"
        assert config["transport"] == "REDIS_PUB_SUB_TO_WEBSOCKET"
        assert config["namespace"] == "/dispatch-dashboard"
        assert config["source_of_truth"] == "POSTGRESQL"
        assert config["redis_channel"] == "dispatch_events"
        assert config["notification_channel"] == "notification_events"
        assert config["deduplication_ttl_seconds"] == 3600
        assert "SENT" in config["valid_statuses"]
        assert config["default_contract"]["status"] == "SENT"


class TestStatusNormalizationAndMasking:
    """Verifies status normalization and PII masking."""

    def test_status_normalization(self):
        assert normalize_notification_status("queued") == "QUEUED"
        assert normalize_notification_status("pending") == "QUEUED"
        assert normalize_notification_status("sending") == "SENT"
        assert normalize_notification_status("accepted") == "SENT"
        assert normalize_notification_status("sent") == "SENT"
        assert normalize_notification_status("failed") == "FAILED"
        assert normalize_notification_status("permanent_failure") == "FAILED"
        assert normalize_notification_status("retried") == "RETRIED"
        assert normalize_notification_status("delivered") == "DELIVERED"
        assert normalize_notification_status("UNKNOWN_VAL") == "SENT"

    def test_recipient_masking(self):
        masked_email = mask_recipient("operator@fieldops.com")
        assert masked_email.startswith("op***@fieldops.com")
        assert "operator@" not in masked_email

        masked_phone = mask_recipient("+14155551234")
        assert "***" in masked_phone
        assert "+1" in masked_phone
        assert "234" in masked_phone

        assert mask_recipient(None) is None


class TestAuthoritativeStoryAliases:
    """Verifies story aliases across all modules."""

    def test_aliases_in_broadcaster_module(self):
        assert SendNotificationUpdates == send_notification_updates
        assert Send_notification_updates == send_notification_updates
        assert broadcast_notification_update == send_notification_updates
        assert get_notification_update_status == send_notification_updates
        assert SendNotificationUpdates()["event"] == "notification.updated"

    def test_reexports_in_event_publisher(self):
        import app.services.event_publisher as ep
        assert hasattr(ep, "DEFAULT_NOTIFICATION_UPDATE_CONFIG")
        assert hasattr(ep, "send_notification_updates")
        assert hasattr(ep, "SendNotificationUpdates")
        assert hasattr(ep, "broadcast_notification_update")
        assert ep.SendNotificationUpdates()["status"] == "SENT"

    def test_reexports_in_redis_client(self):
        import app.redis_client as rc
        assert hasattr(rc, "DEFAULT_NOTIFICATION_UPDATE_CONFIG")
        assert hasattr(rc, "send_notification_updates")
        assert hasattr(rc, "SendNotificationUpdates")
        assert hasattr(rc, "broadcast_notification_update")
        assert rc.SendNotificationUpdates()["event"] == "notification.updated"

    def test_reexports_in_celery_app(self):
        import app.celery_app as ca
        assert hasattr(ca, "DEFAULT_NOTIFICATION_UPDATE_CONFIG")
        assert hasattr(ca, "send_notification_updates")
        assert hasattr(ca, "SendNotificationUpdates")
        assert hasattr(ca, "get_notification_update_config")
        assert ca.SendNotificationUpdates()["notification_id"] == "n-1"

    def test_reexports_and_task_in_tasks(self):
        import app.tasks as tasks
        assert hasattr(tasks, "send_notification_updates_task")
        assert hasattr(tasks, "SendNotificationUpdatesTask")
        assert hasattr(tasks, "Send_notification_updates_task")
        assert hasattr(tasks, "SendNotificationUpdates")
        res = tasks.send_notification_updates_task()
        assert res["notification_id"] == "n-1"
        assert res["event"] == "notification.updated"

    def test_reexports_in_notification_services(self):
        import app.services.notification_services as ns
        assert hasattr(ns, "DEFAULT_NOTIFICATION_UPDATE_CONFIG")
        assert hasattr(ns, "send_notification_updates")
        assert hasattr(ns, "SendNotificationUpdates")
        assert hasattr(ns, "broadcast_notification_update")


class TestValidationAndDeduplication:
    """Verifies validation and deduplication."""

    def test_validation_rejects_missing_mandatory_fields(self):
        broadcaster = NotificationUpdateBroadcaster()
        res1 = broadcaster.broadcast_notification_update(notification_id="", status="SENT", tenant_id="t1")
        assert res1["status_code"] == "REJECTED"

        res2 = broadcaster.broadcast_notification_update(notification_id="n-1", status="SENT", tenant_id="")
        assert res2["status_code"] == "REJECTED"

    def test_duplicate_notification_deduplication(self):
        mock_redis = MagicMock()
        mock_redis.get.return_value = b"1"  # Already seen

        broadcaster = NotificationUpdateBroadcaster(redis_client=mock_redis)
        res = broadcaster.broadcast_notification_update(
            notification_id="notif-dup",
            status="DELIVERED",
            tenant_id="tenant-dup",
            event_id="stable-dup-notif-id",
            redis_client=mock_redis,
        )

        assert res["event"] == "notification.updated"
        assert res["duplicate"] is True
        assert res["status_code"] == "SKIPPED"
        mock_redis.publish.assert_not_called()


class TestRedisPublishAndFailureSafety:
    """Verifies wire payload structure, PII masking, and bounded failure behavior."""

    def test_publish_payload_structure(self):
        mock_redis = MagicMock()
        mock_redis.get.return_value = None

        broadcaster = NotificationUpdateBroadcaster(redis_client=mock_redis)
        res = broadcaster.broadcast_notification_update(
            notification_id="notif-555",
            status="RETRIED",
            tenant_id="tenant-alpha",
            channel="SMS",
            recipient="+15551234567",
            recipient_id="usr-12",
            attempt=2,
            job_id="job-99",
            error_reason="carrier rate limited",
            provider_id="sid-tw-12345",
            correlation_id="corr-notif-1",
            timestamp="2026-11-23T17:30:00Z",
            redis_client=mock_redis,
        )

        assert res["event"] == "notification.updated"
        assert res["notification_id"] == "notif-555"
        assert res["status"] == "RETRIED"
        assert res["tenant_id"] == "tenant-alpha"

        assert mock_redis.publish.call_count >= 1
        calls = mock_redis.publish.call_args_list
        channels = [call[0][0] for call in calls]
        assert "dispatch_events" in channels

        raw_payload = calls[0][0][1]
        payload = json.loads(raw_payload)
        assert payload["event"] == "notification.updated"
        assert payload["tenant_id"] == "tenant-alpha"
        assert payload["notification_id"] == "notif-555"
        assert payload["status"] == "RETRIED"
        assert payload["channel"] == "SMS"
        assert payload["recipient_id"] == "usr-12"
        assert "***" in payload["recipient_masked"]
        assert payload["attempt"] == 2
        assert payload["job_id"] == "job-99"
        assert payload["error_reason"] == "carrier rate limited"
        assert payload["provider_id"] == "sid-tw-12345"
        assert payload["correlation_id"] == "corr-notif-1"
        assert payload["timestamp"] == "2026-11-23T17:30:00Z"
        # Ensure no sensitive content body exists
        assert "body" not in payload
        assert "password" not in payload

    def test_bounded_failure_safety_on_redis_error(self):
        mock_redis = MagicMock()
        mock_redis.publish.side_effect = ConnectionError("Redis down")

        broadcaster = NotificationUpdateBroadcaster(redis_client=mock_redis)
        res = broadcaster.broadcast_notification_update(
            notification_id="notif-fail",
            status="FAILED",
            tenant_id="tenant-fail",
            redis_client=mock_redis,
        )

        assert res["event"] == "notification.updated"
        assert res["notification_id"] == "notif-fail"
        assert res["tenant_id"] == "tenant-fail"
        assert res["status"] == "FAILED"


class TestChannelsMarkDeliveryIntegration:
    """Verifies mark_delivery safely invokes notification broadcaster."""

    def test_mark_delivery_triggers_notification_broadcaster(self):
        from app.services.ai.FieldOpsAI.agents.communication_agent.channels import mark_delivery
        from app.models_legacy import NotificationChannelDelivery

        mock_db = MagicMock()
        mock_delivery = MagicMock(spec=NotificationChannelDelivery)
        mock_delivery.notification_id = "notif-delivery-1"
        mock_delivery.tenant_id = "tenant-delivery"
        mock_delivery.channel = "email"
        mock_delivery.recipient_id = "recip-9"
        mock_delivery.attempts = 1

        mock_db.query.return_value.filter.return_value.first.return_value = mock_delivery

        with patch("app.services.notification_update_broadcaster.send_notification_updates") as mock_send:
            mark_delivery(
                db=mock_db,
                delivery_id=101,
                status="accepted",
                error=None,
                provider_id="prov-1",
            )
            mock_send.assert_called_once()
            call_kwargs = mock_send.call_args[1]
            assert call_kwargs["notification_id"] == "notif-delivery-1"
            assert call_kwargs["tenant_id"] == "tenant-delivery"
            assert call_kwargs["status"] == "accepted"


class TestAdminQueueRestEndpoints:
    """Verifies REST endpoints in admin_queue."""

    def test_admin_queue_status_and_config(self):
        from app.routes.admin_queue import (
            get_notification_updates_status_endpoint,
            get_notification_updates_config_endpoint,
            post_send_notification_update_endpoint,
        )

        status = get_notification_updates_status_endpoint()
        assert status["status"] == "READY"
        assert status["event"] == "notification.updated"

        config = get_notification_updates_config_endpoint()
        assert config["event"] == "notification.updated"
        assert "valid_statuses" in config

        mock_user = MagicMock()
        mock_user.tenant_id = "tenant-user-7"
        post_res = post_send_notification_update_endpoint(
            payload={"notification_id": "notif-api-1", "status": "QUEUED"},
            current_user=mock_user,
        )
        assert post_res["event"] == "notification.updated"
        assert post_res["notification_id"] == "notif-api-1"
        assert post_res["tenant_id"] == "tenant-user-7"
        assert post_res["status"] == "QUEUED"


class TestWebSocketServerIntegration:
    """Verifies ws-server/server.js contains notification update event and endpoints."""

    def test_ws_server_contains_notification_update_logic(self):
        server_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "ws-server", "server.js")
        )
        assert os.path.exists(server_path), f"ws-server/server.js not found at {server_path}"

        with open(server_path, "r", encoding="utf-8") as f:
            content = f.read()

        assert "notification.updated" in content
        assert "/notifications/config" in content
        assert "/notifications/broadcast" in content
        assert "notification_events" in content
