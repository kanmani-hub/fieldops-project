from __future__ import annotations

import re
from enum import Enum
from typing import List, Literal, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.services.ai.FieldOpsAI.schemas.prompt_variable import PromptVariableDeclaration
from app.services.ai.FieldOpsAI.services.prompt_variable_injector import (
    PromptVariableInjector,
    PromptVariableInjectionError,
)
from app.services.ai.FieldOpsAI.services.prompt_locale_service import (
    normalize_locale,
    InvalidLocaleError,
)


# ==========================================================
# Supported values
# ==========================================================


TemplateFormat = Literal["text", "html"]

_VALID_FORMATS: frozenset[str] = frozenset({"text", "html"})


def _validate_format_value(value: str) -> str:
    """
    Normalize and validate a template format.

    Accepts surrounding whitespace and uppercase, then
    rejects any value that is not ``text`` or ``html``.
    """
    if not isinstance(value, str):
        raise ValueError("Format must be a string.")

    normalized = value.strip().lower()

    if normalized not in _VALID_FORMATS:
        raise ValueError(
            "Format must be 'text' or 'html'."
        )

    return normalized


class AgentType(str, Enum):
    CommsAgent = "CommsAgent"
    SentimentAgent = "SentimentAgent"


class PromptChannel(str, Enum):
    sms = "sms"
    email = "email"
    push = "push"
    portal = "portal"

    @classmethod
    def _missing_(
        cls,
        value,
    ):
        # The existing database uses "in_app".
        # The Task 5.1 API exposes it as "portal".
        if value == "in_app":
            return cls.portal

        return super()._missing_(value)


class PromptLanguage(str, Enum):
    en = "en"
    es = "es"
    ta = "ta"
    hi = "hi"


DEFAULT_TEMPLATE_STATUS = "default"


class MessageTemplateStatus(str, Enum):
    CREATED = "created"
    ASSIGNED = "assigned"
    ENROUTE = "enroute"
    ONSITE = "onsite"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class UnsupportedTemplateStatusError(ValueError):
    """Raised when a template status cannot be normalized to a canonical status."""
    pass


LEGACY_TEMPLATE_STATUS_ALIASES: dict[str, MessageTemplateStatus] = {
    # CREATED
    "created": MessageTemplateStatus.CREATED,
    "job_created": MessageTemplateStatus.CREATED,
    
    # ASSIGNED
    "assigned": MessageTemplateStatus.ASSIGNED,
    "job_assigned": MessageTemplateStatus.ASSIGNED,
    "technician_job_assigned": MessageTemplateStatus.ASSIGNED,
    "dispatcher_job_assigned": MessageTemplateStatus.ASSIGNED,

    # ENROUTE
    "enroute": MessageTemplateStatus.ENROUTE,
    "en_route": MessageTemplateStatus.ENROUTE,
    "technician_journey_started": MessageTemplateStatus.ENROUTE,
    "journey_started": MessageTemplateStatus.ENROUTE,
    "technician_en_route": MessageTemplateStatus.ENROUTE,
    "dispatcher_en_route": MessageTemplateStatus.ENROUTE,
    "job_en_route": MessageTemplateStatus.ENROUTE,

    # ONSITE
    "onsite": MessageTemplateStatus.ONSITE,
    "on_site": MessageTemplateStatus.ONSITE,
    "technician_arrived_on_site": MessageTemplateStatus.ONSITE,
    "technician_arrived": MessageTemplateStatus.ONSITE,
    "arrived_on_site": MessageTemplateStatus.ONSITE,
    "dispatcher_on_site": MessageTemplateStatus.ONSITE,
    "job_on_site": MessageTemplateStatus.ONSITE,

    # COMPLETED
    "completed": MessageTemplateStatus.COMPLETED,
    "complete": MessageTemplateStatus.COMPLETED,
    "job_completed": MessageTemplateStatus.COMPLETED,
    "technician_job_completed": MessageTemplateStatus.COMPLETED,
    "dispatcher_completed": MessageTemplateStatus.COMPLETED,
    "job_done_survey": MessageTemplateStatus.COMPLETED,
    "customer_job_completed": MessageTemplateStatus.COMPLETED,

    # CANCELLED
    "cancelled": MessageTemplateStatus.CANCELLED,
    "canceled": MessageTemplateStatus.CANCELLED,
    "job_cancelled": MessageTemplateStatus.CANCELLED,
    "job_canceled": MessageTemplateStatus.CANCELLED,
    "technician_job_cancelled": MessageTemplateStatus.CANCELLED,
    "dispatcher_job_cancelled": MessageTemplateStatus.CANCELLED,
    "job_cancelled_customer": MessageTemplateStatus.CANCELLED,
    "customer_job_cancelled": MessageTemplateStatus.CANCELLED,
}


STATUS_LOOKUP_CANDIDATES: dict[MessageTemplateStatus, tuple[str, ...]] = {
    MessageTemplateStatus.CREATED: (
        "created",
        "job_created",
    ),
    MessageTemplateStatus.ASSIGNED: (
        "assigned",
        "job_assigned",
        "technician_job_assigned",
        "dispatcher_job_assigned",
    ),
    MessageTemplateStatus.ENROUTE: (
        "enroute",
        "en_route",
        "job_en_route",
        "technician_en_route",
        "technician_journey_started",
        "journey_started",
        "dispatcher_en_route",
    ),
    MessageTemplateStatus.ONSITE: (
        "onsite",
        "on_site",
        "job_on_site",
        "technician_arrived_on_site",
        "technician_arrived",
        "arrived_on_site",
        "dispatcher_on_site",
    ),
    MessageTemplateStatus.COMPLETED: (
        "completed",
        "complete",
        "job_completed",
        "technician_job_completed",
        "dispatcher_completed",
        "job_done_survey",
        "customer_job_completed",
    ),
    MessageTemplateStatus.CANCELLED: (
        "cancelled",
        "canceled",
        "job_cancelled",
        "job_canceled",
        "technician_job_cancelled",
        "dispatcher_job_cancelled",
        "job_cancelled_customer",
        "customer_job_cancelled",
    ),
}


def normalize_template_status(
    value: str,
    *,
    allow_default: bool = False,
) -> MessageTemplateStatus | str:
    """
    Normalize and validate a template status string into a canonical MessageTemplateStatus enum or DEFAULT_TEMPLATE_STATUS.

    Requires a string, strips whitespace, converts case/separators, and maps approved aliases.
    If allow_default is True, "default" is accepted and returned as DEFAULT_TEMPLATE_STATUS.
    Rejects unsupported values with UnsupportedTemplateStatusError.
    """
    if not isinstance(value, str):
        raise UnsupportedTemplateStatusError("Status must be a string.")

    stripped = value.strip()
    if not stripped:
        raise UnsupportedTemplateStatusError("Status cannot be blank.")

    if len(stripped) > 50 or re.search(r"[\x00-\x1f\x7f-\x9f]", stripped):
        raise UnsupportedTemplateStatusError("Unsupported message template status.")

    normalized = stripped.lower().replace("-", "_")

    if allow_default and normalized == DEFAULT_TEMPLATE_STATUS:
        return DEFAULT_TEMPLATE_STATUS

    try:
        return MessageTemplateStatus(normalized)
    except ValueError:
        pass

    if normalized in LEGACY_TEMPLATE_STATUS_ALIASES:
        return LEGACY_TEMPLATE_STATUS_ALIASES[normalized]

    raise UnsupportedTemplateStatusError(f"Unsupported message template status '{value}'.")


# ==========================================================
# Shared validation helpers
# ==========================================================


def _validate_status_value(
    value: str,
    *,
    allow_default: bool = True,
) -> str:
    """
    Normalize and validate a prompt status.
    """
    try:
        res = normalize_template_status(value, allow_default=allow_default)
        return res.value if hasattr(res, "value") else str(res)
    except UnsupportedTemplateStatusError as err:
        raise ValueError(str(err)) from None


def _validate_jinja_variables(
    body: str,
    variables: list[PromptVariableDeclaration],
    title: Optional[str] = None,
) -> None:
    try:
        PromptVariableInjector().validate(body, variables, title)
    except PromptVariableInjectionError:
        raise ValueError("Template validation failed.") from None

# ==========================================================
# Base model
# ==========================================================


class PromptTemplateBase(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )

    name: str = Field(
        ...,
        min_length=1,
    )

    agent_type: AgentType

    channel: PromptChannel

    language: str

    @field_validator("language")
    @classmethod
    def validate_language(cls, value: str) -> str:
        try:
            return normalize_locale(value)
        except InvalidLocaleError:
            raise ValueError("Template validation failed.") from None

    status: str = Field(
        ...,
        min_length=1,
    )

    body: str = Field(
        ...,
        min_length=1,
    )

    format: TemplateFormat = Field(
        default="text",
    )

    @field_validator(
        "format",
        mode="before",
    )
    @classmethod
    def validate_format(
        cls,
        value: str,
    ) -> str:
        return _validate_format_value(value)

    title: Optional[str] = None

    variables: List[PromptVariableDeclaration] = Field(
        default_factory=list
    )

    is_active: bool = True

    @field_validator("name")
    @classmethod
    def validate_name(
        cls,
        value: Optional[str],
    ) -> Optional[str]:
        if value is None:
            return None

        normalized = value.strip()

        if not normalized:
            raise ValueError(
                "Name cannot be blank."
            )

        return normalized

    @field_validator("status")
    @classmethod
    def validate_status(
        cls,
        value: str,
    ) -> str:
        return _validate_status_value(
            value
        )




# ==========================================================
# Create
# ==========================================================


class PromptTemplateCreate(
    PromptTemplateBase
):
    @model_validator(mode="after")
    def validate_create_content(
        self,
    ) -> "PromptTemplateCreate":
        _validate_jinja_variables(
            body=self.body,
            variables=self.variables,
            title=self.title,
        )

        return self


# ==========================================================
# Update
# ==========================================================


class PromptTemplateUpdate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )

    name: Optional[str] = Field(
        default=None,
        min_length=1,
    )

    agent_type: Optional[
        AgentType
    ] = None

    channel: Optional[
        PromptChannel
    ] = None

    language: Optional[str] = None

    @field_validator("language")
    @classmethod
    def validate_language(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        try:
            return normalize_locale(value)
        except InvalidLocaleError:
            raise ValueError("Template validation failed.") from None

    status: Optional[str] = Field(
        default=None,
        min_length=1,
    )

    body: Optional[str] = Field(
        default=None,
        min_length=1,
    )

    title: Optional[str] = None

    variables: Optional[
        List[PromptVariableDeclaration]
    ] = None

    format: Optional[TemplateFormat] = None

    is_active: Optional[bool] = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None

        normalized = value.strip()

        if not normalized:
            raise ValueError(
                "Name cannot be blank."
            )

        return normalized

    @field_validator(
        "format",
        mode="before",
    )
    @classmethod
    def validate_format(
        cls,
        value: Optional[str],
    ) -> Optional[str]:
        if value is None:
            return None

        return _validate_format_value(value)

    @field_validator("status")
    @classmethod
    def validate_status(
        cls,
        value: Optional[str],
    ) -> Optional[str]:
        if value is None:
            return None

        return _validate_status_value(
            value
        )


# ==========================================================
# Standard response
# ==========================================================


class PromptTemplateResponse(
    PromptTemplateBase
):
    id: int

    version: int = Field(
        ge=1
    )


# ==========================================================
# Lookup response
# ==========================================================


class PromptTemplateLookupResponse(
    BaseModel
):
    model_config = ConfigDict(
        extra="forbid",
    )

    id: Optional[int]

    name: str = Field(
        min_length=1
    )

    agent_type: AgentType

    channel: PromptChannel

    language: str

    @field_validator("language")
    @classmethod
    def validate_language(cls, value: str) -> str:
        try:
            return normalize_locale(value)
        except InvalidLocaleError:
            raise ValueError("Template validation failed.") from None

    status: str = Field(
        min_length=1
    )

    body: str = Field(
        min_length=1
    )

    format: TemplateFormat = Field(
        default="text",
    )

    @field_validator(
        "format",
        mode="before",
    )
    @classmethod
    def validate_format(
        cls,
        value: str,
    ) -> str:
        return _validate_format_value(value)

    title: Optional[str] = None

    variables: List[PromptVariableDeclaration] = Field(
        default_factory=list
    )

    version: Optional[int] = Field(
        default=None,
        ge=1,
    )

    is_active: bool

    source: Literal[
        "tenant",
        "platform",
        "builtin_default",
    ]

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return value.strip()

    @field_validator("status")
    @classmethod
    def validate_status(
        cls,
        value: str,
    ) -> str:
        return _validate_status_value(
            value
        )