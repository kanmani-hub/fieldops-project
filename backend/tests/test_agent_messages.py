"""
test_agent_messages.py

Unit tests for AI agent communication schemas and message envelopes.
"""

from __future__ import annotations

import pytest
from datetime import datetime, UTC
from uuid import UUID, uuid4
from typing import Any

from app.context import correlation_id_ctx
from app.services.ai.FieldOpsAI.schemas.agent_messages import (
    AgentAddress,
    BaseMessage,
    CommandMessage,
    ErrorMessage,
    EventMessage,
    MessageType,
    QueryMessage,
    ResponseMessage,
    MessageEnvelope,
    PublishResult,
    DeliveryFailure,
)


# ==========================================================
# Existing Compatibility (Tests 1-9)
# ==========================================================


def test_agent_address_creation():
    """
    Test 1: Existing AgentAddress construction.
    """
    address = AgentAddress(
        agent_type="planning",
        agent_id="planner-01",
        tenant_id="tenant-001",
    )
    assert address.agent_type == "planning"
    assert address.agent_id == "planner-01"
    assert address.tenant_id == "tenant-001"


def test_canonical_address_string():
    """
    Test 2: Existing canonical address string.
    """
    address = AgentAddress(
        agent_type="planning",
        agent_id="planner-01",
        tenant_id="tenant-001",
    )
    assert str(address) == "planning:planner-01:tenant-001"
    assert address.address == "planning:planner-01:tenant-001"


def test_command_message_construction():
    """
    Test 3: Existing CommandMessage construction.
    """
    sender = AgentAddress(
        agent_type="planning",
        agent_id="planner-01",
        tenant_id="tenant-001",
    )
    recipient = AgentAddress(
        agent_type="dispatch",
        agent_id="dispatcher-01",
        tenant_id="tenant-001",
    )
    message = CommandMessage(
        sender=sender,
        recipient=recipient,
        payload={
            "job_id": "JOB-100",
            "technician": "TECH-101",
        },
    )
    assert message.message_type == MessageType.COMMAND
    assert message.topic == "agent.command"


def test_query_message_construction():
    """
    Test 4: Existing QueryMessage construction.
    """
    sender = AgentAddress(
        agent_type="monitoring",
        agent_id="monitor-01",
        tenant_id="tenant-001",
    )
    recipient = AgentAddress(
        agent_type="planning",
        agent_id="planner-01",
        tenant_id="tenant-001",
    )
    message = QueryMessage(
        sender=sender,
        recipient=recipient,
        payload={"job_id": "JOB-22"},
    )
    assert message.message_type == MessageType.QUERY
    assert message.topic == "agent.query"


def test_event_message_construction():
    """
    Test 5: Existing EventMessage construction.
    """
    sender = AgentAddress(
        agent_type="dispatch",
        agent_id="dispatcher-01",
        tenant_id="tenant-001",
    )
    recipient = AgentAddress(
        agent_type="monitoring",
        agent_id="monitor-01",
        tenant_id="tenant-001",
    )
    message = EventMessage(
        sender=sender,
        recipient=recipient,
        payload={"status": "ACCEPTED"},
    )
    assert message.message_type == MessageType.EVENT
    assert message.topic == "agent.event"


def test_response_message_construction():
    """
    Test 6: Existing ResponseMessage construction.
    """
    sender = AgentAddress(
        agent_type="planning",
        agent_id="planner-01",
        tenant_id="tenant-001",
    )
    recipient = AgentAddress(
        agent_type="dispatch",
        agent_id="dispatcher-01",
        tenant_id="tenant-001",
    )
    message = ResponseMessage(
        sender=sender,
        recipient=recipient,
        payload={"recommended": "TECH-101"},
    )
    assert message.message_type == MessageType.RESPONSE
    assert message.success is True
    assert message.topic == "agent.response"


def test_error_message_construction():
    """
    Test 7: Existing ErrorMessage construction.
    """
    sender = AgentAddress(
        agent_type="dispatch",
        agent_id="dispatcher-01",
        tenant_id="tenant-001",
    )
    recipient = AgentAddress(
        agent_type="planning",
        agent_id="planner-01",
        tenant_id="tenant-001",
    )
    message = ErrorMessage(
        sender=sender,
        recipient=recipient,
        error_code="TECH_NOT_FOUND",
        error_message="Technician unavailable",
    )
    assert message.message_type == MessageType.ERROR
    assert message.success is False
    assert message.topic == "agent.error"


def test_json_serialization():
    """
    Test 8: Existing JSON serialization.
    """
    sender = AgentAddress(
        agent_type="planning",
        agent_id="planner-01",
        tenant_id="tenant-001",
    )
    recipient = AgentAddress(
        agent_type="dispatch",
        agent_id="dispatcher-01",
        tenant_id="tenant-001",
    )
    message = CommandMessage(
        sender=sender,
        recipient=recipient,
        payload={"job_id": "JOB-500"},
    )
    json_data = message.to_json()
    assert "COMMAND" in json_data
    assert "JOB-500" in json_data


def test_dict_serialization():
    """
    Test 9: Existing dictionary round trip.
    """
    sender = AgentAddress(
        agent_type="planning",
        agent_id="planner-01",
        tenant_id="tenant-001",
    )
    recipient = AgentAddress(
        agent_type="dispatch",
        agent_id="dispatcher-01",
        tenant_id="tenant-001",
    )
    message = CommandMessage(
        sender=sender,
        recipient=recipient,
        payload={"job_id": "JOB-999"},
    )
    data = message.to_dict()
    restored = CommandMessage.from_dict(data)
    assert restored.payload["job_id"] == "JOB-999"


# ==========================================================
# Extended Message Contract (Tests 10-41)
# ==========================================================


def test_valid_broadcast_envelope():
    """
    Test 10: Valid broadcast envelope (recipient is None).
    """
    sender = AgentAddress(
        agent_type="planning",
        agent_id="planner-01",
        tenant_id="tenant-001",
    )
    message = MessageEnvelope(
        sender=sender,
        recipient=None,
        message_type=MessageType.EVENT,
        payload={"status": "BROADCAST"},
    )
    assert message.recipient is None


def test_valid_targeted_envelope():
    """
    Test 11: Valid targeted envelope (recipient is set).
    """
    sender = AgentAddress(
        agent_type="planning",
        agent_id="planner-01",
        tenant_id="tenant-001",
    )
    recipient = AgentAddress(
        agent_type="dispatch",
        agent_id="dispatcher-01",
        tenant_id="tenant-001",
    )
    message = MessageEnvelope(
        sender=sender,
        recipient=recipient,
        message_type=MessageType.COMMAND,
        payload={"job_id": "JOB-100"},
    )
    assert message.recipient == recipient


def test_uuid4_message_id():
    """
    Test 12: UUID4 message ID.
    """
    sender = AgentAddress(
        agent_type="planning",
        agent_id="planner-01",
        tenant_id="tenant-001",
    )
    message = MessageEnvelope(
        sender=sender,
        message_type=MessageType.EVENT,
    )
    assert isinstance(message.message_id, UUID)


def test_sender_tenant_property():
    """
    Test 13: Sender tenant property.
    """
    sender = AgentAddress(
        agent_type="planning",
        agent_id="planner-01",
        tenant_id="tenant-001",
    )
    message = MessageEnvelope(
        sender=sender,
        message_type=MessageType.EVENT,
    )
    assert message.tenant_id == "tenant-001"


def test_created_at_alias():
    """
    Test 14: created_at alias.
    """
    sender = AgentAddress(
        agent_type="planning",
        agent_id="planner-01",
        tenant_id="tenant-001",
    )
    message = MessageEnvelope(
        sender=sender,
        message_type=MessageType.EVENT,
    )
    assert message.created_at == message.timestamp


def test_correlation_id_supplied():
    """
    Test 15: Correlation ID supplied.
    """
    sender = AgentAddress(
        agent_type="planning",
        agent_id="planner-01",
        tenant_id="tenant-001",
    )
    message = MessageEnvelope(
        sender=sender,
        message_type=MessageType.EVENT,
        correlation_id="supplied-id-123",
    )
    assert message.correlation_id == "supplied-id-123"


def test_correlation_id_from_context():
    """
    Test 16: Correlation ID from context.
    """
    sender = AgentAddress(
        agent_type="planning",
        agent_id="planner-01",
        tenant_id="tenant-001",
    )
    token = correlation_id_ctx.set("context-id-456")
    try:
        message = MessageEnvelope(
            sender=sender,
            message_type=MessageType.EVENT,
        )
        assert message.correlation_id == "context-id-456"
    finally:
        correlation_id_ctx.reset(token)


def test_generated_correlation_fallback():
    """
    Test 17: Generated correlation fallback.
    """
    sender = AgentAddress(
        agent_type="planning",
        agent_id="planner-01",
        tenant_id="tenant-001",
    )
    token = correlation_id_ctx.set("")
    try:
        message = MessageEnvelope(
            sender=sender,
            message_type=MessageType.EVENT,
        )
        assert len(message.correlation_id) > 0
        UUID(message.correlation_id) # should be valid UUID string
    finally:
        correlation_id_ctx.reset(token)


def test_agent_type_normalized():
    """
    Test 18: Agent type normalized to lowercase.
    """
    address = AgentAddress(
        agent_type="  PLANNING  ",
        agent_id="planner-01",
        tenant_id="tenant-001",
    )
    assert address.agent_type == "planning"


def test_unknown_agent_type_rejected():
    """
    Test 19: Unknown agent type rejected.
    """
    with pytest.raises(ValueError, match="not a known AITask value"):
        AgentAddress(
            agent_type="invalid_type",
            agent_id="planner-01",
            tenant_id="tenant-001",
        )


def test_blank_address_fields_rejected():
    """
    Test 20: Blank address fields rejected.
    """
    with pytest.raises(ValueError, match="string_too_short|Address fields cannot be empty"):
        AgentAddress(
            agent_type="planning",
            agent_id="   ",
            tenant_id="tenant-001",
        )


def test_address_containing_colon_rejected():
    """
    Test 21: Address containing colon rejected.
    """
    with pytest.raises(ValueError, match="cannot contain ':'"):
        AgentAddress(
            agent_type="planning",
            agent_id="planner:01",
            tenant_id="tenant-001",
        )


def test_cross_tenant_recipient_rejected():
    """
    Test 22: Cross-tenant recipient rejected.
    """
    sender = AgentAddress(
        agent_type="planning",
        agent_id="planner-01",
        tenant_id="tenant-001",
    )
    recipient = AgentAddress(
        agent_type="dispatch",
        agent_id="dispatcher-01",
        tenant_id="tenant-002",
    )
    with pytest.raises(ValueError, match="Cross-tenant messages are not allowed"):
        MessageEnvelope(
            sender=sender,
            recipient=recipient,
            message_type=MessageType.EVENT,
        )


def test_topic_normalized():
    """
    Test 23: Topic normalized (stripped and lowercase).
    """
    sender = AgentAddress(
        agent_type="planning",
        agent_id="planner-01",
        tenant_id="tenant-001",
    )
    message = MessageEnvelope(
        sender=sender,
        message_type=MessageType.EVENT,
        topic="  AGENT.My_Topic-123  ",
    )
    assert message.topic == "agent.my_topic-123"


def test_invalid_topic_rejected():
    """
    Test 24: Invalid topic rejected.
    """
    sender = AgentAddress(
        agent_type="planning",
        agent_id="planner-01",
        tenant_id="tenant-001",
    )
    with pytest.raises(ValueError, match="contains invalid characters"):
        MessageEnvelope(
            sender=sender,
            message_type=MessageType.EVENT,
            topic="agent/topic",
        )


def test_naive_timestamp_rejected():
    """
    Test 25: Naive timestamp rejected.
    """
    sender = AgentAddress(
        agent_type="planning",
        agent_id="planner-01",
        tenant_id="tenant-001",
    )
    with pytest.raises(ValueError, match="timestamp must be timezone-aware"):
        MessageEnvelope(
            sender=sender,
            message_type=MessageType.EVENT,
            timestamp=datetime.now(), # naive
        )


def test_blank_correlation_id_rejected():
    """
    Test 26: Blank correlation ID rejected.
    """
    sender = AgentAddress(
        agent_type="planning",
        agent_id="planner-01",
        tenant_id="tenant-001",
    )
    with pytest.raises(ValueError, match="correlation_id must not be blank"):
        MessageEnvelope(
            sender=sender,
            message_type=MessageType.EVENT,
            correlation_id="   ",
        )


def test_payload_and_metadata_defaults_not_shared():
    """
    Test 27: Payload and metadata defaults not shared.
    """
    sender = AgentAddress(
        agent_type="planning",
        agent_id="planner-01",
        tenant_id="tenant-001",
    )
    msg1 = MessageEnvelope(sender=sender, message_type=MessageType.EVENT)
    msg2 = MessageEnvelope(sender=sender, message_type=MessageType.EVENT)
    assert msg1.payload is not msg2.payload
    assert msg1.metadata is not msg2.metadata


def test_payload_deep_copied():
    """
    Test 28: Payload deep copied.
    """
    sender = AgentAddress(
        agent_type="planning",
        agent_id="planner-01",
        tenant_id="tenant-001",
    )
    inner = {"x": 1}
    payload = {"inner": inner}
    msg = MessageEnvelope(
        sender=sender,
        message_type=MessageType.EVENT,
        payload=payload,
    )
    assert msg.payload["inner"] == inner
    assert msg.payload["inner"] is not inner


def test_metadata_deep_copied():
    """
    Test 29: Metadata deep copied.
    """
    sender = AgentAddress(
        agent_type="planning",
        agent_id="planner-01",
        tenant_id="tenant-001",
    )
    inner = {"a": "b"}
    metadata = {"inner": inner}
    msg = MessageEnvelope(
        sender=sender,
        message_type=MessageType.EVENT,
        metadata=metadata,
    )
    assert msg.metadata["inner"] == inner
    assert msg.metadata["inner"] is not inner


def test_non_json_payload_rejected():
    """
    Test 30: Non-JSON payload rejected.
    """
    sender = AgentAddress(
        agent_type="planning",
        agent_id="planner-01",
        tenant_id="tenant-001",
    )
    with pytest.raises(ValueError, match="must be JSON-compatible"):
        MessageEnvelope(
            sender=sender,
            message_type=MessageType.EVENT,
            payload={"object": object()},
        )


def test_nested_non_json_metadata_rejected():
    """
    Test 31: Nested non-JSON metadata rejected.
    """
    sender = AgentAddress(
        agent_type="planning",
        agent_id="planner-01",
        tenant_id="tenant-001",
    )
    with pytest.raises(ValueError, match="must be JSON-compatible"):
        MessageEnvelope(
            sender=sender,
            message_type=MessageType.EVENT,
            metadata={"items": [1, 2, object()]},
        )


def test_sensitive_payload_key_rejected():
    """
    Test 32: Sensitive payload key rejected.
    """
    sender = AgentAddress(
        agent_type="planning",
        agent_id="planner-01",
        tenant_id="tenant-001",
    )
    with pytest.raises(ValueError, match="Forbidden sensitive key"):
        MessageEnvelope(
            sender=sender,
            message_type=MessageType.EVENT,
            payload={"API_KEY": "12345"},
        )


def test_nested_sensitive_metadata_key_rejected():
    """
    Test 33: Nested sensitive metadata key rejected.
    """
    sender = AgentAddress(
        agent_type="planning",
        agent_id="planner-01",
        tenant_id="tenant-001",
    )
    with pytest.raises(ValueError, match="Forbidden sensitive key 'password'"):
        MessageEnvelope(
            sender=sender,
            message_type=MessageType.EVENT,
            metadata={"nested": {"user": "admin", "password": "pwd"}},
        )


def test_errormessage_details_privacy_validation():
    """
    Test 34: ErrorMessage details privacy validation.
    """
    sender = AgentAddress(
        agent_type="planning",
        agent_id="planner-01",
        tenant_id="tenant-001",
    )
    with pytest.raises(ValueError, match="Forbidden sensitive key 'token'"):
        ErrorMessage(
            sender=sender,
            error_code="FAIL",
            error_message="failed",
            details={"auth": {"token": "secret"}},
        )


def test_extra_fields_rejected():
    """
    Test 35: Extra fields rejected.
    """
    sender = AgentAddress(
        agent_type="planning",
        agent_id="planner-01",
        tenant_id="tenant-001",
    )
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        MessageEnvelope(
            sender=sender,
            message_type=MessageType.EVENT,
            extra_field="rejected",  # type: ignore
        )


def test_timeout_bool_rejected():
    """
    Test 36: Timeout bool rejected.
    """
    sender = AgentAddress(
        agent_type="planning",
        agent_id="planner-01",
        tenant_id="tenant-001",
    )
    with pytest.raises(ValueError, match="must be a numeric value, not bool"):
        MessageEnvelope(
            sender=sender,
            message_type=MessageType.EVENT,
            timeout_seconds=True,  # type: ignore
        )


def test_timeout_above_30_rejected():
    """
    Test 37: Timeout above 30 rejected.
    """
    sender = AgentAddress(
        agent_type="planning",
        agent_id="planner-01",
        tenant_id="tenant-001",
    )
    with pytest.raises(ValueError, match="exceeds maximum of 30 seconds"):
        MessageEnvelope(
            sender=sender,
            message_type=MessageType.EVENT,
            timeout_seconds=30.1,
        )


def test_existing_specialized_default_topics():
    """
    Test 38: Existing specialized default topics.
    """
    sender = AgentAddress(
        agent_type="planning",
        agent_id="planner-01",
        tenant_id="tenant-001",
    )
    assert CommandMessage(sender=sender).topic == "agent.command"
    assert QueryMessage(sender=sender).topic == "agent.query"
    assert EventMessage(sender=sender).topic == "agent.event"
    assert ResponseMessage(sender=sender).topic == "agent.response"
    assert ErrorMessage(sender=sender, error_code="E", error_message="msg").topic == "agent.error"


def test_custom_specialized_topic_accepted():
    """
    Test 39: Custom specialized topic accepted.
    """
    sender = AgentAddress(
        agent_type="planning",
        agent_id="planner-01",
        tenant_id="tenant-001",
    )
    cmd = CommandMessage(sender=sender, topic="custom.command")
    assert cmd.topic == "custom.command"


def test_publish_result_count_consistency():
    """
    Test 40: PublishResult count consistency.
    """
    with pytest.raises(ValueError, match="must equal matched_subscribers"):
        PublishResult(
            message_id=uuid4(),
            matched_subscribers=5,
            delivered=3,
            failed=1,
        )


def test_delivery_failure_excludes_payload_and_exception():
    """
    Test 41: DeliveryFailure excludes payload and raw exception fields.
    """
    sub_id = uuid4()
    failure = DeliveryFailure(
        subscription_id=sub_id,
        subscriber=None,
        error_code="HANDLER_FAILED",
        safe_message="Failed cleanly",
    )
    assert failure.subscription_id == sub_id
    assert failure.error_code == "HANDLER_FAILED"
    assert failure.safe_message == "Failed cleanly"


def test_invalid_types_rejected():
    """
    Test 41b: Validation edge cases for coverage.
    """
    with pytest.raises(ValueError, match="agent_type must be a string"):
        AgentAddress(agent_type=123, agent_id="a", tenant_id="t")  # type: ignore

    with pytest.raises(ValueError, match="Address fields must be strings"):
        AgentAddress(agent_type="planning", agent_id=123, tenant_id="t")  # type: ignore

    sender = AgentAddress(agent_type="planning", agent_id="planner-01", tenant_id="tenant-001")

    with pytest.raises(ValueError, match="correlation_id must be a string"):
        MessageEnvelope(sender=sender, message_type=MessageType.EVENT, correlation_id=123)  # type: ignore

    with pytest.raises(ValueError, match="contract_version must be a string"):
        MessageEnvelope(sender=sender, message_type=MessageType.EVENT, contract_version=123)  # type: ignore

    with pytest.raises((TypeError, ValueError), match="topic must be a string"):
        MessageEnvelope(sender=sender, message_type=MessageType.EVENT, topic=123)  # type: ignore

    with pytest.raises(ValueError, match="payload must be a dictionary"):
        MessageEnvelope(sender=sender, message_type=MessageType.EVENT, payload=123)  # type: ignore

    with pytest.raises(ValueError, match="metadata must be a dictionary"):
        MessageEnvelope(sender=sender, message_type=MessageType.EVENT, metadata=123)  # type: ignore

    with pytest.raises(ValueError, match="timeout_seconds must be a number"):
        MessageEnvelope(sender=sender, message_type=MessageType.EVENT, timeout_seconds="invalid")  # type: ignore

    with pytest.raises(ValueError, match="details must be a dictionary"):
        ErrorMessage(sender=sender, error_code="E", error_message="M", details="invalid")  # type: ignore

    # Non-string keys in dict
    with pytest.raises(ValueError, match="keys must be strings"):
        MessageEnvelope(sender=sender, message_type=MessageType.EVENT, payload={123: "val"})  # type: ignore


def test_whitespace_correlation_context_uuid_fallback():
    """
    Test 79: whitespace correlation context UUID fallback.
    """
    sender = AgentAddress(
        agent_type="planning",
        agent_id="planner-01",
        tenant_id="tenant-001",
    )
    token = correlation_id_ctx.set("   ")
    try:
        message = MessageEnvelope(
            sender=sender,
            message_type=MessageType.EVENT,
        )
        assert len(message.correlation_id) > 0
        UUID(message.correlation_id) # should be valid UUID string
    finally:
        correlation_id_ctx.reset(token)
@pytest.mark.parametrize(
    "invalid_number",
    [
        float("nan"),
        float("inf"),
        float("-inf"),
    ],
)
def test_non_finite_float_rejected(
    invalid_number,
):
    sender = AgentAddress(
        agent_type="planning",
        agent_id="planner-01",
        tenant_id="tenant-001",
    )

    with pytest.raises(
        ValueError,
        match="must be finite",
    ):
        MessageEnvelope(
            sender=sender,
            message_type=MessageType.EVENT,
            payload={
                "measurement": invalid_number,
            },
        )