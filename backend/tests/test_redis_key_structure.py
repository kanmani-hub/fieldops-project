"""
test_redis_key_structure.py

Comprehensive test suite for Story: Review Redis key structure (Redis Enhancement capability).
Validates:
- Canonical JSON output contract:
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
- Authoritative inventory across all 5 namespaces with owners, tenant scoping, data types, and TTL policies.
- Collision-prevention key builders (build_redis_key, build_tenant_key) and key parser (parse_redis_key).
- Identification of legacy keys needing migration (job:timer:*, gps_purge_status:*, job:lock:*, task_result:*).
- RedisKeyRegistry and RedisKeyStructureService domain service wrappers.
- Celery task review_redis_key_structure_task.
- FastAPI administrative endpoints under /admin/queue/redis/*.
"""

from unittest.mock import MagicMock
import pytest

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
)
from app.services.redis_key_structure import (
    RedisKeyRegistry,
    RedisKeyStructureService,
    redis_key_structure_service,
    review_redis_key_structure as service_review,
    get_redis_key_inventory as service_inventory,
    DEFAULT_REDIS_KEY_REVIEW as SERVICE_DEFAULT_REVIEW,
    REDIS_KEY_NAMESPACES as SERVICE_NAMESPACES,
)
from app.tasks import (
    review_redis_key_structure_task,
    ReviewRedisKeyStructureTask,
    Review_redis_key_structure_task,
    DEFAULT_REDIS_KEY_REVIEW as TASKS_DEFAULT_REVIEW,
    REDIS_KEY_NAMESPACES as TASKS_NAMESPACES,
    review_redis_key_structure as tasks_review,
    get_redis_key_inventory as tasks_inventory,
)


EXPECTED_CONTRACT = {
    "status": "REVIEWED",
    "key_namespaces": [
        "dispatch",
        "gps_purge",
        "cache",
        "locks",
        "celery",
    ],
}


# =============================================================================
# 1. Canonical Contract & Namespace Validation
# =============================================================================

def test_canonical_json_contract():
    """Verify review_redis_key_structure returns exact canonical contract."""
    result = review_redis_key_structure()
    assert result == EXPECTED_CONTRACT
    assert result["status"] == "REVIEWED"
    assert result["key_namespaces"] == [
        "dispatch",
        "gps_purge",
        "cache",
        "locks",
        "celery",
    ]


def test_default_constant_contract():
    """Verify DEFAULT_REDIS_KEY_REVIEW constant matches the expected contract."""
    assert DEFAULT_REDIS_KEY_REVIEW == EXPECTED_CONTRACT
    assert SERVICE_DEFAULT_REVIEW == EXPECTED_CONTRACT
    assert TASKS_DEFAULT_REVIEW == EXPECTED_CONTRACT


def test_namespace_list_ordering():
    """Verify REDIS_KEY_NAMESPACES contains all 5 required namespaces."""
    assert REDIS_KEY_NAMESPACES == ["dispatch", "gps_purge", "cache", "locks", "celery"]
    assert SERVICE_NAMESPACES == REDIS_KEY_NAMESPACES
    assert TASKS_NAMESPACES == REDIS_KEY_NAMESPACES


def test_story_aliases_consistency():
    """Verify all story function aliases resolve to canonical review implementation."""
    for fn in [
        ReviewRedisKeyStructure,
        Review_redis_key_structure,
        get_redis_key_structure_review,
        get_redis_namespaces,
        service_review,
        tasks_review,
    ]:
        res = fn()
        assert res == EXPECTED_CONTRACT


# =============================================================================
# 2. Redis Key Builders & Parser Tests
# =============================================================================

def test_build_redis_key_global():
    """Verify build_redis_key constructs global non-tenant-scoped keys."""
    key = build_redis_key("dispatch", "timer", "job-101")
    assert key == "dispatch:timer:job-101"

    key_locks = build_redis_key("locks", "job", "job-202")
    assert key_locks == "locks:job:job-202"

    key_gps = build_redis_key("gps_purge", "total_purged_30d")
    assert key_gps == "gps_purge:total_purged_30d"


def test_build_redis_key_tenant_scoped():
    """Verify build_redis_key constructs tenant-isolated keys."""
    key = build_redis_key("dispatch", "heartbeat", "tech-42", tenant_id="tenant-alpha")
    assert key == "dispatch:tenant:tenant-alpha:heartbeat:tech-42"

    key_cache = build_redis_key("cache", "user_profile", "user-99", tenant_id="tenant-beta")
    assert key_cache == "cache:tenant:tenant-beta:user_profile:user-99"


def test_build_tenant_key_helper():
    """Verify build_tenant_key convenience helper produces expected tenant format."""
    key = build_tenant_key("locks", "tenant-omega", "resource-lock")
    assert key == "locks:tenant:tenant-omega:resource-lock"


def test_build_redis_key_strips_colons():
    """Verify build_redis_key safely strips accidental leading/trailing colons."""
    key = build_redis_key(":dispatch:", ":timer:", ":job-303:", tenant_id=":tenant-77:")
    assert key == "dispatch:tenant:tenant-77:timer:job-303"


def test_parse_redis_key():
    """Verify parse_redis_key decomposes keys into namespace, tenant, and sub-parts."""
    # Tenant scoped
    parsed = parse_redis_key("dispatch:tenant:tenant-123:heartbeat:tech-99")
    assert parsed["namespace"] == "dispatch"
    assert parsed["tenant_id"] == "tenant-123"
    assert parsed["parts"] == ["heartbeat", "tech-99"]
    assert parsed["raw_key"] == "dispatch:tenant:tenant-123:heartbeat:tech-99"

    # Global scoped
    parsed_global = parse_redis_key("celery:result:task-xyz")
    assert parsed_global["namespace"] == "celery"
    assert parsed_global["tenant_id"] is None
    assert parsed_global["parts"] == ["result", "task-xyz"]

    # Unknown namespace
    parsed_unknown = parse_redis_key("unregistered:subpart:123")
    assert parsed_unknown["namespace"] == "unknown"
    assert parsed_unknown["tenant_id"] is None

    # Empty or invalid key
    assert parse_redis_key("")["namespace"] == "unknown"
    assert parse_redis_key(None)["namespace"] == "unknown"


# =============================================================================
# 3. Redis Key Inventory & Metadata Documentation
# =============================================================================

def test_redis_key_inventory_structure():
    """Verify get_redis_key_inventory returns complete metadata for all 5 namespaces."""
    inventory_res = get_redis_key_inventory()
    assert inventory_res["status"] == "REVIEWED"
    assert inventory_res["key_namespaces"] == REDIS_KEY_NAMESPACES

    inv = inventory_res["inventory"]
    assert len(inv) == 5

    for ns in REDIS_KEY_NAMESPACES:
        assert ns in inv
        item = inv[ns]
        assert item["namespace"] == ns
        assert "owner" in item and len(item["owner"]) > 0
        assert isinstance(item["tenant_scoped"], bool)
        assert isinstance(item["patterns"], list) and len(item["patterns"]) > 0
        assert isinstance(item["data_types"], list) and len(item["data_types"]) > 0
        assert isinstance(item["ttl_policies"], dict) and len(item["ttl_policies"]) > 0
        assert "migration_notes" in item


def test_dispatch_inventory_includes_pubsub_and_ws():
    """Verify dispatch namespace metadata documents dispatch_events and ws-server ownership."""
    inv = REDIS_KEY_INVENTORY["dispatch"]
    assert "WebSocket" in inv["owner"]
    assert "dispatch_events" in inv["patterns"]
    assert "pubsub" in inv["data_types"]
    assert inv["tenant_scoped"] is True


def test_gps_purge_inventory_includes_statistics_and_dlq():
    """Verify gps_purge namespace metadata documents 30d rolling stats and DLQ."""
    inv = REDIS_KEY_INVENTORY["gps_purge"]
    assert "GPS Purge" in inv["owner"]
    assert "gps_purge:total_purged_30d" in inv["patterns"]
    assert "gps_purge:dlq" in inv["patterns"]
    assert "statistics" in inv["ttl_policies"]


def test_celery_inventory_includes_results_and_telemetry():
    """Verify celery namespace metadata documents task results and queue monitoring."""
    inv = REDIS_KEY_INVENTORY["celery"]
    assert "Celery" in inv["owner"]
    assert "task_result:{task_id}" in inv["patterns"]
    assert "celery:result:{task_id}" in inv["patterns"]
    assert "notifications" in inv["patterns"]


# =============================================================================
# 4. Migration Identification Tests
# =============================================================================

def test_identify_keys_for_migration_with_legacy_list():
    """Verify identify_keys_for_migration flags legacy keys and suggests standard targets."""
    sample_keys = [
        "job:timer:job-1",
        "job:timer_warning:job-2",
        "gps_purge_status:job-3",
        "gps_purge_start_time:job-3",
        "gps_purge_dlq",
        "job:lock:job-4",
        "lock:job_accept:job-5",
        "task_result:task-6",
        "celery_monitoring:failures:notifications",
        # Already standardized key
        "dispatch:timer:job-7",
        "locks:job:job-8",
    ]

    res = identify_keys_for_migration(keys_to_check=sample_keys)
    assert res["status"] == "REVIEWED"
    assert res["total_evaluated"] == len(sample_keys)
    assert res["migration_count"] == 9

    candidates = {c["legacy_key"]: c for c in res["migration_candidates"]}

    assert candidates["job:timer:job-1"]["target_key"] == "dispatch:timer:job-1"
    assert candidates["job:timer:job-1"]["namespace"] == "dispatch"

    assert candidates["gps_purge_status:job-3"]["target_key"] == "gps_purge:status:job-3"
    assert candidates["gps_purge_status:job-3"]["namespace"] == "gps_purge"

    assert candidates["gps_purge_dlq"]["target_key"] == "gps_purge:dlq"

    assert candidates["job:lock:job-4"]["target_key"] == "locks:job:job-4"
    assert candidates["job:lock:job-4"]["namespace"] == "locks"

    assert candidates["task_result:task-6"]["target_key"] == "celery:result:task-6"
    assert candidates["task_result:task-6"]["namespace"] == "celery"

    # Standardized keys should not be in migration list
    assert "dispatch:timer:job-7" not in candidates
    assert "locks:job:job-8" not in candidates


def test_identify_keys_for_migration_with_mock_redis():
    """Verify identify_keys_for_migration can scan Redis keys using a client mock."""
    mock_redis = MagicMock()
    mock_redis.scan.side_effect = [
        (10, ["job:timer:j1", "dispatch:standard:1"]),
        (0, ["gps_purge_status:j2", "random:unrelated"]),
    ]

    res = identify_keys_for_migration(redis_client=mock_redis)
    assert res["status"] == "REVIEWED"
    assert res["total_evaluated"] == 4
    assert res["migration_count"] == 2


# =============================================================================
# 5. Service & Registry Classes Tests
# =============================================================================

def test_redis_key_registry_methods():
    """Verify RedisKeyRegistry coordinates namespaces and key formatting."""
    registry = RedisKeyRegistry()
    assert registry.get_review() == EXPECTED_CONTRACT
    assert len(registry.get_inventory()["inventory"]) == 5

    details = registry.get_namespace_details("dispatch")
    assert details is not None
    assert details["namespace"] == "dispatch"

    key = registry.build_key("cache", "session", "s-1", tenant_id="t-1")
    assert key == "cache:tenant:t-1:session:s-1"

    parsed = registry.parse_key(key)
    assert parsed["namespace"] == "cache"
    assert parsed["tenant_id"] == "t-1"

    with pytest.raises(ValueError):
        registry.build_key("unsupported_ns", "key-1")


def test_redis_key_structure_service():
    """Verify RedisKeyStructureService operational methods."""
    service = RedisKeyStructureService(redis_client=MagicMock())
    assert service.review() == EXPECTED_CONTRACT
    assert service.inventory()["status"] == "REVIEWED"

    fmt = service.format_key("locks", "job", "job-100")
    assert fmt == "locks:job:job-100"

    mig = service.identify_migrations(keys=["job:timer:test"])
    assert mig["migration_count"] == 1


# =============================================================================
# 6. Celery Task Execution Tests
# =============================================================================

def test_review_redis_key_structure_task():
    """Verify review_redis_key_structure_task Celery task returns canonical review contract."""
    res = review_redis_key_structure_task()
    assert res == EXPECTED_CONTRACT

    # Test aliases
    assert ReviewRedisKeyStructureTask() == EXPECTED_CONTRACT
    assert Review_redis_key_structure_task() == EXPECTED_CONTRACT


# =============================================================================
# 7. FastAPI Admin Queue REST Endpoints Tests
# =============================================================================

def test_admin_queue_redis_endpoints():
    """Verify FastAPI admin queue endpoints return expected Redis key review contract."""
    from app.routes.admin_queue import (
        get_redis_key_structure_endpoint,
        get_redis_namespaces_endpoint,
        get_redis_inventory_endpoint,
        post_redis_key_structure_review_endpoint,
        post_redis_identify_migrations_endpoint,
    )

    assert get_redis_key_structure_endpoint() == EXPECTED_CONTRACT
    assert get_redis_namespaces_endpoint() == EXPECTED_CONTRACT
    assert post_redis_key_structure_review_endpoint() == EXPECTED_CONTRACT

    inv_res = get_redis_inventory_endpoint()
    assert inv_res["status"] == "REVIEWED"
    assert len(inv_res["inventory"]) == 5

    mig_res = post_redis_identify_migrations_endpoint(
        payload={"keys": ["job:timer:job-99", "task_result:t-88"]}
    )
    assert mig_res["status"] == "REVIEWED"
    assert mig_res["migration_count"] == 2
