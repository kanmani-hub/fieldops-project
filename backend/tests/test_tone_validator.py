"""
test_tone_validator.py

Tests for FieldOps communication tone validation.
"""

from __future__ import annotations

import pytest

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
from app.services.ai.guardrails.tone_validator import (
    ToneReviewProvider,
    ToneReviewProviderError,
    ToneReviewResult,
    ToneReviewVerdict,
    ToneValidator,
)


# ==========================================================
# Fake External Provider
# ==========================================================


class FakeToneReviewProvider:
    """
    Controllable tone-review provider used by unit tests.
    """

    provider_name = "fake_tone_review"

    def __init__(
        self,
        *,
        result: ToneReviewResult | None = None,
        should_fail: bool = False,
    ) -> None:
        self.result = result or ToneReviewResult(
            verdict=ToneReviewVerdict.SAFE,
            confidence=0.95,
            latency_ms=10.0,
        )

        self.should_fail = should_fail
        self.call_count = 0
        self.last_text: str | None = None

    def review(
        self,
        *,
        text: str,
    ) -> ToneReviewResult:
        """
        Return the configured review result.
        """

        self.call_count += 1
        self.last_text = text

        if self.should_fail:
            raise ToneReviewProviderError(
                "Fake review failure."
            )

        return self.result


# ==========================================================
# Helpers
# ==========================================================


def build_context(
    *,
    sentiment: str = "NEUTRAL",
    channel: str = "SMS",
) -> CommunicationContext:
    """
    Build a valid communication context.
    """

    return CommunicationContext(
        job_id="JOB-1001",
        notification_type="job_assigned",
        recipient_type="CUSTOMER",
        channel=channel,
        customer_name="{{CUSTOMER_NAME_1}}",
        technician_name="{{TECHNICIAN_NAME_1}}",
        job_status="ASSIGNED",
        sentiment=sentiment,
    )


def build_sms_decision(
    message: str,
    *,
    tone: str = "PROFESSIONAL",
) -> CommunicationDecision:
    """
    Build a valid SMS communication decision.
    """

    return CommunicationDecision(
        channel="SMS",
        title=None,
        subject=None,
        message=message,
        tone=tone,
        confidence=0.95,
    )


# ==========================================================
# Interface Tests
# ==========================================================


def test_validator_implements_guardrail_interface() -> None:
    """
    ToneValidator follows the common guardrail contract.
    """

    assert isinstance(
        ToneValidator(),
        GuardrailChecker,
    )


def test_fake_provider_implements_review_interface() -> None:
    """
    The fake provider follows ToneReviewProvider.
    """

    assert isinstance(
        FakeToneReviewProvider(),
        ToneReviewProvider,
    )


def test_external_review_requires_provider() -> None:
    """
    External review cannot be enabled without a provider.
    """

    with pytest.raises(
        ValueError,
        match=(
            "A tone review provider is required"
        ),
    ):
        ToneValidator(
            external_review_enabled=True
        )


# ==========================================================
# Local Passing Test
# ==========================================================


def test_professional_message_passes() -> None:
    """
    Normal professional communication must pass.
    """

    result = ToneValidator().check(
        context=build_context(),
        decision=build_sms_decision(
            "Your technician is on the way."
        ),
    )

    assert result.passed is True
    assert result.violations == ()


# ==========================================================
# Local Tone Detection Tests
# ==========================================================


def test_aggressive_language_fails() -> None:
    """
    Aggressive language must be rejected locally.
    """

    result = ToneValidator().check(
        context=build_context(),
        decision=build_sms_decision(
            "Stop complaining and deal with it."
        ),
    )

    assert result.passed is False

    codes = {
        violation.code
        for violation in result.violations
    }

    assert "AGGRESSIVE_TONE_DETECTED" in codes


def test_sarcastic_language_fails() -> None:
    """
    Sarcastic language must be rejected locally.
    """

    result = ToneValidator().check(
        context=build_context(),
        decision=build_sms_decision(
            "Yeah right, your technician is on the way."
        ),
    )

    assert result.passed is False

    assert (
        result.violations[0].code
        == "SARCASTIC_TONE_DETECTED"
    )


def test_unprofessional_language_fails() -> None:
    """
    Clearly unprofessional wording must be rejected.
    """

    result = ToneValidator().check(
        context=build_context(),
        decision=build_sms_decision(
            "This is not our problem."
        ),
    )

    assert result.passed is False

    assert (
        result.violations[0].code
        == "UNPROFESSIONAL_TONE_DETECTED"
    )


# ==========================================================
# Sentiment and Declared Tone Tests
# ==========================================================


def test_negative_sentiment_with_friendly_tone_fails() -> None:
    """
    FRIENDLY is a clear mismatch for negative sentiment.
    """

    result = ToneValidator().check(
        context=build_context(
            sentiment="NEGATIVE"
        ),
        decision=build_sms_decision(
            "We understand the inconvenience.",
            tone="FRIENDLY",
        ),
    )

    assert result.passed is False

    violation = result.violations[0]

    assert (
        violation.code
        == "SENTIMENT_TONE_MISMATCH"
    )

    assert (
        violation.category
        == GuardrailCategory.TONE
    )

    assert (
        violation.severity
        == GuardrailSeverity.ERROR
    )

    assert violation.field == "tone"


def test_positive_sentiment_with_empathetic_tone_fails() -> None:
    """
    EMPATHETIC is a clear mismatch for positive sentiment.
    """

    result = ToneValidator().check(
        context=build_context(
            sentiment="POSITIVE"
        ),
        decision=build_sms_decision(
            "Thank you for your feedback.",
            tone="EMPATHETIC",
        ),
    )

    assert result.passed is False

    assert (
        result.violations[0].code
        == "SENTIMENT_TONE_MISMATCH"
    )


def test_negative_sentiment_with_professional_tone_passes() -> None:
    """
    PROFESSIONAL remains valid for negative sentiment.
    """

    result = ToneValidator().check(
        context=build_context(
            sentiment="NEGATIVE"
        ),
        decision=build_sms_decision(
            "We are reviewing the delay.",
            tone="PROFESSIONAL",
        ),
    )

    assert result.passed is True
    assert result.violations == ()


# ==========================================================
# Placeholder Test
# ==========================================================


def test_placeholders_are_removed_before_tone_scanning() -> None:
    """
    Placeholder names must not influence tone analysis.
    """

    result = ToneValidator().check(
        context=build_context(),
        decision=build_sms_decision(
            "Hello {{YOUR_FAULT_1}}, "
            "your service request was updated."
        ),
    )

    assert result.passed is True
    assert result.violations == ()


# ==========================================================
# Field Test
# ==========================================================


def test_validator_scans_email_subject() -> None:
    """
    Subject and title must also be checked.
    """

    context = CommunicationContext(
        job_id="JOB-1001",
        notification_type="job_assigned",
        recipient_type="CUSTOMER",
        channel="EMAIL",
        job_status="ASSIGNED",
        sentiment="NEUTRAL",
    )

    decision = CommunicationDecision(
        channel="EMAIL",
        title=None,
        subject="Yeah right, service update",
        message=(
            "Your service request has been updated."
        ),
        tone="PROFESSIONAL",
        confidence=0.95,
    )

    result = ToneValidator().check(
        context=context,
        decision=decision,
    )

    assert result.passed is False
    assert result.violations[0].field == "output"
    assert (
        result.violations[0].code
        == "SARCASTIC_TONE_DETECTED"
    )


# ==========================================================
# External Review Tests
# ==========================================================


def test_external_review_is_not_called_for_clear_safe_text() -> None:
    """
    Safe unambiguous content remains on the fast local path.
    """

    provider = FakeToneReviewProvider()

    validator = ToneValidator(
        review_provider=provider,
        external_review_enabled=True,
    )

    result = validator.check(
        context=build_context(),
        decision=build_sms_decision(
            "Your technician is on the way."
        ),
    )

    assert result.passed is True
    assert provider.call_count == 0


def test_ambiguous_text_can_pass_external_review() -> None:
    """
    Ambiguous text passes when the reviewer classifies it safe.
    """

    provider = FakeToneReviewProvider(
        result=ToneReviewResult(
            verdict=ToneReviewVerdict.SAFE,
            confidence=0.93,
            latency_ms=12.0,
        )
    )

    validator = ToneValidator(
        review_provider=provider,
        external_review_enabled=True,
    )

    result = validator.check(
        context=build_context(),
        decision=build_sms_decision(
            "Sure, your technician is on the way."
        ),
    )

    assert result.passed is True
    assert provider.call_count == 1
    assert provider.last_text is not None


def test_external_sarcastic_verdict_fails() -> None:
    """
    External review may reject ambiguous sarcastic content.
    """

    provider = FakeToneReviewProvider(
        result=ToneReviewResult(
            verdict=ToneReviewVerdict.SARCASTIC,
            confidence=0.96,
            latency_ms=15.0,
        )
    )

    validator = ToneValidator(
        review_provider=provider,
        external_review_enabled=True,
    )

    result = validator.check(
        context=build_context(),
        decision=build_sms_decision(
            "Sure, that is a great update."
        ),
    )

    assert result.passed is False

    violation = result.violations[0]

    assert (
        violation.code
        == "EXTERNAL_SARCASTIC_TONE_DETECTED"
    )

    assert violation.field == "response"

    assert violation.safe_metadata == {
        "review_provider": "fake_tone_review",
        "review_verdict": "SARCASTIC",
        "review_confidence": 0.96,
        "review_latency_ms": 15.0,
        "detection_source": "EXTERNAL",
    }


def test_external_review_failure_creates_safe_violation() -> None:
    """
    Enabled external review failure must trigger fallback later.
    """

    provider = FakeToneReviewProvider(
        should_fail=True
    )

    validator = ToneValidator(
        review_provider=provider,
        external_review_enabled=True,
    )

    result = validator.check(
        context=build_context(),
        decision=build_sms_decision(
            "Sure, your request was updated."
        ),
    )

    assert result.passed is False

    assert (
        result.violations[0].code
        == "TONE_REVIEW_UNAVAILABLE"
    )


def test_external_review_receives_no_placeholders() -> None:
    """
    Placeholder labels must not be sent to the tone reviewer.
    """

    provider = FakeToneReviewProvider()

    validator = ToneValidator(
        review_provider=provider,
        external_review_enabled=True,
    )

    validator.check(
        context=build_context(),
        decision=build_sms_decision(
            "Sure, {{CUSTOMER_NAME_1}} received the update."
        ),
    )

    assert provider.call_count == 1
    assert provider.last_text is not None
    assert (
        "{{CUSTOMER_NAME_1}}"
        not in provider.last_text
    )


# ==========================================================
# Privacy, Immutability, and Timing
# ==========================================================


def test_violation_does_not_store_generated_content() -> None:
    """
    The violation must not store raw communication.
    """

    generated_content = (
        "Private communication: deal with it."
    )

    result = ToneValidator().check(
        context=build_context(),
        decision=build_sms_decision(
            generated_content
        ),
    )

    serialized_result = (
        result.model_dump_json()
    )

    assert (
        generated_content
        not in serialized_result
    )


def test_validator_does_not_modify_inputs() -> None:
    """
    Tone validation only inspects its inputs.
    """

    context = build_context()

    decision = build_sms_decision(
        "Your technician is on the way."
    )

    original_context = (
        context.model_dump()
    )

    original_decision = (
        decision.model_dump()
    )

    ToneValidator().check(
        context=context,
        decision=decision,
    )

    assert (
        context.model_dump()
        == original_context
    )

    assert (
        decision.model_dump()
        == original_decision
    )


def test_validator_records_non_negative_latency() -> None:
    """
    Local execution latency must be recorded.
    """

    result = ToneValidator().check(
        context=build_context(),
        decision=build_sms_decision(
            "Your technician is on the way."
        ),
    )

    assert result.latency_ms >= 0.0