"""
placeholder_integrity_validator.py

Placeholder-integrity validation for FieldOps-generated
communication.

The PII sanitizer replaces private values with placeholders
before a request is sent to an external AI provider.

Examples
--------
Original value:
    Ruby Devi

Sanitized value:
    {{CUSTOMER_NAME_1}}

The AI response must preserve any returned placeholder exactly.

This validator detects:

- Unknown placeholders
- Renamed placeholders
- Changed placeholder casing
- Whitespace added inside placeholders
- Single-brace placeholders
- Incomplete placeholder braces

The validator does not require every input placeholder to appear
in the output because optional personalization may be omitted.

This validator must run before local placeholder restoration.
"""

from __future__ import annotations

import re

from collections.abc import Iterator
from time import perf_counter
from typing import Any, Final

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


class PlaceholderIntegrityValidator:
    """
    Verify that placeholders returned by the AI are known and
    correctly formatted.
    """

    checker_name: Final[str] = (
        "placeholder_integrity_validator"
    )

    OUTPUT_FIELDS: Final[
        tuple[
            GuardrailField,
            ...,
        ]
    ] = (
        "title",
        "subject",
        "message",
    )

    # Strict placeholder format produced by the sanitizer.
    #
    # Valid examples:
    #     {{CUSTOMER_NAME_1}}
    #     {{EMAIL_1}}
    #     {{PHONE_2}}
    #
    # Invalid examples:
    #     {{ CUSTOMER_NAME_1 }}
    #     {CUSTOMER_NAME_1}
    #     {{CUSTOMER-NAME-1}}
    STRICT_PLACEHOLDER_PATTERN: Final[
        re.Pattern[str]
    ] = re.compile(
        r"\{\{[A-Za-z][A-Za-z0-9_]*\}\}"
    )

    # Finds any complete double-brace candidate, including
    # malformed values containing spaces or invalid symbols.
    DOUBLE_BRACE_CANDIDATE_PATTERN: Final[
        re.Pattern[str]
    ] = re.compile(
        r"\{\{[^{}\r\n]*\}\}"
    )

    # Finds placeholder-like values using only one pair of
    # braces.
    SINGLE_BRACE_PLACEHOLDER_PATTERN: Final[
        re.Pattern[str]
    ] = re.compile(
        r"(?<!\{)"
        r"\{[A-Za-z][A-Za-z0-9_]*\}"
        r"(?!\})"
    )

    # ------------------------------------------------------

    def check(
        self,
        *,
        context: CommunicationContext,
        decision: CommunicationDecision,
    ) -> GuardrailCheckResult:
        """
        Validate placeholders in generated communication.

        Parameters
        ----------
        context
            The sanitized CommunicationContext supplied to the
            AI provider.

        decision
            The parsed AI response before placeholder
            restoration.

        Returns
        -------
        GuardrailCheckResult
            A passing result when every returned placeholder is
            valid and known.

            A failed result when unknown or malformed
            placeholders are detected.
        """

        from app.services.ai.FieldOpsAI.schemas.communication import output_text_for_validation
        
        started_at = perf_counter()

        allowed_placeholders = (
            self._extract_allowed_placeholders(
                context
            )
        )

        violations: list[
            GuardrailViolation
        ] = []

        validation_text = output_text_for_validation(decision.output)

        if validation_text:
            unknown_count = (
                self._count_unknown_placeholders(
                    value=validation_text,
                    allowed_placeholders=(
                        allowed_placeholders
                    ),
                )
            )

            if unknown_count > 0:
                violations.append(
                    GuardrailViolation(
                        code=(
                            "UNKNOWN_PLACEHOLDER_DETECTED"
                        ),
                        category=(
                            GuardrailCategory
                            .PLACEHOLDER_INTEGRITY
                        ),
                        severity=(
                            GuardrailSeverity.ERROR
                        ),
                        message=(
                            "Generated communication contains "
                            "a placeholder that was not "
                            "provided in the sanitized context."
                        ),
                        field="output",
                        safe_metadata={
                            "unknown_placeholder_count": (
                                unknown_count
                            ),
                            "allowed_placeholder_count": (
                                len(
                                    allowed_placeholders
                                )
                            ),
                        },
                    )
                )

            malformed_count = (
                self._count_malformed_placeholders(
                    validation_text
                )
            )

            if malformed_count > 0:
                violations.append(
                    GuardrailViolation(
                        code=(
                            "MALFORMED_PLACEHOLDER_DETECTED"
                        ),
                        category=(
                            GuardrailCategory
                            .PLACEHOLDER_INTEGRITY
                        ),
                        severity=(
                            GuardrailSeverity.ERROR
                        ),
                        message=(
                            "Generated communication contains "
                            "an incorrectly formatted "
                            "placeholder."
                        ),
                        field="output",
                        safe_metadata={
                            "malformed_placeholder_count": (
                                malformed_count
                            ),
                        },
                    )
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
    def _extract_allowed_placeholders(
        cls,
        context: CommunicationContext,
    ) -> set[str]:
        """
        Extract all valid placeholders from sanitized context.

        The context may contain placeholders inside normal
        fields or nested additional-context data.

        Only exact sanitizer-style placeholders are accepted.
        """

        context_data = context.model_dump(
            mode="python"
        )

        placeholders: set[str] = set()

        for value in cls._iter_strings(
            context_data
        ):
            placeholders.update(
                cls.STRICT_PLACEHOLDER_PATTERN.findall(
                    value
                )
            )

        return placeholders

    # ------------------------------------------------------

    @classmethod
    def _count_unknown_placeholders(
        cls,
        *,
        value: str,
        allowed_placeholders: set[str],
    ) -> int:
        """
        Count validly formatted placeholders that were not
        supplied in the sanitized context.

        Example
        -------
        Allowed:
            {{CUSTOMER_NAME_1}}

        Generated:
            {{CUSTOMER_NAME_2}}

        Result:
            1 unknown placeholder
        """

        generated_placeholders = (
            cls.STRICT_PLACEHOLDER_PATTERN.findall(
                value
            )
        )

        return sum(
            1
            for placeholder
            in generated_placeholders
            if placeholder
            not in allowed_placeholders
        )

    # ------------------------------------------------------

    @classmethod
    def _count_malformed_placeholders(
        cls,
        value: str,
    ) -> int:
        """
        Count placeholder-like values with invalid formatting.

        This detects:

        - Spaces inside double braces
        - Unsupported placeholder symbols
        - Single-brace placeholders
        - Unclosed double braces
        - Unmatched closing double braces
        """

        malformed_count = 0

        double_brace_candidates = list(
            cls.DOUBLE_BRACE_CANDIDATE_PATTERN.finditer(
                value
            )
        )

        for match in double_brace_candidates:
            candidate = match.group(
                0
            )

            if (
                cls.STRICT_PLACEHOLDER_PATTERN.fullmatch(
                    candidate
                )
                is None
            ):
                malformed_count += 1

        # Remove complete double-brace values before checking
        # for single or incomplete braces. This prevents valid
        # placeholders from being counted twice.
        remaining_text = (
            cls.DOUBLE_BRACE_CANDIDATE_PATTERN.sub(
                "",
                value,
            )
        )

        malformed_count += len(
            cls.SINGLE_BRACE_PLACEHOLDER_PATTERN.findall(
                remaining_text
            )
        )

        malformed_count += (
            remaining_text.count(
                "{{"
            )
        )

        malformed_count += (
            remaining_text.count(
                "}}"
            )
        )

        return malformed_count

    # ------------------------------------------------------

    @classmethod
    def _iter_strings(
        cls,
        value: Any,
    ) -> Iterator[str]:
        """
        Recursively yield strings from structured context data.

        Supported structures include:

        - Dictionaries
        - Lists
        - Tuples
        - Sets
        - Nested combinations of these structures
        """

        if isinstance(
            value,
            str,
        ):
            yield value
            return

        if isinstance(
            value,
            dict,
        ):
            for nested_value in value.values():
                yield from cls._iter_strings(
                    nested_value
                )

            return

        if isinstance(
            value,
            (
                list,
                tuple,
                set,
            ),
        ):
            for nested_value in value:
                yield from cls._iter_strings(
                    nested_value
                )