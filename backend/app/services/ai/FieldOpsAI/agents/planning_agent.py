"""
planning_agent.py

Planning Agent for FieldOps Commander AI, migrated to inherit from BaseAgent.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

from app.services.ai.FieldOpsAI.agents.base import BaseAgent
from app.services.ai.FieldOpsAI.schemas.agent_config import AgentConfig
from app.services.ai.FieldOpsAI.schemas.ai_task import AITask
from app.services.ai.FieldOpsAI.schemas.planning import PlanningContext, PlanningDecision
from app.services.ai.FieldOpsAI.runtime.orchestrator import AIOrchestrator, ai_orchestrator

logger = logging.getLogger(__name__)


class PlanningAgent(BaseAgent[PlanningDecision]):
    """
    AI agent responsible for technician assignment recommendations.
    """

    def __init__(
        self,
        config: AgentConfig,
        orchestrator: Optional[AIOrchestrator] = None,
    ) -> None:
        """
        Initialize the Planning Agent.
        """
        if config.agent_type != AITask.PLANNING:
            raise ValueError(
                "PlanningAgent requires an AITask.PLANNING configuration."
            )

        super().__init__(config)
        self.orchestrator = (
            ai_orchestrator
            if orchestrator is None
            else orchestrator
        )

    async def run(
        self,
        context: dict[str, Any],
    ) -> PlanningDecision:
        """
        Execute the AI planning task.

        Offloads the synchronous AIOrchestrator.execute worker to a thread pool.
        Note that asyncio cancellation cannot forcibly stop the underlying worker thread,
        so the provider-level timeout remains the final hard bound.
        """
        start_time = time.perf_counter()

        logger.info("Planning Agent run started.")

        planning_context = PlanningContext.model_validate(context)

        # Offload synchronous execute to prevent blocking the event loop
        decision = await asyncio.to_thread(
            self.orchestrator.execute,
            task=AITask.PLANNING,
            context=planning_context.model_dump(mode="json"),
            response_schema=PlanningDecision,
        )

        elapsed = time.perf_counter() - start_time

        if decision.recommended_technicians:
            top = decision.recommended_technicians[0]
            logger.info(
                "Planning completed in %.2f sec | Top Technician=%s | Confidence=%.2f",
                elapsed,
                top.technician_id,
                top.confidence,
            )
        else:
            logger.info(
                "Planning completed in %.2f sec | Action=%s",
                elapsed,
                decision.action,
            )

        return decision

    def plan(
        self,
        context: PlanningContext,
    ) -> PlanningDecision:
        """
        Compatibility adapter for legacy synchronous callers.

        This is a temporary compatibility wrapper. Runs setup automatically if needed.
        Do not call from an active event loop.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError(
                "plan() cannot be called from an active event loop. "
                "Use the asynchronous AgentLifecycle / execute path instead."
            )

        exec_context = context.model_dump(mode="json")
        exec_context["tenant_id"] = self.tenant_id

        async def _run_wrapped():
            if not self.is_setup:
                await self.setup()
            return await self.execute(exec_context)

        return asyncio.run(_run_wrapped())