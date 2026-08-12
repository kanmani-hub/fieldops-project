"""
retry.py

Reusable asynchronous retry decorator for FieldOps protocols.

Retry policy
------------
- Initial execution attempt
- Retry 1 after 1 second
- Retry 2 after 2 seconds
- Retry 3 after 4 seconds

The decorator does not retry asyncio cancellation errors.
Timeout enforcement remains the responsibility of the
individual protocol.
"""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Awaitable, Callable
from typing import Any, ParamSpec, TypeVar


P = ParamSpec("P")
R = TypeVar("R")


def retry_with_backoff(
    func: Callable[P, Awaitable[R]],
) -> Callable[P, Awaitable[R]]:
    """
    Retry an asynchronous protocol operation.

    The decorated method must belong to a BaseProtocol-compatible
    class that exposes:

    - retry_delays
    - max_retries
    - log_retry(message, retry_number, delay, exception)

    The first positional argument after ``self`` must be the
    message being processed.
    """

    @functools.wraps(func)
    async def wrapper(
        self: Any,
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> R:
        if args:
            message = args[0]
        else:
            message = kwargs.get("message")

        if message is None:
            raise ValueError(
                "A message is required for retry tracking."
            )

        retry_delays = self.retry_delays

        for attempt_number in range(
            self.max_retries + 1
        ):
            try:
                return await func(
                    self,
                    *args,
                    **kwargs,
                )

            except asyncio.CancelledError:
                # Cancellation must immediately propagate.
                raise

            except Exception as exc:
                retries_exhausted = (
                    attempt_number >= self.max_retries
                )

                if retries_exhausted:
                    raise

                delay_seconds = retry_delays[
                    attempt_number
                ]

                retry_number = attempt_number + 1

                self.log_retry(
                    message=message,
                    retry_number=retry_number,
                    delay_seconds=delay_seconds,
                    exc=exc,
                )

                await asyncio.sleep(
                    delay_seconds
                )

        # This line is unreachable, but keeps static
        # type checkers satisfied.
        raise RuntimeError(
            "Retry execution ended unexpectedly."
        )

    return wrapper