"""
agent_state.py

Pydantic schema for persisted FieldOps AI agent state snapshots.

Story 1.5 — Persistent Agent State.

This schema represents a safe operational snapshot.  It never stores
prompts, AI provider responses, customer payloads, technician private
data, or authentication secrets.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    UUID4,
    field_validator,
    model_validator,
)

from app.services.ai.FieldOpsAI.agents.base import AgentState, BaseAgent
from app.services.ai.FieldOpsAI.schemas.ai_task import AITask


# ---------------------------------------------------------------------------
# Metadata privacy enforcement
# ---------------------------------------------------------------------------

# Keys forbidden in safe_metadata at any nesting level.
# Exact match (case-insensitive) on the key string after lower-casing.
_FORBIDDEN_METADATA_KEYS: frozenset[str] = frozenset({
    # Authentication / secrets
    "api_key",
    "apikey",
    "api_secret",
    "apisecret",
    "auth_token",
    "authtoken",
    "access_token",
    "accesstoken",
    "refresh_token",
    "refreshtoken",
    "bearer_token",
    "bearertoken",
    "secret",
    "password",
    "passwd",
    "credential",
    "credentials",
    "private_key",
    "privatekey",
    "signing_key",
    "signingkey",
    "token",
    # AI provider payloads
    "prompt",
    "system_prompt",
    "systemprompt",
    "response",
    "completion",
    "provider_response",
    "providerresponse",
    "raw_response",
    "rawresponse",
    # Customer PII
    "customer_name",
    "customername",
    "customer_address",
    "customeraddress",
    "customer_email",
    "customeremail",
    "customer_phone",
    "customerphone",
    "phone",
    "phone_number",
    "phonenumber",
    "email",
    "customer_id",
    "customerid",
    # GPS / location
    "gps",
    "latitude",
    "longitude",
    "lat",
    "lng",
    "lon",
    "location",
    "coordinates",
    "address",
    # Generic sensitive
    "ssn",
    "dob",
    "date_of_birth",
    "dateofbirth",
    "credit_card",
    "creditcard",
    "bank_account",
    "bankaccount",
})

# Types whose instances are JSON-compatible scalars.
_JSON_SCALAR_TYPES = (str, int, float, bool, type(None))


def _validate_metadata_value(
    value: Any,
    path: str,
) -> None:
    """
    Recursively validate a metadata value for:

    1. JSON-compatibility (str, int, float, bool, None, dict, list).
    2. No forbidden keys at any nesting depth.

    Raises
    ------
    ValueError
        When a forbidden key or a non-JSON-compatible value is found.
        The error message includes the problematic key path.
        Metadata values are never included in error messages.
    """

    if isinstance(value, _JSON_SCALAR_TYPES):
        return

    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str):
                raise ValueError(
                    f"metadata key at '{path}' must be a string, "
                    f"got {type(k).__name__!r}."
                )
            child_path = f"{path}.{k}" if path else k
            if k.lower() in _FORBIDDEN_METADATA_KEYS:
                raise ValueError(
                    f"metadata key '{child_path}' is forbidden "
                    f"because it may contain sensitive information."
                )
            _validate_metadata_value(v, child_path)
        return

    if isinstance(value, list):
        for i, item in enumerate(value):
            _validate_metadata_value(item, f"{path}[{i}]")
        return

    raise ValueError(
        f"metadata value at '{path}' is not JSON-compatible: "
        f"{type(value).__name__!r}."
    )


def _validate_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """
    Validate metadata dict for privacy and JSON-compatibility.

    Returns the metadata unchanged when all checks pass.
    Raises ValueError on any violation.
    """

    for k, v in metadata.items():
        if not isinstance(k, str):
            raise ValueError(
                f"metadata key must be a string, got {type(k).__name__!r}."
            )
        if k.lower() in _FORBIDDEN_METADATA_KEYS:
            raise ValueError(
                f"metadata key '{k}' is forbidden "
                f"because it may contain sensitive information."
            )
        _validate_metadata_value(v, k)

    return metadata


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class AgentStateSnapshot(BaseModel):
    """
    Safe operational snapshot of one agent's runtime state.

    Used by AgentStateRepository to persist and retrieve agent state
    across process restarts.

    Forbidden fields
    ----------------
    - API keys / authentication secrets / tokens / passwords
    - Prompts or AI provider responses
    - Customer names, addresses, phone numbers, or email addresses
    - Technician GPS, coordinates, or private information
    - Message contents
    - Job payloads

    Metadata privacy
    ----------------
    The ``metadata`` field is validated recursively.  Keys that match
    any forbidden name (case-insensitive, at any nesting depth) cause
    a ``ValidationError``.  Values must be JSON-compatible (str, int,
    float, bool, None, dict, list).  Metadata values are never logged.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=False,
        str_strip_whitespace=True,
    )

    agent_id: UUID4 = Field(
        description="Unique UUID4 identifier for the agent instance.",
    )

    agent_type: AITask = Field(
        description="Type of FieldOps AI agent.",
    )

    tenant_id: str = Field(
        min_length=1,
        max_length=50,
        description="Tenant that owns this agent instance.",
    )

    agent_version: str = Field(
        default="1.0",
        min_length=1,
        max_length=50,
        description="Version of the agent implementation.",
    )

    state: AgentState = Field(
        description="Current lifecycle state of the agent.",
    )

    correlation_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        description="Correlation ID from the last lifecycle event.",
    )

    last_error: str | None = Field(
        default=None,
        min_length=1,
        max_length=500,
        description=(
            "Safe error summary from the last failed execution. "
            "Must contain no secrets, customer data, or full stack traces."
        ),
    )

    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Small safe operational metadata. "
            "Customer context, prompts, AI output, and secrets "
            "must never be stored here.  Validated recursively."
        ),
    )

    created_at: datetime = Field(
        description="UTC timestamp when the state record was first created.",
    )

    updated_at: datetime = Field(
        description="UTC timestamp of the last state update.",
    )

    @field_validator("tenant_id", mode="after")
    @classmethod
    def tenant_id_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("tenant_id must not be blank.")
        return value

    @field_validator("created_at", "updated_at", mode="after")
    @classmethod
    def timestamps_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Timestamps must be timezone-aware.")
        return value

    @field_validator("metadata", mode="after")
    @classmethod
    def metadata_must_be_safe(cls, value: dict[str, Any]) -> dict[str, Any]:
        """
        Validate metadata for privacy and JSON-compatibility.

        Metadata values are never included in error messages.
        """
        return _validate_metadata(value)

    @model_validator(mode="after")
    def updated_at_not_before_created_at(self) -> "AgentStateSnapshot":
        if self.updated_at < self.created_at:
            raise ValueError(
                "updated_at must not be earlier than created_at."
            )
        return self

    @classmethod
    def from_agent(
        cls,
        agent: BaseAgent[Any],
        *,
        correlation_id: str | None = None,
        last_error: str | None = None,
        metadata: dict[str, Any] | None = None,
        created_at: datetime | None = None,
    ) -> "AgentStateSnapshot":
        """
        Create a state snapshot from a live BaseAgent instance.

        Only reads public properties.  Does not modify the agent.

        The caller's metadata dict is deep-copied so nested objects
        are isolated.

        Parameters
        ----------
        agent:
            Live BaseAgent instance to snapshot.
        correlation_id:
            Optional correlation ID from the triggering lifecycle event.
        last_error:
            Optional safe error summary (trimmed, max 500 chars).
        metadata:
            Optional operational metadata dict.  Deep-copied before use.
        created_at:
            Override the created_at timestamp.  Uses now() when omitted.
        """

        safe_error: str | None = None
        if last_error is not None:
            trimmed = last_error.strip()[:500]
            safe_error = trimmed if trimmed else None

        safe_metadata: dict[str, Any] = (
            copy.deepcopy(metadata) if metadata is not None else {}
        )

        now = datetime.now(timezone.utc)
        resolved_created_at = created_at if created_at is not None else now

        return cls(
            agent_id=agent.agent_id,
            agent_type=agent.config.agent_type,
            tenant_id=agent.tenant_id,
            agent_version=agent.config.agent_version,
            state=agent.state,
            correlation_id=correlation_id,
            last_error=safe_error,
            metadata=safe_metadata,
            created_at=resolved_created_at,
            updated_at=now,
        )
