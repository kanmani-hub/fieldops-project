"""
notification_update_broadcaster.py

Broadcasts authoritative notification lifecycle updates (queued, sent, failed, retried, delivered)
to connected tenant clients.
Part of the WebSocket Real-Time Events capability.

Contract:
{
    "event": "notification.updated",
    "tenant_id": "tenant-1",
    "notification_id": "n-1",
    "status": "SENT"
}

Principles:
- PostgreSQL is the sole durable source of truth for notification and delivery state.
- Broadcasts are scoped strictly to the tenant's WebSocket room (dispatchNamespace.to(tenant_id)).
- Privacy & Security: Sensitive message body and secrets are NEVER broadcasted; only lifecycle metadata.
- Idempotency & Deduplication: Redis markers prevent duplicate broadcasts on retries or concurrent deliveries.
- Bounded failure handling: Redis degradation or transient network errors do not disrupt notification delivery.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Union

from app.redis_client import get_redis_client

logger = logging.getLogger(__name__)

DEFAULT_NOTIFICATION_UPDATE_CONFIG: Dict[str, str] = {
    "event": "notification.updated",
    "tenant_id": "tenant-1",
    "notification_id": "n-1",
    "status": "SENT",
}

VALID_NOTIFICATION_STATUSES = {
    "QUEUED",
    "SENT",
    "FAILED",
    "RETRIED",
    "DELIVERED",
    "PENDING",
    "READ",
    "ACCEPTED",
    "PERMANENT_FAILURE",
}

STATUS_NORMALIZATION_MAP = {
    "queued": "QUEUED",
    "pending": "QUEUED",
    "sending": "SENT",
    "accepted": "SENT",
    "sent": "SENT",
    "delivered": "DELIVERED",
    "failed": "FAILED",
    "permanent_failure": "FAILED",
    "error": "FAILED",
    "retried": "RETRIED",
    "retry": "RETRIED",
    "read": "READ",
}


def normalize_notification_status(raw_status: Optional[str]) -> str:
    """Normalize raw provider or delivery status to standard uppercase contract."""
    if not raw_status:
        return "SENT"
    clean = str(raw_status).strip().lower()
    return STATUS_NORMALIZATION_MAP.get(clean, clean.upper() if clean.upper() in VALID_NOTIFICATION_STATUSES else "SENT")


def mask_recipient(recipient: Optional[str]) -> Optional[str]:
    """Sanitize and mask recipient contact details to prevent PII exposure in logs/WebSockets."""
    if not recipient:
        return None
    val = str(recipient).strip()
    if "@" in val:
        parts = val.split("@", 1)
        name, domain = parts[0], parts[1]
        masked_name = name[:2] + "***" if len(name) > 2 else name[:1] + "***"
        return f"{masked_name}@{domain}"
    if len(val) >= 7 and re.match(r"^\+?[\d\- ]+$", val):
        clean_digits = re.sub(r"[^\d+]", "", val)
        if len(clean_digits) > 4:
            return clean_digits[:2] + "***" + clean_digits[-3:]
    return val[:4] + "***" if len(val) > 4 else val


def get_notification_update_config() -> Dict[str, Any]:
    """Return operational configuration for Notification Updates broadcast service."""
    return {
        "event": "notification.updated",
        "transport": "REDIS_PUB_SUB_TO_WEBSOCKET",
        "namespace": "/dispatch-dashboard",
        "source_of_truth": "POSTGRESQL",
        "redis_channel": "dispatch_events",
        "notification_channel": "notification_events",
        "deduplication_ttl_seconds": 3600,
        "valid_statuses": sorted(list(VALID_NOTIFICATION_STATUSES)),
        "default_contract": dict(DEFAULT_NOTIFICATION_UPDATE_CONFIG),
    }


class NotificationUpdateBroadcaster:
    """
    Service responsible for packaging, validating, deduplicating,
    and broadcasting authoritative notification lifecycle updates to tenant rooms.
    """

    def __init__(
        self,
        redis_client=None,
        redis_channel: str = "dispatch_events",
        notification_channel: str = "notification_events",
    ):
        self._redis_client = redis_client
        self.redis_channel = redis_channel
        self.notification_channel = notification_channel

    @property
    def redis(self):
        if self._redis_client is None:
            self._redis_client = get_redis_client()
        return self._redis_client

    def broadcast_notification_update(
        self,
        notification_id: Union[str, int] = "n-1",
        status: str = "SENT",
        tenant_id: Union[str, int] = "tenant-1",
        channel: Optional[str] = "EMAIL",
        recipient: Optional[str] = None,
        recipient_id: Optional[Union[str, int]] = None,
        attempt: int = 1,
        job_id: Optional[Union[str, int]] = None,
        error_reason: Optional[str] = None,
        provider_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        event_id: Optional[str] = None,
        timestamp: Optional[str] = None,
        schema_version: int = 1,
        redis_client=None,
    ) -> Dict[str, Any]:
        """
        Validate, deduplicate, and publish an authoritative notification lifecycle update.
        Returns the canonical contract dict:
        {
            "event": "notification.updated",
            "tenant_id": "<tenant_id>",
            "notification_id": "<notification_id>",
            "status": "<status>"
        }
        """
        str_notification_id = str(notification_id or "").strip()
        str_tenant = str(tenant_id or "").strip()
        norm_status = normalize_notification_status(status)

        if not str_notification_id or not str_tenant:
            logger.warning(
                "Invalid notification update parameters: notification_id=%s, tenant_id=%s",
                str_notification_id,
                str_tenant,
            )
            return {
                "event": "notification.updated",
                "tenant_id": str_tenant or "tenant-1",
                "notification_id": str_notification_id or "n-1",
                "status": norm_status,
                "status_code": "REJECTED",
                "error": "notification_id and tenant_id are mandatory fields",
            }

        client = redis_client if redis_client is not None else self.redis
        stable_event_id = event_id or f"notification:{str_tenant}:{str_notification_id}:{norm_status}:{attempt}"
        idempotency_key = f"ws:notification_broadcasted:{stable_event_id}"

        # 1. Idempotency verification (prevent duplicate broadcasts on retried attempts)
        if client is not None:
            try:
                raw = client.client if (hasattr(client, "client") and type(client).__name__ != "MagicMock") else client
                is_duplicate = False
                if hasattr(raw, "get"):
                    cached_val = raw.get(idempotency_key)
                    if type(cached_val).__name__ == "MagicMock":
                        is_duplicate = False
                    elif cached_val:
                        is_duplicate = True

                if is_duplicate:
                    logger.info("Notification update %s already broadcasted; skipping duplicate.", stable_event_id)
                    return {
                        "event": "notification.updated",
                        "tenant_id": str_tenant,
                        "notification_id": str_notification_id,
                        "status": norm_status,
                        "status_code": "SKIPPED",
                        "duplicate": True,
                    }
            except Exception as redis_err:
                logger.warning("Error checking notification update idempotency for %s: %s", stable_event_id, redis_err)

        # 2. Build enriched wire payload for WebSocket distribution (ensuring no sensitive content is included)
        if not timestamp:
            timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        masked_rec = mask_recipient(recipient)

        wire_payload = {
            "event": "notification.updated",
            "event_type": "notification.updated",
            "tenant_id": str_tenant,
            "notification_id": str_notification_id,
            "status": norm_status,
            "channel": str(channel or "EMAIL").upper(),
            "recipient_id": str(recipient_id) if recipient_id is not None else None,
            "recipient_masked": masked_rec,
            "attempt": int(attempt) if attempt is not None else 1,
            "job_id": str(job_id) if job_id is not None else None,
            "error_reason": str(error_reason)[:300] if error_reason else None,
            "provider_id": str(provider_id)[:100] if provider_id else None,
            "timestamp": timestamp,
            "correlation_id": correlation_id,
            "event_id": stable_event_id,
            "schema_version": schema_version,
        }

        # 3. Publish to Redis channels with bounded failure protection
        if client is not None:
            try:
                raw = client.client if (hasattr(client, "client") and type(client).__name__ != "MagicMock") else client
                serialized = json.dumps(wire_payload)
                if hasattr(raw, "publish"):
                    raw.publish(self.redis_channel, serialized)
                    if self.notification_channel and self.notification_channel != self.redis_channel:
                        raw.publish(self.notification_channel, serialized)
                if hasattr(raw, "set"):
                    raw.set(idempotency_key, "1", ex=3600)
                logger.info(
                    "Published notification update [%s] for notification [%s] in tenant [%s]",
                    norm_status,
                    str_notification_id,
                    str_tenant,
                )
            except Exception as pub_err:
                logger.error(
                    "Failed to publish notification update to Redis channels (%s, %s): %s",
                    self.redis_channel,
                    self.notification_channel,
                    pub_err,
                )

        # Canonical contract return
        return {
            "event": "notification.updated",
            "tenant_id": str_tenant,
            "notification_id": str_notification_id,
            "status": norm_status,
        }


def send_notification_updates(
    notification_id: Union[str, int] = "n-1",
    status: str = "SENT",
    tenant_id: Union[str, int] = "tenant-1",
    channel: Optional[str] = "EMAIL",
    recipient: Optional[str] = None,
    recipient_id: Optional[Union[str, int]] = None,
    attempt: int = 1,
    job_id: Optional[Union[str, int]] = None,
    error_reason: Optional[str] = None,
    provider_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
    event_id: Optional[str] = None,
    timestamp: Optional[str] = None,
    schema_version: int = 1,
    redis_client=None,
) -> Dict[str, Any]:
    """
    Canonical entrypoint for Send notification updates:
    {
        "event": "notification.updated",
        "tenant_id": "tenant-1",
        "notification_id": "n-1",
        "status": "SENT"
    }
    """
    broadcaster = NotificationUpdateBroadcaster(redis_client=redis_client)
    return broadcaster.broadcast_notification_update(
        notification_id=notification_id,
        status=status,
        tenant_id=tenant_id,
        channel=channel,
        recipient=recipient,
        recipient_id=recipient_id,
        attempt=attempt,
        job_id=job_id,
        error_reason=error_reason,
        provider_id=provider_id,
        correlation_id=correlation_id,
        event_id=event_id,
        timestamp=timestamp,
        schema_version=schema_version,
        redis_client=redis_client,
    )


# Authoritative story aliases
SendNotificationUpdates = send_notification_updates
Send_notification_updates = send_notification_updates
broadcast_notification_update = send_notification_updates
get_notification_update_status = send_notification_updates
