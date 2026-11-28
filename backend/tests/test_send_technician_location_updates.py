"""
test_send_technician_location_updates.py

Automated test suite for the story:
Send technician location updates as an integrated part of the
WebSocket Real-Time Events capability.

Expected Output (JSON):
{
    "event": "technician.location.updated",
    "tenant_id": "tenant-1",
    "technician_id": "tech-1",
    "latitude": 0.0,
    "longitude": 0.0
}
"""

import json
import os
import pytest
from unittest.mock import MagicMock, patch

from app.services.technician_location_broadcaster import (
    DEFAULT_TECHNICIAN_LOCATION_CONFIG,
    TechnicianLocationBroadcaster,
    broadcast_technician_location_update,
    get_technician_location_config,
    get_technician_location_status,
    send_technician_location_updates,
    SendTechnicianLocationUpdates,
    Send_technician_location_updates,
)


class TestTechnicianLocationContract:
    """Verifies canonical JSON contract and configuration."""

    def test_canonical_contract_defaults(self):
        result = send_technician_location_updates()
        assert isinstance(result, dict)
        assert result.get("event") == "technician.location.updated"
        assert result.get("tenant_id") == "tenant-1"
        assert result.get("technician_id") == "tech-1"
        assert result.get("latitude") == 0.0
        assert result.get("longitude") == 0.0

    def test_canonical_contract_custom_values(self):
        result = send_technician_location_updates(
            technician_id="tech-42",
            latitude=37.7749,
            longitude=-122.4194,
            tenant_id="tenant-sf",
        )
        assert result["event"] == "technician.location.updated"
        assert result["tenant_id"] == "tenant-sf"
        assert result["technician_id"] == "tech-42"
        assert result["latitude"] == 37.7749
        assert result["longitude"] == -122.4194

    def test_default_config_constants(self):
        assert DEFAULT_TECHNICIAN_LOCATION_CONFIG["event"] == "technician.location.updated"
        assert DEFAULT_TECHNICIAN_LOCATION_CONFIG["tenant_id"] == "tenant-1"
        assert DEFAULT_TECHNICIAN_LOCATION_CONFIG["technician_id"] == "tech-1"
        assert DEFAULT_TECHNICIAN_LOCATION_CONFIG["latitude"] == 0.0
        assert DEFAULT_TECHNICIAN_LOCATION_CONFIG["longitude"] == 0.0

    def test_operational_config(self):
        config = get_technician_location_config()
        assert config["event"] == "technician.location.updated"
        assert config["transport"] == "REDIS_PUB_SUB_TO_WEBSOCKET"
        assert config["namespace"] == "/dispatch-dashboard"
        assert config["source_of_truth"] == "POSTGRESQL"
        assert config["redis_channel"] == "dispatch_events"
        assert config["throttle_window_seconds"] == 2
        assert config["default_contract"]["technician_id"] == "tech-1"


class TestAuthoritativeStoryAliases:
    """Verifies story aliases across all modules."""

    def test_aliases_in_broadcaster_module(self):
        assert SendTechnicianLocationUpdates == send_technician_location_updates
        assert Send_technician_location_updates == send_technician_location_updates
        assert broadcast_technician_location_update == send_technician_location_updates
        assert get_technician_location_status == send_technician_location_updates
        assert SendTechnicianLocationUpdates()["event"] == "technician.location.updated"

    def test_reexports_in_event_publisher(self):
        import app.services.event_publisher as ep
        assert hasattr(ep, "DEFAULT_TECHNICIAN_LOCATION_CONFIG")
        assert hasattr(ep, "send_technician_location_updates")
        assert hasattr(ep, "SendTechnicianLocationUpdates")
        assert hasattr(ep, "broadcast_technician_location_update")
        assert ep.SendTechnicianLocationUpdates()["technician_id"] == "tech-1"

    def test_reexports_in_redis_client(self):
        import app.redis_client as rc
        assert hasattr(rc, "DEFAULT_TECHNICIAN_LOCATION_CONFIG")
        assert hasattr(rc, "send_technician_location_updates")
        assert hasattr(rc, "SendTechnicianLocationUpdates")
        assert hasattr(rc, "broadcast_technician_location_update")
        assert rc.SendTechnicianLocationUpdates()["event"] == "technician.location.updated"

    def test_reexports_in_celery_app(self):
        import app.celery_app as ca
        assert hasattr(ca, "DEFAULT_TECHNICIAN_LOCATION_CONFIG")
        assert hasattr(ca, "send_technician_location_updates")
        assert hasattr(ca, "SendTechnicianLocationUpdates")
        assert hasattr(ca, "get_technician_location_config")
        assert ca.SendTechnicianLocationUpdates()["technician_id"] == "tech-1"

    def test_reexports_and_task_in_tasks(self):
        import app.tasks as tasks
        assert hasattr(tasks, "send_technician_location_updates_task")
        assert hasattr(tasks, "SendTechnicianLocationUpdatesTask")
        assert hasattr(tasks, "Send_technician_location_updates_task")
        assert hasattr(tasks, "SendTechnicianLocationUpdates")
        res = tasks.send_technician_location_updates_task()
        assert res["technician_id"] == "tech-1"
        assert res["event"] == "technician.location.updated"


class TestValidationAndThrottling:
    """Verifies coordinate validation and high-frequency stream backpressure."""

    def test_validation_rejects_out_of_bounds_coordinates(self):
        broadcaster = TechnicianLocationBroadcaster()
        res1 = broadcaster.broadcast_location_update(latitude=95.0, longitude=0.0)
        assert res1["status_code"] == "REJECTED"

        res2 = broadcaster.broadcast_location_update(latitude=0.0, longitude=-185.0)
        assert res2["status_code"] == "REJECTED"

        res3 = broadcaster.broadcast_location_update(latitude="invalid", longitude=0.0)
        assert res3["status_code"] == "REJECTED"

    def test_validation_rejects_missing_mandatory_ids(self):
        broadcaster = TechnicianLocationBroadcaster()
        res1 = broadcaster.broadcast_location_update(technician_id="", tenant_id="t1")
        assert res1["status_code"] == "REJECTED"

        res2 = broadcaster.broadcast_location_update(technician_id="tech-1", tenant_id="")
        assert res2["status_code"] == "REJECTED"

    def test_backpressure_throttles_high_frequency_stream(self):
        mock_redis = MagicMock()
        mock_redis.get.return_value = b"1"  # Already throttled in window

        broadcaster = TechnicianLocationBroadcaster(redis_client=mock_redis)
        res = broadcaster.broadcast_location_update(
            technician_id="tech-fast",
            latitude=12.9716,
            longitude=77.5946,
            tenant_id="tenant-blr",
            redis_client=mock_redis,
        )

        assert res["status_code"] == "THROTTLED"
        assert res["throttled"] is True
        mock_redis.publish.assert_not_called()

    def test_force_broadcast_bypasses_throttle(self):
        mock_redis = MagicMock()
        mock_redis.get.return_value = b"1"  # Throttled, but bypass forced

        broadcaster = TechnicianLocationBroadcaster(redis_client=mock_redis)
        res = broadcaster.broadcast_location_update(
            technician_id="tech-fast",
            latitude=12.9716,
            longitude=77.5946,
            tenant_id="tenant-blr",
            force_broadcast=True,
            redis_client=mock_redis,
        )

        assert res.get("status_code") != "THROTTLED"
        mock_redis.publish.assert_called_once()


class TestRedisPublishAndFailureSafety:
    """Verifies wire payload structure and bounded failure behavior."""

    def test_publish_payload_structure(self):
        mock_redis = MagicMock()
        mock_redis.get.return_value = None

        broadcaster = TechnicianLocationBroadcaster(redis_client=mock_redis)
        res = broadcaster.broadcast_location_update(
            technician_id="tech-99",
            latitude=40.7128,
            longitude=-74.0060,
            tenant_id="tenant-nyc",
            job_id="job-100",
            accuracy=5.2,
            altitude=15.0,
            speed=12.5,
            heading=180.0,
            correlation_id="corr-gps-1",
            timestamp="2026-11-23T16:00:00Z",
            redis_client=mock_redis,
        )

        assert res["event"] == "technician.location.updated"
        assert res["technician_id"] == "tech-99"
        assert res["latitude"] == 40.7128
        assert res["longitude"] == -74.0060

        mock_redis.publish.assert_called_once()
        channel, raw_payload = mock_redis.publish.call_args[0]
        assert channel == "dispatch_events"

        payload = json.loads(raw_payload)
        assert payload["event"] == "technician.location.updated"
        assert payload["tenant_id"] == "tenant-nyc"
        assert payload["technician_id"] == "tech-99"
        assert payload["latitude"] == 40.7128
        assert payload["longitude"] == -74.0060
        assert payload["job_id"] == "job-100"
        assert payload["accuracy"] == 5.2
        assert payload["altitude"] == 15.0
        assert payload["speed"] == 12.5
        assert payload["heading"] == 180.0
        assert payload["correlation_id"] == "corr-gps-1"
        assert payload["timestamp"] == "2026-11-23T16:00:00Z"

    def test_bounded_failure_safety_on_redis_error(self):
        mock_redis = MagicMock()
        mock_redis.publish.side_effect = ConnectionError("Redis down")

        broadcaster = TechnicianLocationBroadcaster(redis_client=mock_redis)
        res = broadcaster.broadcast_location_update(
            technician_id="tech-err",
            latitude=51.5074,
            longitude=-0.1278,
            tenant_id="tenant-lon",
            redis_client=mock_redis,
        )

        # Bounded failure: does not raise, returns contract
        assert res["event"] == "technician.location.updated"
        assert res["technician_id"] == "tech-err"
        assert res["latitude"] == 51.5074


class TestAdminQueueEndpoints:
    """Verifies FastAPI REST endpoints."""

    def test_status_endpoint(self):
        from app.routes.admin_queue import get_technician_location_status_endpoint
        res = get_technician_location_status_endpoint()
        assert res["event"] == "technician.location.updated"
        assert res["technician_id"] == "tech-1"
        assert res["latitude"] == 0.0

    def test_config_endpoint(self):
        from app.routes.admin_queue import get_technician_location_config_endpoint
        res = get_technician_location_config_endpoint()
        assert res["event"] == "technician.location.updated"
        assert res["namespace"] == "/dispatch-dashboard"
        assert res["throttle_window_seconds"] == 2

    def test_send_endpoint(self):
        from types import SimpleNamespace
        from app.routes.admin_queue import post_send_technician_location_update_endpoint

        mock_user = SimpleNamespace(tenant_id="tenant-loc-user")
        payload = {
            "technician_id": "tech-endpoint",
            "latitude": 13.0827,
            "longitude": 80.2707,
            "force_broadcast": True,
        }

        with patch("app.services.technician_location_broadcaster.get_redis_client") as mock_get_rc:
            mock_rc = MagicMock()
            mock_rc.get.return_value = None
            mock_get_rc.return_value = mock_rc

            res = post_send_technician_location_update_endpoint(payload, current_user=mock_user)
            assert res["event"] == "technician.location.updated"
            assert res["technician_id"] == "tech-endpoint"
            assert res["latitude"] == 13.0827
            assert res["tenant_id"] == "tenant-loc-user"


class TestWebSocketServerContractAndFrontend:
    """Verifies ws-server/server.js and frontend services."""

    def test_server_js_contains_location_endpoints(self):
        candidates = [
            "ws-server/server.js",
            "../ws-server/server.js",
            os.path.join(os.path.dirname(__file__), "../../ws-server/server.js"),
        ]
        target_path = next((p for p in candidates if os.path.exists(p)), None)
        assert target_path is not None, f"Could not locate ws-server/server.js from: {candidates}"
        with open(target_path, "r", encoding="utf-8") as f:
            content = f.read()

        assert "/technician-location/config" in content
        assert "/technician-location/broadcast" in content
        assert "technician.location.updated" in content

    def test_frontend_gps_service_contains_listener(self):
        candidates = [
            "frontend/src/services/gpsService.ts",
            "../frontend/src/services/gpsService.ts",
            os.path.join(os.path.dirname(__file__), "../../frontend/src/services/gpsService.ts"),
        ]
        target_path = next((p for p in candidates if os.path.exists(p)), None)
        assert target_path is not None, f"Could not locate gpsService.ts from: {candidates}"
        with open(target_path, "r", encoding="utf-8") as f:
            content = f.read()

        assert "TechnicianLocationUpdatePayload" in content
        assert "subscribeTechnicianLocationUpdates" in content
        assert "technician.location.updated" in content
