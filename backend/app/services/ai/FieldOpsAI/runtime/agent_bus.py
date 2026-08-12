"""
agent_bus.py

Asynchronous in-process publish/subscribe AgentBus.

Story 1.6 — Agent Communication Bus.

Responsibilities
----------------
- Subscribe handlers to (tenant, topic) routing keys.
- Publish MessageEnvelope messages to matching subscribers.
- Enforce tenant isolation — handlers only receive same-tenant messages.
- Apply independent per-handler timeout.
- Isolate handler failures so one bad handler does not affect others.
- Provide each handler with an isolated deep copy of the message.

What this component does NOT do
--------------------------------
- Store or execute live agent instances.
- Connect to Redis, Kafka, or any external broker.
- Persist messages to the database.
- Retry failed deliveries.
- Implement request/reply orchestration.

Separation of concerns
-----------------------
- AgentRegistry  — stores agent definitions.
- AgentPool      — stores live agent instances.
- AgentLifecycle — manages execution flow.
- AgentStateManager — persists runtime snapshots.
- AgentBus       — transports validated messages between handlers.

Thread/async safety
-------------------
asyncio.Lock guards subscription records during mutation and snapshot.
The lock is released BEFORE any handler is called or awaited.

Synchronous timeout and worker threads
--------------------------------------
Sync handlers are run in a separate thread pool using asyncio.to_thread.
If a sync handler times out or raises an exception:
- The bus stops waiting and reports the failure.
- Worker threads executing sync handlers cannot be forcibly terminated and may
  finish execution later.
- It is highly recommended that sync handlers are idempotent to avoid side-effects
  when timeouts occur.
- Async handlers are cancelled normally.

Privacy
-------
Payload and metadata are never logged.
Only message_id, tenant_id, topic, sender, recipient, subscription_id,
and error_code are written to logs.
"""

from __future__ import annotations

import asyncio
import inspect
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable
from uuid import UUID, uuid4

import structlog

from app.services.ai.FieldOpsAI.schemas.agent_messages import (
    AgentAddress,
    DeliveryFailure,
    MessageEnvelope,
    PublishResult,
    _validate_topic,
)
from app.services.ai.FieldOpsAI.schemas.agent_subscription import AgentSubscription


_logger = structlog.get_logger("fieldops.ai.agent_bus")


# ---------------------------------------------------------------------------
# Handler type
# ---------------------------------------------------------------------------

AgentMessageHandler = Callable[
    [MessageEnvelope],
    "Awaitable[None] | None",
]


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class AgentBusError(Exception):
    """Base exception for AgentBus errors."""


class InvalidMessageHandlerError(AgentBusError):
    """Raised when an invalid handler is registered."""


class MessageDeliveryError(AgentBusError):
    """Raised for unrecoverable bus-level delivery failures."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_tenant_id(tenant_id: Any) -> str:
    """
    Validate and normalize a tenant identifier.

    Must be a non-blank string of at most 50 characters.
    """
    if not isinstance(tenant_id, str):
        raise TypeError("tenant_id must be a string.")
    tenant_id = tenant_id.strip()
    if not tenant_id:
        raise ValueError("tenant_id must not be blank.")
    if len(tenant_id) > 50:
        raise ValueError("tenant_id must be at most 50 characters.")
    return tenant_id


# ---------------------------------------------------------------------------
# Internal subscription record (handler NOT exposed externally)
# ---------------------------------------------------------------------------


class _SubscriptionRecord:
    """
    Private record holding subscription metadata plus the handler.

    The handler is never exposed outside AgentBus.
    """

    __slots__ = (
        "subscription_id",
        "tenant_id",
        "topic",
        "subscriber",
        "created_at",
        "handler",
    )

    def __init__(
        self,
        *,
        subscription_id: UUID,
        tenant_id: str,
        topic: str,
        subscriber: AgentAddress | None,
        created_at: datetime,
        handler: AgentMessageHandler,
    ) -> None:
        self.subscription_id = subscription_id
        self.tenant_id = tenant_id
        self.topic = topic
        self.subscriber = subscriber
        self.created_at = created_at
        self.handler = handler


# ---------------------------------------------------------------------------
# AgentBus
# ---------------------------------------------------------------------------


class AgentBus:
    """
    Asynchronous in-process publish/subscribe bus for AI agent messages.

    Routing rules
    -------------
    A subscription matches a published message when:

        subscription.tenant_id == message.sender.tenant_id
        AND subscription.topic == message.topic
        AND (
            message.recipient is None                   # broadcast
            OR subscription.subscriber == message.recipient  # targeted
        )

    For targeted delivery (recipient is not None):
      - Only subscriptions whose subscriber equals message.recipient receive it.
      - subscriber=None subscriptions are skipped.

    For broadcast delivery (recipient is None):
      - All same-tenant, same-topic subscriptions receive it,
        including those with subscriber=None.

    Each handler receives an isolated deep copy of the message.

    Handler timeouts
    ----------------
    Each handler is given at most handler_timeout_seconds to complete.
    Timed-out handlers produce a HANDLER_TIMEOUT DeliveryFailure.
    Failed handlers produce a HANDLER_FAILED DeliveryFailure.

    Privacy
    -------
    Payload and metadata are never logged. Raw exception text is never
    logged or exposed in PublishResult. Only safe operational fields
    appear in logs and DeliveryFailure records.
    """

    # Maximum handler timeout ceiling enforced independently of message timeout
    _MAX_HANDLER_TIMEOUT: float = 30.0

    def __init__(
        self,
        *,
        handler_timeout_seconds: float = 5.0,
    ) -> None:
        """
        Initialize the AgentBus.

        Parameters
        ----------
        handler_timeout_seconds:
            Maximum seconds each individual handler may run.
            Must be int or float, greater than zero, at most 30.
            bool values are rejected.

        Raises
        ------
        TypeError
            When handler_timeout_seconds is bool or not numeric.
        ValueError
            When handler_timeout_seconds is out of range.
        """
        if isinstance(handler_timeout_seconds, bool):
            raise TypeError(
                "handler_timeout_seconds must be a numeric value, not bool."
            )
        if not isinstance(handler_timeout_seconds, (int, float)):
            raise TypeError(
                "handler_timeout_seconds must be an int or float, "
                f"got {type(handler_timeout_seconds).__name__!r}."
            )
        if handler_timeout_seconds <= 0:
            raise ValueError(
                "handler_timeout_seconds must be greater than zero."
            )
        if handler_timeout_seconds > self._MAX_HANDLER_TIMEOUT:
            raise ValueError(
                f"handler_timeout_seconds {handler_timeout_seconds!r} "
                f"exceeds maximum of {self._MAX_HANDLER_TIMEOUT} seconds."
            )

        self._handler_timeout = float(handler_timeout_seconds)
        self._subscriptions: dict[UUID, _SubscriptionRecord] = {}
        self._lock = asyncio.Lock()

        _logger.debug(
            "agent_bus_created",
            handler_timeout_seconds=self._handler_timeout,
        )

    # ------------------------------------------------------------------
    # Subscribe
    # ------------------------------------------------------------------

    async def subscribe(
        self,
        *,
        tenant_id: str,
        topic: str,
        handler: AgentMessageHandler,
        subscriber: AgentAddress | None = None,
    ) -> AgentSubscription:
        """
        Register a handler for (tenant, topic) messages.

        Parameters
        ----------
        tenant_id:
            Non-blank string identifying the subscribing tenant.
        topic:
            Message routing topic. Normalized to lowercase.
        handler:
            Callable invoked when a matching message is published.
            May be sync or async. Never called during subscribe().
        subscriber:
            Optional AgentAddress of the targeted subscriber.
            When None, the subscription receives broadcasts.
            When set, subscriber.tenant_id must match tenant_id.

        Returns
        -------
        AgentSubscription
            Public metadata for the new subscription.

        Raises
        ------
        TypeError
            When tenant_id is not str, handler is not callable, or subscriber is wrong type.
        ValueError
            When tenant_id is blank/too long, topic is invalid, or subscriber
            tenant does not match.
        InvalidMessageHandlerError
            When handler is not callable.
        """
        # Validate and normalize tenant_id
        tenant_id = _normalize_tenant_id(tenant_id)

        # Validate and normalize topic
        topic = _validate_topic(topic)

        # Validate handler
        if not callable(handler):
            raise InvalidMessageHandlerError(
                "handler must be callable, "
                f"got {type(handler).__name__!r}."
            )

        # Validate subscriber
        if subscriber is not None and not isinstance(subscriber, AgentAddress):
            raise TypeError(
                "subscriber must be an AgentAddress or None, "
                f"got {type(subscriber).__name__!r}."
            )

        subscription_id = uuid4()
        created_at = datetime.now(UTC)

        # Construct and validate AgentSubscription model before inserting the private record.
        # This guarantees validation failures leave the bus unchanged.
        public_sub = AgentSubscription(
            subscription_id=subscription_id,
            tenant_id=tenant_id,
            topic=topic,
            subscriber=subscriber,
            created_at=created_at,
        )

        record = _SubscriptionRecord(
            subscription_id=subscription_id,
            tenant_id=tenant_id,
            topic=topic,
            subscriber=subscriber,
            created_at=created_at,
            handler=handler,
        )

        async with self._lock:
            self._subscriptions[subscription_id] = record

        _logger.debug(
            "agent_bus_subscribed",
            subscription_id=str(subscription_id),
            tenant_id=tenant_id,
            topic=topic,
            has_subscriber=subscriber is not None,
        )

        return public_sub

    # ------------------------------------------------------------------
    # Unsubscribe
    # ------------------------------------------------------------------

    async def unsubscribe(self, subscription_id: UUID) -> bool:
        """
        Remove a subscription by ID.

        Parameters
        ----------
        subscription_id:
            UUID identifying the subscription to remove.

        Returns
        -------
        bool
            True when removed, False when not found.

        Raises
        ------
        TypeError
            When subscription_id is not a UUID.
        """
        if not isinstance(subscription_id, UUID):
            raise TypeError(
                "subscription_id must be a UUID, "
                f"got {type(subscription_id).__name__!r}."
            )

        async with self._lock:
            if subscription_id not in self._subscriptions:
                return False
            del self._subscriptions[subscription_id]

        _logger.debug(
            "agent_bus_unsubscribed",
            subscription_id=str(subscription_id),
        )
        return True

    # ------------------------------------------------------------------
    # Publish
    # ------------------------------------------------------------------

    async def publish(self, message: MessageEnvelope) -> PublishResult:
        """
        Publish a message to all matching subscribers.

        Routing is determined by (tenant, topic, recipient) matching.
        Handlers are executed concurrently. Failures are isolated.

        Parameters
        ----------
        message:
            Validated MessageEnvelope or subclass to publish.

        Returns
        -------
        PublishResult
            Delivery summary. Never raises due to handler failures.

        Raises
        ------
        TypeError
            When message is not a MessageEnvelope instance.
        """
        if not isinstance(message, MessageEnvelope):
            raise TypeError(
                "message must be a MessageEnvelope instance, "
                f"got {type(message).__name__!r}."
            )

        message_id = message.message_id
        tenant_id = message.sender.tenant_id
        topic = message.topic
        recipient = message.recipient

        _logger.debug(
            "agent_bus_publish_start",
            message_id=str(message_id),
            tenant_id=tenant_id,
            topic=topic,
            sender=str(message.sender),
            recipient=str(recipient) if recipient is not None else None,
        )

        # Snapshot matching subscriptions under lock; release before calling handlers
        async with self._lock:
            matched = [
                record
                for record in self._subscriptions.values()
                if self._matches(record, message)
            ]

        if not matched:
            _logger.debug(
                "agent_bus_no_subscribers",
                message_id=str(message_id),
                tenant_id=tenant_id,
                topic=topic,
            )
            return PublishResult(
                message_id=message_id,
                matched_subscribers=0,
                delivered=0,
                failed=0,
                failures=(),
            )

        # Execute all matched handlers concurrently
        tasks = [
            self._call_handler(record, message)
            for record in matched
        ]
        results: list[DeliveryFailure | None] = await asyncio.gather(*tasks)

        failures = tuple(r for r in results if r is not None)
        delivered = len(matched) - len(failures)

        _logger.info(
            "agent_bus_publish_complete",
            message_id=str(message_id),
            tenant_id=tenant_id,
            topic=topic,
            matched=len(matched),
            delivered=delivered,
            failed=len(failures),
        )

        return PublishResult(
            message_id=message_id,
            matched_subscribers=len(matched),
            delivered=delivered,
            failed=len(failures),
            failures=failures,
        )

    # ------------------------------------------------------------------
    # Count and clear
    # ------------------------------------------------------------------

    async def subscriber_count(
        self,
        *,
        tenant_id: str | None = None,
        topic: str | None = None,
    ) -> int:
        """
        Return the number of active subscriptions, optionally filtered.

        Parameters
        ----------
        tenant_id:
            When provided, count only subscriptions for this tenant. Normalized.
        topic:
            When provided, count only subscriptions with this topic.
            Topic is normalized before matching.
        """
        # Validate and normalize tenant_id filter if provided
        if tenant_id is not None:
            tenant_id = _normalize_tenant_id(tenant_id)

        # Normalize topic filter if provided
        if topic is not None:
            topic = _validate_topic(topic)

        async with self._lock:
            count = sum(
                1
                for record in self._subscriptions.values()
                if (tenant_id is None or record.tenant_id == tenant_id)
                and (topic is None or record.topic == topic)
            )
        return count

    async def clear_tenant(self, tenant_id: str) -> int:
        """
        Remove all subscriptions for a given tenant.

        Parameters
        ----------
        tenant_id:
            Non-blank tenant whose subscriptions to remove.

        Returns
        -------
        int
            Number of subscriptions removed.

        Raises
        ------
        TypeError
            When tenant_id is not a string.
        ValueError
            When tenant_id is blank or over 50 chars.
        """
        tenant_id = _normalize_tenant_id(tenant_id)

        async with self._lock:
            to_remove = [
                sid
                for sid, record in self._subscriptions.items()
                if record.tenant_id == tenant_id
            ]
            for sid in to_remove:
                del self._subscriptions[sid]

        _logger.debug(
            "agent_bus_tenant_cleared",
            tenant_id=tenant_id,
            removed=len(to_remove),
        )
        return len(to_remove)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _matches(
        record: _SubscriptionRecord,
        message: MessageEnvelope,
    ) -> bool:
        """
        Return True when the subscription matches the message routing rules.

        Rules
        -----
        1. Tenant must match sender's tenant.
        2. Topic must match.
        3. For broadcast (recipient=None): all same-tenant, same-topic.
        4. For targeted (recipient set): only subscription with
           exact matching subscriber receives it;
           subscriber=None subscriptions are excluded.
        """
        if record.tenant_id != message.sender.tenant_id:
            return False
        if record.topic != message.topic:
            return False

        recipient = message.recipient
        if recipient is None:
            # Broadcast: all subscriptions for this tenant+topic match
            return True
        else:
            # Targeted: only the exact subscriber matches;
            # subscriber=None subscriptions do NOT receive targeted messages
            return record.subscriber == recipient

    async def _call_handler(
        self,
        record: _SubscriptionRecord,
        message: MessageEnvelope,
    ) -> DeliveryFailure | None:
        """
        Call a single handler with an isolated message copy.

        Returns None on success; returns DeliveryFailure on timeout or error.
        Never raises. Never logs payload, metadata, or raw exception text.
        """
        # Give each handler an isolated deep copy
        handler_message = message.model_copy(deep=True)
        handler = record.handler

        # Determine if handler is async/coroutine
        is_async = inspect.iscoroutinefunction(handler) or (
            hasattr(handler, "__call__") and inspect.iscoroutinefunction(handler.__call__)
        )

        async def execute_wrapped() -> None:
            if is_async:
                res = handler(handler_message)
                if res is not None and not inspect.isawaitable(res):
                    raise ValueError("Non-None, non-awaitable return value.")
                if inspect.isawaitable(res):
                    awaited_res = await res
                    if awaited_res is not None:
                        raise ValueError("Non-None awaited return value.")
            else:
                # Synchronous handlers must run in a thread executor so they do not block.
                res = await asyncio.to_thread(handler, handler_message)
                if res is not None and not inspect.isawaitable(res):
                    raise ValueError("Non-None, non-awaitable return value.")
                if inspect.isawaitable(res):
                    awaited_res = await res
                    if awaited_res is not None:
                        raise ValueError("Non-None awaited return value.")

        try:
            # Apply one timeout around the entire invocation
            await asyncio.wait_for(
                execute_wrapped(),
                timeout=self._handler_timeout,
            )
            return None

        except asyncio.TimeoutError:
            _logger.warning(
                "agent_bus_handler_timeout",
                message_id=str(message.message_id),
                tenant_id=record.tenant_id,
                topic=record.topic,
                subscription_id=str(record.subscription_id),
                error_code="HANDLER_TIMEOUT",
            )
            return DeliveryFailure(
                subscription_id=record.subscription_id,
                subscriber=record.subscriber,
                error_code="HANDLER_TIMEOUT",
                safe_message="Handler did not complete within the allowed timeout.",
            )

        except Exception:
            _logger.warning(
                "agent_bus_handler_failed",
                message_id=str(message.message_id),
                tenant_id=record.tenant_id,
                topic=record.topic,
                subscription_id=str(record.subscription_id),
                error_code="HANDLER_FAILED",
            )
            return DeliveryFailure(
                subscription_id=record.subscription_id,
                subscriber=record.subscriber,
                error_code="HANDLER_FAILED",
                safe_message="Handler raised an unexpected exception.",
            )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_agent_bus(
    *,
    handler_timeout_seconds: float = 5.0,
) -> AgentBus:
    """
    Create and return a new AgentBus instance.

    Returns a new bus each call — there is no singleton.

    Parameters
    ----------
    handler_timeout_seconds:
        Default per-handler timeout. Must be > 0 and <= 30.
    """
    return AgentBus(handler_timeout_seconds=handler_timeout_seconds)
