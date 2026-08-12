"""
test_message_protocols.py

Integration tests for FieldOps message-flow protocols.

Covered protocols
-----------------
- RequestResponseProtocol
- AsyncFireForgetProtocol
- EventDrivenProtocol

Covered requirements
--------------------
- Five-second synchronous timeout configuration
- Thirty-second asynchronous timeout configuration
- No event timeout
- Immediate asynchronous ACK
- Exponential-backoff retries
- Graceful timeout handling
- Dead-letter routing
- Multiple event subscribers
- Subscriber isolation
- Correlation ID preservation
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest

from app.core.protocols import (
    AsyncFireForgetProtocol,
    EventDrivenProtocol,
    RequestResponseProtocol,
)
from app.services.ai.FieldOpsAI.schemas.agent_messages import (
    AgentAddress,
    BaseMessage,
    CommandMessage,
    ErrorMessage,
    EventMessage,
    QueryMessage,
    ResponseMessage,
)


pytestmark = pytest.mark.anyio


# ==========================================================
# AnyIO Configuration
# ==========================================================


@pytest.fixture
def anyio_backend() -> str:
    """
    Force AnyIO tests to use asyncio.

    The protocol implementation uses asyncio tasks,
    events, cancellation, and timeout handling.
    """

    return "asyncio"


# ==========================================================
# Fast Test Protocols
# ==========================================================


class FastRequestResponseProtocol(
    RequestResponseProtocol
):
    """
    Request/response protocol with short test delays.

    Production delays remain:

    - 1 second
    - 2 seconds
    - 4 seconds
    """

    RETRY_DELAYS = (
        0.001,
        0.002,
        0.004,
    )


class FastTimeoutRequestResponseProtocol(
    FastRequestResponseProtocol
):
    """
    Uses a short timeout so the test suite does not
    wait for five real seconds.
    """

    TIMEOUT_SECONDS = 0.03


class FastAsyncFireForgetProtocol(
    AsyncFireForgetProtocol
):
    """
    Async protocol with short test retry delays.
    """

    RETRY_DELAYS = (
        0.001,
        0.002,
        0.004,
    )


class FastTimeoutAsyncFireForgetProtocol(
    FastAsyncFireForgetProtocol
):
    """
    Async protocol with a short test timeout.
    """

    TIMEOUT_SECONDS = 0.03


class FastEventDrivenProtocol(
    EventDrivenProtocol
):
    """
    Event protocol with short test retry delays.
    """

    RETRY_DELAYS = (
        0.001,
        0.002,
        0.004,
    )


# ==========================================================
# Fixtures
# ==========================================================


@pytest.fixture
def planning_address() -> AgentAddress:
    """
    Address representing the Planning Agent.
    """

    return AgentAddress(
        agent_type="planning",
        agent_id="planner-01",
        tenant_id="tenant-001",
    )


@pytest.fixture
def dispatch_address() -> AgentAddress:
    """
    Address representing the Dispatch Agent.
    """

    return AgentAddress(
        agent_type="dispatch",
        agent_id="dispatcher-01",
        tenant_id="tenant-001",
    )


@pytest.fixture
def communication_address() -> AgentAddress:
    """
    Address representing the Communication Agent.
    """

    return AgentAddress(
        agent_type="communication",
        agent_id="communication-01",
        tenant_id="tenant-001",
    )


@pytest.fixture
def query_message(
    planning_address: AgentAddress,
    dispatch_address: AgentAddress,
) -> QueryMessage:
    """
    Valid synchronous query message.
    """

    return QueryMessage(
        sender=planning_address,
        recipient=dispatch_address,
        correlation_id=str(uuid4()),
        payload={
            "job_id": "JOB-1001",
            "query": "GET_TECHNICIAN_AVAILABILITY",
        },
    )


@pytest.fixture
def command_message(
    planning_address: AgentAddress,
    communication_address: AgentAddress,
) -> CommandMessage:
    """
    Valid asynchronous command message.
    """

    return CommandMessage(
        sender=planning_address,
        recipient=communication_address,
        correlation_id=str(uuid4()),
        payload={
            "job_id": "JOB-1001",
            "action": "SEND_ASSIGNMENT_NOTIFICATION",
        },
    )


@pytest.fixture
def event_message(
    dispatch_address: AgentAddress,
    communication_address: AgentAddress,
) -> EventMessage:
    """
    Valid event message.
    """

    return EventMessage(
        sender=dispatch_address,
        recipient=communication_address,
        correlation_id=str(uuid4()),
        payload={
            "event_name": "TECHNICIAN_ACCEPTED",
            "job_id": "JOB-1001",
            "technician_id": "TECH-101",
        },
    )


# ==========================================================
# Helper Functions
# ==========================================================


def build_success_response(
    request: BaseMessage,
    *,
    correlation_id: str | None = None,
    status: str = "SUCCESS",
) -> ResponseMessage:
    """
    Build a successful response for a request.
    """

    return ResponseMessage(
        sender=request.recipient,
        recipient=request.sender,
        correlation_id=(
            correlation_id
            or request.correlation_id
        ),
        contract_version=request.contract_version,
        payload={
            "status": status,
            "job_id": request.payload.get(
                "job_id"
            ),
        },
    )


# ==========================================================
# Timeout Matrix Tests
# ==========================================================


async def test_protocol_timeout_matrix() -> None:
    """
    Verify the Story 0.3 timeout matrix.
    """

    request_protocol = RequestResponseProtocol()
    async_protocol = AsyncFireForgetProtocol()
    event_protocol = EventDrivenProtocol()

    assert request_protocol.timeout == 5.0
    assert async_protocol.timeout == 30.0
    assert event_protocol.timeout is None


# ==========================================================
# Request/Response Tests
# ==========================================================


async def test_request_response_completes_within_five_seconds(
    query_message: QueryMessage,
) -> None:
    """
    A successful request must complete before the
    five-second synchronous deadline.
    """

    protocol = RequestResponseProtocol()

    async def handler(
        message: BaseMessage,
    ) -> BaseMessage:
        await asyncio.sleep(
            0.01
        )

        return build_success_response(
            message
        )

    started_at = time.perf_counter()

    response = await protocol.execute(
        message=query_message,
        handler=handler,
    )

    elapsed = (
        time.perf_counter()
        - started_at
    )

    assert isinstance(
        response,
        ResponseMessage,
    )

    assert response.payload["status"] == "SUCCESS"

    assert elapsed < 5.0


async def test_request_response_timeout_is_handled_gracefully(
    query_message: QueryMessage,
) -> None:
    """
    A timed-out synchronous request must return an
    ErrorMessage instead of exposing a raw exception.
    """

    protocol = (
        FastTimeoutRequestResponseProtocol()
    )

    async def slow_handler(
        message: BaseMessage,
    ) -> BaseMessage:
        await asyncio.sleep(
            0.20
        )

        return build_success_response(
            message
        )

    response = await protocol.execute(
        message=query_message,
        handler=slow_handler,
    )

    assert isinstance(
        response,
        ErrorMessage,
    )

    assert (
        response.error_code
        == "REQUEST_TIMEOUT"
    )

    assert (
        response.correlation_id
        == query_message.correlation_id
    )


async def test_request_response_retries_then_succeeds(
    query_message: QueryMessage,
) -> None:
    """
    A transient handler failure should trigger retries.

    This handler fails twice and succeeds on the third
    transport call.
    """

    protocol = FastRequestResponseProtocol()

    attempt_count = 0

    async def unstable_handler(
        message: BaseMessage,
    ) -> BaseMessage:
        nonlocal attempt_count

        attempt_count += 1

        if attempt_count < 3:
            raise ConnectionError(
                "Temporary downstream failure."
            )

        return build_success_response(
            message,
            status="RECOVERED",
        )

    response = await protocol.execute(
        message=query_message,
        handler=unstable_handler,
    )

    assert isinstance(
        response,
        ResponseMessage,
    )

    assert response.payload["status"] == "RECOVERED"

    # Initial call + two retries.
    assert attempt_count == 3


async def test_request_response_retry_exhaustion_returns_error(
    query_message: QueryMessage,
) -> None:
    """
    Three retries after the initial attempt should
    produce a controlled ErrorMessage when all calls fail.
    """

    protocol = FastRequestResponseProtocol()

    attempt_count = 0

    async def failing_handler(
        message: BaseMessage,
    ) -> BaseMessage:
        nonlocal attempt_count

        attempt_count += 1

        raise ConnectionError(
            "Service remains unavailable."
        )

    response = await protocol.execute(
        message=query_message,
        handler=failing_handler,
    )

    assert isinstance(
        response,
        ErrorMessage,
    )

    assert (
        response.error_code
        == "REQUEST_FAILED"
    )

    # One initial attempt plus three retries.
    assert attempt_count == 4


async def test_request_response_preserves_correlation_id(
    query_message: QueryMessage,
) -> None:
    """
    The protocol must replace an incorrect response
    correlation ID with the original request ID.
    """

    protocol = RequestResponseProtocol()

    async def handler(
        message: BaseMessage,
    ) -> BaseMessage:
        return build_success_response(
            message,
            correlation_id="incorrect-id",
        )

    response = await protocol.execute(
        message=query_message,
        handler=handler,
    )

    assert (
        response.correlation_id
        == query_message.correlation_id
    )


# ==========================================================
# Async Fire-and-Forget Tests
# ==========================================================


async def test_async_returns_ack_before_processing_finishes(
    command_message: CommandMessage,
) -> None:
    """
    The sender must receive an ACK while the handler
    is still blocked in background processing.
    """

    protocol = FastAsyncFireForgetProtocol()

    handler_started = asyncio.Event()
    allow_handler_to_finish = asyncio.Event()
    handler_finished = asyncio.Event()

    async def background_handler(
        message: BaseMessage,
    ) -> BaseMessage:
        handler_started.set()

        await allow_handler_to_finish.wait()

        handler_finished.set()

        return build_success_response(
            message,
            status="BACKGROUND_COMPLETED",
        )

    acknowledgement = await protocol.execute(
        message=command_message,
        handler=background_handler,
    )

    assert isinstance(
        acknowledgement,
        ResponseMessage,
    )

    assert (
        acknowledgement.payload["status"]
        == "ACK"
    )

    assert acknowledgement.payload["accepted"] is True

    # Wait until the background handler has started.
    await asyncio.wait_for(
        handler_started.wait(),
        timeout=1.0,
    )

    # ACK has already returned, but processing remains active.
    assert handler_finished.is_set() is False

    allow_handler_to_finish.set()

    await protocol.wait_for_pending()

    assert handler_finished.is_set() is True

    assert protocol.pending_task_count == 0


async def test_async_callback_preserves_correlation_id(
    command_message: CommandMessage,
) -> None:
    """
    The final callback result must carry the original
    command correlation ID.
    """

    protocol = FastAsyncFireForgetProtocol()

    callback_results: list[
        BaseMessage
    ] = []

    async def handler(
        message: BaseMessage,
    ) -> BaseMessage:
        return build_success_response(
            message,
            correlation_id="wrong-correlation-id",
        )

    async def callback(
        result: BaseMessage,
    ) -> None:
        callback_results.append(
            result
        )

    acknowledgement = await protocol.execute(
        message=command_message,
        handler=handler,
        callback=callback,
    )

    await protocol.wait_for_pending()

    assert (
        acknowledgement.correlation_id
        == command_message.correlation_id
    )

    assert len(callback_results) == 1

    assert (
        callback_results[0].correlation_id
        == command_message.correlation_id
    )


async def test_async_failure_reaches_dead_letter_queue(
    command_message: CommandMessage,
) -> None:
    """
    Failed background processing must be retried and
    then routed to dead-letter handling.
    """

    published_dead_letters: list[
        ErrorMessage
    ] = []

    async def dead_letter_publisher(
        error: ErrorMessage,
    ) -> None:
        published_dead_letters.append(
            error
        )

    protocol = FastAsyncFireForgetProtocol(
        dead_letter_publisher=(
            dead_letter_publisher
        )
    )

    attempt_count = 0

    async def failing_handler(
        message: BaseMessage,
    ) -> BaseMessage:
        nonlocal attempt_count

        attempt_count += 1

        raise ConnectionError(
            "Notification service unavailable."
        )

    acknowledgement = await protocol.execute(
        message=command_message,
        handler=failing_handler,
    )

    assert (
        acknowledgement.payload["status"]
        == "ACK"
    )

    await protocol.wait_for_pending()

    # Initial attempt plus three retries.
    assert attempt_count == 4

    assert len(protocol.dead_letters) == 1

    assert len(published_dead_letters) == 1

    error = protocol.dead_letters[0]

    assert (
        error.error_code
        == "ASYNC_PROCESSING_FAILED"
    )

    assert (
        error.correlation_id
        == command_message.correlation_id
    )


async def test_async_timeout_reaches_dead_letter_queue(
    command_message: CommandMessage,
) -> None:
    """
    A background operation exceeding its timeout must
    create a dead-letter error.
    """

    protocol = (
        FastTimeoutAsyncFireForgetProtocol()
    )

    async def slow_handler(
        message: BaseMessage,
    ) -> BaseMessage:
        await asyncio.sleep(
            0.20
        )

        return build_success_response(
            message
        )

    acknowledgement = await protocol.execute(
        message=command_message,
        handler=slow_handler,
    )

    assert (
        acknowledgement.payload["status"]
        == "ACK"
    )

    await protocol.wait_for_pending()

    assert len(protocol.dead_letters) == 1

    error = protocol.dead_letters[0]

    assert (
        error.error_code
        == "ASYNC_PROCESSING_TIMEOUT"
    )

    assert (
        error.correlation_id
        == command_message.correlation_id
    )


# ==========================================================
# Event-Driven Tests
# ==========================================================


async def test_event_is_published_to_all_subscribers(
    event_message: EventMessage,
) -> None:
    """
    Every registered subscriber must receive the event.
    """

    protocol = EventDrivenProtocol()

    received_by: list[str] = []

    async def monitoring_subscriber(
        message: BaseMessage,
    ) -> None:
        received_by.append(
            "monitoring"
        )

    async def communication_subscriber(
        message: BaseMessage,
    ) -> None:
        received_by.append(
            "communication"
        )

    async def analytics_subscriber(
        message: BaseMessage,
    ) -> None:
        received_by.append(
            "analytics"
        )

    protocol.subscribe(
        name="monitoring",
        subscriber=monitoring_subscriber,
    )

    protocol.subscribe(
        name="communication",
        subscriber=communication_subscriber,
    )

    protocol.subscribe(
        name="analytics",
        subscriber=analytics_subscriber,
    )

    result = await protocol.execute(
        event_message
    )

    assert result is None

    assert set(received_by) == {
        "monitoring",
        "communication",
        "analytics",
    }


async def test_event_preserves_correlation_id_for_all_subscribers(
    event_message: EventMessage,
) -> None:
    """
    Every subscriber must receive the original event
    correlation ID.
    """

    protocol = EventDrivenProtocol()

    received_ids: list[str] = []

    async def subscriber_one(
        message: BaseMessage,
    ) -> None:
        received_ids.append(
            message.correlation_id
        )

    async def subscriber_two(
        message: BaseMessage,
    ) -> None:
        received_ids.append(
            message.correlation_id
        )

    protocol.subscribe(
        name="subscriber-one",
        subscriber=subscriber_one,
    )

    protocol.subscribe(
        name="subscriber-two",
        subscriber=subscriber_two,
    )

    await protocol.execute(
        event_message
    )

    assert received_ids == [
        event_message.correlation_id,
        event_message.correlation_id,
    ]


async def test_failing_event_subscriber_does_not_stop_others(
    event_message: EventMessage,
) -> None:
    """
    A permanently failing subscriber must be retried,
    logged, and dropped without affecting successful
    subscribers.
    """

    protocol = FastEventDrivenProtocol()

    failing_attempt_count = 0
    successful_deliveries = 0

    async def failing_subscriber(
        message: BaseMessage,
    ) -> None:
        nonlocal failing_attempt_count

        failing_attempt_count += 1

        raise ConnectionError(
            "Subscriber unavailable."
        )

    async def successful_subscriber(
        message: BaseMessage,
    ) -> None:
        nonlocal successful_deliveries

        successful_deliveries += 1

    protocol.subscribe(
        name="failing-subscriber",
        subscriber=failing_subscriber,
    )

    protocol.subscribe(
        name="successful-subscriber",
        subscriber=successful_subscriber,
    )

    # The exception from the failing subscriber must not
    # propagate back to the publisher.
    result = await protocol.execute(
        event_message
    )

    assert result is None

    # Initial attempt plus three retries.
    assert failing_attempt_count == 4

    # The independent subscriber still succeeds.
    assert successful_deliveries == 1


async def test_event_subscriber_retries_then_succeeds(
    event_message: EventMessage,
) -> None:
    """
    A transient subscriber failure should recover through
    the shared retry policy.
    """

    protocol = FastEventDrivenProtocol()

    attempt_count = 0

    async def unstable_subscriber(
        message: BaseMessage,
    ) -> None:
        nonlocal attempt_count

        attempt_count += 1

        if attempt_count < 3:
            raise ConnectionError(
                "Temporary event consumer failure."
            )

    protocol.subscribe(
        name="unstable-subscriber",
        subscriber=unstable_subscriber,
    )

    await protocol.execute(
        event_message
    )

    # Initial attempt plus two retries.
    assert attempt_count == 3


async def test_event_protocol_rejects_non_event_message(
    command_message: CommandMessage,
) -> None:
    """
    EventDrivenProtocol must reject COMMAND messages.
    """

    protocol = EventDrivenProtocol()

    with pytest.raises(
        ValueError,
        match="accepts only EVENT messages",
    ):
        await protocol.execute(
            command_message
        )