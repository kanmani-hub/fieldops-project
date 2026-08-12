"""
event_driven.py

Event-driven communication protocol for FieldOps Commander.

Purpose
-------
Allows an agent to publish an event to multiple subscribers.

Examples
--------
- Technician accepted a job.
- Technician rejected a job.
- Technician arrived onsite.
- Job was completed.
- Customer cancelled a request.

Protocol behavior
-----------------
1. A publisher sends an EVENT message.
2. Every registered subscriber receives the event.
3. Subscribers process the event independently.
4. No response is returned to the publisher.
5. Subscriber failures are retried using exponential backoff.
6. A subscriber failure does not stop other subscribers.
7. After retries are exhausted, the failed delivery is logged
   and dropped without propagating the exception.

Timeout
-------
Event-driven messages have no protocol timeout.

Retry policy
------------
Each subscriber delivery uses:

- Initial attempt+
- Retry 1 after 1 second
- Retry 2 after 2 seconds
- Retry 3 after 4 seconds

Correlation
-----------
The original correlation ID is preserved for every subscriber.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Optional

from app.core.protocols.base_protocol import BaseProtocol
from app.core.protocols.retry import retry_with_backoff
from app.services.ai.FieldOpsAI.schemas.agent_messages import BaseMessage,MessageType



logger = logging.getLogger(__name__)


# ==========================================================
# Subscriber Type
# ==========================================================


EventSubscriber = Callable[
    [BaseMessage],
    Awaitable[None],
]


# ==========================================================
# Event-Driven Protocol
# ==========================================================


class EventDrivenProtocol(BaseProtocol):
    """
    Publish EVENT messages to multiple subscribers.

    The publisher does not receive a business response.

    Each subscriber receives its own copy of the event so
    one subscriber cannot modify the message received by
    another subscriber.
    """

    PROTOCOL_NAME = "event_driven"

    # Events have no protocol timeout.
    TIMEOUT_SECONDS = None

    def __init__(
        self,
        subscribers: Optional[
            Mapping[str, EventSubscriber]
        ] = None,
    ) -> None:
        """
        Initialize the event-driven protocol.

        Parameters
        ----------
        subscribers
            Optional mapping of subscriber names to
            asynchronous subscriber functions.

        Example
        -------
        {
            "monitoring": monitoring_handler,
            "communication": communication_handler,
        }
        """

        super().__init__()

        self._subscribers: dict[
            str,
            EventSubscriber,
        ] = {}

        if subscribers:
            for name, subscriber in subscribers.items():
                self.subscribe(
                    name=name,
                    subscriber=subscriber,
                )

    # ---------------------------------------------------------

    def subscribe(
        self,
        name: str,
        subscriber: EventSubscriber,
    ) -> None:
        """
        Register an event subscriber.

        Parameters
        ----------
        name
            Unique subscriber name.

        subscriber
            Asynchronous function that receives an event.

        Raises
        ------
        ValueError
            If the subscriber name is empty or already exists.

        TypeError
            If the subscriber is not callable.
        """

        normalized_name = self._validate_subscriber_name(
            name
        )

        if not callable(
            subscriber
        ):
            raise TypeError(
                "Event subscriber must be callable."
            )

        if normalized_name in self._subscribers:
            raise ValueError(
                "A subscriber with the name "
                f"'{normalized_name}' is already registered."
            )

        self._subscribers[
            normalized_name
        ] = subscriber

        logger.info(
            "[%s] SUBSCRIBED | subscriber=%s",
            self.protocol_name,
            normalized_name,
        )

    # ---------------------------------------------------------

    def unsubscribe(
        self,
        name: str,
    ) -> bool:
        """
        Remove an event subscriber.

        Parameters
        ----------
        name
            Subscriber name.

        Returns
        -------
        bool
            True if the subscriber was removed.
            False if the subscriber was not registered.
        """

        normalized_name = self._validate_subscriber_name(
            name
        )

        subscriber = self._subscribers.pop(
            normalized_name,
            None,
        )

        if subscriber is None:
            return False

        logger.info(
            "[%s] UNSUBSCRIBED | subscriber=%s",
            self.protocol_name,
            normalized_name,
        )

        return True

    # ---------------------------------------------------------

    async def execute(
        self,
        message: BaseMessage,
    ) -> None:
        """
        Publish an event to every registered subscriber.

        Parameters
        ----------
        message
            EventMessage to publish.

        Returns
        -------
        None
            Event-driven communication does not return
            a business response.

        Notes
        -----
        Subscriber deliveries run concurrently.

        A failed subscriber is retried independently.
        Its failure does not prevent other subscribers
        from receiving the event.
        """

        self._validate_event(
            message
        )

        self.log_send(
            message
        )

        if not self._subscribers:
            logger.info(
                "[%s] EVENT_DROPPED | "
                "reason=no_subscribers | "
                "correlation_id=%s",
                self.protocol_name,
                message.correlation_id,
            )

            return None

        delivery_tasks: list[
            asyncio.Task[None]
        ] = []

        for (
            subscriber_name,
            subscriber,
        ) in self._subscribers.items():

            # Each subscriber receives an independent copy.
            subscriber_message = message.model_copy(
                deep=True
            )

            task = asyncio.create_task(
                self._deliver_safely(
                    message=subscriber_message,
                    subscriber_name=subscriber_name,
                    subscriber=subscriber,
                ),
                name=(
                    "fieldops-event-"
                    f"{subscriber_name}-"
                    f"{message.correlation_id}"
                ),
            )

            delivery_tasks.append(
                task
            )

        # All subscribers execute concurrently.
        #
        # _deliver_safely catches normal subscriber failures,
        # so a failure cannot stop another subscriber.
        await asyncio.gather(
            *delivery_tasks
        )

        logger.info(
            "[%s] EVENT_PUBLISHED | "
            "subscribers=%s | correlation_id=%s",
            self.protocol_name,
            len(delivery_tasks),
            message.correlation_id,
        )

        return None

    # ---------------------------------------------------------

    async def _deliver_safely(
        self,
        message: BaseMessage,
        subscriber_name: str,
        subscriber: EventSubscriber,
    ) -> None:
        """
        Deliver an event without propagating failures.

        After all retries are exhausted, the failure is
        logged and the event is dropped for that subscriber.
        """

        try:
            await self._deliver_with_retry(
                message=message,
                subscriber_name=subscriber_name,
                subscriber=subscriber,
            )

            logger.info(
                "[%s] EVENT_DELIVERED | "
                "subscriber=%s | correlation_id=%s",
                self.protocol_name,
                subscriber_name,
                message.correlation_id,
            )

        except asyncio.CancelledError:
            # Application shutdown and task cancellation
            # must still propagate correctly.
            raise

        except Exception as exc:
            # Story requirement:
            # Event failures are logged and dropped silently.
            logger.error(
                "[%s] EVENT_DROPPED | "
                "subscriber=%s | correlation_id=%s | "
                "error=%s",
                self.protocol_name,
                subscriber_name,
                message.correlation_id,
                str(exc),
                exc_info=True,
            )

            # Do not re-raise.
            #
            # This prevents one failed subscriber from
            # breaking the publisher or other subscribers.
            return None

    # ---------------------------------------------------------

    @retry_with_backoff
    async def _deliver_with_retry(
        self,
        message: BaseMessage,
        subscriber_name: str,
        subscriber: EventSubscriber,
    ) -> None:
        """
        Deliver an event to one subscriber with retries.

        The retry decorator applies:

        - Retry after 1 second
        - Retry after 2 seconds
        - Retry after 4 seconds
        """

        logger.debug(
            "[%s] EVENT_DELIVERY_ATTEMPT | "
            "subscriber=%s | correlation_id=%s",
            self.protocol_name,
            subscriber_name,
            message.correlation_id,
        )

        await subscriber(
            message
        )

    # ---------------------------------------------------------

    @staticmethod
    def _validate_event(
        message: BaseMessage,
    ) -> None:
        """
        Validate the message before publication.

        Raises
        ------
        TypeError
            If the message is not a BaseMessage.

        ValueError
            If the message type is not EVENT.
        """

        if not isinstance(
            message,
            BaseMessage,
        ):
            raise TypeError(
                "EventDrivenProtocol requires "
                "a BaseMessage."
            )

        if (
            message.message_type
            != MessageType.EVENT
        ):
            raise ValueError(
                "EventDrivenProtocol accepts only "
                "EVENT messages."
            )

    # ---------------------------------------------------------

    @staticmethod
    def _validate_subscriber_name(
        name: str,
    ) -> str:
        """
        Validate and normalize a subscriber name.
        """

        if not isinstance(
            name,
            str,
        ):
            raise TypeError(
                "Subscriber name must be a string."
            )

        normalized_name = name.strip()

        if not normalized_name:
            raise ValueError(
                "Subscriber name cannot be empty."
            )

        return normalized_name

    # ---------------------------------------------------------

    @property
    def subscriber_count(self) -> int:
        """
        Return the number of registered subscribers.
        """

        return len(
            self._subscribers
        )

    # ---------------------------------------------------------

    @property
    def subscriber_names(
        self,
    ) -> tuple[str, ...]:
        """
        Return registered subscriber names.

        A tuple prevents external code from modifying
        the internal subscriber registry.
        """

        return tuple(
            self._subscribers.keys()
        )