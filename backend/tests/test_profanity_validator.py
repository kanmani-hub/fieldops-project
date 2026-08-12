"""
test_profanity_validator.py

Tests for profanity detection in AI-generated communication.
"""

from __future__ import annotations

from pathlib import Path

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
from app.services.ai.guardrails.profanity_validator import (
    ProfanityConfigurationError,
    ProfanityLexicon,
    ProfanityValidator,
)


# ==========================================================
# Helpers
# ==========================================================


def build_context() -> CommunicationContext:
    """
    Build a valid sanitized SMS context.
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
# Lexicon Tests
# ==========================================================


def test_default_lexicon_contains_at_least_500_terms() -> None:
    """
    Production vocabulary must meet the Story 0.4 minimum.
    """

    lexicon = ProfanityLexicon.default()

    assert (
        lexicon.expanded_term_count
        >= 500
    )

    assert (
        lexicon.canonical_term_count
        > 0
    )


def test_missing_lexicon_file_raises_error(
    tmp_path: Path,
) -> None:
    """
    Missing safety configuration must fail explicitly.
    """

    missing_file = (
        tmp_path
        / "missing_profanity.txt"
    )

    with pytest.raises(
        ProfanityConfigurationError,
        match=(
            "Profanity vocabulary file was not found"
        ),
    ):
        ProfanityLexicon.from_file(
            missing_file
        )


# ==========================================================
# Interface and Clean Content
# ==========================================================


def test_validator_implements_guardrail_interface() -> None:
    """
    ProfanityValidator follows the shared checker interface.
    """

    assert isinstance(
        ProfanityValidator(),
        GuardrailChecker,
    )


def test_clean_message_passes() -> None:
    """
    Normal FieldOps communication must pass.
    """

    context = build_context()

    decision = build_sms_decision(
        "Your technician is on the way."
    )

    result = ProfanityValidator().check(
        context=context,
        decision=decision,
    )

    assert result.passed is True
    assert result.violations == ()


# ==========================================================
# Exact and Normalized Detection
# ==========================================================


@pytest.mark.parametrize(
    "message",
    [
        "This update is bullshit.",
        "This update is BULLSHIT.",
        "This update is bull5hit.",
        "This update is buuuuullshit.",
    ],
)
def test_exact_and_normalized_profanity_fails(
    message: str,
) -> None:
    """
    Exact, case, leetspeak, and repeated forms must fail.
    """

    context = build_context()

    decision = build_sms_decision(
        message
    )

    result = ProfanityValidator().check(
        context=context,
        decision=decision,
    )

    assert result.passed is False
    assert len(result.violations) == 1

    violation = result.violations[0]

    assert (
        violation.code
        == "PROFANITY_DETECTED"
    )

    assert (
        violation.category
        == GuardrailCategory.PROFANITY
    )

    assert (
        violation.severity
        == GuardrailSeverity.ERROR
    )

    assert violation.field == "output"

    assert (
        violation.safe_metadata[
            "match_count"
        ]
        >= 1
    )


def test_transposed_letter_variant_fails() -> None:
    """
    An adjacent-letter transposition must be detected.
    """

    context = build_context()

    decision = build_sms_decision(
        "This update is bullshti."
    )

    result = ProfanityValidator().check(
        context=context,
        decision=decision,
    )

    assert result.passed is False

    assert (
        result.violations[0]
        .safe_metadata[
            "lexicon_match_count"
        ]
        == 1
    )


def test_duplicated_letter_variant_fails() -> None:
    """
    One repeated internal letter must be detected.
    """

    context = build_context()

    decision = build_sms_decision(
        "This update is bullshhit."
    )

    result = ProfanityValidator().check(
        context=context,
        decision=decision,
    )

    assert result.passed is False

    assert (
        result.violations[0]
        .safe_metadata[
            "lexicon_match_count"
        ]
        == 1
    )


def test_levenshtein_misspelling_fails() -> None:
    """
    A one-character substitution in a longer term must fail.
    """

    context = build_context()

    decision = build_sms_decision(
        "This update is bullshat."
    )

    result = ProfanityValidator().check(
        context=context,
        decision=decision,
    )

    assert result.passed is False

    assert (
        result.violations[0]
        .safe_metadata[
            "fuzzy_match_count"
        ]
        == 1
    )


# ==========================================================
# Placeholder and False-Positive Protection
# ==========================================================


def test_placeholders_are_ignored() -> None:
    """
    Sanitizer placeholders must not be inspected as words.
    """

    context = build_context()

    decision = build_sms_decision(
        "Hello {{CUSTOMER_NAME_1}}, "
        "{{TECHNICIAN_NAME_1}} is on the way."
    )

    result = ProfanityValidator().check(
        context=context,
        decision=decision,
    )

    assert result.passed is True
    assert result.violations == ()


@pytest.mark.parametrize(
    "safe_word",
    [
        "witch",
        "pitch",
        "hitch",
        "ditch",
    ],
)
def test_common_short_words_do_not_false_positive(
    safe_word: str,
) -> None:
    """
    Fuzzy matching is disabled for short words to reduce false
    positives.
    """

    context = build_context()

    decision = build_sms_decision(
        f"The word is {safe_word}."
    )

    result = ProfanityValidator().check(
        context=context,
        decision=decision,
    )

    assert result.passed is True
    assert result.violations == ()


# ==========================================================
# Field and Count Tests
# ==========================================================


def test_validator_scans_email_subject() -> None:
    """
    Email subject must be checked in addition to message.
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
        subject="Bullshit service update",
        message=(
            "Your service request has been updated."
        ),
        tone="PROFESSIONAL",
        confidence=0.95,
    )

    result = ProfanityValidator().check(
        context=context,
        decision=decision,
    )

    assert result.passed is False

    assert (
        result.violations[0].field
        == "output"
    )


def test_multiple_matches_record_safe_counts() -> None:
    """
    Multiple matches are counted without storing the terms.
    """

    context = build_context()

    decision = build_sms_decision(
        "This is bullshit and dumbass behavior."
    )

    result = ProfanityValidator().check(
        context=context,
        decision=decision,
    )

    assert result.passed is False

    assert (
        result.violations[0]
        .safe_metadata[
            "match_count"
        ]
        == 2
    )


# ==========================================================
# Privacy, Immutability, and Timing
# ==========================================================


def test_violation_does_not_store_generated_content() -> None:
    """
    Audit-safe results must not contain the generated content
    or detected profanity.
    """

    generated_content = (
        "Private communication containing bullshit."
    )

    context = build_context()

    decision = build_sms_decision(
        generated_content
    )

    result = ProfanityValidator().check(
        context=context,
        decision=decision,
    )

    serialized_result = (
        result.model_dump_json()
    )

    assert (
        generated_content
        not in serialized_result
    )

    assert (
        "bullshit"
        not in serialized_result.lower()
    )


def test_validator_does_not_modify_inputs() -> None:
    """
    The validator inspects but does not change its inputs.
    """

    context = build_context()

    decision = build_sms_decision(
        "This update is bullshit."
    )

    original_context = (
        context.model_dump()
    )

    original_decision = (
        decision.model_dump()
    )

    ProfanityValidator().check(
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
    Local checker execution time must be recorded.
    """

    context = build_context()

    decision = build_sms_decision(
        "Your service request has been updated."
    )

    result = ProfanityValidator().check(
        context=context,
        decision=decision,
    )

    assert result.latency_ms >= 0.0