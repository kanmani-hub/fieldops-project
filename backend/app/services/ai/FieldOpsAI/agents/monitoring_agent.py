"""
monitoring_agent.py

Monitoring Agent for FieldOps Commander.

Responsibilities
----------------
- Evaluate active field service jobs.
- Assess operational risk.
- Recommend the next operational action.
- Return a validated MonitoringDecision.

The Monitoring Agent NEVER:
- Updates the database.
- Changes job status.
- Sends notifications.
- Dispatches technicians.
- Calls external services.

It only returns structured AI recommendations.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from app.services.ai.FieldOpsAI.runtime.orchestrator import AIOrchestrator,ai_orchestrator
from app.services.ai.FieldOpsAI.schemas.ai_task import AITask
from app.services.ai.FieldOpsAI.schemas.monitoring import MonitoringContext,MonitoringDecision

logger = logging.getLogger(__name__)


class MonitoringAgent:
    """
    AI agent responsible for monitoring active jobs
    and recommending operational actions.
    """

    def __init__(
        self,
        orchestrator: Optional[AIOrchestrator] = None,
    ) -> None:
        """
        Initialize the Monitoring Agent.
        """

        self.orchestrator = orchestrator or ai_orchestrator

    # ---------------------------------------------------------

    def monitor(
        self,
        context: MonitoringContext,
    ) -> MonitoringDecision:
        """
        Evaluate an active job.

        Parameters
        ----------
        context
            Monitoring context.

        Returns
        -------
        MonitoringDecision
            Validated AI recommendation.
        """

        start_time = time.perf_counter()

        logger.info("Monitoring Agent started.")

        try:

            decision = self.orchestrator.execute(
                task=AITask.MONITORING,
                context=context.model_dump(),
                response_schema=MonitoringDecision,
            )

            elapsed = time.perf_counter() - start_time

            logger.info(
                "Monitoring completed in %.2f sec | Job=%s | Action=%s | Risk=%s",
                elapsed,
                context.job.job_id,
                decision.action,
                decision.risk_level,
            )

            return decision

        except Exception as exc:

            logger.exception("Monitoring Agent failed.")

            raise RuntimeError(
                "Monitoring Agent failed while evaluating the active job."
            ) from exc