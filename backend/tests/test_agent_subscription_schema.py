import pytest
from datetime import datetime, timezone
from uuid import uuid4
from pydantic import ValidationError

from app.services.ai.FieldOpsAI.schemas.agent_subscription import AgentSubscription

# Line 95: non-string tenant_id
def test_subscription_non_string_tenant_id():
    with pytest.raises(ValidationError, match="tenant_id must be a string"):
        AgentSubscription(
            subscription_id=uuid4(),
            tenant_id=123,  # type: ignore
            topic="agent.test",
            created_at=datetime.now(timezone.utc)
        )

# Line 98: blank tenant_id
def test_subscription_blank_tenant_id():
    with pytest.raises(ValidationError, match="tenant_id must not be blank"):
        AgentSubscription(
            subscription_id=uuid4(),
            tenant_id="   ",
            topic="agent.test",
            created_at=datetime.now(timezone.utc)
        )

# Line 116: naive created_at datetime
def test_subscription_naive_created_at():
    with pytest.raises(ValidationError, match="created_at must be timezone-aware"):
        AgentSubscription(
            subscription_id=uuid4(),
            tenant_id="tenant-1",
            topic="agent.test",
            created_at=datetime.now()  # naive datetime
        )
