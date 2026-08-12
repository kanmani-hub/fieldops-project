"""
test_channel_validator.py

Tests for communication-channel consistency validation.
"""

from __future__ import annotations

import pytest

from app.services.ai.FieldOpsAI.schemas.communication import (
    CommunicationChannel,
    CommunicationContext,
    CommunicationDecision,
)
from app.services.ai.guardrails.base import (
    GuardrailChecker,
)
from app.services.ai.guardrails.channel_validator import (
    ChannelValidator,
)
from app.services.ai.guardrails.contracts import (
    GuardrailCategory,
    GuardrailSeverity,
)


# ==========================================================
# Helpers
# ==========================================================


def build_context(
    channel: CommunicationChannel,
) -> CommunicationContext:
    """
    Build a valid communication context.
    """

    return CommunicationContext(
        job_id="JOB-1001",
        notification_type="job_assigned",
        recipient_type="CUSTOMER",
        channel=channel,
        job_status="ASSIGNED",
    )


def build_decision(
    channel: CommunicationChannel,
) -> CommunicationDecision:
    """
    Build a valid decision for the selected channel.
    """

    if channel == "EMAIL":
        return CommunicationDecision(
            channel="EMAIL",
            title=None,
            subject="Your service update",
            message=(
                "Your service request has been updated."
            ),
            tone="PROFESSIONAL",
            confidence=0.95,
        )

    if channel == "SMS":
        return CommunicationDecision(
            channel="SMS",
            title=None,
            subject=None,
            message=(
                "Your service request has been updated."
            ),
            tone="PROFESSIONAL",
            confidence=0.95,
        )

    if channel == "PUSH":
        return CommunicationDecision(
            channel="PUSH",
            title="Service Update",
            subject=None,
            message=(
                "Your service request has been updated."
            ),
            tone="PROFESSIONAL",
            confidence=0.95,
        )

    return CommunicationDecision(
        channel="IN_APP",
        title="Service Update",
        subject=None,
        message=(
            "Your service request has been updated."
        ),
        tone="PROFESSIONAL",
        confidence=0.95,
    )


# ==========================================================
# Interface Test
# ==========================================================


def test_channel_validator_implements_guardrail_interface() -> None:
    """
    ChannelValidator follows the common checker contract.
    """

    validator = ChannelValidator()

    assert isinstance(
        validator,
        GuardrailChecker,
    )


# ==========================================================
# Matching Channel Tests
# ==========================================================


@pytest.mark.parametrize(
    "channel",
    [
        "EMAIL",
        "SMS",
        "PUSH",
        "IN_APP",
    ],
)
def test_matching_channels_pass(
    channel: CommunicationChannel,
) -> None:
    """
    Every supported channel passes when context and decision
    use the same value.
    """

    context = build_context(
        channel
    )

    decision = build_decision(
        channel
    )

    result = ChannelValidator().check(
        context=context,
        decision=decision,
    )

    assert result.passed is True
    assert result.violations == ()


# ==========================================================
# Mismatch Test
# ==========================================================


def test_channel_mismatch_fails() -> None:
    """
    An email result must not be used for an SMS request.
    """

    context = build_context(
        "SMS"
    )

    decision = build_decision(
        "EMAIL"
    )

    result = ChannelValidator().check(
        context=context,
        decision=decision,
    )

    assert result.passed is False
    assert len(result.violations) == 1

    violation = result.violations[0]

    assert (
        violation.code
        == "COMMUNICATION_CHANNEL_MISMATCH"
    )

    assert (
        violation.category
        == GuardrailCategory.CHANNEL_MISMATCH
    )

    assert (
        violation.severity
        == GuardrailSeverity.ERROR
    )

    assert violation.field == "channel"

    assert violation.safe_metadata == {
        "requested_channel": "SMS",
        "generated_channel": "EMAIL",
    }


# ==========================================================
# Privacy Test
# ==========================================================


def test_channel_violation_does_not_store_message_content() -> None:
    """
    The violation result contains channel metadata only.
    """

    private_content = (
        "Private recipient-facing communication."
    )

    context = build_context(
        "SMS"
    )

    decision = CommunicationDecision(
        channel="EMAIL",
        title=None,
        subject="Private subject",
        message=private_content,
        tone="PROFESSIONAL",
        confidence=0.95,
    )

    result = ChannelValidator().check(
        context=context,
        decision=decision,
    )

    serialized_result = (
        result.model_dump_json()
    )

    assert (
        private_content
        not in serialized_result
    )

    assert (
        "Private subject"
        not in serialized_result
    )


# ==========================================================
# Immutability Test
# ==========================================================


def test_channel_validator_does_not_modify_inputs() -> None:
    """
    Guardrail checkers must inspect data without changing it.
    """

    context = build_context(
        "SMS"
    )

    decision = build_decision(
        "EMAIL"
    )

    original_context = (
        context.model_dump()
    )

    original_decision = (
        decision.model_dump()
    )

    ChannelValidator().check(
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


# ==========================================================
# Timing Test
# ==========================================================


def test_channel_validator_records_non_negative_latency() -> None:
    """
    Local checker execution time must be recorded.
    """

    context = build_context(
        "SMS"
    )

    decision = build_decision(
        "SMS"
    )

    result = ChannelValidator().check(
        context=context,
        decision=decision,
    )

    assert result.latency_ms >= 0.0