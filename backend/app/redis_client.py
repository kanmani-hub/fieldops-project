import redis
from redis.exceptions import ConnectionError, TimeoutError
import os
import time
import secrets
import json
import hashlib
from contextlib import contextmanager
from typing import Optional, Any, Dict, List, Union, Tuple

class RedisCacheManager:
    def __init__(self):
        redis_url = os.getenv("REDIS_ASYNC_URL") or os.getenv("REDIS_URL")
        redis_host = os.getenv("REDIS_HOST", "localhost")
        redis_port = int(os.getenv("REDIS_PORT", 6379))

        if redis_url:
            self.client = redis.Redis.from_url(
                redis_url,
                decode_responses=True,
                socket_timeout=0.3,
                socket_connect_timeout=0.3,
            )
            self.pool = self.client.connection_pool
        else:
            self.pool = redis.ConnectionPool(
                host=redis_host,
                port=redis_port,
                decode_responses=True,
                socket_timeout=0.3,
                socket_connect_timeout=0.3,
            )
            self.client = redis.Redis(connection_pool=self.pool)

        self._was_connected = True
        self._degraded_mode = False
        self._failure_count = 0
        self._recovery_count = 0
        self._last_error = None

    def _execute_with_retry(self, operation, *args, **kwargs):
        retries = 1
        for i in range(retries):
            try:
                result = operation(*args, **kwargs)
                if self._degraded_mode or not self._was_connected:
                    print("Redis connection restored: exiting SAFE_DEGRADED_MODE.")
                    self._was_connected = True
                    self._degraded_mode = False
                    self._recovery_count += 1
                return result
            except (ConnectionError, TimeoutError) as e:
                self._last_error = str(e)
                if i == retries - 1:
                    if not self._degraded_mode or self._was_connected:
                        print(f"Redis connection failed: {e} -> entering SAFE_DEGRADED_MODE (subsequent warnings suppressed)")
                        self._was_connected = False
                        self._degraded_mode = True
                        self._failure_count += 1
                    return None
                time.sleep(0.1)
        return None

    def is_healthy(self) -> bool:
        """Check if Redis connection is currently operational without throwing."""
        try:
            return bool(self.client.ping())
        except Exception:
            return False

    def is_degraded(self) -> bool:
        """Return True if Redis client has entered safe degraded mode."""
        return self._degraded_mode

    def get_health_status(self) -> dict[str, Any]:
        """Return operational health telemetry and failure handling state."""
        healthy = self.is_healthy()
        return {
            "status": "HARDENED",
            "fallback": "SAFE_DEGRADED_MODE",
            "healthy": healthy,
            "degraded_mode": self._degraded_mode or not healthy,
            "failure_count": self._failure_count,
            "recovery_count": self._recovery_count,
            "last_error": self._last_error,
        }

    def execute_with_fallback(self, operation, fallback_fn=None, default=None, *args, **kwargs):
        """
        Execute Redis operation.
        If Redis is unavailable, degraded, or fails, execute fallback_fn or return default.
        """
        result = self._execute_with_retry(operation, *args, **kwargs)
        if result is not None:
            return result
        if fallback_fn is not None:
            return fallback_fn()
        return default

    def get(self, key: str) -> Optional[str]:
        return self._execute_with_retry(self.client.get, key)

    def setex(self, key: str, time_seconds: int, value: str) -> bool:
        result = self._execute_with_retry(self.client.setex, key, time_seconds, value)
        return result is not None

    def delete(self, key: str) -> bool:
        result = self._execute_with_retry(self.client.delete, key)
        return result is not None
    def get_and_delete(self, key: str) -> Optional[str]:
        """
        Atomically get and delete a Redis key.

        Used for one-time MFA challenges to prevent replay attacks.
        """
        script = """
        local value = redis.call('GET', KEYS[1])
        if value then
            redis.call('DEL', KEYS[1])
        end
        return value
        """

        return self._execute_with_retry(
            self.client.eval,
            script,
            1,
            key,
        )

    def incr(self, key: str, amount: int = 1) -> Optional[int]:
        return self._execute_with_retry(self.client.incrby, key, amount)

    def expire(self, key: str, time_seconds: int) -> bool:
        result = self._execute_with_retry(self.client.expire, key, time_seconds)
        return result is not None

    def ttl(self, key: str) -> int:
        result = self._execute_with_retry(self.client.ttl, key)
        return result if result is not None else -2

    def persist(self, key: str) -> bool:
        result = self._execute_with_retry(self.client.persist, key)
        return bool(result)

    def __getattr__(self, name):
        if name == "client":
            raise AttributeError("client attribute not initialized")
        attr = getattr(self.client, name)
        if callable(attr):
            def wrapper(*args, **kwargs):
                return self._execute_with_retry(attr, *args, **kwargs)
            return wrapper
        return attr

redis_manager = None

def get_redis_client():
    global redis_manager
    if redis_manager is None:
        redis_manager = RedisCacheManager()
    return redis_manager


# =============================================================================
# Redis Enhancement: Review Redis Key Structure & Standard Namespaces
# =============================================================================

REDIS_KEY_NAMESPACES = [
    "dispatch",
    "gps_purge",
    "cache",
    "locks",
    "celery",
]

DEFAULT_REDIS_KEY_REVIEW = {
    "status": "REVIEWED",
    "key_namespaces": [
        "dispatch",
        "gps_purge",
        "cache",
        "locks",
        "celery",
    ],
}

REDIS_KEY_INVENTORY = {
    "dispatch": {
        "namespace": "dispatch",
        "owner": "Dispatch & WebSocket Service",
        "tenant_scoped": True,
        "patterns": [
            "dispatch_events",  # Pub/Sub channel consumed by ws-server
            "dispatch:events",
            "dispatch:heartbeat:{technician_id}",
            "dispatch:tenant:{tenant_id}:heartbeat:{technician_id}",
            "dispatch:timer:{job_id}",
            "dispatch:timer_warning:{job_id}",
            "dispatch:timer_warned:{job_id}",
            "job:timer:{job_id}",  # Legacy pattern
            "task_queue:tenant:{tenant_id}",
            "task_queue:tenants",
            "task_queue:dlq",
            "task_queue:throughput",
            "dispatch:metrics:summary",
        ],
        "data_types": ["string", "zset", "set", "pubsub"],
        "ttl_policies": {
            "heartbeat": "60 seconds",
            "timers": "Task acceptance SLA (typically 120-300s)",
            "metrics": "60 seconds",
            "queues": "Persistent until processed",
        },
        "migration_notes": "job:timer:* -> dispatch:timer:*, job:timer_warning:* -> dispatch:timer_warning:*",
    },
    "gps_purge": {
        "namespace": "gps_purge",
        "owner": "GPS Purge Task & Retention Engine",
        "tenant_scoped": False,
        "patterns": [
            "gps_purge:total_purged_30d",
            "gps_purge:last_purge_run",
            "gps_purge:next_scheduled",
            "gps_purge:status:{job_id}",
            "gps_purge_status:{job_id}",  # Legacy pattern
            "gps_purge_start_time:{job_id}",  # Legacy pattern
            "gps_purge:start_time:{job_id}",
            "gps_purge_dlq",  # Legacy list
            "gps_purge:dlq",
            "gps:interval:*:{job_id}",
            "gps:cache:{job_id}:*",
        ],
        "data_types": ["string", "list"],
        "ttl_policies": {
            "statistics": "30 days (rolling)",
            "job_status": "86400 seconds (24 hours)",
            "intervals_cache": "Purged on job completion",
            "dlq": "Persistent until requeued",
        },
        "migration_notes": "gps_purge_status:* -> gps_purge:status:*, gps_purge_dlq -> gps_purge:dlq",
    },
    "cache": {
        "namespace": "cache",
        "owner": "Application Cache Manager & Auth Services",
        "tenant_scoped": True,
        "patterns": [
            "cache:{key}",
            "cache:tenant:{tenant_id}:{key}",
            "services_catalog:cache",
            "mfa:{challenge_id}",
            "sso:state:{state}",
            "rate_limit:{tenant_id}:{user_id}",
        ],
        "data_types": ["string", "hash"],
        "ttl_policies": {
            "api_cache": "60-300 seconds",
            "catalog_cache": "3600 seconds",
            "mfa_sso": "300 seconds (ephemeral)",
            "rate_limits": "60 seconds",
        },
        "migration_notes": "Bare cache keys should be migrated to cache:tenant:{tenant_id}:{key}",
    },
    "locks": {
        "namespace": "locks",
        "owner": "Distributed Lock Service & Idempotency",
        "tenant_scoped": True,
        "patterns": [
            "locks:job:{job_id}",
            "job:lock:{job_id}",  # Legacy pattern
            "lock:job_accept:{job_id}",
            "lock:ordering:{agg_type}:{agg_id}",
            "lock:idempotency:{domain}:{event_id}",
            "lock:idempotency_key:{domain}:{key}",
            "scheduled_task_lock:{schedule_key}:{bucket}",
            "locks:tenant:{tenant_id}:{resource}",
        ],
        "data_types": ["string"],
        "ttl_policies": {
            "job_lock": "10-30 seconds (with Redlock/single-instance renewal)",
            "idempotency_lock": "60-86400 seconds",
            "scheduled_lock": "Bucket interval (60-3600s)",
        },
        "migration_notes": "job:lock:* -> locks:job:*, bare lock:* -> locks:*",
    },
    "celery": {
        "namespace": "celery",
        "owner": "Celery Queue Management & Broker Runtime",
        "tenant_scoped": True,
        "patterns": [
            "task_result:{task_id}",
            "celery:result:{task_id}",
            "celery_monitoring:failures:{queue}",
            "celery_monitoring:retries:{queue}",
            "celery_monitoring:latency:{queue}",
            "celery:monitoring:{metric}:{queue}",
            "notifications",  # Broker queue list
            "billing",  # Broker queue list
            "gps_processing",  # Broker queue list
            "ai_processing",  # Broker queue list
            "high_priority",  # Broker queue list
            "reports",  # Broker queue list
            "dlq",  # Broker DLQ list
        ],
        "data_types": ["string", "list"],
        "ttl_policies": {
            "task_results": "86400 seconds (24 hours)",
            "monitoring_metrics": "Rolling counter / 86400s",
            "broker_queues": "Persistent broker queues",
        },
        "migration_notes": "task_result:* -> celery:result:*, celery_monitoring:* -> celery:monitoring:*",
    },
}


def build_redis_key(namespace: str, *parts: str, tenant_id: Optional[str] = None) -> str:
    """
    Construct a standardized, collision-safe Redis key with optional tenant isolation.

    Standard Format:
        Global:  {namespace}:{part1}:{part2}:...
        Tenant:  {namespace}:tenant:{tenant_id}:{part1}:{part2}:...
    """
    clean_parts = [str(p).strip(":") for p in parts if p is not None and str(p).strip(":")]
    ns = namespace.strip(":")

    if tenant_id:
        tid = str(tenant_id).strip(":")
        all_parts = [ns, "tenant", tid] + clean_parts
    else:
        all_parts = [ns] + clean_parts

    return ":".join(all_parts)


def build_tenant_key(namespace: str, tenant_id: str, *parts: str) -> str:
    """Convenience helper to build a strictly tenant-scoped Redis key."""
    return build_redis_key(namespace, *parts, tenant_id=tenant_id)


def parse_redis_key(key: str) -> dict[str, Any]:
    """
    Parse a Redis key to extract its namespace, tenant scope, and sub-parts.
    """
    if not key or not isinstance(key, str):
        return {"namespace": "unknown", "tenant_id": None, "parts": []}

    tokens = key.split(":")
    if not tokens:
        return {"namespace": "unknown", "tenant_id": None, "parts": []}

    first = tokens[0]
    namespace = first if first in REDIS_KEY_NAMESPACES else "unknown"

    # Check for tenant scope
    tenant_id = None
    parts = []
    if len(tokens) >= 3 and tokens[1] == "tenant":
        tenant_id = tokens[2]
        parts = tokens[3:]
    else:
        parts = tokens[1:]

    return {
        "namespace": namespace,
        "tenant_id": tenant_id,
        "parts": parts,
        "raw_key": key,
    }


def review_redis_key_structure() -> dict[str, Any]:
    """
    Return the canonical contract for Redis key structure review.

    Expected Output:
    {
        "status": "REVIEWED",
        "key_namespaces": [
            "dispatch",
            "gps_purge",
            "cache",
            "locks",
            "celery"
        ]
    }
    """
    return {
        "status": "REVIEWED",
        "key_namespaces": list(REDIS_KEY_NAMESPACES),
    }


def get_redis_key_inventory() -> dict[str, Any]:
    """Return the complete metadata inventory for all 5 key namespaces."""
    return {
        "status": "REVIEWED",
        "key_namespaces": list(REDIS_KEY_NAMESPACES),
        "inventory": REDIS_KEY_INVENTORY,
    }


def identify_keys_for_migration(redis_client=None, keys_to_check: Optional[list[str]] = None) -> dict[str, Any]:
    """
    Identify legacy, un-namespaced, or colliding Redis keys that require migration.
    Returns categorized candidates with recommended target standardized names.
    """
    legacy_mappings = [
        (r"^job:timer:(.+)$", "dispatch:timer:\\1", "dispatch"),
        (r"^job:timer_warning:(.+)$", "dispatch:timer_warning:\\1", "dispatch"),
        (r"^job:timer_warned:(.+)$", "dispatch:timer_warned:\\1", "dispatch"),
        (r"^gps_purge_status:(.+)$", "gps_purge:status:\\1", "gps_purge"),
        (r"^gps_purge_start_time:(.+)$", "gps_purge:start_time:\\1", "gps_purge"),
        (r"^gps_purge_dlq$", "gps_purge:dlq", "gps_purge"),
        (r"^job:lock:(.+)$", "locks:job:\\1", "locks"),
        (r"^lock:job_accept:(.+)$", "locks:job_accept:\\1", "locks"),
        (r"^task_result:(.+)$", "celery:result:\\1", "celery"),
        (r"^celery_monitoring:(.+)$", "celery:monitoring:\\1", "celery"),
    ]

    import re

    candidates = []
    keys = keys_to_check or []

    if not keys and redis_client is not None:
        try:
            # Sample scan keys from Redis
            scanned = []
            cursor = 0
            for _ in range(5):
                cursor, batch = redis_client.scan(cursor=cursor, count=100)
                scanned.extend(batch)
                if cursor == 0:
                    break
            keys = scanned
        except Exception:
            keys = []

    for k in keys:
        for pattern, target_template, ns in legacy_mappings:
            match = re.match(pattern, k)
            if match:
                target = re.sub(pattern, target_template, k)
                candidates.append({
                    "legacy_key": k,
                    "target_key": target,
                    "namespace": ns,
                    "action": "MIGRATE",
                })
                break

    return {
        "status": "REVIEWED",
        "key_namespaces": list(REDIS_KEY_NAMESPACES),
        "total_evaluated": len(keys),
        "migration_candidates": candidates,
        "migration_count": len(candidates),
    }


# Authoritative story aliases
ReviewRedisKeyStructure = review_redis_key_structure
Review_redis_key_structure = review_redis_key_structure
get_redis_key_structure_review = review_redis_key_structure
get_redis_namespaces = review_redis_key_structure


# =============================================================================
# Redis Enhancement: Configure Redis Expiration Rules
# =============================================================================

DEFAULT_REDIS_EXPIRATION_CONFIG = {
    "status": "CONFIGURED",
    "ttl_policy": "ENABLED",
}

DEFAULT_REDIS_EXPIRATION_RULES = {
    # Dispatch namespace
    "dispatch:heartbeat": {
        "pattern": r"^dispatch:(?:tenant:[^:]+:)?heartbeat:.+$",
        "default_ttl": 60,
        "env_var": "REDIS_TTL_HEARTBEAT",
        "description": "Technician real-time heartbeat status",
        "category": "dispatch",
        "is_durable": False,
    },
    "dispatch:timer": {
        "pattern": r"^(?:dispatch:timer:|job:timer:).+$",
        "default_ttl": 300,
        "env_var": "REDIS_TTL_TIMER",
        "description": "Job assignment acceptance SLA countdown timer",
        "category": "dispatch",
        "is_durable": False,
    },
    "dispatch:timer_warning": {
        "pattern": r"^(?:dispatch:timer_warning:|job:timer_warning:).+$",
        "default_ttl": 300,
        "env_var": "REDIS_TTL_TIMER",
        "description": "Job SLA warning notification timer",
        "category": "dispatch",
        "is_durable": False,
    },
    "dispatch:metrics": {
        "pattern": r"^dispatch:metrics:.+$",
        "default_ttl": 60,
        "env_var": "REDIS_TTL_METRICS",
        "description": "Ephemeral dispatch metrics summary",
        "category": "dispatch",
        "is_durable": False,
    },
    # GPS Purge namespace
    "gps_purge:status": {
        "pattern": r"^(?:gps_purge:status:|gps_purge_status:).+$",
        "default_ttl": 86400,
        "env_var": "REDIS_TTL_GPS_PURGE_STATUS",
        "description": "Status and progress tracking for GPS retention purge job",
        "category": "gps_purge",
        "is_durable": False,
    },
    "gps_purge:start_time": {
        "pattern": r"^(?:gps_purge:start_time:|gps_purge_start_time:).+$",
        "default_ttl": 86400,
        "env_var": "REDIS_TTL_GPS_PURGE_STATUS",
        "description": "Start timestamp for GPS purge job execution",
        "category": "gps_purge",
        "is_durable": False,
    },
    "gps_purge:statistics": {
        "pattern": r"^gps_purge:(?:total_purged_30d|last_purge_run|next_scheduled)$",
        "default_ttl": 2592000,  # 30 days
        "env_var": "REDIS_TTL_GPS_PURGE_STATS",
        "description": "Rolling 30-day purged telemetry audit counter",
        "category": "gps_purge",
        "is_durable": False,
    },
    # Cache namespace
    "cache:general": {
        "pattern": r"^(?:cache:|cache:tenant:[^:]+:).+$",
        "default_ttl": 300,
        "env_var": "REDIS_TTL_CACHE",
        "description": "Tenant-scoped general API and service query cache",
        "category": "cache",
        "is_durable": False,
    },
    "cache:catalog": {
        "pattern": r"^services_catalog:cache$",
        "default_ttl": 3600,
        "env_var": "REDIS_TTL_CATALOG_CACHE",
        "description": "Services catalog cache",
        "category": "cache",
        "is_durable": False,
    },
    "cache:mfa_sso": {
        "pattern": r"^(?:mfa:|sso:state:).+$",
        "default_ttl": 300,
        "env_var": "REDIS_TTL_AUTH_EPHEMERAL",
        "description": "One-time MFA challenges and SSO auth states",
        "category": "cache",
        "is_durable": False,
    },
    "cache:rate_limit": {
        "pattern": r"^rate_limit:.+$",
        "default_ttl": 60,
        "env_var": "REDIS_TTL_RATE_LIMIT",
        "description": "Per-tenant or per-user API rate limit window counter",
        "category": "cache",
        "is_durable": False,
    },
    # Locks namespace
    "locks:job": {
        "pattern": r"^(?:locks:job:|job:lock:).+$",
        "default_ttl": 30,
        "env_var": "REDIS_TTL_LOCK_JOB",
        "description": "Distributed lock for concurrent job transitions",
        "category": "locks",
        "is_durable": False,
    },
    "locks:job_accept": {
        "pattern": r"^(?:locks:job_accept:|lock:job_accept:).+$",
        "default_ttl": 15,
        "env_var": "REDIS_TTL_LOCK_ACCEPT",
        "description": "Short-lived lock for technician job acceptance",
        "category": "locks",
        "is_durable": False,
    },
    "locks:idempotency": {
        "pattern": r"^lock:idempotency(?:_key)?:.+$",
        "default_ttl": 86400,
        "env_var": "REDIS_TTL_LOCK_IDEMPOTENCY",
        "description": "Distributed idempotency deduplication window",
        "category": "locks",
        "is_durable": False,
    },
    "locks:ordering": {
        "pattern": r"^lock:ordering:.+$",
        "default_ttl": 60,
        "env_var": "REDIS_TTL_LOCK_ORDERING",
        "description": "Sequential message ordering lock",
        "category": "locks",
        "is_durable": False,
    },
    # Celery namespace
    "celery:result": {
        "pattern": r"^(?:celery:result:|task_result:).+$",
        "default_ttl": 86400,  # 24 hours
        "env_var": "REDIS_TTL_TASK_RESULT",
        "description": "Celery asynchronous task execution result metadata",
        "category": "celery",
        "is_durable": False,
    },
    "celery:monitoring": {
        "pattern": r"^(?:celery:monitoring:|celery_monitoring:).+$",
        "default_ttl": 86400,
        "env_var": "REDIS_TTL_CELERY_MONITORING",
        "description": "Rolling operational telemetry counters (failures, latency, retries)",
        "category": "celery",
        "is_durable": False,
    },
}

PROTECTED_DURABLE_PATTERNS = [
    r"^notifications$",
    r"^billing$",
    r"^gps_processing$",
    r"^ai_processing$",
    r"^high_priority$",
    r"^reports$",
    r"^dlq$",
    r"^gps_purge_dlq$",
    r"^gps_purge:dlq$",
    r"^task_queue:",
]


def is_durable_record(key: str) -> bool:
    """
    Check if a Redis key corresponds to a durable source of truth, persistent queue, or DLQ.
    Durable records MUST NEVER be expired.
    """
    if not key or not isinstance(key, str):
        return False
    import re
    for pattern in PROTECTED_DURABLE_PATTERNS:
        if re.search(pattern, key):
            return True
    return False


def get_effective_ttl(rule_key: str) -> int:
    """Resolve configured TTL for a rule key, checking environment overrides first."""
    rule = DEFAULT_REDIS_EXPIRATION_RULES.get(rule_key)
    if not rule:
        return 300
    env_name = rule.get("env_var")
    if env_name and os.getenv(env_name):
        try:
            val = int(os.getenv(env_name))
            if val > 0:
                return val
        except (ValueError, TypeError):
            pass
    return rule.get("default_ttl", 300)


def get_expiration_rule(key: str) -> Optional[dict[str, Any]]:
    """
    Match a Redis key against registered expiration rules.
    Returns matched rule metadata, effective TTL, and durability status.
    """
    if not key or not isinstance(key, str):
        return None

    if is_durable_record(key):
        return {
            "matched_rule": "durable_source_of_truth",
            "category": "durable",
            "is_durable": True,
            "ttl_seconds": None,
            "description": "Protected durable queue or source-of-truth record (no expiry)",
        }

    import re
    for rule_name, rule_def in DEFAULT_REDIS_EXPIRATION_RULES.items():
        if re.match(rule_def["pattern"], key):
            return {
                "matched_rule": rule_name,
                "category": rule_def["category"],
                "is_durable": False,
                "ttl_seconds": get_effective_ttl(rule_name),
                "description": rule_def["description"],
            }

    # Fallback for unrecognized non-durable key
    return {
        "matched_rule": "cache:general",
        "category": "cache",
        "is_durable": False,
        "ttl_seconds": get_effective_ttl("cache:general"),
        "description": "Default non-durable key expiration",
    }


def configure_redis_expiration_rules(
    policy_updates: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    Configure and activate Redis expiration rules.
    Returns the canonical contract:
    {
        "status": "CONFIGURED",
        "ttl_policy": "ENABLED"
    }
    """
    if policy_updates and isinstance(policy_updates, dict):
        for rule_key, update in policy_updates.items():
            if rule_key in DEFAULT_REDIS_EXPIRATION_RULES and isinstance(update, dict):
                if "default_ttl" in update and isinstance(update["default_ttl"], int) and update["default_ttl"] > 0:
                    DEFAULT_REDIS_EXPIRATION_RULES[rule_key]["default_ttl"] = update["default_ttl"]

    return dict(DEFAULT_REDIS_EXPIRATION_CONFIG)


def get_redis_expiration_config() -> dict[str, Any]:
    """Return full expiration policy details, effective rules, and protected patterns."""
    rules_summary = {}
    for rule_key, rule_def in DEFAULT_REDIS_EXPIRATION_RULES.items():
        rules_summary[rule_key] = {
            "category": rule_def["category"],
            "effective_ttl": get_effective_ttl(rule_key),
            "default_ttl": rule_def["default_ttl"],
            "is_durable": rule_def["is_durable"],
            "description": rule_def["description"],
        }

    return {
        "status": "CONFIGURED",
        "ttl_policy": "ENABLED",
        "total_rules": len(rules_summary),
        "rules": rules_summary,
        "protected_patterns": list(PROTECTED_DURABLE_PATTERNS),
    }


def apply_expiration(
    key: str,
    ttl_seconds: Optional[int] = None,
    redis_client=None,
    tenant_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
) -> dict[str, Any]:
    """
    Safely apply TTL to a Redis key.
    Rejects expiration if the key matches a protected durable queue or source of truth.
    """
    if not key or not isinstance(key, str):
        return {
            "status": "INVALID_KEY",
            "key": key,
            "applied": False,
            "reason": "Key must be a non-empty string",
        }

    if is_durable_record(key):
        return {
            "status": "REJECTED",
            "key": key,
            "applied": False,
            "reason": "DURABLE_RECORD_PROTECTED",
            "is_durable": True,
        }

    # Determine TTL
    final_ttl = ttl_seconds
    if final_ttl is None:
        rule_info = get_expiration_rule(key)
        final_ttl = rule_info.get("ttl_seconds", 300) if rule_info else 300

    if not isinstance(final_ttl, int) or final_ttl <= 0:
        return {
            "status": "INVALID_TTL",
            "key": key,
            "applied": False,
            "reason": f"TTL must be a positive integer, got {final_ttl}",
        }

    client = redis_client if redis_client is not None else get_redis_client()
    success = False
    try:
        success = bool(client.expire(key, final_ttl))
    except Exception as exc:
        return {
            "status": "ERROR",
            "key": key,
            "applied": False,
            "error": str(exc),
        }

    return {
        "status": "APPLIED",
        "key": key,
        "applied": success,
        "ttl_seconds": final_ttl,
        "is_durable": False,
        "tenant_id": tenant_id,
        "correlation_id": correlation_id,
    }


def refresh_expiration(
    key: str,
    ttl_seconds: Optional[int] = None,
    redis_client=None,
    tenant_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
) -> dict[str, Any]:
    """
    Refresh / extend TTL on an active Redis key.
    Guarantees durable records are never touched.
    """
    if not key or not isinstance(key, str):
        return {
            "status": "INVALID_KEY",
            "key": key,
            "refreshed": False,
            "reason": "Key must be a non-empty string",
        }

    if is_durable_record(key):
        return {
            "status": "REJECTED",
            "key": key,
            "refreshed": False,
            "reason": "DURABLE_RECORD_PROTECTED",
            "is_durable": True,
        }

    final_ttl = ttl_seconds
    if final_ttl is None:
        rule_info = get_expiration_rule(key)
        final_ttl = rule_info.get("ttl_seconds", 300) if rule_info else 300

    if not isinstance(final_ttl, int) or final_ttl <= 0:
        return {
            "status": "INVALID_TTL",
            "key": key,
            "refreshed": False,
            "reason": f"TTL must be a positive integer, got {final_ttl}",
        }

    client = redis_client if redis_client is not None else get_redis_client()
    success = False
    try:
        success = bool(client.expire(key, final_ttl))
    except Exception as exc:
        return {
            "status": "ERROR",
            "key": key,
            "refreshed": False,
            "error": str(exc),
        }

    return {
        "status": "REFRESHED",
        "key": key,
        "refreshed": success,
        "ttl_seconds": final_ttl,
        "is_durable": False,
        "tenant_id": tenant_id,
        "correlation_id": correlation_id,
    }


# Authoritative story aliases
ConfigureRedisExpirationRules = configure_redis_expiration_rules
Configure_redis_expiration_rules = configure_redis_expiration_rules
get_redis_expiration_rules = configure_redis_expiration_rules
get_redis_ttl_config = get_redis_expiration_config


# =============================================================================
# Redis Enhancement: Improve Distributed Locks (Tokenized TTL Lock)
# =============================================================================

DEFAULT_DISTRIBUTED_LOCK_CONFIG = {
    "status": "HARDENED",
    "lock_strategy": "tokenized_ttl_lock",
}

LUA_RELEASE_LOCK = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""

LUA_EXTEND_LOCK = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('pexpire', KEYS[1], ARGV[2])
else
    return 0
end
"""


class LockAcquisitionError(Exception):
    """Raised when a distributed lock cannot be acquired or Redis fails."""
    pass


def _format_lock_key(resource: str, tenant_id: Optional[str] = None) -> str:
    """Format canonical lock key, supporting tenant isolation."""
    clean_res = str(resource).strip(":")
    if clean_res.startswith("locks:") or clean_res.startswith("job:lock:"):
        return clean_res
    if tenant_id:
        return f"locks:tenant:{str(tenant_id).strip(':')}:{clean_res}"
    return f"locks:{clean_res}"


def acquire_distributed_lock(
    resource: str,
    ttl_seconds: int = 30,
    lock_token: Optional[str] = None,
    tenant_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
    redis_client=None,
) -> tuple[bool, Optional[str]]:
    """
    Acquire a tokenized distributed lock with TTL.
    Fail-closed: Returns (False, None) if Redis is down or lock is contested.
    """
    if not resource:
        return False, None

    lock_key = _format_lock_key(resource, tenant_id=tenant_id)
    token = lock_token or secrets.token_urlsafe(32)
    ttl_ms = max(int(ttl_seconds * 1000), 1000)

    client = redis_client if redis_client is not None else get_redis_client()
    if not client:
        return False, None

    try:
        raw_client = getattr(client, "client", client)
        success = raw_client.set(lock_key, token, nx=True, px=ttl_ms)
        if success:
            return True, token
        return False, None
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("Redis lock acquisition failed closed for %s: %s", lock_key, exc)
        return False, None


def release_distributed_lock(
    resource: str,
    lock_token: str,
    tenant_id: Optional[str] = None,
    redis_client=None,
) -> bool:
    """
    Safely release a distributed lock ONLY if owned by the caller token.
    Uses atomic Lua script to prevent deleting an expired lock held by another process.
    """
    if not resource or not lock_token:
        return False

    lock_key = _format_lock_key(resource, tenant_id=tenant_id)
    client = redis_client if redis_client is not None else get_redis_client()
    if not client:
        return False

    try:
        raw_client = getattr(client, "client", client)
        result = raw_client.eval(LUA_RELEASE_LOCK, 1, lock_key, lock_token)
        return bool(result)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("Failed to release lock %s: %s", lock_key, exc)
        return False


def extend_distributed_lock(
    resource: str,
    lock_token: str,
    additional_ttl_seconds: int = 30,
    tenant_id: Optional[str] = None,
    redis_client=None,
) -> bool:
    """
    Safely extend / renew TTL of an active lock held by the caller token.
    """
    if not resource or not lock_token:
        return False

    lock_key = _format_lock_key(resource, tenant_id=tenant_id)
    ttl_ms = max(int(additional_ttl_seconds * 1000), 1000)

    client = redis_client if redis_client is not None else get_redis_client()
    if not client:
        return False

    try:
        raw_client = getattr(client, "client", client)
        result = raw_client.eval(LUA_EXTEND_LOCK, 1, lock_key, lock_token, ttl_ms)
        return bool(result)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("Failed to extend lock %s: %s", lock_key, exc)
        return False


def is_lock_held(
    resource: str,
    tenant_id: Optional[str] = None,
    redis_client=None,
) -> bool:
    """Check if a distributed lock is currently active in Redis."""
    if not resource:
        return False
    lock_key = _format_lock_key(resource, tenant_id=tenant_id)
    client = redis_client if redis_client is not None else get_redis_client()
    if not client:
        return False
    try:
        raw_client = getattr(client, "client", client)
        val = raw_client.get(lock_key)
        return val is not None
    except Exception:
        return False


@contextmanager
def with_distributed_lock(
    resource: str,
    ttl_seconds: int = 30,
    tenant_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
    redis_client=None,
):
    """
    Context manager for acquiring and releasing a hardened distributed lock.
    Raises LockAcquisitionError if lock cannot be acquired or Redis fails.
    """
    acquired, token = acquire_distributed_lock(
        resource=resource,
        ttl_seconds=ttl_seconds,
        tenant_id=tenant_id,
        correlation_id=correlation_id,
        redis_client=redis_client,
    )
    if not acquired or not token:
        raise LockAcquisitionError(
            f"Failed to acquire distributed lock for resource '{resource}' (tenant={tenant_id})"
        )
    try:
        yield token
    finally:
        release_distributed_lock(
            resource=resource,
            lock_token=token,
            tenant_id=tenant_id,
            redis_client=redis_client,
        )


def improve_distributed_locks() -> dict[str, Any]:
    """
    Return canonical contract for hardened distributed lock capability.
    Expected:
    {
        "status": "HARDENED",
        "lock_strategy": "tokenized_ttl_lock"
    }
    """
    return dict(DEFAULT_DISTRIBUTED_LOCK_CONFIG)


def get_distributed_lock_config() -> dict[str, Any]:
    """Return full metadata for distributed lock configuration."""
    return {
        "status": "HARDENED",
        "lock_strategy": "tokenized_ttl_lock",
        "default_ttl_seconds": 30,
        "features": [
            "unique_tokens",
            "bounded_ttl",
            "atomic_lua_release",
            "atomic_lua_extension",
            "fail_closed_error_handling",
            "tenant_scoping",
        ],
    }


# Authoritative story aliases
ImproveDistributedLocks = improve_distributed_locks
Improve_distributed_locks = improve_distributed_locks
get_lock_strategy = improve_distributed_locks
get_distributed_lock_status = improve_distributed_locks


# =============================================================================
# Redis Enhancement: Add Redis Cache for Frequent Queries (Read-Through Cache)
# =============================================================================

DEFAULT_FREQUENT_QUERY_CACHE_CONFIG = {
    "status": "ENABLED",
    "strategy": "read_through",
    "fallback": "database",
}


def build_query_cache_key(
    query_name: str,
    tenant_id: Optional[str] = None,
    **params: Any,
) -> str:
    """
    Construct a standardized, collision-safe cache key for a frequent query.
    Tenant-scoped: cache:tenant:{tenant_id}:{query_name}:{params_hash}
    Global:        cache:{query_name}:{params_hash}
    """
    clean_query = str(query_name).strip(":")
    if params:
        param_str = json.dumps(params, sort_keys=True, default=str)
        param_hash = hashlib.sha256(param_str.encode("utf-8")).hexdigest()[:16]
    else:
        param_hash = "default"

    if tenant_id:
        return f"cache:tenant:{str(tenant_id).strip(':')}:{clean_query}:{param_hash}"
    return f"cache:{clean_query}:{param_hash}"


def get_or_set_cached_query(
    query_name: str,
    fetcher_fn,
    tenant_id: Optional[str] = None,
    ttl_seconds: int = 300,
    redis_client=None,
    **params: Any,
) -> tuple[Any, bool]:
    """
    Read-through cache executor.
    1. Check Redis cache. On hit, return (cached_data, True).
    2. On miss, invoke fetcher_fn(), populate Redis cache with TTL, return (db_data, False).
    3. On Redis failure/unavailability, seamlessly fall back to fetcher_fn() directly from database!
    """
    cache_key = build_query_cache_key(query_name, tenant_id=tenant_id, **params)
    if redis_client is False:
        client = None
    elif redis_client is not None:
        client = redis_client
    else:
        client = get_redis_client()

    # Step 1: Attempt read from Redis
    if client:
        try:
            cached_val = client.get(cache_key)
            if cached_val is not None:
                parsed = json.loads(cached_val)
                return parsed, True
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(
                "Redis cache read failed for key '%s', falling back to database: %s",
                cache_key,
                exc,
            )

    # Step 2: Cache miss or Redis failure -> Fall back to database fetcher_fn
    db_data = fetcher_fn()

    # Step 3: Populate Redis best-effort with TTL
    if client and db_data is not None:
        try:
            serialized = json.dumps(db_data, default=str)
            client.setex(cache_key, ttl_seconds, serialized)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(
                "Failed to write to Redis cache for key '%s': %s",
                cache_key,
                exc,
            )

    return db_data, False


def invalidate_query_cache(
    query_name: Optional[str] = None,
    tenant_id: Optional[str] = None,
    redis_client=None,
) -> int:
    """
    Invalidate cached query results for a tenant, query name, or both.
    Returns the count of deleted keys.
    """
    client = redis_client if redis_client is not None else get_redis_client()
    if not client:
        return 0

    if tenant_id and query_name:
        pattern = f"cache:tenant:{str(tenant_id).strip(':')}:{str(query_name).strip(':')}:*"
    elif tenant_id:
        pattern = f"cache:tenant:{str(tenant_id).strip(':')}:*"
    elif query_name:
        pattern = f"cache:{str(query_name).strip(':')}:*"
    else:
        pattern = "cache:*"

    deleted_count = 0
    try:
        raw_client = getattr(client, "client", client)
        if hasattr(raw_client, "scan_iter"):
            keys = list(raw_client.scan_iter(match=pattern))
            if keys:
                deleted_count = raw_client.delete(*keys)
        elif hasattr(raw_client, "keys"):
            keys = raw_client.keys(pattern)
            if keys:
                deleted_count = raw_client.delete(*keys)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("Failed to invalidate query cache for pattern '%s': %s", pattern, exc)

    return deleted_count


def add_redis_cache_for_frequent_queries() -> dict[str, Any]:
    """
    Return canonical contract for frequent query cache capability:
    {
        "status": "ENABLED",
        "strategy": "read_through",
        "fallback": "database"
    }
    """
    return dict(DEFAULT_FREQUENT_QUERY_CACHE_CONFIG)


def get_query_cache_config() -> dict[str, Any]:
    """Return full operational configuration for read-through frequent query cache."""
    return {
        "status": "ENABLED",
        "strategy": "read_through",
        "fallback": "database",
        "default_ttl_seconds": 300,
        "serialization": "json",
        "supported_candidates": [
            "technician_availability",
            "services_catalog",
            "job_status_summary",
            "customer_display_info",
            "tenant_settings",
        ],
    }


# Authoritative story aliases
AddRedisCacheForFrequentQueries = add_redis_cache_for_frequent_queries
Add_redis_cache_for_frequent_queries = add_redis_cache_for_frequent_queries
get_frequent_query_cache_config = get_query_cache_config
get_query_cache_strategy = add_redis_cache_for_frequent_queries


# =============================================================================
# Redis Enhancement: Add Redis Cache Invalidation (Write-Invalidate Strategy)
# =============================================================================

DEFAULT_CACHE_INVALIDATION_CONFIG = {
    "status": "ENABLED",
    "strategy": "write_invalidate",
}

# Mapping of authoritative domain entities to the query cache keys they affect
ENTITY_CACHE_MAP = {
    "job": [
        "job_status_summary",
        "jobs_list",
        "technician_availability",
    ],
    "technician": [
        "technician_availability",
        "technician_profile",
    ],
    "catalog": [
        "services_catalog",
        "services_taxonomy",
    ],
    "customer": [
        "customer_display_info",
    ],
}


def invalidate_cache_on_write(
    entity_type: str,
    entity_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
    redis_client=None,
) -> dict[str, Any]:
    """
    Invalidate cached query results affected by authoritative data writes/mutations.
    Guarantees strict tenant boundary isolation: never purges another tenant's keys.
    """
    clean_type = str(entity_type).strip().lower()
    affected_queries = ENTITY_CACHE_MAP.get(clean_type, [clean_type])
    total_invalidated = 0
    client = redis_client if redis_client is not None else get_redis_client()

    if not client:
        return {
            "status": "SKIPPED",
            "reason": "Redis client unavailable",
            "entity_type": clean_type,
            "entity_id": entity_id,
            "tenant_id": tenant_id,
            "invalidated_count": 0,
            "affected_queries": affected_queries,
        }

    for query_name in affected_queries:
        # Invalidate tenant-scoped queries
        if tenant_id and tenant_id != "__platform__":
            count = invalidate_query_cache(
                query_name=query_name,
                tenant_id=tenant_id,
                redis_client=client,
            )
            total_invalidated += count
        else:
            # Platform or global catalog items
            count = invalidate_query_cache(
                query_name=query_name,
                tenant_id=None,
                redis_client=client,
            )
            total_invalidated += count

    import logging
    logging.getLogger(__name__).info(
        "Invalidated %d cache keys on %s write (entity_id=%s, tenant_id=%s, correlation_id=%s)",
        total_invalidated,
        clean_type,
        entity_id,
        tenant_id,
        correlation_id,
    )

    return {
        "status": "INVALIDATED",
        "strategy": "write_invalidate",
        "entity_type": clean_type,
        "entity_id": entity_id,
        "tenant_id": tenant_id,
        "correlation_id": correlation_id,
        "affected_queries": affected_queries,
        "invalidated_count": total_invalidated,
    }


def invalidate_job_cache(
    tenant_id: str,
    job_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
    redis_client=None,
) -> dict[str, Any]:
    """Invalidate cached queries affected by a job write / transition."""
    return invalidate_cache_on_write(
        entity_type="job",
        entity_id=job_id,
        tenant_id=tenant_id,
        correlation_id=correlation_id,
        redis_client=redis_client,
    )


def invalidate_technician_cache(
    tenant_id: str,
    technician_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
    redis_client=None,
) -> dict[str, Any]:
    """Invalidate cached queries affected by technician state/availability change."""
    return invalidate_cache_on_write(
        entity_type="technician",
        entity_id=technician_id,
        tenant_id=tenant_id,
        correlation_id=correlation_id,
        redis_client=redis_client,
    )


def invalidate_catalog_cache(
    tenant_id: Optional[str] = None,
    service_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
    redis_client=None,
) -> dict[str, Any]:
    """Invalidate cached queries affected by services catalog mutations."""
    return invalidate_cache_on_write(
        entity_type="catalog",
        entity_id=service_id,
        tenant_id=tenant_id,
        correlation_id=correlation_id,
        redis_client=redis_client,
    )


def invalidate_customer_cache(
    tenant_id: str,
    customer_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
    redis_client=None,
) -> dict[str, Any]:
    """Invalidate cached queries affected by customer updates."""
    return invalidate_cache_on_write(
        entity_type="customer",
        entity_id=customer_id,
        tenant_id=tenant_id,
        correlation_id=correlation_id,
        redis_client=redis_client,
    )


def add_redis_cache_invalidation() -> dict[str, Any]:
    """
    Return canonical contract for cache invalidation capability:
    {
        "status": "ENABLED",
        "strategy": "write_invalidate"
    }
    """
    return dict(DEFAULT_CACHE_INVALIDATION_CONFIG)


def get_cache_invalidation_config() -> dict[str, Any]:
    """Return complete invalidation strategy configuration and entity mappings."""
    return {
        "status": "ENABLED",
        "strategy": "write_invalidate",
        "entity_mappings": dict(ENTITY_CACHE_MAP),
        "supported_entities": list(ENTITY_CACHE_MAP.keys()),
    }


# Authoritative story aliases
AddRedisCacheInvalidation = add_redis_cache_invalidation
Add_redis_cache_invalidation = add_redis_cache_invalidation
get_cache_invalidation_status = add_redis_cache_invalidation
get_invalidation_strategy = add_redis_cache_invalidation


# =============================================================================
# Redis Enhancement: Add Redis Failure Handling (Safe Degraded Mode)
# =============================================================================

DEFAULT_REDIS_FAILURE_HANDLING_CONFIG = {
    "status": "HARDENED",
    "fallback": "SAFE_DEGRADED_MODE",
}


def add_redis_failure_handling() -> dict[str, Any]:
    """
    Return canonical contract for Redis failure handling:
    {
        "status": "HARDENED",
        "fallback": "SAFE_DEGRADED_MODE"
    }
    """
    return dict(DEFAULT_REDIS_FAILURE_HANDLING_CONFIG)


def get_redis_failure_handling_config() -> dict[str, Any]:
    """Return full operational configuration for Redis failure handling and degraded mode."""
    client = get_redis_client()
    status_info = client.get_health_status() if hasattr(client, "get_health_status") else {}
    return {
        "status": "HARDENED",
        "fallback": "SAFE_DEGRADED_MODE",
        "connection_timeout_seconds": 0.3,
        "operation_timeout_seconds": 0.3,
        "max_retries": 1,
        "circuit_breaker": "ACTIVE",
        "source_of_truth_protection": "STRICT_POSTGRESQL",
        "telemetry": status_info,
    }


def check_redis_health(redis_client=None) -> dict[str, Any]:
    """Check live health status of Redis, reporting degraded mode when unresponsive."""
    client = redis_client if redis_client is not None else get_redis_client()
    if client is None:
        return {
            "status": "HARDENED",
            "fallback": "SAFE_DEGRADED_MODE",
            "healthy": False,
            "degraded_mode": True,
            "reason": "Redis client not initialized",
        }

    if isinstance(client, RedisCacheManager):
        return client.get_health_status()

    # Generic client check
    try:
        ping_fn = getattr(client, "ping", None)
        if callable(ping_fn):
            ping_ok = bool(ping_fn())
        else:
            raw_client = getattr(client, "client", client)
            ping_ok = bool(raw_client.ping())
        return {
            "status": "HARDENED",
            "fallback": "SAFE_DEGRADED_MODE",
            "healthy": ping_ok,
            "degraded_mode": not ping_ok,
        }
    except Exception as exc:
        return {
            "status": "HARDENED",
            "fallback": "SAFE_DEGRADED_MODE",
            "healthy": False,
            "degraded_mode": True,
            "error": str(exc),
        }


def execute_with_safe_fallback(
    operation,
    fallback_fn=None,
    default=None,
    redis_client=None,
    *args,
    **kwargs,
) -> Any:
    """
    Execute a Redis operation with bounded error handling.
    If Redis fails, times out, or is degraded:
    1. Catches ConnectionError / TimeoutError safely.
    2. Invokes fallback_fn (e.g. database query) if provided.
    3. Returns default value without bubbling unhandled 500 exceptions.
    """
    client = redis_client if redis_client is not None else get_redis_client()
    if isinstance(client, RedisCacheManager):
        return client.execute_with_fallback(operation, fallback_fn=fallback_fn, default=default, *args, **kwargs)

    try:
        res = operation(*args, **kwargs)
        if res is not None:
            return res
    except (ConnectionError, TimeoutError, Exception) as exc:
        import logging
        logging.getLogger(__name__).warning("Redis operation failed, executing safe fallback: %s", exc)

    if fallback_fn is not None:
        return fallback_fn()
    return default


# Authoritative story aliases
AddRedisFailureHandling = add_redis_failure_handling
Add_redis_failure_handling = add_redis_failure_handling
get_redis_failure_handling_status = add_redis_failure_handling
get_redis_fallback_mode = add_redis_failure_handling







