"""
test_redis_expiration.py

Comprehensive test suite for Story: Configure Redis expiration rules (Redis Enhancement capability).
Validates:
- Canonical JSON output contract:
    {
        "status": "CONFIGURED",
        "ttl_policy": "ENABLED"
    }
- Safe expiration applied only to ephemeral/cached keys.
- Strict protection of durable sources of truth (PostgreSQL state, broker queues, DLQs).
- Configurable TTL values via defaults, runtime updates, and environment overrides.
- Expiry application and active TTL refresh behavior.
- Tenant isolation and correlation_id preservation.
- Authoritative story aliases across redis_client, tasks, services, and worker.
- Celery task configure_redis_expiration_rules_task.
- FastAPI administrative endpoints under /admin/queue/redis/expiration*.
"""

import os
from unittest.mock import MagicMock
import pytest

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
    RedisCacheManager,
)
from app.services.redis_expiration import (
    RedisExpirationManager,
    RedisExpirationService,
    redis_expiration_service,
    configure_redis_expiration_rules as service_configure,
    get_redis_expiration_config as service_config,
    DEFAULT_REDIS_EXPIRATION_CONFIG as SERVICE_DEFAULT_CONFIG,
)
from app.tasks import (
    configure_redis_expiration_rules_task,
    ConfigureRedisExpirationRulesTask,
    Configure_redis_expiration_rules_task,
    DEFAULT_REDIS_EXPIRATION_CONFIG as TASKS_DEFAULT_CONFIG,
    configure_redis_expiration_rules as tasks_configure,
    get_redis_expiration_config as tasks_config,
)


EXPECTED_CONTRACT = {
    "status": "CONFIGURED",
    "ttl_policy": "ENABLED",
}


# =============================================================================
# 1. Canonical Contract & Configuration Validation
# =============================================================================

def test_canonical_json_contract():
    """Verify configure_redis_expiration_rules returns exact canonical contract."""
    res = configure_redis_expiration_rules()
    assert res == EXPECTED_CONTRACT
    assert res["status"] == "CONFIGURED"
    assert res["ttl_policy"] == "ENABLED"


def test_default_constant_contract():
    """Verify default config constants across modules match expected contract."""
    assert DEFAULT_REDIS_EXPIRATION_CONFIG == EXPECTED_CONTRACT
    assert SERVICE_DEFAULT_CONFIG == EXPECTED_CONTRACT
    assert TASKS_DEFAULT_CONFIG == EXPECTED_CONTRACT


def test_story_aliases_consistency():
    """Verify all story function aliases resolve to canonical configuration."""
    for fn in [
        ConfigureRedisExpirationRules,
        Configure_redis_expiration_rules,
        get_redis_expiration_rules,
        service_configure,
        tasks_configure,
    ]:
        res = fn()
        assert res == EXPECTED_CONTRACT


def test_get_redis_expiration_config_details():
    """Verify get_redis_expiration_config returns comprehensive policy metadata."""
    details = get_redis_expiration_config()
    assert details["status"] == "CONFIGURED"
    assert details["ttl_policy"] == "ENABLED"
    assert details["total_rules"] >= 14
    assert len(details["rules"]) >= 14
    assert len(details["protected_patterns"]) >= 10

    # Verify service and tasks config wrappers match
    assert service_config()["status"] == "CONFIGURED"
    assert tasks_config()["status"] == "CONFIGURED"


# =============================================================================
# 2. Durable Source-of-Truth Protection Tests
# =============================================================================

def test_is_durable_record_protects_broker_queues_and_dlqs():
    """Verify durable broker lists and DLQs are flagged as durable and never expired."""
    protected_keys = [
        "notifications",
        "billing",
        "gps_processing",
        "ai_processing",
        "high_priority",
        "reports",
        "dlq",
        "gps_purge_dlq",
        "gps_purge:dlq",
        "task_queue:tenant:tenant-123",
        "task_queue:tenants",
    ]

    for key in protected_keys:
        assert is_durable_record(key) is True, f"Key {key} should be protected as durable"


def test_is_durable_record_allows_ephemeral_keys():
    """Verify ephemeral and cache keys are not flagged as durable."""
    ephemeral_keys = [
        "dispatch:heartbeat:tech-1",
        "dispatch:tenant:t1:heartbeat:tech-1",
        "dispatch:timer:job-1",
        "job:timer:job-1",
        "gps_purge:status:job-99",
        "cache:tenant:t1:jobs",
        "cache:user-profile",
        "locks:job:job-42",
        "job:lock:job-42",
        "celery:result:task-101",
        "celery_monitoring:failures:notifications",
    ]

    for key in ephemeral_keys:
        assert is_durable_record(key) is False, f"Key {key} should NOT be durable"


def test_is_durable_record_handles_invalid_input():
    """Verify is_durable_record safely handles None or empty keys."""
    assert is_durable_record(None) is False
    assert is_durable_record("") is False
    assert is_durable_record(12345) is False


# =============================================================================
# 3. Expiration Application & Refresh Behavior Tests
# =============================================================================

def test_apply_expiration_success_ephemeral_key():
    """Verify apply_expiration sets TTL on ephemeral keys with mock client."""
    mock_redis = MagicMock()
    mock_redis.expire.return_value = True

    res = apply_expiration(
        key="dispatch:heartbeat:tech-10",
        ttl_seconds=60,
        redis_client=mock_redis,
        tenant_id="tenant-alpha",
        correlation_id="corr-101",
    )

    assert res["status"] == "APPLIED"
    assert res["applied"] is True
    assert res["ttl_seconds"] == 60
    assert res["is_durable"] is False
    assert res["tenant_id"] == "tenant-alpha"
    assert res["correlation_id"] == "corr-101"
    mock_redis.expire.assert_called_once_with("dispatch:heartbeat:tech-10", 60)


def test_apply_expiration_uses_rule_derived_ttl_when_none():
    """Verify apply_expiration derives default TTL from registered rule when ttl_seconds is omitted."""
    mock_redis = MagicMock()
    mock_redis.expire.return_value = True

    res = apply_expiration(
        key="locks:job:job-77",
        redis_client=mock_redis,
    )

    assert res["status"] == "APPLIED"
    assert res["applied"] is True
    assert res["ttl_seconds"] == 30  # default lock ttl
    mock_redis.expire.assert_called_once_with("locks:job:job-77", 30)


def test_apply_expiration_rejects_durable_records():
    """Verify apply_expiration rejects expiration on durable queues and DLQs."""
    mock_redis = MagicMock()

    res = apply_expiration(
        key="notifications",
        ttl_seconds=60,
        redis_client=mock_redis,
    )

    assert res["status"] == "REJECTED"
    assert res["applied"] is False
    assert res["reason"] == "DURABLE_RECORD_PROTECTED"
    assert res["is_durable"] is True
    mock_redis.expire.assert_not_called()


def test_apply_expiration_rejects_dlq():
    """Verify DLQs are protected from expiration."""
    mock_redis = MagicMock()

    for dlq_key in ["dlq", "gps_purge_dlq", "gps_purge:dlq"]:
        res = apply_expiration(key=dlq_key, redis_client=mock_redis)
        assert res["status"] == "REJECTED"
        assert res["applied"] is False
        mock_redis.expire.assert_not_called()


def test_apply_expiration_invalid_inputs():
    """Verify apply_expiration handles invalid key or negative TTL gracefully."""
    mock_redis = MagicMock()

    # Empty key
    res_empty = apply_expiration(key="", redis_client=mock_redis)
    assert res_empty["status"] == "INVALID_KEY"
    assert res_empty["applied"] is False

    # Negative TTL
    res_neg = apply_expiration(key="cache:test", ttl_seconds=-10, redis_client=mock_redis)
    assert res_neg["status"] == "INVALID_TTL"
    assert res_neg["applied"] is False


def test_refresh_expiration_behavior():
    """Verify refresh_expiration refreshes active key TTL and protects durable keys."""
    mock_redis = MagicMock()
    mock_redis.expire.return_value = True

    res = refresh_expiration(
        key="dispatch:timer:job-88",
        ttl_seconds=300,
        redis_client=mock_redis,
        tenant_id="tenant-beta",
        correlation_id="corr-202",
    )

    assert res["status"] == "REFRESHED"
    assert res["refreshed"] is True
    assert res["ttl_seconds"] == 300
    mock_redis.expire.assert_called_once_with("dispatch:timer:job-88", 300)

    # Durable key rejected
    mock_redis.reset_mock()
    res_dur = refresh_expiration(key="billing", redis_client=mock_redis)
    assert res_dur["status"] == "REJECTED"
    assert res_dur["refreshed"] is False
    mock_redis.expire.assert_not_called()


# =============================================================================
# 4. Configurable TTL & Environment Overrides Tests
# =============================================================================

def test_get_effective_ttl_default_and_env_override(monkeypatch):
    """Verify TTL respects environment variable overrides when configured."""
    # Default without env var
    monkeypatch.delenv("REDIS_TTL_HEARTBEAT", raising=False)
    assert get_effective_ttl("dispatch:heartbeat") == 60

    # With valid env var override
    monkeypatch.setenv("REDIS_TTL_HEARTBEAT", "120")
    assert get_effective_ttl("dispatch:heartbeat") == 120

    # With invalid env var (falls back to default)
    monkeypatch.setenv("REDIS_TTL_HEARTBEAT", "invalid-int")
    assert get_effective_ttl("dispatch:heartbeat") == 60


def test_configure_redis_expiration_rules_runtime_updates():
    """Verify configure_redis_expiration_rules accepts runtime rule updates."""
    updates = {
        "dispatch:heartbeat": {"default_ttl": 90},
    }
    res = configure_redis_expiration_rules(policy_updates=updates)
    assert res == EXPECTED_CONTRACT
    assert DEFAULT_REDIS_EXPIRATION_RULES["dispatch:heartbeat"]["default_ttl"] == 90

    # Reset back to default
    DEFAULT_REDIS_EXPIRATION_RULES["dispatch:heartbeat"]["default_ttl"] = 60


def test_get_expiration_rule_matching():
    """Verify get_expiration_rule correctly matches keys to rules."""
    rule_hb = get_expiration_rule("dispatch:heartbeat:tech-5")
    assert rule_hb["matched_rule"] == "dispatch:heartbeat"
    assert rule_hb["category"] == "dispatch"
    assert rule_hb["is_durable"] is False

    rule_dur = get_expiration_rule("high_priority")
    assert rule_dur["is_durable"] is True
    assert rule_dur["ttl_seconds"] is None

    rule_res = get_expiration_rule("task_result:t-1234")
    assert rule_res["matched_rule"] == "celery:result"
    assert rule_res["ttl_seconds"] == 86400


# =============================================================================
# 5. RedisCacheManager TTL & Persist Helper Methods
# =============================================================================

def test_redis_cache_manager_ttl_and_persist():
    """Verify RedisCacheManager has explicit ttl() and persist() methods."""
    mgr = RedisCacheManager()
    mock_inner = MagicMock()
    mock_inner.ttl.return_value = 180
    mock_inner.persist.return_value = 1
    mgr.client = mock_inner

    assert mgr.ttl("any-key") == 180
    mock_inner.ttl.assert_called_once_with("any-key")

    assert mgr.persist("any-key") is True
    mock_inner.persist.assert_called_once_with("any-key")


# =============================================================================
# 6. Service & Manager Classes Tests
# =============================================================================

def test_redis_expiration_service_and_manager():
    """Verify RedisExpirationManager coordinates expiration methods cleanly."""
    mock_redis = MagicMock()
    mock_redis.expire.return_value = True
    manager = RedisExpirationManager(redis_client=mock_redis)

    assert manager.get_config() == EXPECTED_CONTRACT
    assert manager.get_details()["status"] == "CONFIGURED"
    assert manager.is_durable("reports") is True
    assert manager.is_durable("cache:abc") is False

    rule = manager.get_rule_for_key("locks:job:j1")
    assert rule is not None
    assert rule["category"] == "locks"

    app_res = manager.apply("cache:item-1", ttl_seconds=120)
    assert app_res["applied"] is True

    ref_res = manager.refresh("cache:item-1", ttl_seconds=120)
    assert ref_res["refreshed"] is True

    # Test singleton instance
    assert redis_expiration_service.get_config() == EXPECTED_CONTRACT


# =============================================================================
# 7. Celery Task Execution Tests
# =============================================================================

def test_configure_redis_expiration_rules_task():
    """Verify configure_redis_expiration_rules_task Celery task returns canonical contract."""
    res = configure_redis_expiration_rules_task()
    assert res == EXPECTED_CONTRACT

    # Test aliases
    assert ConfigureRedisExpirationRulesTask() == EXPECTED_CONTRACT
    assert Configure_redis_expiration_rules_task() == EXPECTED_CONTRACT


# =============================================================================
# 8. FastAPI Admin Queue REST Endpoints Tests
# =============================================================================

def test_admin_queue_redis_expiration_endpoints():
    """Verify FastAPI admin queue endpoints return expected expiration contract."""
    from app.routes.admin_queue import (
        get_redis_expiration_rules_endpoint,
        configure_redis_expiration_rules_endpoint,
        get_redis_expiration_details_endpoint,
        post_redis_apply_expiration_endpoint,
        post_redis_refresh_expiration_endpoint,
    )

    assert get_redis_expiration_rules_endpoint() == EXPECTED_CONTRACT
    assert configure_redis_expiration_rules_endpoint() == EXPECTED_CONTRACT

    details = get_redis_expiration_details_endpoint()
    assert details["status"] == "CONFIGURED"
    assert "rules" in details

    # Safe apply on ephemeral key
    apply_res = post_redis_apply_expiration_endpoint({
        "key": "dispatch:heartbeat:tech-99",
        "ttl_seconds": 60,
        "tenant_id": "tenant-test",
        "correlation_id": "corr-test",
    })
    assert apply_res["status"] in {"APPLIED", "ERROR"}  # APPLIED or handled if no live redis

    # Safe apply rejected on durable key
    dur_res = post_redis_apply_expiration_endpoint({
        "key": "notifications",
        "ttl_seconds": 60,
    })
    assert dur_res["status"] == "REJECTED"
    assert dur_res["applied"] is False

    # Safe refresh rejected on durable key
    dur_ref = post_redis_refresh_expiration_endpoint({
        "key": "dlq",
        "ttl_seconds": 60,
    })
    assert dur_ref["status"] == "REJECTED"
    assert dur_ref["refreshed"] is False
