"""
base.py

Common interface for all FieldOps communication guardrails.

Every guardrail checker receives:

- The original validated CommunicationContext
- The AI-generated CommunicationDecision

Every guardrail checker returns:

- GuardrailCheckResult

Using one interface allows the GuardrailPipeline to execute
different validators in the same way.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.services.ai.FieldOpsAI.schemas.communication import (
    CommunicationContext,
    CommunicationDecision,
)
from app.services.ai.guardrails.contracts import (
    GuardrailCheckResult,
)


@runtime_checkable
class GuardrailChecker(Protocol):
    """
    Interface implemented by every communication guardrail.

    Future implementations include:

    - LengthValidator
    - ProfanityValidator
    - ToneValidator
    - BrandSafetyValidator
    - PIIOutputDetector
    - ChannelValidator
    - PlaceholderIntegrityValidator
    """

    checker_name: str

    def check(
        self,
        *,
        context: CommunicationContext,
        decision: CommunicationDecision,
    ) -> GuardrailCheckResult:
        """
        Validate one generated communication decision.

        The checker must not:

        - Modify the decision
        - Send the message
        - Store raw message content
        - Trigger the Jinja2 fallback directly

        It only returns a structured check result.
        """

        ...