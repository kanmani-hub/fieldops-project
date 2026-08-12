"""
contracts.py

Shared contracts for the FieldOps AI guardrail system.

Every guardrail checker returns a GuardrailCheckResult.

The complete guardrail pipeline returns a
GuardrailPipelineResult.

These contracts intentionally do not store:

- Raw generated messages
- Raw prompts
- Customer PII
- Technician PII
- Provider payloads

Only safe rule identifiers and audit-safe metadata may be
stored in guardrail results.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)


# ==========================================================
# Shared Types
# ==========================================================


SafeMetadataValue = (
    str
    | int
    | float
    | bool
    | None
)


GuardrailField = Literal[
    "channel",
    "title",
    "subject",
    "message",
    "tone",
    "confidence",
    "response",
    "output",
]


# ==========================================================
# Enums
# ==========================================================


class GuardrailCategory(StrEnum):
    """
    Supported guardrail violation categories.
    """

    PROFANITY = "PROFANITY"

    LENGTH = "LENGTH"

    TONE = "TONE"

    BRAND_SAFETY = "BRAND_SAFETY"

    PII = "PII"

    CHANNEL_MISMATCH = "CHANNEL_MISMATCH"

    PLACEHOLDER_INTEGRITY = "PLACEHOLDER_INTEGRITY"

    OUTPUT_SCHEMA = "OUTPUT_SCHEMA"
    
    SYSTEM = "SYSTEM"


class GuardrailSeverity(StrEnum):
    """
    Severity assigned to a guardrail violation.
    """

    INFO = "INFO"

    WARNING = "WARNING"

    ERROR = "ERROR"

    CRITICAL = "CRITICAL"


class GuardrailDecision(StrEnum):
    """
    Final action selected by the guardrail pipeline.

    ALLOW:
        The generated communication passed every required
        guardrail and may continue.

    FALLBACK:
        The generated communication must not be used.
        The communication service should render a safe Jinja2
        fallback template.

    BLOCK:
        Neither the generated communication nor a fallback
        should continue. This is reserved for critical cases,
        such as failure to produce any safe output.
    """

    ALLOW = "ALLOW"

    FALLBACK = "FALLBACK"

    BLOCK = "BLOCK"


# ==========================================================
# Guardrail Violation
# ==========================================================


class GuardrailViolation(BaseModel):
    """
    Audit-safe description of one guardrail violation.

    The violation must describe the problem without storing the
    original unsafe or private text.
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        frozen=True,
    )

    code: str = Field(
        ...,
        min_length=3,
        max_length=100,
        pattern=r"^[A-Z][A-Z0-9_]*$",
        description=(
            "Stable machine-readable violation identifier."
        ),
        examples=[
            "SMS_MESSAGE_TOO_LONG",
            "PII_EMAIL_DETECTED",
        ],
    )

    category: GuardrailCategory = Field(
        ...,
        description="Guardrail category.",
    )

    severity: GuardrailSeverity = Field(
        ...,
        description="Violation severity.",
    )

    message: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description=(
            "Safe explanation that must not include the "
            "original generated content or private value."
        ),
    )

    field: GuardrailField | None = Field(
        default=None,
        description=(
            "CommunicationDecision field related to the "
            "violation."
        ),
    )

    safe_metadata: dict[
        str,
        SafeMetadataValue,
    ] = Field(
        default_factory=dict,
        description=(
            "Audit-safe values such as actual length, allowed "
            "limit, detected category, or configured rule ID. "
            "Raw communication content must never be included."
        ),
    )


# ==========================================================
# Individual Guardrail Result
# ==========================================================


class GuardrailCheckResult(BaseModel):
    """
    Result returned by one guardrail checker.

    Examples:

    - ProfanityValidator
    - LengthValidator
    - ToneValidator
    - BrandSafetyValidator
    - PIIOutputDetector
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        frozen=True,
    )

    checker_name: str = Field(
        ...,
        min_length=3,
        max_length=100,
        pattern=r"^[a-z][a-z0-9_]*$",
        description=(
            "Stable lower-snake-case checker identifier."
        ),
        examples=[
            "length_validator",
            "pii_output_detector",
        ],
    )

    passed: bool = Field(
        ...,
        description=(
            "True when the checker found no violations."
        ),
    )

    violations: tuple[
        GuardrailViolation,
        ...,
    ] = Field(
        default_factory=tuple,
        description=(
            "Violations found by this checker."
        ),
    )

    latency_ms: float = Field(
        default=0.0,
        ge=0.0,
        description=(
            "Local execution time for this checker."
        ),
    )

    # ------------------------------------------------------

    @model_validator(
        mode="after"
    )
    def validate_result_state(
        self,
    ) -> Self:
        """
        Keep passed and violations logically consistent.
        """

        if (
            self.passed
            and self.violations
        ):
            raise ValueError(
                "A passed guardrail check cannot contain "
                "violations."
            )

        if (
            not self.passed
            and not self.violations
        ):
            raise ValueError(
                "A failed guardrail check must contain at "
                "least one violation."
            )

        return self


# ==========================================================
# Complete Pipeline Result
# ==========================================================


class GuardrailPipelineResult(BaseModel):
    """
    Final result returned after all communication guardrails
    have run.
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        frozen=True,
    )

    decision: GuardrailDecision = Field(
        ...,
        description=(
            "Final pipeline action."
        ),
    )

    checks: tuple[
        GuardrailCheckResult,
        ...,
    ] = Field(
        default_factory=tuple,
        description=(
            "Ordered results from all executed checkers."
        ),
    )

    violations: tuple[
        GuardrailViolation,
        ...,
    ] = Field(
        default_factory=tuple,
        description=(
            "Flattened collection of all violations."
        ),
    )

    total_latency_ms: float = Field(
        default=0.0,
        ge=0.0,
        description=(
            "Total local guardrail pipeline execution time."
        ),
    )

    reason: str | None = Field(
        default=None,
        min_length=1,
        max_length=500,
        description=(
            "Audit-safe reason for FALLBACK or BLOCK."
        ),
    )

    # ------------------------------------------------------

    @model_validator(
        mode="after"
    )
    def validate_pipeline_state(
        self,
    ) -> Self:
        """
        Ensure the final decision matches the violations.
        """

        if (
            self.decision
            == GuardrailDecision.ALLOW
        ):
            if self.violations:
                raise ValueError(
                    "ALLOW cannot contain violations."
                )

            if self.reason is not None:
                raise ValueError(
                    "ALLOW must not contain a fallback or "
                    "block reason."
                )

        if self.decision in {
            GuardrailDecision.FALLBACK,
            GuardrailDecision.BLOCK,
        }:
            if not self.violations:
                raise ValueError(
                    "FALLBACK or BLOCK requires at least one "
                    "violation."
                )

            if self.reason is None:
                raise ValueError(
                    "FALLBACK or BLOCK requires a reason."
                )

        return self

    # ------------------------------------------------------

    @classmethod
    def from_checks(
        cls,
        *,
        checks: list[
            GuardrailCheckResult
        ]
        | tuple[
            GuardrailCheckResult,
            ...,
        ],
        total_latency_ms: float,
        block: bool = False,
        reason: str | None = None,
    ) -> GuardrailPipelineResult:
        """
        Build a consistent pipeline result from checker results.

        Default behavior:

        - No violations -> ALLOW
        - One or more violations -> FALLBACK
        - block=True with violations -> BLOCK
        """

        check_tuple = tuple(
            checks
        )

        violations = tuple(
            violation
            for check in check_tuple
            for violation in check.violations
        )

        if not violations:
            return cls(
                decision=GuardrailDecision.ALLOW,
                checks=check_tuple,
                violations=(),
                total_latency_ms=total_latency_ms,
                reason=None,
            )

        decision = (
            GuardrailDecision.BLOCK
            if block
            else GuardrailDecision.FALLBACK
        )

        final_reason = (
            reason
            or (
                "Generated communication failed one or more "
                "guardrail checks."
            )
        )

        return cls(
            decision=decision,
            checks=check_tuple,
            violations=violations,
            total_latency_ms=total_latency_ms,
            reason=final_reason,
        )

    # ------------------------------------------------------

    @property
    def passed(
        self,
    ) -> bool:
        """
        Return True only when communication may continue.
        """

        return (
            self.decision
            == GuardrailDecision.ALLOW
        )

    # ------------------------------------------------------

    @property
    def requires_fallback(
        self,
    ) -> bool:
        """
        Return True when a Jinja2 fallback is required.
        """

        return (
            self.decision
            == GuardrailDecision.FALLBACK
        )

    # ------------------------------------------------------

    @property
    def blocked(
        self,
    ) -> bool:
        """
        Return True when processing must stop completely.
        """

        return (
            self.decision
            == GuardrailDecision.BLOCK
        )