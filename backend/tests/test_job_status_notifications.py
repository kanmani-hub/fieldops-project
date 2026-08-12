import pytest
import json
import asyncio
from datetime import datetime, date, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from unittest.mock import patch, MagicMock

import app.services.notification_services as notification_module
from app.services.ai.FieldOpsAI.schemas.communication import (
    CommunicationDecision,
)
from app.services.ai.FieldOpsAI.services.communication_service import (
    CommunicationServiceResult,
)
from app.services.ai.guardrails.contracts import (
    GuardrailPipelineResult,
)

# Setup test DB
SQLALCHEMY_DATABASE_URL = "sqlite://"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Patch app.database.SessionLocal globally before importing components
import app.database
app.database.SessionLocal = TestingSessionLocal

import fakeredis
fake_redis = fakeredis.FakeRedis(decode_responses=True)
import app.redis_client
app.redis_client.get_redis_client = lambda: fake_redis

from app.database import Base
from app.models import Job, Technician, AuditEvent
from app.services.notification_services import JobStatusEvent, EventPublisher, NotificationRouter
from app.tasks import process_job_status_transition_task, send_dispatcher_digest


class FakeCommunicationIntegration:
    """
    Return deterministic safe communication without calling
    Groq, PostgreSQL brand-rule loading, or the real fallback
    workflow.
    """

    def __init__(
        self,
    ) -> None:
        self.calls: list[dict] = []

    async def generate(
        self,
        *,
        event,
        recipient_type: str,
        channel: str,
        notification_type: str,
        locale: str = "en",
    ) -> CommunicationServiceResult:
        self.calls.append(
            {
                "event": event,
                "recipient_type": recipient_type,
                "channel": channel,
                "notification_type": (
                    notification_type
                ),
                "locale": locale,
            }
        )

        normalized_channel = (
            channel
            .strip()
            .upper()
            .replace(
                "-",
                "_",
            )
        )

        if normalized_channel == "EMAIL":
            decision = CommunicationDecision(
                channel="EMAIL",
                title=None,
                subject="FieldOps service update",
                message=(
                    "<p>Your service request has "
                    "been updated.</p>"
                ),
                tone="PROFESSIONAL",
                confidence=1.0,
            )

        elif normalized_channel == "PUSH":
            decision = CommunicationDecision(
                channel="PUSH",
                title="FieldOps update",
                subject=None,
                message=(
                    "Your service request has "
                    "been updated."
                ),
                tone="PROFESSIONAL",
                confidence=1.0,
            )

        elif normalized_channel == "IN_APP":
            decision = CommunicationDecision(
                channel="IN_APP",
                title="FieldOps update",
                subject=None,
                message=(
                    "The job status has been "
                    "updated safely."
                ),
                tone="PROFESSIONAL",
                confidence=1.0,
            )

        else:
            decision = CommunicationDecision(
                channel="SMS",
                title=None,
                subject=None,
                message=(
                    "Your FieldOps service request "
                    "has been updated."
                ),
                tone="PROFESSIONAL",
                confidence=1.0,
            )

        guardrail_result = (
            GuardrailPipelineResult.from_checks(
                checks=(),
                total_latency_ms=0.0,
            )
        )

        return CommunicationServiceResult(
            decision=decision,
            used_fallback=False,
            fallback_source=None,
            fallback_template_id=None,
            fallback_template_version=None,
            guardrail_result=guardrail_result,
            audit_record_count=0,
        )

@pytest.fixture(autouse=True)
def setup_db(
    monkeypatch,
):
    """
    Give every test the isolated SQLite database and fake Redis.

    We patch notification_services directly because another test
    module may have imported it before this file.
    """
    Base.metadata.drop_all(bind=engine)

    Base.metadata.create_all(bind=engine)
    fake_redis.flushall()
    monkeypatch.setattr(notification_module,"SessionLocal",TestingSessionLocal,)

    monkeypatch.setattr(notification_module,"get_redis_client",lambda: fake_redis,)
    yield
    fake_redis.flushall()

def test_event_publisher_writes_audit_trail_and_redis(setup_db):
    db = TestingSessionLocal()
    
    # Pre-populate tech
    tech = Technician(
        technician_id=1,
        tech_id="1",
        technician_name="John Tech",
        technician_skill="Plumbing",
        technician_location="Zone A",
        phone_number="+15555555555"
    )
    db.add(tech)
    db.commit()
    
    event = JobStatusEvent(
        job_id="101",
        tenant_id="tenant-123",
        from_status="CREATED",
        to_status="ASSIGNED",
        actor_id="dispatcher-1",
        actor_role="dispatcher",
        reason="First assignment",
        timestamp=datetime.now(timezone.utc),
        job_title="Leak Fix",
        job_location="123 Road",
        technician_id="1",
        technician_name="John Tech",
        customer_id="cust-1",
        customer_name="Alice",
        customer_phone="+12222222222",
        customer_email="alice@example.com",
        eta=None,
        notification_channels=[]
    )
    
    # Run publish
    publisher = EventPublisher(redis_client=fake_redis)
    asyncio.run(publisher.publish(event))
    
    # Verify AuditEvent written to DB
    audit = db.query(AuditEvent).filter(AuditEvent.job_id == "101").first()
    assert audit is not None
    assert audit.tenant_id == "tenant-123"
    assert audit.new_status == "ASSIGNED"
    assert audit.old_status == "CREATED"
    assert audit.details["reason"] == "First assignment"
    
    # Verify published to Redis channel
    published_events = fake_redis.pubsub_channels()
    db.close()

@pytest.mark.anyio
async def test_notification_router_sends_email_on_completed(setup_db):
    db = TestingSessionLocal()
    
    event = JobStatusEvent(
        job_id="101",
        tenant_id="tenant-123",
        from_status="ON_SITE",
        to_status="COMPLETED",
        actor_id="tech-1",
        actor_role="technician",
        reason="Work completed",
        timestamp=datetime.now(timezone.utc),
        job_title="Leak Fix",
        job_location="123 Road",
        technician_id="1",
        technician_name="John Tech",
        customer_id="cust-1",
        customer_name="Alice",
        customer_phone="+12222222222",
        customer_email="alice@example.com",
        eta=None,
        notification_channels=[]
    )
    
    mock_email = MagicMock()
    mock_email.send_email = MagicMock(return_value=asyncio.Future())
    mock_email.send_email.return_value.set_result(True)
    
    router = NotificationRouter(
        fcm_service=MagicMock(return_value=asyncio.Future()),
        sms_service=MagicMock(return_value=asyncio.Future()),
        email_service=mock_email,
        ws_manager=MagicMock(),
        redis_client=fake_redis,
        communication_integration=(
            FakeCommunicationIntegration()
        ),
    )
    router.fcm.return_value.set_result({"sent": 1})
    router.sms.return_value.set_result({"sent": 1})
    
    await router.route(event)
    
    # Check email service invoked for customer email
    mock_email.send_email.assert_called_once()
    args, kwargs = mock_email.send_email.call_args
    assert args[0] == "alice@example.com"
    assert "alice@example.com" in args[0]
    assert "survey/101" in args[2]  # Survey link in html content
    db.close()

@pytest.mark.anyio
async def test_notification_router_fallbacks_to_sms_if_no_push_token(setup_db):
    db = TestingSessionLocal()
    # Pre-populate tech with NO fcm_token
    tech = Technician(
        technician_id=1,
        tech_id="1",
        technician_name="John Tech",
        technician_skill="Plumbing",
        technician_location="Zone A",
        phone_number="+15555555555",
        fcm_token=None
    )
    db.add(tech)
    db.commit()
    
    event = JobStatusEvent(
        job_id="101",
        tenant_id="tenant-123",
        from_status="CREATED",
        to_status="ASSIGNED",
        actor_id="dispatcher-1",
        actor_role="dispatcher",
        reason="First assignment",
        timestamp=datetime.now(timezone.utc),
        job_title="Leak Fix",
        job_location="123 Road",
        technician_id="1",
        technician_name="John Tech",
        customer_id="cust-1",
        customer_name="Alice",
        customer_phone="+12222222222",
        customer_email="alice@example.com",
        eta=None,
        notification_channels=[]
    )
    
    mock_sms = MagicMock(return_value=asyncio.Future())
    mock_sms.return_value.set_result({"sent": 1})
    
    router = NotificationRouter(
        fcm_service=MagicMock(return_value=asyncio.Future()),
        sms_service=mock_sms,
        email_service=MagicMock(return_value=asyncio.Future()),
        ws_manager=MagicMock(),
        redis_client=fake_redis,
        communication_integration=(
            FakeCommunicationIntegration()
        ),
    )
    router.fcm.return_value.set_result({"sent": 0})
    router.email.send_email = MagicMock(return_value=asyncio.Future())
    router.email.send_email.return_value.set_result(True)
    
    await router.route(event)
    
    # FCM has no token -> should fallback to SMS
    mock_sms.assert_called_once()
    db.close()

@pytest.mark.anyio
async def test_dispatcher_digest_batching_and_celery_task(setup_db):
    db = TestingSessionLocal()
    
    event1 = JobStatusEvent(
        job_id="101",
        tenant_id="tenant-123",
        from_status="CREATED",
        to_status="ASSIGNED",
        actor_id="dispatcher-1",
        actor_role="dispatcher",
        reason="Assigned",
        timestamp=datetime.now(timezone.utc),
        job_title="Leak Fix",
        job_location="123 Road",
        technician_id="1",
        technician_name="John Tech",
        customer_id="cust-1",
        customer_name="Alice",
        customer_phone="+12222222222",
        customer_email="alice@example.com",
        eta=None,
        notification_channels=[]
    )
    
    event2 = JobStatusEvent(
        job_id="102",
        tenant_id="tenant-123",
        from_status="ASSIGNED",
        to_status="EN_ROUTE",
        actor_id="tech-1",
        actor_role="technician",
        reason="Heading over",
        timestamp=datetime.now(timezone.utc),
        job_title="Lock Fix",
        job_location="456 Ave",
        technician_id="1",
        technician_name="John Tech",
        customer_id="cust-2",
        customer_name="Bob",
        customer_phone="+13333333333",
        customer_email="bob@example.com",
        eta="15 mins",
        notification_channels=[]
    )
    
    mock_ws = MagicMock()
    mock_ws.broadcast = MagicMock(return_value=asyncio.Future())
    mock_ws.broadcast.return_value.set_result(True)
    
    router = NotificationRouter(
        fcm_service=MagicMock(return_value=asyncio.Future()),
        sms_service=MagicMock(return_value=asyncio.Future()),
        email_service=MagicMock(),
        ws_manager=mock_ws,
        redis_client=fake_redis,
        communication_integration=(
            FakeCommunicationIntegration()
        ),
    )
    router.fcm.return_value.set_result({"sent": 1})
    router.sms.return_value.set_result({"sent": 1})
    router.email.send_email = MagicMock(return_value=asyncio.Future())
    router.email.send_email.return_value.set_result(True)
    
    # Route event 1 and 2
    await router.route(event1)
    await router.route(event2)
    
    # Verify dispatcher messages are queued in Redis list
    redis_key = "dispatcher_digest:tenant-123"
    queued_count = fake_redis.llen(redis_key)
    assert queued_count == 2
    
    # Run dispatcher digest celery task
    with patch("app.services.socket_manager.ws_manager", mock_ws):
        send_dispatcher_digest()
        
    # Verify queue cleared and broadcast called
    assert fake_redis.llen(redis_key) == 0
    mock_ws.broadcast.assert_called_once()
    args, kwargs = mock_ws.broadcast.call_args
    assert args[0] == "tenant:tenant-123:dispatchers"
    assert args[1]["type"] == "digest"
    assert len(args[1]["notifications"]) == 2
    db.close()
