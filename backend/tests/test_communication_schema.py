"""
test_communication_schema.py

Tests for the FieldOps Communication Agent input and output
contracts.

These tests verify:

- Communication context validation
- Strict extra-field rejection
- Channel-specific output rules
- Push title support
- Email subject requirements
- SMS message-only behavior
- Rejection of the old incorrect Body field
"""

from __future__ import annotations

import pytest

from pydantic import ValidationError

from app.services.ai.FieldOpsAI.schemas.communication import (
    CommunicationContext,
    CommunicationDecision,
)


# ==========================================================
# Context Tests
# ==========================================================


def test_communication_context_accepts_valid_input() -> None:
    """
    The context contains the metadata required for generation,
    guardrail logging, and template fallback.
    """

    context = CommunicationContext(
        job_id="JOB-1001",
        correlation_id="corr-1001",
        notification_type="technician_en_route",
        recipient_type="CUSTOMER",
        channel="SMS",
        locale="en",
        customer_name="Ruby Devi",
        technician_name="Kumar Raj",
        job_status="EN_ROUTE",
        job_title="Electrical Repair",
        eta="20 minutes",
        sentiment="NEUTRAL",
    )

    assert context.job_id == "JOB-1001"
    assert context.correlation_id == "corr-1001"

    assert (
        context.notification_type
        == "technician_en_route"
    )

    assert context.recipient_type == "CUSTOMER"
    assert context.channel == "SMS"
    assert context.job_status == "EN_ROUTE"


def test_communication_context_strips_whitespace() -> None:
    """
    Normal string fields are stripped automatically.
    """

    context = CommunicationContext(
        job_id="  JOB-1001  ",
        notification_type="job_assigned",
        recipient_type="CUSTOMER",
        channel="SMS",
        customer_name="  Ruby Devi  ",
        job_status="ASSIGNED",
    )

    assert context.job_id == "JOB-1001"
    assert context.customer_name == "Ruby Devi"


def test_communication_context_rejects_extra_fields() -> None:
    """
    Unknown fields must not silently enter the AI prompt.
    """

    with pytest.raises(
        ValidationError
    ):
        CommunicationContext(
            job_id="JOB-1001",
            notification_type="job_assigned",
            recipient_type="CUSTOMER",
            channel="SMS",
            job_status="ASSIGNED",
            unsupported_private_data="secret",
        )


def test_notification_type_requires_safe_identifier_format() -> None:
    """
    Template/event names must use lowercase snake_case.
    """

    with pytest.raises(
        ValidationError
    ):
        CommunicationContext(
            job_id="JOB-1001",
            notification_type="Job Assigned!",
            recipient_type="CUSTOMER",
            channel="SMS",
            job_status="ASSIGNED",
        )


# ==========================================================
# Email Tests
# ==========================================================


def test_email_decision_requires_subject() -> None:
    """
    Valid email output requires subject and message.
    """

    decision = CommunicationDecision(
        channel="EMAIL",
        title=None,
        subject="Your service update",
        message="Your technician has been assigned.",
        tone="PROFESSIONAL",
        confidence=0.95,
    )

    assert (
        decision.subject
        == "Your service update"
    )

    assert decision.title is None


def test_email_decision_rejects_missing_subject() -> None:
    """
    Email cannot be generated without a subject.
    """

    with pytest.raises(
        ValidationError,
        match="EMAIL communication requires subject",
    ):
        CommunicationDecision(
            channel="EMAIL",
            title=None,
            subject=None,
            message="Your technician has been assigned.",
            tone="PROFESSIONAL",
            confidence=0.95,
        )


def test_email_decision_rejects_push_title() -> None:
    """
    Email must not contain the push/in-app title field.
    """

    with pytest.raises(
        ValidationError,
        match="EMAIL communication must not include title",
    ):
        CommunicationDecision(
            channel="EMAIL",
            title="Job Update",
            subject="Your service update",
            message="Your technician has been assigned.",
            tone="PROFESSIONAL",
            confidence=0.95,
        )


# ==========================================================
# SMS Tests
# ==========================================================


def test_sms_decision_accepts_message_only() -> None:
    """
    SMS uses only the message content field.
    """

    decision = CommunicationDecision(
        channel="SMS",
        title=None,
        subject=None,
        message="Your technician is on the way.",
        tone="PROFESSIONAL",
        confidence=0.95,
    )

    assert decision.channel == "SMS"
    assert decision.title is None
    assert decision.subject is None


@pytest.mark.parametrize(
    (
        "title",
        "subject",
        "error_message",
    ),
    [
        (
            "Job Update",
            None,
            "SMS communication must not include title",
        ),
        (
            None,
            "Job Update",
            "SMS communication must not include subject",
        ),
    ],
)
def test_sms_decision_rejects_title_and_subject(
    title: str | None,
    subject: str | None,
    error_message: str,
) -> None:
    """
    SMS must not contain email or push-specific fields.
    """

    with pytest.raises(
        ValidationError,
        match=error_message,
    ):
        CommunicationDecision(
            channel="SMS",
            title=title,
            subject=subject,
            message="Your technician is on the way.",
            tone="PROFESSIONAL",
            confidence=0.95,
        )


# ==========================================================
# Push Tests
# ==========================================================


def test_push_decision_requires_title() -> None:
    """
    Push notification uses title and message.
    """

    decision = CommunicationDecision(
        channel="PUSH",
        title="Technician En Route",
        subject=None,
        message="Your technician is on the way.",
        tone="PROFESSIONAL",
        confidence=0.95,
    )

    assert decision.title == "Technician En Route"
    assert decision.subject is None


def test_push_decision_rejects_missing_title() -> None:
    """
    Push notification cannot be generated without a title.
    """

    with pytest.raises(
        ValidationError,
        match="PUSH communication requires title",
    ):
        CommunicationDecision(
            channel="PUSH",
            title=None,
            subject=None,
            message="Your technician is on the way.",
            tone="PROFESSIONAL",
            confidence=0.95,
        )


def test_push_decision_rejects_email_subject() -> None:
    """
    Push output must not include an email subject.
    """

    with pytest.raises(
        ValidationError,
        match="PUSH communication must not include subject",
    ):
        CommunicationDecision(
            channel="PUSH",
            title="Technician En Route",
            subject="Your technician is on the way",
            message="Your technician is on the way.",
            tone="PROFESSIONAL",
            confidence=0.95,
        )


# ==========================================================
# In-App Tests
# ==========================================================


def test_in_app_decision_allows_optional_title() -> None:
    """
    In-app content may include a title.
    """

    decision = CommunicationDecision(
        channel="IN_APP",
        title="Job Update",
        subject=None,
        message="Your technician has been assigned.",
        tone="FRIENDLY",
        confidence=0.90,
    )

    assert decision.title == "Job Update"


def test_in_app_decision_rejects_email_subject() -> None:
    """
    In-app communication must not contain an email subject.
    """

    with pytest.raises(
        ValidationError,
        match="IN_APP communication must not include subject",
    ):
        CommunicationDecision(
            channel="IN_APP",
            title="Job Update",
            subject="Your service update",
            message="Your technician has been assigned.",
            tone="FRIENDLY",
            confidence=0.90,
        )


# ==========================================================
# Strict Output Tests
# ==========================================================


def test_decision_rejects_old_body_field() -> None:
    """
    The old prompt used Body instead of message.

    This test prevents that schema mismatch from returning.
    """

    with pytest.raises(
        ValidationError
    ):
        CommunicationDecision.model_validate(
            {
                "channel": "EMAIL",
                "title": None,
                "subject": "Your service update",
                "Body": (
                    "Your technician has been assigned."
                ),
                "tone": "PROFESSIONAL",
                "confidence": 0.95,
            }
        )


def test_decision_rejects_unknown_ai_fields() -> None:
    """
    Internal reasoning and other unexpected AI output fields
    must be rejected.
    """

    with pytest.raises(
        ValidationError
    ):
        CommunicationDecision(
            channel="SMS",
            title=None,
            subject=None,
            message="Your technician is on the way.",
            tone="PROFESSIONAL",
            confidence=0.95,
            internal_reasoning="Hidden reasoning",
        )