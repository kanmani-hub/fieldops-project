"""
agent_pool.py

Local in-process registry for active FieldOps AI agent instances.

AgentPool tracks agents that are currently managed by one Python
process. It does not provide distributed discovery. Story 1.3 will
add the Redis-backed AgentRegistry for cross-process tracking.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from uuid import UUID

import structlog

from app.services.ai.FieldOpsAI.agents.base import (
    AgentState,
    BaseAgent,
)


class AgentPoolError(Exception):
    """
    Base exception raised by AgentPool.
    """


class AgentAlreadyRegisteredError(AgentPoolError):
    """
    Raised when an agent ID belongs to another registered object.
    """


class AgentNotRegisteredError(AgentPoolError):
    """
    Raised when the requested agent is not registered.
    """


class AgentPoolTenantError(AgentPoolError):
    """
    Raised when an operation attempts cross-tenant access.
    """


class AgentPool:
    """
    Thread-safe asynchronous pool of active agent instances.

    Each FastAPI process should normally own one AgentPool instance.
    The lifecycle controller registers agents after setup and removes
    them during teardown.

    The pool is tenant-aware. Callers must provide tenant_id when
    retrieving or removing an agent.
    """

    def __init__(self) -> None:
        """
        Initialize an empty local agent pool.
        """

        self._agents: dict[UUID, BaseAgent[object]] = {}
        self._lock = asyncio.Lock()

        self._logger = structlog.get_logger(
            "fieldops.ai.agent_pool"
        )

    async def register(
        self,
        agent: BaseAgent[object],
    ) -> None:
        """
        Register an initialized agent in the local pool.

        Registration is idempotent when the exact same object is
        registered more than once.

        Parameters
        ----------
        agent:
            Agent instance to register.

        Raises
        ------
        TypeError:
            If the supplied object is not a BaseAgent.
        AgentPoolError:
            If setup has not completed or the agent is terminated.
        AgentAlreadyRegisteredError:
            If the same agent ID is already associated with a
            different Python object.
        """

        if not isinstance(agent, BaseAgent):
            raise TypeError(
                "AgentPool can register only BaseAgent instances."
            )
        
        if agent.state is AgentState.TERMINATED:
            raise AgentPoolError(
                "A terminated agent cannot be registered."
            )

        if not agent.is_setup:
            raise AgentPoolError(
                "Agent setup must complete before registration."
            )


        async with self._lock:
            existing_agent = self._agents.get(
                agent.agent_id
            )

            if existing_agent is agent:
                self._logger.debug(
                    "agent_pool_registration_skipped",
                    reason="already_registered",
                    agent_id=str(agent.agent_id),
                    tenant_id=agent.tenant_id,
                )
                return

            if existing_agent is not None:
                raise AgentAlreadyRegisteredError(
                    "The agent ID is already registered "
                    "to another agent instance."
                )

            self._agents[agent.agent_id] = agent

        self._logger.info(
            "agent_pool_registered",
            agent_id=str(agent.agent_id),
            agent_type=agent.config.agent_type.value,
            tenant_id=agent.tenant_id,
            state=agent.state.value,
        )

    async def unregister(
        self,
        *,
        agent_id: UUID,
        tenant_id: str,
    ) -> BaseAgent[object]:
        """
        Remove an agent from the local pool.

        Parameters
        ----------
        agent_id:
            UUID of the agent instance.
        tenant_id:
            Tenant expected to own the agent.

        Returns
        -------
        BaseAgent
            The removed agent instance.

        Raises
        ------
        AgentNotRegisteredError:
            If the agent does not exist.
        AgentPoolTenantError:
            If the agent belongs to another tenant.
        """

        normalized_tenant_id = self._validate_tenant_id(
            tenant_id
        )

        async with self._lock:
            agent = self._agents.get(agent_id)

            if agent is None:
                raise AgentNotRegisteredError(
                    "The requested agent is not registered."
                )

            if agent.tenant_id != normalized_tenant_id:
                self._logger.warning(
                    "agent_pool_tenant_rejected",
                    operation="unregister",
                    agent_id=str(agent_id),
                )

                raise AgentPoolTenantError(
                    "The requested agent does not belong "
                    "to this tenant."
                )

            removed_agent = self._agents.pop(agent_id)

        self._logger.info(
            "agent_pool_unregistered",
            agent_id=str(agent_id),
            agent_type=removed_agent.config.agent_type.value,
            tenant_id=removed_agent.tenant_id,
            state=removed_agent.state.value,
        )

        return removed_agent

    async def get(
        self,
        *,
        agent_id: UUID,
        tenant_id: str,
    ) -> BaseAgent[object]:
        """
        Return one tenant-owned agent from the pool.

        A tenant cannot retrieve another tenant's agent.
        """

        normalized_tenant_id = self._validate_tenant_id(
            tenant_id
        )

        async with self._lock:
            agent = self._agents.get(agent_id)

            if agent is None:
                raise AgentNotRegisteredError(
                    "The requested agent is not registered."
                )

            if agent.tenant_id != normalized_tenant_id:
                self._logger.warning(
                    "agent_pool_tenant_rejected",
                    operation="get",
                    agent_id=str(agent_id),
                )

                raise AgentPoolTenantError(
                    "The requested agent does not belong "
                    "to this tenant."
                )

            return agent

    async def contains(
        self,
        *,
        agent_id: UUID,
        tenant_id: str,
    ) -> bool:
        """
        Return whether the tenant owns the registered agent.

        Cross-tenant agents are reported as absent instead of exposing
        their existence.
        """

        normalized_tenant_id = self._validate_tenant_id(
            tenant_id
        )

        async with self._lock:
            agent = self._agents.get(agent_id)

            return bool(
                agent is not None
                and agent.tenant_id == normalized_tenant_id
            )

    async def list_agents(
        self,
        *,
        tenant_id: str,
        agent_type: str | None = None,
        state: AgentState | None = None,
    ) -> Sequence[BaseAgent[object]]:
        """
        Return agents belonging to one tenant.

        Optional filters can select an agent type or lifecycle state.

        The returned tuple is a snapshot. Modifying it cannot modify
        the pool itself.
        """

        normalized_tenant_id = self._validate_tenant_id(
            tenant_id
        )

        normalized_agent_type = (
            agent_type.strip().lower()
            if agent_type is not None
            else None
        )

        if (
            normalized_agent_type is not None
            and not normalized_agent_type
        ):
            raise ValueError(
                "agent_type must not be blank."
            )

        async with self._lock:
            matching_agents = [
                agent
                for agent in self._agents.values()
                if (
                    agent.tenant_id
                    == normalized_tenant_id
                )
                and (
                    normalized_agent_type is None
                    or agent.config.agent_type.value
                    == normalized_agent_type
                )
                and (
                    state is None
                    or agent.state is state
                )
            ]

        return tuple(matching_agents)

    async def count(
        self,
        *,
        tenant_id: str | None = None,
    ) -> int:
        """
        Return the number of registered agents.

        When tenant_id is supplied, only that tenant's agents are
        counted. A tenant-free count is intended only for internal
        lifecycle and system-health use.
        """

        normalized_tenant_id = (
            self._validate_tenant_id(tenant_id)
            if tenant_id is not None
            else None
        )

        async with self._lock:
            if normalized_tenant_id is None:
                return len(self._agents)

            return sum(
                1
                for agent in self._agents.values()
                if (
                    agent.tenant_id
                    == normalized_tenant_id
                )
            )

    async def clear(self) -> tuple[BaseAgent[object], ...]:
        """
        Remove and return every registered agent.

        This method is intended for controlled application shutdown
        and tests. It does not call agent teardown automatically;
        lifecycle orchestration remains responsible for cleanup.
        """

        async with self._lock:
            agents = tuple(self._agents.values())
            self._agents.clear()

        self._logger.info(
            "agent_pool_cleared",
            agent_count=len(agents),
        )

        return agents

    @staticmethod
    def _validate_tenant_id(
        tenant_id: str,
    ) -> str:
        """
        Validate and normalize a tenant identifier.
        """

        if not isinstance(tenant_id, str):
            raise TypeError(
                "tenant_id must be a string."
            )

        normalized_tenant_id = tenant_id.strip()

        if not normalized_tenant_id:
            raise ValueError(
                "tenant_id must not be blank."
            )

        return normalized_tenant_id