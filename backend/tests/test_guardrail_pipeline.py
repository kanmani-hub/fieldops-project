"""
test_guardrail_pipeline.py

Tests for the central FieldOps communication guardrail pipeline.
"""

from __future__ import annotations

from statistics import mean

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
    GuardrailCheckResult,
    GuardrailDecision,
    GuardrailSeverity,
    GuardrailViolation,
)
from app.services.ai.guardrails.pipeline import (
    GuardrailPipeline,
)


# ==========================================================
# Helpers
# ==========================================================


def build_context() -> CommunicationContext:
    """
    Build a safe sanitized SMS context.
    """

    return CommunicationContext(
        job_id="JOB-1001",
        notification_type="job_assigned",
        recipient_type="CUSTOMER",
        channel="SMS",
        customer_name="{{CUSTOMER_NAME_1}}",
        technician_name="{{TECHNICIAN_NAME_1}}",
        job_status="ASSIGNED",
        sentiment="NEUTRAL",
    )


def build_safe_decision() -> CommunicationDecision:
    """
    Build a safe SMS decision.
    """

    return CommunicationDecision(
        channel="SMS",
        title=None,
        subject=None,
        message=(
            "Hello {{CUSTOMER_NAME_1}}, "
            "{{TECHNICIAN_NAME_1}} is on the way."
        ),
        tone="PROFESSIONAL",
        confidence=0.95,
    )


# ==========================================================
# Fake Checkers
# ==========================================================


class PassingChecker:
    """
    Fake checker that always passes.
    """

    checker_name = "passing_checker"

    def check(
        self,
        *,
        context: CommunicationContext,
        decision: CommunicationDecision,
    ) -> GuardrailCheckResult:
        """
        Return a passing checker result.
        """

        _ = context
        _ = decision

        return GuardrailCheckResult(
            checker_name=self.checker_name,
            passed=True,
            violations=(),
            latency_ms=0.1,
        )


class FailingChecker:
    """
    Fake checker that always fails.
    """

    checker_name = "failing_checker"

    def check(
        self,
        *,
        context: CommunicationContext,
        decision: CommunicationDecision,
    ) -> GuardrailCheckResult:
        """
        Return a failed checker result.
        """

        _ = context
        _ = decision

        violation = GuardrailViolation(
            code="TEST_GUARDRAIL_FAILURE",
            category=GuardrailCategory.SYSTEM,
            severity=GuardrailSeverity.ERROR,
            message="Test guardrail rejected communication.",
            field="response",
            safe_metadata={},
        )

        return GuardrailCheckResult(
            checker_name=self.checker_name,
            passed=False,
            violations=(
                violation,
            ),
            latency_ms=0.1,
        )


class ExplodingChecker:
    """
    Fake checker that raises unexpectedly.
    """

    checker_name = "exploding_checker"

    def check(
        self,
        *,
        context: CommunicationContext,
        decision: CommunicationDecision,
    ) -> GuardrailCheckResult:
        """
        Simulate an unexpected checker failure.
        """

        _ = context
        _ = decision

        raise RuntimeError(
            "Private generated content must not be exposed."
        )


class WrongNameChecker:
    """
    Fake checker returning an incorrect checker name.
    """

    checker_name = "wrong_name_checker"

    def check(
        self,
        *,
        context: CommunicationContext,
        decision: CommunicationDecision,
    ) -> GuardrailCheckResult:
        """
        Return a structurally valid result with the wrong name.
        """

        _ = context
        _ = decision

        return GuardrailCheckResult(
            checker_name="different_checker",
            passed=True,
            violations=(),
            latency_ms=0.1,
        )


# ==========================================================
# Construction Tests
# ==========================================================


def test_passing_checker_implements_interface() -> None:
    """
    Fake checker follows GuardrailChecker.
    """

    assert isinstance(
        PassingChecker(),
        GuardrailChecker,
    )


def test_pipeline_requires_at_least_one_checker() -> None:
    """
    An empty safety pipeline must be rejected.
    """

    with pytest.raises(
        ValueError,
        match="requires at least one checker",
    ):
        GuardrailPipeline(
            checkers=(),
        )


def test_pipeline_rejects_duplicate_checker_names() -> None:
    """
    Checker identifiers must be unique.
    """

    with pytest.raises(
        ValueError,
        match="checker names must be unique",
    ):
        GuardrailPipeline(
            checkers=(
                PassingChecker(),
                PassingChecker(),
            ),
        )


def test_pipeline_rejects_invalid_performance_budget() -> None:
    """
    The performance budget must be positive.
    """

    with pytest.raises(
        ValueError,
        match=(
            "performance_budget_ms must be greater than zero"
        ),
    ):
        GuardrailPipeline(
            checkers=(
                PassingChecker(),
            ),
            performance_budget_ms=0,
        )


# ==========================================================
# Default Order Test
# ==========================================================


def test_default_pipeline_uses_security_order() -> None:
    """
    The official validator order must remain stable.
    """

    pipeline = GuardrailPipeline.default()

    assert pipeline.checker_names == (
        "channel_validator",
        "length_validator",
        "placeholder_integrity_validator",
        "pii_output_detector",
        "profanity_validator",
        "brand_safety_validator",
        "tone_validator",
    )

    assert pipeline.fail_fast is True
    assert pipeline.performance_budget_ms == 50.0


# ==========================================================
# Successful Pipeline Test
# ==========================================================


def test_safe_communication_returns_allow() -> None:
    """
    Safe AI communication passes every checker.
    """

    pipeline = GuardrailPipeline.default()

    result = pipeline.run(
        context=build_context(),
        decision=build_safe_decision(),
    )

    assert (
        result.decision
        == GuardrailDecision.ALLOW
    )

    assert result.passed is True
    assert result.requires_fallback is False
    assert result.blocked is False
    assert result.violations == ()
    assert len(result.checks) == 7
    assert result.reason is None


# ==========================================================
# Fail-Fast Tests
# ==========================================================


def test_channel_failure_stops_pipeline_immediately() -> None:
    """
    A first-checker failure must stop production processing.
    """

    context = build_context()

    decision = CommunicationDecision(
        channel="EMAIL",
        title=None,
        subject="Your service update",
        message="Your service request has been updated.",
        tone="PROFESSIONAL",
        confidence=0.95,
    )

    pipeline = GuardrailPipeline.default()

    result = pipeline.run(
        context=context,
        decision=decision,
    )

    assert (
        result.decision
        == GuardrailDecision.FALLBACK
    )

    assert result.requires_fallback is True
    assert len(result.checks) == 1

    assert (
        result.checks[0].checker_name
        == "channel_validator"
    )

    assert (
        result.violations[0].code
        == "COMMUNICATION_CHANNEL_MISMATCH"
    )


def test_truncated_sms_completes_guardrail_pipeline() -> None:
    """
    SMS truncation prevents a length failure before guardrail validation.
    """

    decision = CommunicationDecision(
        channel="SMS",
        title=None,
        subject=None,
        message="A" * 161,
        tone="PROFESSIONAL",
        confidence=0.95,
    )

    result = GuardrailPipeline.default().run(
        context=build_context(),
        decision=decision,
    )

    assert result.decision == GuardrailDecision.ALLOW
    assert len(result.checks) == 7
    assert result.violations == ()
    assert len(decision.message) == 160
    assert decision.message.endswith("...")


# ==========================================================
# Non-Fail-Fast Test
# ==========================================================


def test_diagnostic_mode_collects_multiple_violations() -> None:
    """
    Diagnostic mode runs all checkers after failures.
    """

    decision = CommunicationDecision(
        channel="SMS",
        title=None,
        subject=None,
        message=(
            "This is bullshit. "
            + ("A" * 170)
        ),
        tone="PROFESSIONAL",
        confidence=0.95,
    )

    pipeline = GuardrailPipeline.default(
        fail_fast=False
    )

    result = pipeline.run(
        context=build_context(),
        decision=decision,
    )

    codes = {
        violation.code
        for violation in result.violations
    }

    assert (
        result.decision
        == GuardrailDecision.FALLBACK
    )

    assert len(result.checks) == 7
    assert "SMS_MESSAGE_TOO_LONG" not in codes
    assert "PROFANITY_DETECTED" in codes


# ==========================================================
# Checker-Failure Tests
# ==========================================================


def test_checker_exception_fails_closed() -> None:
    """
    Unexpected checker errors must reject the AI output.
    """

    pipeline = GuardrailPipeline(
        checkers=(
            ExplodingChecker(),
        ),
    )

    result = pipeline.run(
        context=build_context(),
        decision=build_safe_decision(),
    )

    assert (
        result.decision
        == GuardrailDecision.FALLBACK
    )

    assert result.passed is False
    assert len(result.violations) == 1

    violation = result.violations[0]

    assert (
        violation.code
        == "GUARDRAIL_CHECKER_FAILED"
    )

    assert (
        violation.category
        == GuardrailCategory.SYSTEM
    )

    assert (
        violation.severity
        == GuardrailSeverity.CRITICAL
    )

    assert violation.safe_metadata == {
        "checker_name": "exploding_checker",
    }

    serialized_result = (
        result.model_dump_json()
    )

    assert (
        "Private generated content"
        not in serialized_result
    )


def test_mismatched_checker_name_fails_closed() -> None:
    """
    A checker cannot impersonate another checker.
    """

    pipeline = GuardrailPipeline(
        checkers=(
            WrongNameChecker(),
        ),
    )

    result = pipeline.run(
        context=build_context(),
        decision=build_safe_decision(),
    )

    assert (
        result.decision
        == GuardrailDecision.FALLBACK
    )

    assert (
        result.violations[0].code
        == "GUARDRAIL_CHECKER_FAILED"
    )


# ==========================================================
# Custom Pipeline Test
# ==========================================================


def test_custom_pipeline_preserves_supplied_order() -> None:
    """
    Dependency-injected checkers run in their supplied order.
    """

    first_checker = PassingChecker()

    second_checker = FailingChecker()

    pipeline = GuardrailPipeline(
        checkers=(
            first_checker,
            second_checker,
        ),
    )

    result = pipeline.run(
        context=build_context(),
        decision=build_safe_decision(),
    )

    assert tuple(
        check.checker_name
        for check in result.checks
    ) == (
        "passing_checker",
        "failing_checker",
    )

    assert result.requires_fallback is True


# ==========================================================
# Immutability Test
# ==========================================================


def test_pipeline_does_not_modify_inputs() -> None:
    """
    The pipeline and validators only inspect their inputs.
    """

    context = build_context()
    decision = build_safe_decision()

    original_context = context.model_dump()
    original_decision = decision.model_dump()

    GuardrailPipeline.default().run(
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
# Performance Test
# ==========================================================


def test_default_local_pipeline_average_is_under_budget() -> None:
    """
    The warmed local pipeline should satisfy the Story 0.4
    average performance target.

    External tone-review latency is intentionally not included
    in the default local pipeline.
    """

    pipeline = GuardrailPipeline.default()

    context = build_context()
    decision = build_safe_decision()

    # Warm caches before measuring.
    pipeline.run(
        context=context,
        decision=decision,
    )

    measurements = [
        pipeline.run(
            context=context,
            decision=decision,
        ).total_latency_ms
        for _ in range(20)
    ]

    assert (
        mean(
            measurements
        )
        < pipeline.performance_budget_ms
    )
