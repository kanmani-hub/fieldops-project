"""
agent_lifecycle.py

Schemas used by the FieldOps AI lifecycle controller.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Awaitable, Callable

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)

from app.services.ai.FieldOpsAI.agents.base import AgentState


class LifecycleEvent(str, Enum):
    """
    Events emitted during an agent lifecycle.

    """

    INIT = "init"
    SETUP = "setup"
    RUN = "run"
    PAUSE = "pause"
    RESUME = "resume"
    TEARDOWN = "teardown"
    ERROR = "error"


class LifecycleHookPhase(str, Enum):
    """
    Whether a lifecycle hook runs before or after an event.
    """

    PRE = "pre"
    POST = "post"


class LifecycleEventRecord(BaseModel):
    """
    Safe operational record for one lifecycle event.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    event: LifecycleEvent = Field(
        description="Lifecycle event that occurred.",
    )

    phase: LifecycleHookPhase = Field(
        description="Whether this record is before or after the event.",
    )

    agent_id: str = Field(
        min_length=1,
        description="Agent instance involved in the event.",
    )

    tenant_id: str = Field(
        min_length=1,
        description="Tenant that owns the agent.",
    )

    correlation_id: str | None = Field(
        default=None,
        description="Execution correlation ID when available.",
    )

    previous_state: AgentState = Field(
        description="State before the event.",
    )

    current_state: AgentState = Field(
        description="State when this record was created.",
    )

    occurred_at: datetime = Field(
        description="UTC-aware timestamp for the event.",
    )

    latency_ms: float | None = Field(
        default=None,
        ge=0,
        description="Event latency when measurable.",
    )

    error_code: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        description="Safe lifecycle error code.",
    )

    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Small safe operational metadata. Customer context, "
            "prompts, output, and secrets must not be stored here."
        ),
    )


LifecycleHook = Callable[
    [LifecycleEventRecord],
    Awaitable[None],
]