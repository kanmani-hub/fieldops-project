from typing import Optional, Any, Dict, List
from fastapi import APIRouter, Depends, HTTPException, status

from app.services.task_queue import PriorityTaskQueue
from app.redis_client import get_redis_client

from app.auth.dependencies import (
    AuthenticatedUser,
    get_current_user,
    require_permission,
)
from app.auth.rbac import Permission


router = APIRouter(
    prefix="/admin/queue",
    tags=["Admin Queue"],
)


def get_task_queue() -> PriorityTaskQueue:
    redis = get_redis_client()
    return PriorityTaskQueue(redis)


def _resolve_authorized_ws_tenant(payload: dict, current_user: AuthenticatedUser) -> str:
    """Resolve and enforce tenant isolation for admin WebSocket broadcast endpoints."""
    user_tenant = getattr(current_user, "tenant_id", None)
    user_role = str(getattr(getattr(current_user, "role", None), "value", getattr(current_user, "role", "")) or "").lower()
    requested_tenant = payload.get("tenant_id") if isinstance(payload, dict) else None

    if requested_tenant is not None and str(requested_tenant).strip():
        req_str = str(requested_tenant).strip()
        if user_tenant and str(user_tenant).strip() != req_str and user_role != "super_admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cross-tenant event broadcast forbidden",
            )
        return req_str

    return str(user_tenant or "tenant-1")


@router.get(
    "/stats",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_queue_stats(
    queue: PriorityTaskQueue = Depends(get_task_queue),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Return queue statistics for the authenticated user's tenant.
    """

    return queue.stats(
        tenant_id=current_user.tenant_id
    )

@router.get(
    "/notifications",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_notification_queue_status() -> dict:
    """Return the configuration and status of the dedicated notification Celery queue."""
    from app.celery_app import get_notification_queue_config
    return get_notification_queue_config()


@router.post(
    "/notifications/configure",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def configure_notification_queue_endpoint() -> dict:
    """Ensure the notification queue is configured on the Celery application."""
    from app.celery_app import create_notification_queue
    return create_notification_queue()

@router.get(
    "/gps",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_gps_queue_status() -> dict:
    """Return the configuration and status of the dedicated GPS processing Celery queue."""
    from app.celery_app import get_gps_processing_queue_config
    return get_gps_processing_queue_config()


@router.post(
    "/gps/configure",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def configure_gps_queue_endpoint() -> dict:
    """Ensure the GPS processing queue is configured on the Celery application."""
    from app.celery_app import create_gps_processing_queue
    return create_gps_processing_queue()

@router.get(
    "/billing",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_billing_queue_status() -> dict:
    """Return the configuration and status of the dedicated billing Celery queue."""
    from app.celery_app import get_billing_queue_config
    return get_billing_queue_config()


@router.post(
    "/billing/configure",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def configure_billing_queue_endpoint() -> dict:
    """Ensure the billing queue is configured on the Celery application."""
    from app.celery_app import create_billing_queue
    return create_billing_queue()

@router.get(
    "/ai",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_ai_queue_status() -> dict:
    """Return the configuration and status of the dedicated AI processing Celery queue."""
    from app.celery_app import get_ai_processing_queue_config
    return get_ai_processing_queue_config()


@router.post(
    "/ai/configure",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def configure_ai_queue_endpoint() -> dict:
    """Ensure the AI processing queue is configured on the Celery application."""
    from app.celery_app import create_ai_processing_queue
    return create_ai_processing_queue()

@router.get(
    "/high-priority",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_high_priority_queue_status() -> dict:
    """Return the configuration and status of the dedicated high-priority Celery queue."""
    from app.celery_app import get_high_priority_queue_config
    return get_high_priority_queue_config()


@router.post(
    "/high-priority/configure",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def configure_high_priority_queue_endpoint() -> dict:
    """Ensure the high-priority queue is configured on the Celery application."""
    from app.celery_app import create_high_priority_queue
    return create_high_priority_queue()


# Aliases for /priority
@router.get(
    "/priority",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_priority_queue_status_alias() -> dict:
    return get_high_priority_queue_status()


@router.post(
    "/priority/configure",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def configure_priority_queue_endpoint_alias() -> dict:
    return configure_high_priority_queue_endpoint()

@router.get(
    "/reports",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_report_queue_status() -> dict:
    """Return the configuration and status of the dedicated report-generation Celery queue."""
    from app.celery_app import get_report_generation_queue_config
    return get_report_generation_queue_config()


@router.post(
    "/reports/configure",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def configure_report_queue_endpoint() -> dict:
    """Ensure the report-generation queue is configured on the Celery application."""
    from app.celery_app import create_report_generation_queue
    return create_report_generation_queue()


# Aliases for /report-generation
@router.get(
    "/report-generation",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_report_generation_status_alias() -> dict:
    return get_report_queue_status()


@router.post(
    "/report-generation/configure",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def configure_report_generation_endpoint_alias() -> dict:
    return configure_report_queue_endpoint()

@router.get(
    "/retry-rules",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_retry_rules_status() -> dict:
    """Return the configuration and status of task retry rules."""
    from app.celery_app import get_task_retry_rules_config
    return get_task_retry_rules_config()


@router.post(
    "/retry-rules/configure",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def configure_retry_rules_endpoint() -> dict:
    """Ensure task retry rules are configured on the Celery application."""
    from app.celery_app import configure_task_retry_rules
    return configure_task_retry_rules()


# Aliases for /retry-policy
@router.get(
    "/retry-policy",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_retry_policy_status_alias() -> dict:
    return get_retry_rules_status()


@router.post(
    "/retry-policy/configure",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def configure_retry_policy_endpoint_alias() -> dict:
    return configure_retry_rules_endpoint()


# ---------------------------------------------------------------------------
# Failed Task Handling (DLQ & Safe Requeue Support)
# ---------------------------------------------------------------------------

@router.get(
    "/failed-handling",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_failed_handling_status() -> dict:
    """Return the configuration and status of failed task handling."""
    from app.celery_app import get_failed_task_handling_config
    return get_failed_task_handling_config()


@router.post(
    "/failed-handling/configure",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def configure_failed_handling_endpoint() -> dict:
    """Ensure failed task handling is configured on Celery."""
    from app.celery_app import configure_failed_task_handling
    return configure_failed_task_handling()


# Aliases for /dlq/config and /failed-tasks
@router.get(
    "/dlq/config",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_dlq_config_alias() -> dict:
    return get_failed_handling_status()


@router.post(
    "/dlq/configure",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def configure_dlq_endpoint_alias() -> dict:
    return configure_failed_handling_endpoint()


@router.get(
    "/failed-tasks",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_failed_tasks_status_alias() -> dict:
    return get_failed_handling_status()


@router.post(
    "/failed-tasks/configure",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def configure_failed_tasks_endpoint_alias() -> dict:
    return configure_failed_handling_endpoint()


# ---------------------------------------------------------------------------
# Task Timeouts (Bounded Execution & Worker Timeouts)
# ---------------------------------------------------------------------------

@router.get(
    "/timeouts",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_task_timeouts_status() -> dict:
    """Return the configuration and status of task timeouts."""
    from app.celery_app import get_task_timeouts_config
    return get_task_timeouts_config()


@router.post(
    "/timeouts/configure",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def configure_task_timeouts_endpoint() -> dict:
    """Ensure task timeouts are configured on Celery."""
    from app.celery_app import configure_task_timeouts
    return configure_task_timeouts()


# Aliases for /task-timeouts
@router.get(
    "/task-timeouts",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_task_timeouts_status_alias() -> dict:
    return get_task_timeouts_status()


@router.post(
    "/task-timeouts/configure",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def configure_task_timeouts_endpoint_alias() -> dict:
    return configure_task_timeouts_endpoint()


# ---------------------------------------------------------------------------
# Celery Beat Scheduled Tasks
# ---------------------------------------------------------------------------

@router.get(
    "/schedules",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_scheduled_tasks_status() -> dict:
    """Return the configuration and status of Celery Beat scheduled tasks."""
    from app.celery_app import get_scheduled_tasks_config
    return get_scheduled_tasks_config()


@router.post(
    "/schedules/configure",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def configure_scheduled_tasks_endpoint() -> dict:
    """Ensure Celery Beat scheduled tasks are registered and active."""
    from app.celery_app import add_scheduled_tasks
    return add_scheduled_tasks()


# Aliases for /scheduled-tasks
@router.get(
    "/scheduled-tasks",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_scheduled_tasks_status_alias() -> dict:
    return get_scheduled_tasks_status()


@router.post(
    "/scheduled-tasks/configure",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def configure_scheduled_tasks_endpoint_alias() -> dict:
    return configure_scheduled_tasks_endpoint()


# ---------------------------------------------------------------------------
# Task Result Tracking Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/tracking/config",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_task_result_tracking_config_endpoint() -> dict:
    """Return canonical contract for task result tracking."""
    from app.celery_app import get_task_result_tracking_config
    return get_task_result_tracking_config()


@router.post(
    "/tracking/configure",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def configure_task_result_tracking_endpoint() -> dict:
    """Configure task result tracking on Celery."""
    from app.celery_app import add_task_result_tracking
    return add_task_result_tracking()


@router.get(
    "/tasks/{task_id}/result",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_task_result_endpoint(task_id: str) -> dict:
    """Retrieve operational lifecycle/result metadata for a specific task."""
    from app.celery_app import get_task_result
    result = get_task_result(task_id)
    if result is None:
        return {
            "status": "TRACKED",
            "task_id": task_id,
            "state": "PENDING",
            "queue": "default",
        }
    return {
        "status": "TRACKED",
        "task_id": result.get("task_id", task_id),
        "state": result.get("state", "UNKNOWN"),
        "queue": result.get("queue", "default"),
    }


# Aliases for /results/{task_id} and /task-result
@router.get(
    "/results/{task_id}",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_task_result_alias(task_id: str) -> dict:
    return get_task_result_endpoint(task_id)


@router.post(
    "/tasks/track",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def track_task_endpoint(payload: dict) -> dict:
    """Explicitly record or update task result tracking."""
    from app.celery_app import track_task_result
    return track_task_result(
        task_id=payload.get("task_id", "example-task-id"),
        state=payload.get("state", "SUCCESS"),
        queue=payload.get("queue", "notifications"),
        tenant_id=payload.get("tenant_id"),
        correlation_id=payload.get("correlation_id"),
        result_data=payload.get("result_data"),
    )


# =============================================================================
# Story: Add Celery Monitoring Endpoints
# =============================================================================

@router.get(
    "/monitoring",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_celery_monitoring_status_endpoint() -> dict:
    """Return the configuration and status of Celery operational monitoring."""
    from app.celery_app import get_celery_monitoring_config
    return get_celery_monitoring_config()


@router.get(
    "/monitoring/config",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_celery_monitoring_config_endpoint() -> dict:
    """Return canonical Celery monitoring configuration contract."""
    from app.celery_app import get_celery_monitoring_config
    return get_celery_monitoring_config()


@router.post(
    "/monitoring/configure",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def configure_celery_monitoring_endpoint() -> dict:
    """Ensure Celery operational monitoring is active."""
    from app.celery_app import add_celery_monitoring
    return add_celery_monitoring()


@router.get(
    "/monitoring/metrics",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_celery_monitoring_metrics_endpoint() -> dict:
    """Return aggregated Celery operational telemetry (queue depth, latency, failures, worker health)."""
    from app.celery_app import get_celery_monitoring_metrics
    return get_celery_monitoring_metrics()


@router.get(
    "/monitoring/health",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_celery_worker_health_endpoint() -> dict:
    """Inspect and return Celery worker health and availability."""
    from app.celery_app import get_celery_worker_health
    return get_celery_worker_health()


@router.get(
    "/monitoring/queues",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_celery_queues_depth_endpoint() -> dict:
    """Return current queue depths across all registered operational queues."""
    from app.celery_app import get_all_queue_depths
    return get_all_queue_depths()


@router.post(
    "/monitoring/record",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def record_celery_metric_endpoint(payload: dict) -> dict:
    """Record a metric event (failure, retry, latency) into Celery monitoring telemetry."""
    from app.celery_app import record_task_failure, record_task_retry, record_task_latency

    metric_type = payload.get("metric", "task_failures")
    queue = payload.get("queue", "default")
    task_id = payload.get("task_id", "unknown")
    tenant_id = payload.get("tenant_id")
    correlation_id = payload.get("correlation_id")

    if metric_type == "task_retries":
        return record_task_retry(
            queue=queue,
            task_id=task_id,
            retry_count=payload.get("retry_count", 1),
            tenant_id=tenant_id,
            correlation_id=correlation_id,
        )
    elif metric_type == "task_latency":
        return record_task_latency(
            queue=queue,
            task_id=task_id,
            latency_seconds=float(payload.get("latency_seconds", 0.0)),
            tenant_id=tenant_id,
            correlation_id=correlation_id,
        )
    else:
        return record_task_failure(
            queue=queue,
            task_id=task_id,
            error=payload.get("error"),
            tenant_id=tenant_id,
            correlation_id=correlation_id,
        )


# =============================================================================
# Redis Enhancement: Review Redis Key Structure Endpoints
# =============================================================================

@router.get(
    "/redis/key-structure",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_redis_key_structure_endpoint() -> dict:
    """Return canonical Redis key namespaces review contract."""
    from app.redis_client import review_redis_key_structure
    return review_redis_key_structure()


@router.get(
    "/redis/namespaces",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_redis_namespaces_endpoint() -> dict:
    """Return registered Redis key namespaces."""
    from app.redis_client import review_redis_key_structure
    return review_redis_key_structure()


@router.get(
    "/redis/inventory",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_redis_inventory_endpoint() -> dict:
    """Return complete Redis key structure inventory including patterns, types, and TTL policies."""
    from app.redis_client import get_redis_key_inventory
    return get_redis_key_inventory()


@router.post(
    "/redis/review",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def post_redis_key_structure_review_endpoint() -> dict:
    """Trigger or confirm review of Redis key structure namespaces."""
    from app.redis_client import review_redis_key_structure
    return review_redis_key_structure()


@router.post(
    "/redis/identify-migrations",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def post_redis_identify_migrations_endpoint(payload: Optional[dict] = None) -> dict:
    """Identify legacy or un-namespaced keys that require migration to standard prefixes."""
    from app.redis_client import identify_keys_for_migration, get_redis_client
    keys = payload.get("keys") if payload else None
    redis_client = get_redis_client()
    return identify_keys_for_migration(redis_client=redis_client, keys_to_check=keys)


# =============================================================================
# Redis Enhancement: Configure Redis Expiration Rules Endpoints
# =============================================================================

@router.get(
    "/redis/expiration",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_redis_expiration_rules_endpoint() -> dict:
    """Return canonical Redis expiration rules configuration."""
    from app.redis_client import configure_redis_expiration_rules
    return configure_redis_expiration_rules()


@router.post(
    "/redis/expiration",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def configure_redis_expiration_rules_endpoint(payload: Optional[dict] = None) -> dict:
    """Configure and activate Redis expiration rules."""
    from app.redis_client import configure_redis_expiration_rules
    policy_updates = payload.get("policy_updates") if payload else None
    return configure_redis_expiration_rules(policy_updates=policy_updates)


@router.get(
    "/redis/expiration/rules",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_redis_expiration_details_endpoint() -> dict:
    """Return full Redis expiration rules metadata, effective TTLs, and protected patterns."""
    from app.redis_client import get_redis_expiration_config
    return get_redis_expiration_config()


@router.post(
    "/redis/expiration/apply",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def post_redis_apply_expiration_endpoint(payload: dict) -> dict:
    """Safely apply TTL to a Redis key, preventing expiry of durable sources of truth."""
    from app.redis_client import apply_expiration
    key = payload.get("key")
    ttl = payload.get("ttl_seconds")
    tenant_id = payload.get("tenant_id")
    correlation_id = payload.get("correlation_id")
    return apply_expiration(
        key=key,
        ttl_seconds=ttl,
        tenant_id=tenant_id,
        correlation_id=correlation_id,
    )


@router.post(
    "/redis/expiration/refresh",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def post_redis_refresh_expiration_endpoint(payload: dict) -> dict:
    """Refresh TTL on active key, rejecting durable records."""
    from app.redis_client import refresh_expiration
    key = payload.get("key")
    ttl = payload.get("ttl_seconds")
    tenant_id = payload.get("tenant_id")
    correlation_id = payload.get("correlation_id")
    return refresh_expiration(
        key=key,
        ttl_seconds=ttl,
        tenant_id=tenant_id,
        correlation_id=correlation_id,
    )


# =============================================================================
# Redis Enhancement: Improve Distributed Locks Endpoints
# =============================================================================

@router.get(
    "/redis/locks/status",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_distributed_lock_status_endpoint() -> dict:
    """Return canonical status for hardened distributed lock capability."""
    from app.redis_client import improve_distributed_locks
    return improve_distributed_locks()


@router.get(
    "/redis/locks/strategy",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_distributed_lock_strategy_endpoint() -> dict:
    """Return registered distributed lock strategy."""
    from app.redis_client import improve_distributed_locks
    return improve_distributed_locks()


@router.post(
    "/redis/locks/improve",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def post_improve_distributed_locks_endpoint() -> dict:
    """Activate or verify hardened distributed lock configuration."""
    from app.redis_client import improve_distributed_locks
    return improve_distributed_locks()


@router.get(
    "/redis/locks/config",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_distributed_lock_config_endpoint() -> dict:
    """Return full distributed lock configuration features and default TTL."""
    from app.redis_client import get_distributed_lock_config
    return get_distributed_lock_config()


@router.post(
    "/redis/locks/acquire",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def post_acquire_distributed_lock_endpoint(payload: dict) -> dict:
    """Acquire a tokenized distributed lock."""
    from app.redis_client import acquire_distributed_lock
    resource = payload.get("resource")
    ttl = payload.get("ttl_seconds", 30)
    tenant_id = payload.get("tenant_id")
    correlation_id = payload.get("correlation_id")

    acquired, token = acquire_distributed_lock(
        resource=resource,
        ttl_seconds=ttl,
        tenant_id=tenant_id,
        correlation_id=correlation_id,
    )
    return {
        "acquired": acquired,
        "resource": resource,
        "lock_token": token if acquired else None,
        "tenant_id": tenant_id,
    }


@router.post(
    "/redis/locks/release",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def post_release_distributed_lock_endpoint(payload: dict) -> dict:
    """Safely release a distributed lock if caller owns the token."""
    from app.redis_client import release_distributed_lock
    resource = payload.get("resource")
    token = payload.get("lock_token")
    tenant_id = payload.get("tenant_id")

    released = release_distributed_lock(
        resource=resource,
        lock_token=token,
        tenant_id=tenant_id,
    )
    return {
        "released": released,
        "resource": resource,
        "tenant_id": tenant_id,
    }


@router.post(
    "/redis/locks/extend",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def post_extend_distributed_lock_endpoint(payload: dict) -> dict:
    """Extend TTL of an active lock if caller owns the token."""
    from app.redis_client import extend_distributed_lock
    resource = payload.get("resource")
    token = payload.get("lock_token")
    additional_ttl = payload.get("additional_ttl_seconds", 30)
    tenant_id = payload.get("tenant_id")

    extended = extend_distributed_lock(
        resource=resource,
        lock_token=token,
        additional_ttl_seconds=additional_ttl,
        tenant_id=tenant_id,
    )
    return {
        "extended": extended,
        "resource": resource,
        "tenant_id": tenant_id,
    }


# =============================================================================
# Redis Enhancement: Add Redis Cache for Frequent Queries Endpoints
# =============================================================================

@router.get(
    "/redis/cache/status",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_query_cache_status_endpoint() -> dict:
    """Return canonical status for frequent query cache capability."""
    from app.redis_client import add_redis_cache_for_frequent_queries
    return add_redis_cache_for_frequent_queries()


@router.get(
    "/redis/cache/strategy",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_query_cache_strategy_endpoint() -> dict:
    """Return registered query cache strategy and database fallback mode."""
    from app.redis_client import add_redis_cache_for_frequent_queries
    return add_redis_cache_for_frequent_queries()


@router.post(
    "/redis/cache/enable",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def post_enable_query_cache_endpoint() -> dict:
    """Enable or verify frequent query read-through caching configuration."""
    from app.redis_client import add_redis_cache_for_frequent_queries
    return add_redis_cache_for_frequent_queries()


@router.get(
    "/redis/cache/config",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_query_cache_config_endpoint() -> dict:
    """Return query cache configuration, candidate queries, and default TTLs."""
    from app.redis_client import get_query_cache_config
    return get_query_cache_config()


@router.post(
    "/redis/cache/invalidate",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def post_invalidate_query_cache_endpoint(payload: Optional[dict] = None) -> dict:
    """Invalidate cached queries for a given query name and/or tenant."""
    from app.redis_client import invalidate_query_cache
    query_name = payload.get("query_name") if payload else None
    tenant_id = payload.get("tenant_id") if payload else None
    count = invalidate_query_cache(query_name=query_name, tenant_id=tenant_id)
    return {
        "status": "INVALIDATED",
        "invalidated_count": count,
        "query_name": query_name,
        "tenant_id": tenant_id,
    }


@router.post(
    "/redis/cache/query",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def post_execute_cached_query_endpoint(payload: dict) -> dict:
    """Execute a read-through cached query with database fallback demonstration."""
    from app.redis_client import get_or_set_cached_query
    query_name = payload.get("query_name", "test_query")
    tenant_id = payload.get("tenant_id")
    params = payload.get("params", {})
    ttl_seconds = payload.get("ttl_seconds", 300)

    # Simulated database fetcher
    def db_fetcher():
        return {
            "query": query_name,
            "tenant_id": tenant_id,
            "data": f"Database source of truth result for {query_name}",
            "params": params,
        }

    data, cached = get_or_set_cached_query(
        query_name=query_name,
        fetcher_fn=db_fetcher,
        tenant_id=tenant_id,
        ttl_seconds=ttl_seconds,
        **params,
    )
    return {
        "data": data,
        "cached": cached,
        "source": "redis_cache" if cached else "database",
        "tenant_id": tenant_id,
    }


# =============================================================================
# Redis Enhancement: Add Redis Cache Invalidation Endpoints
# =============================================================================

@router.get(
    "/redis/cache/invalidation/status",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_cache_invalidation_status_endpoint() -> dict:
    """Return canonical status for cache invalidation capability."""
    from app.redis_client import add_redis_cache_invalidation
    return add_redis_cache_invalidation()


@router.get(
    "/redis/cache/invalidation/strategy",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_cache_invalidation_strategy_endpoint() -> dict:
    """Return registered cache invalidation strategy."""
    from app.redis_client import add_redis_cache_invalidation
    return add_redis_cache_invalidation()


@router.post(
    "/redis/cache/invalidation/enable",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def post_enable_cache_invalidation_endpoint() -> dict:
    """Enable or verify write-invalidate cache invalidation strategy."""
    from app.redis_client import add_redis_cache_invalidation
    return add_redis_cache_invalidation()


@router.get(
    "/redis/cache/invalidation/config",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_cache_invalidation_config_endpoint() -> dict:
    """Return cache invalidation mappings and supported entity types."""
    from app.redis_client import get_cache_invalidation_config
    return get_cache_invalidation_config()


@router.post(
    "/redis/cache/invalidation/trigger",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def post_trigger_cache_invalidation_endpoint(payload: dict) -> dict:
    """Trigger cache invalidation for a domain entity write/update/delete."""
    from app.redis_client import invalidate_cache_on_write
    entity_type = payload.get("entity_type", "job")
    entity_id = payload.get("entity_id")
    tenant_id = payload.get("tenant_id")
    correlation_id = payload.get("correlation_id")

    return invalidate_cache_on_write(
        entity_type=entity_type,
        entity_id=entity_id,
        tenant_id=tenant_id,
        correlation_id=correlation_id,
    )


# =============================================================================
# Redis Enhancement: Add Redis Failure Handling Endpoints
# =============================================================================

@router.get(
    "/redis/failure-handling/status",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_redis_failure_handling_status_endpoint() -> dict:
    """Return canonical status for Redis failure handling capability."""
    from app.redis_client import add_redis_failure_handling
    return add_redis_failure_handling()


@router.get(
    "/redis/failure-handling/fallback",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_redis_failure_handling_fallback_endpoint() -> dict:
    """Return configured fallback mode for Redis failure handling."""
    from app.redis_client import add_redis_failure_handling
    return add_redis_failure_handling()


@router.post(
    "/redis/failure-handling/enable",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def post_enable_redis_failure_handling_endpoint() -> dict:
    """Enable or verify Redis failure handling and degraded mode."""
    from app.redis_client import add_redis_failure_handling
    return add_redis_failure_handling()


@router.get(
    "/redis/failure-handling/config",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_redis_failure_handling_config_endpoint() -> dict:
    """Return complete Redis failure handling configuration and telemetry."""
    from app.redis_client import get_redis_failure_handling_config
    return get_redis_failure_handling_config()


@router.get(
    "/redis/failure-handling/health",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_redis_health_endpoint() -> dict:
    """Check live Redis health and report degraded mode status."""
    from app.redis_client import check_redis_health
    return check_redis_health()


# =============================================================================
# Redis Enhancement: Add Redis Backup Strategy Endpoints
# =============================================================================

@router.get(
    "/redis/backup/status",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_redis_backup_status_endpoint() -> dict:
    """Return canonical status for Redis backup strategy capability."""
    from app.redis_client import add_redis_backup_strategy
    return add_redis_backup_strategy()


@router.get(
    "/redis/backup/strategy",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_redis_backup_strategy_endpoint() -> dict:
    """Return configured backup strategy and source of truth."""
    from app.redis_client import add_redis_backup_strategy
    return add_redis_backup_strategy()


@router.post(
    "/redis/backup/configure",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def post_configure_redis_backup_endpoint() -> dict:
    """Ensure or verify Redis backup and persistence strategy."""
    from app.redis_client import add_redis_backup_strategy
    return add_redis_backup_strategy()


@router.get(
    "/redis/backup/config",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_redis_backup_config_endpoint() -> dict:
    """Return comprehensive Redis backup, persistence modes, and retention config."""
    from app.redis_client import get_redis_backup_config
    return get_redis_backup_config()


@router.get(
    "/redis/backup/classification",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_redis_backup_classification_endpoint(key: Optional[str] = None) -> dict:
    """Classify a Redis key as durable-needed vs reconstructable, or return full classification map."""
    from app.redis_client import classify_redis_key, get_redis_backup_config
    if key:
        return classify_redis_key(key)
    return get_redis_backup_config().get("classification", {})


@router.post(
    "/redis/backup/validate-restore",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def post_validate_redis_restore_endpoint(payload: dict) -> dict:
    """Verify that a restored Redis record does not overwrite newer PostgreSQL state."""
    from app.redis_client import validate_redis_restore_safety
    redis_rec = payload.get("redis_record", {})
    postgres_rec = payload.get("postgres_record")
    return validate_redis_restore_safety(redis_rec, postgres_rec)


# =============================================================================
# Redis Enhancement: Monitor Redis Memory Usage Endpoints
# =============================================================================

@router.get(
    "/redis/memory/status",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_redis_memory_status_endpoint() -> dict:
    """Return canonical status and monitored metric list for Redis memory usage."""
    from app.redis_client import monitor_redis_memory_usage
    return monitor_redis_memory_usage()


@router.get(
    "/redis/memory/metrics",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_redis_memory_metrics_endpoint() -> dict:
    """Collect and return live Redis memory metrics including used_memory, memory_limit, and evictions."""
    from app.redis_client import get_redis_memory_metrics
    return get_redis_memory_metrics()


@router.post(
    "/redis/memory/monitor",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def post_redis_memory_monitor_endpoint() -> dict:
    """Trigger or verify Redis memory monitoring contract."""
    from app.redis_client import monitor_redis_memory_usage
    return monitor_redis_memory_usage()


@router.get(
    "/redis/memory/pressure",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_redis_memory_pressure_endpoint() -> dict:
    """Evaluate Redis memory pressure and active alerts without exposing tenant-sensitive labels."""
    from app.redis_client import get_redis_memory_metrics
    return get_redis_memory_metrics()


@router.get(
    "/redis/memory/config",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_redis_memory_config_endpoint() -> dict:
    """Return operational configuration for Redis memory monitoring and alert thresholds."""
    from app.redis_client import get_redis_memory_monitoring_config
    return get_redis_memory_monitoring_config()


# =============================================================================
# Redis Enhancement: Monitor Redis Connection Usage Endpoints
# =============================================================================

@router.get(
    "/redis/connections/status",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_redis_connection_status_endpoint() -> dict:
    """Return canonical status and monitored metric list for Redis connection usage."""
    from app.redis_client import monitor_redis_connection_usage
    return monitor_redis_connection_usage()


@router.get(
    "/redis/connections/metrics",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_redis_connection_metrics_endpoint() -> dict:
    """Collect and return live Redis connection metrics including pool pressure and errors."""
    from app.redis_client import get_redis_connection_metrics
    return get_redis_connection_metrics()


@router.post(
    "/redis/connections/monitor",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def post_redis_connection_monitor_endpoint() -> dict:
    """Trigger or verify Redis connection monitoring contract."""
    from app.redis_client import monitor_redis_connection_usage
    return monitor_redis_connection_usage()


@router.get(
    "/redis/connections/pressure",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_redis_connection_pressure_endpoint() -> dict:
    """Evaluate Redis connection pool pressure and active failure alerts."""
    from app.redis_client import get_redis_connection_metrics
    return get_redis_connection_metrics()


@router.get(
    "/redis/connections/config",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_redis_connection_config_endpoint() -> dict:
    """Return operational configuration for Redis connection monitoring and pool limits."""
    from app.redis_client import get_redis_connection_monitoring_config
    return get_redis_connection_monitoring_config()


# =============================================================================
# Redis Enhancement: Test Redis Recovery Endpoints
# =============================================================================

@router.get(
    "/redis/recovery/status",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_redis_recovery_status_endpoint() -> dict:
    """Return canonical status and covered scenarios for Redis recovery testing."""
    from app.redis_client import test_redis_recovery
    return test_redis_recovery()


@router.get(
    "/redis/recovery/scenarios",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_redis_recovery_scenarios_endpoint() -> dict:
    """Return list of tested Redis recovery scenarios."""
    from app.redis_client import test_redis_recovery
    return test_redis_recovery()


@router.post(
    "/redis/recovery/run",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def post_run_redis_recovery_simulation_endpoint() -> dict:
    """Execute end-to-end non-destructive simulation of Redis recovery scenarios."""
    from app.redis_client import run_redis_recovery_simulation
    return run_redis_recovery_simulation()


@router.get(
    "/redis/recovery/config",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_redis_recovery_config_endpoint() -> dict:
    """Return operational configuration and safety parameters for Redis recovery tests."""
    from app.redis_client import get_redis_recovery_config
    return get_redis_recovery_config()


# =============================================================================
# WebSocket Real-Time Events: Connect Kafka Events to WebSocket Endpoints
# =============================================================================

@router.get(
    "/websocket/kafka-bridge/status",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_websocket_kafka_bridge_status_endpoint() -> dict:
    """Return canonical status for Connect Kafka events to WebSocket."""
    from app.services.kafka_to_websocket_bridge import connect_kafka_events_to_websocket
    return connect_kafka_events_to_websocket()


@router.get(
    "/websocket/kafka-bridge/config",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_websocket_kafka_bridge_config_endpoint() -> dict:
    """Return operational configuration for Kafka-to-WebSocket bridge."""
    from app.services.kafka_to_websocket_bridge import get_kafka_to_websocket_config
    return get_kafka_to_websocket_config()


@router.post(
    "/websocket/kafka-bridge/connect",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def post_connect_websocket_kafka_bridge_endpoint() -> dict:
    """Connect or verify Kafka events to WebSocket bridge connection."""
    from app.services.kafka_to_websocket_bridge import connect_kafka_events_to_websocket
    return connect_kafka_events_to_websocket()


@router.post(
    "/websocket/kafka-bridge/publish",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def post_bridge_kafka_event_endpoint(
    payload: dict,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> dict:
    """Bridge an incoming Kafka event payload into the WebSocket server with tenant isolation and deduplication."""
    from app.services.kafka_to_websocket_bridge import bridge_kafka_event_to_websocket
    resolved_tenant = _resolve_authorized_ws_tenant(payload, current_user)
    payload = dict(payload)
    payload["tenant_id"] = resolved_tenant
    return bridge_kafka_event_to_websocket(payload)


# =============================================================================
# WebSocket Real-Time Events: Send Job Status Updates Endpoints
# =============================================================================

@router.get(
    "/websocket/job-status/status",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_job_status_update_status_endpoint() -> dict:
    """Return canonical output contract for Send job status updates."""
    from app.services.job_status_broadcaster import send_job_status_updates
    return send_job_status_updates()


@router.get(
    "/websocket/job-status/config",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_job_status_update_config_endpoint() -> dict:
    """Return operational configuration for Job Status Updates service."""
    from app.services.job_status_broadcaster import get_job_status_update_config
    return get_job_status_update_config()


@router.post(
    "/websocket/job-status/send",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def post_send_job_status_update_endpoint(
    payload: dict,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> dict:
    """Broadcast an authoritative job status update to the authenticated tenant room."""
    from app.services.job_status_broadcaster import send_job_status_updates

    job_id = payload.get("job_id", "123")
    status = payload.get("status", "ASSIGNED")
    tenant_id = _resolve_authorized_ws_tenant(payload, current_user)

    return send_job_status_updates(
        job_id=job_id,
        status=status,
        tenant_id=tenant_id,
        old_status=payload.get("old_status"),
        correlation_id=payload.get("correlation_id"),
        event_id=payload.get("event_id"),
        technician_id=payload.get("technician_id"),
        technician_name=payload.get("technician_name"),
        timestamp=payload.get("timestamp"),
    )


# =============================================================================
# WebSocket Real-Time Events: Send Technician Location Updates Endpoints
# =============================================================================

@router.get(
    "/websocket/technician-location/status",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_technician_location_status_endpoint() -> dict:
    """Return canonical output contract for Send technician location updates."""
    from app.services.technician_location_broadcaster import send_technician_location_updates
    return send_technician_location_updates()


@router.get(
    "/websocket/technician-location/config",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_technician_location_config_endpoint() -> dict:
    """Return operational configuration for Technician Location Updates broadcast service."""
    from app.services.technician_location_broadcaster import get_technician_location_config
    return get_technician_location_config()


@router.post(
    "/websocket/technician-location/send",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def post_send_technician_location_update_endpoint(
    payload: dict,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> dict:
    """Broadcast an authoritative technician GPS location update to the authenticated tenant room."""
    from app.services.technician_location_broadcaster import send_technician_location_updates

    technician_id = payload.get("technician_id", "tech-1")
    latitude = payload.get("latitude", 0.0)
    longitude = payload.get("longitude", 0.0)
    tenant_id = _resolve_authorized_ws_tenant(payload, current_user)

    return send_technician_location_updates(
        technician_id=technician_id,
        latitude=latitude,
        longitude=longitude,
        tenant_id=tenant_id,
        job_id=payload.get("job_id"),
        accuracy=payload.get("accuracy"),
        altitude=payload.get("altitude"),
        speed=payload.get("speed"),
        heading=payload.get("heading"),
        timestamp=payload.get("timestamp"),
        correlation_id=payload.get("correlation_id"),
        event_id=payload.get("event_id"),
        force_broadcast=payload.get("force_broadcast", False),
    )


# =============================================================================
# WebSocket Real-Time Events: Send Dispatch Updates Endpoints
# =============================================================================

@router.get(
    "/websocket/dispatch/status",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_dispatch_update_status_endpoint() -> dict:
    """Return canonical output contract for Send dispatch updates."""
    from app.services.dispatch_update_broadcaster import send_dispatch_updates
    return send_dispatch_updates()


@router.get(
    "/websocket/dispatch/config",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_dispatch_update_config_endpoint() -> dict:
    """Return operational configuration for Dispatch Updates broadcast service."""
    from app.services.dispatch_update_broadcaster import get_dispatch_update_config
    return get_dispatch_update_config()


@router.post(
    "/websocket/dispatch/send",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def post_send_dispatch_update_endpoint(
    payload: dict,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> dict:
    """Broadcast an authoritative dispatch update to the authenticated tenant room."""
    from app.services.dispatch_update_broadcaster import send_dispatch_updates

    job_id = payload.get("job_id", "123")
    assignment_id = payload.get("assignment_id", "456")
    tenant_id = _resolve_authorized_ws_tenant(payload, current_user)

    return send_dispatch_updates(
        job_id=job_id,
        assignment_id=assignment_id,
        tenant_id=tenant_id,
        technician_id=payload.get("technician_id"),
        technician_name=payload.get("technician_name"),
        previous_technician_id=payload.get("previous_technician_id"),
        dispatch_action=payload.get("dispatch_action", "ASSIGNED"),
        status=payload.get("status", "ASSIGNED"),
        correlation_id=payload.get("correlation_id"),
        event_id=payload.get("event_id"),
        timestamp=payload.get("timestamp"),
    )


# ---------------------------------------------------------------------------
# WebSocket Real-Time Events: Send SLA Alerts Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/websocket/sla-alerts/status",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_sla_alerts_status_endpoint() -> dict:
    """Return operational status for SLA alert WebSocket real-time delivery."""
    from app.services.sla_alert_broadcaster import get_sla_alert_config
    return {
        "status": "READY",
        **get_sla_alert_config(),
    }


@router.get(
    "/websocket/sla-alerts/config",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_sla_alerts_config_endpoint() -> dict:
    """Return configuration contract for SLA alert broadcasts."""
    from app.services.sla_alert_broadcaster import get_sla_alert_config
    return get_sla_alert_config()


@router.post(
    "/websocket/sla-alerts/send",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def post_send_sla_alert_endpoint(
    payload: dict,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> dict:
    """Broadcast an authoritative SLA alert to the authenticated tenant room."""
    from app.services.sla_alert_broadcaster import send_sla_alerts

    job_id = payload.get("job_id", "123")
    severity = payload.get("severity", "AT_RISK")
    tenant_id = _resolve_authorized_ws_tenant(payload, current_user)

    return send_sla_alerts(
        job_id=job_id,
        severity=severity,
        tenant_id=tenant_id,
        sla_type=payload.get("sla_type", "RESPONSE"),
        time_remaining_seconds=payload.get("time_remaining_seconds"),
        target_time=payload.get("target_time"),
        deadline=payload.get("deadline"),
        customer_tier=payload.get("customer_tier", "STANDARD"),
        correlation_id=payload.get("correlation_id"),
        event_id=payload.get("event_id"),
        timestamp=payload.get("timestamp"),
    )


# ---------------------------------------------------------------------------
# WebSocket Real-Time Events: Send Notification Updates Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/websocket/notifications/status",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_notification_updates_status_endpoint() -> dict:
    """Return operational status for notification updates WebSocket real-time delivery."""
    from app.services.notification_update_broadcaster import get_notification_update_config
    return {
        "status": "READY",
        **get_notification_update_config(),
    }


@router.get(
    "/websocket/notifications/config",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_notification_updates_config_endpoint() -> dict:
    """Return configuration contract for notification updates broadcasts."""
    from app.services.notification_update_broadcaster import get_notification_update_config
    return get_notification_update_config()


@router.post(
    "/websocket/notifications/send",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def post_send_notification_update_endpoint(
    payload: dict,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> dict:
    """Broadcast an authoritative notification lifecycle update to the authenticated tenant room."""
    from app.services.notification_update_broadcaster import send_notification_updates

    notification_id = payload.get("notification_id", "n-1")
    status = payload.get("status", "SENT")
    tenant_id = _resolve_authorized_ws_tenant(payload, current_user)

    return send_notification_updates(
        notification_id=notification_id,
        status=status,
        tenant_id=tenant_id,
        channel=payload.get("channel", "EMAIL"),
        recipient=payload.get("recipient"),
        recipient_id=payload.get("recipient_id"),
        attempt=payload.get("attempt", 1),
        job_id=payload.get("job_id"),
        error_reason=payload.get("error_reason"),
        provider_id=payload.get("provider_id"),
        correlation_id=payload.get("correlation_id"),
        event_id=payload.get("event_id"),
        timestamp=payload.get("timestamp"),
    )


# ---------------------------------------------------------------------------
# WebSocket Real-Time Events: Send Payment Updates Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/websocket/payments/status",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_payment_updates_status_endpoint() -> dict:
    """Return operational status for payment updates WebSocket real-time delivery."""
    from app.services.payment_update_broadcaster import get_payment_update_config
    return {
        "status": "READY",
        **get_payment_update_config(),
    }


@router.get(
    "/websocket/payments/config",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_payment_updates_config_endpoint() -> dict:
    """Return configuration contract for payment updates broadcasts."""
    from app.services.payment_update_broadcaster import get_payment_update_config
    return get_payment_update_config()


@router.post(
    "/websocket/payments/send",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def post_send_payment_update_endpoint(
    payload: dict,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> dict:
    """Broadcast an authoritative payment/invoice update to the authenticated tenant room."""
    from app.services.payment_update_broadcaster import send_payment_updates

    invoice_id = payload.get("invoice_id") or payload.get("payment_id", "10001")
    payment_status = payload.get("payment_status") or payload.get("status", "PAID")
    tenant_id = _resolve_authorized_ws_tenant(payload, current_user)

    return send_payment_updates(
        invoice_id=invoice_id,
        payment_status=payment_status,
        tenant_id=tenant_id,
        status=payload.get("status"),
        payment_id=payload.get("payment_id"),
        job_id=payload.get("job_id"),
        customer_id=payload.get("customer_id"),
        amount=payload.get("amount"),
        currency=payload.get("currency", "USD"),
        payment_method=payload.get("payment_method"),
        error_reason=payload.get("error_reason"),
        correlation_id=payload.get("correlation_id"),
        event_id=payload.get("event_id"),
        timestamp=payload.get("timestamp"),
    )


# ---------------------------------------------------------------------------
# WebSocket Real-Time Events: Handle WebSocket Reconnection Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/websocket/reconnect/status",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_websocket_reconnect_status_endpoint() -> dict:
    """Return operational status and recovery window for WebSocket client reconnection."""
    from app.services.websocket_reconnection_service import handle_websocket_reconnection
    return handle_websocket_reconnection()


@router.get(
    "/websocket/reconnect/config",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_websocket_reconnect_config_endpoint() -> dict:
    """Return configuration contract for WebSocket connection-state recovery."""
    from app.services.websocket_reconnection_service import get_reconnection_config
    return get_reconnection_config()


@router.post(
    "/websocket/reconnect/sync",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def post_websocket_reconnect_sync_endpoint(
    payload: dict,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> dict:
    """Evaluate client reconnection and state recovery status."""
    from app.services.websocket_reconnection_service import handle_websocket_reconnection

    tenant_id = payload.get("tenant_id") or getattr(current_user, "tenant_id", "tenant-1")

    return handle_websocket_reconnection(
        tenant_id=tenant_id,
        socket_id=payload.get("socket_id"),
        last_event_id=payload.get("last_event_id"),
        recovered=payload.get("recovered", True),
        disconnected_at=payload.get("disconnected_at"),
        status=payload.get("status"),
        namespace=payload.get("namespace"),
        recovery_window_seconds=payload.get("recovery_window_seconds"),
    )


# ---------------------------------------------------------------------------
# WebSocket Real-Time Events: Handle WebSocket Failures Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/websocket/failures/status",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_websocket_failures_status_endpoint() -> dict:
    """Return operational status for WebSocket failure handling and resilience."""
    from app.services.websocket_failure_service import handle_websocket_failures
    return handle_websocket_failures()


@router.get(
    "/websocket/failures/config",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_websocket_failures_config_endpoint() -> dict:
    """Return configuration contract for WebSocket failure handling."""
    from app.services.websocket_failure_service import get_websocket_failure_config
    return get_websocket_failure_config()


@router.post(
    "/websocket/failures/report",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def post_websocket_failures_report_endpoint(
    payload: dict,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> dict:
    """Report or simulate an isolated WebSocket failure condition without crashing."""
    from app.services.websocket_failure_service import handle_websocket_failures

    tenant_id = payload.get("tenant_id") or getattr(current_user, "tenant_id", "tenant-1")

    return handle_websocket_failures(
        status=payload.get("status", "DEGRADED_OR_RECOVERED"),
        failure_handling=payload.get("failure_handling", "ENABLED"),
        error_type=payload.get("error_type"),
        raw_payload=payload.get("raw_payload"),
        tenant_id=tenant_id,
    )


# ---------------------------------------------------------------------------
# WebSocket Real-Time Events: Add Connection Authentication Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/websocket/auth/status",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_websocket_auth_status_endpoint() -> dict:
    """Return operational status for WebSocket connection authentication."""
    from app.services.websocket_auth_service import get_websocket_auth_status
    return get_websocket_auth_status()


@router.get(
    "/websocket/auth/config",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_websocket_auth_config_endpoint() -> dict:
    """Return configuration contract for WebSocket connection authentication."""
    from app.services.websocket_auth_service import get_websocket_auth_config
    return get_websocket_auth_config()


@router.post(
    "/websocket/auth/verify",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def post_websocket_auth_verify_endpoint(
    payload: dict,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> dict:
    """Validate WebSocket connection credentials and verified claims."""
    from app.services.websocket_auth_service import validate_connection_authentication

    token = payload.get("token")
    tenant_id = payload.get("tenant_id") or getattr(current_user, "tenant_id", "tenant-1")
    role = payload.get("role") or getattr(current_user, "role", "dispatcher")
    allow_fallback = payload.get("allow_fallback", True)

    return validate_connection_authentication(
        token=token,
        tenant_id=tenant_id,
        role=role,
        allow_fallback=allow_fallback,
    )


# ---------------------------------------------------------------------------
# WebSocket Real-Time Events: Add Tenant-Based Event Filtering Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/websocket/tenant-filtering/status",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_websocket_tenant_filtering_status_endpoint() -> dict:
    """Return canonical status for tenant-based WebSocket event filtering."""
    from app.services.websocket_tenant_filter import get_tenant_filter_status
    return get_tenant_filter_status()


@router.get(
    "/websocket/tenant-filtering/config",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def get_websocket_tenant_filtering_config_endpoint() -> dict:
    """Return operational configuration for tenant-based WebSocket event filtering."""
    from app.services.websocket_tenant_filter import get_tenant_filter_config
    return get_tenant_filter_config()


@router.post(
    "/websocket/tenant-filtering/validate",
    dependencies=[
        Depends(
            require_permission(
                Permission.DISPATCH_QUEUE_VIEW
            )
        )
    ],
)
def post_websocket_tenant_filtering_validate_endpoint(
    payload: dict,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> dict:
    """Validate an outbound WebSocket event against tenant room and authorization boundaries."""
    from app.services.websocket_tenant_filter import filter_tenant_events

    event_payload = payload.get("event_payload") if isinstance(payload, dict) and "event_payload" in payload else payload
    socket_tenant = payload.get("socket_tenant_id") if isinstance(payload, dict) else None
    if socket_tenant is None:
        socket_tenant = getattr(current_user, "tenant_id", None)
    target_room = payload.get("target_room") if isinstance(payload, dict) else None
    allow_cross_tenant = bool(payload.get("allow_cross_tenant_scope", False)) if isinstance(payload, dict) else False

    return filter_tenant_events(
        event_payload=event_payload,
        socket_tenant_id=socket_tenant,
        target_room=target_room,
        allow_cross_tenant_scope=allow_cross_tenant,
    )




















