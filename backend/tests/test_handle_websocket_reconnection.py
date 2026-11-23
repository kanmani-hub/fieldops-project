"""
test_handle_websocket_reconnection.py

Automated test suite for the story:
Handle WebSocket reconnection as an integrated part of the
WebSocket Real-Time Events capability.

Expected Output (JSON):
{
    "status": "RECOVERED",
    "namespace": "/dispatch-dashboard",
    "recovery_window_seconds": 120
}
"""

import os
import time
import pytest
from unittest.mock import MagicMock

from app.services.websocket_reconnection_service import (
    DEFAULT_RECONNECTION_CONFIG,
    RECOVERY_WINDOW_SECONDS,
    WebSocketReconnectionService,
    get_reconnection_config,
    get_reconnection_status,
    handle_websocket_reconnection,
    HandleWebSocketReconnection,
    Handle_websocket_reconnection,
)


class TestWebSocketReconnectionContract:
    """Verifies canonical JSON contract and configuration."""

    def test_canonical_contract_defaults(self):
        result = handle_websocket_reconnection()
        assert isinstance(result, dict)
        assert result.get("status") == "RECOVERED"
        assert result.get("namespace") == "/dispatch-dashboard"
        assert result.get("recovery_window_seconds") == 120

    def test_canonical_contract_custom_values(self):
        result = handle_websocket_reconnection(
            tenant_id="tenant-custom",
            namespace="/custom-namespace",
            recovery_window_seconds=180,
            status="SYNC_REQUIRED",
        )
        assert result["status"] == "SYNC_REQUIRED"
        assert result["namespace"] == "/custom-namespace"
        assert result["recovery_window_seconds"] == 180

    def test_default_config_constants(self):
        assert DEFAULT_RECONNECTION_CONFIG["status"] == "RECOVERED"
        assert DEFAULT_RECONNECTION_CONFIG["namespace"] == "/dispatch-dashboard"
        assert DEFAULT_RECONNECTION_CONFIG["recovery_window_seconds"] == 120
        assert RECOVERY_WINDOW_SECONDS == 120

    def test_operational_config(self):
        config = get_reconnection_config()
        assert config["status"] == "RECOVERED"
        assert config["namespace"] == "/dispatch-dashboard"
        assert config["recovery_window_seconds"] == 120
        assert config["max_disconnection_duration_ms"] == 120000
        assert config["skip_middlewares"] is True
        assert config["source_of_truth"] == "POSTGRESQL"
        assert config["transport"] == "SOCKET_IO_CONNECTION_STATE_RECOVERY"

        reconnect_opts = config["client_reconnection_options"]
        assert reconnect_opts["reconnection"] is True
        assert reconnect_opts["reconnection_attempts"] == 10
        assert reconnect_opts["reconnection_delay_ms"] == 1000
        assert reconnect_opts["reconnection_delay_max_ms"] == 10000
        assert reconnect_opts["timeout_ms"] == 10000


class TestAuthoritativeStoryAliases:
    """Verifies story aliases across all modules."""

    def test_aliases_in_broadcaster_module(self):
        assert HandleWebSocketReconnection == handle_websocket_reconnection
        assert Handle_websocket_reconnection == handle_websocket_reconnection
        assert get_reconnection_status == handle_websocket_reconnection
        assert HandleWebSocketReconnection()["status"] == "RECOVERED"

    def test_reexports_in_event_publisher(self):
        import app.services.event_publisher as ep
        assert hasattr(ep, "DEFAULT_RECONNECTION_CONFIG")
        assert hasattr(ep, "handle_websocket_reconnection")
        assert hasattr(ep, "HandleWebSocketReconnection")
        assert hasattr(ep, "get_reconnection_config")
        assert ep.HandleWebSocketReconnection()["status"] == "RECOVERED"

    def test_reexports_in_redis_client(self):
        import app.redis_client as rc
        assert hasattr(rc, "DEFAULT_RECONNECTION_CONFIG")
        assert hasattr(rc, "handle_websocket_reconnection")
        assert hasattr(rc, "HandleWebSocketReconnection")
        assert hasattr(rc, "get_reconnection_config")
        assert rc.HandleWebSocketReconnection()["recovery_window_seconds"] == 120

    def test_reexports_in_celery_app(self):
        import app.celery_app as ca
        assert hasattr(ca, "DEFAULT_RECONNECTION_CONFIG")
        assert hasattr(ca, "handle_websocket_reconnection")
        assert hasattr(ca, "HandleWebSocketReconnection")
        assert hasattr(ca, "get_reconnection_config")
        assert ca.HandleWebSocketReconnection()["namespace"] == "/dispatch-dashboard"

    def test_reexports_and_task_in_tasks(self):
        import app.tasks as tasks
        assert hasattr(tasks, "handle_websocket_reconnection_task")
        assert hasattr(tasks, "HandleWebSocketReconnectionTask")
        assert hasattr(tasks, "Handle_websocket_reconnection_task")
        assert hasattr(tasks, "HandleWebSocketReconnection")
        res = tasks.handle_websocket_reconnection_task()
        assert res["status"] == "RECOVERED"
        assert res["recovery_window_seconds"] == 120


class TestRecoveryWindowAndFallbackSync:
    """Verifies connection-state recovery window behavior and fallback state synchronization."""

    def test_recovery_within_window(self):
        service = WebSocketReconnectionService()
        now = time.time()
        # Disconnected 30s ago (within 120s window)
        res = service.handle_reconnection(
            tenant_id="tenant-test",
            recovered=True,
            disconnected_at=now - 30,
        )
        assert res["status"] == "RECOVERED"
        assert res["recovery_window_seconds"] == 120

    def test_recovery_exceeded_window_signals_sync_required(self):
        service = WebSocketReconnectionService()
        now = time.time()
        # Disconnected 150s ago (exceeds 120s window)
        res = service.handle_reconnection(
            tenant_id="tenant-test",
            recovered=True,
            disconnected_at=now - 150,
        )
        assert res["status"] == "SYNC_REQUIRED"
        assert res["recovery_window_seconds"] == 120

    def test_socket_unrecovered_signals_sync_required(self):
        res = handle_websocket_reconnection(
            tenant_id="tenant-test",
            recovered=False,
        )
        assert res["status"] == "SYNC_REQUIRED"

    def test_validation_rejects_empty_tenant(self):
        res = handle_websocket_reconnection(tenant_id="")
        assert res["status"] == "REJECTED"
        assert "tenant_id is mandatory" in res["error"]


class TestAdminQueueRestEndpoints:
    """Verifies REST endpoints in admin_queue."""

    def test_admin_queue_reconnect_status_and_config(self):
        from app.routes.admin_queue import (
            get_websocket_reconnect_status_endpoint,
            get_websocket_reconnect_config_endpoint,
            post_websocket_reconnect_sync_endpoint,
        )

        status = get_websocket_reconnect_status_endpoint()
        assert status["status"] == "RECOVERED"
        assert status["recovery_window_seconds"] == 120

        config = get_websocket_reconnect_config_endpoint()
        assert config["status"] == "RECOVERED"
        assert config["max_disconnection_duration_ms"] == 120000

        mock_user = MagicMock()
        mock_user.tenant_id = "tenant-user-reconnect"
        post_res = post_websocket_reconnect_sync_endpoint(
            payload={"recovered": True},
            current_user=mock_user,
        )
        assert post_res["status"] == "RECOVERED"
        assert post_res["namespace"] == "/dispatch-dashboard"


class TestWebSocketServerIntegration:
    """Verifies ws-server/server.js contains connection-state recovery config and endpoints."""

    def test_ws_server_contains_reconnection_recovery_logic(self):
        server_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "ws-server", "server.js")
        )
        assert os.path.exists(server_path), f"ws-server/server.js not found at {server_path}"

        with open(server_path, "r", encoding="utf-8") as f:
            content = f.read()

        assert "connectionStateRecovery" in content
        assert "maxDisconnectionDuration" in content
        assert "skipMiddlewares" in content
        assert "socket.recovered" in content
        assert "/reconnect/status" in content
        assert "/reconnect/config" in content
        assert "/reconnect/sync" in content
