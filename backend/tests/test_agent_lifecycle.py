"""
Tests for the FieldOps AI AgentLifecycle controller.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import pytest
from pydantic import BaseModel, ValidationError

from app.context import correlation_id_ctx
from app.services.ai.FieldOpsAI.agents.base import (
    AgentState,
    BaseAgent,
)
from app.services.ai.FieldOpsAI.runtime.agent_pool import (
    AgentPool,
    AgentNotRegisteredError,
)
from app.services.ai.FieldOpsAI.runtime.lifecycle import (
    AgentLifecycle,
    LifecycleNotInitializedError,
    LifecycleStateError,
    LifecycleTimeoutError,
)
from app.services.ai.FieldOpsAI.schemas.agent_config import (
    AgentConfig,
)
from app.services.ai.FieldOpsAI.schemas.agent_lifecycle import (
    LifecycleEvent,
    LifecycleEventRecord,
    LifecycleHookPhase,
)
from app.services.ai.FieldOpsAI.schemas.agent_result import (
    AgentResult,
    AgentResultStatus,
)
from app.services.ai.FieldOpsAI.schemas.ai_task import AITask


@pytest.fixture
def anyio_backend() -> str:
    """
    Force AnyIO tests to use Python asyncio.
    """

    return "asyncio"


class TokenOutput(BaseModel):
    """
    Object output containing provider token usage.
    """

    value: str
    tokens_used: int


class SuccessfulLifecycleAgent(
    BaseAgent[dict[str, Any]]
):
    """
    Agent that completes successfully.
    """

    async def run(
        self,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "success": True,
            "tenant_id": context["tenant_id"],
            "tokens_used": 12,
        }


class ObjectOutputAgent(BaseAgent[TokenOutput]):
    """
    Agent returning an object with token usage.
    """

    async def run(
        self,
        context: dict[str, Any],
    ) -> TokenOutput:
        return TokenOutput(
            value="completed",
            tokens_used=8,
        )


class InvalidTokenOutputAgent(
    BaseAgent[dict[str, Any]]
):
    """
    Agent returning invalid token metadata.
    """

    async def run(
        self,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "success": True,
            "tokens_used": True,
        }


class FailingLifecycleAgent(
    BaseAgent[dict[str, Any]]
):
    """
    Agent that always fails during execution.
    """

    async def run(
        self,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        raise RuntimeError(
            "Private execution failure details."
        )


class SlowLifecycleAgent(
    BaseAgent[dict[str, Any]]
):
    """
    Agent that exceeds a short test timeout.
    """

    async def run(
        self,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        await asyncio.sleep(0.1)

        return {
            "success": True,
        }


class CancellableLifecycleAgent(
    BaseAgent[dict[str, Any]]
):
    """
    Agent that waits until its task is cancelled.
    """

    def __init__(
        self,
        config: AgentConfig,
    ) -> None:
        super().__init__(config)

        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def run(
        self,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        self.started.set()
        await self.release.wait()

        return {
            "success": True,
        }


class FailingSetupAgent(
    SuccessfulLifecycleAgent
):
    """
    Agent whose setup phase fails.
    """

    async def setup(self) -> None:
        raise RuntimeError(
            "Setup dependency failed."
        )


class SlowTeardownAgent(
    SuccessfulLifecycleAgent
):
    """
    Agent whose teardown exceeds the timeout.
    """

    async def teardown(self) -> None:
        await asyncio.sleep(0.1)
        await super().teardown()


class FailingTeardownAgent(
    SuccessfulLifecycleAgent
):
    """
    Agent whose teardown raises an exception.
    """

    async def teardown(self) -> None:
        raise RuntimeError(
            "Teardown dependency failed."
        )


class FailingRegisterPool(AgentPool):
    """
    Pool that fails during registration.
    """

    async def register(
        self,
        agent: BaseAgent[object],
    ) -> None:
        raise RuntimeError(
            "Pool registration failed."
        )
class ConcurrentRemovalPool(AgentPool):
    """
    Pool that simulates another coroutine removing an agent
    between the contains and unregister operations.
    """

    async def unregister(
        self,
        *,
        agent_id: UUID,
        tenant_id: str,
    ) -> BaseAgent[object]:
        self._agents.pop(agent_id, None)

        raise AgentNotRegisteredError(
            "The agent was removed concurrently."
        )

def build_config(
    *,
    timeout_seconds: float = 30,
) -> AgentConfig:
    """
    Create a lifecycle test configuration.
    """

    return AgentConfig(
        agent_type=AITask.PLANNING,
        tenant_id="tenant-001",
        agent_version="1.0",
        timeout_seconds=timeout_seconds,
        max_retries=2,
        enabled=True,
    )


def build_lifecycle(
    *,
    agent: BaseAgent[Any] | None = None,
    pool: AgentPool | None = None,
    run_timeout_seconds: float | None = None,
    teardown_timeout_seconds: float = 5,
) -> AgentLifecycle:
    """
    Create a lifecycle test object.
    """

    active_agent = (
        agent
        if agent is not None
        else SuccessfulLifecycleAgent(
            build_config()
        )
    )

    active_pool = (
        pool
        if pool is not None
        else AgentPool()
    )

    return AgentLifecycle(
        agent=active_agent,
        pool=active_pool,
        run_timeout_seconds=run_timeout_seconds,
        teardown_timeout_seconds=(
            teardown_timeout_seconds
        ),
    )


def test_agent_result_accepts_success() -> None:
    """
    Successful results may contain task output.
    """

    result = AgentResult(
        output={"success": True},
        status=AgentResultStatus.SUCCESS,
        latency_ms=10,
        tokens_used=2,
        agent_id="agent-001",
        correlation_id="corr-001",
    )

    assert result.status is AgentResultStatus.SUCCESS
    assert result.output == {"success": True}
    assert result.error_code is None


def test_agent_result_accepts_failure() -> None:
    """
    Failed results require safe error information.
    """

    result = AgentResult(
        status=AgentResultStatus.FAILED,
        latency_ms=10,
        tokens_used=0,
        agent_id="agent-001",
        correlation_id="corr-001",
        error_code="AGENT_FAILED",
        safe_error_message=(
            "The agent could not complete the request."
        ),
    )

    assert result.output is None
    assert result.status is AgentResultStatus.FAILED


def test_success_result_rejects_error_fields() -> None:
    """
    Successful results cannot contain error details.
    """

    with pytest.raises(ValidationError):
        AgentResult(
            status=AgentResultStatus.SUCCESS,
            latency_ms=10,
            agent_id="agent-001",
            correlation_id="corr-001",
            error_code="INVALID",
        )

    with pytest.raises(ValidationError):
        AgentResult(
            status=AgentResultStatus.SUCCESS,
            latency_ms=10,
            agent_id="agent-001",
            correlation_id="corr-001",
            safe_error_message="Invalid success error.",
        )


def test_unsuccessful_result_rejects_output() -> None:
    """
    Failed results cannot also contain successful output.
    """

    with pytest.raises(ValidationError):
        AgentResult(
            output={"wrong": True},
            status=AgentResultStatus.FAILED,
            latency_ms=10,
            agent_id="agent-001",
            correlation_id="corr-001",
            error_code="FAILED",
            safe_error_message="Failed.",
        )


def test_unsuccessful_result_requires_error_fields() -> None:
    """
    Unsuccessful results require both safe error fields.
    """

    with pytest.raises(ValidationError):
        AgentResult(
            status=AgentResultStatus.TIMEOUT,
            latency_ms=10,
            agent_id="agent-001",
            correlation_id="corr-001",
        )

    with pytest.raises(ValidationError):
        AgentResult(
            status=AgentResultStatus.CANCELLED,
            latency_ms=10,
            agent_id="agent-001",
            correlation_id="corr-001",
            error_code="CANCELLED",
        )


def test_lifecycle_rejects_invalid_agent() -> None:
    """
    Lifecycle requires a BaseAgent instance.
    """

    with pytest.raises(
        TypeError,
        match="BaseAgent",
    ):
        AgentLifecycle(
            agent=object(),  # type: ignore[arg-type]
            pool=AgentPool(),
        )


def test_lifecycle_rejects_invalid_pool() -> None:
    """
    Lifecycle requires an AgentPool instance.
    """

    agent = SuccessfulLifecycleAgent(
        build_config()
    )

    with pytest.raises(
        TypeError,
        match="AgentPool",
    ):
        AgentLifecycle(
            agent=agent,
            pool=object(),  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("field_name", "invalid_value", "error_type"),
    [
        (
            "run_timeout_seconds",
            0,
            ValueError,
        ),
        (
            "run_timeout_seconds",
            -1,
            ValueError,
        ),
        (
            "run_timeout_seconds",
            True,
            TypeError,
        ),
        (
            "run_timeout_seconds",
            "30",
            TypeError,
        ),
        (
            "teardown_timeout_seconds",
            0,
            ValueError,
        ),
        (
            "teardown_timeout_seconds",
            6,
            ValueError,
        ),
    ],
)
def test_lifecycle_rejects_invalid_timeouts(
    field_name: str,
    invalid_value: Any,
    error_type: type[Exception],
) -> None:
    """
    Lifecycle timeouts must be positive and bounded.
    """

    values: dict[str, Any] = {
        "agent": SuccessfulLifecycleAgent(
            build_config()
        ),
        "pool": AgentPool(),
        "run_timeout_seconds": 30,
        "teardown_timeout_seconds": 5,
    }

    values[field_name] = invalid_value

    with pytest.raises(error_type):
        AgentLifecycle(**values)


def test_run_timeout_is_capped_at_thirty_seconds() -> None:
    """
    Configured execution timeout cannot exceed 30 seconds.
    """

    lifecycle = build_lifecycle(
        agent=SuccessfulLifecycleAgent(
            build_config(
                timeout_seconds=60,
            )
        )
    )

    assert lifecycle.run_timeout_seconds == 30
    assert lifecycle.teardown_timeout_seconds == 5


def test_lifecycle_properties() -> None:
    """
    Lifecycle should expose its managed objects safely.
    """

    agent = SuccessfulLifecycleAgent(
        build_config()
    )

    pool = AgentPool()

    lifecycle = build_lifecycle(
        agent=agent,
        pool=pool,
    )

    assert lifecycle.agent is agent
    assert lifecycle.pool is pool
    assert lifecycle.initialized is False


@pytest.mark.anyio
async def test_initialize_sets_up_and_registers_agent() -> None:
    """
    Initialization should set up and register the agent.
    """

    lifecycle = build_lifecycle()

    await lifecycle.initialize(
        correlation_id="initialize-001",
    )

    assert lifecycle.initialized is True
    assert lifecycle.agent.is_setup is True
    assert lifecycle.agent.state is AgentState.IDLE

    assert await lifecycle.pool.contains(
        agent_id=lifecycle.agent.agent_id,
        tenant_id="tenant-001",
    )


@pytest.mark.anyio
async def test_initialize_is_idempotent() -> None:
    """
    Repeated initialization should not duplicate registration.
    """

    lifecycle = build_lifecycle()

    await lifecycle.initialize()
    await lifecycle.initialize()

    assert lifecycle.initialized is True
    assert await lifecycle.pool.count() == 1


@pytest.mark.anyio
async def test_terminated_agent_cannot_initialize() -> None:
    """
    A terminated agent cannot return to the lifecycle.
    """

    agent = SuccessfulLifecycleAgent(
        build_config()
    )

    await agent.setup()
    await agent.teardown()

    lifecycle = build_lifecycle(agent=agent)

    with pytest.raises(
        LifecycleStateError,
        match="terminated agent",
    ):
        await lifecycle.initialize()


@pytest.mark.anyio
async def test_setup_failure_changes_agent_to_error() -> None:
    """
    Setup failures should leave an accurate ERROR state.
    """

    lifecycle = build_lifecycle(
        agent=FailingSetupAgent(
            build_config()
        )
    )

    with pytest.raises(
        RuntimeError,
        match="Setup dependency failed",
    ):
        await lifecycle.initialize()

    assert lifecycle.initialized is False
    assert lifecycle.agent.state is AgentState.ERROR
    assert await lifecycle.pool.count() == 0


@pytest.mark.anyio
async def test_registration_failure_cleans_up_agent() -> None:
    """
    Failed pool registration should clean up setup resources.
    """

    agent = SuccessfulLifecycleAgent(
        build_config()
    )

    lifecycle = build_lifecycle(
        agent=agent,
        pool=FailingRegisterPool(),
    )

    with pytest.raises(
        RuntimeError,
        match="Pool registration failed",
    ):
        await lifecycle.initialize()

    assert lifecycle.initialized is False
    assert agent.state is AgentState.TERMINATED


@pytest.mark.anyio
async def test_execute_requires_initialization() -> None:
    """
    Execution cannot begin before lifecycle initialization.
    """

    lifecycle = build_lifecycle()

    with pytest.raises(
        LifecycleNotInitializedError,
        match="initialized",
    ):
        await lifecycle.execute(
            {
                "tenant_id": "tenant-001",
            }
        )


@pytest.mark.anyio
async def test_successful_execution_returns_standard_result() -> None:
    """
    Successful agent output should be wrapped in AgentResult.
    """

    lifecycle = build_lifecycle()
    await lifecycle.initialize()

    result = await lifecycle.execute(
        {
            "tenant_id": "tenant-001",
        },
        correlation_id="run-001",
    )

    assert result.status is AgentResultStatus.SUCCESS
    assert result.output["success"] is True
    assert result.tokens_used == 12
    assert result.correlation_id == "run-001"
    assert result.agent_id == str(
        lifecycle.agent.agent_id
    )

    assert result.latency_ms >= 0
    assert lifecycle.agent.state is AgentState.IDLE


@pytest.mark.anyio
async def test_tokens_are_extracted_from_object_output() -> None:
    """
    Token usage may come from an output object.
    """

    lifecycle = build_lifecycle(
        agent=ObjectOutputAgent(
            build_config()
        )
    )

    await lifecycle.initialize()

    result = await lifecycle.execute(
        {
            "tenant_id": "tenant-001",
        }
    )

    assert result.status is AgentResultStatus.SUCCESS
    assert result.tokens_used == 8
    assert result.output.value == "completed"


@pytest.mark.anyio
async def test_invalid_token_metadata_defaults_to_zero() -> None:
    """
    Invalid token metadata must not corrupt the result.
    """

    lifecycle = build_lifecycle(
        agent=InvalidTokenOutputAgent(
            build_config()
        )
    )

    await lifecycle.initialize()

    result = await lifecycle.execute(
        {
            "tenant_id": "tenant-001",
        }
    )

    assert result.status is AgentResultStatus.SUCCESS
    assert result.tokens_used == 0


@pytest.mark.anyio
async def test_execution_failure_returns_safe_result() -> None:
    """
    Raw execution failures should become safe failed results.
    """

    lifecycle = build_lifecycle(
        agent=FailingLifecycleAgent(
            build_config()
        )
    )

    await lifecycle.initialize()

    result = await lifecycle.execute(
        {
            "tenant_id": "tenant-001",
        }
    )

    assert result.status is AgentResultStatus.FAILED
    assert result.output is None
    assert (
        result.error_code
        == "AGENT_EXECUTION_FAILED"
    )

    assert (
        result.safe_error_message
        == "The agent could not complete the request."
    )

    assert "Private execution failure" not in (
        result.safe_error_message
    )

    assert lifecycle.agent.state is AgentState.ERROR


@pytest.mark.anyio
async def test_execution_timeout_returns_timeout_result() -> None:
    """
    Run timeout should return a safe timeout result.
    """

    lifecycle = build_lifecycle(
        agent=SlowLifecycleAgent(
            build_config()
        ),
        run_timeout_seconds=0.01,
    )

    await lifecycle.initialize()

    result = await lifecycle.execute(
        {
            "tenant_id": "tenant-001",
        }
    )

    assert result.status is AgentResultStatus.TIMEOUT
    assert result.error_code == "AGENT_RUN_TIMEOUT"
    assert result.output is None
    assert lifecycle.agent.state is AgentState.ERROR


@pytest.mark.anyio
async def test_cancelled_execution_is_re_raised() -> None:
    """
    External cancellation must not be hidden.
    """

    agent = CancellableLifecycleAgent(
        build_config()
    )

    lifecycle = build_lifecycle(agent=agent)

    await lifecycle.initialize()

    execution_task = asyncio.create_task(
        lifecycle.execute(
            {
                "tenant_id": "tenant-001",
            }
        )
    )

    await agent.started.wait()

    execution_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await execution_task

    assert lifecycle.agent.state is AgentState.ERROR


@pytest.mark.anyio
async def test_pause_and_resume_preserve_agent() -> None:
    """
    An IDLE agent can pause and later resume.
    """

    lifecycle = build_lifecycle()
    await lifecycle.initialize()

    await lifecycle.pause(
        correlation_id="pause-001",
    )

    assert lifecycle.agent.state is AgentState.PAUSED
    assert lifecycle.initialized is True

    await lifecycle.resume(
        correlation_id="resume-001",
    )

    assert lifecycle.agent.state is AgentState.IDLE
    assert lifecycle.initialized is True


@pytest.mark.anyio
async def test_pause_requires_initialization() -> None:
    """
    Pause cannot occur before initialization.
    """

    lifecycle = build_lifecycle()

    with pytest.raises(
        LifecycleNotInitializedError,
    ):
        await lifecycle.pause()


@pytest.mark.anyio
async def test_pause_requires_idle_state() -> None:
    """
    An already-paused agent cannot be paused again.
    """

    lifecycle = build_lifecycle()
    await lifecycle.initialize()
    await lifecycle.pause()

    with pytest.raises(
        LifecycleStateError,
        match="IDLE",
    ):
        await lifecycle.pause()


@pytest.mark.anyio
async def test_resume_requires_paused_state() -> None:
    """
    An IDLE agent cannot be resumed.
    """

    lifecycle = build_lifecycle()
    await lifecycle.initialize()

    with pytest.raises(
        LifecycleStateError,
        match="PAUSED",
    ):
        await lifecycle.resume()


@pytest.mark.anyio
async def test_teardown_terminates_and_unregisters() -> None:
    """
    Teardown should terminate and remove the agent.
    """

    lifecycle = build_lifecycle()
    await lifecycle.initialize()

    await lifecycle.teardown(
        correlation_id="teardown-001",
    )

    assert lifecycle.initialized is False
    assert lifecycle.agent.state is AgentState.TERMINATED
    assert await lifecycle.pool.count() == 0


@pytest.mark.anyio
async def test_teardown_is_idempotent_after_success() -> None:
    """
    Repeated completed teardown should be safe.
    """

    lifecycle = build_lifecycle()
    await lifecycle.initialize()

    await lifecycle.teardown()
    await lifecycle.teardown()

    assert lifecycle.agent.state is AgentState.TERMINATED
    assert lifecycle.initialized is False


@pytest.mark.anyio
async def test_teardown_timeout_removes_agent() -> None:
    """
    Timed-out teardown must still remove pool registration.
    """

    lifecycle = build_lifecycle(
        agent=SlowTeardownAgent(
            build_config()
        ),
        teardown_timeout_seconds=0.01,
    )

    await lifecycle.initialize()

    with pytest.raises(
        LifecycleTimeoutError,
        match="exceeded five seconds",
    ):
        await lifecycle.teardown()

    assert lifecycle.initialized is False
    assert lifecycle.agent.state is AgentState.ERROR
    assert await lifecycle.pool.count() == 0


@pytest.mark.anyio
async def test_teardown_failure_removes_agent() -> None:
    """
    Failed teardown must still clean the local pool.
    """

    lifecycle = build_lifecycle(
        agent=FailingTeardownAgent(
            build_config()
        )
    )

    await lifecycle.initialize()

    with pytest.raises(
        RuntimeError,
        match="Teardown dependency failed",
    ):
        await lifecycle.teardown()

    assert lifecycle.initialized is False
    assert lifecycle.agent.state is AgentState.ERROR
    assert await lifecycle.pool.count() == 0


@pytest.mark.anyio
async def test_execute_rejects_started_teardown() -> None:
    """
    No execution may begin after teardown starts.
    """

    lifecycle = build_lifecycle()
    await lifecycle.initialize()

    lifecycle._teardown_started = True

    with pytest.raises(
        LifecycleStateError,
        match="teardown",
    ):
        await lifecycle.execute(
            {
                "tenant_id": "tenant-001",
            }
        )

    lifecycle._teardown_started = False
    await lifecycle.teardown()


@pytest.mark.anyio
async def test_duplicate_teardown_in_progress_is_rejected() -> None:
    """
    Only one teardown operation may run at a time.
    """

    lifecycle = build_lifecycle()
    await lifecycle.initialize()

    lifecycle._teardown_started = True

    with pytest.raises(
        LifecycleStateError,
        match="already in progress",
    ):
        await lifecycle.teardown()

    lifecycle._teardown_started = False
    await lifecycle.teardown()


def test_add_hook_rejects_non_callable() -> None:
    """
    Lifecycle hooks must be callable.
    """

    lifecycle = build_lifecycle()

    with pytest.raises(
        TypeError,
        match="callable",
    ):
        lifecycle.add_hook(
            event=LifecycleEvent.RUN,
            phase=LifecycleHookPhase.PRE,
            hook="not-callable",  # type: ignore[arg-type]
        )


@pytest.mark.anyio
async def test_run_hooks_execute_in_order() -> None:
    """
    PRE and POST hooks should receive lifecycle records.
    """

    lifecycle = build_lifecycle()
    records: list[LifecycleEventRecord] = []

    async def collect_hook(
        record: LifecycleEventRecord,
    ) -> None:
        records.append(record)

    lifecycle.add_hook(
        event=LifecycleEvent.RUN,
        phase=LifecycleHookPhase.PRE,
        hook=collect_hook,
    )

    lifecycle.add_hook(
        event=LifecycleEvent.RUN,
        phase=LifecycleHookPhase.POST,
        hook=collect_hook,
    )

    await lifecycle.initialize()

    await lifecycle.execute(
        {
            "tenant_id": "tenant-001",
        },
        correlation_id="hook-run-001",
    )

    assert [
        record.phase
        for record in records
    ] == [
        LifecycleHookPhase.PRE,
        LifecycleHookPhase.POST,
    ]

    assert all(
        record.event is LifecycleEvent.RUN
        for record in records
    )

    assert all(
        record.tenant_id == "tenant-001"
        for record in records
    )

    assert records[1].metadata["status"] == "success"
    assert records[1].metadata["tokens_used"] == 12


@pytest.mark.anyio
async def test_duplicate_hook_registration_is_idempotent() -> None:
    """
    The same hook should not run twice for one phase.
    """

    lifecycle = build_lifecycle()
    call_count = 0

    async def count_hook(
        record: LifecycleEventRecord,
    ) -> None:
        nonlocal call_count
        call_count += 1

    lifecycle.add_hook(
        event=LifecycleEvent.RUN,
        phase=LifecycleHookPhase.PRE,
        hook=count_hook,
    )

    lifecycle.add_hook(
        event=LifecycleEvent.RUN,
        phase=LifecycleHookPhase.PRE,
        hook=count_hook,
    )

    await lifecycle.initialize()

    await lifecycle.execute(
        {
            "tenant_id": "tenant-001",
        }
    )

    assert call_count == 1


def test_remove_hook_returns_correct_status() -> None:
    """
    Removing a hook reports whether it existed.
    """

    lifecycle = build_lifecycle()

    async def hook(
        record: LifecycleEventRecord,
    ) -> None:
        return None

    assert lifecycle.remove_hook(
        event=LifecycleEvent.RUN,
        phase=LifecycleHookPhase.PRE,
        hook=hook,
    ) is False

    lifecycle.add_hook(
        event=LifecycleEvent.RUN,
        phase=LifecycleHookPhase.PRE,
        hook=hook,
    )

    assert lifecycle.remove_hook(
        event=LifecycleEvent.RUN,
        phase=LifecycleHookPhase.PRE,
        hook=hook,
    ) is True

    assert lifecycle.remove_hook(
        event=LifecycleEvent.RUN,
        phase=LifecycleHookPhase.PRE,
        hook=hook,
    ) is False


@pytest.mark.anyio
async def test_synchronous_hook_is_supported() -> None:
    """
    A normal callable hook may also be executed safely.
    """

    lifecycle = build_lifecycle()
    received_events: list[LifecycleEvent] = []

    def sync_hook(
        record: LifecycleEventRecord,
    ) -> None:
        received_events.append(record.event)

    lifecycle.add_hook(
        event=LifecycleEvent.SETUP,
        phase=LifecycleHookPhase.POST,
        hook=sync_hook,  # type: ignore[arg-type]
    )

    await lifecycle.initialize()

    assert received_events == [
        LifecycleEvent.SETUP,
    ]


@pytest.mark.anyio
async def test_hook_failure_does_not_break_lifecycle() -> None:
    """
    Hook failures must not block core lifecycle cleanup.
    """

    lifecycle = build_lifecycle()

    async def failing_hook(
        record: LifecycleEventRecord,
    ) -> None:
        raise RuntimeError(
            "Audit hook unavailable."
        )

    lifecycle.add_hook(
        event=LifecycleEvent.RUN,
        phase=LifecycleHookPhase.PRE,
        hook=failing_hook,
    )

    await lifecycle.initialize()

    result = await lifecycle.execute(
        {
            "tenant_id": "tenant-001",
        }
    )

    assert result.status is AgentResultStatus.SUCCESS


@pytest.mark.anyio
async def test_explicit_correlation_id_is_normalized() -> None:
    """
    Explicit correlation IDs should be stripped.
    """

    lifecycle = build_lifecycle()
    await lifecycle.initialize()

    result = await lifecycle.execute(
        {
            "tenant_id": "tenant-001",
        },
        correlation_id="  explicit-001  ",
    )

    assert result.correlation_id == "explicit-001"


@pytest.mark.anyio
async def test_blank_correlation_id_is_rejected() -> None:
    """
    Explicit blank correlation IDs are invalid.
    """

    lifecycle = build_lifecycle()
    await lifecycle.initialize()

    with pytest.raises(
        ValueError,
        match="must not be blank",
    ):
        await lifecycle.execute(
            {
                "tenant_id": "tenant-001",
            },
            correlation_id="   ",
        )


@pytest.mark.anyio
async def test_existing_correlation_id_is_reused() -> None:
    """
    Existing request correlation should be preserved.
    """

    token = correlation_id_ctx.set(
        "existing-correlation-001"
    )

    try:
        lifecycle = build_lifecycle()
        await lifecycle.initialize()

        result = await lifecycle.execute(
            {
                "tenant_id": "tenant-001",
            }
        )

        assert (
            result.correlation_id
            == "existing-correlation-001"
        )

    finally:
        correlation_id_ctx.reset(token)


@pytest.mark.anyio
async def test_correlation_id_is_generated() -> None:
    """
    A UUID4 correlation ID should be generated when absent.
    """

    lifecycle = build_lifecycle()
    await lifecycle.initialize()

    result = await lifecycle.execute(
        {
            "tenant_id": "tenant-001",
        }
    )

    generated_id = UUID(result.correlation_id)

    assert generated_id.version == 4


@pytest.mark.anyio
async def test_async_context_manager_controls_lifecycle() -> None:
    """
    Context entry initializes and exit tears down.
    """

    lifecycle = build_lifecycle()

    async with lifecycle as active_lifecycle:
        assert active_lifecycle is lifecycle
        assert lifecycle.initialized is True
        assert await lifecycle.pool.count() == 1

        result = await lifecycle.execute(
            {
                "tenant_id": "tenant-001",
            }
        )

        assert result.status is AgentResultStatus.SUCCESS

    assert lifecycle.initialized is False
    assert lifecycle.agent.state is AgentState.TERMINATED
    assert await lifecycle.pool.count() == 0


@pytest.mark.anyio
async def test_context_manager_does_not_hide_exception() -> None:
    """
    Exceptions inside the context must reach the caller.
    """

    lifecycle = build_lifecycle()

    with pytest.raises(
        ValueError,
        match="Context operation failed",
    ):
        async with lifecycle:
            raise ValueError(
                "Context operation failed."
            )

    assert lifecycle.agent.state is AgentState.TERMINATED
    assert lifecycle.initialized is False

def test_remove_unknown_hook_from_existing_hook_list() -> None:
    """
    Removing an unknown hook should return false even when
    another hook exists for the same event and phase.
    """

    lifecycle = build_lifecycle()

    async def registered_hook(
        record: LifecycleEventRecord,
    ) -> None:
        return None

    async def unknown_hook(
        record: LifecycleEventRecord,
    ) -> None:
        return None

    lifecycle.add_hook(
        event=LifecycleEvent.RUN,
        phase=LifecycleHookPhase.PRE,
        hook=registered_hook,
    )

    removed = lifecycle.remove_hook(
        event=LifecycleEvent.RUN,
        phase=LifecycleHookPhase.PRE,
        hook=unknown_hook,
    )

    assert removed is False

def test_remove_hook_preserves_other_registered_hooks() -> None:
    """
    Removing one hook must not remove other hooks registered
    for the same lifecycle event and phase.
    """

    lifecycle = build_lifecycle()

    async def first_hook(
        record: LifecycleEventRecord,
    ) -> None:
        return None

    async def second_hook(
        record: LifecycleEventRecord,
    ) -> None:
        return None

    lifecycle.add_hook(
        event=LifecycleEvent.RUN,
        phase=LifecycleHookPhase.PRE,
        hook=first_hook,
    )

    lifecycle.add_hook(
        event=LifecycleEvent.RUN,
        phase=LifecycleHookPhase.PRE,
        hook=second_hook,
    )

    assert lifecycle.remove_hook(
        event=LifecycleEvent.RUN,
        phase=LifecycleHookPhase.PRE,
        hook=first_hook,
    ) is True

    # The second hook must still be registered.
    assert lifecycle.remove_hook(
        event=LifecycleEvent.RUN,
        phase=LifecycleHookPhase.PRE,
        hook=second_hook,
    ) is True


@pytest.mark.anyio
async def test_registration_failure_preserves_original_error_when_cleanup_fails(
) -> None:
    """
    A cleanup failure must not replace the original registration
    failure raised during initialization.
    """

    agent = FailingTeardownAgent(
        build_config()
    )

    lifecycle = build_lifecycle(
        agent=agent,
        pool=FailingRegisterPool(),
    )

    with pytest.raises(
        RuntimeError,
        match="Pool registration failed",
    ):
        await lifecycle.initialize()

    assert lifecycle.initialized is False
    assert agent.state is AgentState.ERROR


@pytest.mark.anyio
async def test_teardown_handles_agent_already_absent_from_pool() -> None:
    """
    Teardown should succeed when the agent was already removed
    from the local pool.
    """

    lifecycle = build_lifecycle()

    await lifecycle.initialize()

    await lifecycle.pool.unregister(
        agent_id=lifecycle.agent.agent_id,
        tenant_id=lifecycle.agent.tenant_id,
    )

    assert await lifecycle.pool.count() == 0

    await lifecycle.teardown()

    assert lifecycle.initialized is False
    assert lifecycle.agent.state is AgentState.TERMINATED
    assert await lifecycle.pool.count() == 0


@pytest.mark.anyio
async def test_teardown_handles_concurrent_pool_removal() -> None:
    """
    Teardown should tolerate another coroutine removing the
    agent between the pool check and removal operation.
    """

    pool = ConcurrentRemovalPool()

    lifecycle = build_lifecycle(
        pool=pool,
    )

    await lifecycle.initialize()

    assert await pool.count() == 1

    await lifecycle.teardown()

    assert lifecycle.initialized is False
    assert lifecycle.agent.state is AgentState.TERMINATED
    assert await pool.count() == 0

@pytest.mark.anyio
async def test_mark_agent_error_does_not_count_existing_error_twice(
) -> None:
    """
    Lifecycle-level error handling must not increment the error
    count when the agent is already in ERROR state.
    """

    lifecycle = build_lifecycle()

    await lifecycle.initialize()

    lifecycle.agent._error_count = 1

    lifecycle.agent._transition_to(
        AgentState.ERROR,
        event="test_existing_agent_error",
    )

    lifecycle._mark_agent_error(
        event="test_duplicate_error",
        correlation_id="duplicate-error-001",
    )

    health = await lifecycle.agent.health_check()

    assert lifecycle.agent.state is AgentState.ERROR
    assert health["error_count"] == 1


def test_persist_state_none_check() -> None:
    """Invoking _persist_state when state_manager is None returns early."""
    agent = SuccessfulLifecycleAgent(build_config())
    pool = AgentPool()
    lifecycle = AgentLifecycle(agent=agent, pool=pool, state_manager=None)
    # This should return immediately without raising or doing anything
    lifecycle._persist_state()