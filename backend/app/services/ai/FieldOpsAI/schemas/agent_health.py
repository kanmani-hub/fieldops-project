"""
agent_health.py

Health monitoring schemas for FieldOps AI agents.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
import math
from typing import Any, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    UUID4,
    field_validator,
    model_validator,
)

from app.services.ai.FieldOpsAI.agents.base import AgentState
from app.services.ai.FieldOpsAI.schemas.agent_result import AgentResultStatus
from app.services.ai.FieldOpsAI.schemas.ai_task import AITask

FORBIDDEN_KEYS = {
    "api_key", "secret", "password", "token", "auth_token", "authorization",
    "prompt", "system_prompt", "provider_response", "raw_response",
    "customer_name", "customer_address", "customer_email", "customer_phone",
    "phone_number", "gps", "latitude", "longitude", "coordinates",
    "stack_trace", "traceback", "exception"
}


def _check_json_and_keys(value: Any, path: str = "metadata") -> Any:
    """
    Recursively checks if the value is JSON-compatible and contains no sensitive keys.
    Also returns a deep copy of the structure.
    """
    if isinstance(value, dict):
        copied_dict = {}
        for k, v in value.items():
            if not isinstance(k, str):
                raise ValueError(f"Metadata keys must be strings, got {type(k).__name__} at {path}")
            child_path = f"{path}.{k}" if path else k
            k_lower = k.lower()
            if k_lower in FORBIDDEN_KEYS:
                raise ValueError(f"Forbidden key found at path {child_path}")
            copied_dict[k] = _check_json_and_keys(v, child_path)
        return copied_dict
    elif isinstance(value, list):
        copied_list = []
        for i, item in enumerate(value):
            next_path = f"{path}[{i}]"
            copied_list.append(_check_json_and_keys(item, next_path))
        return copied_list
    elif value is None:
        return None
    elif isinstance(value, bool):
        # Handle bool first (bool is a subclass of int in Python)
        return value
    elif isinstance(value, int):
        # Handle int separately without math.isfinite
        return value
    elif isinstance(value, float):
        # Apply math.isfinite only to float, reject NaN and infinity
        if not math.isfinite(value):
            raise ValueError(f"Metadata float values must be finite at {path}")
        return value
    elif isinstance(value, str):
        return value
    else:
        raise ValueError(f"Type {type(value).__name__} at {path} is not JSON compatible")


class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class AgentHeartbeat(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    agent_id: UUID4
    tenant_id: str
    agent_type: AITask
    state: AgentState
    observed_at: datetime
    correlation_id: str | None = None
    result_status: AgentResultStatus | None = None
    latency_ms: float | None = None
    safe_error_code: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("tenant_id", mode="before")
    @classmethod
    def validate_tenant_id(cls, v: Any) -> str:
        if not isinstance(v, str):
            raise ValueError("tenant_id must be a string.")
        v = v.strip()
        if not v:
            raise ValueError("tenant_id must not be blank.")
        if len(v) > 50:
            raise ValueError("tenant_id must be at most 50 characters.")
        return v

    @field_validator("observed_at")
    @classmethod
    def validate_observed_at(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("observed_at must be timezone-aware.")
        return v

    @field_validator("correlation_id", mode="before")
    @classmethod
    def validate_correlation_id(cls, v: Any) -> str | None:
        if v is None:
            return None
        if not isinstance(v, str):
            raise ValueError("correlation_id must be a string.")
        v = v.strip()
        if not v:
            raise ValueError("correlation_id must not be blank.")
        if len(v) > 100:
            raise ValueError("correlation_id must be at most 100 characters.")
        return v

    @field_validator("latency_ms")
    @classmethod
    def validate_latency_ms(cls, v: float | None) -> float | None:
        if v is None:
            return None
        if not math.isfinite(v):
            raise ValueError("latency_ms must be finite.")
        if v < 0:
            raise ValueError("latency_ms must be non-negative.")
        return v

    @field_validator("safe_error_code", mode="before")
    @classmethod
    def validate_safe_error_code(cls, v: Any) -> str | None:
        if v is None:
            return None
        if not isinstance(v, str):
            raise ValueError("safe_error_code must be a string.")
        v = v.strip()
        if not v:
            raise ValueError("safe_error_code must not be blank.")
        if len(v) > 100:
            raise ValueError("safe_error_code must be at most 100 characters.")
        return v

    @field_validator("metadata", mode="before")
    @classmethod
    def validate_metadata(cls, v: Any) -> dict[str, Any]:
        if v is None:
            return {}
        if not isinstance(v, dict):
            raise ValueError("metadata must be a dictionary.")
        # Perform deep copy and recursive validation
        return _check_json_and_keys(v, "metadata")


class AgentHealthSnapshot(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    agent_id: UUID4
    tenant_id: str
    agent_type: AITask
    state: AgentState
    status: HealthStatus
    last_seen_at: datetime
    age_seconds: float = Field(ge=0)
    consecutive_failures: int = Field(ge=0)
    total_heartbeats: int = Field(ge=0)
    total_successes: int = Field(ge=0)
    total_failures: int = Field(ge=0)
    total_timeouts: int = Field(ge=0)
    last_result_status: AgentResultStatus | None = None
    last_latency_ms: float | None = Field(default=None, ge=0)
    average_latency_ms: float | None = Field(default=None, ge=0)
    safe_error_code: str | None = None

    @field_validator("tenant_id", mode="before")
    @classmethod
    def validate_tenant_id(cls, v: Any) -> str:
        if not isinstance(v, str):
            raise ValueError("tenant_id must be a string.")
        v = v.strip()
        if not v:
            raise ValueError("tenant_id must not be blank.")
        if len(v) > 50:
            raise ValueError("tenant_id must be at most 50 characters.")
        return v

    @field_validator("last_seen_at")
    @classmethod
    def validate_last_seen_at(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("last_seen_at must be timezone-aware.")
        return v

    @field_validator("safe_error_code", mode="before")
    @classmethod
    def validate_safe_error_code(cls, v: Any) -> str | None:
        if v is None:
            return None
        if not isinstance(v, str):
            raise ValueError("safe_error_code must be a string.")
        v = v.strip()
        if not v:
            raise ValueError("safe_error_code must not be blank.")
        if len(v) > 100:
            raise ValueError("safe_error_code must be at most 100 characters.")
        return v

    @field_validator("age_seconds", "last_latency_ms", "average_latency_ms")
    @classmethod
    def validate_finite_non_negative_floats(cls, v: float | None) -> float | None:
        if v is None:
            return None
        if not math.isfinite(v):
            raise ValueError("Value must be finite.")
        if v < 0:
            raise ValueError("Value must be non-negative.")
        return v

    @model_validator(mode="after")
    def validate_counters(self) -> Self:
        if self.total_successes + self.total_failures + self.total_timeouts > self.total_heartbeats:
            raise ValueError("Sum of successes, failures, and timeouts must not exceed total_heartbeats.")
        return self


class HealthSummary(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    status: HealthStatus
    checked_at: datetime
    tenant_id: str | None = None
    total_agents: int = Field(ge=0)
    healthy: int = Field(ge=0)
    degraded: int = Field(ge=0)
    unhealthy: int = Field(ge=0)
    unknown: int = Field(ge=0)
    by_agent_type: dict[str, int]

    @field_validator("checked_at")
    @classmethod
    def validate_checked_at(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("checked_at must be timezone-aware.")
        return v

    @field_validator("tenant_id", mode="before")
    @classmethod
    def validate_tenant_id(cls, v: Any) -> str | None:
        if v is None:
            return None
        if not isinstance(v, str):
            raise ValueError("tenant_id must be a string.")
        v = v.strip()
        if not v:
            raise ValueError("tenant_id must not be blank.")
        if len(v) > 50:
            raise ValueError("tenant_id must be at most 50 characters.")
        return v

    @field_validator("by_agent_type")
    @classmethod
    def validate_by_agent_type(cls, v: dict[str, int]) -> dict[str, int]:
        valid_types = {t.value for t in AITask}
        for k, val in v.items():
            if k not in valid_types:
                raise ValueError(f"Invalid agent type in summary: {k}")
            if val < 0:
                raise ValueError(f"Agent count must be non-negative: {val}")
        return v

    @model_validator(mode="after")
    def validate_summary_consistency(self) -> Self:
        if self.healthy + self.degraded + self.unhealthy + self.unknown != self.total_agents:
            raise ValueError("Sum of healthy, degraded, unhealthy, and unknown must equal total_agents.")
        if sum(self.by_agent_type.values()) != self.total_agents:
            raise ValueError("Sum of by_agent_type values must equal total_agents.")
        return self
