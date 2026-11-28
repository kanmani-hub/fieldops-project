"""
test_cache_invalidation.py

Comprehensive test suite for Story: Add Redis cache invalidation (Redis Enhancement capability).
Validates:
- Canonical JSON output contract:
    {
        "status": "ENABLED",
        "strategy": "write_invalidate"
    }
- Authority write invalidation for jobs, technicians, catalog, and customers.
- Strict multi-tenant isolation: writing in tenant A never purges tenant B caches.
- Cache hit -> Write/Invalidate -> Cache miss -> Fresh PostgreSQL data cycle.
- Entity-specific helper functions (invalidate_job_cache, invalidate_technician_cache, etc.).
- Bounded error handling when Redis fails during invalidation.
- Integration with CacheInvalidationService and CacheInvalidationManager.
- Celery task add_redis_cache_invalidation_task.
- FastAPI administrative endpoints under /admin/queue/redis/cache/invalidation/*.
"""

from unittest.mock import MagicMock
import pytest
from redis.exceptions import ConnectionError

from app.redis_client import (
    DEFAULT_CACHE_INVALIDATION_CONFIG,
    ENTITY_CACHE_MAP,
    get_or_set_cached_query,
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
)
from app.services.cache_invalidation_service import (
    CacheInvalidationManager,
    CacheInvalidationService,
    cache_invalidation_service,
    AddRedisCacheInvalidation as ServiceAddInvalidation,
    Add_redis_cache_invalidation as ServiceAdd_invalidation,
    get_cache_invalidation_status as service_get_status,
    get_invalidation_strategy as service_get_strategy,
    DEFAULT_CACHE_INVALIDATION_CONFIG as SERVICE_DEFAULT_CONFIG,
)
from app.tasks import (
    add_redis_cache_invalidation_task,
    AddRedisCacheInvalidationTask,
    Add_redis_cache_invalidation_task,
    DEFAULT_CACHE_INVALIDATION_CONFIG as TASKS_DEFAULT_CONFIG,
    add_redis_cache_invalidation as tasks_add_invalidation,
    get_cache_invalidation_config as tasks_get_config,
)


EXPECTED_CONTRACT = {
    "status": "ENABLED",
    "strategy": "write_invalidate",
}


# =============================================================================
# Fake Redis Fixture for Invalidation Tests
# =============================================================================

class FakeInvalidationRedis:
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
    """Verify add_redis_cache_invalidation returns exact canonical contract."""
    res = add_redis_cache_invalidation()
    assert res == EXPECTED_CONTRACT
    assert res["status"] == "ENABLED"
    assert res["strategy"] == "write_invalidate"


def test_default_constant_contract():
    """Verify default constants match across all modules."""
    assert DEFAULT_CACHE_INVALIDATION_CONFIG == EXPECTED_CONTRACT
    assert SERVICE_DEFAULT_CONFIG == EXPECTED_CONTRACT
    assert TASKS_DEFAULT_CONFIG == EXPECTED_CONTRACT


def test_story_aliases_consistency():
    """Verify all story function aliases resolve to canonical contract."""
    for fn in [
        AddRedisCacheInvalidation,
        Add_redis_cache_invalidation,
        get_cache_invalidation_status,
        get_invalidation_strategy,
        ServiceAddInvalidation,
        ServiceAdd_invalidation,
        service_get_status,
        service_get_strategy,
        tasks_add_invalidation,
    ]:
        assert fn() == EXPECTED_CONTRACT


def test_get_cache_invalidation_config_metadata():
    """Verify get_cache_invalidation_config returns mappings and entities."""
    cfg = get_cache_invalidation_config()
    assert cfg["status"] == "ENABLED"
    assert cfg["strategy"] == "write_invalidate"
    assert "job" in cfg["supported_entities"]
    assert "technician" in cfg["supported_entities"]
    assert "catalog" in cfg["supported_entities"]
    assert "customer" in cfg["supported_entities"]

    assert "job_status_summary" in cfg["entity_mappings"]["job"]
    assert "technician_availability" in cfg["entity_mappings"]["technician"]
    assert tasks_get_config()["status"] == "ENABLED"


# =============================================================================
# 2. Entity Write Invalidation Mechanics
# =============================================================================

def test_invalidate_job_cache_purges_affected_queries():
    """Verify writing/updating a job invalidates job summaries and technician availability."""
    fake_redis = FakeInvalidationRedis()

    # Seed cache
    get_or_set_cached_query("job_status_summary", lambda: {"pending": 5}, tenant_id="t1", redis_client=fake_redis)
    get_or_set_cached_query("technician_availability", lambda: {"active": 2}, tenant_id="t1", redis_client=fake_redis)
    get_or_set_cached_query("services_catalog", lambda: {"cats": []}, tenant_id="t1", redis_client=fake_redis)

    assert len(fake_redis.store) == 3

    # Invalidate on job write
    res = invalidate_job_cache(tenant_id="t1", job_id="job-101", redis_client=fake_redis)
    assert res["status"] == "INVALIDATED"
    assert res["entity_type"] == "job"
    assert res["entity_id"] == "job-101"
    assert res["tenant_id"] == "t1"
    assert res["invalidated_count"] == 2

    # Verify job and technician keys purged, but catalog remained
    assert any("services_catalog" in k for k in fake_redis.store.keys())


def test_invalidate_technician_cache_purges_availability():
    """Verify technician state changes invalidate availability and profile caches."""
    fake_redis = FakeInvalidationRedis()

    get_or_set_cached_query("technician_availability", lambda: "online", tenant_id="t2", redis_client=fake_redis)
    get_or_set_cached_query("technician_profile", lambda: "details", tenant_id="t2", redis_client=fake_redis)

    assert len(fake_redis.store) == 2

    res = invalidate_technician_cache(tenant_id="t2", technician_id="tech-7", redis_client=fake_redis)
    assert res["status"] == "INVALIDATED"
    assert res["entity_type"] == "technician"
    assert res["invalidated_count"] == 2
    assert len(fake_redis.store) == 0


def test_invalidate_catalog_cache():
    """Verify catalog mutations invalidate taxonomy and catalog caches."""
    fake_redis = FakeInvalidationRedis()

    get_or_set_cached_query("services_catalog", lambda: "cat", tenant_id="t3", redis_client=fake_redis)
    get_or_set_cached_query("services_taxonomy", lambda: "tax", tenant_id="t3", redis_client=fake_redis)

    res = invalidate_catalog_cache(tenant_id="t3", service_id="svc-1", redis_client=fake_redis)
    assert res["status"] == "INVALIDATED"
    assert res["invalidated_count"] == 2


def test_invalidate_customer_cache():
    """Verify customer update invalidates customer display cache."""
    fake_redis = FakeInvalidationRedis()

    get_or_set_cached_query("customer_display_info", lambda: "cust", tenant_id="t4", redis_client=fake_redis)

    res = invalidate_customer_cache(tenant_id="t4", customer_id="cust-10", redis_client=fake_redis)
    assert res["status"] == "INVALIDATED"
    assert res["invalidated_count"] == 1


# =============================================================================
# 3. Strict Multi-Tenant Isolation (No Cross-Tenant Eviction)
# =============================================================================

def test_tenant_isolation_prevents_cross_tenant_cache_eviction():
    """Verify invalidating a job in tenant A does NOT purge tenant B's cached data."""
    fake_redis = FakeInvalidationRedis()

    # Populate tenant A and tenant B
    get_or_set_cached_query("job_status_summary", lambda: "data-A", tenant_id="tenant-A", redis_client=fake_redis)
    get_or_set_cached_query("job_status_summary", lambda: "data-B", tenant_id="tenant-B", redis_client=fake_redis)

    assert len(fake_redis.store) == 2

    # Invalidate tenant A
    res = invalidate_job_cache(tenant_id="tenant-A", job_id="job-99", redis_client=fake_redis)
    assert res["invalidated_count"] == 1

    # Verify tenant A is gone but tenant B remains intact
    keys = list(fake_redis.store.keys())
    assert not any("tenant:tenant-A" in k for k in keys)
    assert any("tenant:tenant-B" in k for k in keys)


# =============================================================================
# 4. Freshness Cycle: Hit -> Write/Invalidate -> Miss -> Fresh Data
# =============================================================================

def test_invalidation_freshness_lifecycle():
    """
    Demonstrate authoritative data lifecycle:
    1. Read 1: Miss -> fetches v1 from DB.
    2. Read 2: Hit -> returns v1 from Redis.
    3. Update occurs in DB (v2) and invalidation is triggered.
    4. Read 3: Miss -> fetches v2 from DB and populates Redis.
    5. Read 4: Hit -> returns v2 from Redis.
    """
    fake_redis = FakeInvalidationRedis()
    authoritative_job_state = {"status": "SCHEDULED", "version": 1}

    def fetch_job_from_postgres():
        return dict(authoritative_job_state)

    # Step 1: Initial query (Cache Miss)
    data1, cached1 = get_or_set_cached_query(
        "job_status_summary",
        fetcher_fn=fetch_job_from_postgres,
        tenant_id="tenant-cycle",
        redis_client=fake_redis,
    )
    assert cached1 is False
    assert data1["status"] == "SCHEDULED"
    assert data1["version"] == 1

    # Step 2: Immediate follow-up query (Cache Hit)
    data2, cached2 = get_or_set_cached_query(
        "job_status_summary",
        fetcher_fn=fetch_job_from_postgres,
        tenant_id="tenant-cycle",
        redis_client=fake_redis,
    )
    assert cached2 is True
    assert data2["status"] == "SCHEDULED"

    # Step 3: Authoritative write in PostgreSQL (Job transitions to IN_PROGRESS)
    authoritative_job_state["status"] = "IN_PROGRESS"
    authoritative_job_state["version"] = 2
    invalidate_job_cache(tenant_id="tenant-cycle", job_id="job-42", redis_client=fake_redis)

    # Step 4: Next read after invalidation (Cache Miss -> fetches updated PostgreSQL data)
    data3, cached3 = get_or_set_cached_query(
        "job_status_summary",
        fetcher_fn=fetch_job_from_postgres,
        tenant_id="tenant-cycle",
        redis_client=fake_redis,
    )
    assert cached3 is False
    assert data3["status"] == "IN_PROGRESS"
    assert data3["version"] == 2

    # Step 5: Subsequent read (Cache Hit with fresh data)
    data4, cached4 = get_or_set_cached_query(
        "job_status_summary",
        fetcher_fn=fetch_job_from_postgres,
        tenant_id="tenant-cycle",
        redis_client=fake_redis,
    )
    assert cached4 is True
    assert data4["status"] == "IN_PROGRESS"
    assert data4["version"] == 2


# =============================================================================
# 5. Bounded Error Handling
# =============================================================================

def test_invalidation_bounds_redis_exceptions():
    """Verify invalidation handles Redis connection errors safely without crashing."""
    mock_redis = MagicMock()
    mock_redis.scan_iter.side_effect = ConnectionError("Redis down")

    res = invalidate_cache_on_write(
        entity_type="job",
        tenant_id="tenant-err",
        redis_client=mock_redis,
    )
    assert res["status"] == "INVALIDATED"
    assert res["invalidated_count"] == 0


def test_invalidation_when_redis_client_none():
    """Verify invalidation gracefully skips when redis_client is None/unavailable."""
    mock_redis = None
    res = invalidate_cache_on_write(
        entity_type="job",
        tenant_id="tenant-none",
        redis_client=mock_redis,
    )
    # Falls back to get_redis_client or returns skipped
    assert res["status"] in {"INVALIDATED", "SKIPPED"}


# =============================================================================
# 6. Service & Manager Classes Tests
# =============================================================================

def test_cache_invalidation_service_class():
    """Verify CacheInvalidationService coordinates domain invalidation methods."""
    fake_redis = FakeInvalidationRedis()
    service = CacheInvalidationService(redis_client=fake_redis)

    assert service.get_status() == EXPECTED_CONTRACT
    assert service.get_config()["status"] == "ENABLED"

    # Seed
    get_or_set_cached_query("job_status_summary", lambda: "data", tenant_id="t-svc", redis_client=fake_redis)
    assert len(fake_redis.store) == 1

    # Invalidate job
    res = service.invalidate_job(tenant_id="t-svc", job_id="j-1")
    assert res["invalidated_count"] == 1
    assert len(fake_redis.store) == 0

    # Singleton instance
    assert cache_invalidation_service.get_status() == EXPECTED_CONTRACT


# =============================================================================
# 7. Celery Task Execution Tests
# =============================================================================

def test_add_redis_cache_invalidation_task():
    """Verify add_redis_cache_invalidation_task returns canonical contract."""
    res = add_redis_cache_invalidation_task()
    assert res == EXPECTED_CONTRACT

    # Test aliases
    assert AddRedisCacheInvalidationTask() == EXPECTED_CONTRACT
    assert Add_redis_cache_invalidation_task() == EXPECTED_CONTRACT


# =============================================================================
# 8. FastAPI Admin Queue REST Endpoints Tests
# =============================================================================

def test_admin_queue_cache_invalidation_endpoints():
    """Verify FastAPI admin queue endpoints return expected contract and execute operations."""
    from app.routes.admin_queue import (
        get_cache_invalidation_status_endpoint,
        get_cache_invalidation_strategy_endpoint,
        post_enable_cache_invalidation_endpoint,
        get_cache_invalidation_config_endpoint,
        post_trigger_cache_invalidation_endpoint,
    )

    assert get_cache_invalidation_status_endpoint() == EXPECTED_CONTRACT
    assert get_cache_invalidation_strategy_endpoint() == EXPECTED_CONTRACT
    assert post_enable_cache_invalidation_endpoint() == EXPECTED_CONTRACT

    cfg = get_cache_invalidation_config_endpoint()
    assert cfg["status"] == "ENABLED"
    assert cfg["strategy"] == "write_invalidate"

    # Trigger invalidation endpoint
    trig_res = post_trigger_cache_invalidation_endpoint({
        "entity_type": "job",
        "entity_id": "job-admin-1",
        "tenant_id": "tenant-admin",
        "correlation_id": "corr-admin",
    })
    assert trig_res["status"] in {"INVALIDATED", "SKIPPED"}
    assert trig_res["entity_type"] == "job"
