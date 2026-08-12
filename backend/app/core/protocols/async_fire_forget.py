"""
async_fire_forget.py

Asynchronous fire-and-forget communication protocol.

Purpose
-------
Allows one FieldOps agent to submit work without waiting
for the complete result.

Flow
----
1. Agent sends a COMMAND or EVENT message.
2. Protocol validates and schedules the message.
3. Sender immediately receives an ACK.
4. Processing continues independently in the background.
5. An optional callback receives the final result.
6. Failed messages are routed to the dead-letter queue.

Timeout
-------
Background processing and its callback must complete within
30 seconds unless the message specifies a smaller timeout.

Retry policy
------------
The processing handler, callback, and dead-letter publisher
use the shared retry policy:

- Retry 1 after 1 second
- Retry 2 after 2 seconds
- Retry 3 after 4 seconds

The same correlation ID is preserved throughout the flow.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Optional

from app.core.protocols.base_protocol import BaseProtocol
from app.core.protocols.retry import retry_with_backoff
from app.services.ai.FieldOpsAI.schemas.agent_messages import BaseMessage,ErrorMessage,MessageType,ResponseMessage
from app.core.protocols.timeout import ProtocolTimeoutError,run_with_timeout

logger = logging.getLogger(__name__)


# ==========================================================
# Handler Types
# ==========================================================

# Processes the original message.
#
# A handler may:
# - return a ResponseMessage,
# - return an ErrorMessage,
# - or return None when no result body is required.
AsyncMessageHandler = Callable[
    [BaseMessage],
    Awaitable[BaseMessage | None],
]


# Receives the final processing result.
AsyncCallback = Callable[
    [BaseMessage],
    Awaitable[None],
]


# Publishes failed messages to a durable dead-letter queue.
DeadLetterPublisher = Callable[
    [ErrorMessage],
    Awaitable[None],
]


# ==========================================================
# Async Fire-and-Forget Protocol
# ==========================================================


class AsyncFireForgetProtocol(BaseProtocol):
    """
    Implements asynchronous fire-and-forget communication.

    The sender receives an immediate acknowledgement after
    the background operation is successfully scheduled.

    The acknowledgement does not mean that the full operation
    has completed. It only means that processing was accepted.
    """

    PROTOCOL_NAME = "async_fire_and_forget"

    # Processing plus callback must complete within 30 seconds.
    TIMEOUT_SECONDS = 30.0

    def __init__(
        self,
        dead_letter_publisher: Optional[
            DeadLetterPublisher
        ] = None,
    ) -> None:
        """
        Initialize the asynchronous protocol.

        Parameters
        ----------
        dead_letter_publisher
            Optional asynchronous function that publishes
            failed messages to Redis, Celery, or another
            dead-letter transport.

            When no publisher is provided, errors remain
            available in the local dead_letters collection
            and are written to application logs.
        """

        super().__init__()

        if (
            dead_letter_publisher is not None
            and not callable(dead_letter_publisher)
        ):
            raise TypeError(
                "dead_letter_publisher must be callable."
            )

        self._dead_letter_publisher = (
            dead_letter_publisher
        )

        # Strong references prevent background tasks from
        # being garbage-collected before they finish.
        self._background_tasks: set[
            asyncio.Task[None]
        ] = set()

        # Local fallback storage.
        #
        # Production environments should inject a durable
        # Redis or Celery dead-letter publisher.
        self._dead_letters: list[
            ErrorMessage
        ] = []

    # ---------------------------------------------------------

    async def execute(
        self,
        message: BaseMessage,
        handler: AsyncMessageHandler,
        callback: Optional[AsyncCallback] = None,
    ) -> ResponseMessage | ErrorMessage:
        """
        Accept a message and return an immediate ACK.

        Parameters
        ----------
        message
            COMMAND or EVENT message to process.

        handler
            Asynchronous function that performs the actual
            background work.

        callback
            Optional asynchronous function that receives the
            final ResponseMessage or ErrorMessage.

        Returns
        -------
        ResponseMessage
            Immediate ACK when the message was successfully
            scheduled.

        ErrorMessage
            Returned only when scheduling or initial validation
            fails.

        Notes
        -----
        The returned ACK means "accepted for processing."
        It does not mean "processing completed successfully."
        """

        if not isinstance(
            message,
            BaseMessage,
        ):
            raise TypeError(
                "AsyncFireForgetProtocol requires "
                "a BaseMessage."
            )

        try:
            self._validate_message(
                message=message,
                handler=handler,
                callback=callback,
            )

            timeout_seconds = self._resolve_timeout(
                message
            )

            self.log_send(
                message
            )

            task = asyncio.create_task(
                self._run_background(
                    message=message,
                    handler=handler,
                    callback=callback,
                    timeout_seconds=timeout_seconds,
                ),
                name=(
                    "fieldops-async-"
                    f"{message.correlation_id}"
                ),
            )

            self._background_tasks.add(
                task
            )

            task.add_done_callback(
                self._on_task_completed
            )

            acknowledgement = self._build_acknowledgement(
                request=message,
                timeout_seconds=timeout_seconds,
            )

            logger.info(
                "[%s] ACK | correlation_id=%s | "
                "background_task=%s",
                self.protocol_name,
                message.correlation_id,
                task.get_name(),
            )

            return acknowledgement

        except Exception as exc:
            self.log_error(
                message=message,
                exc=exc,
            )

            return self._build_error_message(
                request=message,
                error_code="ASYNC_SCHEDULING_FAILED",
                error_message=(
                    "The asynchronous message could not "
                    "be scheduled."
                ),
                details={
                    "protocol": self.protocol_name,
                    "exception_type": type(exc).__name__,
                    "reason": str(exc),
                },
                timeout_seconds=(
                    message.timeout_seconds
                    or self.TIMEOUT_SECONDS
                ),
            )

    # ---------------------------------------------------------

    async def _run_background(
        self,
        message: BaseMessage,
        handler: AsyncMessageHandler,
        callback: Optional[AsyncCallback],
        timeout_seconds: float,
    ) -> None:
        """
        Execute processing and callback in the background.

        Processing is limited by the resolved 30-second
        asynchronous timeout.
        """

        try:
            await run_with_timeout(
                operation=self._process_and_callback(
                    message=message,
                    handler=handler,
                    callback=callback,
                ),
                timeout_seconds=timeout_seconds,
                protocol_name=self.protocol_name,
                correlation_id=message.correlation_id,
            )

        except ProtocolTimeoutError as exc:
            self.log_timeout(
                message
            )

            error = self._build_error_message(
                request=message,
                error_code="ASYNC_PROCESSING_TIMEOUT",
                error_message=(
                    "Asynchronous processing did not "
                    f"complete within {timeout_seconds:g} "
                    "seconds."
                ),
                details={
                    "protocol": self.protocol_name,
                    "timeout_seconds": timeout_seconds,
                    "exception_type": type(exc).__name__,
                },
                timeout_seconds=timeout_seconds,
            )

            await self._route_to_dead_letter(
                error
            )

        except asyncio.CancelledError:
            logger.info(
                "[%s] CANCELLED | correlation_id=%s",
                self.protocol_name,
                message.correlation_id,
            )

            raise

        except Exception as exc:
            self.log_error(
                message=message,
                exc=exc,
            )

            error = self._build_error_message(
                request=message,
                error_code="ASYNC_PROCESSING_FAILED",
                error_message=(
                    "The asynchronous message could not "
                    "be processed successfully."
                ),
                details={
                    "protocol": self.protocol_name,
                    "exception_type": type(exc).__name__,
                    "reason": str(exc),
                },
                timeout_seconds=timeout_seconds,
            )

            await self._route_to_dead_letter(
                error
            )

    # ---------------------------------------------------------

    async def _process_and_callback(
        self,
        message: BaseMessage,
        handler: AsyncMessageHandler,
        callback: Optional[AsyncCallback],
    ) -> None:
        """
        Process the message and optionally deliver a callback.
        """

        result = await self._process_with_retry(
            message=message,
            handler=handler,
        )

        result = self._prepare_result(
            request=message,
            result=result,
        )

        # An explicit ErrorMessage returned by the handler
        # must also be preserved in the dead-letter queue.
        if isinstance(
            result,
            ErrorMessage,
        ):
            await self._route_to_dead_letter(
                result
            )

        if callback is not None:
            await self._deliver_callback_with_retry(
                message=result,
                callback=callback,
            )

        self.log_receive(
            result
        )

    # ---------------------------------------------------------

    @retry_with_backoff
    async def _process_with_retry(
        self,
        message: BaseMessage,
        handler: AsyncMessageHandler,
    ) -> BaseMessage:
        """
        Run the processing handler with exponential backoff.

        The handler is retried when it raises an exception.
        """

        result = await handler(
            message
        )

        if result is None:
            return ResponseMessage(
                sender=message.recipient,
                recipient=message.sender,
                correlation_id=message.correlation_id,
                contract_version=(
                    message.contract_version
                ),
                timeout_seconds=(
                    message.timeout_seconds
                ),
                payload={
                    "status": "COMPLETED",
                    "original_message_type": (
                        message.message_type.value
                    ),
                },
            )

        if not isinstance(
            result,
            BaseMessage,
        ):
            raise TypeError(
                "The asynchronous handler must return "
                "a BaseMessage or None."
            )

        return result

    # ---------------------------------------------------------

    @retry_with_backoff
    async def _deliver_callback_with_retry(
        self,
        message: BaseMessage,
        callback: AsyncCallback,
    ) -> None:
        """
        Deliver the result callback with retry support.
        """

        await callback(
            message
        )

    # ---------------------------------------------------------

    async def _route_to_dead_letter(
        self,
        error: ErrorMessage,
    ) -> None:
        """
        Save and publish a failed asynchronous message.

        The local list provides a fallback for tests and
        development.

        Production should provide a publisher that sends
        the error to Redis Streams, Celery, or another
        durable dead-letter queue.
        """

        self._dead_letters.append(
            error
        )

        logger.error(
            "[%s] DEAD_LETTER | error_code=%s | "
            "correlation_id=%s",
            self.protocol_name,
            error.error_code,
            error.correlation_id,
        )

        if self._dead_letter_publisher is None:
            return

        try:
            await self._publish_dead_letter_with_retry(
                message=error,
                publisher=self._dead_letter_publisher,
            )

        except asyncio.CancelledError:
            raise

        except Exception as exc:
            logger.critical(
                "[%s] DEAD_LETTER_PUBLISH_FAILED | "
                "correlation_id=%s | error=%s",
                self.protocol_name,
                error.correlation_id,
                str(exc),
                exc_info=True,
            )

    # ---------------------------------------------------------

    @retry_with_backoff
    async def _publish_dead_letter_with_retry(
        self,
        message: ErrorMessage,
        publisher: DeadLetterPublisher,
    ) -> None:
        """
        Publish an error to the external dead-letter queue.
        """

        await publisher(
            message
        )

    # ---------------------------------------------------------

    def _validate_message(
        self,
        message: BaseMessage,
        handler: AsyncMessageHandler,
        callback: Optional[AsyncCallback],
    ) -> None:
        """
        Validate an asynchronous message and its handlers.
        """

        if message.message_type not in {
            MessageType.COMMAND,
            MessageType.EVENT,
        }:
            raise ValueError(
                "AsyncFireForgetProtocol accepts only "
                "COMMAND or EVENT messages."
            )

        if not callable(
            handler
        ):
            raise TypeError(
                "The asynchronous handler must be callable."
            )

        if (
            callback is not None
            and not callable(callback)
        ):
            raise TypeError(
                "The asynchronous callback must be callable."
            )

    # ---------------------------------------------------------

    def _resolve_timeout(
        self,
        message: BaseMessage,
    ) -> float:
        """
        Resolve the asynchronous timeout.

        The message may request a shorter timeout, but it
        cannot increase the protocol timeout beyond
        30 seconds.
        """

        protocol_timeout = self.timeout

        if protocol_timeout is None:
            raise RuntimeError(
                "AsyncFireForgetProtocol must define "
                "a timeout."
            )

        requested_timeout = (
            message.timeout_seconds
        )

        if requested_timeout is None:
            return protocol_timeout

        if requested_timeout <= 0:
            raise ValueError(
                "Message timeout_seconds must be "
                "greater than zero."
            )

        return min(
            float(requested_timeout),
            protocol_timeout,
        )

    # ---------------------------------------------------------

    def _prepare_result(
        self,
        request: BaseMessage,
        result: BaseMessage,
    ) -> BaseMessage:
        """
        Validate and normalize the background result.

        The original correlation ID is always preserved.
        """

        if result.message_type not in {
            MessageType.RESPONSE,
            MessageType.ERROR,
        }:
            raise ValueError(
                "The asynchronous handler must return a "
                "RESPONSE or ERROR message."
            )

        if (
            result.correlation_id
            != request.correlation_id
        ):
            result = result.model_copy(
                update={
                    "correlation_id": (
                        request.correlation_id
                    ),
                }
            )

        return result

    # ---------------------------------------------------------

    def _build_acknowledgement(
        self,
        request: BaseMessage,
        timeout_seconds: float,
    ) -> ResponseMessage:
        """
        Build the immediate ACK returned to the sender.
        """

        return ResponseMessage(
            sender=request.recipient,
            recipient=request.sender,
            correlation_id=request.correlation_id,
            contract_version=request.contract_version,
            timeout_seconds=timeout_seconds,
            payload={
                "status": "ACK",
                "accepted": True,
                "processing": "BACKGROUND",
                "protocol": self.protocol_name,
                "callback_timeout_seconds": (
                    timeout_seconds
                ),
                "original_message_type": (
                    request.message_type.value
                ),
            },
        )

    # ---------------------------------------------------------

    def _build_error_message(
        self,
        request: BaseMessage,
        error_code: str,
        error_message: str,
        details: dict[str, object],
        timeout_seconds: float,
    ) -> ErrorMessage:
        """
        Build a standardized asynchronous ErrorMessage.
        """

        return ErrorMessage(
            sender=request.recipient,
            recipient=request.sender,
            correlation_id=request.correlation_id,
            contract_version=request.contract_version,
            timeout_seconds=timeout_seconds,
            payload={
                "original_message_type": (
                    request.message_type.value
                ),
            },
            error_code=error_code,
            error_message=error_message,
            details=details,
        )

    # ---------------------------------------------------------

    def _on_task_completed(
        self,
        task: asyncio.Task[None],
    ) -> None:
        """
        Remove completed background tasks from tracking.
        """

        self._background_tasks.discard(
            task
        )

        if task.cancelled():
            return

        exception = task.exception()

        if exception is not None:
            logger.error(
                "[%s] UNEXPECTED_BACKGROUND_ERROR | "
                "task=%s | error=%s",
                self.protocol_name,
                task.get_name(),
                str(exception),
                exc_info=(
                    type(exception),
                    exception,
                    exception.__traceback__,
                ),
            )

    # ---------------------------------------------------------

    @property
    def pending_task_count(self) -> int:
        """
        Return the number of unfinished background tasks.
        """

        return len(
            self._background_tasks
        )

    # ---------------------------------------------------------

    @property
    def dead_letters(
        self,
    ) -> tuple[ErrorMessage, ...]:
        """
        Return locally recorded dead-letter messages.

        A tuple prevents callers from modifying the
        internal collection.
        """

        return tuple(
            self._dead_letters
        )

    # ---------------------------------------------------------

    async def wait_for_pending(
        self,
    ) -> None:
        """
        Wait until all currently scheduled tasks finish.

        This is mainly useful for:

        - Integration tests
        - Graceful application shutdown
        - Local development
        """

        if not self._background_tasks:
            return

        await asyncio.gather(
            *tuple(self._background_tasks),
            return_exceptions=True,
        )