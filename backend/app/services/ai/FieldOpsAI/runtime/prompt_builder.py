"""
prompt_builder.py

Purpose
-------
Build the complete FieldOps AI system prompt by combining
multiple Markdown files.

Instead of maintaining one huge prompt inside Python,
the prompt is assembled from separate Markdown documents.

Benefits
--------
- Easy to maintain
- Easy to update
- Non-developers can edit prompts
- Clean separation of concerns
"""

from typing import List, Optional
from app.services.ai.FieldOpsAI.runtime.prompt_registry import PromptTemplateRegistry, get_default_prompt_registry
from app.services.ai.FieldOpsAI.schemas.ai_task import AITask

class PromptBuilder:
    """
    Responsible for assembling the complete
    FieldOps AI system prompt.
    """

    def __init__(self, registry: Optional[PromptTemplateRegistry] = None):
        """
        Initialize PromptBuilder.
        Uses the default PromptTemplateRegistry if none is provided.
        """
        self._registry = registry

    @property
    def registry(self) -> PromptTemplateRegistry:
        if self._registry is None:
            self._registry = get_default_prompt_registry()
        return self._registry

    def build(self) -> str:
        """
        Build the complete system prompt.

        The prompt is assembled in a fixed order based on the registry.
        
        Returns
        -------
        str
            Complete system prompt.
        """
        sections: List[str] = list(self.registry.get_system_instructions())
        return "\n\n".join(sections)

    def get_task_prompt(self, task: AITask) -> str:
        """
        Get the prompt for a specific AI task from the registry.
        """
        return self.registry.get_task_prompt(task)