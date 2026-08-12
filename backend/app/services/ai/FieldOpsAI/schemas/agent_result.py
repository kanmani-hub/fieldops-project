"""
agent_result.py

Standard execution result returned by the FieldOps AI lifecycle.

Every agent can produce different business output, but the lifecycle
wraps that output in one predictable result contract.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)


class AgentResultStatus(str, Enum):
    """
    Final status of one agent execution.
    """

    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class AgentResult(BaseModel):
    """
    Standard result returned after an agent execution.

    The task-specific result is stored in ``output``. Operational
    information such as status, latency, token usage, and safe error
    details is represented consistently for every agent.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    output: Any | None = Field(
        default=None,
        description="Task-specific output returned by the agent.",
    )

    status: AgentResultStatus = Field(
        description="Final execution status.",
    )

    latency_ms: float = Field(
        ge=0,
        description="Total execution latency in milliseconds.",
    )

    tokens_used: int = Field(
        default=0,
        ge=0,
        description="Total AI-provider tokens used during execution.",
    )

    agent_id: str = Field(
        min_length=1,
        description="Agent instance that produced this result.",
    )

    correlation_id: str = Field(
        min_length=1,
        description="Correlation ID associated with the execution.",
    )

    error_code: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        description="Safe machine-readable error code.",
    )

    safe_error_message: str | None = Field(
        default=None,
        min_length=1,
        max_length=500,
        description=(
            "Safe error description that contains no secrets "
            "or customer information."
        ),
    )

    @model_validator(mode="after")
    def validate_status_fields(self) -> "AgentResult":
        """
        Ensure success and failure fields are internally consistent.
        """

        if self.status is AgentResultStatus.SUCCESS:
            if self.error_code is not None:
                raise ValueError(
                    "Successful results cannot contain error_code."
                )

            if self.safe_error_message is not None:
                raise ValueError(
                    "Successful results cannot contain "
                    "safe_error_message."
                )

        if self.status in {
            AgentResultStatus.FAILED,
            AgentResultStatus.TIMEOUT,
            AgentResultStatus.CANCELLED,
        }:
            if self.output is not None:
                raise ValueError(
                    "Unsuccessful results cannot contain output."
                )

            if self.error_code is None:
                raise ValueError(
                    "Unsuccessful results must contain error_code."
                )

            if self.safe_error_message is None:
                raise ValueError(
                    "Unsuccessful results must contain "
                    "safe_error_message."
                )

        return self