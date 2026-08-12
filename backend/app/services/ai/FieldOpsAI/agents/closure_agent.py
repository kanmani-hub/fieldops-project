"""
closure_agent.py

Closure Agent for FieldOps Commander AI.

Responsibilities
----------------
- Generate structured job completion summaries.
- Create customer-friendly completion messages.
- Generate invoice descriptions.
- Recommend follow-up requirements.
- Return a validated ClosureDecision.

The Closure Agent NEVER:
- Updates the database.
- Changes job status.
- Sends notifications.
- Makes business decisions.
"""

from __future__ import annotations

import logging
import time

from typing import Optional

from app.services.ai.FieldOpsAI.runtime.orchestrator import AIOrchestrator,ai_orchestrator
from app.services.ai.FieldOpsAI.schemas.ai_task import AITask
from app.services.ai.FieldOpsAI.schemas.closure import (
    ClosureContext,
    ClosureDecision,
)

logger = logging.getLogger(__name__)


class ClosureAgent:
    """
    AI agent responsible for generating
    structured job closure information.
    """

    def __init__(
        self,
        orchestrator: Optional[AIOrchestrator] = None,
    ) -> None:
        """
        Initialize the Closure Agent.
        """

        self.orchestrator = orchestrator or ai_orchestrator

    # ---------------------------------------------------------

    def generate(
        self,
        context: ClosureContext,
    ) -> ClosureDecision:
        """
        Generate structured closure information.

        Parameters
        ----------
        context
            Structured ClosureContext.

        Returns
        -------
        ClosureDecision
            Validated AI-generated closure information.
        """

        start_time = time.perf_counter()

        logger.info("Closure Agent started.")

        try:

            decision = self.orchestrator.execute(
                task=AITask.CLOSURE,
                context=context.model_dump(),
                response_schema=ClosureDecision,
            )

            elapsed = time.perf_counter() - start_time

            logger.info(
                "Closure completed in %.2f sec | Follow-up=%s",
                elapsed,
                decision.follow_up_required,
            )

            return decision

        except Exception as exc:

            logger.exception(
                "Closure Agent failed."
            )

            raise RuntimeError(
                "Closure Agent failed while generating closure information."
            ) from exc