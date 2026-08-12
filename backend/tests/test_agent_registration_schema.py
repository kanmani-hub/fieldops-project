import pytest
from app.services.ai.FieldOpsAI.schemas.ai_task import AITask
from app.services.ai.FieldOpsAI.schemas.agent_registration import AgentRegistration
from app.services.ai.FieldOpsAI.agents.base import BaseAgent
from typing import Any

class MockAgent(BaseAgent[dict[str, Any]]):
    async def run(self, context: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True}

# Line 94: agent_type is not an AITask
def test_registration_invalid_agent_type():
    with pytest.raises(TypeError, match="agent_type must be an AITask member"):
        AgentRegistration(
            agent_type="planning",  # type: ignore
            agent_class=MockAgent,
            version="1.0"
        )

# Line 134: enabled is not a bool
def test_registration_invalid_enabled_type():
    with pytest.raises(TypeError, match="enabled must be a bool"):
        AgentRegistration(
            agent_type=AITask.PLANNING,
            agent_class=MockAgent,
            version="1.0",
            enabled="yes"  # type: ignore
        )
