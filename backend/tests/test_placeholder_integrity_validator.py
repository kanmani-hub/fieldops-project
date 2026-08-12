"""
test_placeholder_integrity_validator.py

Tests for placeholder-integrity validation.
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
from app.services.ai.guardrails.placeholder_integrity_validator import (
    PlaceholderIntegrityValidator,
)


# ==========================================================
# Helpers
# ==========================================================


def build_context(
    *,
    customer_name: str | None = (
        "{{CUSTOMER_NAME_1}}"
    ),
    technician_name: str | None = (
        "{{TECHNICIAN_NAME_1}}"
    ),
) -> CommunicationContext:
    """
    Build a sanitized communication context.
    """

    return CommunicationContext(
        job_id="JOB-1001",
        notification_type="job_assigned",
        recipient_type="CUSTOMER",
        channel="SMS",
        customer_name=customer_name,
        technician_name=technician_name,
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
# Interface Test
# ==========================================================


def test_validator_implements_guardrail_interface() -> None:
    """
    The validator follows the common guardrail interface.
    """

    validator = (
        PlaceholderIntegrityValidator()
    )

    assert isinstance(
        validator,
        GuardrailChecker,
    )


# ==========================================================
# Passing Tests
# ==========================================================


def test_message_without_placeholders_passes() -> None:
    """
    Generic communication without placeholders is valid.
    """

    context = build_context(
        customer_name=None,
        technician_name=None,
    )

    decision = build_sms_decision(
        "Your service request has been updated."
    )

    result = (
        PlaceholderIntegrityValidator().check(
            context=context,
            decision=decision,
        )
    )

    assert result.passed is True
    assert result.violations == ()


def test_known_placeholder_is_preserved() -> None:
    """
    An exact placeholder from context is valid.
    """

    context = build_context()

    decision = build_sms_decision(
        "Hello {{CUSTOMER_NAME_1}}, "
        "your technician has been assigned."
    )

    result = (
        PlaceholderIntegrityValidator().check(
            context=context,
            decision=decision,
        )
    )

    assert result.passed is True
    assert result.violations == ()


def test_multiple_known_placeholders_pass() -> None:
    """
    Multiple supplied placeholders may be used together.
    """

    context = build_context()

    decision = build_sms_decision(
        "{{TECHNICIAN_NAME_1}} has been assigned to "
        "{{CUSTOMER_NAME_1}}."
    )

    result = (
        PlaceholderIntegrityValidator().check(
            context=context,
            decision=decision,
        )
    )

    assert result.passed is True
    assert result.violations == ()


def test_optional_placeholder_may_be_omitted() -> None:
    """
    The model does not need to use every context placeholder.
    """

    context = build_context()

    decision = build_sms_decision(
        "Your technician has been assigned."
    )

    result = (
        PlaceholderIntegrityValidator().check(
            context=context,
            decision=decision,
        )
    )

    assert result.passed is True
    assert result.violations == ()


# ==========================================================
# Unknown Placeholder Test
# ==========================================================


def test_unknown_placeholder_fails() -> None:
    """
    A valid-looking placeholder not supplied in context fails.
    """

    context = build_context()

    decision = build_sms_decision(
        "Hello {{MANAGER_NAME_1}}, your job was updated."
    )

    result = (
        PlaceholderIntegrityValidator().check(
            context=context,
            decision=decision,
        )
    )

    assert result.passed is False
    assert len(result.violations) == 1

    violation = result.violations[0]

    assert (
        violation.code
        == "UNKNOWN_PLACEHOLDER_DETECTED"
    )

    assert (
        violation.category
        == GuardrailCategory.PLACEHOLDER_INTEGRITY
    )

    assert (
        violation.severity
        == GuardrailSeverity.ERROR
    )

    assert violation.field == "output"

    assert violation.safe_metadata == {
        "unknown_placeholder_count": 1,
        "allowed_placeholder_count": 2,
    }


# ==========================================================
# Malformed Placeholder Tests
# ==========================================================


def test_spaced_placeholder_fails() -> None:
    """
    Spaces added inside a placeholder are invalid.
    """

    context = build_context()

    decision = build_sms_decision(
        "Hello {{ CUSTOMER_NAME_1 }}."
    )

    result = (
        PlaceholderIntegrityValidator().check(
            context=context,
            decision=decision,
        )
    )

    assert result.passed is False

    violation = result.violations[0]

    assert (
        violation.code
        == "MALFORMED_PLACEHOLDER_DETECTED"
    )

    assert violation.field == "output"

    assert violation.safe_metadata == {
        "malformed_placeholder_count": 1,
    }


def test_single_brace_placeholder_fails() -> None:
    """
    A placeholder using single braces is invalid.
    """

    context = build_context()

    decision = build_sms_decision(
        "Hello {CUSTOMER_NAME_1}."
    )

    result = (
        PlaceholderIntegrityValidator().check(
            context=context,
            decision=decision,
        )
    )

    assert result.passed is False

    assert (
        result.violations[0].code
        == "MALFORMED_PLACEHOLDER_DETECTED"
    )


def test_unclosed_placeholder_fails() -> None:
    """
    An incomplete double-brace placeholder is invalid.
    """

    context = build_context()

    decision = build_sms_decision(
        "Hello {{CUSTOMER_NAME_1."
    )

    result = (
        PlaceholderIntegrityValidator().check(
            context=context,
            decision=decision,
        )
    )

    assert result.passed is False

    assert (
        result.violations[0].code
        == "MALFORMED_PLACEHOLDER_DETECTED"
    )


# ==========================================================
# Privacy Test
# ==========================================================


def test_violation_does_not_store_generated_content() -> None:
    """
    Guardrail violations must not contain raw communication.
    """

    private_content = (
        "Private customer communication"
    )

    context = build_context()

    decision = build_sms_decision(
        f"{private_content} "
        "{{UNKNOWN_PERSON_1}}"
    )

    result = (
        PlaceholderIntegrityValidator().check(
            context=context,
            decision=decision,
        )
    )

    serialized_result = (
        result.model_dump_json()
    )

    assert (
        private_content
        not in serialized_result
    )

    assert (
        "{{UNKNOWN_PERSON_1}}"
        not in serialized_result
    )


# ==========================================================
# Immutability Test
# ==========================================================


def test_validator_does_not_modify_inputs() -> None:
    """
    The validator only inspects context and decision.
    """

    context = build_context()

    decision = build_sms_decision(
        "Hello {{CUSTOMER_NAME_1}}."
    )

    original_context = (
        context.model_dump()
    )

    original_decision = (
        decision.model_dump()
    )

    PlaceholderIntegrityValidator().check(
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


def test_validator_records_non_negative_latency() -> None:
    """
    Local guardrail execution time must be recorded.
    """

    context = build_context()

    decision = build_sms_decision(
        "Hello {{CUSTOMER_NAME_1}}."
    )

    result = (
        PlaceholderIntegrityValidator().check(
            context=context,
            decision=decision,
        )
    )

    assert result.latency_ms >= 0.0