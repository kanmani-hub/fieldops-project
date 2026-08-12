from typing import Literal, Optional
from enum import Enum
from pydantic import BaseModel, Field, ConfigDict, field_validator
from datetime import datetime

class UnsupportedCommunicationChannelError(Exception):
    def __init__(self, channel: str):
        super().__init__(f"Unsupported communication channel: {channel}")
        self.channel = channel

class CommunicationConfigurationNotFoundError(Exception):
    def __init__(self, channel: str):
        super().__init__(f"Configuration for channel '{channel}' not found.")
        self.channel = channel

class CommunicationConfigurationUnavailableError(Exception):
    def __init__(self, message: str = "Communication configuration unavailable"):
        super().__init__(message)

class CommunicationConfigurationConflictError(Exception):
    def __init__(self, message: str = "Communication configuration conflict"):
        super().__init__(message)

def normalize_channel(channel: str) -> str:
    normalized = channel.strip().upper()
    if normalized not in {"SMS", "EMAIL"}:
        raise UnsupportedCommunicationChannelError(normalized)
    return normalized
class CommunicationChannelState(str, Enum):
    ENABLED = "ENABLED"
    DISABLED = "DISABLED"
    EMERGENCY_ONLY = "EMERGENCY_ONLY"

class CommunicationMessageCategory(str, Enum):
    STANDARD = "STANDARD"
    EMERGENCY = "EMERGENCY"

class CommunicationChannelStateUpdate(BaseModel):
    state: CommunicationChannelState
    reason: str = Field(..., min_length=10, max_length=500, description="Reason for the state change")
    
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )
        
class CommunicationConfigurationResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    channel: str
    state: CommunicationChannelState
    revision: int
    updated_at: datetime
    updated_by: str

class DeliveryDecision(BaseModel):
    model_config = ConfigDict(frozen=True)
    allowed: bool
    channel: str
    state: CommunicationChannelState
    category: CommunicationMessageCategory
    reason_code: str
    revision: int
    persistent_state: Optional[str] = None
    override_active: Optional[bool] = None
    effective_state: Optional[str] = None
    policy_source: Optional[str] = None

class CommunicationChannelDisabledError(Exception):
    def __init__(self, message: str, decision: DeliveryDecision):
        super().__init__(message)
        self.decision = decision


# ---------------------------------------------------------------------------
# Story 14.3 — Cache payload contract
# ---------------------------------------------------------------------------
# Internal frozen model used exclusively by CommunicationConfigurationService.
# Callers outside the service must never construct or inspect this directly.

_CACHE_PAYLOAD_MAX_BYTES = 512  # safety ceiling; real payloads are ~150 bytes


class CommunicationConfigurationCachePayload(BaseModel):
    """
    Safe, strictly-validated Redis cache payload for one channel configuration.

    Constraints
    -----------
    - No PII, no customer data, no message content, no credentials.
    - Extra fields are forbidden so a schema-version mismatch is caught on read.
    - timezone-aware updated_at preserves the authoritative database timestamp.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
    )

    schema_version: Literal[1]
    channel: Literal["SMS", "EMAIL"]
    state: CommunicationChannelState
    revision: int = Field(..., ge=1)
    updated_at: datetime
    updated_by: str = Field(..., min_length=1, max_length=100)

    @field_validator("updated_at")
    @classmethod
    def validate_timezone_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("updated_at must be timezone-aware")
        return v
