"""
agent_state_manager.py

Connects BaseAgent snapshots to AgentStateRepository.

Story 1.5 — Persistent Agent State.

The state manager reads public BaseAgent properties and delegates all
persistence to AgentStateRepository.

What this component does NOT do
--------------------------------
- Modify agent state.
- Recreate or reconstruct an agent instance.
- Call agent.setup(), agent.execute(), agent.run(), or agent.teardown().
- Persist complete execution contexts, prompts, or provider responses.
- Enforce business lifecycle rules.

Tenant consistency
------------------
If an agent's tenant_id does not match the snapshot's tenant_id the
save_agent method raises ValueError before calling the repository.

Persistence failure policy
--------------------------
The state manager does NOT swallow exceptions from the repository.
If a database write fails the exception is re-raised so the caller can
decide how to handle it.  When integrated with AgentLifecycle the
caller's policy is:

    **Log and continue** — a persistence failure must not interrupt agent
    execution.  The lifecycle integration catches the exception from
    save_agent, logs it, and allows the agent to continue normally.

This policy is documented in docs/architecture/agent_persistent_state.md.
"""

from __future__ import annotations

import copy
from typing import Any
from uuid import UUID

import structlog

from app.services.ai.FieldOpsAI.agents.base import BaseAgent
from app.services.ai.FieldOpsAI.repositories.agent_state_repository import (
    AgentStateRepository,
)
from app.services.ai.FieldOpsAI.schemas.agent_state import AgentStateSnapshot


_logger = structlog.get_logger("fieldops.ai.agent_state_manager")

# Maximum length allowed for a safe error summary.
_MAX_ERROR_LENGTH = 500


class AgentStateManager:
    """
    Connect BaseAgent snapshots to AgentStateRepository.

    Parameters
    ----------
    repository:
        AgentStateRepository to delegate all database operations to.
        The repository is stored as-is regardless of its boolean value
        to avoid treating a valid dependency as absent.
    """

    def __init__(
        self,
        repository: AgentStateRepository,
    ) -> None:
        self._repository = repository

        _logger.debug(
            "agent_state_manager_initialized",
        )

    @property
    def repository(self) -> AgentStateRepository:
        """
        Return the underlying repository.
        """
        return self._repository

    # ------------------------------------------------------------------

    def save_agent(
        self,
        agent: BaseAgent[Any],
        *,
        correlation_id: str | None = None,
        last_error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AgentStateSnapshot:
        """
        Persist a safe operational snapshot of the supplied agent.

        Only public BaseAgent properties are read.  The agent's state is
        never modified.

        Caller-owned metadata is copied so the original dict is not
        mutated if the repository modifies the stored reference.

        Parameters
        ----------
        agent:
            Live BaseAgent instance.  Must not be None.
        correlation_id:
            Optional lifecycle correlation ID to associate with this
            snapshot.
        last_error:
            Optional safe error summary.  Trimmed to 500 characters.
            Must not contain secrets, full stack traces, or customer data.
        metadata:
            Optional safe operational metadata.  Copied before storage.

        Returns
        -------
        AgentStateSnapshot
            The persisted snapshot returned by the repository.

        Raises
        ------
        ValueError
            If agent is None (explicit None check).
        SQLAlchemyError
            If the repository write fails.
        """

        if agent is None:
            raise ValueError("agent must not be None.")

        # Sanitize error summary
        safe_error: str | None = None
        if last_error is not None:
            trimmed = last_error.strip()[:_MAX_ERROR_LENGTH]
            safe_error = trimmed if trimmed else None

        # Deep-copy caller-provided metadata so nested structures are
        # fully isolated — the caller's dict cannot be mutated later.
        safe_metadata: dict[str, Any] = (
            copy.deepcopy(metadata) if metadata is not None else {}
        )

        snapshot = AgentStateSnapshot.from_agent(
            agent,
            correlation_id=correlation_id,
            last_error=safe_error,
            metadata=safe_metadata,
        )

        _logger.debug(
            "agent_state_manager_saving",
            agent_id=str(agent.agent_id),
            agent_type=agent.config.agent_type.value,
            tenant_id=agent.tenant_id,
            state=agent.state.value,
        )

        return self._repository.upsert(snapshot)

    # ------------------------------------------------------------------

    def load(
        self,
        *,
        agent_id: UUID,
        tenant_id: str,
    ) -> AgentStateSnapshot | None:
        """
        Load the persisted state for (tenant_id, agent_id).

        Returns None when no record exists.  Cross-tenant records are
        never returned.

        Parameters
        ----------
        agent_id:
            UUID4 agent identifier.
        tenant_id:
            Owning tenant.
        """

        return self._repository.get(
            agent_id=agent_id,
            tenant_id=tenant_id,
        )

    # ------------------------------------------------------------------

    def delete(
        self,
        *,
        agent_id: UUID,
        tenant_id: str,
    ) -> bool:
        """
        Delete the persisted state for (tenant_id, agent_id).

        Returns True when the record existed.
        Returns False when no record was found.

        Parameters
        ----------
        agent_id:
            UUID4 agent identifier.
        tenant_id:
            Owning tenant.
        """

        return self._repository.delete(
            agent_id=agent_id,
            tenant_id=tenant_id,
        )
