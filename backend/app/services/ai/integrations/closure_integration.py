"""
closure_integration.py

Integration layer between the backend
job completion workflow and the AI Closure Agent.
"""

from typing import Dict

from app.services.ai.FieldOpsAI.agents.closure_agent import ClosureAgent
from app.services.ai.FieldOpsAI.schemas.closure import ClosureDecision


class ClosureIntegration:
    """
    Adapter for AI-generated closure summaries.
    """

    def __init__(self):
        self.agent = ClosureAgent()

    def generate(
        self,
        context: Dict,
    ) -> ClosureDecision:
        """
        Generate closure information.
        """

        return self.agent.generate(
            context=context,
        )