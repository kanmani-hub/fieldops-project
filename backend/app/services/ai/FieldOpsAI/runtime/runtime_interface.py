"""
runtime_interface.py

Defines the contract for any AI runtime implementation.

A runtime is responsible for orchestrating prompts,
tool execution, and interaction with the AI provider.

Examples:

- VellumRuntime
- OpenClawRuntime
"""

from abc import ABC, abstractmethod
from typing import Any, Dict
from app.services.ai.FieldOpsAI.schemas.ai_task import AITask


class RuntimeInterface(ABC):
    """
    Abstract interface for AI runtimes.
    """

    @abstractmethod
    def execute(
        self,
        task: AITask,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Execute an AI task.

        Parameters
        ----------
        task
            The type of task to execute
            (planning, dispatch, communication, etc.)

        context
            Structured input data for the task.

        Returns
        -------
        Dict[str, Any]

        Structured AI response.
        """
        raise NotImplementedError

    @abstractmethod
    def runtime_name(self) -> str:
        """
        Returns the runtime name.

        Example:

        Vellum

        OpenClaw
        """
        raise NotImplementedError

    @abstractmethod
    def health_check(self) -> bool:
        """
        Check whether the runtime is operational.
        """
        raise NotImplementedError