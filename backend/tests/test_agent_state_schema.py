import pytest
from datetime import datetime, timezone, timedelta
from uuid import uuid4
from pydantic import ValidationError

from app.services.ai.FieldOpsAI.agents.base import AgentState
from app.services.ai.FieldOpsAI.schemas.ai_task import AITask
from app.services.ai.FieldOpsAI.schemas.agent_state import AgentStateSnapshot, _validate_metadata

# Line 137: nested dictionary key is not a string
def test_metadata_nested_non_string_key():
    with pytest.raises(ValueError, match="metadata key at 'outer' must be a string"):
        _validate_metadata({"outer": {123: "value"}})

# Line 171: top-level metadata key is not a string (defensive check)
def test_metadata_top_level_non_string_key():
    with pytest.raises(ValueError, match="metadata key must be a string"):
        _validate_metadata({123: "value"})  # type: ignore

# Line 282: blank tenant_id
def test_agent_state_blank_tenant_id():
    now = datetime.now(timezone.utc)
    with pytest.raises(ValidationError):
        AgentStateSnapshot(
            agent_id=uuid4(),
            agent_type=AITask.PLANNING,
            tenant_id="   ",
            agent_version="1.0",
            state=AgentState.IDLE,
            created_at=now,
            updated_at=now
        )
    with pytest.raises(ValueError, match="tenant_id must not be blank"):
        AgentStateSnapshot.tenant_id_not_blank("   ")

# Line 305: updated_at before created_at
def test_agent_state_updated_at_before_created_at():
    now = datetime.now(timezone.utc)
    earlier = now - timedelta(seconds=10)
    with pytest.raises(ValidationError, match="updated_at must not be earlier than created_at"):
        AgentStateSnapshot(
            agent_id=uuid4(),
            agent_type=AITask.PLANNING,
            tenant_id="tenant-1",
            agent_version="1.0",
            state=AgentState.IDLE,
            created_at=now,
            updated_at=earlier
        )
