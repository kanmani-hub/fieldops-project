"""
request_response.py

Synchronous request/response communication protocol.

Purpose
-------
Allows one FieldOps agent to send a request to another agent
and wait for a validated response.

Protocol behavior
-----------------
1. Validate the outgoing request.
2. Preserve its correlation ID.
3. Invoke the recipient handler.
4. Retry transient failures using exponential backoff.
5. Wait no longer than five seconds overall.
6. Return either a response message or an error message.

Timeout
-------
The complete request/response operation has a maximum
deadline of five seconds.

Error propagation
-----------------
Synchronous failures are returned to the caller as an
ErrorMessage.
"""

from __future__ import annotations


from collections.abc import Awaitable, Callable

from app.core.protocols.base_protocol import BaseProtocol
from app.core.protocols.retry import retry_with_backoff
from app.services.ai.FieldOpsAI.schemas.agent_messages import BaseMessage,ErrorMessage,MessageType
from app.core.protocols.timeout import ProtocolTimeoutError,run_with_timeout



# The recipient handler receives a message and returns
# another validated message.
RequestHandler = Callable[
    [BaseMessage],
    Awaitable[BaseMessage],
]


class RequestResponseProtocol(BaseProtocol):
    """
    Implements synchronous agent request/response messaging.

    Example
    -------
    Planning Agent
        sends QUERY
            ↓
    Dispatch Agent
        processes request
            ↓
    Planning Agent
        receives RESPONSE or ERROR
    """

    PROTOCOL_NAME = "request_response"

    # The complete synchronous operation must not run
    # longer than five seconds.
    TIMEOUT_SECONDS = 5.0

    # ---------------------------------------------------------

    async def execute(
        self,
        message: BaseMessage,
        handler: RequestHandler,
    ) -> BaseMessage:
        """
        Send a request and wait for its response.

        Parameters
        ----------
        message
            CommandMessage or QueryMessage sent by an agent.

        handler
            Asynchronous function responsible for processing
            the request and returning a ResponseMessage or
            ErrorMessage.

        Returns
        -------
        BaseMessage
            A validated response or error message.

        Notes
        -----
        The same correlation ID is preserved between the
        outgoing request and the returned response.
        """

        self._validate_request(
            message=message,
            handler=handler,
        )

        timeout_seconds = self._resolve_timeout(
            message
        )

        self.log_send(
            message
        )

        try:
            response = await run_with_timeout(
                operation=self._send_with_retry(
                    message=message,
                    handler=handler,
                ),
                timeout_seconds=timeout_seconds,
                protocol_name=self.protocol_name,
                correlation_id=message.correlation_id,
            )

            response = self._prepare_response(
                request=message,
                response=response,
            )

            self.log_receive(
                response
            )

            return response

        except ProtocolTimeoutError as exc:
            self.log_timeout(
                message
            )

            return self._build_error_response(
                request=message,
                error_code="REQUEST_TIMEOUT",
                error_message=(
                    "The request did not complete within "
                    f"{timeout_seconds:g} seconds."
                ),
                details={
                    "protocol": self.protocol_name,
                    "timeout_seconds": timeout_seconds,
                    "exception_type": type(exc).__name__,
                },
            )

        except Exception as exc:
            self.log_error(
                message=message,
                exc=exc,
            )

            return self._build_error_response(
                request=message,
                error_code="REQUEST_FAILED",
                error_message=(
                    "The synchronous request could not "
                    "be completed."
                ),
                details={
                    "protocol": self.protocol_name,
                    "exception_type": type(exc).__name__,
                    "reason": str(exc),
                },
            )

    # ---------------------------------------------------------

    @retry_with_backoff
    async def _send_with_retry(
        self,
        message: BaseMessage,
        handler: RequestHandler,
    ) -> BaseMessage:
        """
        Execute the recipient handler with retry support.

        Retries occur when the handler raises an exception.

        The outer five-second deadline still takes priority.
        Therefore, retry processing stops immediately when
        the synchronous deadline is reached.
        """

        response = await handler(
            message
        )

        if not isinstance(
            response,
            BaseMessage,
        ):
            raise TypeError(
                "Request handler must return a BaseMessage."
            )

        return response

    # ---------------------------------------------------------

    def _validate_request(
        self,
        message: BaseMessage,
        handler: RequestHandler,
    ) -> None:
        """
        Validate the request before sending it.

        Raises
        ------
        TypeError
            If the message or handler is invalid.

        ValueError
            If the message is not a command or query.
        """

        if not isinstance(
            message,
            BaseMessage,
        ):
            raise TypeError(
                "RequestResponseProtocol requires "
                "a BaseMessage."
            )

        if message.message_type not in {
            MessageType.COMMAND,
            MessageType.QUERY,
        }:
            raise ValueError(
                "RequestResponseProtocol accepts only "
                "COMMAND or QUERY messages."
            )

        if not callable(handler):
            raise TypeError(
                "The request handler must be callable."
            )

    # ---------------------------------------------------------

    def _resolve_timeout(
        self,
        message: BaseMessage,
    ) -> float:
        """
        Resolve the timeout for the current request.

        A message may request a smaller timeout, but it
        cannot increase the protocol maximum beyond five
        seconds.
        """

        protocol_timeout = self.timeout

        if protocol_timeout is None:
            raise RuntimeError(
                "RequestResponseProtocol must define "
                "a timeout."
            )

        requested_timeout = message.timeout_seconds

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

    def _prepare_response(
        self,
        request: BaseMessage,
        response: BaseMessage,
    ) -> BaseMessage:
        """
        Validate and normalize a returned response.

        The response must be a RESPONSE or ERROR message.
        Its correlation ID is replaced with the request's
        correlation ID when necessary.
        """

        if response.message_type not in {
            MessageType.RESPONSE,
            MessageType.ERROR,
        }:
            raise ValueError(
                "The request handler must return a "
                "RESPONSE or ERROR message."
            )

        if (
            response.correlation_id
            != request.correlation_id
        ):
            response = response.model_copy(
                update={
                    "correlation_id": (
                        request.correlation_id
                    ),
                }
            )

        return response

    # ---------------------------------------------------------

    def _build_error_response(
        self,
        request: BaseMessage,
        error_code: str,
        error_message: str,
        details: dict[str, object],
    ) -> ErrorMessage:
        """
        Create an ErrorMessage for the original caller.

        The sender and recipient are reversed because the
        error is returned from the destination agent back
        to the original sender.
        """

        return ErrorMessage(
            sender=request.recipient,
            recipient=request.sender,
            payload={
                "original_message_type": (
                    request.message_type.value
                ),
            },
            correlation_id=request.correlation_id,
            contract_version=request.contract_version,
            timeout_seconds=request.timeout_seconds,
            error_code=error_code,
            error_message=error_message,
            details=details,
        )