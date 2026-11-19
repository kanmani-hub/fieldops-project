"""
redis_key_structure.py

Dedicated service module for Redis Key Structure Review & Standardization.
Provides inventory metadata, collision prevention, tenant scoping, and key migration
identification across FieldOps core namespaces: dispatch, gps_purge, cache, locks, celery.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from app.redis_client import (
    REDIS_KEY_NAMESPACES,
    DEFAULT_REDIS_KEY_REVIEW,
    REDIS_KEY_INVENTORY,
    build_redis_key,
    build_tenant_key,
    parse_redis_key,
    review_redis_key_structure,
    get_redis_key_inventory,
    identify_keys_for_migration,
    ReviewRedisKeyStructure,
    Review_redis_key_structure,
    get_redis_key_structure_review,
    get_redis_namespaces,
    get_redis_client,
)


class RedisKeyRegistry:
    """Registry coordinating standard key namespaces, ownership, and value schemas."""

    def __init__(self, namespaces: Optional[List[str]] = None):
        self.namespaces = namespaces or list(REDIS_KEY_NAMESPACES)
        self.inventory = REDIS_KEY_INVENTORY

    def get_review(self) -> Dict[str, Any]:
        return review_redis_key_structure()

    def get_inventory(self) -> Dict[str, Any]:
        return get_redis_key_inventory()

    def get_namespace_details(self, namespace: str) -> Optional[Dict[str, Any]]:
        return self.inventory.get(namespace)

    def build_key(self, namespace: str, *parts: str, tenant_id: Optional[str] = None) -> str:
        if namespace not in self.namespaces:
            raise ValueError(f"Invalid namespace '{namespace}'. Must be one of {self.namespaces}")
        return build_redis_key(namespace, *parts, tenant_id=tenant_id)

    def parse_key(self, key: str) -> Dict[str, Any]:
        return parse_redis_key(key)


class RedisKeyStructureService:
    """Operational service for reviewing key namespaces and analyzing migration requirements."""

    def __init__(self, redis_client=None):
        self._redis = redis_client
        self.registry = RedisKeyRegistry()

    @property
    def redis(self):
        if self._redis is None:
            self._redis = get_redis_client()
        return self._redis

    def review(self) -> Dict[str, Any]:
        return self.registry.get_review()

    def inventory(self) -> Dict[str, Any]:
        return self.registry.get_inventory()

    def identify_migrations(self, keys: Optional[List[str]] = None) -> Dict[str, Any]:
        return identify_keys_for_migration(redis_client=self.redis, keys_to_check=keys)

    def format_key(self, namespace: str, *parts: str, tenant_id: Optional[str] = None) -> str:
        return self.registry.build_key(namespace, *parts, tenant_id=tenant_id)


# Default singleton instance
redis_key_structure_service = RedisKeyStructureService()


__all__ = [
    "REDIS_KEY_NAMESPACES",
    "DEFAULT_REDIS_KEY_REVIEW",
    "REDIS_KEY_INVENTORY",
    "build_redis_key",
    "build_tenant_key",
    "parse_redis_key",
    "review_redis_key_structure",
    "get_redis_key_inventory",
    "identify_keys_for_migration",
    "ReviewRedisKeyStructure",
    "Review_redis_key_structure",
    "get_redis_key_structure_review",
    "get_redis_namespaces",
    "RedisKeyRegistry",
    "RedisKeyStructureService",
    "redis_key_structure_service",
]
