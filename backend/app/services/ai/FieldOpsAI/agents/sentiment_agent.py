"""
sentiment_agent.py

Sentiment Analysis Agent for FieldOps Commander.

Responsibilities
----------------
- Analyze customer communication.
- Determine sentiment, emotion, and urgency.
- Detect whether human intervention is recommended.
- Return a validated SentimentDecision.

The Sentiment Agent NEVER:
- Generates customer replies.
- Updates the database.
- Changes job status.
- Assigns technicians.
- Sends notifications.

It only returns structured AI analysis.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from app.services.ai.FieldOpsAI.runtime.orchestrator import AIOrchestrator,ai_orchestrator
from app.services.ai.FieldOpsAI.schemas.ai_task import AITask
from app.services.ai.FieldOpsAI.schemas.sentiment import SentimentContext,SentimentDecision

logger = logging.getLogger(__name__)


class SentimentAgent:
    """
    AI agent responsible for customer sentiment analysis.
    """

    def __init__(
        self,
        orchestrator: Optional[AIOrchestrator] = None,
    ) -> None:
        """
        Initialize the Sentiment Agent.
        """

        self.orchestrator = orchestrator or ai_orchestrator

    # ---------------------------------------------------------

    def analyze(
        self,
        context: SentimentContext,
    ) -> SentimentDecision:
        """
        Analyze customer communication.

        Parameters
        ----------
        context
            Structured sentiment analysis context.

        Returns
        -------
        SentimentDecision
            Validated AI sentiment analysis.
        """

        start_time = time.perf_counter()

        logger.info("Sentiment Agent started.")

        try:

            decision = self.orchestrator.execute(
                task=AITask.SENTIMENT,
                context=context.model_dump(),
                response_schema=SentimentDecision,
            )

            elapsed = time.perf_counter() - start_time

            logger.info(
                "Sentiment completed in %.2f sec | Sentiment=%s | Urgency=%s",
                elapsed,
                decision.sentiment,
                decision.urgency,
            )

            return decision

        except Exception as exc:

            logger.exception("Sentiment Agent failed.")

            raise RuntimeError(
                "Sentiment Agent failed while analyzing customer communication."
            ) from exc