"""
dispatch_integration.py

Integration layer between the existing
FieldOps dispatch workflow and the AI Dispatch Agent.

Responsibilities
----------------
- Receive dispatch events.
- Invoke the DispatchAgent.
- Return a validated DispatchDecision.

No database operations occur here.
"""

from typing import Dict

from app.services.ai.FieldOpsAI.agents.dispatch_agent import DispatchAgent
from app.services.ai.FieldOpsAI.schemas.dispatch import DispatchDecision


class DispatchIntegration:
    """
    Adapter between backend dispatch
    services and the AI Dispatch Agent.
    """

    def __init__(self):
        self.agent = DispatchAgent()

    def handle(
        self,
        job: Dict,
        technician: Dict,
        event: str,
    ) -> DispatchDecision:
        """
        Process a technician dispatch event.

        Parameters
        ----------
        job
            Job information.

        technician
            Technician information.

        event
            Dispatch event.

        Returns
        -------
        DispatchDecision
        """

        return self.agent.handle_event(
            job=job,
            technician=technician,
            event=event,
        )