"""
agent_subscription.py

Public metadata record for an AgentBus subscription.

Story 1.6 — Agent Communication Bus.

AgentSubscription holds metadata only. The handler callable is stored
privately inside the bus and is never exposed externally.

Separation of concerns
-----------------------
- AgentSubscription  — public metadata (ID, tenant, topic, subscriber, created_at).
- AgentBus internals — private handler storage.

Rules
-----
- subscriber=None represents a broadcast-only subscription.
- When subscriber is supplied, subscriber.tenant_id must match tenant_id.
- Topic validation uses the shared _validate_topic helper.
- created_at must be timezone-aware.
- The model is immutable.
- No handler, callable, or live agent is stored.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.ai.FieldOpsAI.schemas.agent_messages import (
    AgentAddress,
    _validate_topic,
)


class AgentSubscription(BaseModel):
    """
    Immutable public record describing one AgentBus subscription.

    Attributes
    ----------
    subscription_id:
        Unique UUID4 identifying this subscription.
    tenant_id:
        Non-blank tenant that owns this subscription.
    topic:
        Normalized (lowercase, stripped) routing topic.
    subscriber:
        Optional AgentAddress of the targeted subscriber.
        None means this subscription receives broadcasts only.
    created_at:
        Timezone-aware UTC timestamp when the subscription was created.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    subscription_id: UUID = Field(
        description="Unique identifier for this subscription.",
    )

    tenant_id: str = Field(
        min_length=1,
        max_length=50,
        description="Tenant that owns this subscription.",
    )

    topic: str = Field(
        min_length=1,
        max_length=100,
        description="Normalized routing topic.",
    )

    subscriber: Optional[AgentAddress] = Field(
        default=None,
        description=(
            "Targeted subscriber address, or None for broadcast-only subscriptions."
        ),
    )

    created_at: datetime = Field(
        description="UTC timestamp when the subscription was created.",
    )

    @field_validator("tenant_id", mode="before")
    @classmethod
    def validate_tenant_id(cls, value: str) -> str:
        """
        Strip and validate tenant_id is non-blank.
        """
        if not isinstance(value, str):
            raise ValueError("tenant_id must be a string.")
        value = value.strip()
        if not value:
            raise ValueError("tenant_id must not be blank.")
        return value

    @field_validator("topic", mode="before")
    @classmethod
    def validate_topic(cls, value: str) -> str:
        """
        Validate topic using the shared helper.
        """
        return _validate_topic(value)

    @field_validator("created_at", mode="after")
    @classmethod
    def validate_created_at_aware(cls, value: datetime) -> datetime:
        """
        Reject naive datetimes.
        """
        if value.tzinfo is None:
            raise ValueError("created_at must be timezone-aware.")
        return value

    @model_validator(mode="after")
    def validate_subscriber_tenant(self) -> "AgentSubscription":
        """
        When subscriber is set, require subscriber.tenant_id == tenant_id.
        """
        if self.subscriber is not None:
            if self.subscriber.tenant_id != self.tenant_id:
                raise ValueError(
                    "subscriber.tenant_id must match subscription tenant_id. "
                    f"Got subscriber.tenant_id={self.subscriber.tenant_id!r} "
                    f"but tenant_id={self.tenant_id!r}."
                )
        return self
