"""
lifecycle.py

Lifecycle orchestration for FieldOps AI agents.

AgentLifecycle coordinates agent setup, local pool registration,
execution timeouts, standard results, lifecycle hooks, pause/resume,
and controlled teardown.

Optional persistent state snapshots are supported through an injected
AgentStateManager. Redis registration and database lifecycle auditing
remain future implementation steps.
"""

from __future__ import annotations

import asyncio
import inspect
from collections import defaultdict
from datetime import datetime, timezone
from time import perf_counter
from typing import Any
from uuid import uuid4

import structlog

from app.context import correlation_id_ctx
from app.services.ai.FieldOpsAI.agents.base import (
    AgentState,
    BaseAgent,
)
from app.services.ai.FieldOpsAI.runtime.agent_pool import (
    AgentNotRegisteredError,
    AgentPool,
)
from app.services.ai.FieldOpsAI.schemas.agent_lifecycle import (
    LifecycleEvent,
    LifecycleEventRecord,
    LifecycleHook,
    LifecycleHookPhase,
)
from app.services.ai.FieldOpsAI.schemas.agent_result import (
    AgentResult,
    AgentResultStatus,
)


class AgentLifecycleError(Exception):
    """
    Base exception raised by AgentLifecycle.
    """


class LifecycleNotInitializedError(AgentLifecycleError):
    """
    Raised when execution is requested before initialization.
    """


class LifecycleStateError(AgentLifecycleError):
    """
    Raised when pause, resume, or teardown is invalid.
    """


class LifecycleTimeoutError(AgentLifecycleError):
    """
    Raised when setup or teardown exceeds its allowed time.
    """


class AgentLifecycle:
    """
    Coordinate the lifecycle of one FieldOps AI agent.

    Parameters
    ----------
    agent:
        BaseAgent instance managed by this lifecycle.
    pool:
        Local in-process AgentPool.
    run_timeout_seconds:
        Maximum execution duration. FieldOps agents are limited to
        30 seconds by Story 1.2.
    teardown_timeout_seconds:
        Maximum teardown duration. Story 1.2 limits teardown to
        five seconds.
    """

    MAX_RUN_TIMEOUT_SECONDS = 30.0
    MAX_TEARDOWN_TIMEOUT_SECONDS = 5.0

    def __init__(
        self,
        *,
        agent: BaseAgent[Any],
        pool: AgentPool,
        run_timeout_seconds: float | None = None,
        teardown_timeout_seconds: float = 5.0,
        state_manager: "Any | None" = None,
        health_monitor: "Any | None" = None,
    ) -> None:
        """
        Initialize lifecycle management without performing external I/O.

        Parameters
        ----------
        agent:
            BaseAgent instance managed by this lifecycle.
        pool:
            Local in-process AgentPool.
        run_timeout_seconds:
            Maximum execution duration.  Capped at 30 seconds.
        teardown_timeout_seconds:
            Maximum teardown duration.  Cannot exceed 5 seconds.
        state_manager:
            Optional AgentStateManager.  When supplied, agent state
            snapshots are persisted after setup, after execution
            (success and failure), and after teardown.

            Persistence failures are logged and do not interrupt agent
            execution (log-and-continue policy).

            When None (the default), no persistence is performed and
            existing behavior is fully preserved.
        health_monitor:
            Optional AgentHealthMonitor. When supplied, agent heartbeats
            are recorded to track operational health and liveness.
        """

        if not isinstance(agent, BaseAgent):
            raise TypeError(
                "agent must be a BaseAgent instance."
            )

        if not isinstance(pool, AgentPool):
            raise TypeError(
                "pool must be an AgentPool instance."
            )

        configured_run_timeout = (
            agent.config.timeout_seconds
            if run_timeout_seconds is None
            else run_timeout_seconds
        )

        self._validate_positive_timeout(
            configured_run_timeout,
            field_name="run_timeout_seconds",
        )

        self._validate_positive_timeout(
            teardown_timeout_seconds,
            field_name="teardown_timeout_seconds",
        )

        if configured_run_timeout > self.MAX_RUN_TIMEOUT_SECONDS:
            configured_run_timeout = (
                self.MAX_RUN_TIMEOUT_SECONDS
            )

        if (
            teardown_timeout_seconds
            > self.MAX_TEARDOWN_TIMEOUT_SECONDS
        ):
            raise ValueError(
                "teardown_timeout_seconds cannot exceed "
                "5 seconds."
            )

        self._agent = agent
        self._pool = pool
        self._state_manager = state_manager
        self._health_monitor = health_monitor

        self._run_timeout_seconds = (
            float(configured_run_timeout)
        )

        self._teardown_timeout_seconds = (
            float(teardown_timeout_seconds)
        )

        self._initialized = False
        self._teardown_started = False

        self._hooks: dict[
            tuple[LifecycleEvent, LifecycleHookPhase],
            list[LifecycleHook],
        ] = defaultdict(list)

        self._logger = structlog.get_logger(
            "fieldops.ai.lifecycle"
        ).bind(
            agent_id=str(agent.agent_id),
            agent_type=agent.config.agent_type.value,
            agent_version=agent.config.agent_version,
            tenant_id=agent.tenant_id,
        )

    @property
    def agent(self) -> BaseAgent[Any]:
        """
        Return the managed agent.
        """

        return self._agent

    @property
    def pool(self) -> AgentPool:
        """
        Return the local AgentPool.
        """

        return self._pool

    @property
    def initialized(self) -> bool:
        """
        Return whether initialization completed successfully.
        """

        return self._initialized

    @property
    def run_timeout_seconds(self) -> float:
        """
        Return the enforced run timeout.
        """

        return self._run_timeout_seconds

    @property
    def teardown_timeout_seconds(self) -> float:
        """
        Return the enforced teardown timeout.
        """

        return self._teardown_timeout_seconds

    def add_hook(
        self,
        *,
        event: LifecycleEvent,
        phase: LifecycleHookPhase,
        hook: LifecycleHook,
    ) -> None:
        """
        Register an asynchronous lifecycle callback.

        Registering the same callback for the same event and phase more
        than once is idempotent.
        """

        if not callable(hook):
            raise TypeError(
                "Lifecycle hook must be callable."
            )

        hook_key = (event, phase)
        registered_hooks = self._hooks[hook_key]

        if hook not in registered_hooks:
            registered_hooks.append(hook)

    def remove_hook(
        self,
        *,
        event: LifecycleEvent,
        phase: LifecycleHookPhase,
        hook: LifecycleHook,
    ) -> bool:
        """
        Remove a previously registered hook.

        Returns true when the hook existed.
        """

        hook_key = (event, phase)
        registered_hooks = self._hooks.get(hook_key)

        if not registered_hooks:
            return False

        try:
            registered_hooks.remove(hook)
        except ValueError:
            return False

        if not registered_hooks:
            self._hooks.pop(hook_key, None)

        return True

    async def initialize(
        self,
        *,
        correlation_id: str | None = None,
    ) -> None:
        """
        Set up the agent and register it in the local pool.

        Initialization is idempotent for an already initialized
        lifecycle.
        """

        if self._initialized:
            self._logger.debug(
                "agent_lifecycle_initialize_skipped",
                reason="already_initialized",
            )
            return

        if self._agent.state is AgentState.TERMINATED:
            raise LifecycleStateError(
                "A terminated agent cannot be initialized."
            )

        active_correlation_id = self._resolve_correlation_id(
            correlation_id
        )

        await self._emit_event(
            event=LifecycleEvent.INIT,
            phase=LifecycleHookPhase.PRE,
            previous_state=self._agent.state,
            correlation_id=active_correlation_id,
        )

        await self._emit_event(
            event=LifecycleEvent.INIT,
            phase=LifecycleHookPhase.POST,
            previous_state=self._agent.state,
            correlation_id=active_correlation_id,
        )

        previous_state = self._agent.state

        await self._emit_event(
            event=LifecycleEvent.SETUP,
            phase=LifecycleHookPhase.PRE,
            previous_state=previous_state,
            correlation_id=active_correlation_id,
        )

        try:
            await self._agent.setup()
            await self._pool.register(self._agent)

        except Exception:
            self._mark_agent_error(
                event="agent_lifecycle_setup_failed",
                correlation_id=active_correlation_id,
            )

            await self._emit_error_event(
                previous_state=previous_state,
                correlation_id=active_correlation_id,
                error_code="AGENT_SETUP_FAILED",
            )

            if (
                self._agent.is_setup
                and self._agent.state
                is not AgentState.TERMINATED
            ):
                try:
                    await self._agent.teardown()
                except Exception:
                    self._logger.exception(
                        "agent_setup_cleanup_failed",
                        correlation_id=active_correlation_id,
                    )

            raise

        self._initialized = True

        await self._record_health(
            state=AgentState.IDLE,
            correlation_id=active_correlation_id,
        )

        await self._emit_event(
            event=LifecycleEvent.SETUP,
            phase=LifecycleHookPhase.POST,
            previous_state=previous_state,
            correlation_id=active_correlation_id,
        )

        # Optional persistence after successful setup.
        if self._state_manager is not None:
            self._persist_state(
                correlation_id=active_correlation_id,
            )

        self._logger.info(
            "agent_lifecycle_initialized",
            correlation_id=active_correlation_id,
            state=self._agent.state.value,
        )

    async def execute(
        self,
        context: dict[str, Any],
        *,
        correlation_id: str | None = None,
    ) -> AgentResult:
        """
        Execute the managed agent and return a standard AgentResult.

        Unhandled agent exceptions are converted into a safe failed
        result. Run timeouts are converted into a safe timeout result.
        """

        if not self._initialized:
            raise LifecycleNotInitializedError(
                "Agent lifecycle must be initialized "
                "before execution."
            )

        if self._teardown_started:
            raise LifecycleStateError(
                "Agent teardown has already started."
            )

        active_correlation_id = self._resolve_correlation_id(
            correlation_id
        )

        previous_state = self._agent.state
        started_at = perf_counter()

        await self._emit_event(
            event=LifecycleEvent.RUN,
            phase=LifecycleHookPhase.PRE,
            previous_state=previous_state,
            correlation_id=active_correlation_id,
        )

        result: AgentResult

        await self._record_health(
            state=AgentState.RUNNING,
            correlation_id=active_correlation_id,
        )

        try:
            output = await asyncio.wait_for(
                self._agent.execute(
                    context,
                    correlation_id=active_correlation_id,
                ),
                timeout=self._run_timeout_seconds,
            )

            latency_ms = self._elapsed_ms(started_at)

            result = AgentResult(
                output=output,
                status=AgentResultStatus.SUCCESS,
                latency_ms=latency_ms,
                tokens_used=self._extract_tokens_used(
                    output
                ),
                agent_id=str(self._agent.agent_id),
                correlation_id=active_correlation_id,
            )

            await self._record_health(
                state=AgentState.IDLE,
                result_status=AgentResultStatus.SUCCESS,
                latency_ms=result.latency_ms,
                correlation_id=active_correlation_id,
            )

        except TimeoutError:
            latency_ms = self._elapsed_ms(started_at)

            self._mark_agent_error(
                event="agent_lifecycle_run_timeout",
                correlation_id=active_correlation_id,
            )

            await self._emit_error_event(
                previous_state=previous_state,
                correlation_id=active_correlation_id,
                error_code="AGENT_RUN_TIMEOUT",
                latency_ms=latency_ms,
            )

            result = AgentResult(
                status=AgentResultStatus.TIMEOUT,
                latency_ms=latency_ms,
                tokens_used=0,
                agent_id=str(self._agent.agent_id),
                correlation_id=active_correlation_id,
                error_code="AGENT_RUN_TIMEOUT",
                safe_error_message=(
                    "The agent exceeded its execution timeout."
                ),
            )

            await self._record_health(
                state=AgentState.ERROR,
                result_status=AgentResultStatus.TIMEOUT,
                safe_error_code="AGENT_EXECUTION_TIMEOUT",
                correlation_id=active_correlation_id,
            )

        except asyncio.CancelledError:
            self._mark_agent_error(
                event="agent_lifecycle_run_cancelled",
                correlation_id=active_correlation_id,
            )

            await self._emit_error_event(
                previous_state=previous_state,
                correlation_id=active_correlation_id,
                error_code="AGENT_RUN_CANCELLED",
                latency_ms=self._elapsed_ms(started_at),
            )

            raise

        except Exception:
            latency_ms = self._elapsed_ms(started_at)

            await self._emit_error_event(
                previous_state=previous_state,
                correlation_id=active_correlation_id,
                error_code="AGENT_EXECUTION_FAILED",
                latency_ms=latency_ms,
            )

            result = AgentResult(
                status=AgentResultStatus.FAILED,
                latency_ms=latency_ms,
                tokens_used=0,
                agent_id=str(self._agent.agent_id),
                correlation_id=active_correlation_id,
                error_code="AGENT_EXECUTION_FAILED",
                safe_error_message=(
                    "The agent could not complete the request."
                ),
            )

            await self._record_health(
                state=AgentState.ERROR,
                result_status=AgentResultStatus.FAILED,
                safe_error_code="AGENT_EXECUTION_FAILED",
                correlation_id=active_correlation_id,
            )

        await self._emit_event(
            event=LifecycleEvent.RUN,
            phase=LifecycleHookPhase.POST,
            previous_state=previous_state,
            correlation_id=active_correlation_id,
            latency_ms=result.latency_ms,
            error_code=result.error_code,
            metadata={
                "status": result.status.value,
                "tokens_used": result.tokens_used,
            },
        )

        # Optional persistence after execution (success, failure, or timeout).
        if self._state_manager is not None:
            self._persist_state(
                correlation_id=active_correlation_id,
                last_error=result.safe_error_message,
                metadata={
                    "result_status": result.status.value,
                    "tokens_used": result.tokens_used,
                },
            )

        return result

    async def pause(
        self,
        *,
        correlation_id: str | None = None,
    ) -> None:
        """
        Pause an IDLE agent.

        Pause prevents new executions. It does not interrupt work that
        is already running.
        """

        self._require_initialized()

        if self._agent.state is not AgentState.IDLE:
            raise LifecycleStateError(
                "Only an IDLE agent can be paused."
            )

        active_correlation_id = self._resolve_correlation_id(
            correlation_id
        )

        previous_state = self._agent.state

        await self._emit_event(
            event=LifecycleEvent.PAUSE,
            phase=LifecycleHookPhase.PRE,
            previous_state=previous_state,
            correlation_id=active_correlation_id,
        )

        self._agent._transition_to(
            AgentState.PAUSED,
            event="agent_lifecycle_paused",
            correlation_id=active_correlation_id,
        )

        await self._emit_event(
            event=LifecycleEvent.PAUSE,
            phase=LifecycleHookPhase.POST,
            previous_state=previous_state,
            correlation_id=active_correlation_id,
        )

    async def resume(
        self,
        *,
        correlation_id: str | None = None,
    ) -> None:
        """
        Resume a PAUSED agent and return it to IDLE.
        """

        self._require_initialized()

        if self._agent.state is not AgentState.PAUSED:
            raise LifecycleStateError(
                "Only a PAUSED agent can be resumed."
            )

        active_correlation_id = self._resolve_correlation_id(
            correlation_id
        )

        previous_state = self._agent.state

        await self._emit_event(
            event=LifecycleEvent.RESUME,
            phase=LifecycleHookPhase.PRE,
            previous_state=previous_state,
            correlation_id=active_correlation_id,
        )

        self._agent._transition_to(
            AgentState.IDLE,
            event="agent_lifecycle_resumed",
            correlation_id=active_correlation_id,
        )

        await self._emit_event(
            event=LifecycleEvent.RESUME,
            phase=LifecycleHookPhase.POST,
            previous_state=previous_state,
            correlation_id=active_correlation_id,
        )

    async def teardown(
        self,
        *,
        correlation_id: str | None = None,
    ) -> None:
        """
        Tear down the agent and remove it from the local pool.

        Teardown is idempotent after successful termination.
        """

        if (
            not self._initialized
            and self._agent.state
            is AgentState.TERMINATED
        ):
            return

        if self._teardown_started:
            raise LifecycleStateError(
                "Agent teardown is already in progress."
            )

        self._teardown_started = True

        active_correlation_id = self._resolve_correlation_id(
            correlation_id
        )

        previous_state = self._agent.state
        started_at = perf_counter()

        await self._emit_event(
            event=LifecycleEvent.TEARDOWN,
            phase=LifecycleHookPhase.PRE,
            previous_state=previous_state,
            correlation_id=active_correlation_id,
        )

        teardown_error: Exception | None = None

        try:
            await asyncio.wait_for(
                self._agent.teardown(),
                timeout=self._teardown_timeout_seconds,
            )

        except TimeoutError as exc:
            teardown_error = LifecycleTimeoutError(
                "Agent teardown exceeded five seconds."
            )

            self._mark_agent_error(
                event="agent_lifecycle_teardown_timeout",
                correlation_id=active_correlation_id,
            )

            await self._emit_error_event(
                previous_state=previous_state,
                correlation_id=active_correlation_id,
                error_code="AGENT_TEARDOWN_TIMEOUT",
                latency_ms=self._elapsed_ms(started_at),
            )

            self._logger.warning(
                "agent_lifecycle_teardown_timeout",
                correlation_id=active_correlation_id,
            )

            teardown_error.__cause__ = exc

        except Exception as exc:
            teardown_error = exc

            self._mark_agent_error(
                event="agent_lifecycle_teardown_failed",
                correlation_id=active_correlation_id,
            )

            await self._emit_error_event(
                previous_state=previous_state,
                correlation_id=active_correlation_id,
                error_code="AGENT_TEARDOWN_FAILED",
                latency_ms=self._elapsed_ms(started_at),
            )

        finally:
            try:
                if await self._pool.contains(
                    agent_id=self._agent.agent_id,
                    tenant_id=self._agent.tenant_id,
                ):
                    await self._pool.unregister(
                        agent_id=self._agent.agent_id,
                        tenant_id=self._agent.tenant_id,
                    )

            except AgentNotRegisteredError:
                pass

            self._initialized = False
            self._teardown_started = False

        if teardown_error is not None:
            raise teardown_error

        await self._emit_event(
            event=LifecycleEvent.TEARDOWN,
            phase=LifecycleHookPhase.POST,
            previous_state=previous_state,
            correlation_id=active_correlation_id,
            latency_ms=self._elapsed_ms(started_at),
        )

        # Optional persistence after successful teardown.
        if self._state_manager is not None:
            self._persist_state(
                correlation_id=active_correlation_id,
            )

        await self._record_health(
            state=AgentState.TERMINATED,
            correlation_id=active_correlation_id,
        )

        self._logger.info(
            "agent_lifecycle_terminated",
            correlation_id=active_correlation_id,
            state=self._agent.state.value,
        )

    async def __aenter__(self) -> "AgentLifecycle":
        """
        Initialize lifecycle management on context entry.
        """

        await self.initialize()
        return self

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: Any,
    ) -> bool:
        """
        Tear down lifecycle management on context exit.

        Exceptions from the context block are not suppressed.
        """

        await self.teardown()
        return False

    def _require_initialized(self) -> None:
        """
        Require successful lifecycle initialization.
        """

        if not self._initialized:
            raise LifecycleNotInitializedError(
                "Agent lifecycle is not initialized."
            )

    async def _emit_error_event(
        self,
        *,
        previous_state: AgentState,
        correlation_id: str,
        error_code: str,
        latency_ms: float | None = None,
    ) -> None:
        """
        Emit pre and post ERROR lifecycle hooks.
        """

        await self._emit_event(
            event=LifecycleEvent.ERROR,
            phase=LifecycleHookPhase.PRE,
            previous_state=previous_state,
            correlation_id=correlation_id,
            latency_ms=latency_ms,
            error_code=error_code,
        )

        await self._emit_event(
            event=LifecycleEvent.ERROR,
            phase=LifecycleHookPhase.POST,
            previous_state=previous_state,
            correlation_id=correlation_id,
            latency_ms=latency_ms,
            error_code=error_code,
        )

    async def _emit_event(
        self,
        *,
        event: LifecycleEvent,
        phase: LifecycleHookPhase,
        previous_state: AgentState,
        correlation_id: str | None,
        latency_ms: float | None = None,
        error_code: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        Create an event record and invoke registered callbacks.

        Hook failures are logged and do not prevent agent cleanup or
        execution. Database auditing will later use a dedicated
        persistence path.
        """

        record = LifecycleEventRecord(
            event=event,
            phase=phase,
            agent_id=str(self._agent.agent_id),
            tenant_id=self._agent.tenant_id,
            correlation_id=correlation_id,
            previous_state=previous_state,
            current_state=self._agent.state,
            occurred_at=datetime.now(timezone.utc),
            latency_ms=latency_ms,
            error_code=error_code,
            metadata=metadata or {},
        )

        hooks = tuple(
            self._hooks.get(
                (event, phase),
                (),
            )
        )

        for hook in hooks:
            try:
                hook_result = hook(record)

                if inspect.isawaitable(hook_result):
                    await hook_result

            except Exception:
                self._logger.exception(
                    "agent_lifecycle_hook_failed",
                    lifecycle_event=event.value,
                    phase=phase.value,
                    correlation_id=correlation_id,
                )

    def _mark_agent_error(
        self,
        *,
        event: str,
        correlation_id: str,
    ) -> None:
        """
        Record an externally detected lifecycle error on the agent.

        This is required for timeouts and cancellations because
        asyncio cancellation may interrupt BaseAgent before its normal
        Exception handler changes the state.
        """

        if self._agent.state in {
            AgentState.ERROR,
            AgentState.TERMINATED,
        }:
            return

        occurred_at = datetime.now(timezone.utc)

        self._agent._error_count += 1
        self._agent._last_error_at = occurred_at
        self._agent._last_run_at = occurred_at

        self._agent._transition_to(
            AgentState.ERROR,
            event=event,
            correlation_id=correlation_id,
        )

    @staticmethod
    def _extract_tokens_used(
        output: Any,
    ) -> int:
        """
        Extract non-negative token usage from common output formats.
        """

        tokens_used: Any = None

        if isinstance(output, dict):
            tokens_used = output.get("tokens_used")
        else:
            tokens_used = getattr(
                output,
                "tokens_used",
                None,
            )

        if (
            type(tokens_used) is int
            and tokens_used >= 0
        ):
            return tokens_used

        return 0

    @staticmethod
    def _resolve_correlation_id(
        supplied_correlation_id: str | None,
    ) -> str:
        """
        Resolve an explicit, existing, or generated correlation ID.
        """

        if supplied_correlation_id is not None:
            normalized_id = supplied_correlation_id.strip()

            if not normalized_id:
                raise ValueError(
                    "correlation_id must not be blank."
                )

            return normalized_id

        existing_correlation_id = correlation_id_ctx.get()

        if existing_correlation_id:
            return existing_correlation_id

        return str(uuid4())

    @staticmethod
    def _elapsed_ms(
        started_at: float,
    ) -> float:
        """
        Calculate elapsed monotonic time in milliseconds.
        """

        return round(
            (perf_counter() - started_at) * 1000,
            3,
        )

    def _persist_state(
        self,
        *,
        correlation_id: str | None = None,
        last_error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        Persist agent state via the optional state manager.

        Persistence failures are logged and never interrupt agent
        execution (log-and-continue policy).
        """

        if self._state_manager is None:
            return

        try:
            self._state_manager.save_agent(
                self._agent,
                correlation_id=correlation_id,
                last_error=last_error,
                metadata=metadata,
            )
        except Exception:
            self._logger.exception(
                "agent_lifecycle_state_persistence_failed",
                agent_id=str(self._agent.agent_id),
                tenant_id=self._agent.tenant_id,
                state=self._agent.state.value,
                correlation_id=correlation_id,
            )

    @staticmethod
    def _validate_positive_timeout(
        value: float,
        *,
        field_name: str,
    ) -> None:
        """
        Validate a positive numeric timeout.
        """

        if isinstance(value, bool) or not isinstance(
            value,
            (int, float),
        ):
            raise TypeError(
                f"{field_name} must be a number."
            )

        if value <= 0:
            raise ValueError(
                f"{field_name} must be greater than zero."
            )

    async def _record_health(
        self,
        *,
        state: AgentState,
        result_status: AgentResultStatus | None = None,
        latency_ms: float | None = None,
        safe_error_code: str | None = None,
        metadata: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> None:
        """
        Record a heartbeat with the health monitor if configured.

        Follows a log-and-continue policy so monitor failures never
        interrupt execution.
        """
        if self._health_monitor is None:
            return

        try:
            from app.services.ai.FieldOpsAI.schemas.agent_health import AgentHeartbeat

            heartbeat = AgentHeartbeat(
                agent_id=self._agent.agent_id,
                tenant_id=self._agent.tenant_id,
                agent_type=self._agent.config.agent_type,
                state=state,
                observed_at=datetime.now(timezone.utc),
                correlation_id=correlation_id,
                result_status=result_status,
                latency_ms=latency_ms,
                safe_error_code=safe_error_code,
                metadata=metadata or {},
            )

            await self._health_monitor.record_heartbeat(heartbeat)

        except Exception:
            self._logger.warning(
                "agent_lifecycle_health_record_failed",
                agent_id=str(self._agent.agent_id),
                tenant_id=self._agent.tenant_id,
                agent_type=self._agent.config.agent_type.value,
                state=state.value,
                correlation_id=correlation_id,
                safe_error_code=safe_error_code,
            )