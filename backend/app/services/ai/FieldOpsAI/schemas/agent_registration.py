"""
agent_registration.py

Immutable registration record for a FieldOps AI agent definition.

Story 1.3 — Agent Registry.

An AgentRegistration describes what an agent type is and how it is
instantiated.  It stores definition metadata only.

What this schema does NOT store
--------------------------------
- Live agent instances
- Tenant context
- Database sessions
- API keys or secrets
- Prompts or provider responses
- Customer or technician data
"""

from __future__ import annotations

import dataclasses
import inspect
from typing import Any, TYPE_CHECKING

from app.services.ai.FieldOpsAI.schemas.ai_task import AITask

if TYPE_CHECKING:
    from app.services.ai.FieldOpsAI.agents.base import BaseAgent


@dataclasses.dataclass(frozen=True)
class AgentRegistration:
    """
    Immutable record describing one registered agent type.

    Uses a frozen dataclass because Pydantic v2 cannot safely
    validate class-typed fields (``type[BaseAgent]``) without
    resorting to ``arbitrary_types_allowed`` which makes field
    validation opaque and non-portable.

    Attributes
    ----------
    agent_type:
        AITask value that uniquely identifies this registration.
    agent_class:
        Concrete BaseAgent subclass to instantiate.  BaseAgent itself
        and any abstract subclass are rejected.
    version:
        Non-blank version string for the agent implementation.
    enabled:
        When False, the registration is discoverable but cannot be
        used to create agents.
    description:
        Optional human-readable description of the agent's purpose.
        Must be a str or None.

    Rules
    -----
    - ``agent_class`` must be a concrete subclass of ``BaseAgent``.
      BaseAgent itself and abstract subclasses are rejected.
    - ``version`` must be a non-blank string.
    - ``description`` must be a str or None.
    - No live instances, sessions, secrets, or prompts may be stored.
    """

    agent_type: AITask
    agent_class: "type[BaseAgent[Any]]"
    version: str
    enabled: bool = True
    description: str | None = None

    def __post_init__(self) -> None:
        """
        Validate fields after dataclass construction.

        Raises
        ------
        TypeError
            When agent_type is not an AITask, agent_class is not a class,
            version is not a string, enabled is not a bool, or description
            is not a str or None.
        ValueError
            When agent_class is BaseAgent itself, when agent_class is not
            a BaseAgent subclass, when agent_class is abstract, or when
            version is blank.
        """

        # Import here to avoid circular imports at module load time.
        from app.services.ai.FieldOpsAI.agents.base import BaseAgent as _BaseAgent

        if not isinstance(self.agent_type, AITask):
            raise TypeError(
                "agent_type must be an AITask member, "
                f"got {type(self.agent_type).__name__!r}."
            )

        if not isinstance(self.agent_class, type):
            raise TypeError(
                "agent_class must be a class, "
                f"got {type(self.agent_class).__name__!r}."
            )

        if self.agent_class is _BaseAgent:
            raise ValueError(
                "agent_class must not be BaseAgent itself because it "
                "is abstract and cannot be instantiated."
            )

        if not issubclass(self.agent_class, _BaseAgent):
            raise ValueError(
                f"agent_class must be a BaseAgent subclass, "
                f"got {self.agent_class.__name__!r}."
            )

        if inspect.isabstract(self.agent_class):
            raise ValueError(
                f"agent_class must be a concrete (non-abstract) BaseAgent "
                f"subclass; {self.agent_class.__name__!r} has unimplemented "
                "abstract methods."
            )

        if not isinstance(self.version, str):
            raise TypeError(
                "version must be a string, "
                f"got {type(self.version).__name__!r}."
            )

        if not self.version.strip():
            raise ValueError("version must not be blank.")

        if not isinstance(self.enabled, bool):
            raise TypeError(
                "enabled must be a bool, "
                f"got {type(self.enabled).__name__!r}."
            )

        if self.description is not None and not isinstance(self.description, str):
            raise TypeError(
                "description must be a str or None, "
                f"got {type(self.description).__name__!r}."
            )
