"""
closure_service.py

Closure Service for FieldOps Commander.

Responsibilities
----------------
- Generate work completion summaries.
- Generate customer completion messages.
- Generate dispatcher closure notes.
- Generate follow-up communication.

This service NEVER:
- Updates the database.
- Changes job status.
- Sends notifications.

It only generates AI-assisted closure content.
"""

from typing import Dict

from app.services.ai.FieldOpsAI.runtime.orchestrator import AIOrchestrator


class ClosureService:
    """
    AI-powered closure service.
    """

    def __init__(self):
        self.orchestrator = AIOrchestrator()

    # ---------------------------------------------------------

    def generate_work_summary(
        self,
        job: Dict,
        technician: Dict,
    ):
        """
        Generate technician work summary.
        """

        context = {
            "job": job,
            "technician": technician,
        }

        return self.orchestrator.execute(
            task="work_summary",
            context=context,
        )

    # ---------------------------------------------------------

    def generate_customer_closure(
        self,
        job: Dict,
        technician: Dict,
    ):
        """
        Generate customer completion message.
        """

        context = {
            "job": job,
            "technician": technician,
        }

        return self.orchestrator.execute(
            task="customer_closure",
            context=context,
        )

    # ---------------------------------------------------------

    def generate_dispatcher_notes(
        self,
        job: Dict,
        technician: Dict,
    ):
        """
        Generate dispatcher closure notes.
        """

        context = {
            "job": job,
            "technician": technician,
        }

        return self.orchestrator.execute(
            task="dispatcher_notes",
            context=context,
        )

    # ---------------------------------------------------------

    def generate_followup(
        self,
        job: Dict,
        technician: Dict,
    ):
        """
        Generate follow-up communication.
        """

        context = {
            "job": job,
            "technician": technician,
        }

        return self.orchestrator.execute(
            task="followup",
            context=context,
        )