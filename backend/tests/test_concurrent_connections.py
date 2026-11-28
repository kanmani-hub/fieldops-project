"""
test_concurrent_connections.py

Automated integration and stress test suite for the story:
Test concurrent connections as an integrated part of the
WebSocket Real-Time Events capability.

Expected Output (JSON):
{
    "status": "PASSED",
    "scenarios": [
        "same_tenant_concurrency",
        "multi_tenant_isolation",
        "reconnect",
        "event_broadcast"
    ]
}
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest

from app.services.websocket_auth_service import (
    JWT_ALGORITHM,
    _get_jwt_secret,
    validate_connection_authentication,
)
from app.services.websocket_failure_service import handle_websocket_failures
from app.services.websocket_reconnection_service import (
    CONCURRENT_CONNECTION_SCENARIOS,
    DEFAULT_CONCURRENT_CONNECTIONS_CONFIG,
    RECOVERY_WINDOW_SECONDS,
    ConcurrentConnectionsTester,
    TestConcurrentConnections,
    Test_concurrent_connections,
    get_concurrent_connections_config,
    get_concurrent_connections_status,
    get_concurrent_connections_test_results,
    handle_websocket_reconnection,
    run_concurrent_connections_simulation,
    test_concurrent_connections as api_test_concurrent_connections,
)
from app.services.websocket_tenant_filter import (
    filter_tenant_events,
    format_tenant_room,
)


EXPECTED_CONTRACT = {
    "status": "PASSED",
    "scenarios": [
        "same_tenant_concurrency",
        "multi_tenant_isolation",
        "reconnect",
        "event_broadcast",
    ],
}


def _make_jwt(user_id: str, tenant_id: str, role: str = "dispatcher", exp_delta: int = 3600) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": user_id,
            "tenant_id": tenant_id,
            "role": role,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=exp_delta)).timestamp()),
            "type": "access",
        },
        _get_jwt_secret(),
        algorithm=JWT_ALGORITHM,
    )


def _run_sync(coro):
    try:
        coro.send(None)
    except StopIteration as exc:
        return exc.value
    finally:
        coro.close()


class TestConcurrentConnectionsContractAndExports:
    """Verifies canonical JSON contract, config, aliases, re-exports, and endpoints."""

    def test_canonical_contract(self):
        res = api_test_concurrent_connections()
        assert res == EXPECTED_CONTRACT
        assert DEFAULT_CONCURRENT_CONNECTIONS_CONFIG == EXPECTED_CONTRACT

    def test_operational_config(self):
        cfg = get_concurrent_connections_config()
        assert cfg["status"] == "PASSED"
        assert cfg["scenarios"] == EXPECTED_CONTRACT["scenarios"]
        assert cfg["namespace"] == "/dispatch-dashboard"
        assert cfg["max_connections_per_ip"] == 100
        assert cfg["max_connections_per_tenant"] == 200
        assert cfg["recovery_window_seconds"] == 120
        assert cfg["dedup_cache_max_entries"] == 10000
        assert cfg["max_redis_backoff_ms"] == 3000
        assert cfg["source_of_truth"] == "POSTGRESQL"

    def test_aliases_reexports_and_celery_task(self):
        assert TestConcurrentConnections is api_test_concurrent_connections
        assert Test_concurrent_connections is api_test_concurrent_connections
        assert get_concurrent_connections_status is api_test_concurrent_connections
        assert get_concurrent_connections_test_results is api_test_concurrent_connections

        import app.services.event_publisher as ep
        import app.redis_client as rc
        import app.celery_app as ca
        import app.tasks as tasks

        for mod in (ep, rc, ca, tasks):
            assert hasattr(mod, "DEFAULT_CONCURRENT_CONNECTIONS_CONFIG")
            assert hasattr(mod, "test_concurrent_connections")
            assert hasattr(mod, "run_concurrent_connections_simulation")
            assert hasattr(mod, "TestConcurrentConnections")
            assert mod.test_concurrent_connections() == EXPECTED_CONTRACT

        assert tasks.test_concurrent_connections_task() == EXPECTED_CONTRACT
        assert tasks.TestConcurrentConnectionsTask() == EXPECTED_CONTRACT
        assert tasks.Test_concurrent_connections_task() == EXPECTED_CONTRACT

    def test_admin_queue_concurrency_endpoints(self):
        from app.routes.admin_queue import (
            get_websocket_concurrency_config_endpoint,
            get_websocket_concurrency_status_endpoint,
            post_run_websocket_concurrency_endpoint,
        )

        assert get_websocket_concurrency_status_endpoint() == EXPECTED_CONTRACT
        cfg = get_websocket_concurrency_config_endpoint()
        assert cfg["status"] == "PASSED"
        assert cfg["max_connections_per_tenant"] == 200

        run_res = post_run_websocket_concurrency_endpoint(
            {"clients_per_tenant": 8, "tenant_count": 3, "events_per_tenant": 4}
        )
        assert run_res["status"] == "PASSED"
        assert run_res["overall_success"] is True


class TestSameTenantConcurrency:
    """Verifies many authenticated clients in the same tenant receive events without conflict."""

    def test_many_clients_same_tenant_concurrent_auth_and_delivery(self):
        tenant_id = "tenant-stress-same"
        client_count = 30

        def authenticate_and_filter(idx: int):
            token = _make_jwt(user_id=f"disp-{idx}", tenant_id=tenant_id)
            auth_res = validate_connection_authentication(token=token)
            assert auth_res["authenticated"] is True
            assert auth_res["tenant_id"] == tenant_id

            room = format_tenant_room(auth_res["tenant_id"])
            filt = filter_tenant_events(
                event_payload={
                    "event": "job.status.updated",
                    "tenant_id": tenant_id,
                    "job_id": "job-888",
                    "status": "EN_ROUTE",
                    "correlation_id": f"corr-same-{idx}",
                    "event_id": f"evt-same-{idx}",
                },
                socket_tenant_id=auth_res["tenant_id"],
                target_room=room,
                redis_client=False,
            )
            return filt

        with ThreadPoolExecutor(max_workers=10) as pool:
            results = list(pool.map(authenticate_and_filter, range(client_count)))

        assert len(results) == client_count
        assert all(r["allowed"] is True and r["status"] == "FILTERED" for r in results)
        assert all(r["room"] == f"tenant:{tenant_id}" for r in results)


class TestMultiTenantConcurrentIsolation:
    """Verifies simultaneous clients across multiple tenants never leak cross-tenant events."""

    def test_simultaneous_multi_tenant_clients_and_socket_manager_isolation(self):
        from app.services import socket_manager as sm

        tenants = ["tenant-alpha", "tenant-beta", "tenant-gamma", "tenant-delta"]
        clients_per_tenant = 8

        tasks_input = [
            (t_idx, c_idx, tenants[t_idx], tenants[(t_idx + 1) % len(tenants)])
            for t_idx in range(len(tenants))
            for c_idx in range(clients_per_tenant)
        ]

        def check_tenant_client(item):
            t_idx, c_idx, own_tenant, other_tenant = item
            token = _make_jwt(user_id=f"u-{t_idx}-{c_idx}", tenant_id=own_tenant)
            auth = validate_connection_authentication(token=token, tenant_id=other_tenant)
            # Impersonation attempt overridden to verified token claim
            assert auth["tenant_id"] == own_tenant

            own_ok = filter_tenant_events(
                event_payload={
                    "event": "dispatch.updated",
                    "tenant_id": own_tenant,
                    "job_id": f"j-{t_idx}-{c_idx}",
                    "correlation_id": f"corr-mt-{t_idx}-{c_idx}",
                },
                socket_tenant_id=own_tenant,
                redis_client=False,
            )
            cross_blocked = filter_tenant_events(
                event_payload={
                    "event": "dispatch.updated",
                    "tenant_id": other_tenant,
                    "job_id": f"j-{t_idx}-{c_idx}",
                    "correlation_id": f"corr-cross-{t_idx}-{c_idx}",
                },
                socket_tenant_id=own_tenant,
                redis_client=False,
            )
            return own_ok["allowed"], cross_blocked["allowed"]

        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(check_tenant_client, tasks_input))

        assert len(outcomes) == len(tenants) * clients_per_tenant
        assert all(own is True and cross is False for own, cross in outcomes)

        # Verify WebSocketManager delivers only matching-tenant broadcasts
        with patch.object(sm.sio, "emit", new_callable=AsyncMock) as mock_emit:
            for t in tenants:
                assert _run_sync(
                    sm.ws_manager.broadcast_to_tenant(
                        t,
                        {"event": "payment.updated", "tenant_id": t, "invoice_id": "inv-1"},
                    )
                ) is True
                other = tenants[(tenants.index(t) + 1) % len(tenants)]
                assert _run_sync(
                    sm.ws_manager.broadcast_to_tenant(
                        t,
                        {"event": "payment.updated", "tenant_id": other, "invoice_id": "inv-leak"},
                    )
                ) is False
            assert mock_emit.call_count == len(tenants)


class TestConcurrentBroadcastsReconnectsAndFailures:
    """Verifies concurrent broadcasts across streams, reconnect recovery, and Redis/Kafka interruptions."""

    def test_concurrent_multi_stream_broadcasts_and_idempotency(self):
        from app.services.dispatch_update_broadcaster import send_dispatch_updates
        from app.services.job_status_broadcaster import send_job_status_updates
        from app.services.kafka_to_websocket_bridge import KafkaToWebSocketBridge
        from app.services.notification_update_broadcaster import send_notification_updates
        from app.services.payment_update_broadcaster import send_payment_updates
        from app.services.sla_alert_broadcaster import send_sla_alerts
        from app.services.technician_location_broadcaster import send_technician_location_updates

        published = []
        seen_keys = set()

        mock_redis = MagicMock()
        mock_redis.get.side_effect = lambda k: b"1" if k in seen_keys else None

        def _set(k, v, ex=None):
            seen_keys.add(k)
            return True

        mock_redis.set.side_effect = _set
        mock_redis.publish.side_effect = lambda ch, msg: published.append((ch, json.loads(msg)))

        bridge = KafkaToWebSocketBridge(redis_client=mock_redis)

        def emit_stream(idx: int):
            t_id = f"tenant-stream-{idx % 3}"
            corr = f"corr-stream-{idx}"
            send_job_status_updates(job_id=f"j-{idx}", status="ASSIGNED", tenant_id=t_id, correlation_id=corr, redis_client=mock_redis)
            send_dispatch_updates(job_id=f"j-{idx}", assignment_id=f"a-{idx}", tenant_id=t_id, correlation_id=corr, redis_client=mock_redis)
            send_technician_location_updates(technician_id=f"tech-{idx}", latitude=13.0, longitude=80.2, tenant_id=t_id, correlation_id=corr, force_broadcast=True, redis_client=mock_redis)
            send_sla_alerts(job_id=f"j-{idx}", severity="AT_RISK", tenant_id=t_id, correlation_id=corr, redis_client=mock_redis)
            send_notification_updates(notification_id=f"n-{idx}", status="SENT", tenant_id=t_id, correlation_id=corr, redis_client=mock_redis)
            send_payment_updates(invoice_id=f"inv-{idx}", payment_status="PAID", tenant_id=t_id, correlation_id=corr, redis_client=mock_redis)
            bridge.bridge_event(
                {
                    "event_id": f"kafka-evt-{idx}",
                    "event_type": "job-completed",
                    "tenant_id": t_id,
                    "job_id": f"j-{idx}",
                    "correlation_id": corr,
                },
                redis_client=mock_redis,
            )

        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(emit_stream, range(12)))

        assert len(published) > 0
        # Every published event retains tenant_id and correlation_id
        for _, msg in published:
            assert msg.get("tenant_id", "").startswith("tenant-stream-")
            assert msg.get("correlation_id", "").startswith("corr-stream-")

        # Re-delivering the exact same Kafka event_id must be skipped idempotently
        dup_res = bridge.bridge_event(
            {
                "event_id": "kafka-evt-0",
                "event_type": "job-completed",
                "tenant_id": "tenant-stream-0",
                "job_id": "j-0",
                "correlation_id": "corr-stream-0",
            },
            redis_client=mock_redis,
        )
        assert dup_res["status"] == "SKIPPED"
        assert dup_res["idempotent_duplicate"] is True

    def test_concurrent_reconnects_and_redis_interruption_bounded_recovery(self):
        mock_redis = MagicMock()
        mock_redis.get.side_effect = ConnectionError("Simulated Redis outage")
        mock_redis.publish.side_effect = ConnectionError("Simulated Redis outage")

        now = time.time()

        def reconnect_and_fail(idx: int):
            tenant = f"tenant-rec-{idx % 4}"
            in_win = handle_websocket_reconnection(
                tenant_id=tenant,
                socket_id=f"s-{idx}",
                recovered=True,
                disconnected_at=now - 20,
                redis_client=mock_redis,
            )
            expired = handle_websocket_reconnection(
                tenant_id=tenant,
                socket_id=f"s-{idx}",
                recovered=True,
                disconnected_at=now - (RECOVERY_WINDOW_SECONDS + 40),
                redis_client=mock_redis,
            )
            fail_res = handle_websocket_failures(
                status="DEGRADED_OR_RECOVERED",
                error_type="REDIS_INTERRUPTION",
                tenant_id=tenant,
                redis_client=mock_redis,
            )
            return in_win["status"], expired["status"], fail_res["failure_handling"]

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(reconnect_and_fail, range(24)))

        assert all(r == ("RECOVERED", "SYNC_REQUIRED", "ENABLED") for r in results)

    def test_end_to_end_concurrent_simulation_runner_and_bounds(self):
        # Verify excessive inputs are safely bounded and all scenarios pass
        res = run_concurrent_connections_simulation(
            clients_per_tenant=500,  # bounded internally to 100
            tenant_count=100,        # bounded internally to 20
            events_per_tenant=200,   # bounded internally to 50
            redis_client=False,
        )
        assert res["status"] == "PASSED"
        assert res["overall_success"] is True
        assert res["scenarios"] == list(CONCURRENT_CONNECTION_SCENARIOS)
        assert res["metrics"]["clients_per_tenant"] == 100
        assert res["metrics"]["tenants_tested"] == 20
        assert res["metrics"]["cross_tenant_leaks"] == 0
        for sc in CONCURRENT_CONNECTION_SCENARIOS:
            assert res["scenario_results"][sc]["status"] == "PASSED"


class TestWsServerAndFrontendConcurrencyIntegration:
    """Verifies ws-server/server.js and frontend dispatcherService.ts support bounded concurrency."""

    def test_ws_server_contains_concurrency_limits_cleanup_and_endpoints(self):
        server_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "ws-server", "server.js")
        )
        assert os.path.exists(server_path)
        with open(server_path, "r", encoding="utf-8") as f:
            content = f.read()

        assert "MAX_CONNECTIONS_PER_IP" in content
        assert "MAX_CONNECTIONS_PER_TENANT" in content
        assert "tenantConnectionCounts" in content
        assert "decrementCounter" in content
        assert "rejectHandshake" in content
        assert "SEEN_EVENTS_MAX" in content
        assert "/concurrency/status" in content
        assert "/concurrency/config" in content
        assert "/concurrency/run" in content
        assert "active_tenants" in content
        assert "seen_events_cache_size" in content

    def test_frontend_dispatcher_service_contains_concurrent_stream_and_dedup(self):
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
        with open(disp_path, "r", encoding="utf-8") as f:
            content = f.read()

        assert "DEFAULT_CONCURRENT_CONNECTIONS_CONFIG" in content
        assert "createBoundedEventDeduplicator" in content
        assert "subscribeConcurrentTenantEvents" in content
        assert "same_tenant_concurrency" in content
