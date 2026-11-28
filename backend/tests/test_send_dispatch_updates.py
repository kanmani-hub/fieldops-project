"""
test_send_dispatch_updates.py

Automated test suite for the story:
Send dispatch updates as an integrated part of the
WebSocket Real-Time Events capability.

Expected Output (JSON):
{
    "event": "dispatch.updated",
    "tenant_id": "tenant-1",
    "job_id": "123",
    "assignment_id": "456"
}
"""

import json
import os
import pytest
from unittest.mock import MagicMock, patch

from app.services.dispatch_update_broadcaster import (
    DEFAULT_DISPATCH_UPDATE_CONFIG,
    DispatchUpdateBroadcaster,
    broadcast_dispatch_update,
    get_dispatch_update_config,
    get_dispatch_update_status,
    send_dispatch_updates,
    SendDispatchUpdates,
    Send_dispatch_updates,
)


class TestDispatchUpdateContract:
    """Verifies canonical JSON contract and configuration."""

    def test_canonical_contract_defaults(self):
        result = send_dispatch_updates()
        assert isinstance(result, dict)
        assert result.get("event") == "dispatch.updated"
        assert result.get("tenant_id") == "tenant-1"
        assert result.get("job_id") == "123"
        assert result.get("assignment_id") == "456"

    def test_canonical_contract_custom_values(self):
        result = send_dispatch_updates(
            job_id="999",
            assignment_id="888",
            tenant_id="tenant-custom",
        )
        assert result["event"] == "dispatch.updated"
        assert result["tenant_id"] == "tenant-custom"
        assert result["job_id"] == "999"
        assert result["assignment_id"] == "888"

    def test_default_config_constants(self):
        assert DEFAULT_DISPATCH_UPDATE_CONFIG["event"] == "dispatch.updated"
        assert DEFAULT_DISPATCH_UPDATE_CONFIG["tenant_id"] == "tenant-1"
        assert DEFAULT_DISPATCH_UPDATE_CONFIG["job_id"] == "123"
        assert DEFAULT_DISPATCH_UPDATE_CONFIG["assignment_id"] == "456"

    def test_operational_config(self):
        config = get_dispatch_update_config()
        assert config["event"] == "dispatch.updated"
        assert config["transport"] == "REDIS_PUB_SUB_TO_WEBSOCKET"
        assert config["namespace"] == "/dispatch-dashboard"
        assert config["source_of_truth"] == "POSTGRESQL"
        assert config["redis_channel"] == "dispatch_events"
        assert config["deduplication_ttl_seconds"] == 3600
        assert config["default_contract"]["assignment_id"] == "456"


class TestAuthoritativeStoryAliases:
    """Verifies story aliases across all modules."""

    def test_aliases_in_broadcaster_module(self):
        assert SendDispatchUpdates == send_dispatch_updates
        assert Send_dispatch_updates == send_dispatch_updates
        assert broadcast_dispatch_update == send_dispatch_updates
        assert get_dispatch_update_status == send_dispatch_updates
        assert SendDispatchUpdates()["event"] == "dispatch.updated"

    def test_reexports_in_event_publisher(self):
        import app.services.event_publisher as ep
        assert hasattr(ep, "DEFAULT_DISPATCH_UPDATE_CONFIG")
        assert hasattr(ep, "send_dispatch_updates")
        assert hasattr(ep, "SendDispatchUpdates")
        assert hasattr(ep, "broadcast_dispatch_update")
        assert ep.SendDispatchUpdates()["assignment_id"] == "456"

    def test_reexports_in_redis_client(self):
        import app.redis_client as rc
        assert hasattr(rc, "DEFAULT_DISPATCH_UPDATE_CONFIG")
        assert hasattr(rc, "send_dispatch_updates")
        assert hasattr(rc, "SendDispatchUpdates")
        assert hasattr(rc, "broadcast_dispatch_update")
        assert rc.SendDispatchUpdates()["event"] == "dispatch.updated"

    def test_reexports_in_celery_app(self):
        import app.celery_app as ca
        assert hasattr(ca, "DEFAULT_DISPATCH_UPDATE_CONFIG")
        assert hasattr(ca, "send_dispatch_updates")
        assert hasattr(ca, "SendDispatchUpdates")
        assert hasattr(ca, "get_dispatch_update_config")
        assert ca.SendDispatchUpdates()["assignment_id"] == "456"

    def test_reexports_and_task_in_tasks(self):
        import app.tasks as tasks
        assert hasattr(tasks, "send_dispatch_updates_task")
        assert hasattr(tasks, "SendDispatchUpdatesTask")
        assert hasattr(tasks, "Send_dispatch_updates_task")
        assert hasattr(tasks, "SendDispatchUpdates")
        res = tasks.send_dispatch_updates_task()
        assert res["assignment_id"] == "456"
        assert res["event"] == "dispatch.updated"


class TestValidationAndDeduplication:
    """Verifies validation and deduplication."""

    def test_validation_rejects_missing_mandatory_fields(self):
        broadcaster = DispatchUpdateBroadcaster()
        res1 = broadcaster.broadcast_dispatch_update(job_id="", assignment_id="456", tenant_id="t1")
        assert res1["status_code"] == "REJECTED"

        res2 = broadcaster.broadcast_dispatch_update(job_id="123", assignment_id="", tenant_id="t1")
        assert res2["status_code"] == "REJECTED"

        res3 = broadcaster.broadcast_dispatch_update(job_id="123", assignment_id="456", tenant_id="")
        assert res3["status_code"] == "REJECTED"

    def test_duplicate_dispatch_deduplication(self):
        mock_redis = MagicMock()
        mock_redis.get.return_value = b"1"  # Already seen

        broadcaster = DispatchUpdateBroadcaster(redis_client=mock_redis)
        res = broadcaster.broadcast_dispatch_update(
            job_id="job-dup",
            assignment_id="asgn-dup",
            tenant_id="tenant-dup",
            event_id="stable-dup-event-id",
            redis_client=mock_redis,
        )

        assert res["event"] == "dispatch.updated"
        assert res["duplicate"] is True
        assert res["status_code"] == "SKIPPED"
        mock_redis.publish.assert_not_called()


class TestRedisPublishAndFailureSafety:
    """Verifies wire payload structure and bounded failure behavior."""

    def test_publish_payload_structure(self):
        mock_redis = MagicMock()
        mock_redis.get.return_value = None

        broadcaster = DispatchUpdateBroadcaster(redis_client=mock_redis)
        res = broadcaster.broadcast_dispatch_update(
            job_id="job-789",
            assignment_id="asgn-101",
            tenant_id="tenant-dispatch",
            technician_id="tech-7",
            technician_name="Charlie",
            previous_technician_id="tech-2",
            dispatch_action="OVERRIDE",
            status="ASSIGNED",
            correlation_id="corr-disp-1",
            timestamp="2026-11-23T16:30:00Z",
            redis_client=mock_redis,
        )

        assert res["event"] == "dispatch.updated"
        assert res["job_id"] == "job-789"
        assert res["assignment_id"] == "asgn-101"
        assert res["tenant_id"] == "tenant-dispatch"

        mock_redis.publish.assert_called_once()
        channel, raw_payload = mock_redis.publish.call_args[0]
        assert channel == "dispatch_events"

        payload = json.loads(raw_payload)
        assert payload["event"] == "dispatch.updated"
        assert payload["tenant_id"] == "tenant-dispatch"
        assert payload["job_id"] == "job-789"
        assert payload["assignment_id"] == "asgn-101"
        assert payload["technician_id"] == "tech-7"
        assert payload["technician_name"] == "Charlie"
        assert payload["previous_technician_id"] == "tech-2"
        assert payload["dispatch_action"] == "OVERRIDE"
        assert payload["status"] == "ASSIGNED"
        assert payload["correlation_id"] == "corr-disp-1"
        assert payload["timestamp"] == "2026-11-23T16:30:00Z"

    def test_bounded_failure_safety_on_redis_error(self):
        mock_redis = MagicMock()
        mock_redis.publish.side_effect = ConnectionError("Redis down")

        broadcaster = DispatchUpdateBroadcaster(redis_client=mock_redis)
        res = broadcaster.broadcast_dispatch_update(
            job_id="job-err",
            assignment_id="asgn-err",
            tenant_id="tenant-err",
            redis_client=mock_redis,
        )

        # Bounded failure: does not raise, returns contract
        assert res["event"] == "dispatch.updated"
        assert res["job_id"] == "job-err"
        assert res["assignment_id"] == "asgn-err"


class TestAdminQueueEndpoints:
    """Verifies FastAPI REST endpoints."""

    def test_status_endpoint(self):
        from app.routes.admin_queue import get_dispatch_update_status_endpoint
        res = get_dispatch_update_status_endpoint()
        assert res["event"] == "dispatch.updated"
        assert res["job_id"] == "123"
        assert res["assignment_id"] == "456"

    def test_config_endpoint(self):
        from app.routes.admin_queue import get_dispatch_update_config_endpoint
        res = get_dispatch_update_config_endpoint()
        assert res["event"] == "dispatch.updated"
        assert res["namespace"] == "/dispatch-dashboard"

    def test_send_endpoint(self):
        from types import SimpleNamespace
        from app.routes.admin_queue import post_send_dispatch_update_endpoint

        mock_user = SimpleNamespace(tenant_id="tenant-dispatch-user")
        payload = {
            "job_id": "job-555",
            "assignment_id": "asgn-777",
            "dispatch_action": "ASSIGNED",
        }

        with patch("app.services.dispatch_update_broadcaster.get_redis_client") as mock_get_rc:
            mock_rc = MagicMock()
            mock_rc.get.return_value = None
            mock_get_rc.return_value = mock_rc

            res = post_send_dispatch_update_endpoint(payload, current_user=mock_user)
            assert res["event"] == "dispatch.updated"
            assert res["job_id"] == "job-555"
            assert res["assignment_id"] == "asgn-777"
            assert res["tenant_id"] == "tenant-dispatch-user"


class TestWebSocketServerContractAndFrontend:
    """Verifies ws-server/server.js and frontend services."""

    def test_server_js_contains_dispatch_endpoints_and_security(self):
        candidates = [
            "ws-server/server.js",
            "../ws-server/server.js",
            os.path.join(os.path.dirname(__file__), "../../ws-server/server.js"),
        ]
        target_path = next((p for p in candidates if os.path.exists(p)), None)
        assert target_path is not None, f"Could not locate ws-server/server.js from: {candidates}"
        with open(target_path, "r", encoding="utf-8") as f:
            content = f.read()

        assert "/dispatch/config" in content
        assert "/dispatch/broadcast" in content
        assert "dispatch.updated" in content
        assert "override_assignment" in content
        assert "mutate_dispatch" in content

    def test_frontend_dispatcher_service_contains_listener(self):
        candidates = [
            "frontend/src/services/dispatcherService.ts",
            "../frontend/src/services/dispatcherService.ts",
            os.path.join(os.path.dirname(__file__), "../../frontend/src/services/dispatcherService.ts"),
        ]
        target_path = next((p for p in candidates if os.path.exists(p)), None)
        assert target_path is not None, f"Could not locate dispatcherService.ts from: {candidates}"
        with open(target_path, "r", encoding="utf-8") as f:
            content = f.read()

        assert "DispatchUpdatePayload" in content
        assert "subscribeDispatchUpdates" in content
        assert "dispatch.updated" in content
