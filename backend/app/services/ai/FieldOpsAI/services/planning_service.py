"""
planning_service.py

Planning Service for FieldOps Commander.
"""

from __future__ import annotations

import asyncio
from typing import List, Callable

from app.services.ai.FieldOpsAI.repositories.job_repository import JobRepository
from app.services.ai.FieldOpsAI.repositories.technician_repository import TechnicianRepository
from app.services.ai.FieldOpsAI.repositories.job_assignment_repository import JobAssignmentRepository

from app.services.ai.FieldOpsAI.agents.planning_agent import PlanningAgent
from app.services.ai.FieldOpsAI.schemas.agent_config import AgentConfig
from app.services.ai.FieldOpsAI.schemas.planning import PlanningContext, PlanningDecision
from app.services.ai.FieldOpsAI.config.agent_config_manager import AgentConfigManager
from app.services.ai.FieldOpsAI.schemas.ai_task import AITask
from app.services.ai.FieldOpsAI.runtime.lifecycle import AgentLifecycle
from app.services.ai.FieldOpsAI.runtime.agent_pool import AgentPool
from app.services.ai.FieldOpsAI.schemas.agent_result import AgentResultStatus
from app.services.ai.FieldOpsAI.runtime.orchestrator import AIOrchestrator


class PlanningService:
    """
    Service responsible for AI-assisted technician planning.
    """

    def __init__(
        self,
        db,
        config_manager: AgentConfigManager | None = None,
        pool: AgentPool | None = None,
        agent_factory: Callable[[AgentConfig, AIOrchestrator | None], PlanningAgent] | None = None,
        orchestrator: AIOrchestrator | None = None,
    ):
        self.db = db
        self.job_repository = JobRepository(db)
        self.technician_repository = TechnicianRepository(db)
        self.assignment_repository = JobAssignmentRepository(db)

        self._config_manager = config_manager
        self._pool = pool
        self._agent_factory = agent_factory
        self._orchestrator = orchestrator
        self.agent = None

    # ---------------------------------------------------------

    async def plan_async(
        self,
        job_id: int,
        rejected_technician_ids: List[int] | None = None,
    ) -> PlanningDecision:
        """
        Generate AI technician recommendations asynchronously.
        """
        if rejected_technician_ids is None:
            rejected_technician_ids = []

        # 1. Load Job
        job = self.job_repository.get_by_id(job_id)
        if job is None:
            raise ValueError(
                f"Job {job_id} was not found."
            )

        # 2. Determine the authoritative tenant
        tenant_id = job.tenant_id

        # 3. Load available technicians
        technicians = self.technician_repository.get_available(
            tenant_id=tenant_id
        )
        if not technicians:
            raise ValueError(
                "No available technicians found."
            )

        available_technicians = []
        for technician in technicians:
            available_technicians.append(
                self.technician_repository.to_ai_dict(
                    technician
                )
            )

        # 4. Build PlanningContext
        context = PlanningContext(
            job_id=job.id,
            customer_request={
                "customer_name": job.customer_name,
                "location": job.location,
                "priority": job.priority,
                "required_skill": job.required_skill,
            },
            available_technicians=available_technicians,
            rejected_technician_ids=rejected_technician_ids,
        )

        # 5. Resolve AgentConfig
        config_manager = (
            AgentConfigManager()
            if self._config_manager is None
            else self._config_manager
        )
        config = config_manager.resolve(
            agent_type=AITask.PLANNING,
            tenant_id=tenant_id,
        )

        # 6. Create PlanningAgent
        if self._agent_factory is not None:
            agent = self._agent_factory(config, self._orchestrator)
        else:
            agent = PlanningAgent(config=config, orchestrator=self._orchestrator)
        self.agent = agent

        exec_context = context.model_dump(mode="json")
        exec_context["tenant_id"] = tenant_id

        # 7. Execute exactly one AgentLifecycle
        pool = (
            AgentPool()
            if self._pool is None
            else self._pool
        )

        async with AgentLifecycle(agent=agent, pool=pool) as lifecycle:
            result = await lifecycle.execute(exec_context)
            # 8. Validate successful AgentResult
            if result.status != AgentResultStatus.SUCCESS:
                raise RuntimeError(
                    "Planning Agent failed while generating technician recommendations."
                )
            decision = result.output

            if not isinstance(decision, PlanningDecision):
                raise RuntimeError(
                    "Planning Agent returned an invalid decision."
                )

        # 9. Save recommendations once
        recommendations = []
        for technician in decision.recommended_technicians:
            recommendations.append(
                {
                    "technician_id": technician.technician_id,
                    "rank": technician.rank,
                }
            )

        self.assignment_repository.save_recommendations(
            job_id=job.id,
            recommendations=recommendations,
        )
        self.assignment_repository.save()

        # 10. Return PlanningDecision
        return decision

    def plan(
        self,
        job_id: int,
        rejected_technician_ids: List[int] | None = None,
    ) -> PlanningDecision:
        """
        Generate AI technician recommendations synchronously.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError(
                "PlanningService.plan() cannot be called from an active "
                "event loop. Use await plan_async(...) instead."
            )

        return asyncio.run(
            self.plan_async(
                job_id=job_id,
                rejected_technician_ids=rejected_technician_ids,
            )
        )