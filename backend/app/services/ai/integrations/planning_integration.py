"""
planning_integration.py

Integration layer between the existing
FieldOps planning workflow and the AI Planning Agent.
"""

from __future__ import annotations

import asyncio
from typing import Dict, List

from app.services.ai.FieldOpsAI.agents.planning_agent import PlanningAgent
from app.services.ai.FieldOpsAI.schemas.planning import PlanningDecision, PlanningContext
from app.services.ai.FieldOpsAI.runtime.agent_pool import AgentPool
from app.services.ai.FieldOpsAI.runtime.lifecycle import AgentLifecycle
from app.services.ai.FieldOpsAI.schemas.agent_result import AgentResultStatus
from app.services.ai.FieldOpsAI.agents.base import TenantIsolationError, AgentLifecycleError, AgentState


class PlanningIntegration:
    """
    Adapter between backend planning
    services and the AI Planning Agent.
    """

    def __init__(self, agent: PlanningAgent | None = None):
        self.agent = agent

    async def recommend_async(
        self,
        customer_request: Dict,
        candidate_technicians: List[Dict],
        tenant_id: str,
    ) -> PlanningDecision:
        """
        Ask the AI to recommend the best technician from the candidate list asynchronously.
        """
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id must be a non-blank string.")

        if self.agent is not None:
            # Check if injected agent is already terminated
            if self.agent.state == AgentState.TERMINATED:
                raise AgentLifecycleError("The injected agent is already terminated.")

            # Explicit injected-agent design: verify tenant matches
            if self.agent.tenant_id != tenant_id:
                raise TenantIsolationError(
                    "The injected PlanningAgent does not belong to the requested tenant."
                )
            agent_to_use = self.agent
        else:
            # Operation-scoped design: fresh agent per call, not cached
            from app.services.ai.FieldOpsAI.config.agent_config_manager import AgentConfigManager
            from app.services.ai.FieldOpsAI.schemas.ai_task import AITask

            config = AgentConfigManager().resolve(
                agent_type=AITask.PLANNING,
                tenant_id=tenant_id,
            )
            agent_to_use = PlanningAgent(config=config)

        context = PlanningContext(
            customer_request=customer_request,
            available_technicians=candidate_technicians,
        )

        exec_context = context.model_dump(mode="json")
        exec_context["tenant_id"] = tenant_id

        pool = AgentPool()
        async with AgentLifecycle(agent=agent_to_use, pool=pool) as lifecycle:
            result = await lifecycle.execute(exec_context)
            if result.status != AgentResultStatus.SUCCESS:
                raise RuntimeError(
                    "Planning Agent failed while generating technician recommendations."
                )
            decision = result.output
            if not isinstance(decision, PlanningDecision):
                raise RuntimeError(
                    "Planning Agent returned an invalid decision."
                )
            return decision

    def recommend(
        self,
        customer_request: Dict,
        candidate_technicians: List[Dict],
        tenant_id: str,
    ) -> PlanningDecision:
        """
        Ask the AI to recommend the best technician from the candidate list synchronously.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError(
                "PlanningIntegration.recommend() cannot be called from an active "
                "event loop. Use await recommend_async(...) instead."
            )

        return asyncio.run(
            self.recommend_async(
                customer_request=customer_request,
                candidate_technicians=candidate_technicians,
                tenant_id=tenant_id,
            )
        )