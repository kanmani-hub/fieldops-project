"""
ai_generator.py

AI Generator for FieldOps Commander.

Responsibilities
----------------
- Generate AI responses.
- Use the GroqClient.
- Hide AI provider details from higher layers.

The AI Generator NEVER:
- Falls back to templates.
- Updates the database.
- Contains business logic.
"""

from typing import Any, Dict

from app.services.ai.FieldOpsAI.runtime.orchestrator import AIOrchestrator


class AIGenerator:
    """
    High-level AI message generator.
    """

    def __init__(self):
        """
        Initialize the AI orchestrator.
        """

        self.orchestrator = AIOrchestrator()

    # ---------------------------------------------------------

    def generate(
        self,
        task: str,
        context: Dict[str, Any],
    ) -> str:
        """
        Generate AI output.

        Parameters
        ----------
        task
            AI task name.

            Examples

            planning

            communication

            sentiment

            closure

        context
            Task context.

        Returns
        -------
        str
            Raw AI response.
        """

        return self.orchestrator.execute(
            task=task,
            context=context,
        )