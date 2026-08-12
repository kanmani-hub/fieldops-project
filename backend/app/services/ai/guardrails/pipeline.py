"""
pipeline.py

Central execution pipeline for FieldOps AI communication
guardrails.

Responsibilities
----------------
- Execute communication guardrails in a fixed security order
- Support production fail-fast behavior
- Aggregate checker results
- Measure total local execution latency
- Convert unexpected checker failures into safe violations
- Return ALLOW or FALLBACK through GuardrailPipelineResult

The pipeline does not:

- Modify CommunicationContext
- Modify CommunicationDecision
- Restore PII placeholders
- Render Jinja2 templates
- Store audit records
- Send notifications

Those responsibilities belong to later service layers.
"""

from __future__ import annotations

from collections.abc import Iterable
from time import perf_counter
from typing import Final

from app.services.ai.FieldOpsAI.schemas.communication import (
    CommunicationContext,
    CommunicationDecision,
)
from app.services.ai.guardrails.base import (
    GuardrailChecker,
)
from app.services.ai.guardrails.brand_safety_validator import (
    BrandSafetyValidator,
)
from app.services.ai.guardrails.channel_validator import (
    ChannelValidator,
)
from app.services.ai.guardrails.contracts import (
    GuardrailCategory,
    GuardrailCheckResult,
    GuardrailPipelineResult,
    GuardrailSeverity,
    GuardrailViolation,
)
from app.services.ai.guardrails.length_validator import (
    LengthValidator,
)
from app.services.ai.guardrails.pii_output_detector import (
    PIIOutputDetector,
)
from app.services.ai.guardrails.placeholder_integrity_validator import (
    PlaceholderIntegrityValidator,
)
from app.services.ai.guardrails.profanity_validator import (
    ProfanityValidator,
)
from app.services.ai.guardrails.tone_validator import (
    ToneValidator,
)


class GuardrailPipeline:
    """
    Execute FieldOps communication guardrails in a controlled
    and predictable order.
    """

    DEFAULT_PERFORMANCE_BUDGET_MS: Final[float] = 50.0

    # ------------------------------------------------------

    def __init__(
        self,
        *,
        checkers: Iterable[GuardrailChecker],
        fail_fast: bool = True,
        performance_budget_ms: float = (
            DEFAULT_PERFORMANCE_BUDGET_MS
        ),
    ) -> None:
        """
        Initialize the guardrail pipeline.

        Parameters
        ----------
        checkers
            Ordered guardrail checkers.

            Their order determines the order in which safety
            checks run.

        fail_fast
            When True, stop after the first failed checker.

            Production should use True.

            False may be used for diagnostics and tests when all
            violations are needed.

        performance_budget_ms
            Target execution time for the local pipeline.

            The value is exposed for monitoring and performance
            tests. Exceeding the target does not make safe
            communication unsafe by itself.
        """

        checker_tuple = tuple(
            checkers
        )

        if not checker_tuple:
            raise ValueError(
                "GuardrailPipeline requires at least one "
                "checker."
            )

        if performance_budget_ms <= 0:
            raise ValueError(
                "performance_budget_ms must be greater than "
                "zero."
            )

        checker_names = tuple(
            checker.checker_name
            for checker in checker_tuple
        )

        if (
            len(
                set(
                    checker_names
                )
            )
            != len(
                checker_names
            )
        ):
            raise ValueError(
                "Guardrail checker names must be unique."
            )

        self._checkers = checker_tuple
        self._fail_fast = fail_fast
        self._performance_budget_ms = (
            performance_budget_ms
        )

    # ------------------------------------------------------

    @classmethod
    def default(
        cls,
        *,
        fail_fast: bool = True,
        performance_budget_ms: float = (
            DEFAULT_PERFORMANCE_BUDGET_MS
        ),
    ) -> GuardrailPipeline:
        """
        Build the default local production pipeline.

        Execution order
        ---------------
        1. ChannelValidator
        2. LengthValidator
        3. PlaceholderIntegrityValidator
        4. PIIOutputDetector
        5. ProfanityValidator
        6. BrandSafetyValidator
        7. ToneValidator

        The default ToneValidator runs locally only.

        Optional Groq-based tone review will be injected later
        during integration so external latency is not confused
        with the under-50-ms local pipeline target.
        """

        return cls(
            checkers=(
                ChannelValidator(),
                LengthValidator(),
                PlaceholderIntegrityValidator(),
                PIIOutputDetector(),
                ProfanityValidator(),
                BrandSafetyValidator(),
                ToneValidator(),
            ),
            fail_fast=fail_fast,
            performance_budget_ms=(
                performance_budget_ms
            ),
        )

    # ------------------------------------------------------

    @property
    def checker_names(
        self,
    ) -> tuple[str, ...]:
        """
        Return checker names in execution order.
        """

        return tuple(
            checker.checker_name
            for checker in self._checkers
        )

    # ------------------------------------------------------

    @property
    def fail_fast(
        self,
    ) -> bool:
        """
        Return whether the pipeline stops at the first failure.
        """

        return self._fail_fast

    # ------------------------------------------------------

    @property
    def performance_budget_ms(
        self,
    ) -> float:
        """
        Return the configured local performance target.
        """

        return self._performance_budget_ms

    # ------------------------------------------------------

    def run(
        self,
        *,
        context: CommunicationContext,
        decision: CommunicationDecision,
    ) -> GuardrailPipelineResult:
        """
        Execute all required guardrails.

        Processing
        ----------
        1. Start the pipeline timer
        2. Execute each checker in order
        3. Convert checker crashes into safe failed results
        4. Stop at the first failure when fail_fast=True
        5. Aggregate results into GuardrailPipelineResult

        Returns
        -------
        GuardrailPipelineResult
            ALLOW:
                Every executed checker passed.

            FALLBACK:
                A checker failed or could not safely complete.
        """

        started_at = perf_counter()

        check_results: list[
            GuardrailCheckResult
        ] = []

        for checker in self._checkers:
            check_result = self._execute_checker(
                checker=checker,
                context=context,
                decision=decision,
            )

            check_results.append(
                check_result
            )

            if (
                self._fail_fast
                and not check_result.passed
            ):
                break

        total_latency_ms = (
            perf_counter()
            - started_at
        ) * 1000

        return GuardrailPipelineResult.from_checks(
            checks=check_results,
            total_latency_ms=total_latency_ms,
            block=False,
            reason=(
                "Generated communication failed the "
                "guardrail pipeline."
                if any(
                    not result.passed
                    for result in check_results
                )
                else None
            ),
        )

    # ------------------------------------------------------

    @staticmethod
    def _execute_checker(
        *,
        checker: GuardrailChecker,
        context: CommunicationContext,
        decision: CommunicationDecision,
    ) -> GuardrailCheckResult:
        """
        Execute one checker using fail-closed behavior.

        If a checker unexpectedly raises an exception or returns
        an invalid result, the AI output is treated as unsafe.

        The exception message is intentionally not copied into
        the violation because it could contain private data or
        internal implementation details.
        """

        started_at = perf_counter()

        try:
            result = checker.check(
                context=context,
                decision=decision,
            )

            if not isinstance(
                result,
                GuardrailCheckResult,
            ):
                raise TypeError(
                    "Checker did not return "
                    "GuardrailCheckResult."
                )

            if (
                result.checker_name
                != checker.checker_name
            ):
                raise ValueError(
                    "Checker returned a mismatched "
                    "checker_name."
                )

            return result

        except Exception:
            latency_ms = (
                perf_counter()
                - started_at
            ) * 1000

            violation = GuardrailViolation(
                code="GUARDRAIL_CHECKER_FAILED",
                category=GuardrailCategory.SYSTEM,
                severity=GuardrailSeverity.CRITICAL,
                message=(
                    "A required communication safety check "
                    "could not be completed."
                ),
                field="response",
                safe_metadata={
                    "checker_name": (
                        checker.checker_name
                    ),
                },
            )

            return GuardrailCheckResult(
                checker_name=checker.checker_name,
                passed=False,
                violations=(
                    violation,
                ),
                latency_ms=latency_ms,
            )