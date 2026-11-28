"""
test_send_payment_updates.py

Automated test suite for the story:
Send payment updates as an integrated part of the
WebSocket Real-Time Events capability.

Expected Output (JSON):
{
    "event": "payment.updated",
    "tenant_id": "tenant-1",
    "invoice_id": "10001",
    "payment_status": "PAID"
}
"""

import json
import os
import pytest
from unittest.mock import MagicMock, patch

from app.services.payment_update_broadcaster import (
    DEFAULT_PAYMENT_UPDATE_CONFIG,
    VALID_PAYMENT_STATUSES,
    PaymentUpdateBroadcaster,
    broadcast_payment_update,
    get_payment_update_config,
    get_payment_update_status,
    normalize_payment_status,
    send_payment_updates,
    SendPaymentUpdates,
    Send_payment_updates,
)


class TestPaymentUpdateContract:
    """Verifies canonical JSON contract and configuration."""

    def test_canonical_contract_defaults(self):
        result = send_payment_updates()
        assert isinstance(result, dict)
        assert result.get("event") == "payment.updated"
        assert result.get("tenant_id") == "tenant-1"
        assert result.get("invoice_id") == "10001"
        assert result.get("payment_status") == "PAID"

    def test_canonical_contract_custom_values(self):
        result = send_payment_updates(
            invoice_id="inv-9999",
            payment_status="REFUNDED",
            tenant_id="tenant-custom",
        )
        assert result["event"] == "payment.updated"
        assert result["tenant_id"] == "tenant-custom"
        assert result["invoice_id"] == "inv-9999"
        assert result["payment_status"] == "REFUNDED"

    def test_default_config_constants(self):
        assert DEFAULT_PAYMENT_UPDATE_CONFIG["event"] == "payment.updated"
        assert DEFAULT_PAYMENT_UPDATE_CONFIG["tenant_id"] == "tenant-1"
        assert DEFAULT_PAYMENT_UPDATE_CONFIG["invoice_id"] == "10001"
        assert DEFAULT_PAYMENT_UPDATE_CONFIG["payment_status"] == "PAID"

    def test_valid_statuses_set(self):
        assert "PAID" in VALID_PAYMENT_STATUSES
        assert "PENDING" in VALID_PAYMENT_STATUSES
        assert "FAILED" in VALID_PAYMENT_STATUSES
        assert "REFUNDED" in VALID_PAYMENT_STATUSES
        assert "PARTIALLY_REFUNDED" in VALID_PAYMENT_STATUSES
        assert "CANCELLED" in VALID_PAYMENT_STATUSES
        assert "AUTHORIZED" in VALID_PAYMENT_STATUSES

    def test_operational_config(self):
        config = get_payment_update_config()
        assert config["event"] == "payment.updated"
        assert config["transport"] == "REDIS_PUB_SUB_TO_WEBSOCKET"
        assert config["namespace"] == "/dispatch-dashboard"
        assert config["source_of_truth"] == "POSTGRESQL"
        assert config["redis_channel"] == "dispatch_events"
        assert config["payment_channel"] == "payment_events"
        assert config["deduplication_ttl_seconds"] == 3600
        assert "PAID" in config["valid_statuses"]
        assert config["default_contract"]["payment_status"] == "PAID"


class TestStatusNormalization:
    """Verifies status normalization."""

    def test_status_normalization(self):
        assert normalize_payment_status("paid") == "PAID"
        assert normalize_payment_status("successful") == "PAID"
        assert normalize_payment_status("success") == "PAID"
        assert normalize_payment_status("pending") == "PENDING"
        assert normalize_payment_status("initiated") == "PENDING"
        assert normalize_payment_status("failed") == "FAILED"
        assert normalize_payment_status("declined") == "FAILED"
        assert normalize_payment_status("refunded") == "REFUNDED"
        assert normalize_payment_status("partially_refunded") == "PARTIALLY_REFUNDED"
        assert normalize_payment_status("cancelled") == "CANCELLED"
        assert normalize_payment_status("unknown_val") == "PAID"


class TestAuthoritativeStoryAliases:
    """Verifies story aliases across all modules."""

    def test_aliases_in_broadcaster_module(self):
        assert SendPaymentUpdates == send_payment_updates
        assert Send_payment_updates == send_payment_updates
        assert broadcast_payment_update == send_payment_updates
        assert get_payment_update_status == send_payment_updates
        assert SendPaymentUpdates()["event"] == "payment.updated"

    def test_reexports_in_event_publisher(self):
        import app.services.event_publisher as ep
        assert hasattr(ep, "DEFAULT_PAYMENT_UPDATE_CONFIG")
        assert hasattr(ep, "send_payment_updates")
        assert hasattr(ep, "SendPaymentUpdates")
        assert hasattr(ep, "broadcast_payment_update")
        assert ep.SendPaymentUpdates()["payment_status"] == "PAID"

    def test_reexports_in_redis_client(self):
        import app.redis_client as rc
        assert hasattr(rc, "DEFAULT_PAYMENT_UPDATE_CONFIG")
        assert hasattr(rc, "send_payment_updates")
        assert hasattr(rc, "SendPaymentUpdates")
        assert hasattr(rc, "broadcast_payment_update")
        assert rc.SendPaymentUpdates()["event"] == "payment.updated"

    def test_reexports_in_celery_app(self):
        import app.celery_app as ca
        assert hasattr(ca, "DEFAULT_PAYMENT_UPDATE_CONFIG")
        assert hasattr(ca, "send_payment_updates")
        assert hasattr(ca, "SendPaymentUpdates")
        assert hasattr(ca, "get_payment_update_config")
        assert ca.SendPaymentUpdates()["invoice_id"] == "10001"

    def test_reexports_and_task_in_tasks(self):
        import app.tasks as tasks
        assert hasattr(tasks, "send_payment_updates_task")
        assert hasattr(tasks, "SendPaymentUpdatesTask")
        assert hasattr(tasks, "Send_payment_updates_task")
        assert hasattr(tasks, "SendPaymentUpdates")
        res = tasks.send_payment_updates_task()
        assert res["invoice_id"] == "10001"
        assert res["event"] == "payment.updated"


class TestValidationAndDeduplication:
    """Verifies validation and deduplication."""

    def test_validation_rejects_missing_mandatory_fields(self):
        broadcaster = PaymentUpdateBroadcaster()
        res1 = broadcaster.broadcast_payment_update(invoice_id="", payment_status="PAID", tenant_id="t1")
        assert res1["status_code"] == "REJECTED"

        res2 = broadcaster.broadcast_payment_update(invoice_id="10001", payment_status="PAID", tenant_id="")
        assert res2["status_code"] == "REJECTED"

    def test_duplicate_payment_deduplication(self):
        mock_redis = MagicMock()
        mock_redis.get.return_value = b"1"  # Already seen

        broadcaster = PaymentUpdateBroadcaster(redis_client=mock_redis)
        res = broadcaster.broadcast_payment_update(
            invoice_id="inv-dup",
            payment_status="PAID",
            tenant_id="tenant-dup",
            event_id="stable-dup-pay-id",
            redis_client=mock_redis,
        )

        assert res["event"] == "payment.updated"
        assert res["duplicate"] is True
        assert res["status_code"] == "SKIPPED"
        mock_redis.publish.assert_not_called()


class TestRedisPublishAndFailureSafety:
    """Verifies wire payload structure, secrets exclusion, and bounded failure behavior."""

    def test_publish_payload_structure(self):
        mock_redis = MagicMock()
        mock_redis.get.return_value = None

        broadcaster = PaymentUpdateBroadcaster(redis_client=mock_redis)
        res = broadcaster.broadcast_payment_update(
            invoice_id="inv-777",
            payment_status="PAID",
            tenant_id="tenant-fin",
            payment_id="pay-456",
            job_id="job-123",
            customer_id="cust-88",
            amount=149.99,
            currency="USD",
            payment_method="CARD",
            correlation_id="corr-pay-1",
            timestamp="2026-11-23T17:40:00Z",
            redis_client=mock_redis,
        )

        assert res["event"] == "payment.updated"
        assert res["invoice_id"] == "inv-777"
        assert res["payment_status"] == "PAID"
        assert res["tenant_id"] == "tenant-fin"

        assert mock_redis.publish.call_count >= 1
        calls = mock_redis.publish.call_args_list
        channels = [call[0][0] for call in calls]
        assert "dispatch_events" in channels

        raw_payload = calls[0][0][1]
        payload = json.loads(raw_payload)
        assert payload["event"] == "payment.updated"
        assert payload["tenant_id"] == "tenant-fin"
        assert payload["invoice_id"] == "inv-777"
        assert payload["payment_status"] == "PAID"
        assert payload["payment_id"] == "pay-456"
        assert payload["job_id"] == "job-123"
        assert payload["customer_id"] == "cust-88"
        assert payload["amount"] == 149.99
        assert payload["currency"] == "USD"
        assert payload["payment_method"] == "CARD"
        assert payload["correlation_id"] == "corr-pay-1"
        assert payload["timestamp"] == "2026-11-23T17:40:00Z"
        # Ensure no payment credentials or CVV are exposed
        assert "cvv" not in payload
        assert "card_number" not in payload
        assert "pan" not in payload
        assert "secret" not in payload

    def test_bounded_failure_safety_on_redis_error(self):
        mock_redis = MagicMock()
        mock_redis.publish.side_effect = ConnectionError("Redis down")

        broadcaster = PaymentUpdateBroadcaster(redis_client=mock_redis)
        res = broadcaster.broadcast_payment_update(
            invoice_id="inv-fail",
            payment_status="FAILED",
            tenant_id="tenant-fail",
            redis_client=mock_redis,
        )

        assert res["event"] == "payment.updated"
        assert res["invoice_id"] == "inv-fail"
        assert res["tenant_id"] == "tenant-fail"
        assert res["payment_status"] == "FAILED"


class TestEventPublisherIntegration:
    """Verifies publish_payment_event triggers payment update broadcast."""

    def test_publish_payment_event_triggers_broadcaster(self):
        from app.services.event_publisher import publish_payment_event

        mock_redis = MagicMock()
        mock_redis.get.return_value = None

        publish_payment_event(
            redis_client=mock_redis,
            event_type="PAYMENT_SUCCESSFUL",
            job_id="job-pay-ep",
            tenant_id="tenant-pay-ep",
            payment_id="pay-ep-1",
            payload={"amount": 200, "status": "PAID", "invoice_id": "inv-ep-1"},
        )

        assert mock_redis.publish.call_count >= 1


class TestAdminQueueRestEndpoints:
    """Verifies REST endpoints in admin_queue."""

    def test_admin_queue_status_and_config(self):
        from app.routes.admin_queue import (
            get_payment_updates_status_endpoint,
            get_payment_updates_config_endpoint,
            post_send_payment_update_endpoint,
        )

        status = get_payment_updates_status_endpoint()
        assert status["status"] == "READY"
        assert status["event"] == "payment.updated"

        config = get_payment_updates_config_endpoint()
        assert config["event"] == "payment.updated"
        assert "valid_statuses" in config

        mock_user = MagicMock()
        mock_user.tenant_id = "tenant-user-pay"
        post_res = post_send_payment_update_endpoint(
            payload={"invoice_id": "inv-api-1", "payment_status": "PAID"},
            current_user=mock_user,
        )
        assert post_res["event"] == "payment.updated"
        assert post_res["invoice_id"] == "inv-api-1"
        assert post_res["tenant_id"] == "tenant-user-pay"
        assert post_res["payment_status"] == "PAID"


class TestWebSocketServerIntegration:
    """Verifies ws-server/server.js contains payment update event and endpoints."""

    def test_ws_server_contains_payment_update_logic(self):
        server_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "ws-server", "server.js")
        )
        assert os.path.exists(server_path), f"ws-server/server.js not found at {server_path}"

        with open(server_path, "r", encoding="utf-8") as f:
            content = f.read()

        assert "payment.updated" in content
        assert "/payments/config" in content
        assert "/payments/broadcast" in content
        assert "payment_events" in content
