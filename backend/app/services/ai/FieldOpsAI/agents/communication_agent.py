"""
communication_agent.py

Communication Agent for FieldOps Commander AI.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

from app.services.ai.FieldOpsAI.agents.base import BaseAgent, AgentState, AgentLifecycleError
from app.services.ai.FieldOpsAI.schemas.agent_config import AgentConfig
from app.services.ai.FieldOpsAI.schemas.ai_task import AITask
from app.services.ai.FieldOpsAI.schemas.communication import CommunicationContext, CommunicationDecision
from app.services.ai.FieldOpsAI.runtime.orchestrator import AIOrchestrator, ai_orchestrator
from app.services.ai.FieldOpsAI.runtime.lifecycle import AgentLifecycle
from app.services.ai.FieldOpsAI.runtime.agent_pool import AgentPool
from app.services.ai.FieldOpsAI.schemas.agent_result import AgentResultStatus

logger = logging.getLogger(__name__)


class CommunicationAgent(BaseAgent[CommunicationDecision]):
    """
    AI agent responsible for generating customer-facing communication.
    """

    def __init__(
        self,
        config: AgentConfig,
        orchestrator: Optional[AIOrchestrator] = None,
    ) -> None:
        """
        Initialize the Communication Agent.
        """
        if config.agent_type != AITask.COMMUNICATION:
            raise ValueError(
                "CommunicationAgent requires an AITask.COMMUNICATION configuration."
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
    ) -> CommunicationDecision:
        """
        Execute the AI communication generation task.
        """
        start_time = time.perf_counter()

        # Copy context and remove tenant_id
        exec_context = context.copy()
        exec_context.pop("tenant_id", None)

        validated_context = CommunicationContext.model_validate(exec_context)

        # Offload synchronous AIOrchestrator execute to prevent blocking the event loop
        decision = await asyncio.to_thread(
            self.orchestrator.execute,
            task=AITask.COMMUNICATION,
            context=validated_context.model_dump(mode="json"),
            response_schema=CommunicationDecision,
        )

        if not isinstance(decision, CommunicationDecision):
            raise TypeError("Returned object is not a CommunicationDecision.")

        elapsed = time.perf_counter() - start_time

        logger.info(
            "Communication generation run completed.",
            extra={
                "agent_id": str(self.agent_id),
                "agent_type": self.config.agent_type.value,
                "channel": validated_context.channel,
                "notification_type": validated_context.notification_type,
                "elapsed": elapsed,
            }
        )

        return decision

    def generate(
        self,
        context: CommunicationContext,
    ) -> CommunicationDecision:
        """
        Synchronous compatibility adapter method.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError(
                "generate() cannot be called from an active event loop. "
                "Use the asynchronous AgentLifecycle / execute path instead."
            )

        if self.state is AgentState.TERMINATED:
            raise AgentLifecycleError(
                "A terminated agent cannot execute work."
            )

        exec_context = context.model_dump(mode="json")
        exec_context["tenant_id"] = self.tenant_id

        async def _run_wrapped() -> CommunicationDecision:
            pool = AgentPool()
            async with AgentLifecycle(agent=self, pool=pool) as lifecycle:
                result = await lifecycle.execute(exec_context)
                if result.status != AgentResultStatus.SUCCESS:
                    raise RuntimeError(
                        f"Communication agent execution failed with status: {result.status}"
                    )
                if not isinstance(result.output, CommunicationDecision):
                    raise TypeError(
                        "Communication agent returned an invalid output type."
                    )
                return result.output

        return asyncio.run(_run_wrapped())