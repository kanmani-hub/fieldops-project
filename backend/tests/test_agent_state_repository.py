"""
test_agent_state_repository.py

Unit and integration tests for AgentStateRepository and AgentStateRecord.

Uses an in-memory SQLite database following existing project patterns.

Coverage targets
----------------
Model / schema:
  1. Valid state snapshot
  2. Invalid UUID rejected
  3. Invalid AgentState rejected
  4. Invalid AITask rejected
  5. Missing tenant rejected
  6. Timezone-aware timestamps validated
  7. Metadata defaults are not shared between instances
  8. Sensitive fields are not part of the top-level schema
  9. Forbidden metadata top-level key rejected (privacy validation)
  10. Forbidden metadata nested key rejected (recursive validation)
  11. Non-JSON-compatible metadata value rejected
  12. Nested list with non-JSON value rejected
  13. Valid nested metadata accepted

Repository:
  14. Insert state
  15. Retrieve by tenant and agent ID
  16. Update existing state (upsert)
  17. Upsert does not create duplicate records
  18. Tenant isolation — only returns own-tenant records
  19. Wrong tenant returns None
  20. List states by tenant
  21. Delete existing state
  22. Delete missing state returns False
  23. Database failure triggers rollback and rollback called exactly once
  24. Timestamps update correctly on upsert
  25. Agent UUID round-trips through the database
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import patch, call
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import AgentStateRecord
from app.services.ai.FieldOpsAI.agents.base import AgentState
from app.services.ai.FieldOpsAI.repositories.agent_state_repository import (
    AgentStateRepository,
)
from app.services.ai.FieldOpsAI.schemas.agent_state import AgentStateSnapshot
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_snapshot(
    *,
    agent_id: UUID | None = None,
    agent_type: AITask = AITask.DISPATCH,
    tenant_id: str = "tenant-test",
    state: AgentState = AgentState.IDLE,
    correlation_id: str | None = None,
    last_error: str | None = None,
    metadata: dict | None = None,
) -> AgentStateSnapshot:
    now = datetime.now(timezone.utc)
    return AgentStateSnapshot(
        agent_id=agent_id or uuid4(),
        agent_type=agent_type,
        tenant_id=tenant_id,
        agent_version="1.0",
        state=state,
        correlation_id=correlation_id,
        last_error=last_error,
        metadata=metadata if metadata is not None else {},
        created_at=now,
        updated_at=now,
    )


# ===========================================================================
# Model and schema tests (1–13)
# ===========================================================================

class TestAgentStateSnapshot:

    def test_valid_state_snapshot(self):
        """Test 1: A fully valid snapshot is accepted."""
        snap = make_snapshot()
        assert isinstance(snap.agent_id, UUID)
        assert snap.tenant_id == "tenant-test"
        assert snap.state is AgentState.IDLE

    def test_invalid_uuid_rejected(self):
        """Test 2: A non-UUID string for agent_id is rejected."""
        with pytest.raises(ValidationError) as exc:
            AgentStateSnapshot(
                agent_id="not-a-uuid",
                agent_type=AITask.DISPATCH,
                tenant_id="t",
                state=AgentState.IDLE,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        assert "agent_id" in str(exc.value).lower()

    def test_invalid_state_rejected(self):
        """Test 3: An unrecognised state string is rejected."""
        with pytest.raises(ValidationError) as exc:
            AgentStateSnapshot(
                agent_id=uuid4(),
                agent_type=AITask.DISPATCH,
                tenant_id="t",
                state="FLYING",  # type: ignore[arg-type]
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        assert "state" in str(exc.value).lower()

    def test_invalid_agent_type_rejected(self):
        """Test 4: An unrecognised agent_type string is rejected."""
        with pytest.raises(ValidationError) as exc:
            AgentStateSnapshot(
                agent_id=uuid4(),
                agent_type="UNICORN",  # type: ignore[arg-type]
                tenant_id="t",
                state=AgentState.IDLE,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        assert "agent_type" in str(exc.value).lower()

    def test_missing_tenant_rejected(self):
        """Test 5: An empty tenant_id is rejected."""
        with pytest.raises(ValidationError):
            AgentStateSnapshot(
                agent_id=uuid4(),
                agent_type=AITask.DISPATCH,
                tenant_id="",
                state=AgentState.IDLE,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )

    def test_timezone_aware_timestamps_required(self):
        """Test 6: Naive timestamps are rejected."""
        with pytest.raises(ValidationError) as exc:
            AgentStateSnapshot(
                agent_id=uuid4(),
                agent_type=AITask.DISPATCH,
                tenant_id="t",
                state=AgentState.IDLE,
                created_at=datetime(2024, 1, 1, 0, 0, 0),  # naive
                updated_at=datetime.now(timezone.utc),
            )
        assert "timezone" in str(exc.value).lower()

    def test_metadata_defaults_are_not_shared(self):
        """Test 7: Each snapshot gets its own metadata dict."""
        s1 = make_snapshot()
        s2 = make_snapshot()
        s1.metadata["key"] = "value"
        assert "key" not in s2.metadata

    def test_sensitive_fields_are_not_in_schema(self):
        """Test 8: Sensitive field names are absent from the top-level schema."""
        field_names = set(AgentStateSnapshot.model_fields.keys())
        forbidden = {
            "api_key", "prompt", "response", "customer_name",
            "customer_address", "gps", "auth_token", "password",
            "secret", "phone_number",
        }
        overlap = field_names & forbidden
        assert not overlap, f"Sensitive fields found in schema: {overlap}"

    def test_forbidden_metadata_top_level_key_rejected(self):
        """Test 9: A forbidden key at the top level of metadata is rejected."""
        with pytest.raises(ValidationError) as exc:
            make_snapshot(metadata={"api_key": "sk-abc"})
        assert "forbidden" in str(exc.value).lower()

    def test_forbidden_metadata_nested_key_rejected(self):
        """Test 10: A forbidden key in a nested dict is rejected (recursive check)."""
        with pytest.raises(ValidationError) as exc:
            make_snapshot(metadata={"context": {"password": "hunter2"}})
        assert "forbidden" in str(exc.value).lower()

    def test_non_json_compatible_metadata_value_rejected(self):
        """Test 11: A non-JSON-compatible value in metadata is rejected."""
        with pytest.raises(ValidationError) as exc:
            make_snapshot(metadata={"worker_ref": object()})
        assert "json-compatible" in str(exc.value).lower() or "not json" in str(exc.value).lower() or "metadata" in str(exc.value).lower()

    def test_nested_list_with_non_json_value_rejected(self):
        """Test 12: A non-JSON-compatible value inside a nested list is rejected."""
        with pytest.raises(ValidationError):
            make_snapshot(metadata={"items": [1, 2, object()]})

    def test_valid_nested_metadata_accepted(self):
        """Test 13: Valid nested metadata (no forbidden keys) is accepted."""
        snap = make_snapshot(metadata={
            "result_status": "success",
            "tokens_used": 42,
            "steps": [1, 2, 3],
            "context": {"agent_version": "1.0", "retry_count": 0},
        })
        assert snap.metadata["result_status"] == "success"
        assert snap.metadata["context"]["retry_count"] == 0


# ===========================================================================
# Repository tests (14–25)
# ===========================================================================

class TestAgentStateRepository:

    def test_insert_state(self, repo):
        """Test 14: Upserting a new snapshot creates a record."""
        snap = make_snapshot()
        saved = repo.upsert(snap)
        assert saved.agent_id == snap.agent_id
        assert saved.tenant_id == snap.tenant_id
        assert saved.state is AgentState.IDLE

    def test_retrieve_by_tenant_and_agent_id(self, repo):
        """Test 15: Saved snapshot can be retrieved by tenant + agent_id."""
        snap = make_snapshot(tenant_id="tenant-retrieve")
        repo.upsert(snap)
        result = repo.get(agent_id=snap.agent_id, tenant_id="tenant-retrieve")
        assert result is not None
        assert result.agent_id == snap.agent_id

    def test_update_existing_state(self, repo):
        """Test 16: Upserting again updates the existing record."""
        snap = make_snapshot(state=AgentState.IDLE, tenant_id="tenant-update")
        repo.upsert(snap)

        snap.state = AgentState.ERROR
        snap.last_error = "Something went wrong"
        updated = repo.upsert(snap)

        assert updated.state is AgentState.ERROR
        assert updated.last_error == "Something went wrong"

    def test_upsert_does_not_create_duplicates(self, repo):
        """Test 17: Multiple upserts for the same agent produce exactly one record."""
        snap = make_snapshot(tenant_id="tenant-nodup")
        for state in (AgentState.IDLE, AgentState.RUNNING, AgentState.IDLE):
            snap.state = state
            repo.upsert(snap)

        count = (
            repo.db.query(AgentStateRecord)
            .filter(
                AgentStateRecord.tenant_id == "tenant-nodup",
                AgentStateRecord.agent_id == str(snap.agent_id),
            )
            .count()
        )
        assert count == 1

    def test_tenant_isolation_own_tenant_returned(self, repo):
        """Test 18: Records for tenant-A are returned for tenant-A only."""
        snap_a = make_snapshot(tenant_id="tenant-iso-a")
        snap_b = make_snapshot(tenant_id="tenant-iso-b")
        repo.upsert(snap_a)
        repo.upsert(snap_b)

        results_a = repo.list_by_tenant("tenant-iso-a")
        ids_a = {r.agent_id for r in results_a}
        assert snap_a.agent_id in ids_a
        assert snap_b.agent_id not in ids_a

    def test_wrong_tenant_returns_none(self, repo):
        """Test 19: get() with a wrong tenant_id returns None."""
        snap = make_snapshot(tenant_id="tenant-correct")
        repo.upsert(snap)
        result = repo.get(agent_id=snap.agent_id, tenant_id="tenant-wrong")
        assert result is None

    def test_list_states_by_tenant(self, repo):
        """Test 20: list_by_tenant returns all records for that tenant."""
        tenant = "tenant-list"
        snaps = [make_snapshot(tenant_id=tenant) for _ in range(3)]
        for s in snaps:
            repo.upsert(s)

        results = repo.list_by_tenant(tenant)
        returned_ids = {r.agent_id for r in results}
        for s in snaps:
            assert s.agent_id in returned_ids

    def test_delete_existing_state(self, repo):
        """Test 21: Deleting an existing record returns True."""
        snap = make_snapshot(tenant_id="tenant-del")
        repo.upsert(snap)
        deleted = repo.delete(agent_id=snap.agent_id, tenant_id="tenant-del")
        assert deleted is True
        assert repo.get(agent_id=snap.agent_id, tenant_id="tenant-del") is None

    def test_delete_missing_state_returns_false(self, repo):
        """Test 22: Deleting a non-existent record returns False."""
        result = repo.delete(agent_id=uuid4(), tenant_id="tenant-missing")
        assert result is False

    def test_database_failure_rollback_called_exactly_once(self, db):
        """Test 23: A database error triggers rollback; rollback called exactly once."""
        from sqlalchemy.exc import SQLAlchemyError

        repo = AgentStateRepository(db=db)
        snap = make_snapshot(tenant_id="tenant-rollback")

        with patch.object(
            db, "commit", side_effect=SQLAlchemyError("boom")
        ), patch.object(db, "rollback") as mock_rollback:
            with pytest.raises(SQLAlchemyError):
                repo.upsert(snap)

        mock_rollback.assert_called_once()

    def test_timestamps_update_on_upsert(self, repo):
        """Test 24: updated_at advances on re-upsert."""
        import time
        snap = make_snapshot(tenant_id="tenant-ts")
        first = repo.upsert(snap)
        time.sleep(0.01)

        snap.state = AgentState.RUNNING
        second = repo.upsert(snap)

        assert second.updated_at >= first.updated_at

    def test_agent_uuid_round_trip(self, repo):
        """Test 25: Agent UUID survives storage and retrieval unchanged."""
        original_id = uuid4()
        snap = make_snapshot(agent_id=original_id, tenant_id="tenant-uuid")
        repo.upsert(snap)
        result = repo.get(agent_id=original_id, tenant_id="tenant-uuid")
        assert result is not None
        assert result.agent_id == original_id

    def test_delete_database_failure_rollback(self, db):
        """Test: Deleting with a database error triggers rollback and raises SQLAlchemyError."""
        from sqlalchemy.exc import SQLAlchemyError
        repo = AgentStateRepository(db=db)
        agent_id = uuid4()
        tenant_id = "tenant-err"
        
        # Insert a record first so record is not None (which would return False early)
        snap = make_snapshot(agent_id=agent_id, tenant_id=tenant_id)
        repo.upsert(snap)

        with patch.object(
            db, "commit", side_effect=SQLAlchemyError("delete boom")
        ), patch.object(db, "rollback") as mock_rollback:
            with pytest.raises(SQLAlchemyError):
                repo.delete(agent_id=agent_id, tenant_id=tenant_id)
        mock_rollback.assert_called_once()

    def test_to_snapshot_null_timestamps(self):
        """Test: _to_snapshot converts None created_at/updated_at to timezone-aware UTC now."""
        record = AgentStateRecord(
            agent_id=str(uuid4()),
            tenant_id="tenant-test",
            agent_type="planning",
            agent_version="1.0",
            state="idle",
            correlation_id=None,
            last_error=None,
            created_at=None,
            updated_at=None
        )
        snap = AgentStateRepository._to_snapshot(record)
        assert snap.created_at is not None
        assert snap.created_at.tzinfo == timezone.utc
        assert snap.updated_at is not None
        assert snap.updated_at.tzinfo == timezone.utc

    def test_to_snapshot_timezone_aware(self):
        """Test: _to_snapshot preserves timezone-aware datetimes."""
        dt = datetime.now(timezone.utc)
        record = AgentStateRecord(
            agent_id=str(uuid4()),
            tenant_id="tenant-test",
            agent_type="planning",
            agent_version="1.0",
            state="idle",
            correlation_id=None,
            last_error=None,
            created_at=dt,
            updated_at=dt
        )
        snap = AgentStateRepository._to_snapshot(record)
        assert snap.created_at == dt
        assert snap.updated_at == dt
