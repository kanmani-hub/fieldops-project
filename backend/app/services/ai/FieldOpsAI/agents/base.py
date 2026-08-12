"""
base.py

Foundational abstract base class for all FieldOps AI agents.

Every FieldOps AI agent inherits from BaseAgent so that agent
identity, tenant isolation, lifecycle state, correlation logging,
error handling, and cleanup behave consistently.

Story 1.1 provides the local agent contract.
Later stories will add lifecycle auditing, persistent state,
Redis registration, heartbeat monitoring, and distributed messaging.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Generic, Self, TypeVar
from uuid import UUID, uuid4

import structlog

from app.context import correlation_id_ctx
from app.services.ai.FieldOpsAI.schemas.agent_config import AgentConfig


AgentOutputT = TypeVar("AgentOutputT")


class AgentState(str, Enum):
    """
    Possible lifecycle states for a FieldOps AI agent.
    """

    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    ERROR = "error"
    TERMINATED = "terminated"


class BaseAgentError(Exception):
    """
    Base exception for errors raised by BaseAgent.
    """


class AgentLifecycleError(BaseAgentError):
    """
    Raised when an operation is invalid for the current agent state.
    """


class AgentDisabledError(BaseAgentError):
    """
    Raised when execution is requested for a disabled agent.
    """


class TenantIsolationError(BaseAgentError):
    """
    Raised when execution context belongs to another tenant.
    """


class BaseAgent(ABC, Generic[AgentOutputT]):
    """
    Abstract base class for every FieldOps AI agent.

    Subclasses implement the task-specific ``run`` method.

    Callers should normally use ``execute`` rather than calling
    ``run`` directly. The execute method applies tenant validation,
    state transitions, correlation logging, and error handling.

    Parameters
    ----------
    config:
        Validated immutable agent configuration.
    """

    def __init__(
        self,
        config: AgentConfig,
    ) -> None:
        """
        Initialize a new agent instance.

        Initialization performs no Redis, database, network, or
        provider calls. External resource setup belongs in ``setup``.
        """

        self._config = config
        self._agent_id = uuid4()
        self._created_at = datetime.now(timezone.utc)
        self._state = AgentState.IDLE

        self._is_setup = False
        self._last_run_at: datetime | None = None
        self._last_error_at: datetime | None = None
        self._error_count = 0

        self._logger = structlog.get_logger(
            f"fieldops.ai.{config.agent_type.value}"
        ).bind(
            agent_id=str(self._agent_id),
            agent_type=config.agent_type.value,
            agent_version=config.agent_version,
            tenant_id=config.tenant_id,
        )

        self._logger.info(
            "agent_initialized",
            state=self._state.value,
            created_at=self._created_at.isoformat(),
        )

    @property
    def agent_id(self) -> UUID:
        """
        Return the unique UUID4 identifier for this agent instance.
        """

        return self._agent_id

    @property
    def tenant_id(self) -> str:
        """
        Return the tenant that owns this agent.
        """

        return self._config.tenant_id

    @property
    def created_at(self) -> datetime:
        """
        Return the UTC timestamp when the agent was created.
        """

        return self._created_at

    @property
    def state(self) -> AgentState:
        """
        Return the current lifecycle state.
        """

        return self._state

    @property
    def config(self) -> AgentConfig:
        """
        Return the validated immutable agent configuration.
        """

        return self._config

    @property
    def is_setup(self) -> bool:
        """
        Return whether setup completed successfully.
        """

        return self._is_setup

    async def setup(self) -> None:
        """
        Prepare the agent for execution.

        Story 1.1 performs only local setup. Later stories will extend
        this phase with registry registration, state recovery, and
        dependency validation.

        Calling setup more than once is safe.
        """

        if self._state is AgentState.TERMINATED:
            raise AgentLifecycleError(
                "A terminated agent cannot be set up again."
            )

        if self._is_setup:
            self._logger.debug(
                "agent_setup_skipped",
                reason="already_setup",
                state=self._state.value,
            )
            return

        self._is_setup = True
        self._transition_to(
            AgentState.IDLE,
            event="agent_setup_completed",
        )

    async def execute(
        self,
        context: dict[str, Any],
        *,
        correlation_id: str | None = None,
    ) -> AgentOutputT:
        """
        Execute the agent using the universal safety wrapper.

        This method:

        1. Verifies that setup completed.
        2. Verifies that the agent is enabled.
        3. Enforces tenant isolation.
        4. Creates or preserves a correlation ID.
        5. Changes the state to RUNNING.
        6. Calls the subclass implementation of ``run``.
        7. Returns the state to IDLE after success.
        8. Changes the state to ERROR after failure.

        Parameters
        ----------
        context:
            Task-specific execution data. It must include tenant_id.
        correlation_id:
            Optional request correlation ID. When it is not provided,
            the current request correlation ID is reused. If no
            correlation ID exists, a UUID4 value is generated.

        Returns
        -------
        AgentOutputT
            Task-specific result returned by the child agent.

        Raises
        ------
        AgentLifecycleError:
            If the agent is not ready for execution.
        AgentDisabledError:
            If the agent is disabled.
        TenantIsolationError:
            If the context belongs to another tenant.
        Exception:
            The original execution exception is re-raised after the
            state changes to ERROR.
        """

        self._validate_execution_allowed()
        self._validate_tenant_context(context)

        active_correlation_id = (
            correlation_id
            or correlation_id_ctx.get()
            or str(uuid4())
        )

        context_token = correlation_id_ctx.set(
            active_correlation_id
        )

        started_at = datetime.now(timezone.utc)

        self._transition_to(
            AgentState.RUNNING,
            event="agent_run_started",
            correlation_id=active_correlation_id,
        )

        try:
            result = await self.run(context)

            self._last_run_at = datetime.now(timezone.utc)

            latency_ms = (
                self._last_run_at - started_at
            ).total_seconds() * 1000

            self._transition_to(
                AgentState.IDLE,
                event="agent_run_completed",
                correlation_id=active_correlation_id,
                latency_ms=round(latency_ms, 3),
            )

            return result

        except Exception as exc:
            self._error_count += 1
            self._last_error_at = datetime.now(timezone.utc)
            self._last_run_at = self._last_error_at

            latency_ms = (
                self._last_error_at - started_at
            ).total_seconds() * 1000

            self._transition_to(
                AgentState.ERROR,
                event="agent_run_failed",
                correlation_id=active_correlation_id,
                latency_ms=round(latency_ms, 3),
                error_type=type(exc).__name__,
            )

            raise

        finally:
            correlation_id_ctx.reset(context_token)

    @abstractmethod
    async def run(
        self,
        context: dict[str, Any],
    ) -> AgentOutputT:
        """
        Execute task-specific agent logic.

        Every child agent must implement this method.

        Child agents should not manually control common lifecycle
        states. Callers should use ``execute`` so BaseAgent can apply
        lifecycle and error handling consistently.
        """

        raise NotImplementedError

    async def teardown(self) -> None:
        """
        Release resources and terminate the agent.

        Calling teardown more than once is safe.
        """

        if self._state is AgentState.TERMINATED:
            self._logger.debug(
                "agent_teardown_skipped",
                reason="already_terminated",
            )
            return

        self._is_setup = False

        self._transition_to(
            AgentState.TERMINATED,
            event="agent_teardown_completed",
        )

    async def health_check(self) -> dict[str, Any]:
        """
        Return the current safe operational health information.

        No customer context, prompts, outputs, or secrets are included.
        """

        return {
            "agent_id": str(self._agent_id),
            "agent_type": self._config.agent_type.value,
            "agent_version": self._config.agent_version,
            "tenant_id": self._config.tenant_id,
            "state": self._state.value,
            "is_setup": self._is_setup,
            "enabled": self._config.enabled,
            "created_at": self._created_at,
            "last_run_at": self._last_run_at,
            "last_error_at": self._last_error_at,
            "error_count": self._error_count,
            "healthy": (
                self._is_setup
                and self._config.enabled
                and self._state
                not in {
                    AgentState.ERROR,
                    AgentState.TERMINATED,
                }
            ),
        }

    async def __aenter__(self) -> Self:
        """
        Set up the agent when entering an async context.
        """

        await self.setup()
        return self

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: Any,
    ) -> bool:
        """
        Tear down the agent when leaving an async context.

        Exceptions are not suppressed.
        """

        if (
            exception is not None
            and self._state
            not in {
                AgentState.ERROR,
                AgentState.TERMINATED,
            }
        ):
            self._error_count += 1
            self._last_error_at = datetime.now(timezone.utc)

            self._transition_to(
                AgentState.ERROR,
                event="agent_context_failed",
                correlation_id=(
                    correlation_id_ctx.get() or None
                ),
                error_type=type(exception).__name__,
            )

        await self.teardown()

        return False

    def _validate_execution_allowed(self) -> None:
        """
        Validate whether the agent may begin execution.
        """
        if self._state is AgentState.TERMINATED:
            raise AgentLifecycleError(
                "A terminated agent cannot execute work."
            )
        
        if not self._is_setup:
            raise AgentLifecycleError(
                "Agent setup must complete before execution."
            )

        if not self._config.enabled:
            raise AgentDisabledError(
                "This agent is disabled by configuration."
            )

        if self._state is AgentState.PAUSED:
            raise AgentLifecycleError(
                "A paused agent cannot accept new work."
            )

        if self._state is AgentState.RUNNING:
            raise AgentLifecycleError(
                "This agent is already running."
            )

        if self._state is AgentState.ERROR:
            raise AgentLifecycleError(
                "An agent in ERROR state must be recovered "
                "before it can run again."
            )


    def _validate_tenant_context(
        self,
        context: dict[str, Any],
    ) -> None:
        """
        Ensure the execution context belongs to this agent's tenant.
        """

        if not isinstance(context, dict):
            raise TypeError(
                "Agent execution context must be a dictionary."
            )

        context_tenant_id = context.get("tenant_id")

        if not isinstance(context_tenant_id, str):
            raise TenantIsolationError(
                "Agent execution context must include tenant_id."
            )

        context_tenant_id = context_tenant_id.strip()

        if not context_tenant_id:
            raise TenantIsolationError(
                "Agent execution context tenant_id must not be blank."
            )

        if context_tenant_id != self._config.tenant_id:
            self._logger.warning(
                "agent_tenant_isolation_rejected",
                correlation_id=(
                    correlation_id_ctx.get() or None
                ),
            )

            raise TenantIsolationError(
                "Agent execution context does not belong "
                "to this agent's tenant."
            )

    def _transition_to(
        self,
        new_state: AgentState,
        *,
        event: str,
        **log_values: Any,
    ) -> None:
        """
        Change state and write a structured transition log.
        """

        previous_state = self._state
        self._state = new_state

        self._logger.info(
            event,
            previous_state=previous_state.value,
            current_state=new_state.value,
            **log_values,
        )