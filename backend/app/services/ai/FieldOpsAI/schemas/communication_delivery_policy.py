from typing import Literal, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    field_validator,
)

from .communication import CommunicationRecipient
from .communication_configuration import (
    CommunicationChannelState,
    CommunicationMessageCategory,
)


CommunicationDeliveryChannel = Literal[
    "SMS",
    "EMAIL",
]


class CommunicationDeliveryEligibilityInput(BaseModel):
    """
    Strict input for the final SMS/EMAIL delivery policy.
    """

    channel: CommunicationDeliveryChannel
    category: CommunicationMessageCategory
    recipient_type: CommunicationRecipient

    tenant_id: Optional[str] = None
    customer_id: Optional[str] = None

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    @field_validator(
        "channel",
        "recipient_type",
        mode="before",
    )
    @classmethod
    def normalize_uppercase_value(
        cls,
        value,
    ):
        if not isinstance(value, str):
            raise ValueError(
                "Value must be a string."
            )

        normalized = value.strip().upper()

        if not normalized:
            raise ValueError(
                "Value must not be blank."
            )

        return normalized


class CommunicationDeliveryEligibilityDecision(BaseModel):
    allowed: bool
    channel: CommunicationDeliveryChannel
    category: CommunicationMessageCategory
    recipient_type: CommunicationRecipient

    global_allowed: bool
    global_state: CommunicationChannelState
    global_reason_code: str
    global_revision: int

    preference_applied: bool
    preference_allowed: Optional[bool] = None
    preference_reason_code: Optional[str] = None
    preference_source: Optional[str] = None
    preference_revision: Optional[int] = None

    final_reason_code: str

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )