"""
test_status_template_selection.py

Focused test suite for FieldOps Epic 5 — Story 8.2: Implement Status-Based Template Selection.
"""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from datetime import datetime, timezone

from app.models import Base, NotificationTemplate
from app.services.ai.FieldOpsAI.schemas.prompt_template import (
    MessageTemplateStatus,
    DEFAULT_TEMPLATE_STATUS,
    UnsupportedTemplateStatusError,
    normalize_template_status,
    LEGACY_TEMPLATE_STATUS_ALIASES,
    STATUS_LOOKUP_CANDIDATES,
    PromptTemplateCreate,
    PromptTemplateUpdate,
)
from app.services.ai.FieldOpsAI.services.managed_prompt_template_registry import (
    ManagedPromptTemplateRegistry,
    TemplateValidationServiceError,
)
from app.services.ai.FieldOpsAI.repositories.prompt_template_repository import (
    PromptTemplateRepository,
)
from app.services.template_engine import (
    render_managed_template,
    MessageTemplateLookupError,
)
from app.services.default_template import (
    LOCALIZED_NOTIFICATION_TYPES,
)
from app.services.ai.guardrails.fallback_service import (
    GuardrailFallbackService,
    FallbackTemplateSource,
)
from app.services.ai.FieldOpsAI.schemas.communication import (
    CommunicationContext,
)
from app.services.ai.integrations.communication_integration import (
    CommunicationIntegration,
    CommunicationIntegrationError,
)
from app.services.notification_services import (
    NotificationRouter,
    JobStatusEvent,
)

# SQLite in-memory test database
engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture
def db_session():
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


class FakeRedis:
    def __init__(self):
        self.store = {}
    def get(self, key):
        return self.store.get(key)
    def setex(self, key, ttl, value):
        self.store[key] = value
    def delete(self, key):
        self.store.pop(key, None)


# ==========================================================
# 1. Strict Enum & Normalization Contract
# ==========================================================

def test_six_lifecycle_statuses_enum():
    assert set(status.value for status in MessageTemplateStatus) == {
        "created",
        "assigned",
        "enroute",
        "onsite",
        "completed",
        "cancelled",
    }
    assert "default" not in {status.value for status in MessageTemplateStatus}
    assert DEFAULT_TEMPLATE_STATUS == "default"


def test_normalize_template_status_allow_default():
    assert normalize_template_status("default", allow_default=True) == "default"
    with pytest.raises(UnsupportedTemplateStatusError):
        normalize_template_status("default", allow_default=False)


@pytest.mark.parametrize(
    "invalid_val",
    [
        "invalid_status_xyz",
        "random_status",
        "foo_bar",
        "closed",
        "active",
        "pending",
        "new",
        "open",
        "in_progress",
    ],
)
def test_unsupported_status_rejection(invalid_val: str):
    with pytest.raises(UnsupportedTemplateStatusError):
        normalize_template_status(invalid_val, allow_default=False)
    with pytest.raises(UnsupportedTemplateStatusError):
        normalize_template_status(invalid_val, allow_default=True)


def test_pydantic_schema_status_validation():
    dto1 = PromptTemplateCreate(
        name="Test Template",
        agent_type="CommsAgent",
        channel="sms",
        language="en",
        status="EN_ROUTE",
        body="Body",
        variables=[],
    )
    assert dto1.status == "enroute"

    dto2 = PromptTemplateCreate(
        name="Default Fallback Template",
        agent_type="CommsAgent",
        channel="sms",
        language="en",
        status="default",
        body="Body",
        variables=[],
    )
    assert dto2.status == "default"

    with pytest.raises(ValueError):
        PromptTemplateCreate(
            name="Invalid Template",
            agent_type="CommsAgent",
            channel="sms",
            language="en",
            status="invalid_status_xyz",
            body="Body",
            variables=[],
        )


# ==========================================================
# 2. Canonical vs Legacy vs Default Precedence
# ==========================================================

def test_canonical_beats_legacy_beats_default(db_session: Session):
    repo = PromptTemplateRepository(db_session, tenant_id="tenant_1")
    reg = ManagedPromptTemplateRegistry(db_session, tenant_id="tenant_1", actor_id="actor1", redis_client=None)

    # Create canonical enroute, legacy technician_en_route, and default templates
    t_canonical = repo.create({
        "name": "Canonical Enroute",
        "agent_type": "CommsAgent",
        "channel": "sms",
        "language": "en",
        "status": "enroute",
        "body": "Canonical body",
        "title": "Canonical title",
        "variables": [],
        "version": 1,
        "is_active": True,
    })
    t_legacy = repo.create({
        "name": "Legacy Enroute",
        "agent_type": "CommsAgent",
        "channel": "sms",
        "language": "en",
        "status": "technician_en_route",
        "body": "Legacy body",
        "title": "Legacy title",
        "variables": [],
        "version": 1,
        "is_active": True,
    })
    t_default = repo.create({
        "name": "Default Fallback",
        "agent_type": "CommsAgent",
        "channel": "sms",
        "language": "en",
        "status": "default",
        "body": "Default body",
        "title": "Default title",
        "variables": [],
        "version": 1,
        "is_active": True,
    })
    db_session.commit()

    # 1. Canonical row wins
    res = reg.find("CommsAgent", "sms", "en", "enroute")
    assert res.id == t_canonical.id
    assert res.body == "Canonical body"

    # 2. Soft-delete canonical row -> legacy row wins
    t_canonical.is_active = False
    db_session.commit()
    res2 = reg.find("CommsAgent", "sms", "en", "enroute")
    assert res2.id == t_legacy.id
    assert res2.body == "Legacy body"

    # 3. Soft-delete legacy row -> default row wins
    t_legacy.is_active = False
    db_session.commit()
    res3 = reg.find("CommsAgent", "sms", "en", "enroute")
    assert res3.id == t_default.id
    assert res3.body == "Default body"


# ==========================================================
# 3. Tenant / Platform & Locale Precedence
# ==========================================================

def test_tenant_platform_locale_precedence(db_session: Session):
    repo_plat = PromptTemplateRepository(db_session, tenant_id="**platform**")
    t_plat = repo_plat.create({
        "name": "Platform Enroute EN",
        "agent_type": "CommsAgent",
        "channel": "sms",
        "language": "en",
        "status": "enroute",
        "body": "Platform EN body",
        "title": "Title",
        "variables": [],
        "version": 1,
        "is_active": True,
    })

    repo_tenant = PromptTemplateRepository(db_session, tenant_id="tenant_2")
    t_tenant_es = repo_tenant.create({
        "name": "Tenant Enroute ES",
        "agent_type": "CommsAgent",
        "channel": "sms",
        "language": "es",
        "status": "enroute",
        "body": "Tenant ES body",
        "title": "Title",
        "variables": [],
        "version": 1,
        "is_active": True,
    })
    db_session.commit()

    reg_tenant = ManagedPromptTemplateRegistry(db_session, tenant_id="tenant_2", actor_id="actor1", redis_client=None)

    # Requested es-MX -> tenant es row beats platform en row
    res1 = reg_tenant.find("CommsAgent", "sms", "es-MX", "EN_ROUTE")
    assert res1.id == t_tenant_es.id
    assert res1.body == "Tenant ES body"


# ==========================================================
# 4. Normalized Cache Identity
# ==========================================================

def test_cache_key_normalized_identity(db_session: Session):
    repo = PromptTemplateRepository(db_session, tenant_id="tenant_c")
    repo.create({
        "name": "Test Template",
        "agent_type": "CommsAgent",
        "channel": "sms",
        "language": "en",
        "status": "enroute",
        "body": "Body",
        "title": "Title",
        "variables": [],
        "version": 1,
        "is_active": True,
    })
    db_session.commit()

    fake_redis = FakeRedis()
    reg = ManagedPromptTemplateRegistry(db_session, tenant_id="tenant_c", actor_id="actor1", redis_client=fake_redis)

    for spell in ["EN_ROUTE", "en-route", "en_route", "enroute"]:
        reg.find("CommsAgent", "sms", "en", spell)

    keys = list(fake_redis.store.keys())
    assert len(keys) == 1
    assert "status:enroute" in keys[0]


# ==========================================================
# 5. Authoritative Job Status & Conflict Enforcement
# ==========================================================

def test_guardrail_fallback_uses_job_status(db_session: Session):
    service = GuardrailFallbackService(db=db_session)
    ctx = CommunicationContext(
        job_id="101",
        channel="SMS",
        recipient_type="CUSTOMER",
        locale="en",
        job_status="EN_ROUTE",
        notification_type="technician_location_update",
    )
    result = service.render(context=ctx)
    assert result is not None
    assert any(term in result.decision.message.lower() for term in ["en route", "journey", "way", "on the way"])


def test_communication_integration_exact_conflict_exception():
    integration = CommunicationIntegration(
        session_factory=MagicMock(),
        redis_client=MagicMock(),
    )
    event = JobStatusEvent(
        job_id="job_2",
        tenant_id="tenant_y",
        from_status="CREATED",
        to_status="ASSIGNED",
        actor_id="system",
        actor_role="system",
        reason=None,
        timestamp=datetime.now(timezone.utc),
        job_title="Inspection",
        job_location="456 Elm St",
        technician_id="tech_2",
        technician_name="Bob Tech",
        customer_id="cust_2",
        customer_name="Alice Customer",
        customer_phone="+15559876543",
        customer_email="alice@example.com",
        eta="11:00 AM",
        notification_channels=["email"],
    )

    with pytest.raises(CommunicationIntegrationError) as exc_info:
        integration.build_context(
            event=event,
            recipient_type="customer",
            channel="email",
            notification_type="job_completed",
            locale="en",
        )

    assert str(exc_info.value) == "TEMPLATE_STATUS_CONFLICT"
    assert "job_completed" not in str(exc_info.value)
    assert "ASSIGNED" not in str(exc_info.value)


# ==========================================================
# 6. Sentinel Rendering Prevention & Unknown Status Safety
# ==========================================================

def test_sentinel_rendering_prevention(db_session: Session):
    reg = ManagedPromptTemplateRegistry(db_session, tenant_id="tenant_empty", actor_id="actor1", redis_client=None)
    sentinel = reg.find("CommsAgent", "sms", "en", "assigned")
    assert sentinel.source == "builtin_default"
    assert sentinel.id is None

    with pytest.raises(MessageTemplateLookupError):
        render_managed_template(
            db=db_session,
            tenant_id="tenant_empty",
            agent_type="CommsAgent",
            channel="sms",
            language="en",
            status="assigned",
            context={},
        )


def test_unknown_status_safety(db_session: Session):
    with pytest.raises(MessageTemplateLookupError):
        render_managed_template(
            db=db_session,
            tenant_id="tenant_x",
            agent_type="CommsAgent",
            channel="sms",
            language="en",
            status="invalid_status_xyz",
            context={},
        )
