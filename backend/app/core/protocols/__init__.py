"""
FieldOps communication protocol package.

This package defines the standard message-flow protocols used
by agents and backend services.

Public protocols
----------------
- RequestResponseProtocol
- AsyncFireForgetProtocol
- EventDrivenProtocol

Shared utilities
----------------
- BaseProtocol
- retry_with_backoff
- TimeoutHandler
- ProtocolTimeoutError
- run_with_timeout

Other application modules should import protocol components
from this package instead of importing internal files directly.
"""

from app.core.protocols.async_fire_forget import (
    AsyncFireForgetProtocol,
)
from app.core.protocols.base_protocol import BaseProtocol
from app.core.protocols.event_driven import EventDrivenProtocol

from app.core.protocols.request_response import (
    RequestResponseProtocol,
)
from app.core.protocols.retry import (
    retry_with_backoff,
)
from app.core.protocols.timeout import (
    ProtocolTimeoutError,
    TimeoutContext,
    TimeoutHandler,
    run_with_timeout,
)


__all__ = [
    "AsyncFireForgetProtocol",
    "BaseProtocol",
    "EventDrivenProtocol",
    "ProtocolTimeoutError",
    "RequestResponseProtocol",
    "TimeoutContext",
    "TimeoutHandler",
    "retry_with_backoff",
    "run_with_timeout",
]