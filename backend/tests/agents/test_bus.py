import asyncio
import time
import math
import pytest
from unittest.mock import patch

from app.services.ai.FieldOpsAI.runtime.agent_bus import AgentBus
from app.services.ai.FieldOpsAI.schemas.agent_messages import (
    AgentAddress,
    MessageEnvelope,
    MessageType
)

# 1. Subscribe and broadcast publish.
@pytest.mark.anyio
async def test_subscribe_and_broadcast(agent_bus: AgentBus, sender_address: AgentAddress, noop_handler) -> None:
    """A subscriber receives a broadcast message with matching topic and tenant."""
    sub = await agent_bus.subscribe(
        tenant_id="tenant-001",
        topic="agent.test.broadcast",
        handler=noop_handler
    )
    
    msg = MessageEnvelope(
        sender=sender_address,
        message_type=MessageType.EVENT,
        topic="agent.test.broadcast",
        payload={"data": "hello"}
    )
    
    result = await agent_bus.publish(msg)
    assert result.delivered == 1
    assert len(noop_handler.received) == 1
    assert noop_handler.received[0].message_id == msg.message_id

# 2. Exact-address targeted routing.
@pytest.mark.anyio
async def test_targeted_routing(
    agent_bus: AgentBus,
    sender_address: AgentAddress,
    recipient_address: AgentAddress,
    noop_handler
) -> None:
    """A targeted subscriber receives the message, but other subscribers do not."""
    # Targeted subscription
    sub_target = await agent_bus.subscribe(
        tenant_id="tenant-001",
        topic="agent.test.targeted",
        handler=noop_handler,
        subscriber=recipient_address
    )
    
    # Another subscriber on same topic but not targeted
    other_noop = []
    async def other_handler(env):
        other_noop.append(env)
    
    await agent_bus.subscribe(
        tenant_id="tenant-001",
        topic="agent.test.targeted",
        handler=other_handler
    )
    
    msg = MessageEnvelope(
        sender=sender_address,
        recipient=recipient_address,
        message_type=MessageType.COMMAND,
        topic="agent.test.targeted",
        payload={"task": "do_work"}
    )
    
    result = await agent_bus.publish(msg)
    assert result.delivered == 1
    assert len(noop_handler.received) == 1
    assert len(other_noop) == 0

# 3. Cross-tenant routing isolation.
@pytest.mark.anyio
async def test_cross_tenant_routing_isolation(
    agent_bus: AgentBus,
    sender_address: AgentAddress,
    noop_handler
) -> None:
    """A subscriber on tenant-002 does not receive messages published by tenant-001."""
    # Tenant 2 subscriber
    sub_t2 = await agent_bus.subscribe(
        tenant_id="tenant-002",
        topic="agent.test.isolated",
        handler=noop_handler
    )
    
    # Publish on tenant-001
    msg = MessageEnvelope(
        sender=sender_address,
        message_type=MessageType.EVENT,
        topic="agent.test.isolated",
        payload={"val": 1}
    )
    
    result = await agent_bus.publish(msg)
    assert result.delivered == 0
    assert len(noop_handler.received) == 0

# 4. Topic isolation.
@pytest.mark.anyio
async def test_topic_isolation(agent_bus: AgentBus, sender_address: AgentAddress, noop_handler) -> None:
    """Subscribers only receive messages published to their subscribed topic."""
    await agent_bus.subscribe(
        tenant_id="tenant-001",
        topic="agent.topic.a",
        handler=noop_handler
    )
    
    msg = MessageEnvelope(
        sender=sender_address,
        message_type=MessageType.EVENT,
        topic="agent.topic.b",
        payload={"val": 1}
    )
    
    result = await agent_bus.publish(msg)
    assert result.delivered == 0
    assert len(noop_handler.received) == 0

# 5. Handler failure isolation.
@pytest.mark.anyio
async def test_handler_failure_isolation(
    agent_bus: AgentBus,
    sender_address: AgentAddress,
    failing_handler,
    noop_handler
) -> None:
    """A failing handler does not prevent other handlers from receiving the message."""
    await agent_bus.subscribe(
        tenant_id="tenant-001",
        topic="agent.test.failure",
        handler=failing_handler
    )
    await agent_bus.subscribe(
        tenant_id="tenant-001",
        topic="agent.test.failure",
        handler=noop_handler
    )
    
    msg = MessageEnvelope(
        sender=sender_address,
        message_type=MessageType.EVENT,
        topic="agent.test.failure"
    )
    
    result = await agent_bus.publish(msg)
    # Both subscriptions matched, even if one failed
    assert result.matched_subscribers == 2
    assert result.delivered == 1
    assert result.failed == 1
    assert len(noop_handler.received) == 1

# 6. Handler timeout isolation.
@pytest.mark.anyio
async def test_handler_timeout_isolation(
    sender_address: AgentAddress,
    noop_handler
) -> None:
    """A slow handler timing out does not interrupt delivery to other handlers."""
    # Set small handler timeout of 0.1s
    bus = AgentBus(handler_timeout_seconds=0.1)
    
    async def very_slow_handler(env):
        await asyncio.sleep(0.5)
        
    await bus.subscribe(
        tenant_id="tenant-001",
        topic="agent.test.timeout",
        handler=very_slow_handler
    )
    await bus.subscribe(
        tenant_id="tenant-001",
        topic="agent.test.timeout",
        handler=noop_handler
    )
    
    msg = MessageEnvelope(
        sender=sender_address,
        message_type=MessageType.EVENT,
        topic="agent.test.timeout"
    )
    
    result = await bus.publish(msg)
    assert result.matched_subscribers == 2
    assert result.delivered == 1
    assert result.failed == 1 # The slow one timed out
    assert len(noop_handler.received) == 1

# 7. Synchronous handler does not block the event loop.
@pytest.mark.anyio
async def test_sync_handler_non_blocking(
    sender_address: AgentAddress,
) -> None:
    """
    A slow synchronous handler runs in a worker thread and does
    not block the asyncio event loop.
    """

    import threading

    bus = AgentBus(
        handler_timeout_seconds=1.0
    )

    handler_started = threading.Event()

    def blocking_handler(
        envelope: MessageEnvelope,
    ) -> None:
        handler_started.set()
        time.sleep(0.30)

    await bus.subscribe(
        tenant_id="tenant-001",
        topic="agent.test.sync",
        handler=blocking_handler,
    )

    message = MessageEnvelope(
        sender=sender_address,
        message_type=MessageType.EVENT,
        topic="agent.test.sync",
    )

    started_at = time.perf_counter()

    publish_task = asyncio.create_task(
        bus.publish(message)
    )

    started = await asyncio.to_thread(
        handler_started.wait,
        1.0,
    )

    assert started is True

    await asyncio.sleep(0.02)

    elapsed = time.perf_counter() - started_at

    # If the handler ran on the event-loop thread, this coroutine
    # could not resume until approximately 0.30 seconds passed.
    assert elapsed < 0.15

    result = await publish_task

    assert result.delivered == 1
# 8. Each handler receives an isolated deep copy.
@pytest.mark.anyio
async def test_deep_copy_payload_isolation(agent_bus: AgentBus, sender_address: AgentAddress) -> None:
    """Handlers receive independent copies of the message payload so they can't mutate each other's data."""
    received_payloads = []
    
    async def handler_one(env: MessageEnvelope) -> None:
        env.payload["data"] = "mutated_by_one"
        received_payloads.append(env.payload)
        
    async def handler_two(env: MessageEnvelope) -> None:
        received_payloads.append(env.payload)
        
    await agent_bus.subscribe(
        tenant_id="tenant-001",
        topic="agent.test.copy",
        handler=handler_one
    )
    await agent_bus.subscribe(
        tenant_id="tenant-001",
        topic="agent.test.copy",
        handler=handler_two
    )
    
    msg = MessageEnvelope(
        sender=sender_address,
        message_type=MessageType.EVENT,
        topic="agent.test.copy",
        payload={"data": "original"}
    )
    
    await agent_bus.publish(msg)
    # Check that handler_two received the original payload, not mutated
    assert len(received_payloads) == 2
    assert "mutated_by_one" in [p["data"] for p in received_payloads]
    assert "original" in [p["data"] for p in received_payloads]

# 9. Unsubscribe prevents later deliveries.
@pytest.mark.anyio
async def test_unsubscribe_prevents_delivery(agent_bus: AgentBus, sender_address: AgentAddress, noop_handler) -> None:
    """Once a subscription is removed, it no longer receives messages."""
    sub = await agent_bus.subscribe(
        tenant_id="tenant-001",
        topic="agent.test.unsub",
        handler=noop_handler
    )
    
    msg = MessageEnvelope(
        sender=sender_address,
        message_type=MessageType.EVENT,
        topic="agent.test.unsub"
    )
    
    await agent_bus.publish(msg)
    assert len(noop_handler.received) == 1
    
    # Unsubscribe
    unsubscribed = await agent_bus.unsubscribe(sub.subscription_id)
    assert unsubscribed is True
    
    # Publish again
    await agent_bus.publish(msg)
    assert len(noop_handler.received) == 1 # Still 1

# 10. In-process publish performance meets the bus SLA.

# 10. In-process publish performance meets the bus SLA.
@pytest.mark.performance
@pytest.mark.anyio
async def test_publish_performance_sla(
    agent_bus: AgentBus,
    sender_address: AgentAddress,
) -> None:
    """
    AgentBus routing and async-handler delivery must complete
    within the 10 ms p95 SLA.

    Log-output processing is excluded because console and pytest
    capture speed is unrelated to in-process message routing.
    """

    received_count = 0

    async def fast_handler(
        envelope: MessageEnvelope,
    ) -> None:
        nonlocal received_count
        received_count += 1

    await agent_bus.subscribe(
        tenant_id="tenant-001",
        topic="agent.perf.test",
        handler=fast_handler,
    )

    message = MessageEnvelope(
        sender=sender_address,
        message_type=MessageType.EVENT,
        topic="agent.perf.test",
    )

    class NoOpLogger:
        """Logger used only during the timed performance section."""

        def debug(
            self,
            *args: object,
            **kwargs: object,
        ) -> None:
            return None

        def info(
            self,
            *args: object,
            **kwargs: object,
        ) -> None:
            return None

        def warning(
            self,
            *args: object,
            **kwargs: object,
        ) -> None:
            return None

        def exception(
            self,
            *args: object,
            **kwargs: object,
        ) -> None:
            return None

    durations: list[int] = []

    # Patch only the log sink. The real AgentBus routing,
    # locking, copying and handler execution still run.
    with patch(
        "app.services.ai.FieldOpsAI.runtime.agent_bus._logger",
        new=NoOpLogger(),
    ):
        # Warm up the event loop and Pydantic copy path.
        warmup_iterations = 20

        for _ in range(warmup_iterations):
            result = await agent_bus.publish(message)

            assert result.delivered == 1
            assert result.failed == 0

        # Use enough iterations so one Windows scheduling pause
        # does not make the p95 result unreliable.
        measured_iterations = 100

        for _ in range(measured_iterations):
            started_at = time.perf_counter_ns()

            result = await agent_bus.publish(message)

            elapsed_ns = (
                time.perf_counter_ns()
                - started_at
            )

            durations.append(elapsed_ns)

            assert result.delivered == 1
            assert result.failed == 0

    assert received_count == (
        warmup_iterations
        + measured_iterations
    )

    durations.sort()

    p95_index = (
        math.ceil(len(durations) * 0.95)
        - 1
    )

    p95_ns = durations[p95_index]
    p95_ms = p95_ns / 1_000_000.0

    assert p95_ms < 10.0, (
        f"Bus publish p95 latency is "
        f"{p95_ms:.3f} ms, which exceeds "
        f"the 10 ms SLA limit."
    )