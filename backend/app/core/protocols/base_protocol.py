"""
base_protocol.py

Shared foundation for FieldOps communication protocols.

Purpose
-------
Defines behavior and configuration shared by all message-flow
protocols used within FieldOps Commander.

Shared responsibilities
-----------------------
- Retry configuration
- Exponential-backoff configuration
- Timeout configuration
- Correlation ID tracking
- Consistent protocol logging

Concrete protocols
------------------
- RequestResponseProtocol
- AsyncFireForgetProtocol
- EventDrivenProtocol

This class contains no transport-specific implementation.
Redis, Celery, HTTP, and other transport details belong in
the concrete protocol classes or transport adapters.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from app.services.ai.FieldOpsAI.schemas.agent_messages import BaseMessage


logger = logging.getLogger(__name__)


class BaseProtocol(ABC):
    """
    Abstract base class for all communication protocols.

    Concrete protocol classes inherit the common retry,
    timeout, correlation-tracking, and logging behavior
    defined here.
    """

    # Therefore, the maximum number of transport calls is four:
    # one initial call plus three retries.
    MAX_RETRIES: ClassVar[int] = 3

    RETRY_DELAYS: ClassVar[tuple[float, ...]] = (
        1.0,
        2.0,
        4.0,
    )
    
    TIMEOUT_SECONDS: ClassVar[float | None] = None

    PROTOCOL_NAME: ClassVar[str] = "base"

    def __init__(self) -> None:
        """
        Initialize the protocol and validate its configuration.
        """

        self._validate_configuration()

        logger.debug(
            "Protocol initialized | protocol=%s | "
            "max_retries=%s | retry_delays=%s | timeout=%s",
            self.protocol_name,
            self.max_retries,
            self.retry_delays,
            self.timeout,
        )

    # ---------------------------------------------------------

    def _validate_configuration(self) -> None:
        """
        Validate retry and timeout configuration.

        Raises
        ------
        ValueError
            If retry delays do not match the retry count,
            contain invalid values, or the timeout is invalid.
        """

        if self.MAX_RETRIES < 0:
            raise ValueError(
                "MAX_RETRIES cannot be negative."
            )

        if len(self.RETRY_DELAYS) != self.MAX_RETRIES:
            raise ValueError(
                "RETRY_DELAYS must contain exactly one delay "
                "for every configured retry."
            )

        if any(delay < 0 for delay in self.RETRY_DELAYS):
            raise ValueError(
                "Retry delays cannot be negative."
            )

        if (
            self.TIMEOUT_SECONDS is not None
            and self.TIMEOUT_SECONDS <= 0
        ):
            raise ValueError(
                "TIMEOUT_SECONDS must be greater than zero "
                "or None."
            )

    # ---------------------------------------------------------

    @property
    def protocol_name(self) -> str:
        """
        Return the protocol name.
        """

        return self.PROTOCOL_NAME

    # ---------------------------------------------------------

    @property
    def timeout(self) -> float | None:
        """
        Return the protocol timeout in seconds.

        None means that the protocol has no timeout.
        """

        return self.TIMEOUT_SECONDS

    # ---------------------------------------------------------

    @property
    def max_retries(self) -> int:
        """
        Return the number of retries after the initial attempt.
        """

        return self.MAX_RETRIES

    # ---------------------------------------------------------

    @property
    def retry_delays(self) -> tuple[float, ...]:
        """
        Return exponential-backoff delays.

        Returns
        -------
        tuple[float, ...]
            Delays of 1, 2, and 4 seconds.
        """

        return self.RETRY_DELAYS

    # ---------------------------------------------------------

    @staticmethod
    def get_correlation_id(
        message: BaseMessage,
    ) -> str:
        """
        Return the message correlation ID.

        The same correlation ID must be preserved across
        every protocol hop in a workflow.
        """

        return message.correlation_id

    # ---------------------------------------------------------

    def log_send(
        self,
        message: BaseMessage,
    ) -> None:
        """
        Log an outgoing message.
        """

        logger.info(
            "[%s] SEND | sender=%s | recipient=%s | "
            "type=%s | correlation_id=%s",
            self.protocol_name,
            message.sender,
            message.recipient,
            message.message_type.value,
            message.correlation_id,
        )

    # ---------------------------------------------------------

    def log_receive(
        self,
        message: BaseMessage,
    ) -> None:
        """
        Log a successfully received or processed message.
        """

        logger.info(
            "[%s] RECEIVE | sender=%s | recipient=%s | "
            "type=%s | correlation_id=%s",
            self.protocol_name,
            message.sender,
            message.recipient,
            message.message_type.value,
            message.correlation_id,
        )

    # ---------------------------------------------------------

    def log_retry(
        self,
        message: BaseMessage,
        retry_number: int,
        delay_seconds: float,
        exc: Exception,
    ) -> None:
        """
        Log a retry attempt.
        """

        logger.warning(
            "[%s] RETRY | retry=%s/%s | delay=%.1fs | "
            "correlation_id=%s | error=%s",
            self.protocol_name,
            retry_number,
            self.max_retries,
            delay_seconds,
            message.correlation_id,
            str(exc),
        )

    # ---------------------------------------------------------

    def log_timeout(
        self,
        message: BaseMessage,
    ) -> None:
        """
        Log a protocol timeout.
        """

        logger.error(
            "[%s] TIMEOUT | timeout=%s | correlation_id=%s",
            self.protocol_name,
            self.timeout,
            message.correlation_id,
        )

    # ---------------------------------------------------------

    def log_error(
        self,
        message: BaseMessage,
        exc: Exception,
    ) -> None:
        """
        Log a final protocol failure.
        """

        logger.error(
            "[%s] ERROR | correlation_id=%s | error=%s",
            self.protocol_name,
            message.correlation_id,
            str(exc),
            exc_info=True,
        )

    # ---------------------------------------------------------

    @abstractmethod
    async def execute(
        self,
        message: BaseMessage,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """
        Execute the concrete communication protocol.

        Every child protocol must implement this method.
        """

        raise NotImplementedError