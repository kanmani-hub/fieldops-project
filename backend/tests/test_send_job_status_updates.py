"""
test_send_job_status_updates.py

Automated test suite for the story:
Send job status updates as an integrated part of the
WebSocket Real-Time Events capability.

Expected Output (JSON):
{
    "event": "job.status.updated",
    "tenant_id": "tenant-1",
    "job_id": "123",
    "status": "ASSIGNED"
}
"""

import json
import os
import pytest
from unittest.mock import MagicMock, patch

from app.services.job_status_broadcaster import (
    DEFAULT_JOB_STATUS_UPDATE_CONFIG,
    JobStatusUpdateBroadcaster,
    broadcast_job_status_update,
    get_job_status_update_config,
    get_job_status_update_status,
    send_job_status_updates,
    SendJobStatusUpdates,
    Send_job_status_updates,
)


class TestJobStatusUpdateContract:
    """Verifies canonical JSON contract and configuration."""

    def test_canonical_contract_defaults(self):
        result = send_job_status_updates()
        assert isinstance(result, dict)
        assert result.get("event") == "job.status.updated"
        assert result.get("tenant_id") == "tenant-1"
        assert result.get("job_id") == "123"
        assert result.get("status") == "ASSIGNED"

    def test_canonical_contract_custom_values(self):
        result = send_job_status_updates(
            job_id="999",
            status="in_progress",
            tenant_id="tenant-custom",
        )
        assert result["event"] == "job.status.updated"
        assert result["tenant_id"] == "tenant-custom"
        assert result["job_id"] == "999"
        assert result["status"] == "IN_PROGRESS"

    def test_default_config_constants(self):
        assert DEFAULT_JOB_STATUS_UPDATE_CONFIG["event"] == "job.status.updated"
        assert DEFAULT_JOB_STATUS_UPDATE_CONFIG["tenant_id"] == "tenant-1"
        assert DEFAULT_JOB_STATUS_UPDATE_CONFIG["job_id"] == "123"
        assert DEFAULT_JOB_STATUS_UPDATE_CONFIG["status"] == "ASSIGNED"

    def test_operational_config(self):
        config = get_job_status_update_config()
        assert config["event"] == "job.status.updated"
        assert config["transport"] == "REDIS_PUB_SUB_TO_WEBSOCKET"
        assert config["namespace"] == "/dispatch-dashboard"
        assert config["source_of_truth"] == "POSTGRESQL"
        assert config["redis_channel"] == "dispatch_events"
        assert config["deduplication_ttl_seconds"] == 3600
        assert config["default_contract"]["status"] == "ASSIGNED"


class TestAuthoritativeStoryAliases:
    """Verifies story aliases across all modules."""

    def test_aliases_in_broadcaster_module(self):
        assert SendJobStatusUpdates == send_job_status_updates
        assert Send_job_status_updates == send_job_status_updates
        assert broadcast_job_status_update == send_job_status_updates
        assert get_job_status_update_status == send_job_status_updates
        assert SendJobStatusUpdates()["status"] == "ASSIGNED"

    def test_reexports_in_event_publisher(self):
        import app.services.event_publisher as ep
        assert hasattr(ep, "DEFAULT_JOB_STATUS_UPDATE_CONFIG")
        assert hasattr(ep, "send_job_status_updates")
        assert hasattr(ep, "SendJobStatusUpdates")
        assert hasattr(ep, "broadcast_job_status_update")
        assert ep.SendJobStatusUpdates()["status"] == "ASSIGNED"

    def test_reexports_in_redis_client(self):
        import app.redis_client as rc
        assert hasattr(rc, "DEFAULT_JOB_STATUS_UPDATE_CONFIG")
        assert hasattr(rc, "send_job_status_updates")
        assert hasattr(rc, "SendJobStatusUpdates")
        assert hasattr(rc, "broadcast_job_status_update")
        assert rc.SendJobStatusUpdates()["status"] == "ASSIGNED"

    def test_reexports_in_celery_app(self):
        import app.celery_app as ca
        assert hasattr(ca, "DEFAULT_JOB_STATUS_UPDATE_CONFIG")
        assert hasattr(ca, "send_job_status_updates")
        assert hasattr(ca, "SendJobStatusUpdates")
        assert hasattr(ca, "get_job_status_update_config")
        assert ca.SendJobStatusUpdates()["status"] == "ASSIGNED"

    def test_reexports_and_task_in_tasks(self):
        import app.tasks as tasks
        assert hasattr(tasks, "send_job_status_updates_task")
        assert hasattr(tasks, "SendJobStatusUpdatesTask")
        assert hasattr(tasks, "Send_job_status_updates_task")
        assert hasattr(tasks, "SendJobStatusUpdates")
        res = tasks.send_job_status_updates_task()
        assert res["status"] == "ASSIGNED"
        assert res["event"] == "job.status.updated"


class TestValidationAndErrorHandling:
    """Verifies input validation and bounded failure behavior."""

    def test_validation_rejects_missing_fields(self):
        broadcaster = JobStatusUpdateBroadcaster()
        res1 = broadcaster.broadcast_status_update(job_id="", status="ASSIGNED", tenant_id="t1")
        assert res1["status_code"] == "REJECTED"

        res2 = broadcaster.broadcast_status_update(job_id="123", status="", tenant_id="t1")
        assert res2["status_code"] == "REJECTED"

        res3 = broadcaster.broadcast_status_update(job_id="123", status="ASSIGNED", tenant_id="")
        assert res3["status_code"] == "REJECTED"

    def test_bounded_redis_failure_safety(self):
        mock_redis = MagicMock()
        mock_redis.publish.side_effect = ConnectionError("Redis unreachable")

        broadcaster = JobStatusUpdateBroadcaster(redis_client=mock_redis)
        res = broadcaster.broadcast_status_update(
            job_id="job-fail",
            status="IN_PROGRESS",
            tenant_id="tenant-fail",
            redis_client=mock_redis,
        )
        # Bounded failure: gracefully returns contract without raising
        assert res["event"] == "job.status.updated"
        assert res["job_id"] == "job-fail"
        assert res["status"] == "IN_PROGRESS"


class TestRedisPublishAndDeduplication:
    """Verifies wire payload structure and idempotency deduplication."""

    def test_publish_payload_structure(self):
        mock_redis = MagicMock()
        mock_redis.get.return_value = None

        broadcaster = JobStatusUpdateBroadcaster(redis_client=mock_redis)
        res = broadcaster.broadcast_status_update(
            job_id="job-888",
            status="COMPLETED",
            tenant_id="tenant-delta",
            old_status="IN_PROGRESS",
            correlation_id="corr-123",
            technician_id="tech-5",
            technician_name="Bob",
            timestamp="2026-11-23T15:00:00Z",
            redis_client=mock_redis,
        )

        assert res["event"] == "job.status.updated"
        assert res["job_id"] == "job-888"
        assert res["status"] == "COMPLETED"

        mock_redis.publish.assert_called_once()
        channel, raw_payload = mock_redis.publish.call_args[0]
        assert channel == "dispatch_events"

        payload = json.loads(raw_payload)
        assert payload["event"] == "job.status.updated"
        assert payload["tenant_id"] == "tenant-delta"
        assert payload["job_id"] == "job-888"
        assert payload["status"] == "COMPLETED"
        assert payload["new_status"] == "COMPLETED"
        assert payload["old_status"] == "IN_PROGRESS"
        assert payload["correlation_id"] == "corr-123"
        assert payload["timestamp"] == "2026-11-23T15:00:00Z"
        assert payload["technician"]["tech_id"] == "tech-5"
        assert payload["technician"]["name"] == "Bob"

        mock_redis.set.assert_called_once()

    def test_duplicate_update_deduplication(self):
        mock_redis = MagicMock()
        mock_redis.get.return_value = b"1"  # Already seen

        broadcaster = JobStatusUpdateBroadcaster(redis_client=mock_redis)
        res = broadcaster.broadcast_status_update(
            job_id="job-dup",
            status="CANCELLED",
            tenant_id="tenant-dup",
            event_id="stable-dup-event-id",
            redis_client=mock_redis,
        )

        assert res["event"] == "job.status.updated"
        assert res["duplicate"] is True
        assert res["status_code"] == "SKIPPED"
        mock_redis.publish.assert_not_called()


class TestAdminQueueEndpoints:
    """Verifies FastAPI REST endpoints."""

    def test_status_endpoint(self):
        from app.routes.admin_queue import get_job_status_update_status_endpoint
        res = get_job_status_update_status_endpoint()
        assert res["event"] == "job.status.updated"
        assert res["job_id"] == "123"
        assert res["status"] == "ASSIGNED"

    def test_config_endpoint(self):
        from app.routes.admin_queue import get_job_status_update_config_endpoint
        res = get_job_status_update_config_endpoint()
        assert res["event"] == "job.status.updated"
        assert res["namespace"] == "/dispatch-dashboard"

    def test_send_endpoint(self):
        from types import SimpleNamespace
        from app.routes.admin_queue import post_send_job_status_update_endpoint

        mock_user = SimpleNamespace(tenant_id="tenant-api-user")
        payload = {
            "job_id": "job-777",
            "status": "EN_ROUTE",
        }

        with patch("app.services.job_status_broadcaster.get_redis_client") as mock_get_rc:
            mock_rc = MagicMock()
            mock_rc.get.return_value = None
            mock_get_rc.return_value = mock_rc

            res = post_send_job_status_update_endpoint(payload, current_user=mock_user)
            assert res["event"] == "job.status.updated"
            assert res["job_id"] == "job-777"
            assert res["status"] == "EN_ROUTE"
            assert res["tenant_id"] == "tenant-api-user"


class TestWebSocketServerContractAndSecurity:
    """Verifies server.js security rejects client mutations and handles broadcasts."""

    def test_server_js_contains_status_endpoints_and_security(self):
        candidates = [
            "ws-server/server.js",
            "../ws-server/server.js",
            os.path.join(os.path.dirname(__file__), "../../ws-server/server.js"),
        ]
        target_path = next((p for p in candidates if os.path.exists(p)), None)
        assert target_path is not None, f"Could not locate ws-server/server.js from: {candidates}"
        with open(target_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Endpoint presence
        assert "/job-status/config" in content
        assert "/job-status/broadcast" in content
        assert "job.status.updated" in content

        # Security check: rejects client mutations
        assert "rejectStatusMutation" in content
        assert "Direct client status mutation forbidden" in content
        assert "job.status.update" in content

    def test_frontend_dispatcher_service_contains_status_listener(self):
        candidates = [
            "frontend/src/services/dispatcherService.ts",
            "../frontend/src/services/dispatcherService.ts",
            os.path.join(os.path.dirname(__file__), "../../frontend/src/services/dispatcherService.ts"),
        ]
        target_path = next((p for p in candidates if os.path.exists(p)), None)
        assert target_path is not None, f"Could not locate dispatcherService.ts from: {candidates}"
        with open(target_path, "r", encoding="utf-8") as f:
            content = f.read()

        assert "JobStatusUpdatePayload" in content
        assert "subscribeJobStatusUpdates" in content
        assert "job.status.updated" in content
