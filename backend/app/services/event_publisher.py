import json
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

def publish_dispatch_event(
    redis_client,
    event_type: str,
    job_id: str,
    old_status: str,
    new_status: str,
    tenant_id: str,
    technician_id: Optional[str] = None,
    technician_name: Optional[str] = None
):
    """
    Publish a dispatch event to the Redis Pub/Sub channel 'dispatch_events'
    so the Node.js WebSocket server can broadcast it to dashboard clients.
    """
    if not redis_client:
        logger.warning("Redis client unavailable; skipping dispatch event publish.")
        return

    payload = {
        "event": event_type,
        "job_id": str(job_id),
        "old_status": old_status,
        "new_status": new_status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tenant_id": tenant_id
    }

    if technician_id and technician_name:
        payload["technician"] = {
            "tech_id": technician_id,
            "name": technician_name
        }

    try:
        redis_client.publish("dispatch_events", json.dumps(payload))
        logger.info(f"Published {event_type} for job {job_id} to dispatch_events channel.")
    except Exception as e:
        logger.error(f"Failed to publish dispatch event to Redis: {e}")
