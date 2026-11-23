"""
test_kafka_to_websocket_bridge.py

Automated test suite for the story:
Connect Kafka events to WebSocket as an integrated part of the
WebSocket Real-Time Events capability.

Contract:
{
    "status": "CONNECTED",
    "transport": "KAFKA_TO_WEBSOCKET",
    "namespace": "/dispatch-dashboard"
}
"""

import json
import pytest
from unittest.mock import MagicMock, patch

from app.services.kafka_to_websocket_bridge import (
    DEFAULT_KAFKA_TO_WEBSOCKET_CONFIG,
    KAFKA_TO_WEBSOCKET_EVENT_MAP,
    KafkaToWebSocketBridge,
    bridge_kafka_event_to_websocket,
    connect_kafka_events_to_websocket,
    get_kafka_to_websocket_config,
    map_kafka_event_to_websocket,
    ConnectKafkaEventsToWebSocket,
    Connect_kafka_events_to_websocket,
    get_kafka_to_websocket_status,
    get_websocket_bridge_status,
)


class TestKafkaToWebSocketContract:
    """Verifies canonical JSON contract and configuration."""

    def test_canonical_contract(self):
        result = connect_kafka_events_to_websocket()
        assert isinstance(result, dict)
        assert result.get("status") == "CONNECTED"
        assert result.get("transport") == "KAFKA_TO_WEBSOCKET"
        assert result.get("namespace") == "/dispatch-dashboard"

    def test_default_config_constants(self):
        assert DEFAULT_KAFKA_TO_WEBSOCKET_CONFIG["status"] == "CONNECTED"
        assert DEFAULT_KAFKA_TO_WEBSOCKET_CONFIG["transport"] == "KAFKA_TO_WEBSOCKET"
        assert DEFAULT_KAFKA_TO_WEBSOCKET_CONFIG["namespace"] == "/dispatch-dashboard"

    def test_operational_config(self):
        config = get_kafka_to_websocket_config()
        assert config["status"] == "CONNECTED"
        assert config["transport"] == "KAFKA_TO_WEBSOCKET"
        assert config["namespace"] == "/dispatch-dashboard"
        assert config["source_of_truth"] == "POSTGRESQL"
        assert config["redis_channel"] == "dispatch_events"
        assert config["deduplication_ttl_seconds"] == 3600
        assert config["event_mappings_count"] > 10
        assert "job_assigned" in config["supported_event_types"]


class TestAuthoritativeStoryAliases:
    """Verifies authoritative story aliases across all modules."""

    def test_aliases_in_bridge_module(self):
        assert ConnectKafkaEventsToWebSocket == connect_kafka_events_to_websocket
        assert Connect_kafka_events_to_websocket == connect_kafka_events_to_websocket
        assert get_kafka_to_websocket_status == connect_kafka_events_to_websocket
        assert get_websocket_bridge_status == connect_kafka_events_to_websocket
        assert ConnectKafkaEventsToWebSocket()["status"] == "CONNECTED"

    def test_reexports_in_event_publisher(self):
        import app.services.event_publisher as ep
        assert hasattr(ep, "DEFAULT_KAFKA_TO_WEBSOCKET_CONFIG")
        assert hasattr(ep, "connect_kafka_events_to_websocket")
        assert hasattr(ep, "ConnectKafkaEventsToWebSocket")
        assert hasattr(ep, "bridge_kafka_event_to_websocket")
        assert hasattr(ep, "map_kafka_event_to_websocket")
        assert ep.ConnectKafkaEventsToWebSocket()["status"] == "CONNECTED"

    def test_reexports_in_redis_client(self):
        import app.redis_client as rc
        assert hasattr(rc, "DEFAULT_KAFKA_TO_WEBSOCKET_CONFIG")
        assert hasattr(rc, "connect_kafka_events_to_websocket")
        assert hasattr(rc, "ConnectKafkaEventsToWebSocket")
        assert hasattr(rc, "bridge_kafka_event_to_websocket")
        assert rc.ConnectKafkaEventsToWebSocket()["status"] == "CONNECTED"

    def test_reexports_in_celery_app(self):
        import app.celery_app as ca
        assert hasattr(ca, "DEFAULT_KAFKA_TO_WEBSOCKET_CONFIG")
        assert hasattr(ca, "connect_kafka_events_to_websocket")
        assert hasattr(ca, "ConnectKafkaEventsToWebSocket")
        assert hasattr(ca, "get_kafka_to_websocket_status")
        assert ca.ConnectKafkaEventsToWebSocket()["status"] == "CONNECTED"

    def test_reexports_and_task_in_tasks(self):
        import app.tasks as tasks
        assert hasattr(tasks, "connect_kafka_events_to_websocket_task")
        assert hasattr(tasks, "ConnectKafkaEventsToWebSocketTask")
        assert hasattr(tasks, "Connect_kafka_events_to_websocket_task")
        assert hasattr(tasks, "ConnectKafkaEventsToWebSocket")
        task_res = tasks.connect_kafka_events_to_websocket_task()
        assert task_res["status"] == "CONNECTED"
        assert task_res["transport"] == "KAFKA_TO_WEBSOCKET"


class TestKafkaEventTypeMapping:
    """Verifies Kafka event names mapping to canonical WebSocket event names."""

    def test_job_lifecycle_mappings(self):
        assert map_kafka_event_to_websocket("job-assigned") == "job_assigned"
        assert map_kafka_event_to_websocket("job_assigned") == "job_assigned"
        assert map_kafka_event_to_websocket("job-completed") == "job_completed"
        assert map_kafka_event_to_websocket("job_completed") == "job_completed"
        assert map_kafka_event_to_websocket("job-status-transition") == "status_transition"
        assert map_kafka_event_to_websocket("re-dispatch") == "redispatch"

    def test_gps_and_location_mappings(self):
        assert map_kafka_event_to_websocket("gps-location-updated") == "technician_location_updated"
        assert map_kafka_event_to_websocket("technician_location_updated") == "technician_location_updated"

    def test_billing_and_sla_mappings(self):
        assert map_kafka_event_to_websocket("invoice-paid") == "invoice_paid"
        assert map_kafka_event_to_websocket("payment-completed") == "payment_completed"
        assert map_kafka_event_to_websocket("sla-breached") == "sla_breached"
        assert map_kafka_event_to_websocket("notification-sent") == "notification_sent"

    def test_fallback_and_edge_cases(self):
        assert map_kafka_event_to_websocket("custom-domain-event") == "custom_domain_event"
        assert map_kafka_event_to_websocket("") == "generic_event"
        assert map_kafka_event_to_websocket(None) == "generic_event"


class TestEventBridgingAndDeduplication:
    """Verifies event bridging, tenant isolation, and deduplication logic."""

    def test_bridge_event_success_and_redis_publish(self):
        mock_redis = MagicMock()
        mock_redis.get.return_value = None  # Not duplicate

        bridge = KafkaToWebSocketBridge(redis_client=mock_redis)
        event_payload = {
            "event_id": "evt-kafka-101",
            "event_type": "job-assigned",
            "tenant_id": "tenant-alpha",
            "correlation_id": "corr-xyz",
            "job_id": "job-555",
            "technician_id": "tech-777",
            "timestamp": "2026-11-23T12:00:00Z",
            "payload": {"details": "assigned to Bob"},
        }

        res = bridge.bridge_event(event_payload, redis_client=mock_redis)
        assert res["status"] == "CONNECTED"
        assert res["transport"] == "KAFKA_TO_WEBSOCKET"
        assert res["namespace"] == "/dispatch-dashboard"
        assert res["event_name"] == "job_assigned"
        assert res["event_id"] == "evt-kafka-101"
        assert res["tenant_id"] == "tenant-alpha"
        assert res["delivered"] is True

        # Check Redis publish was called
        mock_redis.publish.assert_called_once()
        published_channel, published_msg = mock_redis.publish.call_args[0]
        assert published_channel == "dispatch_events"

        msg_data = json.loads(published_msg)
        assert msg_data["event"] == "job_assigned"
        assert msg_data["event_type"] == "job_assigned"
        assert msg_data["event_id"] == "evt-kafka-101"
        assert msg_data["tenant_id"] == "tenant-alpha"
        assert msg_data["correlation_id"] == "corr-xyz"
        assert msg_data["job_id"] == "job-555"
        assert msg_data["technician_id"] == "tech-777"
        assert msg_data["timestamp"] == "2026-11-23T12:00:00Z"

        # Check idempotency marker set
        mock_redis.set.assert_called_once()
        set_key = mock_redis.set.call_args[0][0]
        assert set_key == "ws:event_broadcasted:evt-kafka-101"

    def test_bridge_event_duplicate_deduplication(self):
        mock_redis = MagicMock()
        mock_redis.get.return_value = b"1"  # Already seen

        bridge = KafkaToWebSocketBridge(redis_client=mock_redis)
        event_payload = {
            "event_id": "evt-kafka-duplicate",
            "event_type": "job-completed",
            "tenant_id": "tenant-beta",
        }

        res = bridge.bridge_event(event_payload, redis_client=mock_redis)
        assert res["status"] == "SKIPPED"
        assert res["idempotent_duplicate"] is True
        assert res["event_id"] == "evt-kafka-duplicate"
        assert res["tenant_id"] == "tenant-beta"
        assert res["namespace"] == "/dispatch-dashboard"

        # Verify publish was skipped
        mock_redis.publish.assert_not_called()

    def test_bridge_event_validation_missing_fields(self):
        bridge = KafkaToWebSocketBridge()
        # Missing tenant_id
        res1 = bridge.bridge_event({"event_id": "e1", "event_type": "test"})
        assert res1["status"] == "REJECTED"

        # Missing event_id
        res2 = bridge.bridge_event({"tenant_id": "t1", "event_type": "test"})
        assert res2["status"] == "REJECTED"

        # Invalid format
        res3 = bridge.bridge_event(12345)
        assert res3["status"] == "FAILED"

    def test_bridge_event_string_json_parsing(self):
        mock_redis = MagicMock()
        mock_redis.get.return_value = None

        raw_json = json.dumps({
            "event_id": "evt-str-1",
            "event_type": "gps-location-updated",
            "tenant_id": "tenant-gamma",
            "payload": {"lat": 37.77, "lng": -122.41},
        })

        res = bridge_kafka_event_to_websocket(raw_json, redis_client=mock_redis)
        assert res["status"] == "CONNECTED"
        assert res["event_name"] == "technician_location_updated"
        assert res["tenant_id"] == "tenant-gamma"


class TestAdminQueueApiEndpoints:
    """Verifies FastAPI REST endpoints for the Kafka-to-WebSocket bridge."""

    def test_status_endpoint(self):
        from app.routes.admin_queue import get_websocket_kafka_bridge_status_endpoint
        res = get_websocket_kafka_bridge_status_endpoint()
        assert res["status"] == "CONNECTED"
        assert res["transport"] == "KAFKA_TO_WEBSOCKET"
        assert res["namespace"] == "/dispatch-dashboard"

    def test_config_endpoint(self):
        from app.routes.admin_queue import get_websocket_kafka_bridge_config_endpoint
        res = get_websocket_kafka_bridge_config_endpoint()
        assert res["status"] == "CONNECTED"
        assert res["transport"] == "KAFKA_TO_WEBSOCKET"
        assert res["namespace"] == "/dispatch-dashboard"
        assert res["source_of_truth"] == "POSTGRESQL"

    def test_connect_endpoint(self):
        from app.routes.admin_queue import post_connect_websocket_kafka_bridge_endpoint
        res = post_connect_websocket_kafka_bridge_endpoint()
        assert res["status"] == "CONNECTED"
        assert res["transport"] == "KAFKA_TO_WEBSOCKET"
        assert res["namespace"] == "/dispatch-dashboard"

    def test_publish_endpoint(self):
        from types import SimpleNamespace
        from app.routes.admin_queue import post_bridge_kafka_event_endpoint

        mock_user = SimpleNamespace(tenant_id="tenant-api")
        payload = {
            "event_id": "evt-api-1",
            "event_type": "job-assigned",
        }

        with patch("app.services.kafka_to_websocket_bridge.get_redis_client") as mock_get_rc:
            mock_rc = MagicMock()
            mock_rc.get.return_value = None
            mock_get_rc.return_value = mock_rc

            res = post_bridge_kafka_event_endpoint(payload, current_user=mock_user)
            assert res["status"] == "CONNECTED"
            assert res["event_id"] == "evt-api-1"
            assert res["tenant_id"] == "tenant-api"


class TestWebSocketServerBridgeContract:
    """Verifies ws-server/server.js adheres to the contract and deduplication."""

    def test_server_js_contains_bridge_endpoints(self):
        import os
        candidates = [
            "ws-server/server.js",
            "../ws-server/server.js",
            os.path.join(os.path.dirname(__file__), "../../ws-server/server.js"),
        ]
        target_path = next((p for p in candidates if os.path.exists(p)), None)
        assert target_path is not None, f"Could not locate ws-server/server.js from candidates: {candidates}"
        with open(target_path, "r", encoding="utf-8") as f:
            content = f.read()

        assert "/bridge/status" in content
        assert "/bridge/kafka-event" in content
        assert "isDuplicateEvent" in content
        assert "KAFKA_TO_WEBSOCKET" in content
        assert "/dispatch-dashboard" in content

