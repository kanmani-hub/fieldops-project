"""
agent_config.py

Validated configuration contracts for FieldOps AI agents.

"""

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
)

from app.services.ai.FieldOpsAI.schemas.ai_task import AITask


class AgentConfig(BaseModel):
    """
    Common configuration supplied to every FieldOps AI agent.

    The configuration is immutable after validation. This prevents
    important values such as tenant_id and agent_type from being
    changed while an agent is running.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    agent_type: AITask = Field(
        description="Type of FieldOps AI agent.",
    )

    tenant_id: str = Field(
        min_length=1,
        max_length=100,
        description="Tenant that owns this agent instance.",
    )

    agent_version: str = Field(
        default="1.0",
        min_length=1,
        max_length=50,
        description="Version of the agent implementation.",
    )

    timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        le=300,
        description=(
            "Maximum execution time allowed for one agent run."
        ),
    )

    max_retries: int = Field(
        default=2,
        ge=0,
        le=10,
        description=(
            "Maximum retry attempts allowed for agent execution."
        ),
    )

    enabled: StrictBool = Field(
        default=True,
        description="Whether the agent is enabled for execution.",
    )