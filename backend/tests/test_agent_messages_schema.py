import pytest
from pydantic import ValidationError
from app.context import correlation_id_ctx
from app.services.ai.FieldOpsAI.schemas.agent_messages import (
    AgentAddress,
    MessageEnvelope,
    MessageType,
    ErrorMessage,
    _validate_topic,
)

@pytest.fixture(autouse=True)
def isolate_correlation_id_context():
    """
    Ensure every test starts with an empty correlation context
    and restores the previous context when it finishes.
    """

    token = correlation_id_ctx.set("")

    try:
        yield

    finally:
        correlation_id_ctx.reset(token)

# Line 101->105: correlation_id context set
def test_resolve_default_correlation_id_from_context() -> None:
    """
    Correlation ID is loaded and normalized from the
    current request context.
    """

    token = correlation_id_ctx.set(
        "   ctx-correlation-id   "
    )

    try:
        sender = AgentAddress(
            agent_type="planning",
            agent_id="planner",
            tenant_id="tenant-1",
        )

        envelope = MessageEnvelope(
            sender=sender,
            message_type=MessageType.EVENT,
            topic="agent.test",
        )

        assert (
            envelope.correlation_id
            == "ctx-correlation-id"
        )

    finally:
        correlation_id_ctx.reset(token)
# Line 119: blank topic
def test_validate_topic_blank():
    with pytest.raises(ValueError, match="topic must not be blank"):
        _validate_topic("   ")

# Line 161->exit: finite float check
def test_validate_json_finite_float():
    sender = AgentAddress(agent_type="planning", agent_id="planner", tenant_id="tenant-1")
    env = MessageEnvelope(
        sender=sender,
        message_type=MessageType.EVENT,
        topic="agent.test",
        payload={"value": 12.34}
    )
    assert env.payload["value"] == 12.34

# Line 285: blank AgentAddress fields
def test_address_fields_blank():
    with pytest.raises(ValidationError):
        AgentAddress(agent_type="", agent_id="planner", tenant_id="tenant-1")
    with pytest.raises(ValueError, match="Address fields cannot be empty"):
        AgentAddress.validate_not_empty("")

# Line 315-316: task property of AgentAddress
def test_address_task_property():
    addr = AgentAddress(agent_type="planning", agent_id="planner", tenant_id="tenant-1")
    from app.services.ai.FieldOpsAI.schemas.ai_task import AITask
    assert addr.task == AITask.PLANNING

# Line 474: blank contract_version
def test_contract_version_blank():
    sender = AgentAddress(agent_type="planning", agent_id="planner", tenant_id="tenant-1")
    with pytest.raises(ValidationError, match="contract_version must not be blank"):
        MessageEnvelope(
            sender=sender,
            message_type=MessageType.EVENT,
            topic="agent.test",
            contract_version="   "
        )

# Line 494: negative timeout_seconds
def test_timeout_seconds_negative():
    sender = AgentAddress(agent_type="planning", agent_id="planner", tenant_id="tenant-1")
    with pytest.raises(ValidationError, match="timeout_seconds must be greater than zero"):
        MessageEnvelope(
            sender=sender,
            message_type=MessageType.EVENT,
            topic="agent.test",
            timeout_seconds=-1.0
        )

# Line 499: valid float conversion for timeout
def test_timeout_seconds_valid_float():
    sender = AgentAddress(agent_type="planning", agent_id="planner", tenant_id="tenant-1")
    env = MessageEnvelope(
        sender=sender,
        message_type=MessageType.EVENT,
        topic="agent.test",
        timeout_seconds=5
    )
    assert env.timeout_seconds == 5.0

# Line 516: None payload
def test_envelope_payload_none():
    sender = AgentAddress(agent_type="planning", agent_id="planner", tenant_id="tenant-1")
    env = MessageEnvelope(
        sender=sender,
        message_type=MessageType.EVENT,
        topic="agent.test",
        payload=None  # type: ignore
    )
    assert env.payload == {}

# Line 530: None metadata
def test_envelope_metadata_none():
    sender = AgentAddress(agent_type="planning", agent_id="planner", tenant_id="tenant-1")
    env = MessageEnvelope(
        sender=sender,
        message_type=MessageType.EVENT,
        topic="agent.test",
        metadata=None  # type: ignore
    )
    assert env.metadata == {}

# Line 805: ErrorMessage details=None
def test_error_message_details_none():
    sender = AgentAddress(agent_type="planning", agent_id="planner", tenant_id="tenant-1")
    err = ErrorMessage(
        sender=sender,
        error_code="TEST_ERROR",
        error_message="Test message",
        details=None  # type: ignore
    )
    assert err.details == {}
