"""
test_length_validator.py

Tests for channel-specific communication length guardrails.
"""

from __future__ import annotations

from app.services.ai.FieldOpsAI.schemas.communication import (
    CommunicationContext,
    CommunicationDecision,
)
from app.services.ai.guardrails.base import (
    GuardrailChecker,
)
from app.services.ai.guardrails.contracts import (
    GuardrailCategory,
    GuardrailSeverity,
)
from app.services.ai.guardrails.length_validator import (
    LengthValidator,
)


# ==========================================================
# Test Helpers
# ==========================================================


def build_context(
    channel: str,
) -> CommunicationContext:
    """
    Build a valid communication context for one channel.
    """

    return CommunicationContext(
        job_id="JOB-1001",
        notification_type="job_assigned",
        recipient_type="CUSTOMER",
        channel=channel,
        job_status="ASSIGNED",
    )


# ==========================================================
# Interface Test
# ==========================================================


def test_length_validator_implements_guardrail_interface() -> None:
    """
    LengthValidator follows the common GuardrailChecker
    contract.
    """

    validator = LengthValidator()

    assert isinstance(
        validator,
        GuardrailChecker,
    )


# ==========================================================
# SMS Tests
# ==========================================================


def test_sms_message_at_limit_passes() -> None:
    """
    An SMS containing exactly 160 characters is valid.
    """

    context = build_context(
        "SMS"
    )

    decision = CommunicationDecision(
        channel="SMS",
        title=None,
        subject=None,
        message="A" * 160,
        tone="PROFESSIONAL",
        confidence=0.95,
    )

    result = LengthValidator().check(
        context=context,
        decision=decision,
    )

    assert result.passed is True
    assert result.violations == ()


def test_sms_message_at_transport_limit_passes() -> None:
    context = build_context("SMS")

    decision = CommunicationDecision(
        channel="SMS",
        title=None,
        subject=None,
        message="A" * 170,
        tone="PROFESSIONAL",
        confidence=0.95,
    )

    # CommunicationDecision already normalizes SMS to 160.
    assert len(decision.message) == 160

    result = LengthValidator().check(
        context=context,
        decision=decision,
    )

    assert result.passed is True


# ==========================================================
# Email Tests
# ==========================================================


def test_email_subject_at_limit_passes() -> None:
    """
    An email subject containing exactly 78 characters is valid.
    """

    context = build_context(
        "EMAIL"
    )

    decision = CommunicationDecision(
        channel="EMAIL",
        title=None,
        subject="A" * 78,
        message="Your service request has been updated.",
        tone="PROFESSIONAL",
        confidence=0.95,
    )

    result = LengthValidator().check(
        context=context,
        decision=decision,
    )

    assert result.passed is True
    assert result.violations == ()


def test_email_subject_over_limit_fails() -> None:
    """
    An email subject containing 79 characters must fail.
    """

    context = build_context(
        "EMAIL"
    )

    decision = CommunicationDecision(
        channel="EMAIL",
        title=None,
        subject="A" * 79,
        message="Your service request has been updated.",
        tone="PROFESSIONAL",
        confidence=0.95,
    )

    result = LengthValidator().check(
        context=context,
        decision=decision,
    )

    assert result.passed is False

    violation = result.violations[0]

    assert (
        violation.code
        == "EMAIL_SUBJECT_TOO_LONG"
    )

    assert violation.field == "output"

    assert violation.safe_metadata == {
        "channel": "EMAIL",
        "actual_length": 79,
        "maximum_length": 78,
    }


# ==========================================================
# Push Tests
# ==========================================================


def test_push_title_at_limit_passes() -> None:
    """
    A push title containing exactly 50 characters is valid.
    """

    context = build_context(
        "PUSH"
    )

    decision = CommunicationDecision(
        channel="PUSH",
        title="A" * 50,
        subject=None,
        message="Your service request has been updated.",
        tone="PROFESSIONAL",
        confidence=0.95,
    )

    result = LengthValidator().check(
        context=context,
        decision=decision,
    )

    assert result.passed is True
    assert result.violations == ()


def test_push_title_at_transport_limit_passes() -> None:
    context = build_context("PUSH")

    decision = CommunicationDecision(
        channel="PUSH",
        title="A" * 51,
        subject=None,
        message="Your service request has been updated.",
        tone="PROFESSIONAL",
        confidence=0.95,
    )

    assert decision.title is not None
    assert len(decision.title) == 50

    result = LengthValidator().check(
        context=context,
        decision=decision,
    )

    assert result.passed is True


# ==========================================================
# In-App Tests
# ==========================================================


def test_in_app_has_no_configured_length_violation() -> None:
    """
    Story 0.4 does not currently define an in-app limit.
    """

    context = build_context(
        "IN_APP"
    )

    decision = CommunicationDecision(
        channel="IN_APP",
        title="Job Update",
        subject=None,
        message="A" * 500,
        tone="PROFESSIONAL",
        confidence=0.95,
    )

    result = LengthValidator().check(
        context=context,
        decision=decision,
    )

    assert result.passed is True
    assert result.violations == ()


# ==========================================================
# Privacy and Timing Tests
# ==========================================================


def test_length_violation_does_not_store_raw_content() -> None:
    """
    Violation records must not contain generated content.
    """

    private_message = (
        "Private customer content "
        * 20
    )

    context = build_context(
        "SMS"
    )

    decision = CommunicationDecision(
        channel="SMS",
        title=None,
        subject=None,
        message=private_message,
        tone="PROFESSIONAL",
        confidence=0.95,
    )

    result = LengthValidator().check(
        context=context,
        decision=decision,
    )

    serialized_result = (
        result.model_dump_json()
    )

    assert (
        private_message
        not in serialized_result
    )


def test_length_validator_records_non_negative_latency() -> None:
    """
    Every checker records local execution latency.
    """

    context = build_context(
        "SMS"
    )

    decision = CommunicationDecision(
        channel="SMS",
        title=None,
        subject=None,
        message="Valid SMS message.",
        tone="PROFESSIONAL",
        confidence=0.95,
    )

    result = LengthValidator().check(
        context=context,
        decision=decision,
    )

    assert result.latency_ms >= 0.0

def test_sms_150_chars_passes() -> None:
    context = build_context("SMS")

    decision = CommunicationDecision(
        channel="SMS",
        title=None,
        subject=None,
        message="A" * 150,
        tone="PROFESSIONAL",
        confidence=0.95,
    )

    result = LengthValidator().check(
        context=context,
        decision=decision,
    )

    assert result.passed is True





def test_unicode_sms_70_chars_passes() -> None:
    context = build_context("SMS")

    decision = CommunicationDecision(
        channel="SMS",
        title=None,
        subject=None,
        message="😀" * 70,
        tone="PROFESSIONAL",
        confidence=0.95,
    )

    result = LengthValidator().check(
        context=context,
        decision=decision,
    )

    assert result.passed is True


def test_unicode_sms_80_chars_fails() -> None:
    context = build_context("SMS")

    decision = CommunicationDecision(
        channel="SMS",
        title=None,
        subject=None,
        message="😀" * 80,
        tone="PROFESSIONAL",
        confidence=0.95,
    )

    result = LengthValidator().check(
        context=context,
        decision=decision,
    )

    assert result.passed is False
    assert result.violations[0].code == "SMS_MESSAGE_TOO_LONG"


def test_push_body_200_chars_passes() -> None:
    context = build_context("PUSH")

    decision = CommunicationDecision(
        channel="PUSH",
        title="Job Update",
        subject=None,
        message="A" * 200,
        tone="PROFESSIONAL",
        confidence=0.95,
    )

    result = LengthValidator().check(
        context=context,
        decision=decision,
    )

    assert result.passed is True


def test_push_body_at_transport_limit_passes() -> None:
    context = build_context("PUSH")

    decision = CommunicationDecision(
        channel="PUSH",
        title="Job Update",
        subject=None,
        message="A" * 201,
        tone="PROFESSIONAL",
        confidence=0.95,
    )

    assert len(decision.message) == 200

    result = LengthValidator().check(
        context=context,
        decision=decision,
    )

    assert result.passed is True


def test_sms_truncate_adds_ellipsis() -> None:
    message = "A" * 170

    result = LengthValidator.truncate_sms(message)

    assert len(result) == 160
    assert result.endswith("...")
    assert LengthValidator.sms_length(result) <= 160


def test_sms_truncate_with_full_message_link() -> None:
    message = "A" * 170
    link = "https://example.com/message/123"

    result = LengthValidator.truncate_sms(
        message,
        full_message_link=link,
    )

    assert result.endswith(
        f"... {link}"
    )

    assert LengthValidator.sms_length(result) <= 160


def test_gsm7_detection() -> None:
    assert LengthValidator.is_gsm7(
        "Hello, your job is confirmed."
    ) is True


def test_unicode_detection() -> None:
    assert LengthValidator.is_gsm7(
        "Hello 😀"
    ) is False
