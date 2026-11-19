"""
test_frequent_query_cache.py

Comprehensive test suite for Story: Add Redis cache for frequent queries (Redis Enhancement capability).
Validates:
- Canonical JSON output contract:
    {
        "status": "ENABLED",
        "strategy": "read_through",
        "fallback": "database"
    }
- Safe read-through caching for read-heavy non-authoritative queries.
- Tenant isolation and parameter hashing (cache:tenant:{tenant_id}:{query}:{hash}).
- Database fallback when Redis is unavailable or raises connection/timeout errors.
- Cache hit vs cache miss mechanics and TTL adherence.
- Cache invalidation by query name, tenant ID, or global scope.
- Integration with QueryCacheService and QueryCacheManager.
- Celery task add_redis_cache_for_frequent_queries_task.
- FastAPI administrative endpoints under /admin/queue/redis/cache/*.
"""

import json
from unittest.mock import MagicMock
import pytest
from redis.exceptions import ConnectionError, TimeoutError

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
)
from app.services.query_cache_service import (
    QueryCacheManager,
    QueryCacheService,
    query_cache_service,
    AddRedisCacheForFrequentQueries as ServiceAddCache,
    Add_redis_cache_for_frequent_queries as ServiceAdd_cache,
    get_frequent_query_cache_config as service_get_config,
    get_query_cache_strategy as service_get_strategy,
    DEFAULT_FREQUENT_QUERY_CACHE_CONFIG as SERVICE_DEFAULT_CONFIG,
)
from app.tasks import (
    add_redis_cache_for_frequent_queries_task,
    AddRedisCacheForFrequentQueriesTask,
    Add_redis_cache_for_frequent_queries_task,
    DEFAULT_FREQUENT_QUERY_CACHE_CONFIG as TASKS_DEFAULT_CONFIG,
    add_redis_cache_for_frequent_queries as tasks_add_cache,
    get_query_cache_config as tasks_get_config,
)


EXPECTED_CONTRACT = {
    "status": "ENABLED",
    "strategy": "read_through",
    "fallback": "database",
}


# =============================================================================
# Fake Redis Fixture for Query Cache Tests
# =============================================================================

class FakeQueryCacheRedis:
    def __init__(self):
        self.store = {}
        self.ttls = {}

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, time_seconds, value):
        self.store[key] = str(value)
        self.ttls[key] = time_seconds
        return True

    def delete(self, *keys):
        deleted = 0
        for k in keys:
            if k in self.store:
                del self.store[k]
                self.ttls.pop(k, None)
                deleted += 1
        return deleted

    def scan_iter(self, match=None):
        import fnmatch
        for key in list(self.store.keys()):
            if match is None or fnmatch.fnmatch(key, match):
                yield key


# =============================================================================
# 1. Canonical Contract & Configuration Validation
# =============================================================================

def test_canonical_json_contract():
    """Verify add_redis_cache_for_frequent_queries returns exact canonical contract."""
    res = add_redis_cache_for_frequent_queries()
    assert res == EXPECTED_CONTRACT
    assert res["status"] == "ENABLED"
    assert res["strategy"] == "read_through"
    assert res["fallback"] == "database"


def test_default_constant_contract():
    """Verify default constants match across all modules."""
    assert DEFAULT_FREQUENT_QUERY_CACHE_CONFIG == EXPECTED_CONTRACT
    assert SERVICE_DEFAULT_CONFIG == EXPECTED_CONTRACT
    assert TASKS_DEFAULT_CONFIG == EXPECTED_CONTRACT


def test_story_aliases_consistency():
    """Verify all story function aliases resolve to canonical contract."""
    for fn in [
        AddRedisCacheForFrequentQueries,
        Add_redis_cache_for_frequent_queries,
        get_query_cache_strategy,
        ServiceAddCache,
        ServiceAdd_cache,
        service_get_strategy,
        tasks_add_cache,
    ]:
        assert fn() == EXPECTED_CONTRACT


def test_get_query_cache_config_metadata():
    """Verify get_query_cache_config returns configuration and candidate queries."""
    cfg = get_query_cache_config()
    assert cfg["status"] == "ENABLED"
    assert cfg["strategy"] == "read_through"
    assert cfg["fallback"] == "database"
    assert cfg["default_ttl_seconds"] == 300
    assert cfg["serialization"] == "json"
    assert "technician_availability" in cfg["supported_candidates"]
    assert "services_catalog" in cfg["supported_candidates"]
    assert "job_status_summary" in cfg["supported_candidates"]

    # Verify service and tasks config
    assert service_get_config()["status"] == "ENABLED"
    assert tasks_get_config()["status"] == "ENABLED"


# =============================================================================
# 2. Key Builder & Deterministic Parameter Hashing Tests
# =============================================================================

def test_build_query_cache_key_tenant_scoping():
    """Verify build_query_cache_key formats tenant-scoped keys."""
    key = build_query_cache_key("technicians_availability", tenant_id="org-123", status="available")
    assert key.startswith("cache:tenant:org-123:technicians_availability:")

    # Global query
    key_global = build_query_cache_key("services_catalog")
    assert key_global == "cache:services_catalog:default"


def test_build_query_cache_key_parameter_hashing_consistency():
    """Verify parameter hashing is deterministic regardless of dictionary order."""
    key1 = build_query_cache_key("job_summary", tenant_id="org-1", status="PENDING", priority="P1")
    key2 = build_query_cache_key("job_summary", tenant_id="org-1", priority="P1", status="PENDING")
    assert key1 == key2

    # Different parameters produce different keys
    key3 = build_query_cache_key("job_summary", tenant_id="org-1", status="COMPLETED")
    assert key1 != key3


# =============================================================================
# 3. Read-Through Caching Mechanics (Hit vs Miss)
# =============================================================================

def test_read_through_cache_miss_populates_redis():
    """Verify cache miss invokes fetcher_fn and populates Redis with result."""
    fake_redis = FakeQueryCacheRedis()
    fetch_count = 0

    def db_fetcher():
        nonlocal fetch_count
        fetch_count += 1
        return [{"technician_id": 1, "status": "AVAILABLE"}]

    data, cached = get_or_set_cached_query(
        query_name="technician_availability",
        fetcher_fn=db_fetcher,
        tenant_id="tenant-alpha",
        ttl_seconds=120,
        redis_client=fake_redis,
    )

    assert fetch_count == 1
    assert cached is False
    assert len(data) == 1
    assert data[0]["technician_id"] == 1

    # Verify key was written to Redis with correct TTL
    key = build_query_cache_key("technician_availability", tenant_id="tenant-alpha")
    assert key in fake_redis.store
    assert fake_redis.ttls[key] == 120


def test_read_through_cache_hit_bypasses_database():
    """Verify second read returns cached data directly without calling database."""
    fake_redis = FakeQueryCacheRedis()
    fetch_count = 0

    def db_fetcher():
        nonlocal fetch_count
        fetch_count += 1
        return {"catalog": ["PLUMBING", "ELECTRICAL"]}

    # Call 1: Miss
    data1, cached1 = get_or_set_cached_query(
        query_name="services_catalog",
        fetcher_fn=db_fetcher,
        redis_client=fake_redis,
    )
    assert cached1 is False
    assert fetch_count == 1

    # Call 2: Hit
    data2, cached2 = get_or_set_cached_query(
        query_name="services_catalog",
        fetcher_fn=db_fetcher,
        redis_client=fake_redis,
    )
    assert cached2 is True
    assert fetch_count == 1  # Database was NOT called again!
    assert data2 == data1


# =============================================================================
# 4. Fail-Safe Database Fallback (Redis Unavailability)
# =============================================================================

def test_fallback_to_database_on_redis_connection_error():
    """Verify query seamlessly falls back to database when Redis raises ConnectionError."""
    mock_redis = MagicMock()
    mock_redis.get.side_effect = ConnectionError("Redis cluster unreachable")
    fetch_called = False

    def db_fetcher():
        nonlocal fetch_called
        fetch_called = True
        return {"status": "ok", "source": "postgres"}

    data, cached = get_or_set_cached_query(
        query_name="job_status_summary",
        fetcher_fn=db_fetcher,
        tenant_id="tenant-beta",
        redis_client=mock_redis,
    )

    assert fetch_called is True
    assert cached is False
    assert data["source"] == "postgres"


def test_fallback_to_database_on_redis_timeout_error():
    """Verify query seamlessly falls back to database when Redis times out."""
    mock_redis = MagicMock()
    mock_redis.get.side_effect = TimeoutError("Redis socket read timeout")
    fetch_called = False

    def db_fetcher():
        nonlocal fetch_called
        fetch_called = True
        return {"data": 42}

    data, cached = get_or_set_cached_query(
        query_name="customer_display_info",
        fetcher_fn=db_fetcher,
        redis_client=mock_redis,
    )

    assert fetch_called is True
    assert cached is False
    assert data["data"] == 42


def test_fallback_to_database_when_redis_client_none():
    """Verify query executes database fetcher even when redis client is disabled or None."""
    fetch_called = False

    def db_fetcher():
        nonlocal fetch_called
        fetch_called = True
        return {"fallback": True}

    data, cached = get_or_set_cached_query(
        query_name="no_redis_query_disabled",
        fetcher_fn=db_fetcher,
        redis_client=False,
    )

    assert fetch_called is True
    assert cached is False
    assert data["fallback"] is True


# =============================================================================
# 5. Multi-Tenant Isolation Tests
# =============================================================================

def test_tenant_boundary_isolation_prevents_data_leakage():
    """Verify tenant A and tenant B have isolated query caches."""
    fake_redis = FakeQueryCacheRedis()

    def fetcher_a():
        return {"tenant": "tenant-A", "confidential_count": 100}

    def fetcher_b():
        return {"tenant": "tenant-B", "confidential_count": 5}

    # Query tenant A
    data_a, _ = get_or_set_cached_query(
        "job_summary",
        fetcher_fn=fetcher_a,
        tenant_id="tenant-A",
        redis_client=fake_redis,
    )

    # Query tenant B
    data_b, _ = get_or_set_cached_query(
        "job_summary",
        fetcher_fn=fetcher_b,
        tenant_id="tenant-B",
        redis_client=fake_redis,
    )

    assert data_a["tenant"] == "tenant-A"
    assert data_a["confidential_count"] == 100

    assert data_b["tenant"] == "tenant-B"
    assert data_b["confidential_count"] == 5

    # Keys in redis must have distinct tenant prefixes
    keys = list(fake_redis.store.keys())
    assert any("tenant:tenant-A" in k for k in keys)
    assert any("tenant:tenant-B" in k for k in keys)


# =============================================================================
# 6. Cache Invalidation Tests
# =============================================================================

def test_invalidate_query_cache_by_tenant_and_query():
    """Verify invalidate_query_cache removes target cached keys."""
    fake_redis = FakeQueryCacheRedis()

    get_or_set_cached_query("query_1", lambda: "val1", tenant_id="t1", redis_client=fake_redis)
    get_or_set_cached_query("query_2", lambda: "val2", tenant_id="t1", redis_client=fake_redis)
    get_or_set_cached_query("query_1", lambda: "val3", tenant_id="t2", redis_client=fake_redis)

    assert len(fake_redis.store) == 3

    # Invalidate query_1 for t1 only
    deleted = invalidate_query_cache("query_1", tenant_id="t1", redis_client=fake_redis)
    assert deleted == 1
    assert len(fake_redis.store) == 2

    # Invalidate all queries for t1
    deleted_all_t1 = invalidate_query_cache(tenant_id="t1", redis_client=fake_redis)
    assert deleted_all_t1 == 1
    assert len(fake_redis.store) == 1

    # Only t2 remains
    assert any("tenant:t2" in k for k in fake_redis.store.keys())


# =============================================================================
# 7. Service & Manager Classes Tests
# =============================================================================

def test_query_cache_service_class():
    """Verify QueryCacheService coordinates query execution and invalidation."""
    fake_redis = FakeQueryCacheRedis()
    service = QueryCacheService(redis_client=fake_redis)

    assert service.get_status() == EXPECTED_CONTRACT
    assert service.get_config()["status"] == "ENABLED"

    data, cached = service.execute(
        "customer_info",
        fetcher_fn=lambda: {"name": "Alice"},
        tenant_id="t-cust",
        customer_id=101,
    )
    assert cached is False
    assert data["name"] == "Alice"

    # Second execution is cached
    data2, cached2 = service.execute(
        "customer_info",
        fetcher_fn=lambda: {"name": "Bob"},
        tenant_id="t-cust",
        customer_id=101,
    )
    assert cached2 is True
    assert data2["name"] == "Alice"

    # Invalidate
    del_count = service.invalidate(query_name="customer_info", tenant_id="t-cust")
    assert del_count == 1

    # Singleton instance
    assert query_cache_service.get_status() == EXPECTED_CONTRACT


# =============================================================================
# 8. Celery Task Execution Tests
# =============================================================================

def test_add_redis_cache_for_frequent_queries_task():
    """Verify add_redis_cache_for_frequent_queries_task returns canonical contract."""
    res = add_redis_cache_for_frequent_queries_task()
    assert res == EXPECTED_CONTRACT

    # Test aliases
    assert AddRedisCacheForFrequentQueriesTask() == EXPECTED_CONTRACT
    assert Add_redis_cache_for_frequent_queries_task() == EXPECTED_CONTRACT


# =============================================================================
# 9. FastAPI Admin Queue REST Endpoints Tests
# =============================================================================

def test_admin_queue_frequent_query_cache_endpoints():
    """Verify FastAPI admin queue endpoints return expected contract and execute operations."""
    from app.routes.admin_queue import (
        get_query_cache_status_endpoint,
        get_query_cache_strategy_endpoint,
        post_enable_query_cache_endpoint,
        get_query_cache_config_endpoint,
        post_invalidate_query_cache_endpoint,
        post_execute_cached_query_endpoint,
    )

    assert get_query_cache_status_endpoint() == EXPECTED_CONTRACT
    assert get_query_cache_strategy_endpoint() == EXPECTED_CONTRACT
    assert post_enable_query_cache_endpoint() == EXPECTED_CONTRACT

    cfg = get_query_cache_config_endpoint()
    assert cfg["status"] == "ENABLED"
    assert cfg["strategy"] == "read_through"
    assert cfg["fallback"] == "database"

    # Execute query test endpoint
    query_res = post_execute_cached_query_endpoint({
        "query_name": "technician_availability",
        "tenant_id": "tenant-admin",
        "params": {"region": "north"},
    })
    assert "data" in query_res
    assert "source" in query_res
    assert query_res["tenant_id"] == "tenant-admin"

    # Invalidation test endpoint
    inv_res = post_invalidate_query_cache_endpoint({
        "query_name": "technician_availability",
        "tenant_id": "tenant-admin",
    })
    assert inv_res["status"] == "INVALIDATED"
    assert inv_res["query_name"] == "technician_availability"
