"""
timeout.py

Reusable timeout handling for FieldOps communication protocols.

Purpose
-------
Provides one consistent timeout mechanism for all protocol
implementations.

Timeout rules
-------------
- Request/Response: maximum 5 seconds
- Async processing/callback: maximum 30 seconds
- Event-driven: no timeout

Responsibilities
----------------
- Enforce protocol deadlines
- Validate timeout values
- Preserve protocol and correlation metadata
- Convert raw asyncio timeouts into a clear protocol error
- Support optional graceful fallback behavior

This module contains no business logic and does not depend
on Redis, Celery, FastAPI, or any individual AI agent.
"""

from __future__ import annotations

import asyncio
import inspect

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar


T = TypeVar("T")



TimeoutFallback = Callable[
    ["ProtocolTimeoutError"],
    T | Awaitable[T],
]


@dataclass(
    frozen=True,
    slots=True,
)
class TimeoutContext:
    """
    Metadata describing a protocol timeout.

    Attributes
    ----------
    protocol_name
        Name of the protocol that timed out.

    correlation_id
        Correlation ID of the message being processed.

    timeout_seconds
        Maximum time allowed for the operation.
    """

    protocol_name: str

    correlation_id: str

    timeout_seconds: float


class ProtocolTimeoutError(TimeoutError):
    """
    Raised when a communication protocol exceeds its timeout.

    This exception is more useful than a generic TimeoutError
    because it contains:

    - protocol name
    - correlation ID
    - timeout duration
    """

    def __init__(
        self,
        context: TimeoutContext,
    ) -> None:
        """
        Initialize the timeout error.
        """

        self.context = context

        super().__init__(
            (
                f"Protocol '{context.protocol_name}' "
                f"timed out after "
                f"{context.timeout_seconds:g} seconds. "
                f"Correlation ID: "
                f"{context.correlation_id}"
            )
        )

    @property
    def protocol_name(self) -> str:
        """
        Return the protocol name.
        """

        return self.context.protocol_name

    @property
    def correlation_id(self) -> str:
        """
        Return the correlation ID.
        """

        return self.context.correlation_id

    @property
    def timeout_seconds(self) -> float:
        """
        Return the configured timeout.
        """

        return self.context.timeout_seconds


class TimeoutHandler:
    """
    Execute asynchronous operations with timeout protection.

    The handler is shared by all protocols so timeout
    validation and timeout errors remain consistent.
    """

    @staticmethod
    def validate_timeout(
        timeout_seconds: float | None,
    ) -> float | None:
        """
        Validate and normalize a timeout value.

        Parameters
        ----------
        timeout_seconds
            Timeout in seconds.

            None means no timeout.

        Returns
        -------
        float | None
            Validated timeout value.

        Raises
        ------
        TypeError
            If the timeout is not numeric or None.

        ValueError
            If the timeout is zero or negative.
        """

        if timeout_seconds is None:
            return None

        if isinstance(
            timeout_seconds,
            bool,
        ):
            raise TypeError(
                "Timeout must be a number or None."
            )

        if not isinstance(
            timeout_seconds,
            (int, float),
        ):
            raise TypeError(
                "Timeout must be a number or None."
            )

        normalized_timeout = float(
            timeout_seconds
        )

        if normalized_timeout <= 0:
            raise ValueError(
                "Timeout must be greater than zero."
            )

        return normalized_timeout

    # ---------------------------------------------------------

    @classmethod
    async def execute(
        cls,
        operation: Awaitable[T],
        *,
        timeout_seconds: float | None,
        protocol_name: str,
        correlation_id: str,
        fallback: TimeoutFallback[T] | None = None,
    ) -> T:
        """
        Execute an asynchronous operation with a deadline.

        Parameters
        ----------
        operation
            Awaitable operation to execute.

        timeout_seconds
            Maximum execution time.

            None means that no timeout is enforced.

        protocol_name
            Name of the protocol performing the operation.

        correlation_id
            Message correlation ID used for tracking.

        fallback
            Optional function called when a timeout occurs.

            When no fallback is supplied, a
            ProtocolTimeoutError is raised.

        Returns
        -------
        T
            Operation result or fallback result.

        Raises
        ------
        ProtocolTimeoutError
            If the operation exceeds its timeout and no
            fallback was provided.

        asyncio.CancelledError
            If the application explicitly cancels the task.
        """

        normalized_timeout = cls.validate_timeout(
            timeout_seconds
        )

        # Event-driven flows can pass None because they have
        # no timeout according to the protocol contract.
        if normalized_timeout is None:
            return await operation

        try:
            async with asyncio.timeout(
                normalized_timeout
            ):
                return await operation

        except asyncio.CancelledError:
            # Application shutdown or explicit cancellation
            # must never be converted into a normal timeout.
            raise

        except TimeoutError as exc:
            timeout_error = ProtocolTimeoutError(
                TimeoutContext(
                    protocol_name=protocol_name,
                    correlation_id=correlation_id,
                    timeout_seconds=normalized_timeout,
                )
            )

            if fallback is None:
                raise timeout_error from exc

            fallback_result = fallback(
                timeout_error
            )

            if inspect.isawaitable(
                fallback_result
            ):
                return await fallback_result

            return fallback_result


async def run_with_timeout(
    operation: Awaitable[T],
    *,
    timeout_seconds: float | None,
    protocol_name: str,
    correlation_id: str,
    fallback: TimeoutFallback[T] | None = None,
) -> T:
    """
    Convenient function for using TimeoutHandler.

    This allows protocols to write:

    result = await run_with_timeout(...)

    instead of directly calling TimeoutHandler.execute().
    """

    return await TimeoutHandler.execute(
        operation=operation,
        timeout_seconds=timeout_seconds,
        protocol_name=protocol_name,
        correlation_id=correlation_id,
        fallback=fallback,
    )