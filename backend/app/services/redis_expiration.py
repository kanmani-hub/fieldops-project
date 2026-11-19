"""
redis_expiration.py

Dedicated service module for Redis Expiration Rules & TTL Management.
Provides safe expiration policies, durable source-of-truth protection,
refresh behaviors, and tenant isolation across FieldOps core namespaces.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from app.redis_client import (
    DEFAULT_REDIS_EXPIRATION_CONFIG,
    DEFAULT_REDIS_EXPIRATION_RULES,
    PROTECTED_DURABLE_PATTERNS,
    is_durable_record,
    get_effective_ttl,
    get_expiration_rule,
    configure_redis_expiration_rules,
    get_redis_expiration_config,
    apply_expiration,
    refresh_expiration,
    ConfigureRedisExpirationRules,
    Configure_redis_expiration_rules,
    get_redis_expiration_rules,
    get_redis_ttl_config,
    get_redis_client,
)


class RedisExpirationManager:
    """Manager coordinating TTL policies, durable record safeguards, and key expiration refreshes."""

    def __init__(self, redis_client=None):
        self._redis = redis_client
        self.rules = DEFAULT_REDIS_EXPIRATION_RULES
        self.protected_patterns = PROTECTED_DURABLE_PATTERNS

    @property
    def redis(self):
        if self._redis is None:
            self._redis = get_redis_client()
        return self._redis

    def get_config(self) -> Dict[str, Any]:
        """Return canonical status contract {"status": "CONFIGURED", "ttl_policy": "ENABLED"}."""
        return configure_redis_expiration_rules()

    def get_details(self) -> Dict[str, Any]:
        """Return full policy metadata, rules, and protected patterns."""
        return get_redis_expiration_config()

    def is_durable(self, key: str) -> bool:
        """Check if key is protected durable data."""
        return is_durable_record(key)

    def get_rule_for_key(self, key: str) -> Optional[Dict[str, Any]]:
        """Resolve rule definition for given key."""
        return get_expiration_rule(key)

    def apply(
        self,
        key: str,
        ttl_seconds: Optional[int] = None,
        tenant_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Apply rule-based or explicit TTL to key, rejecting durable keys."""
        return apply_expiration(
            key=key,
            ttl_seconds=ttl_seconds,
            redis_client=self.redis,
            tenant_id=tenant_id,
            correlation_id=correlation_id,
        )

    def refresh(
        self,
        key: str,
        ttl_seconds: Optional[int] = None,
        tenant_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Refresh TTL on active key, rejecting durable keys."""
        return refresh_expiration(
            key=key,
            ttl_seconds=ttl_seconds,
            redis_client=self.redis,
            tenant_id=tenant_id,
            correlation_id=correlation_id,
        )

    def configure(self, policy_updates: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Update runtime policy TTLs."""
        return configure_redis_expiration_rules(policy_updates=policy_updates)


class RedisExpirationService(RedisExpirationManager):
    """Operational service alias for Redis expiration management."""
    pass


# Default singleton service instance
redis_expiration_service = RedisExpirationService()


__all__ = [
    "DEFAULT_REDIS_EXPIRATION_CONFIG",
    "DEFAULT_REDIS_EXPIRATION_RULES",
    "PROTECTED_DURABLE_PATTERNS",
    "is_durable_record",
    "get_effective_ttl",
    "get_expiration_rule",
    "configure_redis_expiration_rules",
    "get_redis_expiration_config",
    "apply_expiration",
    "refresh_expiration",
    "ConfigureRedisExpirationRules",
    "Configure_redis_expiration_rules",
    "get_redis_expiration_rules",
    "get_redis_ttl_config",
    "RedisExpirationManager",
    "RedisExpirationService",
    "redis_expiration_service",
]
