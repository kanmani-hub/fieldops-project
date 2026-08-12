"""
channel_validator.py

Channel consistency validation for FieldOps-generated
communication.

The validator verifies that the channel requested by the backend
matches the channel returned by the Communication Agent.

Example
-------
Requested channel:
    SMS

Generated channel:
    EMAIL

Result:
    CHANNEL_MISMATCH violation

This validator:

- Does not modify CommunicationContext
- Does not modify CommunicationDecision
- Does not render fallback templates
- Does not send notifications
- Does not store generated message content
"""

from __future__ import annotations

from time import perf_counter
from typing import Final

from app.services.ai.FieldOpsAI.schemas.communication import (
    CommunicationContext,
    CommunicationDecision,
)
from app.services.ai.guardrails.contracts import (
    GuardrailCategory,
    GuardrailCheckResult,
    GuardrailSeverity,
    GuardrailViolation,
)


class ChannelValidator:
    """
    Verify that the generated channel matches the requested
    communication channel.
    """

    checker_name: Final[str] = "channel_validator"

    # ------------------------------------------------------

    def check(
        self,
        *,
        context: CommunicationContext,
        decision: CommunicationDecision,
    ) -> GuardrailCheckResult:
        """
        Compare the requested and generated channels.

        Returns
        -------
        GuardrailCheckResult
            passed=True:
                The generated channel matches the requested
                channel.

            passed=False:
                The channels differ and the generated output
                must not be delivered.
        """

        started_at = perf_counter()

        violations: list[
            GuardrailViolation
        ] = []

        if (
            context.channel
            != decision.channel
        ):
            violations.append(
                GuardrailViolation(
                    code="COMMUNICATION_CHANNEL_MISMATCH",
                    category=(
                        GuardrailCategory.CHANNEL_MISMATCH
                    ),
                    severity=GuardrailSeverity.ERROR,
                    message=(
                        "Generated communication channel does "
                        "not match the requested channel."
                    ),
                    field="channel",
                    safe_metadata={
                        "requested_channel": (
                            context.channel
                        ),
                        "generated_channel": (
                            decision.channel
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