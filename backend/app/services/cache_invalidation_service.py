"""
cache_invalidation_service.py

Dedicated service module for Redis Cache Invalidation (Write-Invalidate Strategy).
Coordinates cache purging upon authoritative data writes, status transitions, and deletions,
strictly respecting tenant isolation boundaries so users never see stale operational data.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from app.redis_client import (
    DEFAULT_CACHE_INVALIDATION_CONFIG,
    ENTITY_CACHE_MAP,
    invalidate_cache_on_write,
    invalidate_job_cache,
    invalidate_technician_cache,
    invalidate_catalog_cache,
    invalidate_customer_cache,
    add_redis_cache_invalidation,
    get_cache_invalidation_config,
    AddRedisCacheInvalidation,
    Add_redis_cache_invalidation,
    get_cache_invalidation_status,
    get_invalidation_strategy,
    get_redis_client,
)


class CacheInvalidationManager:
    """Manager coordinating cache invalidation on authoritative data writes."""

    def __init__(self, redis_client=None):
        self._redis = redis_client
        self.entity_map = ENTITY_CACHE_MAP

    @property
    def redis(self):
        if self._redis is None:
            self._redis = get_redis_client()
        return self._redis

    def get_status(self) -> Dict[str, Any]:
        """Return canonical contract {"status": "ENABLED", "strategy": "write_invalidate"}."""
        return add_redis_cache_invalidation()

    def get_config(self) -> Dict[str, Any]:
        """Return full invalidation configuration and mappings."""
        return get_cache_invalidation_config()

    def invalidate_write(
        self,
        entity_type: str,
        entity_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Invalidate queries affected by a write to any registered entity."""
        return invalidate_cache_on_write(
            entity_type=entity_type,
            entity_id=entity_id,
            tenant_id=tenant_id,
            correlation_id=correlation_id,
            redis_client=self.redis,
        )

    def invalidate_job(
        self,
        tenant_id: str,
        job_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Invalidate cache on job state changes or assignments."""
        return invalidate_job_cache(
            tenant_id=tenant_id,
            job_id=job_id,
            correlation_id=correlation_id,
            redis_client=self.redis,
        )

    def invalidate_technician(
        self,
        tenant_id: str,
        technician_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Invalidate cache on technician availability or profile updates."""
        return invalidate_technician_cache(
            tenant_id=tenant_id,
            technician_id=technician_id,
            correlation_id=correlation_id,
            redis_client=self.redis,
        )

    def invalidate_catalog(
        self,
        tenant_id: Optional[str] = None,
        service_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Invalidate cache on catalog items add/toggle/delete."""
        return invalidate_catalog_cache(
            tenant_id=tenant_id,
            service_id=service_id,
            correlation_id=correlation_id,
            redis_client=self.redis,
        )

    def invalidate_customer(
        self,
        tenant_id: str,
        customer_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Invalidate cache on customer profile updates."""
        return invalidate_customer_cache(
            tenant_id=tenant_id,
            customer_id=customer_id,
            correlation_id=correlation_id,
            redis_client=self.redis,
        )


class CacheInvalidationService(CacheInvalidationManager):
    """Operational service alias for cache invalidation."""
    pass


# Default singleton service instance
cache_invalidation_service = CacheInvalidationService()


__all__ = [
    "DEFAULT_CACHE_INVALIDATION_CONFIG",
    "ENTITY_CACHE_MAP",
    "invalidate_cache_on_write",
    "invalidate_job_cache",
    "invalidate_technician_cache",
    "invalidate_catalog_cache",
    "invalidate_customer_cache",
    "add_redis_cache_invalidation",
    "get_cache_invalidation_config",
    "AddRedisCacheInvalidation",
    "Add_redis_cache_invalidation",
    "get_cache_invalidation_status",
    "get_invalidation_strategy",
    "CacheInvalidationManager",
    "CacheInvalidationService",
    "cache_invalidation_service",
]
