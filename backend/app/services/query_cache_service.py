"""
query_cache_service.py

Dedicated service module for Read-Through Frequent Query Caching in Redis.
Provides safe caching for read-heavy, non-authoritative queries with tenant isolation,
SHA-256 parameter hashing, and seamless database fallback upon cache miss or Redis failure.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple
from app.redis_client import (
    DEFAULT_FREQUENT_QUERY_CACHE_CONFIG,
    build_query_cache_key,
    get_or_set_cached_query,
    invalidate_query_cache,
    add_redis_cache_for_frequent_queries,
    get_query_cache_config,
    AddRedisCacheForFrequentQueries,
    Add_redis_cache_for_frequent_queries,
    get_frequent_query_cache_config,
    get_query_cache_strategy,
    get_redis_client,
)


class QueryCacheManager:
    """Manager coordinating read-through query caching, tenant scoping, and database fallback."""

    def __init__(self, redis_client=None):
        self._redis = redis_client

    @property
    def redis(self):
        if self._redis is None:
            self._redis = get_redis_client()
        return self._redis

    def get_status(self) -> Dict[str, Any]:
        """Return canonical contract {"status": "ENABLED", "strategy": "read_through", "fallback": "database"}."""
        return add_redis_cache_for_frequent_queries()

    def get_config(self) -> Dict[str, Any]:
        """Return full query cache configuration."""
        return get_query_cache_config()

    def build_key(self, query_name: str, tenant_id: Optional[str] = None, **params: Any) -> str:
        """Construct a standardized collision-safe query cache key."""
        return build_query_cache_key(query_name, tenant_id=tenant_id, **params)

    def execute(
        self,
        query_name: str,
        fetcher_fn: Callable[[], Any],
        tenant_id: Optional[str] = None,
        ttl_seconds: int = 300,
        **params: Any,
    ) -> Tuple[Any, bool]:
        """
        Execute query with read-through caching and database fallback.
        Returns (data, is_cached).
        """
        return get_or_set_cached_query(
            query_name=query_name,
            fetcher_fn=fetcher_fn,
            tenant_id=tenant_id,
            ttl_seconds=ttl_seconds,
            redis_client=self.redis,
            **params,
        )

    def invalidate(
        self,
        query_name: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> int:
        """Invalidate cached results for a query and/or tenant."""
        return invalidate_query_cache(
            query_name=query_name,
            tenant_id=tenant_id,
            redis_client=self.redis,
        )


class QueryCacheService(QueryCacheManager):
    """Operational service alias for query caching."""
    pass


# Default singleton instance
query_cache_service = QueryCacheService()


__all__ = [
    "DEFAULT_FREQUENT_QUERY_CACHE_CONFIG",
    "build_query_cache_key",
    "get_or_set_cached_query",
    "invalidate_query_cache",
    "add_redis_cache_for_frequent_queries",
    "get_query_cache_config",
    "AddRedisCacheForFrequentQueries",
    "Add_redis_cache_for_frequent_queries",
    "get_frequent_query_cache_config",
    "get_query_cache_strategy",
    "QueryCacheManager",
    "QueryCacheService",
    "query_cache_service",
]
