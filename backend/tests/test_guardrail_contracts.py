"""
test_guardrail_contracts.py

Tests for the shared FieldOps AI guardrail contracts.
"""

from __future__ import annotations

import pytest

from pydantic import ValidationError

from app.services.ai.guardrails.contracts import (
    GuardrailCategory,
    GuardrailCheckResult,
    GuardrailDecision,
    GuardrailPipelineResult,
    GuardrailSeverity,
    GuardrailViolation,
)


# ==========================================================
# Test Helpers
# ==========================================================


def build_length_violation() -> GuardrailViolation:
    """
    Build a safe sample length violation.
    """

    return GuardrailViolation(
        code="SMS_MESSAGE_TOO_LONG",
        category=GuardrailCategory.LENGTH,
        severity=GuardrailSeverity.ERROR,
        message="The SMS message exceeds 160 characters.",
        field="output",
        safe_metadata={
            "actual_length": 175,
            "maximum_length": 160,
        },
    )


# ==========================================================
# GuardrailViolation Tests
# ==========================================================


def test_violation_accepts_safe_metadata() -> None:
    """
    Violation metadata may contain safe measurements.
    """

    violation = build_length_violation()

    assert (
        violation.code
        == "SMS_MESSAGE_TOO_LONG"
    )

    assert (
        violation.category
        == GuardrailCategory.LENGTH
    )

    assert violation.field == "output"

    assert (
        violation.safe_metadata[
            "actual_length"
        ]
        == 175
    )


def test_violation_rejects_invalid_code_format() -> None:
    """
    Violation codes must use uppercase snake case.
    """

    with pytest.raises(
        ValidationError
    ):
        GuardrailViolation(
            code="sms-message-too-long",
            category=GuardrailCategory.LENGTH,
            severity=GuardrailSeverity.ERROR,
            message="Invalid length.",
            field="message",
        )


def test_violation_rejects_unknown_fields() -> None:
    """
    Guardrail contracts must reject unexpected properties.
    """

    with pytest.raises(
        ValidationError
    ):
        GuardrailViolation(
            code="SMS_MESSAGE_TOO_LONG",
            category=GuardrailCategory.LENGTH,
            severity=GuardrailSeverity.ERROR,
            message="Invalid length.",
            field="message",
            raw_generated_message=(
                "Unsafe raw content"
            ),
        )


# ==========================================================
# GuardrailCheckResult Tests
# ==========================================================


def test_passed_check_contains_no_violations() -> None:
    """
    Successful checker results contain no violations.
    """

    result = GuardrailCheckResult(
        checker_name="length_validator",
        passed=True,
        violations=(),
        latency_ms=0.8,
    )

    assert result.passed is True
    assert result.violations == ()


def test_failed_check_contains_violations() -> None:
    """
    Failed checker results contain one or more violations.
    """

    violation = build_length_violation()

    result = GuardrailCheckResult(
        checker_name="length_validator",
        passed=False,
        violations=(
            violation,
        ),
        latency_ms=0.9,
    )

    assert result.passed is False

    assert result.violations == (
        violation,
    )


def test_passed_check_rejects_violations() -> None:
    """
    A passed checker cannot contain violations.
    """

    with pytest.raises(
        ValidationError,
        match=(
            "A passed guardrail check cannot contain "
            "violations"
        ),
    ):
        GuardrailCheckResult(
            checker_name="length_validator",
            passed=True,
            violations=(
                build_length_violation(),
            ),
            latency_ms=0.5,
        )


def test_failed_check_requires_violation() -> None:
    """
    A failed checker must explain why it failed.
    """

    with pytest.raises(
        ValidationError,
        match=(
            "A failed guardrail check must contain at least "
            "one violation"
        ),
    ):
        GuardrailCheckResult(
            checker_name="length_validator",
            passed=False,
            violations=(),
            latency_ms=0.5,
        )


def test_checker_name_requires_lower_snake_case() -> None:
    """
    Checker identifiers must be stable and machine-readable.
    """

    with pytest.raises(
        ValidationError
    ):
        GuardrailCheckResult(
            checker_name="LengthValidator",
            passed=True,
            violations=(),
            latency_ms=0.5,
        )


# ==========================================================
# GuardrailPipelineResult Tests
# ==========================================================


def test_pipeline_from_passed_checks_returns_allow() -> None:
    """
    A clean pipeline allows generated communication.
    """

    check = GuardrailCheckResult(
        checker_name="length_validator",
        passed=True,
        violations=(),
        latency_ms=0.5,
    )

    result = (
        GuardrailPipelineResult.from_checks(
            checks=[
                check,
            ],
            total_latency_ms=0.5,
        )
    )

    assert (
        result.decision
        == GuardrailDecision.ALLOW
    )

    assert result.passed is True

    assert (
        result.requires_fallback
        is False
    )

    assert result.blocked is False
    assert result.reason is None


def test_pipeline_from_failed_checks_returns_fallback() -> None:
    """
    Normal AI output violations require a Jinja2 fallback.
    """

    violation = build_length_violation()

    check = GuardrailCheckResult(
        checker_name="length_validator",
        passed=False,
        violations=(
            violation,
        ),
        latency_ms=0.7,
    )

    result = (
        GuardrailPipelineResult.from_checks(
            checks=[
                check,
            ],
            total_latency_ms=0.7,
        )
    )

    assert (
        result.decision
        == GuardrailDecision.FALLBACK
    )

    assert result.passed is False

    assert (
        result.requires_fallback
        is True
    )

    assert result.blocked is False

    assert result.violations == (
        violation,
    )

    assert result.reason is not None


def test_pipeline_can_return_block() -> None:
    """
    Critical processing may stop when no safe output can be
    produced.
    """

    violation = GuardrailViolation(
        code="CRITICAL_PII_LEAK",
        category=GuardrailCategory.PII,
        severity=GuardrailSeverity.CRITICAL,
        message=(
            "Generated communication contains prohibited "
            "private information."
        ),
        field="message",
        safe_metadata={
            "pii_category": "EMAIL",
        },
    )

    check = GuardrailCheckResult(
        checker_name="pii_output_detector",
        passed=False,
        violations=(
            violation,
        ),
        latency_ms=1.2,
    )

    result = (
        GuardrailPipelineResult.from_checks(
            checks=[
                check,
            ],
            total_latency_ms=1.2,
            block=True,
            reason=(
                "No safe communication output is available."
            ),
        )
    )

    assert (
        result.decision
        == GuardrailDecision.BLOCK
    )

    assert result.blocked is True

    assert (
        result.requires_fallback
        is False
    )


def test_allow_rejects_violations() -> None:
    """
    ALLOW cannot be manually constructed with violations.
    """

    with pytest.raises(
        ValidationError,
        match="ALLOW cannot contain violations",
    ):
        GuardrailPipelineResult(
            decision=GuardrailDecision.ALLOW,
            checks=(),
            violations=(
                build_length_violation(),
            ),
            total_latency_ms=0.5,
            reason=None,
        )


def test_allow_rejects_reason() -> None:
    """
    A successful result must not contain a failure reason.
    """

    with pytest.raises(
        ValidationError,
        match=(
            "ALLOW must not contain a fallback or block "
            "reason"
        ),
    ):
        GuardrailPipelineResult(
            decision=GuardrailDecision.ALLOW,
            checks=(),
            violations=(),
            total_latency_ms=0.5,
            reason="Unexpected reason.",
        )


@pytest.mark.parametrize(
    "decision",
    [
        GuardrailDecision.FALLBACK,
        GuardrailDecision.BLOCK,
    ],
)
def test_failed_decision_requires_violations(
    decision: GuardrailDecision,
) -> None:
    """
    FALLBACK and BLOCK require a recorded violation.
    """

    with pytest.raises(
        ValidationError,
        match=(
            "FALLBACK or BLOCK requires at least one "
            "violation"
        ),
    ):
        GuardrailPipelineResult(
            decision=decision,
            checks=(),
            violations=(),
            total_latency_ms=0.5,
            reason="Guardrail failed.",
        )


def test_failed_decision_requires_reason() -> None:
    """
    Fallback decisions must have an audit-safe reason.
    """

    with pytest.raises(
        ValidationError,
        match=(
            "FALLBACK or BLOCK requires a reason"
        ),
    ):
        GuardrailPipelineResult(
            decision=GuardrailDecision.FALLBACK,
            checks=(),
            violations=(
                build_length_violation(),
            ),
            total_latency_ms=0.5,
            reason=None,
        )


def test_pipeline_flattens_multiple_check_violations() -> None:
    """
    Pipeline results aggregate violations from all checkers.
    """

    length_violation = (
        build_length_violation()
    )

    tone_violation = GuardrailViolation(
        code="AGGRESSIVE_TONE_DETECTED",
        category=GuardrailCategory.TONE,
        severity=GuardrailSeverity.ERROR,
        message=(
            "Generated communication does not use an "
            "approved tone."
        ),
        field="message",
        safe_metadata={
            "expected_tone": "PROFESSIONAL",
        },
    )

    length_check = GuardrailCheckResult(
        checker_name="length_validator",
        passed=False,
        violations=(
            length_violation,
        ),
        latency_ms=0.6,
    )

    tone_check = GuardrailCheckResult(
        checker_name="tone_validator",
        passed=False,
        violations=(
            tone_violation,
        ),
        latency_ms=0.9,
    )

    result = (
        GuardrailPipelineResult.from_checks(
            checks=[
                length_check,
                tone_check,
            ],
            total_latency_ms=1.5,
        )
    )

    assert (
        len(
            result.violations
        )
        == 2
    )

    assert result.violations == (
        length_violation,
        tone_violation,
    )