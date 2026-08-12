"""
length_validator.py

Channel-specific length validation for FieldOps-generated
communication.

Rules
-----
- SMS message: maximum 160 characters
- Email subject: maximum 78 characters
- Push title: maximum 50 characters
- In-app: no explicit length limit in Story 0.4

This validator:

- Does not truncate generated content
- Does not modify CommunicationDecision
- Does not render Jinja2 templates
- Does not log raw message content

It returns an audit-safe GuardrailCheckResult.
"""

from __future__ import annotations

from time import perf_counter
from typing import Final

from app.services.ai.FieldOpsAI.schemas.communication import (
    CommunicationChannel,
    CommunicationContext,
    CommunicationDecision,
)
from app.services.ai.guardrails.contracts import (
    GuardrailCategory,
    GuardrailCheckResult,
    GuardrailField,
    GuardrailSeverity,
    GuardrailViolation,
)


class LengthValidator:
    """
    Validate channel-specific communication length limits.
    """

    checker_name: Final[str] = "length_validator"

    SMS_MESSAGE_MAX_LENGTH: Final[int] = 160

    EMAIL_SUBJECT_MAX_LENGTH: Final[int] = 78

    PUSH_TITLE_MAX_LENGTH: Final[int] = 50

    # ------------------------------------------------------

    def check(
        self,
        *,
        context: CommunicationContext,
        decision: CommunicationDecision,
    ) -> GuardrailCheckResult:
        """
        Check the generated decision against channel limits.

        The context parameter is part of the shared guardrail
        interface. Length validation currently uses the channel
        and content from CommunicationDecision.

        Returns:
            GuardrailCheckResult containing either:

            - passed=True with no violations
            - passed=False with one length violation
        """

        started_at = perf_counter()

        violations: list[
            GuardrailViolation
        ] = []

        if decision.channel == "SMS":
            violation = self._check_max_length(
                value=decision.output.text,
                maximum_length=(
                    self.SMS_MESSAGE_MAX_LENGTH
                ),
                code="SMS_MESSAGE_TOO_LONG",
                field="output",
                channel=decision.channel,
                safe_message=(
                    "SMS message exceeds the configured "
                    "character limit."
                ),
            )

            if violation is not None:
                violations.append(
                    violation
                )

        elif decision.channel == "EMAIL":
            if decision.output.subject is not None:
                violation = self._check_max_length(
                    value=decision.output.subject,
                    maximum_length=(
                        self.EMAIL_SUBJECT_MAX_LENGTH
                    ),
                    code="EMAIL_SUBJECT_TOO_LONG",
                    field="output",
                    channel=decision.channel,
                    safe_message=(
                        "Email subject exceeds the configured "
                        "character limit."
                    ),
                )

                if violation is not None:
                    violations.append(
                        violation
                    )

        elif decision.channel == "PUSH":
            if decision.output.title is not None:
                violation = self._check_max_length(
                    value=decision.output.title,
                    maximum_length=(
                        self.PUSH_TITLE_MAX_LENGTH
                    ),
                    code="PUSH_TITLE_TOO_LONG",
                    field="output",
                    channel=decision.channel,
                    safe_message=(
                        "Push title exceeds the configured "
                        "character limit."
                    ),
                )

                if violation is not None:
                    violations.append(
                        violation
                    )

        latency_ms = (
            perf_counter()
            - started_at
        ) * 1000

        return GuardrailCheckResult(
            checker_name=self.checker_name,
            passed=not violations,
            violations=tuple(
                violations
            ),
            latency_ms=latency_ms,
        )

    # ------------------------------------------------------

    @staticmethod
    def _check_max_length(
        *,
        value: str,
        maximum_length: int,
        code: str,
        field: GuardrailField,
        channel: CommunicationChannel,
        safe_message: str,
    ) -> GuardrailViolation | None:
        """
        Compare one string with its configured maximum length.

        Returns:
            None:
                The value is within the allowed limit.

            GuardrailViolation:
                The value exceeds the allowed limit.

        Only safe measurements are included in metadata.
        The original message, title, or subject is never stored.
        """

        actual_length = len(
            value
        )

        if (
            actual_length
            <= maximum_length
        ):
            return None

        return GuardrailViolation(
            code=code,
            category=GuardrailCategory.LENGTH,
            severity=GuardrailSeverity.ERROR,
            message=safe_message,
            field=field,
            safe_metadata={
                "channel": channel,
                "actual_length": actual_length,
                "maximum_length": maximum_length,
            },
        )