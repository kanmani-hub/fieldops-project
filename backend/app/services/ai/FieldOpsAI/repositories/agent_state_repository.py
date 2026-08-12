"""
agent_state_repository.py

Synchronous SQLAlchemy repository for AgentStateRecord persistence.

Story 1.5 — Persistent Agent State.

Responsibilities
----------------
- Upsert agent state snapshots (create or update by tenant_id + agent_id).
- Retrieve snapshots with mandatory tenant isolation.
- List all snapshots for one tenant.
- Delete snapshots with mandatory tenant isolation.

This repository contains NO business logic and NO lifecycle logic.
It only communicates with the database.

Tenant isolation
----------------
Every read, update, and delete operation includes tenant_id in the
WHERE clause.  Cross-tenant records are never returned.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import structlog

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import AgentStateRecord
from app.services.ai.FieldOpsAI.schemas.agent_state import AgentStateSnapshot


_logger = structlog.get_logger("fieldops.ai.agent_state_repository")


class AgentStateRepository:
    """
    Repository for AgentStateRecord database operations.

    All lookups enforce tenant isolation.  The repository never returns
    records that belong to a different tenant than the one supplied.

    Parameters
    ----------
    db:
        Active SQLAlchemy session.  The caller owns the session lifetime.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------

    def upsert(
        self,
        snapshot: AgentStateSnapshot,
    ) -> AgentStateSnapshot:
        """
        Insert or update the state record for (tenant_id, agent_id).

        At most one record exists for a given tenant/agent pair.
        If a record does not exist it is created.  If it does exist its
        fields are updated and updated_at is refreshed.

        Parameters
        ----------
        snapshot:
            Validated AgentStateSnapshot to persist.

        Returns
        -------
        AgentStateSnapshot
            The persisted state (with updated_at from the database).

        Raises
        ------
        SQLAlchemyError
            On database write failure.  The session is rolled back.
        """

        agent_id_str = str(snapshot.agent_id)

        try:
            existing = (
                self.db.query(AgentStateRecord)
                .filter(
                    AgentStateRecord.tenant_id == snapshot.tenant_id,
                    AgentStateRecord.agent_id == agent_id_str,
                )
                .first()
            )

            now = datetime.now(timezone.utc)

            if existing is None:
                record = AgentStateRecord(
                    agent_id=agent_id_str,
                    agent_type=snapshot.agent_type.value,
                    tenant_id=snapshot.tenant_id,
                    agent_version=snapshot.agent_version,
                    state=snapshot.state.value,
                    correlation_id=snapshot.correlation_id,
                    last_error=snapshot.last_error,
                    safe_metadata=snapshot.metadata if snapshot.metadata else None,
                    created_at=snapshot.created_at,
                    updated_at=now,
                )
                self.db.add(record)
            else:
                existing.agent_type = snapshot.agent_type.value
                existing.agent_version = snapshot.agent_version
                existing.state = snapshot.state.value
                existing.correlation_id = snapshot.correlation_id
                existing.last_error = snapshot.last_error
                existing.safe_metadata = snapshot.metadata if snapshot.metadata else None
                existing.updated_at = now

            self.db.commit()

            # Re-read to get server-generated timestamps
            saved = (
                self.db.query(AgentStateRecord)
                .filter(
                    AgentStateRecord.tenant_id == snapshot.tenant_id,
                    AgentStateRecord.agent_id == agent_id_str,
                )
                .first()
            )

            return self._to_snapshot(saved)  # type: ignore[arg-type]

        except SQLAlchemyError:
            self.db.rollback()
            _logger.exception(
                "agent_state_repository_upsert_failed",
                agent_id=agent_id_str,
                tenant_id=snapshot.tenant_id,
            )
            raise

    # ------------------------------------------------------------------

    def get(
        self,
        *,
        agent_id: UUID,
        tenant_id: str,
    ) -> AgentStateSnapshot | None:
        """
        Return the state snapshot for (tenant_id, agent_id) or None.

        Cross-tenant records are never returned.

        Parameters
        ----------
        agent_id:
            UUID4 agent identifier.
        tenant_id:
            Owning tenant.  Must match the record exactly.
        """

        agent_id_str = str(agent_id)

        record = (
            self.db.query(AgentStateRecord)
            .filter(
                AgentStateRecord.tenant_id == tenant_id,
                AgentStateRecord.agent_id == agent_id_str,
            )
            .first()
        )

        if record is None:
            return None

        return self._to_snapshot(record)

    # ------------------------------------------------------------------

    def list_by_tenant(
        self,
        tenant_id: str,
    ) -> list[AgentStateSnapshot]:
        """
        Return all state snapshots for one tenant.

        Records from other tenants are never included.

        Parameters
        ----------
        tenant_id:
            Owning tenant.
        """

        records = (
            self.db.query(AgentStateRecord)
            .filter(AgentStateRecord.tenant_id == tenant_id)
            .order_by(AgentStateRecord.updated_at.desc())
            .all()
        )

        return [self._to_snapshot(r) for r in records]

    # ------------------------------------------------------------------

    def delete(
        self,
        *,
        agent_id: UUID,
        tenant_id: str,
    ) -> bool:
        """
        Delete the state record for (tenant_id, agent_id).

        Returns True when the record existed and was deleted.
        Returns False when no matching record was found.

        Cross-tenant records are never deleted by this method.

        Parameters
        ----------
        agent_id:
            UUID4 agent identifier.
        tenant_id:
            Owning tenant.
        """

        agent_id_str = str(agent_id)

        record = (
            self.db.query(AgentStateRecord)
            .filter(
                AgentStateRecord.tenant_id == tenant_id,
                AgentStateRecord.agent_id == agent_id_str,
            )
            .first()
        )

        if record is None:
            return False

        try:
            self.db.delete(record)
            self.db.commit()
            return True

        except SQLAlchemyError:
            self.db.rollback()
            _logger.exception(
                "agent_state_repository_delete_failed",
                agent_id=agent_id_str,
                tenant_id=tenant_id,
            )
            raise

    # ------------------------------------------------------------------

    @staticmethod
    def _to_snapshot(record: AgentStateRecord) -> AgentStateSnapshot:
        """
        Convert a database record to an AgentStateSnapshot.
        """

        from app.services.ai.FieldOpsAI.agents.base import AgentState
        from app.services.ai.FieldOpsAI.schemas.ai_task import AITask

        # Timestamps may arrive as naive datetimes from some SQLite
        # drivers; ensure UTC awareness before constructing the schema.
        def _ensure_utc(dt: datetime | None) -> datetime:
            if dt is None:
                return datetime.now(timezone.utc)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt

        created_at = _ensure_utc(record.created_at)
        updated_at = _ensure_utc(record.updated_at)

        return AgentStateSnapshot(
            agent_id=record.agent_id,  # type: ignore[arg-type]
            agent_type=AITask(record.agent_type),
            tenant_id=record.tenant_id,
            agent_version=record.agent_version,
            state=AgentState(record.state),
            correlation_id=record.correlation_id,
            last_error=record.last_error,
            metadata=record.safe_metadata if record.safe_metadata is not None else {},
            created_at=created_at,
            updated_at=updated_at,
        )
