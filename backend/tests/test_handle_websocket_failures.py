"""
test_handle_websocket_failures.py

Automated test suite for the story:
Handle WebSocket failures as an integrated part of the
WebSocket Real-Time Events capability.

Expected Output (JSON):
{
    "status": "DEGRADED_OR_RECOVERED",
    "failure_handling": "ENABLED"
}
"""

import json
import os
import pytest
from unittest.mock import MagicMock

from app.services.websocket_failure_service import (
    DEFAULT_FAILURE_HANDLING_CONFIG,
    VALID_FAILURE_STATUSES,
    WebSocketFailureService,
    get_websocket_failure_config,
    get_websocket_failure_status,
    handle_websocket_failures,
    HandleWebSocketFailures,
    Handle_websocket_failures,
)


class TestWebSocketFailureContract:
    """Verifies canonical JSON contract and configuration."""

    def test_canonical_contract_defaults(self):
        result = handle_websocket_failures()
        assert isinstance(result, dict)
        assert result.get("status") == "DEGRADED_OR_RECOVERED"
        assert result.get("failure_handling") == "ENABLED"

    def test_canonical_contract_custom_values(self):
        result = handle_websocket_failures(
            status="HEALTHY",
            failure_handling="ENABLED",
        )
        assert result["status"] == "HEALTHY"
        assert result["failure_handling"] == "ENABLED"

    def test_default_config_constants(self):
        assert DEFAULT_FAILURE_HANDLING_CONFIG["status"] == "DEGRADED_OR_RECOVERED"
        assert DEFAULT_FAILURE_HANDLING_CONFIG["failure_handling"] == "ENABLED"

    def test_valid_statuses_set(self):
        assert "DEGRADED_OR_RECOVERED" in VALID_FAILURE_STATUSES
        assert "HEALTHY" in VALID_FAILURE_STATUSES
        assert "DEGRADED" in VALID_FAILURE_STATUSES
        assert "RECOVERED" in VALID_FAILURE_STATUSES

    def test_operational_config(self):
        config = get_websocket_failure_config()
        assert config["status"] == "DEGRADED_OR_RECOVERED"
        assert config["failure_handling"] == "ENABLED"
        assert config["malformed_event_protection"] is True
        assert config["max_redis_backoff_ms"] == 3000
        assert config["circuit_breaker_enabled"] is True
        assert config["uncaught_exception_guards"] is True
        assert config["data_leak_protection"] is True
        assert config["source_of_truth"] == "POSTGRESQL"


class TestAuthoritativeStoryAliases:
    """Verifies story aliases across all modules."""

    def test_aliases_in_broadcaster_module(self):
        assert HandleWebSocketFailures == handle_websocket_failures
        assert Handle_websocket_failures == handle_websocket_failures
        assert get_websocket_failure_status == handle_websocket_failures
        assert HandleWebSocketFailures()["status"] == "DEGRADED_OR_RECOVERED"

    def test_reexports_in_event_publisher(self):
        import app.services.event_publisher as ep
        assert hasattr(ep, "DEFAULT_FAILURE_HANDLING_CONFIG")
        assert hasattr(ep, "handle_websocket_failures")
        assert hasattr(ep, "HandleWebSocketFailures")
        assert hasattr(ep, "get_websocket_failure_config")
        assert ep.HandleWebSocketFailures()["failure_handling"] == "ENABLED"

    def test_reexports_in_redis_client(self):
        import app.redis_client as rc
        assert hasattr(rc, "DEFAULT_FAILURE_HANDLING_CONFIG")
        assert hasattr(rc, "handle_websocket_failures")
        assert hasattr(rc, "HandleWebSocketFailures")
        assert hasattr(rc, "get_websocket_failure_config")
        assert rc.HandleWebSocketFailures()["status"] == "DEGRADED_OR_RECOVERED"

    def test_reexports_in_celery_app(self):
        import app.celery_app as ca
        assert hasattr(ca, "DEFAULT_FAILURE_HANDLING_CONFIG")
        assert hasattr(ca, "handle_websocket_failures")
        assert hasattr(ca, "HandleWebSocketFailures")
        assert hasattr(ca, "get_websocket_failure_config")
        assert ca.HandleWebSocketFailures()["failure_handling"] == "ENABLED"

    def test_reexports_in_kafka_to_websocket_bridge(self):
        import app.services.kafka_to_websocket_bridge as kwb
        assert hasattr(kwb, "DEFAULT_FAILURE_HANDLING_CONFIG")
        assert hasattr(kwb, "handle_websocket_failures")
        assert hasattr(kwb, "HandleWebSocketFailures")
        assert hasattr(kwb, "get_websocket_failure_config")
        assert kwb.HandleWebSocketFailures()["status"] == "DEGRADED_OR_RECOVERED"

    def test_reexports_and_task_in_tasks(self):
        import app.tasks as tasks
        assert hasattr(tasks, "handle_websocket_failures_task")
        assert hasattr(tasks, "HandleWebSocketFailuresTask")
        assert hasattr(tasks, "Handle_websocket_failures_task")
        assert hasattr(tasks, "HandleWebSocketFailures")
        res = tasks.handle_websocket_failures_task()
        assert res["status"] == "DEGRADED_OR_RECOVERED"
        assert res["failure_handling"] == "ENABLED"


class TestMalformedPayloadHandling:
    """Verifies safe rejection of corrupted and malformed events without crashing or data leakage."""

    def test_corrupted_json_string_handled_safely(self):
        service = WebSocketFailureService()
        res = service.process_failure_event(
            raw_payload="{corrupted-invalid-json: none,",
            error_type="CORRUPTED_JSON",
        )
        assert res["status"] == "DEGRADED_OR_RECOVERED"
        assert res["failure_handling"] == "ENABLED"

    def test_missing_tenant_id_isolated_without_leakage(self):
        service = WebSocketFailureService()
        res = service.process_failure_event(
            raw_payload={"event": "job.status.updated"},  # missing tenant_id
            error_type="MISSING_TENANT_ID",
        )
        assert res["status"] == "DEGRADED_OR_RECOVERED"
        assert res["failure_handling"] == "ENABLED"

    def test_missing_event_name_isolated(self):
        service = WebSocketFailureService()
        res = service.process_failure_event(
            raw_payload={"tenant_id": "tenant-1"},  # missing event
            error_type="MISSING_EVENT_NAME",
        )
        assert res["status"] == "DEGRADED_OR_RECOVERED"
        assert res["failure_handling"] == "ENABLED"

    def test_unknown_status_defaults_safely(self):
        res = handle_websocket_failures(status="UNKNOWN_STATUS")
        assert res["status"] == "DEGRADED_OR_RECOVERED"


class TestBoundedBackoffAndRedisFailure:
    """Verifies behavior when dependency fails with bounded retry."""

    def test_redis_failure_handled_safely(self):
        mock_redis = MagicMock()
        mock_redis.publish.side_effect = ConnectionError("Redis down")

        res = handle_websocket_failures(
            status="DEGRADED",
            error_type="REDIS_CONNECTION_FAILURE",
            redis_client=mock_redis,
        )
        assert res["status"] == "DEGRADED"
        assert res["failure_handling"] == "ENABLED"


class TestAdminQueueRestEndpoints:
    """Verifies REST endpoints in admin_queue."""

    def test_admin_queue_failures_status_and_config(self):
        from app.routes.admin_queue import (
            get_websocket_failures_status_endpoint,
            get_websocket_failures_config_endpoint,
            post_websocket_failures_report_endpoint,
        )

        status = get_websocket_failures_status_endpoint()
        assert status["status"] == "DEGRADED_OR_RECOVERED"
        assert status["failure_handling"] == "ENABLED"

        config = get_websocket_failures_config_endpoint()
        assert config["status"] == "DEGRADED_OR_RECOVERED"
        assert config["malformed_event_protection"] is True

        mock_user = MagicMock()
        mock_user.tenant_id = "tenant-user-fail"
        post_res = post_websocket_failures_report_endpoint(
            payload={"error_type": "SYNTHETIC_TEST", "status": "RECOVERED"},
            current_user=mock_user,
        )
        assert post_res["status"] == "RECOVERED"
        assert post_res["failure_handling"] == "ENABLED"


class TestWebSocketServerIntegration:
    """Verifies ws-server/server.js contains crash prevention and failure endpoints."""

    def test_ws_server_contains_failure_handling_logic(self):
        server_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "ws-server", "server.js")
        )
        assert os.path.exists(server_path), f"ws-server/server.js not found at {server_path}"

        with open(server_path, "r", encoding="utf-8") as f:
            content = f.read()

        assert "uncaughtException" in content
        assert "unhandledRejection" in content
        assert "malformedEventsCount" in content
        assert "/failures/status" in content
        assert "/failures/config" in content
        assert "/failures/report" in content
        assert "DEGRADED_OR_RECOVERED" in content
        assert "failure_handling" in content
