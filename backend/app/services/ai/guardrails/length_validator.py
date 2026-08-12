"""
Channel-specific length validation for FieldOps communication.

Story 8.5:
- GSM-7 SMS: maximum 160 characters/septets
- Unicode SMS: maximum 70 characters
- Email subject: maximum 78 characters
- Push title: maximum 50 characters
- Push body: maximum 200 characters

The validator does not modify CommunicationDecision.
SMS truncation is provided as a separate helper.
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
    """Validate channel-specific communication length limits."""

    checker_name: Final[str] = "length_validator"

    SMS_GSM7_MAX_LENGTH: Final[int] = 160
    SMS_UNICODE_MAX_LENGTH: Final[int] = 70
    EMAIL_SUBJECT_MAX_LENGTH: Final[int] = 78
    PUSH_TITLE_MAX_LENGTH: Final[int] = 50
    PUSH_BODY_MAX_LENGTH: Final[int] = 200

    SMS_TRUNCATION_SUFFIX: Final[str] = "..."

    # GSM-7 basic character set.
    GSM7_BASIC: Final[frozenset[str]] = frozenset(
        "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ"
        + " !\"#¤%&'()*+,-./"
        + "0123456789:;<=>?"
        + "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§"
        + "¿abcdefghijklmnopqrstuvwxyzäöñüà"
    )

    # GSM-7 extended characters consume two septets.
    GSM7_EXTENDED: Final[frozenset[str]] = frozenset(
        "^{}\\[~]|€"
    )

    def check(
        self,
        *,
        context: CommunicationContext,
        decision: CommunicationDecision,
    ) -> GuardrailCheckResult:
        """
        Validate the generated communication.

        This method never modifies the CommunicationDecision.
        """

        started_at = perf_counter()
        violations: list[GuardrailViolation] = []

        if decision.channel == "SMS":
            violation = self._check_sms(
                decision.message
            )

            if violation is not None:
                violations.append(violation)

        elif decision.channel == "EMAIL":
            violation = self._check_max_length(
                value=decision.subject,
                maximum_length=self.EMAIL_SUBJECT_MAX_LENGTH,
                code="EMAIL_SUBJECT_TOO_LONG",
                field="output",
                channel=decision.channel,
                safe_message=(
                    "Email subject exceeds the configured "
                    "character limit."
                ),
            )

            if violation is not None:
                violations.append(violation)

        elif decision.channel == "PUSH":
            title_violation = self._check_max_length(
                value=decision.title or "",
                maximum_length=self.PUSH_TITLE_MAX_LENGTH,
                code="PUSH_TITLE_TOO_LONG",
                field="output",
                channel=decision.channel,
                safe_message=(
                    "Push title exceeds the configured "
                    "character limit."
                ),
            )

            if title_violation is not None:
                violations.append(title_violation)

            body_violation = self._check_max_length(
                value=decision.message,
                maximum_length=self.PUSH_BODY_MAX_LENGTH,
                code="PUSH_BODY_TOO_LONG",
                field="output",
                channel=decision.channel,
                safe_message=(
                    "Push body exceeds the configured "
                    "character limit."
                ),
            )

            if body_violation is not None:
                violations.append(body_violation)

        # IN_APP currently has no configured length limit.
        elif decision.channel == "IN_APP":
            pass

        latency_ms = (
            perf_counter() - started_at
        ) * 1000

        return GuardrailCheckResult(
            checker_name=self.checker_name,
            passed=not violations,
            violations=tuple(violations),
            latency_ms=latency_ms,
        )

    # ======================================================
    # SMS
    # ======================================================

    @classmethod
    def is_gsm7(cls, value: str) -> bool:
        """
        Return True when every character belongs to GSM-7.

        Extended GSM-7 characters such as ^, {, }, [, ], | and €
        are supported.
        """

        return all(
            character in cls.GSM7_BASIC
            or character in cls.GSM7_EXTENDED
            for character in value
        )

    @classmethod
    def sms_limit(cls, value: str) -> int:
        """
        Return the SMS character limit for the message encoding.

        GSM-7 messages use 160.
        Unicode messages use 70.
        """

        if cls.is_gsm7(value):
            return cls.SMS_GSM7_MAX_LENGTH

        return cls.SMS_UNICODE_MAX_LENGTH

    @classmethod
    def sms_length(cls, value: str) -> int:
        """
        Return the GSM-7 septet length or Unicode character length.

        GSM-7 extended characters consume two septets.
        """

        if not cls.is_gsm7(value):
            return len(value)

        length = 0

        for character in value:
            if character in cls.GSM7_EXTENDED:
                length += 2
            else:
                length += 1

        return length

    @classmethod
    def truncate_sms(
        cls,
        value: str,
        *,
        full_message_link: str | None = None,
    ) -> str:
        """
        Truncate an SMS while respecting its transport limit.

        The returned value includes "..." when truncation occurs.

        If full_message_link is supplied, it is appended after the
        truncation suffix.

        Raises:
            ValueError:
                If the suffix/link cannot fit inside the SMS limit.
        """

        limit = cls.sms_limit(value)

        if cls.sms_length(value) <= limit:
            return value

        suffix = cls.SMS_TRUNCATION_SUFFIX

        if full_message_link:
            suffix = f"{suffix} {full_message_link}"

        suffix_length = cls.sms_length(suffix)

        if suffix_length >= limit:
            raise ValueError(
                "SMS truncation suffix and full-message link "
                "cannot fit within the configured SMS limit."
            )

        available = limit - suffix_length

        result: list[str] = []
        current_length = 0

        for character in value:
            character_length = (
                2
                if character in cls.GSM7_EXTENDED
                and cls.is_gsm7(value)
                else 1
            )

            if current_length + character_length > available:
                break

            result.append(character)
            current_length += character_length

        truncated = "".join(result) + suffix

        if cls.sms_length(truncated) > limit:
            raise ValueError(
                "Unable to truncate SMS within the configured limit."
            )

        return truncated

    @classmethod
    def _check_sms(
        cls,
        value: str,
    ) -> GuardrailViolation | None:
        detected_encoding = (
            "GSM-7"
            if cls.is_gsm7(value)
            else "UNICODE"
        )

        actual_length = cls.sms_length(value)
        maximum_length = cls.sms_limit(value)

        if actual_length <= maximum_length:
            return None

        return GuardrailViolation(
            code="SMS_MESSAGE_TOO_LONG",
            category=GuardrailCategory.LENGTH,
            severity=GuardrailSeverity.ERROR,
            message=(
                "SMS message exceeds the configured "
                "character limit."
            ),
            field="message",
            safe_metadata={
                "channel": "SMS",
                "encoding": detected_encoding,
                "actual_length": actual_length,
                "maximum_length": maximum_length,
                "truncatable": True,
            },
        )

    # ======================================================
    # Generic validation
    # ======================================================

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
        """Validate one string against a maximum length."""

        actual_length = len(value)

        if actual_length <= maximum_length:
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