"""
dispatch_agent.py

Dispatch Agent for FieldOps Commander AI, migrated to inherit from BaseAgent.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

from app.services.ai.FieldOpsAI.agents.base import BaseAgent
from app.services.ai.FieldOpsAI.schemas.agent_config import AgentConfig
from app.services.ai.FieldOpsAI.schemas.ai_task import AITask
from app.services.ai.FieldOpsAI.schemas.dispatch import DispatchContext, DispatchDecision
from app.services.ai.FieldOpsAI.runtime.orchestrator import AIOrchestrator, ai_orchestrator

logger = logging.getLogger(__name__)


class DispatchAgent(BaseAgent[DispatchDecision]):
    """
    AI agent responsible for technician dispatch workflow decisions.
    """

    def __init__(
        self,
        config: AgentConfig,
        orchestrator: Optional[AIOrchestrator] = None,
    ) -> None:
        """
        Initialize the Dispatch Agent.
        """
        if config.agent_type != AITask.DISPATCH:
            raise ValueError(
                "DispatchAgent requires an AITask.DISPATCH configuration."
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
    ) -> DispatchDecision:
        """
        Execute the AI dispatch task.

        Offloads the synchronous AIOrchestrator.execute worker to a thread pool.
        Note that asyncio cancellation cannot forcibly stop the underlying worker thread,
        so the provider-level timeout remains the final hard bound.
        """
        start_time = time.perf_counter()

        logger.info("Dispatch Agent run started.")

        dispatch_context = DispatchContext.model_validate(context)

        # Offload synchronous execute to prevent blocking the event loop
        decision = await asyncio.to_thread(
            self.orchestrator.execute,
            task=AITask.DISPATCH,
            context=dispatch_context.model_dump(mode="json"),
            response_schema=DispatchDecision,
        )

        elapsed = time.perf_counter() - start_time

        logger.info(
            "Dispatch completed in %.2f sec | Job=%s | Technician=%s | Action=%s | Status=%s",
            elapsed,
            decision.job_id,
            decision.technician_id,
            decision.action,
            decision.status,
        )

        return decision

    def dispatch(
        self,
        context: DispatchContext,
    ) -> DispatchDecision:
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
                "dispatch() cannot be called from an active event loop. "
                "Use the asynchronous AgentLifecycle / execute path instead."
            )

        exec_context = context.model_dump(mode="json")
        exec_context["tenant_id"] = self.tenant_id

        async def _run_wrapped():
            if not self.is_setup:
                await self.setup()
            return await self.execute(exec_context)

        return asyncio.run(_run_wrapped())