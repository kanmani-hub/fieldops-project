"""
test_brand_safety_validator.py

Tests for deterministic FieldOps brand-safety validation.
"""

from __future__ import annotations

import pytest

from pydantic import ValidationError

from app.services.ai.FieldOpsAI.schemas.communication import (
    CommunicationContext,
    CommunicationDecision,
)
from app.services.ai.guardrails.base import (
    GuardrailChecker,
)
from app.services.ai.guardrails.brand_safety_validator import (
    BrandSafetyMatchType,
    BrandSafetyRule,
    BrandSafetyRuleCategory,
    BrandSafetyRuleProvider,
    BrandSafetyValidator,
    StaticBrandSafetyRuleProvider,
)
from app.services.ai.guardrails.contracts import (
    GuardrailCategory,
    GuardrailSeverity,
)


# ==========================================================
# Helpers
# ==========================================================


def build_context() -> CommunicationContext:
    """
    Build a valid sanitized communication context.
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


def build_custom_validator(
    *rules: BrandSafetyRule,
) -> BrandSafetyValidator:
    """
    Build a validator with injected custom rules.
    """

    provider = StaticBrandSafetyRuleProvider(
        rules
    )

    return BrandSafetyValidator(
        rule_provider=provider
    )


# ==========================================================
# Interface Tests
# ==========================================================


def test_static_provider_implements_rule_provider() -> None:
    """
    Static provider follows the shared rule-provider interface.
    """

    provider = StaticBrandSafetyRuleProvider(
        []
    )

    assert isinstance(
        provider,
        BrandSafetyRuleProvider,
    )


def test_validator_implements_guardrail_interface() -> None:
    """
    BrandSafetyValidator follows GuardrailChecker.
    """

    assert isinstance(
        BrandSafetyValidator(),
        GuardrailChecker,
    )


# ==========================================================
# Passing Test
# ==========================================================


def test_clean_message_passes() -> None:
    """
    Normal FieldOps communication must pass.
    """

    context = build_context()

    decision = build_sms_decision(
        "Your technician is on the way."
    )

    result = BrandSafetyValidator().check(
        context=context,
        decision=decision,
    )

    assert result.passed is True
    assert result.violations == ()


# ==========================================================
# Competitor Test
# ==========================================================


def test_configured_competitor_mention_fails() -> None:
    """
    Administrator-configured competitor names must be blocked.
    """

    rule = BrandSafetyRule(
        rule_id="COMPETITOR_ACME_SERVICES",
        category=BrandSafetyRuleCategory.COMPETITOR,
        match_type=BrandSafetyMatchType.PHRASE,
        pattern="Acme Services",
    )

    validator = build_custom_validator(
        rule
    )

    result = validator.check(
        context=build_context(),
        decision=build_sms_decision(
            "You should contact Acme Services instead."
        ),
    )

    assert result.passed is False
    assert len(result.violations) == 1

    violation = result.violations[0]

    assert (
        violation.code
        == "BRAND_COMPETITOR_MENTION"
    )

    assert (
        violation.category
        == GuardrailCategory.BRAND_SAFETY
    )

    assert (
        violation.severity
        == GuardrailSeverity.ERROR
    )

    assert violation.field == "output"

    assert violation.safe_metadata == {
        "rule_id": "COMPETITOR_ACME_SERVICES",
        "rule_category": "COMPETITOR",
        "match_type": "PHRASE",
        "match_count": 1,
    }


# ==========================================================
# Default Rule Tests
# ==========================================================


def test_political_content_fails() -> None:
    """
    Political promotion must be blocked.
    """

    result = BrandSafetyValidator().check(
        context=build_context(),
        decision=build_sms_decision(
            "Please vote for this candidate."
        ),
    )

    assert result.passed is False

    assert (
        result.violations[0].code
        == "BRAND_POLITICAL_CONTENT"
    )


def test_off_brand_language_fails() -> None:
    """
    Rude or dismissive language must be blocked.
    """

    result = BrandSafetyValidator().check(
        context=build_context(),
        decision=build_sms_decision(
            "This is not our problem."
        ),
    )

    assert result.passed is False

    assert (
        result.violations[0].code
        == "BRAND_OFF_BRAND_LANGUAGE"
    )


def test_blocked_business_promise_fails() -> None:
    """
    Unsupported promises must be blocked.
    """

    result = BrandSafetyValidator().check(
        context=build_context(),
        decision=build_sms_decision(
            "You will receive a guaranteed refund."
        ),
    )

    assert result.passed is False

    assert (
        result.violations[0].code
        == "BRAND_BLOCKED_PHRASE"
    )


# ==========================================================
# Matching Behavior Tests
# ==========================================================


def test_matching_is_case_insensitive_by_default() -> None:
    """
    Default rules must match regardless of letter case.
    """

    result = BrandSafetyValidator().check(
        context=build_context(),
        decision=build_sms_decision(
            "THIS IS NOT OUR PROBLEM."
        ),
    )

    assert result.passed is False


def test_word_rule_does_not_match_inside_larger_word() -> None:
    """
    WORD rules must respect complete-term boundaries.
    """

    rule = BrandSafetyRule(
        rule_id="COMPETITOR_ACME",
        category=BrandSafetyRuleCategory.COMPETITOR,
        match_type=BrandSafetyMatchType.WORD,
        pattern="Acme",
    )

    validator = build_custom_validator(
        rule
    )

    result = validator.check(
        context=build_context(),
        decision=build_sms_decision(
            "Acmeology is only a sample project name."
        ),
    )

    assert result.passed is True
    assert result.violations == ()


def test_phrase_matching_allows_whitespace_variation() -> None:
    """
    PHRASE rules support multiple spaces between words.
    """

    result = BrandSafetyValidator().check(
        context=build_context(),
        decision=build_sms_decision(
            "Please vote     for this candidate."
        ),
    )

    assert result.passed is False

    assert (
        result.violations[0].code
        == "BRAND_POLITICAL_CONTENT"
    )


def test_inactive_rule_is_ignored() -> None:
    """
    Disabled administrator rules must not run.
    """

    rule = BrandSafetyRule(
        rule_id="DISABLED_COMPETITOR",
        category=BrandSafetyRuleCategory.COMPETITOR,
        match_type=BrandSafetyMatchType.WORD,
        pattern="Acme",
        active=False,
    )

    validator = build_custom_validator(
        rule
    )

    result = validator.check(
        context=build_context(),
        decision=build_sms_decision(
            "Contact Acme."
        ),
    )

    assert result.passed is True
    assert result.violations == ()


# ==========================================================
# Multiple Rule Test
# ==========================================================


def test_multiple_rules_create_multiple_violations() -> None:
    """
    Every matched rule must receive an audit-safe violation.
    """

    result = BrandSafetyValidator().check(
        context=build_context(),
        decision=build_sms_decision(
            "This is not our problem. Vote for the candidate."
        ),
    )

    assert result.passed is False
    assert len(result.violations) == 2

    codes = {
        violation.code
        for violation in result.violations
    }

    assert codes == {
        "BRAND_OFF_BRAND_LANGUAGE",
        "BRAND_POLITICAL_CONTENT",
    }


# ==========================================================
# Field Test
# ==========================================================


def test_validator_scans_email_subject() -> None:
    """
    Subject and title must be scanned in addition to message.
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
        subject="Guaranteed refund for your service",
        message="Your service request has been updated.",
        tone="PROFESSIONAL",
        confidence=0.95,
    )

    result = BrandSafetyValidator().check(
        context=context,
        decision=decision,
    )

    assert result.passed is False
    assert result.violations[0].field == "output"

    assert (
        result.violations[0].code
        == "BRAND_BLOCKED_PHRASE"
    )


# ==========================================================
# Placeholder Test
# ==========================================================


def test_placeholders_are_ignored() -> None:
    """
    Sanitizer placeholders must not be treated as content.
    """

    rule = BrandSafetyRule(
        rule_id="BLOCK_CUSTOMER_WORD",
        category=BrandSafetyRuleCategory.BLOCKED_PHRASE,
        match_type=BrandSafetyMatchType.WORD,
        pattern="customer",
    )

    validator = build_custom_validator(
        rule
    )

    result = validator.check(
        context=build_context(),
        decision=build_sms_decision(
            "Hello {{CUSTOMER_NAME_1}}."
        ),
    )

    assert result.passed is True
    assert result.violations == ()


# ==========================================================
# Safe Audit Test
# ==========================================================


def test_violation_does_not_store_content_or_pattern() -> None:
    """
    Violation output must not expose the generated message or
    configured blocked phrase.
    """

    blocked_pattern = "Private Competitor"

    generated_content = (
        "Use Private Competitor instead."
    )

    rule = BrandSafetyRule(
        rule_id="PRIVATE_COMPETITOR_RULE",
        category=BrandSafetyRuleCategory.COMPETITOR,
        match_type=BrandSafetyMatchType.PHRASE,
        pattern=blocked_pattern,
    )

    validator = build_custom_validator(
        rule
    )

    result = validator.check(
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

    assert (
        blocked_pattern
        not in serialized_result
    )


# ==========================================================
# Immutability and Timing
# ==========================================================


def test_validator_does_not_modify_inputs() -> None:
    """
    Brand-safety validation only inspects its inputs.
    """

    context = build_context()

    decision = build_sms_decision(
        "This is not our problem."
    )

    original_context = (
        context.model_dump()
    )

    original_decision = (
        decision.model_dump()
    )

    BrandSafetyValidator().check(
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

    result = BrandSafetyValidator().check(
        context=build_context(),
        decision=build_sms_decision(
            "Your service request has been updated."
        ),
    )

    assert result.latency_ms >= 0.0


# ==========================================================
# Rule Validation Test
# ==========================================================


def test_rule_rejects_invalid_rule_id() -> None:
    """
    Rule IDs must use uppercase machine-readable formatting.
    """

    with pytest.raises(
        ValidationError
    ):
        BrandSafetyRule(
            rule_id="invalid-rule-id",
            category=(
                BrandSafetyRuleCategory.COMPETITOR
            ),
            match_type=(
                BrandSafetyMatchType.WORD
            ),
            pattern="Acme",
        )