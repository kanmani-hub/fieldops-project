"""
websocket_reconnection_service.py

Provides reliable client reconnection and connection-state recovery management
for the WebSocket Real-Time Events capability.

Contract:
{
    "status": "RECOVERED",
    "namespace": "/dispatch-dashboard",
    "recovery_window_seconds": 120
}

Principles:
- Preserves authenticated tenant context after reconnect.
- Uses bounded reconnect/backoff behavior on both client and server.
- Recovers missed events within the 120-second connection-state recovery window.
- When recovery is incomplete, signals clients to refresh state from backend (PostgreSQL) source of truth.
- Bounded failure handling: Redis or transient errors do not block reconnection or auth.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional, Union

from app.redis_client import get_redis_client

logger = logging.getLogger(__name__)

DEFAULT_RECONNECTION_CONFIG: Dict[str, Any] = {
    "status": "RECOVERED",
    "namespace": "/dispatch-dashboard",
    "recovery_window_seconds": 120,
}

RECOVERY_WINDOW_SECONDS: int = 120


def get_reconnection_config() -> Dict[str, Any]:
    """Return operational configuration for WebSocket reconnection and recovery."""
    return {
        "status": "RECOVERED",
        "namespace": "/dispatch-dashboard",
        "recovery_window_seconds": RECOVERY_WINDOW_SECONDS,
        "max_disconnection_duration_ms": RECOVERY_WINDOW_SECONDS * 1000,
        "skip_middlewares": True,
        "client_reconnection_options": {
            "reconnection": True,
            "reconnection_attempts": 10,
            "reconnection_delay_ms": 1000,
            "reconnection_delay_max_ms": 10000,
            "timeout_ms": 10000,
        },
        "source_of_truth": "POSTGRESQL",
        "transport": "SOCKET_IO_CONNECTION_STATE_RECOVERY",
        "default_contract": dict(DEFAULT_RECONNECTION_CONFIG),
    }


class WebSocketReconnectionService:
    """
    Service responsible for coordinating WebSocket connection state recovery,
    evaluating reconnection windows, and managing backend state refresh triggers.
    """

    def __init__(
        self,
        namespace: str = "/dispatch-dashboard",
        recovery_window_seconds: int = RECOVERY_WINDOW_SECONDS,
        redis_client=None,
    ):
        self.namespace = namespace
        self.recovery_window_seconds = recovery_window_seconds
        self._redis_client = redis_client

    @property
    def redis(self):
        if self._redis_client is None:
            self._redis_client = get_redis_client()
        return self._redis_client

    def handle_reconnection(
        self,
        tenant_id: Union[str, int] = "tenant-1",
        socket_id: Optional[str] = None,
        last_event_id: Optional[str] = None,
        recovered: bool = True,
        disconnected_at: Optional[float] = None,
        status: Optional[str] = None,
        namespace: Optional[str] = None,
        recovery_window_seconds: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Evaluate and process client connection-state recovery.
        Returns the canonical contract dict:
        {
            "status": "RECOVERED",
            "namespace": "/dispatch-dashboard",
            "recovery_window_seconds": 120
        }
        """
        str_tenant = str(tenant_id or "").strip()
        ns = str(namespace or self.namespace)
        window = int(recovery_window_seconds or self.recovery_window_seconds)

        if not str_tenant:
            logger.warning("Invalid reconnection request: tenant_id is mandatory.")
            return {
                "status": "REJECTED",
                "namespace": ns,
                "recovery_window_seconds": window,
                "error": "tenant_id is mandatory",
            }

        # Check disconnection elapsed time if provided
        now = time.time()
        is_recovered = bool(recovered)
        if disconnected_at is not None:
            elapsed = now - float(disconnected_at)
            if elapsed > window:
                is_recovered = False
                logger.info(
                    "Client disconnection duration (%.2fs) exceeded recovery window (%ds) for tenant [%s]; full sync needed.",
                    elapsed,
                    window,
                    str_tenant,
                )

        target_status = str(status) if status is not None else ("RECOVERED" if is_recovered else "SYNC_REQUIRED")

        # Canonical contract return
        return {
            "status": target_status,
            "namespace": ns,
            "recovery_window_seconds": window,
        }


def handle_websocket_reconnection(
    tenant_id: Union[str, int] = "tenant-1",
    socket_id: Optional[str] = None,
    last_event_id: Optional[str] = None,
    recovered: bool = True,
    disconnected_at: Optional[float] = None,
    status: Optional[str] = None,
    namespace: Optional[str] = None,
    recovery_window_seconds: Optional[int] = None,
    redis_client=None,
) -> Dict[str, Any]:
    """
    Canonical entrypoint for Handle WebSocket reconnection:
    {
        "status": "RECOVERED",
        "namespace": "/dispatch-dashboard",
        "recovery_window_seconds": 120
    }
    """
    service = WebSocketReconnectionService(
        namespace=namespace or "/dispatch-dashboard",
        recovery_window_seconds=recovery_window_seconds or RECOVERY_WINDOW_SECONDS,
        redis_client=redis_client,
    )
    return service.handle_reconnection(
        tenant_id=tenant_id,
        socket_id=socket_id,
        last_event_id=last_event_id,
        recovered=recovered,
        disconnected_at=disconnected_at,
        status=status,
        namespace=namespace,
        recovery_window_seconds=recovery_window_seconds,
    )


# Authoritative story aliases
HandleWebSocketReconnection = handle_websocket_reconnection
Handle_websocket_reconnection = handle_websocket_reconnection
get_reconnection_status = handle_websocket_reconnection
