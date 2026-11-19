"""
test_distributed_locks.py

Comprehensive test suite for Story: Improve distributed locks (Redis Enhancement capability).
Validates:
- Canonical JSON output contract:
    {
        "status": "HARDENED",
        "lock_strategy": "tokenized_ttl_lock"
    }
- Cryptographically unique lock tokens for each acquired lock.
- Bounded lock TTL expiry preventing permanent deadlocks.
- Safe lock release using atomic Lua script (only releasing if caller owns token).
- Safe lock extension using atomic Lua script.
- Fail-closed error handling: Redis connection/timeout errors never assume lock exists.
- Tenant isolation and correlation_id preservation.
- Integration with PriorityTaskQueue and ReDispatchQueueService.
- Integration with DistributedLockService and with_job_lock backwards compatibility.
- Celery task improve_distributed_locks_task.
- FastAPI administrative endpoints under /admin/queue/redis/locks/*.
"""

from unittest.mock import MagicMock
import pytest
from redis.exceptions import ConnectionError, TimeoutError

from app.redis_client import (
    DEFAULT_DISTRIBUTED_LOCK_CONFIG,
    acquire_distributed_lock,
    release_distributed_lock,
    extend_distributed_lock,
    is_lock_held,
    with_distributed_lock,
    improve_distributed_locks,
    get_distributed_lock_config,
    ImproveDistributedLocks,
    Improve_distributed_locks,
    get_lock_strategy,
    get_distributed_lock_status,
    LockAcquisitionError,
    _format_lock_key,
)
from app.services.distributed_lock_service import (
    DistributedLockService,
    distributed_lock_service,
    with_job_lock,
    with_distributed_lock as service_with_distributed_lock,
    ImproveDistributedLocks as ServiceImproveLocks,
    Improve_distributed_locks as ServiceImprove_locks,
    get_lock_strategy as service_get_strategy,
    get_distributed_lock_status as service_get_status,
    DEFAULT_DISTRIBUTED_LOCK_CONFIG as SERVICE_DEFAULT_LOCK_CONFIG,
)
from app.services.task_queue import (
    PriorityTaskQueue,
    ImproveDistributedLocks as TaskQueueImproveLocks,
    DEFAULT_DISTRIBUTED_LOCK_CONFIG as TASK_QUEUE_DEFAULT_LOCK_CONFIG,
)
from app.services.re_dispatch_queue import (
    ReDispatchQueueService,
    ImproveDistributedLocks as ReDispatchImproveLocks,
    DEFAULT_DISTRIBUTED_LOCK_CONFIG as RE_DISPATCH_DEFAULT_LOCK_CONFIG,
)
from app.tasks import (
    improve_distributed_locks_task,
    ImproveDistributedLocksTask,
    Improve_distributed_locks_task,
    DEFAULT_DISTRIBUTED_LOCK_CONFIG as TASKS_DEFAULT_LOCK_CONFIG,
    improve_distributed_locks as tasks_improve_locks,
)


EXPECTED_CONTRACT = {
    "status": "HARDENED",
    "lock_strategy": "tokenized_ttl_lock",
}


# =============================================================================
# Fake Redis Fixture for Concurrency & Tokenized Lock Tests
# =============================================================================

class FakeLockRedis:
    def __init__(self):
        self.store = {}
        self.ttls = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, nx=False, px=None, ex=None):
        if nx and key in self.store:
            return False
        self.store[key] = str(value)
        if px:
            self.ttls[key] = px
        elif ex:
            self.ttls[key] = ex * 1000
        return True

    def delete(self, key):
        if key in self.store:
            del self.store[key]
            self.ttls.pop(key, None)
            return 1
        return 0

    def eval(self, script, numkeys, key, *args):
        # Emulate atomic release script
        if "del" in script:
            expected_token = args[0]
            if self.store.get(key) == expected_token:
                del self.store[key]
                self.ttls.pop(key, None)
                return 1
            return 0
        # Emulate atomic extend script
        if "pexpire" in script:
            expected_token = args[0]
            new_ttl_ms = int(args[1])
            if self.store.get(key) == expected_token:
                self.ttls[key] = new_ttl_ms
                return 1
            return 0
        return 0


# =============================================================================
# 1. Canonical Contract & Module Exports
# =============================================================================

def test_canonical_json_contract():
    """Verify improve_distributed_locks returns exact canonical contract."""
    res = improve_distributed_locks()
    assert res == EXPECTED_CONTRACT
    assert res["status"] == "HARDENED"
    assert res["lock_strategy"] == "tokenized_ttl_lock"


def test_default_constant_contract():
    """Verify DEFAULT_DISTRIBUTED_LOCK_CONFIG constants match across modules."""
    assert DEFAULT_DISTRIBUTED_LOCK_CONFIG == EXPECTED_CONTRACT
    assert SERVICE_DEFAULT_LOCK_CONFIG == EXPECTED_CONTRACT
    assert TASK_QUEUE_DEFAULT_LOCK_CONFIG == EXPECTED_CONTRACT
    assert RE_DISPATCH_DEFAULT_LOCK_CONFIG == EXPECTED_CONTRACT
    assert TASKS_DEFAULT_LOCK_CONFIG == EXPECTED_CONTRACT


def test_story_aliases_consistency():
    """Verify all story function aliases resolve to canonical contract."""
    for fn in [
        ImproveDistributedLocks,
        Improve_distributed_locks,
        get_lock_strategy,
        get_distributed_lock_status,
        ServiceImproveLocks,
        ServiceImprove_locks,
        service_get_strategy,
        service_get_status,
        TaskQueueImproveLocks,
        ReDispatchImproveLocks,
        tasks_improve_locks,
    ]:
        assert fn() == EXPECTED_CONTRACT


def test_get_distributed_lock_config_metadata():
    """Verify get_distributed_lock_config returns detailed metadata and features."""
    cfg = get_distributed_lock_config()
    assert cfg["status"] == "HARDENED"
    assert cfg["lock_strategy"] == "tokenized_ttl_lock"
    assert cfg["default_ttl_seconds"] == 30
    assert "unique_tokens" in cfg["features"]
    assert "atomic_lua_release" in cfg["features"]
    assert "fail_closed_error_handling" in cfg["features"]


# =============================================================================
# 2. Unique Token & TTL Locking Behavior Tests
# =============================================================================

def test_acquire_lock_generates_unique_token():
    """Verify each lock acquisition generates a unique cryptographic token."""
    fake_redis = FakeLockRedis()
    acquired_1, token_1 = acquire_distributed_lock("job:100", redis_client=fake_redis)
    assert acquired_1 is True
    assert token_1 is not None
    assert len(token_1) >= 32

    # Second acquisition for same resource must fail (contention)
    acquired_2, token_2 = acquire_distributed_lock("job:100", redis_client=fake_redis)
    assert acquired_2 is False
    assert token_2 is None

    # Different resource gets different unique token
    acquired_3, token_3 = acquire_distributed_lock("job:200", redis_client=fake_redis)
    assert acquired_3 is True
    assert token_3 != token_1


def test_acquire_lock_with_custom_token_and_ttl():
    """Verify acquire_distributed_lock accepts custom token and sets TTL."""
    fake_redis = FakeLockRedis()
    acquired, token = acquire_distributed_lock(
        "resource:custom",
        ttl_seconds=45,
        lock_token="explicit-token-123",
        redis_client=fake_redis,
    )
    assert acquired is True
    assert token == "explicit-token-123"
    assert fake_redis.ttls["locks:resource:custom"] == 45000


# =============================================================================
# 3. Safe Atomic Release & Extension Tests
# =============================================================================

def test_release_lock_only_by_owner_token():
    """Verify release_distributed_lock only deletes if token matches."""
    fake_redis = FakeLockRedis()
    _, token = acquire_distributed_lock("job:300", redis_client=fake_redis)

    # Release with WRONG token fails and preserves lock
    failed_release = release_distributed_lock("job:300", lock_token="wrong-token", redis_client=fake_redis)
    assert failed_release is False
    assert is_lock_held("job:300", redis_client=fake_redis) is True

    # Release with CORRECT token succeeds and frees lock
    successful_release = release_distributed_lock("job:300", lock_token=token, redis_client=fake_redis)
    assert successful_release is True
    assert is_lock_held("job:300", redis_client=fake_redis) is False


def test_extend_lock_only_by_owner_token():
    """Verify extend_distributed_lock only extends TTL if token matches."""
    fake_redis = FakeLockRedis()
    _, token = acquire_distributed_lock("job:400", ttl_seconds=10, redis_client=fake_redis)
    assert fake_redis.ttls["locks:job:400"] == 10000

    # Extend with wrong token fails
    failed_ext = extend_distributed_lock("job:400", lock_token="bad-token", additional_ttl_seconds=60, redis_client=fake_redis)
    assert failed_ext is False
    assert fake_redis.ttls["locks:job:400"] == 10000

    # Extend with correct token succeeds
    success_ext = extend_distributed_lock("job:400", lock_token=token, additional_ttl_seconds=60, redis_client=fake_redis)
    assert success_ext is True
    assert fake_redis.ttls["locks:job:400"] == 60000


# =============================================================================
# 4. Fail-Closed Error Handling (Redis Failure Tests)
# =============================================================================

def test_fail_closed_on_redis_connection_error():
    """Verify acquire_distributed_lock fails closed and returns (False, None) on Redis errors."""
    mock_redis = MagicMock()
    mock_redis.client.set.side_effect = ConnectionError("Redis server unreachable")

    acquired, token = acquire_distributed_lock("critical:job", redis_client=mock_redis)
    assert acquired is False
    assert token is None


def test_fail_closed_on_redis_timeout_error():
    """Verify acquire_distributed_lock fails closed on Redis timeout."""
    mock_redis = MagicMock()
    mock_redis.client.set.side_effect = TimeoutError("Redis socket timed out")

    acquired, token = acquire_distributed_lock("critical:job", redis_client=mock_redis)
    assert acquired is False
    assert token is None


def test_release_lock_bounds_redis_exceptions():
    """Verify release_distributed_lock safely bounds Redis errors without uncaught crashes."""
    mock_redis = MagicMock()
    mock_redis.client.eval.side_effect = ConnectionError("Redis down during release")

    released = release_distributed_lock("job:1", "token-1", redis_client=mock_redis)
    assert released is False


# =============================================================================
# 5. Tenant Scoping & Isolation Tests
# =============================================================================

def test_format_lock_key_tenant_scoping():
    """Verify lock key formatting enforces tenant isolation."""
    # With tenant
    key_tenant = _format_lock_key("dispatch:sync", tenant_id="tenant-alpha")
    assert key_tenant == "locks:tenant:tenant-alpha:dispatch:sync"

    # Global
    key_global = _format_lock_key("dispatch:sync")
    assert key_global == "locks:dispatch:sync"


def test_tenant_isolation_concurrent_locks():
    """Verify two tenants can lock the same resource name without collision."""
    fake_redis = FakeLockRedis()

    acq_a, tok_a = acquire_distributed_lock("order:1", tenant_id="org-a", redis_client=fake_redis)
    acq_b, tok_b = acquire_distributed_lock("order:1", tenant_id="org-b", redis_client=fake_redis)

    assert acq_a is True
    assert acq_b is True
    assert tok_a != tok_b
    assert is_lock_held("order:1", tenant_id="org-a", redis_client=fake_redis) is True
    assert is_lock_held("order:1", tenant_id="org-b", redis_client=fake_redis) is True


# =============================================================================
# 6. Context Manager Tests
# =============================================================================

def test_with_distributed_lock_context_manager():
    """Verify with_distributed_lock acquires and cleanly releases upon exit."""
    fake_redis = FakeLockRedis()

    with with_distributed_lock("ctx:job", redis_client=fake_redis) as token:
        assert token is not None
        assert is_lock_held("ctx:job", redis_client=fake_redis) is True

    # Lock must be released after with block exits
    assert is_lock_held("ctx:job", redis_client=fake_redis) is False


def test_with_distributed_lock_releases_on_exception():
    """Verify lock is released even if an unhandled exception occurs inside context."""
    fake_redis = FakeLockRedis()

    with pytest.raises(RuntimeError):
        with with_distributed_lock("exc:job", redis_client=fake_redis):
            assert is_lock_held("exc:job", redis_client=fake_redis) is True
            raise RuntimeError("Failure inside protected block")

    assert is_lock_held("exc:job", redis_client=fake_redis) is False


def test_with_distributed_lock_raises_on_contention():
    """Verify with_distributed_lock raises LockAcquisitionError if lock is already held."""
    fake_redis = FakeLockRedis()
    acquire_distributed_lock("contested:job", redis_client=fake_redis)

    with pytest.raises(LockAcquisitionError):
        with with_distributed_lock("contested:job", redis_client=fake_redis):
            pass


# =============================================================================
# 7. Integration with DistributedLockService & with_job_lock
# =============================================================================

def test_distributed_lock_service_class():
    """Verify DistributedLockService coordinates lock operations."""
    fake_redis = FakeLockRedis()
    service = DistributedLockService(redis_client=fake_redis)

    assert service.get_status() == EXPECTED_CONTRACT
    assert service.get_config()["status"] == "HARDENED"

    acq, tok = service.acquire("job:999")
    assert acq is True
    assert service.is_held("job:999") is True
    assert service.extend("job:999", tok, 45) is True
    assert service.release("job:999", tok) is True
    assert service.is_held("job:999") is False


def test_with_job_lock_backwards_compatibility():
    """Verify with_job_lock continues to work seamlessly."""
    with with_job_lock("compat-job-123"):
        pass


# =============================================================================
# 8. Integration with PriorityTaskQueue & ReDispatchQueueService
# =============================================================================

def test_priority_task_queue_lock_helpers():
    """Verify PriorityTaskQueue queue locking helpers."""
    fake_redis = FakeLockRedis()
    queue = PriorityTaskQueue(redis_client=fake_redis)

    acq, tok = queue.acquire_queue_lock(tenant_id="tenant-fairness")
    assert acq is True
    assert tok is not None

    rel = queue.release_queue_lock(tenant_id="tenant-fairness", lock_token=tok)
    assert rel is True


def test_redispatch_queue_service_lock_helpers():
    """Verify ReDispatchQueueService job locking helpers."""
    fake_redis = FakeLockRedis()

    acq, tok = ReDispatchQueueService.acquire_job_lock(
        job_id="job-555",
        tenant_id="tenant-requeue",
        redis_client=fake_redis,
    )
    assert acq is True
    assert tok is not None

    rel = ReDispatchQueueService.release_job_lock(
        job_id="job-555",
        lock_token=tok,
        tenant_id="tenant-requeue",
        redis_client=fake_redis,
    )
    assert rel is True


# =============================================================================
# 9. Celery Task Execution Tests
# =============================================================================

def test_improve_distributed_locks_task():
    """Verify improve_distributed_locks_task Celery task returns canonical contract."""
    res = improve_distributed_locks_task()
    assert res == EXPECTED_CONTRACT

    # Test aliases
    assert ImproveDistributedLocksTask() == EXPECTED_CONTRACT
    assert Improve_distributed_locks_task() == EXPECTED_CONTRACT


# =============================================================================
# 10. FastAPI Admin Queue REST Endpoints Tests
# =============================================================================

def test_admin_queue_distributed_lock_endpoints():
    """Verify FastAPI admin queue endpoints return expected contract and execute operations."""
    from app.routes.admin_queue import (
        get_distributed_lock_status_endpoint,
        get_distributed_lock_strategy_endpoint,
        post_improve_distributed_locks_endpoint,
        get_distributed_lock_config_endpoint,
        post_acquire_distributed_lock_endpoint,
        post_release_distributed_lock_endpoint,
        post_extend_distributed_lock_endpoint,
    )

    assert get_distributed_lock_status_endpoint() == EXPECTED_CONTRACT
    assert get_distributed_lock_strategy_endpoint() == EXPECTED_CONTRACT
    assert post_improve_distributed_locks_endpoint() == EXPECTED_CONTRACT

    cfg = get_distributed_lock_config_endpoint()
    assert cfg["status"] == "HARDENED"
    assert cfg["lock_strategy"] == "tokenized_ttl_lock"

    # Test acquire/release/extend endpoints
    acq_res = post_acquire_distributed_lock_endpoint({
        "resource": "admin:test:lock",
        "ttl_seconds": 30,
        "tenant_id": "tenant-admin",
    })
    assert "acquired" in acq_res

    ext_res = post_extend_distributed_lock_endpoint({
        "resource": "admin:test:lock",
        "lock_token": "some-token",
        "additional_ttl_seconds": 30,
        "tenant_id": "tenant-admin",
    })
    assert "extended" in ext_res

    rel_res = post_release_distributed_lock_endpoint({
        "resource": "admin:test:lock",
        "lock_token": "some-token",
        "tenant_id": "tenant-admin",
    })
    assert "released" in rel_res
