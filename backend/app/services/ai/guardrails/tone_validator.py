"""
tone_validator.py

Tone validation for FieldOps-generated communication.

The validator provides two layers:

1. Fast deterministic local checks
   - Aggressive language
   - Sarcastic language
   - Clearly unprofessional language
   - Obvious sentiment/tone mismatches

2. Optional external review
   - Used only for ambiguous language
   - Supplied through ToneReviewProvider
   - A future Groq adapter will implement this interface

The validator never stores raw generated communication inside a
GuardrailViolation.
"""

from __future__ import annotations

import re

from enum import StrEnum
from time import perf_counter
from typing import Final, Protocol, runtime_checkable

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)

from app.services.ai.FieldOpsAI.schemas.communication import (
    CommunicationContext,
    CommunicationDecision,
)
from app.services.ai.guardrails.contracts import (
    GuardrailCategory,
    GuardrailCheckResult,
    GuardrailField,
    GuardrailSeverity,
    GuardrailViolation,
)


# ==========================================================
# External Review Contracts
# ==========================================================


class ToneReviewVerdict(StrEnum):
    """
    Verdict returned by an optional external tone reviewer.
    """

    SAFE = "SAFE"
    AGGRESSIVE = "AGGRESSIVE"
    SARCASTIC = "SARCASTIC"
    UNPROFESSIONAL = "UNPROFESSIONAL"


class ToneReviewResult(BaseModel):
    """
    Structured response returned by an external tone reviewer.

    The result must never contain the original reviewed text.
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        frozen=True,
    )

    verdict: ToneReviewVerdict = Field(
        ...,
        description="Final external tone classification.",
    )

    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Reviewer confidence from 0.0 to 1.0.",
    )

    latency_ms: float = Field(
        default=0.0,
        ge=0.0,
        description="External review execution time.",
    )


class ToneReviewProviderError(RuntimeError):
    """
    Raised when an enabled external tone reviewer cannot return
    a valid result.
    """


@runtime_checkable
class ToneReviewProvider(Protocol):
    """
    Interface for optional external tone-review providers.

    A future GroqToneReviewProvider will implement this contract
    using the existing FieldOps Groq client.
    """

    provider_name: str

    def review(
        self,
        *,
        text: str,
    ) -> ToneReviewResult:
        """
        Review sanitized recipient-facing text.

        The input must already have:

        - Passed placeholder-integrity checks
        - Passed PII-output checks
        - Had placeholders masked before this method is called
        """

        ...


# ==========================================================
# Pattern Compilation
# ==========================================================


def _compile_phrase_pattern(
    phrase: str,
) -> re.Pattern[str]:
    """
    Compile one case-insensitive phrase pattern.

    Whitespace between phrase words may vary.

    Example:
        "deal with it"

    Matches:
        "deal with it"
        "deal    with it"
    """

    words = phrase.split()

    expression = (
        r"(?<![A-Za-z0-9_])"
        + r"\s+".join(
            re.escape(word)
            for word in words
        )
        + r"(?![A-Za-z0-9_])"
    )

    return re.compile(
        expression,
        re.IGNORECASE,
    )


# ==========================================================
# Tone Validator
# ==========================================================


class ToneValidator:
    """
    Validate that generated communication remains professional,
    helpful, calm, and appropriate for the supplied sentiment.
    """

    checker_name: Final[str] = "tone_validator"

    OUTPUT_FIELDS: Final[
        tuple[GuardrailField, ...]
    ] = (
        "title",
        "subject",
        "message",
    )

    PLACEHOLDER_PATTERN: Final[re.Pattern[str]] = re.compile(
        r"\{\{[A-Za-z][A-Za-z0-9_]*\}\}"
    )

    AGGRESSIVE_PHRASES: Final[tuple[str, ...]] = (
        "shut up",
        "deal with it",
        "stop complaining",
        "do not waste our time",
        "you are wasting our time",
        "figure it out yourself",
        "leave us alone",
    )

    SARCASTIC_PHRASES: Final[tuple[str, ...]] = (
        "yeah right",
        "thanks for nothing",
        "what a surprise",
        "good luck with that",
        "as if",
        "obviously you",
        "clearly you",
    )

    UNPROFESSIONAL_PHRASES: Final[tuple[str, ...]] = (
        "whatever",
        "not my problem",
        "not our problem",
        "your fault",
        "we do not care",
        "nobody cares",
    )

    # These words may be harmless or inappropriate depending on
    # the complete sentence. They trigger optional review rather
    # than an immediate violation.
    AMBIGUOUS_MARKERS: Final[tuple[str, ...]] = (
        "obviously",
        "clearly",
        "sure",
        "fine",
        "great",
        "right",
        "interesting",
    )

    AGGRESSIVE_PATTERNS: Final[
        tuple[re.Pattern[str], ...]
    ] = tuple(
        _compile_phrase_pattern(phrase)
        for phrase in AGGRESSIVE_PHRASES
    )

    SARCASTIC_PATTERNS: Final[
        tuple[re.Pattern[str], ...]
    ] = tuple(
        _compile_phrase_pattern(phrase)
        for phrase in SARCASTIC_PHRASES
    )

    UNPROFESSIONAL_PATTERNS: Final[
        tuple[re.Pattern[str], ...]
    ] = tuple(
        _compile_phrase_pattern(phrase)
        for phrase in UNPROFESSIONAL_PHRASES
    )

    AMBIGUOUS_PATTERNS: Final[
        tuple[re.Pattern[str], ...]
    ] = tuple(
        _compile_phrase_pattern(marker)
        for marker in AMBIGUOUS_MARKERS
    )

    EXTERNAL_VERDICT_CODES: Final[
        dict[ToneReviewVerdict, str]
    ] = {
        ToneReviewVerdict.AGGRESSIVE: (
            "EXTERNAL_AGGRESSIVE_TONE_DETECTED"
        ),
        ToneReviewVerdict.SARCASTIC: (
            "EXTERNAL_SARCASTIC_TONE_DETECTED"
        ),
        ToneReviewVerdict.UNPROFESSIONAL: (
            "EXTERNAL_UNPROFESSIONAL_TONE_DETECTED"
        ),
    }

    # ------------------------------------------------------

    def __init__(
        self,
        *,
        review_provider: ToneReviewProvider | None = None,
        external_review_enabled: bool = False,
    ) -> None:
        """
        Initialize the tone validator.

        Parameters
        ----------
        review_provider
            Optional provider used for ambiguous language.

        external_review_enabled
            When False, only deterministic local checks run.

            When True, ambiguous content is sent to the injected
            review provider after placeholders are masked.
        """

        if (
            external_review_enabled
            and review_provider is None
        ):
            raise ValueError(
                "A tone review provider is required when "
                "external review is enabled."
            )

        self._review_provider = review_provider
        self._external_review_enabled = (
            external_review_enabled
        )

    # ------------------------------------------------------

    def check(
        self,
        *,
        context: CommunicationContext,
        decision: CommunicationDecision,
    ) -> GuardrailCheckResult:
        """
        Validate generated communication tone.

        Processing order:

        1. Scan each recipient-facing field locally
        2. Check clear sentiment/tone contradictions
        3. Optionally review ambiguous text externally
        4. Return one structured GuardrailCheckResult
        """

        from app.services.ai.FieldOpsAI.schemas.communication import output_text_for_validation

        started_at = perf_counter()

        violations: list[GuardrailViolation] = []

        scannable_fields: list[str] = []

        validation_text = output_text_for_validation(decision.output)

        if validation_text:
            scannable_text = self._prepare_text(
                validation_text
            )

            scannable_fields.append(
                scannable_text
            )

            aggressive_count = self._count_matches(
                text=scannable_text,
                patterns=self.AGGRESSIVE_PATTERNS,
            )

            if aggressive_count > 0:
                violations.append(
                    GuardrailViolation(
                        code="AGGRESSIVE_TONE_DETECTED",
                        category=GuardrailCategory.TONE,
                        severity=GuardrailSeverity.ERROR,
                        message=(
                            "Generated communication contains "
                            "aggressive language."
                        ),
                        field="output",
                        safe_metadata={
                            "match_count": aggressive_count,
                            "detection_source": "LOCAL",
                        },
                    )
                )

            sarcastic_count = self._count_matches(
                text=scannable_text,
                patterns=self.SARCASTIC_PATTERNS,
            )

            if sarcastic_count > 0:
                violations.append(
                    GuardrailViolation(
                        code="SARCASTIC_TONE_DETECTED",
                        category=GuardrailCategory.TONE,
                        severity=GuardrailSeverity.ERROR,
                        message=(
                            "Generated communication contains "
                            "sarcastic language."
                        ),
                        field="output",
                        safe_metadata={
                            "match_count": sarcastic_count,
                            "detection_source": "LOCAL",
                        },
                    )
                )

            unprofessional_count = self._count_matches(
                text=scannable_text,
                patterns=(
                    self.UNPROFESSIONAL_PATTERNS
                ),
            )

            if unprofessional_count > 0:
                violations.append(
                    GuardrailViolation(
                        code=(
                            "UNPROFESSIONAL_TONE_DETECTED"
                        ),
                        category=GuardrailCategory.TONE,
                        severity=GuardrailSeverity.ERROR,
                        message=(
                            "Generated communication does not "
                            "meet professional tone standards."
                        ),
                        field="output",
                        safe_metadata={
                            "match_count": (
                                unprofessional_count
                            ),
                            "detection_source": "LOCAL",
                        },
                    )
                )

        tone_mismatch = self._check_tone_selection(
            context=context,
            decision=decision,
        )

        if tone_mismatch is not None:
            violations.append(
                tone_mismatch
            )

        combined_text = " ".join(
            scannable_fields
        )

        has_local_content_violation = any(
            violation.code
            in {
                "AGGRESSIVE_TONE_DETECTED",
                "SARCASTIC_TONE_DETECTED",
                "UNPROFESSIONAL_TONE_DETECTED",
            }
            for violation in violations
        )

        if (
            not has_local_content_violation
            and self._should_run_external_review(
                combined_text
            )
        ):
            external_violation = (
                self._run_external_review(
                    combined_text
                )
            )

            if external_violation is not None:
                violations.append(
                    external_violation
                )

        latency_ms = (
            perf_counter()
            - started_at
        ) * 1000

        return GuardrailCheckResult(
            checker_name=self.checker_name,
            passed=not violations,
            violations=tuple(
                violations
            ),
            latency_ms=latency_ms,
        )

    # ------------------------------------------------------

    @classmethod
    def _prepare_text(
        cls,
        value: str,
    ) -> str:
        """
        Prepare generated content for local tone analysis.

        Placeholders are removed before analysis because
        placeholder names are not recipient-facing language.
        """

        without_placeholders = (
            cls.PLACEHOLDER_PATTERN.sub(
                " ",
                value,
            )
        )

        normalized_whitespace = re.sub(
            r"\s+",
            " ",
            without_placeholders,
        )

        return normalized_whitespace.strip()

    # ------------------------------------------------------

    @staticmethod
    def _count_matches(
        *,
        text: str,
        patterns: tuple[re.Pattern[str], ...],
    ) -> int:
        """
        Count phrase-pattern matches without returning the
        matched text.
        """

        return sum(
            1
            for pattern in patterns
            for _ in pattern.finditer(text)
        )

    # ------------------------------------------------------

    @staticmethod
    def _check_tone_selection(
        *,
        context: CommunicationContext,
        decision: CommunicationDecision,
    ) -> GuardrailViolation | None:
        """
        Detect only clear sentiment/tone contradictions.

        Conservative rules are used to avoid false positives:

        - NEGATIVE sentiment must not use FRIENDLY
        - POSITIVE sentiment must not use EMPATHETIC

        PROFESSIONAL is accepted for every sentiment.
        URGENT is not rejected here because urgency may be
        supplied through workflow context outside sentiment.
        """

        mismatch = (
            (
                context.sentiment == "NEGATIVE"
                and decision.tone == "FRIENDLY"
            )
            or (
                context.sentiment == "POSITIVE"
                and decision.tone == "EMPATHETIC"
            )
        )

        if not mismatch:
            return None

        return GuardrailViolation(
            code="SENTIMENT_TONE_MISMATCH",
            category=GuardrailCategory.TONE,
            severity=GuardrailSeverity.ERROR,
            message=(
                "Generated communication tone does not match "
                "the supplied customer sentiment."
            ),
            field="tone",
            safe_metadata={
                "sentiment": context.sentiment,
                "generated_tone": decision.tone,
            },
        )

    # ------------------------------------------------------

    def _should_run_external_review(
        self,
        text: str,
    ) -> bool:
        """
        Determine whether ambiguous text needs external review.

        External review runs only when:

        - It is explicitly enabled
        - A provider exists
        - The text contains an ambiguity marker or unusual
          punctuation associated with uncertain tone
        """

        if (
            not self._external_review_enabled
            or self._review_provider is None
            or not text
        ):
            return False

        has_ambiguous_marker = any(
            pattern.search(text)
            is not None
            for pattern in self.AMBIGUOUS_PATTERNS
        )

        has_ambiguous_punctuation = (
            "!!" in text
            or "!?" in text
            or "?!" in text
        )

        return (
            has_ambiguous_marker
            or has_ambiguous_punctuation
        )

    # ------------------------------------------------------

    def _run_external_review(
        self,
        text: str,
    ) -> GuardrailViolation | None:
        """
        Execute the optional external tone review.

        The reviewed text already has placeholders removed.

        A provider error returns a safe violation so the future
        pipeline can use a deterministic Jinja2 fallback.
        """

        provider = self._review_provider

        if provider is None:
            return None

        try:
            review_result = provider.review(
                text=text
            )
        except ToneReviewProviderError:
            return GuardrailViolation(
                code="TONE_REVIEW_UNAVAILABLE",
                category=GuardrailCategory.TONE,
                severity=GuardrailSeverity.ERROR,
                message=(
                    "External tone review could not be "
                    "completed."
                ),
                field="response",
                safe_metadata={
                    "review_provider": (
                        provider.provider_name
                    ),
                },
            )

        if (
            review_result.verdict
            == ToneReviewVerdict.SAFE
        ):
            return None

        return GuardrailViolation(
            code=self.EXTERNAL_VERDICT_CODES[
                review_result.verdict
            ],
            category=GuardrailCategory.TONE,
            severity=GuardrailSeverity.ERROR,
            message=(
                "External tone review rejected the generated "
                "communication."
            ),
            field="response",
            safe_metadata={
                "review_provider": (
                    provider.provider_name
                ),
                "review_verdict": (
                    review_result.verdict.value
                ),
                "review_confidence": (
                    review_result.confidence
                ),
                "review_latency_ms": (
                    review_result.latency_ms
                ),
                "detection_source": "EXTERNAL",
            },
        )