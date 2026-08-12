from pydantic import BaseModel, ConfigDict, model_validator
from typing import Literal, Optional
from datetime import datetime

CustomerPreferenceChannel = Literal["SMS", "EMAIL", "PUSH", "PORTAL"]

class CustomerPreferenceValues(BaseModel):
    sms_enabled: bool
    email_enabled: bool
    push_enabled: bool
    portal_enabled: bool
    preferred_locale: str

    model_config = ConfigDict(extra="forbid", frozen=True)

class CustomerPreferenceUpdate(BaseModel):
    sms_enabled: Optional[bool] = None
    email_enabled: Optional[bool] = None
    push_enabled: Optional[bool] = None
    portal_enabled: Optional[bool] = None
    preferred_locale: Optional[str] = None

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_update(self):
        if not self.model_fields_set:
            raise ValueError(
                "At least one update field must be provided."
            )

        null_fields = [
            field
            for field in self.model_fields_set
            if getattr(self, field) is None
        ]

        if null_fields:
            raise ValueError(
                "Explicit null preference values are not allowed."
            )

        return self

class CustomerPreferenceResponse(BaseModel):
    profile_id: Optional[str]
    tenant_id: str
    customer_id: str
    sms_enabled: bool
    email_enabled: bool
    push_enabled: bool
    portal_enabled: bool
    preferred_locale: str
    revision: int
    source: Literal["PROFILE", "COMPATIBILITY_DEFAULT"]
    updated_at: Optional[datetime]
    updated_by: Optional[str]

    model_config = ConfigDict(extra="forbid", frozen=True)

class CustomerPreferenceDecision(BaseModel):
    allowed: bool
    channel: CustomerPreferenceChannel
    reason_code: str
    source: Literal["PROFILE", "COMPATIBILITY_DEFAULT"]
    revision: int

    model_config = ConfigDict(extra="forbid", frozen=True)
