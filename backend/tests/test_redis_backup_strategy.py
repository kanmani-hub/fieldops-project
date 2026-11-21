"""
test_redis_backup_strategy.py

Comprehensive test suite for Story: Add Redis backup strategy (Redis Enhancement capability).
Validates:
- Canonical JSON output contract:
    {
        "status": "DOCUMENTED",
        "backup_strategy": "RDB_OR_AOF_AS_REQUIRED",
        "source_of_truth": "POSTGRESQL"
    }
- Redis data classification: Durable-needed vs Reconstructable.
- Restore safety verification: Restoring Redis NEVER overwrites newer PostgreSQL state.
- Celery task add_redis_backup_strategy_task and story aliases.
- FastAPI administrative endpoints under /admin/queue/redis/backup/*.
- Docker compose Redis service persistence and volume configuration.
"""

from pathlib import Path
from typing import Any
import pytest
import yaml

from app.redis_client import (
    DEFAULT_REDIS_BACKUP_CONFIG,
    add_redis_backup_strategy,
    get_redis_backup_config,
    classify_redis_key,
    validate_redis_restore_safety,
    AddRedisBackupStrategy,
    Add_redis_backup_strategy,
    get_redis_backup_strategy,
    get_redis_backup_status,
)
from app.celery_app import (
    DEFAULT_REDIS_BACKUP_CONFIG as CELERY_DEFAULT_CONFIG,
    add_redis_backup_strategy as celery_add_backup_strategy,
    AddRedisBackupStrategy as CeleryAddBackupStrategy,
    get_redis_backup_strategy as celery_get_strategy,
    get_redis_backup_status as celery_get_status,
)
from app.tasks import (
    add_redis_backup_strategy_task,
    AddRedisBackupStrategyTask,
    Add_redis_backup_strategy_task,
    DEFAULT_REDIS_BACKUP_CONFIG as TASKS_DEFAULT_CONFIG,
    add_redis_backup_strategy as tasks_add_backup_strategy,
)


EXPECTED_CONTRACT = {
    "status": "DOCUMENTED",
    "backup_strategy": "RDB_OR_AOF_AS_REQUIRED",
    "source_of_truth": "POSTGRESQL",
}


# =============================================================================
# 1. Canonical Contract & Aliases Consistency
# =============================================================================

def test_canonical_json_contract():
    """Verify add_redis_backup_strategy returns exact canonical contract."""
    res = add_redis_backup_strategy()
    assert res == EXPECTED_CONTRACT
    assert res["status"] == "DOCUMENTED"
    assert res["backup_strategy"] == "RDB_OR_AOF_AS_REQUIRED"
    assert res["source_of_truth"] == "POSTGRESQL"


def test_default_constant_contract():
    """Verify default constants match across all modules."""
    assert DEFAULT_REDIS_BACKUP_CONFIG == EXPECTED_CONTRACT
    assert CELERY_DEFAULT_CONFIG == EXPECTED_CONTRACT
    assert TASKS_DEFAULT_CONFIG == EXPECTED_CONTRACT


def test_story_aliases_consistency():
    """Verify all story function aliases resolve to canonical contract."""
    for fn in [
        AddRedisBackupStrategy,
        Add_redis_backup_strategy,
        get_redis_backup_strategy,
        get_redis_backup_status,
        CeleryAddBackupStrategy,
        celery_add_backup_strategy,
        celery_get_strategy,
        celery_get_status,
        tasks_add_backup_strategy,
    ]:
        assert fn() == EXPECTED_CONTRACT


def test_get_redis_backup_config_metadata():
    """Verify get_redis_backup_config returns complete operational and persistence details."""
    cfg = get_redis_backup_config()
    assert cfg["status"] == "DOCUMENTED"
    assert cfg["backup_strategy"] == "RDB_OR_AOF_AS_REQUIRED"
    assert cfg["source_of_truth"] == "POSTGRESQL"

    # Persistence modes
    assert cfg["persistence_modes"]["rdb"]["enabled"] is True
    assert len(cfg["persistence_modes"]["rdb"]["snapshot_rules"]) >= 3
    assert cfg["persistence_modes"]["aof"]["enabled"] is True
    assert cfg["persistence_modes"]["aof"]["appendfsync"] == "everysec"

    # Retention & Restore guidelines
    assert cfg["retention_policy"]["rdb_snapshot_retention_days"] == 14
    assert cfg["restore_guidelines"]["source_of_truth"] == "POSTGRESQL"
    assert cfg["restore_guidelines"]["precedence_rule"] == "POSTGRESQL_PRECEDENCE"


# =============================================================================
# 2. Redis Data Classification Tests (Durable-Needed vs Reconstructable)
# =============================================================================

@pytest.mark.parametrize(
    "durable_key",
    [
        "notifications",
        "billing",
        "gps_processing",
        "ai_processing",
        "high_priority",
        "reports",
        "dlq",
        "gps_purge_dlq",
        "task_queue:default",
        "task_queue:notifications:123",
        "celery:result:task-uuid-abc",
        "task_result:xyz-789",
        "celery:monitoring:latency",
        "audit:events:batch_1",
    ],
)
def test_classify_redis_key_durable_needed(durable_key: str):
    """Verify in-flight queues, task results, and DLQs are classified as DURABLE_NEEDED."""
    res = classify_redis_key(durable_key)
    assert res["classification"] == "DURABLE_NEEDED"
    assert res["is_durable"] is True
    assert res["persistence_required"] is True
    assert res["source_of_truth"] == "POSTGRESQL"
    assert res["reconstruction_source"] is None


@pytest.mark.parametrize(
    "reconstructable_key",
    [
        "cache:job:123",
        "cache:technician:availability:456",
        "cache:catalog:services",
        "cache:customer:profile:789",
        "cache:query:tenant-1:sha256abc",
        "query_cache:tenant-9:all",
        "locks:job:123",
        "lock:idempotency:order-99",
        "rate_limit:tenant-1:endpoint",
        "mfa:challenge:session-xyz",
        "sso:state:token-abc",
    ],
)
def test_classify_redis_key_reconstructable(reconstructable_key: str):
    """Verify caches, temporary locks, and rate limits are classified as RECONSTRUCTABLE."""
    res = classify_redis_key(reconstructable_key)
    assert res["classification"] == "RECONSTRUCTABLE"
    assert res["is_durable"] is False
    assert res["persistence_required"] is False
    assert res["source_of_truth"] == "POSTGRESQL"
    assert res["reconstruction_source"] == "POSTGRESQL"


def test_classify_redis_key_invalid_inputs():
    """Verify classify_redis_key gracefully handles empty or non-string keys."""
    for invalid in [None, "", 123, []]:
        res = classify_redis_key(invalid)
        assert res["classification"] == "RECONSTRUCTABLE"
        assert res["is_durable"] is False
        assert res["persistence_required"] is False


# =============================================================================
# 3. Restore Safety & Strict PostgreSQL Anti-Overwrite Protection Tests
# =============================================================================

def test_validate_restore_superseded_by_newer_postgres_timestamp():
    """Verify Redis restore is discarded when PostgreSQL updated_at timestamp is newer."""
    redis_rec = {
        "entity_id": "job-101",
        "timestamp": "2026-11-20T10:00:00Z",
        "status": "IN_PROGRESS",
    }
    postgres_rec = {
        "id": "job-101",
        "updated_at": "2026-11-20T11:30:00Z",  # Newer in PostgreSQL!
        "status": "COMPLETED",
    }

    res = validate_redis_restore_safety(redis_rec, postgres_rec)
    assert res["safe"] is False
    assert res["action"] == "DISCARD"
    assert res["superseded_by_postgresql"] is True
    assert res["source_of_truth"] == "POSTGRESQL"


def test_validate_restore_superseded_by_higher_postgres_version():
    """Verify Redis restore is discarded when PostgreSQL entity version is higher or equal."""
    redis_rec = {
        "entity_id": "job-102",
        "version": 2,
        "mutation": "assign_technician",
    }
    postgres_rec = {
        "id": "job-102",
        "version": 3,  # Higher version in PostgreSQL!
        "mutation": "job_started",
    }

    res = validate_redis_restore_safety(redis_rec, postgres_rec)
    assert res["safe"] is False
    assert res["action"] == "DISCARD"
    assert res["superseded_by_postgresql"] is True


def test_validate_restore_discarded_for_terminal_postgres_status():
    """Verify Redis mutations are discarded if entity in PostgreSQL is already in terminal state."""
    for terminal in ["COMPLETED", "CANCELLED", "PAID", "CLOSED"]:
        redis_rec = {
            "entity_id": "job-103",
            "action": "reassign",
        }
        postgres_rec = {
            "id": "job-103",
            "status": terminal,
        }

        res = validate_redis_restore_safety(redis_rec, postgres_rec)
        assert res["safe"] is False
        assert res["action"] == "DISCARD"
        assert res["superseded_by_postgresql"] is True


def test_validate_restore_safe_when_no_postgres_conflict():
    """Verify Redis record is safe to process when PostgreSQL is older or not yet created."""
    # Case 1: No postgres record exists yet
    redis_rec = {"entity_id": "job-new-1", "action": "create_job"}
    res1 = validate_redis_restore_safety(redis_rec, postgres_record=None)
    assert res1["safe"] is True
    assert res1["action"] == "PROCESS"

    # Case 2: PostgreSQL is older and non-terminal
    redis_rec2 = {
        "entity_id": "job-existing-1",
        "version": 3,
        "updated_at": "2026-11-20T12:00:00Z",
    }
    postgres_rec2 = {
        "id": "job-existing-1",
        "version": 2,
        "updated_at": "2026-11-20T11:00:00Z",
        "status": "ASSIGNED",
    }
    res2 = validate_redis_restore_safety(redis_rec2, postgres_record=postgres_rec2)
    assert res2["safe"] is True
    assert res2["action"] == "PROCESS"


def test_validate_restore_invalid_record_input():
    """Verify validate_redis_restore_safety handles non-dict input safely."""
    res = validate_redis_restore_safety("not-a-dict")
    assert res["safe"] is False
    assert res["action"] == "DISCARD"


# =============================================================================
# 4. Celery Task Execution Tests
# =============================================================================

def test_add_redis_backup_strategy_task():
    """Verify add_redis_backup_strategy_task returns canonical contract."""
    res = add_redis_backup_strategy_task()
    assert res == EXPECTED_CONTRACT
    assert AddRedisBackupStrategyTask() == EXPECTED_CONTRACT
    assert Add_redis_backup_strategy_task() == EXPECTED_CONTRACT


# =============================================================================
# 5. FastAPI Admin Queue REST Endpoints Tests
# =============================================================================

def test_admin_queue_redis_backup_endpoints():
    """Verify FastAPI admin queue endpoints return expected backup strategy contract."""
    from app.routes.admin_queue import (
        get_redis_backup_status_endpoint,
        get_redis_backup_strategy_endpoint,
        post_configure_redis_backup_endpoint,
        get_redis_backup_config_endpoint,
        get_redis_backup_classification_endpoint,
        post_validate_redis_restore_endpoint,
    )

    assert get_redis_backup_status_endpoint() == EXPECTED_CONTRACT
    assert get_redis_backup_strategy_endpoint() == EXPECTED_CONTRACT
    assert post_configure_redis_backup_endpoint() == EXPECTED_CONTRACT

    config_res = get_redis_backup_config_endpoint()
    assert config_res["status"] == "DOCUMENTED"
    assert config_res["backup_strategy"] == "RDB_OR_AOF_AS_REQUIRED"

    # Classification endpoint
    class_all = get_redis_backup_classification_endpoint()
    assert "durable_needed" in class_all
    assert "reconstructable" in class_all

    class_single = get_redis_backup_classification_endpoint(key="notifications")
    assert class_single["classification"] == "DURABLE_NEEDED"

    # Validation endpoint
    valid_res = post_validate_redis_restore_endpoint({
        "redis_record": {"version": 1},
        "postgres_record": {"version": 2},
    })
    assert valid_res["safe"] is False
    assert valid_res["action"] == "DISCARD"


# =============================================================================
# 6. Docker / Compose Redis Persistence Configuration Tests
# =============================================================================

def test_compose_redis_persistence_configuration():
    """Verify compose.yaml specifies redis service with volume persistence and RDB/AOF flags."""
    compose_path = Path(__file__).resolve().parent.parent.parent / "compose.yaml"
    assert compose_path.exists(), f"compose.yaml not found at {compose_path}"

    with open(compose_path, "r", encoding="utf-8") as f:
        compose_data = yaml.safe_load(f)

    services = compose_data.get("services", {})
    assert "redis" in services, "Redis service missing from compose.yaml"

    redis_service = services["redis"]
    assert "redis_data:/data" in redis_service.get("volumes", [])

    volumes = compose_data.get("volumes", {})
    assert "redis_data" in volumes, "redis_data volume missing from top-level volumes in compose.yaml"

    cmd = str(redis_service.get("command", ""))
    assert "--save" in cmd
    assert "--appendonly" in cmd
    assert "yes" in cmd
