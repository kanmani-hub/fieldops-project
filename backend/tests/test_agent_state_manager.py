"""
test_agent_state_manager.py

Unit tests for AgentStateManager.

Uses the same in-memory SQLite approach as test_agent_state_repository.

Coverage targets
----------------
Manager:
  1.  save_agent snapshots agent state into the repository
  2.  save_agent returns the persisted snapshot
  3.  repository is retained via explicit assignment (falsey-value safety)
  4.  save_agent with correlation_id stores it
  5.  save_agent with last_error stores a trimmed safe summary
  6.  save_agent with last_error > 500 chars truncates to 500
  7.  save_agent with blank last_error stores None
  8.  save_agent with metadata deep-copies the dict
  9.  save_agent with None metadata stores empty dict
  10. load returns None for unknown agent
  11. load returns saved snapshot
  12. delete returns True for existing agent
  13. delete returns False for missing agent
  14. save_agent with None agent raises ValueError
  15. Tenant isolation — save then load with wrong tenant returns None
  16. Multiple tenants do not interfere with each other

Lifecycle integration:
  17. AgentLifecycle without state_manager runs without errors (no regression)
  18. After initialize — IDLE state persisted before teardown
  19. After execute (success) — IDLE state persisted, result_status=success
  20. After teardown — TERMINATED state persisted
  21. After execute (failure) — ERROR state persisted, last_error present,
      result_status=failed; TERMINATED after teardown
  22. After execute (timeout) — ERROR state persisted,
      result_status=timeout; TERMINATED after teardown
  23. Persistence failure does not interrupt agent execution (log-and-continue)
  24. Persistence failure during execute still returns a valid AgentResult
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import SQLAlchemyError
from unittest.mock import MagicMock, patch

from app.database import Base
from app.services.ai.FieldOpsAI.agents.base import AgentState, BaseAgent
from app.services.ai.FieldOpsAI.repositories.agent_state_repository import (
    AgentStateRepository,
)
from app.services.ai.FieldOpsAI.runtime.agent_pool import AgentPool
from app.services.ai.FieldOpsAI.runtime.agent_state_manager import AgentStateManager
from app.services.ai.FieldOpsAI.runtime.lifecycle import AgentLifecycle
from app.services.ai.FieldOpsAI.schemas.agent_config import AgentConfig
from app.services.ai.FieldOpsAI.schemas.agent_result import AgentResult, AgentResultStatus
from app.services.ai.FieldOpsAI.schemas.ai_task import AITask


# ---------------------------------------------------------------------------
# In-memory SQLite test database
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture
def db(engine):
    TestSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = TestSession()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def repo(db):
    return AgentStateRepository(db=db)


@pytest.fixture
def manager(repo):
    return AgentStateManager(repository=repo)


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ---------------------------------------------------------------------------
# Minimal concrete agents
# ---------------------------------------------------------------------------

class SimpleAgent(BaseAgent[dict[str, Any]]):
    """Agent that always succeeds."""
    async def run(self, context: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True}


class FailingAgent(BaseAgent[dict[str, Any]]):
    """Agent that always raises a runtime error."""
    async def run(self, context: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("deliberate failure")


class BlockingAgent(BaseAgent[dict[str, Any]]):
    """Agent that blocks until cancelled (used for timeout tests)."""
    _released = False

    async def run(self, context: dict[str, Any]) -> dict[str, Any]:
        try:
            await asyncio.sleep(999)
        finally:
            BlockingAgent._released = True
        return {}  # unreachable


def make_config(
    tenant_id: str = "tenant-mgr",
    timeout_seconds: float = 30.0,
) -> AgentConfig:
    return AgentConfig(
        agent_type=AITask.DISPATCH,
        tenant_id=tenant_id,
        agent_version="1.0",
        timeout_seconds=timeout_seconds,
    )


def make_agent(tenant_id: str = "tenant-mgr") -> SimpleAgent:
    return SimpleAgent(config=make_config(tenant_id=tenant_id))


def make_failing_agent(tenant_id: str = "tenant-fail-agent") -> FailingAgent:
    return FailingAgent(config=make_config(tenant_id=tenant_id))


def make_blocking_agent(
    tenant_id: str = "tenant-timeout",
    timeout_seconds: float = 0.1,
) -> BlockingAgent:
    BlockingAgent._released = False
    return BlockingAgent(config=make_config(
        tenant_id=tenant_id,
        timeout_seconds=timeout_seconds,
    ))


# ===========================================================================
# Manager unit tests (1–16)
# ===========================================================================

class TestAgentStateManager:

    def test_save_agent_inserts_snapshot(self, manager, repo):
        """Test 1: save_agent creates a database record."""
        agent = make_agent()
        manager.save_agent(agent)
        result = repo.get(agent_id=agent.agent_id, tenant_id=agent.tenant_id)
        assert result is not None

    def test_save_agent_returns_snapshot(self, manager):
        """Test 2: save_agent returns the persisted AgentStateSnapshot."""
        from app.services.ai.FieldOpsAI.schemas.agent_state import AgentStateSnapshot
        agent = make_agent()
        snap = manager.save_agent(agent)
        assert isinstance(snap, AgentStateSnapshot)
        assert snap.agent_id == agent.agent_id

    def test_repository_retained_via_explicit_assignment(self):
        """Test 3: AgentStateManager stores the supplied repository regardless of
        its boolean truthiness (falsey-value safety).

        A falsey object (e.g. one whose __bool__ returns False) must still
        be retained so callers cannot accidentally replace the dependency with
        a default by relying on 'if repo: ...' logic.
        """
        class FalseyRepo:
            """Repository whose __bool__ always returns False."""
            def __bool__(self) -> bool:
                return False

        falsey_repo = FalseyRepo()
        mgr = AgentStateManager(repository=falsey_repo)  # type: ignore[arg-type]
        assert mgr.repository is falsey_repo

    def test_save_agent_stores_correlation_id(self, manager, repo):
        """Test 4: correlation_id is stored on the snapshot."""
        agent = make_agent(tenant_id="tenant-corr")
        corr_id = str(uuid4())
        manager.save_agent(agent, correlation_id=corr_id)
        result = repo.get(agent_id=agent.agent_id, tenant_id="tenant-corr")
        assert result is not None
        assert result.correlation_id == corr_id

    def test_save_agent_stores_last_error(self, manager, repo):
        """Test 5: last_error is stored on the snapshot."""
        agent = make_agent(tenant_id="tenant-err")
        manager.save_agent(agent, last_error="safe error message")
        result = repo.get(agent_id=agent.agent_id, tenant_id="tenant-err")
        assert result is not None
        assert result.last_error == "safe error message"

    def test_save_agent_truncates_long_last_error(self, manager, repo):
        """Test 6: last_error longer than 500 chars is truncated."""
        agent = make_agent(tenant_id="tenant-trunc")
        long_error = "x" * 600
        manager.save_agent(agent, last_error=long_error)
        result = repo.get(agent_id=agent.agent_id, tenant_id="tenant-trunc")
        assert result is not None
        assert result.last_error is not None
        assert len(result.last_error) == 500

    def test_save_agent_blank_last_error_stored_as_none(self, manager, repo):
        """Test 7: A blank last_error is stored as None."""
        agent = make_agent(tenant_id="tenant-blank-err")
        manager.save_agent(agent, last_error="   ")
        result = repo.get(agent_id=agent.agent_id, tenant_id="tenant-blank-err")
        assert result is not None
        assert result.last_error is None

    def test_save_agent_deep_copies_metadata(self, manager, repo):
        """Test 8: Caller's nested metadata dict is not mutated (deep copy)."""
        agent = make_agent(tenant_id="tenant-meta")
        original_meta = {"nested": {"key": "value"}}
        manager.save_agent(agent, metadata=original_meta)

        # Mutate the original's nested dict after save
        original_meta["nested"]["injected"] = "bad"

        result = repo.get(agent_id=agent.agent_id, tenant_id="tenant-meta")
        assert result is not None
        assert "injected" not in result.metadata.get("nested", {})

    def test_save_agent_none_metadata_stores_empty_dict(self, manager, repo):
        """Test 9: save_agent with None metadata stores an empty dict."""
        agent = make_agent(tenant_id="tenant-nometa")
        manager.save_agent(agent, metadata=None)
        result = repo.get(agent_id=agent.agent_id, tenant_id="tenant-nometa")
        assert result is not None
        assert result.metadata == {}

    def test_load_returns_none_for_unknown_agent(self, manager):
        """Test 10: load() returns None for an agent that was never saved."""
        result = manager.load(agent_id=uuid4(), tenant_id="tenant-unknown")
        assert result is None

    def test_load_returns_saved_snapshot(self, manager):
        """Test 11: load() returns the snapshot saved by save_agent."""
        agent = make_agent(tenant_id="tenant-load")
        manager.save_agent(agent)
        result = manager.load(agent_id=agent.agent_id, tenant_id="tenant-load")
        assert result is not None
        assert result.agent_id == agent.agent_id

    def test_delete_returns_true_for_existing(self, manager):
        """Test 12: delete() returns True when the record exists."""
        agent = make_agent(tenant_id="tenant-del-mgr")
        manager.save_agent(agent)
        deleted = manager.delete(agent_id=agent.agent_id, tenant_id="tenant-del-mgr")
        assert deleted is True

    def test_delete_returns_false_for_missing(self, manager):
        """Test 13: delete() returns False for a non-existent agent."""
        result = manager.delete(agent_id=uuid4(), tenant_id="tenant-no-del")
        assert result is False

    def test_save_agent_raises_on_none_agent(self, manager):
        """Test 14: save_agent raises ValueError when agent is None."""
        with pytest.raises(ValueError, match="agent must not be None"):
            manager.save_agent(None)  # type: ignore[arg-type]

    def test_tenant_isolation_load_wrong_tenant(self, manager):
        """Test 15: Loading with wrong tenant_id returns None."""
        agent = make_agent(tenant_id="tenant-iso-right")
        manager.save_agent(agent)
        result = manager.load(agent_id=agent.agent_id, tenant_id="tenant-iso-wrong")
        assert result is None

    def test_multiple_tenants_do_not_interfere(self, manager, repo):
        """Test 16: Snapshots for tenant-A and tenant-B are independent."""
        agent_a = make_agent(tenant_id="tenant-multi-a")
        agent_b = make_agent(tenant_id="tenant-multi-b")
        manager.save_agent(agent_a)
        manager.save_agent(agent_b)

        results_a = repo.list_by_tenant("tenant-multi-a")
        results_b = repo.list_by_tenant("tenant-multi-b")

        ids_a = {r.agent_id for r in results_a}
        ids_b = {r.agent_id for r in results_b}

        assert agent_a.agent_id in ids_a
        assert agent_b.agent_id not in ids_a
        assert agent_b.agent_id in ids_b
        assert agent_a.agent_id not in ids_b


# ===========================================================================
# Lifecycle integration tests (17–24)
# ===========================================================================

class TestAgentLifecycleWithStateManager:

    @pytest.mark.anyio
    async def test_lifecycle_without_state_manager_no_regression(self):
        """Test 17: Lifecycle without state_manager works exactly as before."""
        agent = make_agent()
        pool = AgentPool()
        lc = AgentLifecycle(agent=agent, pool=pool)

        await lc.initialize()
        result = await lc.execute({"tenant_id": "tenant-mgr"})
        await lc.teardown()

        assert result.status is AgentResultStatus.SUCCESS

    @pytest.mark.anyio
    async def test_lifecycle_persists_idle_after_initialize(self, db):
        """Test 18: IDLE state is persisted immediately after initialize."""
        agent = make_agent(tenant_id="tenant-init")
        pool = AgentPool()
        repo = AgentStateRepository(db=db)
        state_mgr = AgentStateManager(repository=repo)

        lc = AgentLifecycle(agent=agent, pool=pool, state_manager=state_mgr)
        await lc.initialize()

        # Assert IDLE before teardown
        snap = repo.get(agent_id=agent.agent_id, tenant_id="tenant-init")
        assert snap is not None
        assert snap.state is AgentState.IDLE

        await lc.teardown()

    @pytest.mark.anyio
    async def test_lifecycle_persists_idle_after_successful_execute(self, db):
        """Test 19: IDLE state persisted after successful execute; result_status=success."""
        agent = make_agent(tenant_id="tenant-exec-ok")
        pool = AgentPool()
        repo = AgentStateRepository(db=db)
        state_mgr = AgentStateManager(repository=repo)

        lc = AgentLifecycle(agent=agent, pool=pool, state_manager=state_mgr)
        await lc.initialize()
        result = await lc.execute({"tenant_id": "tenant-exec-ok"})

        # Assert immediately after execute, before teardown
        assert result.status is AgentResultStatus.SUCCESS
        snap = repo.get(agent_id=agent.agent_id, tenant_id="tenant-exec-ok")
        assert snap is not None
        assert snap.state is AgentState.IDLE
        assert snap.metadata.get("result_status") == "success"

        await lc.teardown()

    @pytest.mark.anyio
    async def test_lifecycle_persists_terminated_after_teardown(self, db):
        """Test 20: TERMINATED state persisted after teardown."""
        agent = make_agent(tenant_id="tenant-teardown")
        pool = AgentPool()
        repo = AgentStateRepository(db=db)
        state_mgr = AgentStateManager(repository=repo)

        lc = AgentLifecycle(agent=agent, pool=pool, state_manager=state_mgr)
        await lc.initialize()
        await lc.execute({"tenant_id": "tenant-teardown"})
        await lc.teardown()

        snap = repo.get(agent_id=agent.agent_id, tenant_id="tenant-teardown")
        assert snap is not None
        assert snap.state is AgentState.TERMINATED

    @pytest.mark.anyio
    async def test_lifecycle_persists_error_after_failing_execute(self, db):
        """Test 21: After a failing execute:
        - AgentResult.status is FAILED
        - Persisted state is ERROR before teardown
        - safe last_error is present
        - result_status metadata is 'failed'
        - After teardown, state is TERMINATED
        """
        agent = make_failing_agent(tenant_id="tenant-fail-lc")
        pool = AgentPool()
        repo = AgentStateRepository(db=db)
        state_mgr = AgentStateManager(repository=repo)

        lc = AgentLifecycle(agent=agent, pool=pool, state_manager=state_mgr)
        await lc.initialize()
        result = await lc.execute({"tenant_id": "tenant-fail-lc"})

        # Assert immediately after execute, before teardown
        assert result.status is AgentResultStatus.FAILED

        snap = repo.get(agent_id=agent.agent_id, tenant_id="tenant-fail-lc")
        assert snap is not None
        assert snap.state is AgentState.ERROR
        assert snap.last_error is not None
        assert snap.metadata.get("result_status") == "failed"

        # Teardown transitions to TERMINATED
        # We must reset the agent's state to allow teardown from ERROR
        await lc.teardown()

        snap_after = repo.get(agent_id=agent.agent_id, tenant_id="tenant-fail-lc")
        assert snap_after is not None
        assert snap_after.state is AgentState.TERMINATED

    @pytest.mark.anyio
    async def test_lifecycle_persists_error_after_timeout(self, db):
        """Test 22: After a timeout execute:
        - AgentResult.status is TIMEOUT
        - Persisted state is ERROR before teardown
        - result_status metadata is 'timeout'
        - Worker is always released (finally block in BlockingAgent)
        - After teardown, state is TERMINATED
        """
        BlockingAgent._released = False
        agent = make_blocking_agent(
            tenant_id="tenant-timeout-lc",
            timeout_seconds=0.05,
        )
        pool = AgentPool()
        repo = AgentStateRepository(db=db)
        state_mgr = AgentStateManager(repository=repo)

        lc = AgentLifecycle(
            agent=agent,
            pool=pool,
            state_manager=state_mgr,
            run_timeout_seconds=0.05,
        )
        await lc.initialize()
        result = await lc.execute({"tenant_id": "tenant-timeout-lc"})

        # Worker must always be released
        assert BlockingAgent._released is True

        assert result.status is AgentResultStatus.TIMEOUT

        snap = repo.get(agent_id=agent.agent_id, tenant_id="tenant-timeout-lc")
        assert snap is not None
        assert snap.state is AgentState.ERROR
        assert snap.metadata.get("result_status") == "timeout"

        # Reset state for teardown
        await lc.teardown()

        snap_after = repo.get(agent_id=agent.agent_id, tenant_id="tenant-timeout-lc")
        assert snap_after is not None
        assert snap_after.state is AgentState.TERMINATED

    @pytest.mark.anyio
    async def test_persistence_failure_does_not_interrupt_execution(self, db):
        """Test 23: A persistence failure is logged but does not raise from execute."""
        agent = make_agent(tenant_id="tenant-pf")
        pool = AgentPool()
        repo = AgentStateRepository(db=db)
        state_mgr = AgentStateManager(repository=repo)

        with patch.object(state_mgr, "save_agent", side_effect=SQLAlchemyError("db down")):
            lc = AgentLifecycle(agent=agent, pool=pool, state_manager=state_mgr)
            await lc.initialize()
            result = await lc.execute({"tenant_id": "tenant-pf"})
            await lc.teardown()

        assert result.status is AgentResultStatus.SUCCESS

    @pytest.mark.anyio
    async def test_persistence_failure_during_execute_returns_valid_result(self, db):
        """Test 24: A persistence failure during execute still returns AgentResult."""
        agent = make_agent(tenant_id="tenant-pfr")
        pool = AgentPool()
        repo = AgentStateRepository(db=db)
        state_mgr = AgentStateManager(repository=repo)

        with patch.object(state_mgr, "save_agent", side_effect=RuntimeError("unexpected")):
            lc = AgentLifecycle(agent=agent, pool=pool, state_manager=state_mgr)
            await lc.initialize()
            result = await lc.execute({"tenant_id": "tenant-pfr"})
            await lc.teardown()

        assert isinstance(result, AgentResult)
