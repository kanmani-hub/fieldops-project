"""
test_tenant_based_event_filtering.py

Automated test suite for the story:
Add tenant-based event filtering as an integrated part of the
WebSocket Real-Time Events capability.

Expected Output (JSON):
{
    "status": "FILTERED",
    "isolation": "TENANT_ROOM_AND_AUTHORIZATION"
}
"""

import asyncio
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.services.websocket_tenant_filter import (
    CANONICAL_TENANT_REQUIRED_EVENTS,
    DEFAULT_TENANT_FILTER_CONFIG,
    TENANT_ROOM_PREFIX,
    AddTenantBasedEventFiltering,
    Add_tenant_based_event_filtering,
    FilterTenantEvents,
    Filter_tenant_events,
    TenantEventFilterService,
    extract_tenant_from_room,
    filter_tenant_events,
    format_tenant_room,
    format_tenant_scoped_room,
    get_tenant_filter_config,
    get_tenant_filter_status,
    get_tenant_filtering_config,
    get_tenant_filtering_status,
    is_valid_tenant_id,
    validate_event_tenant,
    validate_tenant_event_filter,
)


class TestTenantFilterContractAndConfig:
    """Verifies canonical JSON contract, room naming, and operational configuration."""

    def test_canonical_contract_defaults(self):
        result = filter_tenant_events()
        assert isinstance(result, dict)
        assert result.get("status") == "FILTERED"
        assert result.get("isolation") == "TENANT_ROOM_AND_AUTHORIZATION"

    def test_default_config_constants(self):
        assert DEFAULT_TENANT_FILTER_CONFIG == {
            "status": "FILTERED",
            "isolation": "TENANT_ROOM_AND_AUTHORIZATION",
        }
        assert TENANT_ROOM_PREFIX == "tenant:"

    def test_operational_config_and_status(self):
        status = get_tenant_filter_status()
        assert status == DEFAULT_TENANT_FILTER_CONFIG

        config = get_tenant_filter_config()
        assert config["status"] == "FILTERED"
        assert config["isolation"] == "TENANT_ROOM_AND_AUTHORIZATION"
        assert config["namespace"] == "/dispatch-dashboard"
        assert config["room_prefix"] == "tenant:"
        assert config["room_format"] == "tenant:{tenant_id}"
        assert config["require_tenant_id"] is True
        assert config["validate_before_broadcast"] is True
        assert config["cross_tenant_protection"] is True
        assert config["wildcard_tenants_blocked"] is True
        assert config["source_of_truth"] == "POSTGRESQL"
        assert "job.status.updated" in config["canonical_events"]
        assert "payment.updated" in config["canonical_events"]

    def test_room_formatting_and_extraction(self):
        assert format_tenant_room("tenant-alpha") == "tenant:tenant-alpha"
        assert (
            format_tenant_scoped_room("tenant-alpha", "dispatchers")
            == "tenant:tenant-alpha:dispatchers"
        )
        assert (
            format_tenant_scoped_room("tenant-alpha", "job", 42)
            == "tenant:tenant-alpha:job:42"
        )
        assert extract_tenant_from_room("tenant:tenant-alpha") == "tenant-alpha"
        assert extract_tenant_from_room("tenant:tenant-alpha:job:42") == "tenant-alpha"
        assert extract_tenant_from_room("tenant_tenant-alpha") == "tenant-alpha"
        assert extract_tenant_from_room("global_room") is None


class TestPrimaryFilteringSuccessPath:
    """Verifies authorized same-tenant event delivery and metadata preservation."""

    @pytest.mark.parametrize(
        "event_name",
        [
            "job.status.updated",
            "technician.location.updated",
            "dispatch.updated",
            "sla.alert",
            "notification.updated",
            "payment.updated",
        ],
    )
    def test_canonical_events_filtered_to_tenant_room(self, event_name):
        payload = {
            "event": event_name,
            "tenant_id": "tenant-alpha",
            "event_id": f"evt-{event_name}-1",
            "correlation_id": "corr-123",
            "job_id": "job-99",
        }
        res = filter_tenant_events(
            event_payload=payload,
            socket_tenant_id="tenant-alpha",
            redis_client=False,
        )
        assert res["status"] == "FILTERED"
        assert res["isolation"] == "TENANT_ROOM_AND_AUTHORIZATION"
        assert res["allowed"] is True
        assert res["tenant_id"] == "tenant-alpha"
        assert res["room"] == "tenant:tenant-alpha"
        assert res["correlation_id"] == "corr-123"
        assert res["event_id"] == f"evt-{event_name}-1"

    def test_scoped_room_authorized_for_same_tenant(self):
        res = filter_tenant_events(
            event_payload={
                "event": "dispatch.updated",
                "tenant_id": "tenant-1",
                "job_id": "123",
            },
            socket_tenant_id="tenant-1",
            target_room="tenant:tenant-1:dispatchers",
            authorized_rooms=["tenant:tenant-1", "tenant:tenant-1:dispatchers"],
            redis_client=False,
        )
        assert res["status"] == "FILTERED"
        assert res["allowed"] is True
        assert res["room"] == "tenant:tenant-1:dispatchers"


class TestCrossTenantAndInvalidInputNegativeCases:
    """Verifies cross-tenant isolation, missing tenant rejection, and wildcard blocking."""

    def test_missing_tenant_id_rejected(self):
        res = validate_event_tenant({"event": "job.status.updated", "job_id": "123"})
        assert res["allowed"] is False
        assert res["status"] == "REJECTED"
        assert res["code"] == "MISSING_TENANT_ID"

    @pytest.mark.parametrize(
        "bad_tenant",
        ["", "   ", "*", "all", "ALL", "global", "broadcast", "none", "null", "undefined", "tenant-*", True, None],
    )
    def test_invalid_or_wildcard_tenant_rejected(self, bad_tenant):
        assert is_valid_tenant_id(bad_tenant) is False
        res = filter_tenant_events(
            event_payload={"event": "dispatch.updated", "tenant_id": bad_tenant, "job_id": "123"},
            redis_client=False,
        )
        assert res["allowed"] is False
        assert res["status"] == "REJECTED"

    def test_cross_tenant_socket_delivery_blocked(self):
        res = filter_tenant_events(
            event_payload={
                "event": "payment.updated",
                "tenant_id": "tenant-victim",
                "invoice_id": "inv-1",
                "correlation_id": "corr-cross-1",
            },
            socket_tenant_id="tenant-attacker",
            redis_client=False,
        )
        assert res["allowed"] is False
        assert res["status"] == "REJECTED"
        assert res["code"] == "CROSS_TENANT_EVENT_BLOCKED"

    def test_cross_tenant_target_room_blocked(self):
        res = filter_tenant_events(
            event_payload={
                "event": "sla.alert",
                "tenant_id": "tenant-a",
                "job_id": "10",
            },
            socket_tenant_id="tenant-a",
            target_room="tenant:tenant-b",
            redis_client=False,
        )
        assert res["allowed"] is False
        assert res["status"] == "REJECTED"
        assert res["code"] == "CROSS_TENANT_ROOM_FORBIDDEN"

    def test_nested_payload_tenant_tampering_blocked(self):
        res = validate_event_tenant(
            {
                "event": "notification.updated",
                "tenant_id": "tenant-a",
                "payload": {"tenant_id": "tenant-b", "notification_id": "n-1"},
            }
        )
        assert res["allowed"] is False
        assert res["status"] == "REJECTED"
        assert res["code"] == "CROSS_TENANT_NESTED_MISMATCH"

    def test_unauthorized_scope_blocked(self):
        res = filter_tenant_events(
            event_payload={"event": "dispatch.updated", "tenant_id": "tenant-a", "job_id": "5"},
            socket_tenant_id="tenant-a",
            target_room="tenant:tenant-a:dispatchers",
            authorized_rooms=["tenant:tenant-a:job:5"],
            redis_client=False,
        )
        assert res["allowed"] is False
        assert res["status"] == "REJECTED"
        assert res["code"] == "UNAUTHORIZED_ROOM_SCOPE"

    def test_non_dict_payload_rejected(self):
        res = validate_event_tenant(["not", "an", "object"])
        assert res["allowed"] is False
        assert res["code"] == "INVALID_EVENT_PAYLOAD"


class TestIdempotencyAndBoundedFailureHandling:
    """Verifies idempotent event filtering and safe degradation when Redis fails."""

    def test_duplicate_event_id_is_skipped(self):
        mock_redis = MagicMock()
        mock_redis.get.return_value = b"1"

        res = filter_tenant_events(
            event_payload={
                "event": "job.status.updated",
                "tenant_id": "tenant-alpha",
                "event_id": "evt-dup-1",
                "correlation_id": "corr-dup",
            },
            socket_tenant_id="tenant-alpha",
            redis_client=mock_redis,
        )
        assert res["status"] == "FILTERED"
        assert res["duplicate"] is True
        assert res["status_code"] == "SKIPPED"
        mock_redis.set.assert_not_called()

    def test_redis_error_does_not_crash_or_leak_cross_tenant(self):
        mock_redis = MagicMock()
        mock_redis.get.side_effect = ConnectionError("Redis unavailable")
        mock_redis.set.side_effect = ConnectionError("Redis unavailable")

        # Same-tenant succeeds safely despite Redis error
        ok_res = filter_tenant_events(
            event_payload={
                "event": "dispatch.updated",
                "tenant_id": "tenant-alpha",
                "event_id": "evt-err-1",
            },
            socket_tenant_id="tenant-alpha",
            redis_client=mock_redis,
        )
        assert ok_res["status"] == "FILTERED"
        assert ok_res["allowed"] is True

        # Cross-tenant is still blocked even when Redis is down
        bad_res = filter_tenant_events(
            event_payload={
                "event": "dispatch.updated",
                "tenant_id": "tenant-alpha",
                "event_id": "evt-err-2",
            },
            socket_tenant_id="tenant-beta",
            redis_client=mock_redis,
        )
        assert bad_res["status"] == "REJECTED"
        assert bad_res["allowed"] is False


class TestIntegratedBackendBoundaries:
    """Verifies tenant filtering across socket_manager, bridge, broadcasters, publisher, and routes."""

    def test_socket_manager_blocks_cross_tenant_broadcast(self):
        from app.services import socket_manager as sm

        def _run_sync(coro):
            try:
                coro.send(None)
            except StopIteration as exc:
                return exc.value
            finally:
                coro.close()

        with patch.object(sm.sio, "emit", new_callable=AsyncMock) as mock_emit:
            # Cross-tenant payload into tenant_tenant-a must be blocked
            ok1 = _run_sync(
                sm.ws_manager.broadcast_to_tenant(
                    "tenant-a",
                    {"event": "dispatch.updated", "tenant_id": "tenant-b", "job_id": "1"},
                )
            )
            assert ok1 is False
            mock_emit.assert_not_called()

            # Cross-tenant payload into tenant:tenant-a room must be blocked
            ok2 = _run_sync(
                sm.ws_manager.broadcast(
                    "tenant:tenant-a",
                    {"event": "dispatch.updated", "tenant_id": "tenant-b", "job_id": "1"},
                )
            )
            assert ok2 is False
            mock_emit.assert_not_called()

            # Same-tenant payload succeeds
            ok3 = _run_sync(
                sm.ws_manager.broadcast_to_tenant(
                    "tenant-a",
                    {"event": "dispatch.updated", "tenant_id": "tenant-a", "job_id": "1"},
                )
            )
            assert ok3 is True
            mock_emit.assert_called_once()

    def test_kafka_bridge_rejects_wildcard_and_nested_mismatch(self):
        from app.services.kafka_to_websocket_bridge import KafkaToWebSocketBridge

        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        bridge = KafkaToWebSocketBridge(redis_client=mock_redis)

        res_wild = bridge.bridge_event(
            {"event_id": "e-wild", "event_type": "job-assigned", "tenant_id": "*"}
        )
        assert res_wild["status"] == "REJECTED"

        res_nested = bridge.bridge_event(
            {
                "event_id": "e-nest",
                "event_type": "job-assigned",
                "tenant_id": "tenant-a",
                "payload": {"tenant_id": "tenant-b"},
            }
        )
        assert res_nested["status"] == "REJECTED"
        mock_redis.publish.assert_not_called()

    def test_broadcasters_and_event_publisher_reject_wildcard_tenant(self):
        from app.services.dispatch_update_broadcaster import send_dispatch_updates
        from app.services.job_status_broadcaster import send_job_status_updates
        from app.services.notification_update_broadcaster import send_notification_updates
        from app.services.payment_update_broadcaster import send_payment_updates
        from app.services.sla_alert_broadcaster import send_sla_alerts
        from app.services.technician_location_broadcaster import send_technician_location_updates
        from app.services.event_publisher import publish_dispatch_event

        assert send_job_status_updates(job_id="1", status="ASSIGNED", tenant_id="*")["status_code"] == "REJECTED"
        assert send_dispatch_updates(job_id="1", assignment_id="2", tenant_id="all")["status_code"] == "REJECTED"
        assert send_technician_location_updates(technician_id="t1", latitude=1.0, longitude=2.0, tenant_id="*")["status_code"] == "REJECTED"
        assert send_sla_alerts(job_id="1", severity="AT_RISK", tenant_id="null")["status_code"] == "REJECTED"
        assert send_notification_updates(notification_id="n1", status="SENT", tenant_id="*")["status_code"] == "REJECTED"
        assert send_payment_updates(invoice_id="i1", payment_status="PAID", tenant_id="all")["status_code"] == "REJECTED"

        mock_redis = MagicMock()
        publish_dispatch_event(mock_redis, event_type="JOB_ASSIGNED", job_id="1", tenant_id="*")
        mock_redis.publish.assert_not_called()

    def test_admin_queue_rejects_cross_tenant_broadcast(self):
        from app.routes.admin_queue import (
            get_websocket_tenant_filtering_config_endpoint,
            get_websocket_tenant_filtering_status_endpoint,
            post_send_dispatch_update_endpoint,
            post_websocket_tenant_filtering_validate_endpoint,
        )

        status_res = get_websocket_tenant_filtering_status_endpoint()
        assert status_res == DEFAULT_TENANT_FILTER_CONFIG

        cfg_res = get_websocket_tenant_filtering_config_endpoint()
        assert cfg_res["isolation"] == "TENANT_ROOM_AND_AUTHORIZATION"

        user_a = SimpleNamespace(tenant_id="tenant-a", role="dispatcher")
        with pytest.raises(HTTPException) as exc:
            post_send_dispatch_update_endpoint(
                {"job_id": "1", "assignment_id": "2", "tenant_id": "tenant-b"},
                current_user=user_a,
            )
        assert exc.value.status_code == 403

        val_blocked = post_websocket_tenant_filtering_validate_endpoint(
            {"event": "dispatch.updated", "tenant_id": "tenant-b"},
            current_user=user_a,
        )
        assert val_blocked["allowed"] is False
        assert val_blocked["status"] == "REJECTED"

        val_ok = post_websocket_tenant_filtering_validate_endpoint(
            {"event": "dispatch.updated", "tenant_id": "tenant-a"},
            current_user=user_a,
        )
        assert val_ok["allowed"] is True
        assert val_ok["status"] == "FILTERED"


class TestStoryAliasesExportsAndTasks:
    """Verifies story aliases, re-exports, and Celery task integration."""

    def test_aliases_and_reexports(self):
        assert AddTenantBasedEventFiltering is filter_tenant_events
        assert Add_tenant_based_event_filtering is filter_tenant_events
        assert FilterTenantEvents is filter_tenant_events
        assert Filter_tenant_events is filter_tenant_events
        assert validate_tenant_event_filter is filter_tenant_events
        assert get_tenant_filtering_status is get_tenant_filter_status
        assert get_tenant_filtering_config is get_tenant_filter_config

        import app.services.event_publisher as ep
        import app.redis_client as rc
        import app.celery_app as ca
        import app.tasks as tasks

        for mod in (ep, rc, ca, tasks):
            assert hasattr(mod, "DEFAULT_TENANT_FILTER_CONFIG")
            assert hasattr(mod, "filter_tenant_events")
            assert hasattr(mod, "AddTenantBasedEventFiltering")
            assert hasattr(mod, "validate_event_tenant")

        assert hasattr(tasks, "filter_tenant_events_task")
        assert hasattr(tasks, "AddTenantBasedEventFilteringTask")
        task_res = tasks.filter_tenant_events_task(
            event_payload={"event": "job.status.updated", "tenant_id": "tenant-celery"},
            socket_tenant_id="tenant-celery",
        )
        assert task_res["status"] == "FILTERED"
        assert task_res["isolation"] == "TENANT_ROOM_AND_AUTHORIZATION"


class TestWsServerAndFrontendIntegration:
    """Verifies ws-server/server.js and frontend services implement tenant-based filtering."""

    def test_ws_server_tenant_room_and_validation_present(self):
        server_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "ws-server", "server.js")
        )
        assert os.path.exists(server_path)
        with open(server_path, "r", encoding="utf-8") as f:
            content = f.read()

        assert "getTenantRoom" in content
        assert "validateEventTenant" in content
        assert "validateAndEmitTenantEvent" in content
        assert "rejectUnauthorizedRoomJoin" in content
        assert "crossTenantBlockedCount" in content
        assert "/tenant-filtering/status" in content
        assert "/tenant-filtering/config" in content
        assert "/tenant-filtering/validate" in content
        assert "TENANT_ROOM_AND_AUTHORIZATION" in content

    def test_frontend_services_contain_tenant_filtering(self):
        disp_path = os.path.abspath(
            os.path.join(
                os.path.dirname(__file__),
                "..",
                "..",
                "frontend",
                "src",
                "services",
                "dispatcherService.ts",
            )
        )
        track_path = os.path.abspath(
            os.path.join(
                os.path.dirname(__file__),
                "..",
                "..",
                "frontend",
                "src",
                "hooks",
                "useTrackingWebSocket.ts",
            )
        )
        with open(disp_path, "r", encoding="utf-8") as f:
            disp_content = f.read()
        with open(track_path, "r", encoding="utf-8") as f:
            track_content = f.read()

        assert "isAuthorizedTenantEvent" in disp_content
        assert "DEFAULT_TENANT_FILTERING_CONFIG" in disp_content
        assert "TENANT_ROOM_AND_AUTHORIZATION" in disp_content
        assert "ignored cross-tenant tracking frame" in track_content
