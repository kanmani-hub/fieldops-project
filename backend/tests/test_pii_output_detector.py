"""
test_pii_output_detector.py

Tests for PII detection in AI-generated communication.
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
from app.services.ai.guardrails.pii_output_detector import (
    PIIOutputDetector,
)


# ==========================================================
# Helpers
# ==========================================================


def build_context() -> CommunicationContext:
    """
    Build a sanitized communication context.
    """

    return CommunicationContext(
        job_id="JOB-1001",
        notification_type="job_assigned",
        recipient_type="CUSTOMER",
        channel="SMS",
        customer_name="{{CUSTOMER_NAME_1}}",
        technician_name="{{TECHNICIAN_NAME_1}}",
        job_status="ASSIGNED",
    )


def build_sms_decision(
    message: str,
) -> CommunicationDecision:
    """
    Build a valid SMS decision.
    """

    return CommunicationDecision(
        channel="SMS",
        title=None,
        subject=None,
        message=message,
        tone="PROFESSIONAL",
        confidence=0.95,
    )


# ==========================================================
# Interface and Passing Tests
# ==========================================================


def test_detector_implements_guardrail_interface() -> None:
    """
    The detector follows the shared checker interface.
    """

    assert isinstance(
        PIIOutputDetector(),
        GuardrailChecker,
    )


def test_clean_message_passes() -> None:
    """
    Normal operational communication contains no PII.
    """

    context = build_context()

    decision = build_sms_decision(
        "Your technician is on the way. ETA: 20 minutes."
    )

    result = PIIOutputDetector().check(
        context=context,
        decision=decision,
    )

    assert result.passed is True
    assert result.violations == ()


def test_placeholders_are_not_detected_as_pii() -> None:
    """
    Sanitizer placeholders are safe provider-facing values.
    """

    context = build_context()

    decision = build_sms_decision(
        "Hello {{CUSTOMER_NAME_1}}, contact details remain "
        "{{CUSTOMER_EMAIL_1}} and {{CUSTOMER_PHONE_1}}."
    )

    result = PIIOutputDetector().check(
        context=context,
        decision=decision,
    )

    assert result.passed is True
    assert result.violations == ()


# ==========================================================
# Email Test
# ==========================================================


def test_generated_email_fails() -> None:
    """
    A newly generated email address is prohibited.
    """

    context = build_context()

    decision = build_sms_decision(
        "Contact support at alex@example.com."
    )

    result = PIIOutputDetector().check(
        context=context,
        decision=decision,
    )

    assert result.passed is False
    assert len(result.violations) == 1

    violation = result.violations[0]

    assert violation.code == "PII_EMAIL_DETECTED"
    assert violation.category == GuardrailCategory.PII
    assert violation.severity == GuardrailSeverity.CRITICAL
    assert violation.field == "output"
    assert violation.safe_metadata == {
        "pii_type": "EMAIL",
        "match_count": 1,
    }


# ==========================================================
# Phone Tests
# ==========================================================


@pytest.mark.parametrize(
    "phone_number",
    [
        "+1 (415) 555-0123",
        "(415) 555-0123",
        "9876543210",
        "+91 98765 43210",
    ],
)
def test_generated_phone_number_fails(
    phone_number: str,
) -> None:
    """
    Common phone-number formats must be detected.
    """

    context = build_context()

    decision = build_sms_decision(
        f"Call the technician at {phone_number}."
    )

    result = PIIOutputDetector().check(
        context=context,
        decision=decision,
    )

    assert result.passed is False
    assert len(result.violations) == 1

    violation = result.violations[0]

    assert violation.code == "PII_PHONE_DETECTED"
    assert violation.field == "output"
    assert violation.safe_metadata == {
        "pii_type": "PHONE",
        "match_count": 1,
    }


# ==========================================================
# SSN Test
# ==========================================================


def test_generated_ssn_fails() -> None:
    """
    A Social Security number is a critical violation.
    """

    context = build_context()

    decision = build_sms_decision(
        "The identifier is 123-45-6789."
    )

    result = PIIOutputDetector().check(
        context=context,
        decision=decision,
    )

    assert result.passed is False

    violation = result.violations[0]

    assert violation.code == "PII_SSN_DETECTED"
    assert violation.safe_metadata == {
        "pii_type": "SSN",
        "match_count": 1,
    }


# ==========================================================
# Address Test
# ==========================================================


def test_generated_street_address_fails() -> None:
    """
    A numbered street address is prohibited output.
    """

    context = build_context()

    decision = build_sms_decision(
        "The service location is 221B Baker Street."
    )

    result = PIIOutputDetector().check(
        context=context,
        decision=decision,
    )

    assert result.passed is False

    violation = result.violations[0]

    assert violation.code == "PII_ADDRESS_DETECTED"
    assert violation.safe_metadata == {
        "pii_type": "ADDRESS",
        "match_count": 1,
    }


# ==========================================================
# GPS Test
# ==========================================================


def test_generated_gps_coordinates_fail() -> None:
    """
    Decimal latitude and longitude values are prohibited.
    """

    context = build_context()

    decision = build_sms_decision(
        "Technician location: 37.7749, -122.4194."
    )

    result = PIIOutputDetector().check(
        context=context,
        decision=decision,
    )

    assert result.passed is False

    violation = result.violations[0]

    assert violation.code == "PII_GPS_DETECTED"
    assert violation.safe_metadata == {
        "pii_type": "GPS",
        "match_count": 1,
    }

@pytest.mark.parametrize(
    "message",
    [
        "Location: 37.7749, -122.4194",
        "Location: 37.7749, -122.4194.",
        "Location: 37.7749, -122.4194, please review.",
        "Location: 37.7749; -122.4194",
    ],
)
def test_gps_coordinates_with_common_punctuation_fail(
    message: str,
) -> None:
    """
    GPS coordinates must be detected when followed by normal
    sentence punctuation.
    """

    context = build_context()

    decision = build_sms_decision(
        message
    )

    result = PIIOutputDetector().check(
        context=context,
        decision=decision,
    )

    assert result.passed is False

    assert any(
        violation.code
        == "PII_GPS_DETECTED"
        for violation in result.violations
    )
@pytest.mark.parametrize(
    "message",
    [
        "Invalid coordinates: 91.0000, 20.0000.",
        "Invalid coordinates: 37.7749, -181.0000.",
        "Malformed value: 37.7749, -122.4194.5",
    ],
)
def test_invalid_coordinate_values_are_not_detected(
    message: str,
) -> None:
    """
    Invalid or malformed coordinates must not be classified as
    valid GPS pairs.
    """

    context = build_context()

    decision = build_sms_decision(
        message
    )

    result = PIIOutputDetector().check(
        context=context,
        decision=decision,
    )

    assert all(
        violation.code
        != "PII_GPS_DETECTED"
        for violation in result.violations
    )
# ==========================================================
# Multiple PII Types
# ==========================================================


def test_multiple_pii_types_create_multiple_violations() -> None:
    """
    Each detected PII category receives its own violation.
    """

    context = build_context()

    decision = build_sms_decision(
        "Email alex@example.com or call +1 415-555-0123."
    )

    result = PIIOutputDetector().check(
        context=context,
        decision=decision,
    )

    assert result.passed is False
    assert len(result.violations) == 2

    codes = {
        violation.code
        for violation in result.violations
    }

    assert codes == {
        "PII_EMAIL_DETECTED",
        "PII_PHONE_DETECTED",
    }


# ==========================================================
# Field Test
# ==========================================================


def test_detector_scans_email_subject() -> None:
    """
    Title and subject must be scanned in addition to message.
    """

    context = CommunicationContext(
        job_id="JOB-1001",
        notification_type="job_assigned",
        recipient_type="CUSTOMER",
        channel="EMAIL",
        job_status="ASSIGNED",
    )

    decision = CommunicationDecision(
        channel="EMAIL",
        title=None,
        subject="Contact alex@example.com",
        message="Your service request has been updated.",
        tone="PROFESSIONAL",
        confidence=0.95,
    )

    result = PIIOutputDetector().check(
        context=context,
        decision=decision,
    )

    assert result.passed is False
    assert result.violations[0].field == "output"
    assert result.violations[0].code == "PII_EMAIL_DETECTED"


# ==========================================================
# False-Positive Test
# ==========================================================


def test_operational_numbers_are_not_phone_pii() -> None:
    """
    Job IDs, years, and durations must not be treated as phone
    numbers.
    """

    context = build_context()

    decision = build_sms_decision(
        "Job JOB-1001 is scheduled for 10 July 2026. "
        "ETA: 20 minutes."
    )

    result = PIIOutputDetector().check(
        context=context,
        decision=decision,
    )

    assert result.passed is True
    assert result.violations == ()


# ==========================================================
# Privacy Test
# ==========================================================


def test_violation_does_not_store_detected_pii() -> None:
    """
    The violation must not contain the generated private value.
    """

    private_email = "private.customer@example.com"

    context = build_context()

    decision = build_sms_decision(
        f"Contact {private_email}."
    )

    result = PIIOutputDetector().check(
        context=context,
        decision=decision,
    )

    serialized_result = result.model_dump_json()

    assert private_email not in serialized_result


# ==========================================================
# Immutability Test
# ==========================================================


def test_detector_does_not_modify_inputs() -> None:
    """
    The detector only inspects context and decision.
    """

    context = build_context()

    decision = build_sms_decision(
        "Contact alex@example.com."
    )

    original_context = context.model_dump()
    original_decision = decision.model_dump()

    PIIOutputDetector().check(
        context=context,
        decision=decision,
    )

    assert context.model_dump() == original_context
    assert decision.model_dump() == original_decision


# ==========================================================
# Timing Test
# ==========================================================


def test_detector_records_non_negative_latency() -> None:
    """
    Local checker execution latency must be recorded.
    """

    context = build_context()

    decision = build_sms_decision(
        "Your service request has been updated."
    )

    result = PIIOutputDetector().check(
        context=context,
        decision=decision,
    )

    assert result.latency_ms >= 0.0