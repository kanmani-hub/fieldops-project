import pytest
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch, ANY
import asyncio
import uuid
import datetime
import os
import alembic.config
import alembic.command
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import (
    Base,
    Organization,
    Technician,
    CommunicationChannelConfiguration,
    CommunicationConfigurationAudit,
)
from app.services.ai.FieldOpsAI.schemas.communication_configuration import (
    CommunicationChannelState,
    CommunicationMessageCategory,
    DeliveryDecision,
    CommunicationChannelDisabledError,
    UnsupportedCommunicationChannelError,
    CommunicationConfigurationNotFoundError,
)
from app.services.ai.FieldOpsAI.services.communication_configuration_service import CommunicationConfigurationService
from app.services.ai.FieldOpsAI.repositories.communication_configuration_repository import CommunicationConfigurationRepository
from app.services.twilio_sms import send_job_assignment_sms
from app.main import app
from app.dependencies.prompt_admin_authorization import require_prompt_admin, PromptAdminPrincipal
from app.database import get_db
from twilio.base.exceptions import TwilioRestException
from app.models import Base

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        org = Organization(
             id="default",
             name="Test Organization",
             slug="test-org",
   )

        session.add(org)
        session.commit()
        config = CommunicationChannelConfiguration(
            id=str(uuid.uuid4()),
            tenant_id="default",
            channel="SMS",
            state="ENABLED",
            revision=1,
            updated_by="system_migration",
            created_at=datetime.datetime.now(datetime.timezone.utc),
            updated_at=datetime.datetime.now(datetime.timezone.utc)
        )
        config_email = CommunicationChannelConfiguration(
            id=str(uuid.uuid4()),
            tenant_id="default",
            channel="EMAIL",
            state="ENABLED",
            revision=1,
            updated_by="system_migration",
            created_at=datetime.datetime.now(datetime.timezone.utc),
            updated_at=datetime.datetime.now(datetime.timezone.utc)
        )
        session.add(config)
        session.add(config_email)
        session.commit()
    yield
    Base.metadata.drop_all(bind=engine)

@pytest.fixture
def db_session():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

@pytest.fixture
def no_sms_rate_limit(monkeypatch):
    monkeypatch.setattr(
        "app.services.twilio_sms.get_redis_client",
        lambda: None,
    )

# Migration Tests
def test_real_migration():
    db_file = "test_migration.db"
    if os.path.exists(db_file):
        os.remove(db_file)

    db_url = f"sqlite:///{db_file}"
    alembic_cfg = alembic.config.Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", db_url)

    original_db_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = db_url

    try:
        engine_mig = create_engine(db_url)

        # Seed alembic_version at the revision before Story 14.1
        with engine_mig.connect() as conn:
            conn.execute(text(
                "CREATE TABLE alembic_version "
                "(version_num VARCHAR(32) NOT NULL, "
                "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
            ))
            conn.execute(text("INSERT INTO alembic_version (version_num) VALUES ('5a33c0bd93b5')"))
            conn.commit()

        # Step 1: upgrade to Story 14.1 head
        alembic.command.upgrade(alembic_cfg, "1a2b3c4d5e6f")

        with engine_mig.connect() as conn:
            tables = [t[0] for t in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).fetchall()]
            assert "communication_channel_configurations" in tables
            assert "communication_configuration_audits" in tables

            sms_row = conn.execute(
                text("SELECT channel, state, revision, updated_by FROM communication_channel_configurations WHERE channel='SMS'")
            ).fetchone()
            assert sms_row is not None, "SMS row must exist after Story 14.1 upgrade"
            sms_state = sms_row._mapping["state"]
            sms_revision = sms_row._mapping["revision"]
            assert sms_state == "ENABLED"
            assert sms_revision == 1

            # No EMAIL row yet
            email_row_before = conn.execute(
                text("SELECT * FROM communication_channel_configurations WHERE channel='EMAIL'")
            ).fetchone()
            assert email_row_before is None, "EMAIL row must NOT exist at Story 14.1 head"

        # Step 2: upgrade to Story 14.2 head
        alembic.command.upgrade(alembic_cfg, "b15cb1f9d24e")

        with engine_mig.connect() as conn:
            email_row = conn.execute(
                text("SELECT channel, state, revision, updated_by FROM communication_channel_configurations WHERE channel='EMAIL'")
            ).fetchone()
            assert email_row is not None, "EMAIL row must exist after Story 14.2 upgrade"
            em = email_row._mapping
            assert em["state"] == "ENABLED"
            assert em["revision"] == 1
            assert em["updated_by"] == "system_migration"

            # SMS must be unchanged
            sms_row2 = conn.execute(
                text("SELECT state, revision FROM communication_channel_configurations WHERE channel='SMS'")
            ).fetchone()
            assert sms_row2 is not None
            assert sms_row2._mapping["state"] == sms_state
            assert sms_row2._mapping["revision"] == sms_revision

        # Step 3: downgrade back to Story 14.1 head
        alembic.command.downgrade(alembic_cfg, "1a2b3c4d5e6f")

        with engine_mig.connect() as conn:
            tables_after = [t[0] for t in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).fetchall()]

            # Story 14.1 generic tables must still exist
            assert "communication_channel_configurations" in tables_after, \
                "communication_channel_configurations table must survive downgrade"
            assert "communication_configuration_audits" in tables_after, \
                "communication_configuration_audits table must survive downgrade"

            # SMS row must be preserved
            sms_after = conn.execute(
                text("SELECT state, revision FROM communication_channel_configurations WHERE channel='SMS'")
            ).fetchone()
            assert sms_after is not None, "SMS row must survive downgrade"
            assert sms_after._mapping["state"] == sms_state
            assert sms_after._mapping["revision"] == sms_revision

            # EMAIL row must be gone
            email_after = conn.execute(
                text("SELECT * FROM communication_channel_configurations WHERE channel='EMAIL'")
            ).fetchone()
            assert email_after is None, "EMAIL row must be removed by Story 14.2 downgrade"

    finally:
        if "engine_mig" in locals():
            engine_mig.dispose()
        if original_db_url is not None:
            os.environ["DATABASE_URL"] = original_db_url
        else:
            del os.environ["DATABASE_URL"]
        if os.path.exists(db_file):
            os.remove(db_file)

# Service Tests
def test_unknown_channel_rejected(db_session):
    repo = CommunicationConfigurationRepository(db_session)
    service = CommunicationConfigurationService(repo, db_session)
    with pytest.raises(UnsupportedCommunicationChannelError):
        service.get_channel_configuration("UNKNOWN")

@pytest.mark.parametrize("channel", ["SMS", "EMAIL"])
def test_missing_sms_row_uses_compatibility_default(db_session, channel):
    db_session.query(CommunicationChannelConfiguration).delete()
    db_session.commit()
    
    repo = CommunicationConfigurationRepository(db_session)
    service = CommunicationConfigurationService(repo, db_session)
    decision = service.evaluate_delivery(channel)
    assert decision.allowed is True
    assert decision.state == CommunicationChannelState.ENABLED
    assert decision.reason_code == "COMPATIBILITY_DEFAULT"

@pytest.mark.parametrize("channel", ["SMS", "EMAIL"])
def test_database_failure_returns_blocked_CONFIGURATION_UNAVAILABLE(db_session, channel):
    repo = CommunicationConfigurationRepository(db_session)
    repo.get_by_channel = MagicMock(side_effect=Exception("DB Failure"))
    service = CommunicationConfigurationService(repo, db_session)
    
    decision = service.evaluate_delivery(channel)
    assert decision.allowed is False
    assert decision.reason_code == "CONFIGURATION_UNAVAILABLE"
    assert decision.state == CommunicationChannelState.DISABLED

@pytest.mark.parametrize("channel", ["SMS", "EMAIL"])
def test_no_op_does_not_commit_or_update_timestamp(db_session, channel):
    repo = CommunicationConfigurationRepository(db_session)
    service = CommunicationConfigurationService(repo, db_session)
    
    config = db_session.query(CommunicationChannelConfiguration).filter_by(channel=channel).first()
    old_updated = config.updated_at
    old_rev = config.revision
    
    service.update_channel_state(channel, CommunicationChannelState.ENABLED, "u", "t", "valid reason")
    
    db_session.refresh(config)
    assert config.revision == old_rev
    assert config.updated_at == old_updated

@pytest.mark.parametrize("channel", ["SMS", "EMAIL"])
def test_failed_audit_insertion_rolls_back_state_and_revision(db_session, channel):
    repo = CommunicationConfigurationRepository(db_session)
    service = CommunicationConfigurationService(repo, db_session)
    
    config = db_session.query(CommunicationChannelConfiguration).filter_by(channel=channel).first()
    old_rev = config.revision
    
    repo.add_audit = MagicMock(side_effect=Exception("DB Error"))
    
    with pytest.raises(Exception):
        service.update_channel_state(channel, CommunicationChannelState.DISABLED, "u", "t", "valid reason")
        
    db_session.refresh(config)
    assert config.state == "ENABLED"
    assert config.revision == old_rev

# Authorization Tests
def test_authorization_tests():
    import jwt
    import time
    
    client = TestClient(app)
    response = client.get("/admin/communication-config/channels/SMS")
    assert response.status_code == 401
            
    # We will use dependency override to test the require_platform_super_admin logic
    def mock_require_prompt_admin_super():
        return PromptAdminPrincipal(actor_id="user1", tenant_id="**platform**", role="super_admin")
        
    def mock_require_prompt_admin_tenant():
        return PromptAdminPrincipal(actor_id="user1", tenant_id="tenant1", role="admin")

    app.dependency_overrides[get_db] = lambda: TestingSessionLocal()
    
    # Platform super_admin GET allowed
    app.dependency_overrides[require_prompt_admin] = mock_require_prompt_admin_super
    response = client.get("/admin/communication-config/channels/SMS")
    assert response.status_code == 200
    
    # Platform super_admin PUT allowed
    response = client.put("/admin/communication-config/channels/SMS", 
                          json={"state": "DISABLED", "reason": "testing valid reason longer than 10"})
    assert response.status_code == 200
    
    # tenant admin denied
    app.dependency_overrides[require_prompt_admin] = mock_require_prompt_admin_tenant
    response = client.get("/admin/communication-config/channels/SMS")
    assert response.status_code == 403
    
    # Client actor/revision fields rejected
    app.dependency_overrides[require_prompt_admin] = mock_require_prompt_admin_super
    response = client.put("/admin/communication-config/channels/SMS", 
                          json={"state": "DISABLED", "reason": "testing valid reason longer than 10", "actor_id": "hacker"})
    assert response.status_code == 400
    
    app.dependency_overrides.pop(
        get_db,
        None,
    )
    app.dependency_overrides.pop(
        require_prompt_admin,
        None,
    )

# Delivery Tests
def test_delivery_enabled_standard_calls_twilio(db_session,no_sms_rate_limit,):
    async def run_test():
        tech = Technician(technician_id=1, tech_id="tech1", technician_name="T", technician_skill="S", technician_location="L", phone_number="+1234567890", sms_opt_out=0)
        db_session.add(tech)
        db_session.commit()
        
        with patch("app.services.twilio_sms.twilio_client") as mock_twilio:
            mock_twilio.messages.create.return_value = MagicMock(sid="123")
            await send_job_assignment_sms(db_session, "job1", "Title", "Loc", "P", ["tech1"])
            mock_twilio.messages.create.assert_called_once()
    asyncio.run(run_test())

def test_delivery_disabled_standard_never_calls(db_session,no_sms_rate_limit,):
    async def run_test():
        config = db_session.query(CommunicationChannelConfiguration).filter_by(channel="SMS").first()
        config.state = "DISABLED"
        db_session.commit()
        
        tech = Technician(technician_id=1, tech_id="tech1", technician_name="T", technician_skill="S", technician_location="L", phone_number="+1234567890", sms_opt_out=0)
        db_session.add(tech)
        db_session.commit()
        
        with patch("app.services.twilio_sms.twilio_client") as mock_twilio:
            # Since standard is blocked by disabled
            result = await send_job_assignment_sms(
                db_session,
                "job1",
                "Title",
                "Loc",
                "P",
                ["tech1"],
            )
            mock_twilio.messages.create.assert_not_called()
            assert result["blocked"] == 1
            assert result["blocked_reasons"] == {
                "SMS_DISABLED": 1
            }
    asyncio.run(run_test())

def test_delivery_emergency_only_emergency_calls(db_session,no_sms_rate_limit,):
    async def run_test():
        config = db_session.query(CommunicationChannelConfiguration).filter_by(channel="SMS").first()
        config.state = "EMERGENCY_ONLY"
        db_session.commit()
        
        tech = Technician(technician_id=1, tech_id="tech1", technician_name="T", technician_skill="S", technician_location="L", phone_number="+1234567890", sms_opt_out=0)
        db_session.add(tech)
        db_session.commit()
        
        with patch("app.services.twilio_sms.twilio_client") as mock_twilio:
            mock_twilio.messages.create.return_value = MagicMock(sid="123")
            await send_job_assignment_sms(db_session, "job1", "Title", "Loc", "P", ["tech1"], category=CommunicationMessageCategory.EMERGENCY)
            mock_twilio.messages.create.assert_called_once()
    asyncio.run(run_test())
        
def test_delivery_state_change_during_batch(db_session,no_sms_rate_limit,):
    async def run_test():
        tech1 = Technician(technician_id=1, tech_id="tech1", technician_name="T1", technician_skill="S", technician_location="L", phone_number="+1234567890", sms_opt_out=0)
        tech2 = Technician(technician_id=2, tech_id="tech2", technician_name="T2", technician_skill="S", technician_location="L", phone_number="+1234567891", sms_opt_out=0)
        db_session.add(tech1)
        db_session.add(tech2)
        db_session.commit()
        
        call_count = 0
        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                config = db_session.query(CommunicationChannelConfiguration).filter_by(channel="SMS").first()
                config.state = "DISABLED"
                db_session.commit()
            return MagicMock(sid="123")
                
        with patch("app.services.twilio_sms.twilio_client") as mock_twilio:
            mock_twilio.messages.create.side_effect = side_effect
            res = await send_job_assignment_sms(db_session, "job1", "Title", "Loc", "P", ["tech1", "tech2"])
            assert mock_twilio.messages.create.call_count == 1 # Only one call should succeed
    asyncio.run(run_test())

def test_delivery_state_change_during_retry(db_session,no_sms_rate_limit,):
    async def run_test():
        tech = Technician(technician_id=1, tech_id="tech1", technician_name="T", technician_skill="S", technician_location="L", phone_number="+1234567890", sms_opt_out=0)
        db_session.add(tech)
        db_session.commit()
        
        call_count = 0
        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                config = db_session.query(CommunicationChannelConfiguration).filter_by(channel="SMS").first()
                config.state = "DISABLED"
                db_session.commit()
                raise TwilioRestException(status=500, uri="x") # retryable
            return MagicMock(sid="123")
                
        with patch("app.services.twilio_sms.twilio_client") as mock_twilio:
            mock_twilio.messages.create.side_effect = side_effect
            await send_job_assignment_sms(db_session, "job1", "Title", "Loc", "P", ["tech1"])
            assert mock_twilio.messages.create.call_count == 1 # First attempt fails, next retry sees DISABLED and breaks
    asyncio.run(run_test())

def test_delivery_emergency_only_standard_never_calls(
    db_session,no_sms_rate_limit,
):
    async def run_test():
        config = (
            db_session.query(
                CommunicationChannelConfiguration
            )
            .filter_by(channel="SMS")
            .first()
        )
        config.state = "EMERGENCY_ONLY"
        db_session.commit()

        tech = Technician(
            technician_id=1,
            tech_id="tech1",
            technician_name="T",
            technician_skill="S",
            technician_location="L",
            phone_number="+1234567890",
            sms_opt_out=0,
        )
        db_session.add(tech)
        db_session.commit()

        with patch(
            "app.services.twilio_sms.twilio_client"
        ) as mock_twilio:
            result = await send_job_assignment_sms(
                db_session,
                "job1",
                "Title",
                "Loc",
                "P",
                ["tech1"],
            )

            mock_twilio.messages.create.assert_not_called()

            assert result["blocked"] == 1
            assert result["blocked_reasons"] == {
                "SMS_EMERGENCY_REQUIRED": 1
            }

    asyncio.run(run_test())
# Audit Tests
def test_audit_immutability(db_session,no_sms_rate_limit,):
    audit = CommunicationConfigurationAudit(
        tenant_id="tenant1",
        channel="SMS",
        new_state="DISABLED",
        new_revision=2,
        actor_id="user",
        actor_tenant_id="tenant",
        reason="test"
    )
    db_session.add(audit)
    db_session.commit()
    db_session.refresh(audit)
    
    with pytest.raises(Exception):
        audit.reason = "new reason"
        db_session.commit()
    db_session.rollback()
        
    with pytest.raises(Exception):
        db_session.delete(audit)
        db_session.commit()
    db_session.rollback()

# Route duplication test
def test_route_duplication(db_session,no_sms_rate_limit,):
    # Assert exactly one registration for GET and PUT endpoints
    get_count = sum(1 for route in app.routes if getattr(route, "path", None) == "/admin/communication-config/channels/{channel}" and "GET" in route.methods)
    put_count = sum(1 for route in app.routes if getattr(route, "path", None) == "/admin/communication-config/channels/{channel}" and "PUT" in route.methods)
    assert get_count == 1
    assert put_count == 1

# ======================================================
# Email Delivery Boundary Tests
# ======================================================
#
# These tests verify the enforced email delivery boundary via the
# NotificationRouter.  They use a controlled in-memory database
# (same TestingSessionLocal), a FakeCommunicationIntegration that
# produces valid email decisions, and a tracking email service.
# SessionLocal is monkeypatched so the policy check reads from the
# test database without touching the real Postgres instance.

import app.services.notification_services as _notification_module
from app.services.notification_services import NotificationRouter, JobStatusEvent
from app.services.ai.FieldOpsAI.schemas.communication import CommunicationDecision
from app.services.ai.FieldOpsAI.services.communication_service import CommunicationServiceResult
from app.services.ai.guardrails.contracts import GuardrailPipelineResult
from datetime import timezone
from unittest.mock import AsyncMock


class _FakeCommIntegration:
    """Returns a valid EMAIL CommunicationDecision for any request."""

    async def generate(self, *, event, recipient_type, channel, notification_type, locale="en"):
        decision = CommunicationDecision(
            channel="EMAIL",
            title=None,
            subject="Your job is complete",
            message="<p>Job done.</p>",
            tone="PROFESSIONAL",
            confidence=1.0,
        )
        guardrail_result = GuardrailPipelineResult.from_checks(checks=(), total_latency_ms=0.0)
        return CommunicationServiceResult(
            decision=decision,
            used_fallback=False,
            fallback_source=None,
            fallback_template_id=None,
            fallback_template_version=None,
            guardrail_result=guardrail_result,
            audit_record_count=0,
        )

from tests.conftest import _TrackingEmailService, _make_router, _build_completed_event


def _set_email_state(db_session, state: str):
    cfg = db_session.query(CommunicationChannelConfiguration).filter_by(channel="EMAIL").first()
    cfg.state = state
    db_session.commit()


# 1. EMAIL ENABLED + STANDARD -> provider called exactly once
def test_email_boundary_enabled_standard_calls_provider(db_session, monkeypatch):
    _set_email_state(db_session, "ENABLED")
    email_svc = _TrackingEmailService()
    router = _make_router(email_svc, db_session)
    monkeypatch.setattr(_notification_module, "SessionLocal", lambda: TestingSessionLocal())

    async def run():
        await router.route(_build_completed_event())

    asyncio.run(run())
    assert len(email_svc.calls) == 1, "Provider must be called once when EMAIL is ENABLED"


# 2. EMAIL DISABLED + STANDARD -> provider not called
def test_email_boundary_disabled_standard_does_not_call_provider(db_session, monkeypatch):
    _set_email_state(db_session, "DISABLED")
    email_svc = _TrackingEmailService()
    router = _make_router(email_svc, db_session)
    monkeypatch.setattr(_notification_module, "SessionLocal", lambda: TestingSessionLocal())

    async def run():
        await router.route(_build_completed_event())

    asyncio.run(run())
    assert len(email_svc.calls) == 0, "Provider must NOT be called when EMAIL is DISABLED"


# 3. EMAIL EMERGENCY_ONLY + STANDARD -> provider not called
def test_email_boundary_emergency_only_standard_does_not_call_provider(db_session, monkeypatch):
    _set_email_state(db_session, "EMERGENCY_ONLY")
    email_svc = _TrackingEmailService()
    router = _make_router(email_svc, db_session)
    monkeypatch.setattr(_notification_module, "SessionLocal", lambda: TestingSessionLocal())

    async def run():
        await router.route(_build_completed_event())

    asyncio.run(run())
    assert len(email_svc.calls) == 0, "Provider must NOT be called when EMAIL is EMERGENCY_ONLY and category is STANDARD"


# 4. EMAIL EMERGENCY_ONLY + EMERGENCY -> provider called once (trusted internal path)
def test_email_boundary_emergency_only_emergency_calls_provider(db_session, monkeypatch):
    _set_email_state(db_session, "EMERGENCY_ONLY")
    email_svc = _TrackingEmailService()
    router = _make_router(email_svc, db_session)
    monkeypatch.setattr(_notification_module, "SessionLocal", lambda: TestingSessionLocal())

    async def run():
        # Trusted internal caller passes EMERGENCY category directly
        await router._send_email(
            _build_completed_event(),
            "customer",
            {},
            {},
            "job_done_survey",
            category=CommunicationMessageCategory.EMERGENCY,
        )

    asyncio.run(run())
    assert len(email_svc.calls) == 1, "Provider must be called when EMAIL is EMERGENCY_ONLY and category is EMERGENCY"


# 5. Configuration DB failure -> provider not called
def test_email_boundary_db_failure_does_not_call_provider(db_session, monkeypatch):
    email_svc = _TrackingEmailService()
    router = _make_router(email_svc, db_session)

    # Make SessionLocal return a session whose repo raises
    class _BrokenSession:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def query(self, *a): raise Exception("DB is down")
        def close(self): pass

    monkeypatch.setattr(_notification_module, "SessionLocal", lambda: _BrokenSession())

    async def run():
        try:
            await router._send_email(
                _build_completed_event(),
                "customer",
                {},
                {},
                "job_done_survey",
                category=CommunicationMessageCategory.STANDARD,
            )
        except CommunicationChannelDisabledError:
            pass  # Expected - policy blocked due to config unavailable

    asyncio.run(run())
    assert len(email_svc.calls) == 0, "Provider must NOT be called when configuration lookup fails"


# ======================================================
# Category Propagation Tests
# ======================================================

# 6. Ordinary routed email defaults to STANDARD
def test_category_ordinary_routed_email_defaults_to_standard(db_session, monkeypatch):
    """route() always passes STANDARD; provider is called when email is ENABLED."""
    _set_email_state(db_session, "ENABLED")
    email_svc = _TrackingEmailService()
    router = _make_router(email_svc, db_session)
    monkeypatch.setattr(_notification_module, "SessionLocal", lambda: TestingSessionLocal())

    async def run():
        await router.route(_build_completed_event())

    asyncio.run(run())
    # Under ENABLED + STANDARD -> provider is called
    assert len(email_svc.calls) == 1


# 7. Untrusted content cannot elevate to EMERGENCY (EMERGENCY_ONLY + STANDARD = blocked)
def test_category_content_cannot_create_emergency(db_session, monkeypatch):
    """route() always uses STANDARD; no subject/body content can produce EMERGENCY category."""
    _set_email_state(db_session, "EMERGENCY_ONLY")
    email_svc = _TrackingEmailService()

    class _UrgentContentIntegration:
        """Simulates an AI decision with 'urgent' language in subject/body."""
        async def generate(self, *, event, recipient_type, channel, notification_type, locale="en"):
            decision = CommunicationDecision(
                channel="EMAIL",
                title=None,
                subject="URGENT: Critical issue",
                message="<p>This is critical and urgent!</p>",
                tone="PROFESSIONAL",
                confidence=1.0,
            )
            guardrail_result = GuardrailPipelineResult.from_checks(checks=(), total_latency_ms=0.0)
            return CommunicationServiceResult(
                decision=decision,
                used_fallback=False,
                fallback_source=None,
                fallback_template_id=None,
                fallback_template_version=None,
                guardrail_result=guardrail_result,
                audit_record_count=0,
            )

    router = NotificationRouter(
        fcm_service=AsyncMock(return_value={"sent": 0, "failed": 0, "delivery_ids": []}),
        sms_service=AsyncMock(return_value={"sent": 0, "failed": 0, "blocked": 0, "blocked_reasons": {}}),
        email_service=email_svc,
        ws_manager=MagicMock(),
        redis_client=MagicMock(),
        communication_integration=_UrgentContentIntegration(),
    )
    monkeypatch.setattr(_notification_module, "SessionLocal", lambda: TestingSessionLocal())

    async def run():
        await router.route(_build_completed_event())

    asyncio.run(run())
    # EMERGENCY_ONLY + STANDARD (from route()) -> blocked even with urgent content
    assert len(email_svc.calls) == 0, \
        "Urgent message content must NOT elevate category to EMERGENCY"


# 8. Trusted internal EMERGENCY reaches the provider under EMERGENCY_ONLY
def test_category_trusted_emergency_reaches_provider_under_emergency_only(db_session, monkeypatch):
    """A trusted internal caller passing category=EMERGENCY reaches the provider."""
    _set_email_state(db_session, "EMERGENCY_ONLY")
    email_svc = _TrackingEmailService()
    router = _make_router(email_svc, db_session)
    monkeypatch.setattr(_notification_module, "SessionLocal", lambda: TestingSessionLocal())

    async def run():
        await router._send_email(
            _build_completed_event(),
            "customer",
            {},
            {},
            "job_done_survey",
            category=CommunicationMessageCategory.EMERGENCY,
        )

    asyncio.run(run())
    assert len(email_svc.calls) == 1, \
        "Trusted EMERGENCY category must reach provider under EMERGENCY_ONLY state"


# ==============================================================================
# Story 14.3 - Cache-Aside Configuration Tests
# ==============================================================================
import json
import datetime

from app.services.ai.FieldOpsAI.services.communication_configuration_service import (
    CommunicationConfigurationService,
    _CACHE_KEY_PREFIX,
)
from app.services.ai.FieldOpsAI.schemas.communication_configuration import (
    CommunicationConfigurationCachePayload,
)
from tests.conftest import SimTimer, FakeRedisClient

@pytest.fixture
def config_service(db_session, fake_redis):
    from app.services.ai.FieldOpsAI.repositories.communication_configuration_repository import CommunicationConfigurationRepository
    repo = CommunicationConfigurationRepository(db_session)
    return CommunicationConfigurationService(repo, db_session, redis_client=fake_redis)

# ---------------------------------------------------------
# Key and payload
# ---------------------------------------------------------
def test_cache_keys(config_service):
    # 1. SMS uses safe namespaced key
    key = config_service._cache_key("SMS")
    assert key == f"{_CACHE_KEY_PREFIX}:sms"
    # 2. EMAIL uses safe namespaced key
    key2 = config_service._cache_key("EMAIL")
    assert key2 == f"{_CACHE_KEY_PREFIX}:email"
    # 3. SMS and EMAIL keys are distinct
    assert key != key2
    # 4. Equivalent channel casing
    assert config_service._cache_key("sms") == key
    # 5. Unsupported channels handled elsewhere (by normalize_channel)

from pydantic import ValidationError

def test_cache_payload_validation_success():
    now = datetime.datetime.now(datetime.timezone.utc)
    payload = CommunicationConfigurationCachePayload(
        schema_version=1,
        channel="SMS",
        state=CommunicationChannelState.ENABLED,
        revision=2,
        updated_at=now,
        updated_by="admin-1"
    )
    data = json.loads(payload.model_dump_json())
    assert "schema_version" in data
    assert data["state"] == "ENABLED"
    assert data["revision"] == 2
    assert "reason" not in data
    assert "phone" not in data
    assert "email" not in data
    assert "message" not in data

def test_cache_payload_validation_rejections():
    now = datetime.datetime.now(datetime.timezone.utc)
    base_data = {
        "schema_version": 1,
        "channel": "SMS",
        "state": "ENABLED",
        "revision": 2,
        "updated_at": now,
        "updated_by": "admin-1"
    }

    # missing schema_version
    with pytest.raises(ValidationError):
        data = base_data.copy()
        del data["schema_version"]
        CommunicationConfigurationCachePayload(**data)

    # schema_version other than 1
    with pytest.raises(ValidationError):
        data = base_data.copy()
        data["schema_version"] = 2
        CommunicationConfigurationCachePayload(**data)

    # revision 0
    with pytest.raises(ValidationError):
        data = base_data.copy()
        data["revision"] = 0
        CommunicationConfigurationCachePayload(**data)

    # negative revision
    with pytest.raises(ValidationError):
        data = base_data.copy()
        data["revision"] = -1
        CommunicationConfigurationCachePayload(**data)

    # unsupported channel
    with pytest.raises(ValidationError):
        data = base_data.copy()
        data["channel"] = "PUSH"
        CommunicationConfigurationCachePayload(**data)

    # lowercase channel inside payload
    with pytest.raises(ValidationError):
        data = base_data.copy()
        data["channel"] = "sms"
        CommunicationConfigurationCachePayload(**data)

    # naive updated_at
    with pytest.raises(ValidationError):
        data = base_data.copy()
        data["updated_at"] = datetime.datetime.now()
        CommunicationConfigurationCachePayload(**data)

    # blank or whitespace-only updated_by
    with pytest.raises(ValidationError):
        data = base_data.copy()
        data["updated_by"] = "   "
        CommunicationConfigurationCachePayload(**data)

    # oversized updated_by
    with pytest.raises(ValidationError):
        data = base_data.copy()
        data["updated_by"] = "a" * 101
        CommunicationConfigurationCachePayload(**data)

    # extra fields
    with pytest.raises(ValidationError):
        data = base_data.copy()
        data["extra_field"] = "value"
        CommunicationConfigurationCachePayload(**data)

# ---------------------------------------------------------
# Cache miss and population
# ---------------------------------------------------------
def test_cache_miss_populates_redis(db_session, config_service, fake_redis, sim_timer):
    _set_email_state(db_session, "ENABLED")
    
    # 13, 14, 15, 16 - Cache miss
    res = config_service.get_channel_configuration("EMAIL")
    assert res.state == CommunicationChannelState.ENABLED
    key = f"{_CACHE_KEY_PREFIX}:email"
    assert f"get:{key}" in fake_redis.calls
    assert f"setex:{key}" in fake_redis.calls
    
    # Verify exact TTL is 60
    val, expiry = fake_redis.store[key]
    assert expiry - sim_timer.time() == 60
    
    # Decode and verify
    data = json.loads(val)
    assert data["state"] == "ENABLED"
    
def test_cache_miss_setex_fails(db_session, config_service, fake_redis):
    _set_email_state(db_session, "ENABLED")
    fake_redis.fail_setex = True
    
    # 17 - Returns DB config when setex fails
    res = config_service.get_channel_configuration("EMAIL")
    assert res.state == CommunicationChannelState.ENABLED
    assert len(fake_redis.store) == 0

def test_missing_row_compatibility_not_cached(db_session, config_service, fake_redis):
    db_session.query(CommunicationChannelConfiguration).filter_by(channel="SMS").delete()
    db_session.commit()
    
    # 18 - Missing row returns default but is NOT cached
    res = config_service.get_channel_configuration("SMS")
    assert res.revision == 0
    key = f"{_CACHE_KEY_PREFIX}:sms"
    assert f"setex:{key}" not in fake_redis.calls

def test_database_exception_not_cached(config_service, fake_redis, monkeypatch):
    def fake_get(*args, **kwargs):
        raise Exception("DB Down")
    monkeypatch.setattr(config_service.repository, "get_by_channel", fake_get)
    
    # 19 - Exception is raised, not cached
    with pytest.raises(Exception):
        config_service.get_channel_configuration("SMS")
    
    key = f"{_CACHE_KEY_PREFIX}:sms"
    assert f"setex:{key}" not in fake_redis.calls

# ---------------------------------------------------------
# Cache hit
# ---------------------------------------------------------
def test_valid_cache_hit(db_session, config_service, fake_redis, sim_timer):
    _set_email_state(db_session, "DISABLED")
    
    # Prime cache
    config_service.get_channel_configuration("EMAIL")
    
    # Change DB behind its back
    _set_email_state(db_session, "ENABLED")
    
    fake_redis.calls.clear()
    
    # 20, 21 - Valid cache hit returns cached state, no DB
    res = config_service.get_channel_configuration("EMAIL")
    assert res.state == CommunicationChannelState.DISABLED
    key = f"{_CACHE_KEY_PREFIX}:email"
    assert f"get:{key}" in fake_redis.calls
    assert f"setex:{key}" not in fake_redis.calls
    
    # 22, 23, 24, 25, 26 - Hit does not extend TTL, preserves state/rev/timestamp
    val, expiry = fake_redis.store[key]
    assert expiry == sim_timer.time() + 60

def test_valid_cache_survives_db_outage(db_session, config_service, monkeypatch):
    _set_email_state(db_session, "ENABLED")
    config_service.get_channel_configuration("EMAIL")
    
    # DB goes down
    def fake_get(*args, **kwargs):
        raise Exception("DB Down")
    monkeypatch.setattr(config_service.repository, "get_by_channel", fake_get)
    
    # 27 - Valid cache used during DB outage
    res = config_service.get_channel_configuration("EMAIL")
    assert res.state == CommunicationChannelState.ENABLED

# ---------------------------------------------------------
# Corrupt cache
# ---------------------------------------------------------
@pytest.mark.parametrize("corrupt_value", [
    "{invalid_json",
    "\"string_json\"",
    '{"channel": "SMS", "state": "ENABLED", "revision": 1}', # missing fields
    '{"schema_version": 1, "channel": "SMS", "state": "INVALID_STATE", "revision": 1, "updated_at": "2023", "updated_by": "A"}', # invalid enum
    '{"schema_version": 1, "channel": "SMS", "state": "ENABLED", "revision": -1, "updated_at": "2023", "updated_by": "A"}', # invalid revision
    '{"schema_version": 1, "channel": "SMS", "state": "ENABLED", "revision": 1, "updated_at": "2023", "updated_by": "A", "extra": 1}', # extra fields
    '{"schema_version": 2, "channel": "SMS", "state": "ENABLED", "revision": 1, "updated_at": "2023", "updated_by": "A"}', # wrong schema
    '{"schema_version": 1, "channel": "WRONG", "state": "ENABLED", "revision": 1, "updated_at": "2023", "updated_by": "A"}', # wrong channel
    "A" * 600, # oversized
])
def test_corrupt_cache_falls_back_to_db(db_session, config_service, fake_redis, corrupt_value):
    _set_email_state(db_session, "DISABLED")
    key = f"{_CACHE_KEY_PREFIX}:email"
    fake_redis.store[key] = (corrupt_value, 999999)
    
    # 28-36: all fall back to DB, 39: safe logged only
    res = config_service.get_channel_configuration("EMAIL")
    assert res.state == CommunicationChannelState.DISABLED
    
    # 37: replaced after DB read
    val, _ = fake_redis.store[key]
    data = json.loads(val)
    assert data["state"] == "DISABLED"

def test_corrupt_cache_plus_db_failure_fails_closed(config_service, fake_redis, monkeypatch):
    key = f"{_CACHE_KEY_PREFIX}:email"
    fake_redis.store[key] = ("{invalid", 999999)
    
    def fake_get(*args, **kwargs):
        raise Exception("DB Down")
    monkeypatch.setattr(config_service.repository, "get_by_channel", fake_get)
    
    # 38: Corrupt cache + DB failure -> exception in get, but evaluate_delivery handles it safely
    decision = config_service.evaluate_delivery("EMAIL")
    assert decision.allowed is False
    assert decision.reason_code == "CONFIGURATION_UNAVAILABLE"

# ---------------------------------------------------------
# Redis failure
# ---------------------------------------------------------
def test_redis_get_failure_falls_back_to_db(db_session, config_service, fake_redis):
    _set_email_state(db_session, "ENABLED")
    fake_redis.fail_get = True
    
    # 40, 43, 45: GET exception falls back to DB cleanly
    res = config_service.get_channel_configuration("EMAIL")
    assert res.state == CommunicationChannelState.ENABLED

def test_redis_timeout_falls_back_to_db(db_session, config_service, fake_redis):
    _set_email_state(db_session, "ENABLED")
    fake_redis.timeout_get = True
    
    # 41: Timeout exception
    res = config_service.get_channel_configuration("EMAIL")
    assert res.state == CommunicationChannelState.ENABLED

def test_redis_and_db_failure_fails_closed(config_service, fake_redis, monkeypatch):
    fake_redis.fail_get = True
    def fake_get(*args, **kwargs):
        raise Exception("DB Down")
    monkeypatch.setattr(config_service.repository, "get_by_channel", fake_get)
    
    # 44: Both fail -> closed
    decision = config_service.evaluate_delivery("EMAIL")
    assert decision.allowed is False
    assert decision.reason_code == "CONFIGURATION_UNAVAILABLE"

# ---------------------------------------------------------
# TTL behavior
# ---------------------------------------------------------
def test_ttl_expiration(db_session, config_service, fake_redis, sim_timer):
    _set_email_state(db_session, "DISABLED")
    config_service.get_channel_configuration("EMAIL")
    
    _set_email_state(db_session, "ENABLED")
    
    # 49, 50, 51: Valid before expiration (simulated)
    sim_timer.tick(30)
    res = config_service.get_channel_configuration("EMAIL")
    assert res.state == CommunicationChannelState.DISABLED
    
    # 46, 47: Expires after 60, triggers DB read
    sim_timer.tick(31) # total 61s elapsed
    fake_redis.calls.clear()
    res = config_service.get_channel_configuration("EMAIL")
    assert res.state == CommunicationChannelState.ENABLED
    
    # 48: Repopulated
    key = f"{_CACHE_KEY_PREFIX}:email"
    assert f"setex:{key}" in fake_redis.calls

# ---------------------------------------------------------
# Admin and bounded staleness
# ---------------------------------------------------------
def test_admin_get_and_put(db_session, config_service, fake_redis):
    _set_email_state(db_session, "ENABLED")
    
    # 65, 66: Admin GET caches
    res = config_service.get_channel_configuration("EMAIL")
    key = f"{_CACHE_KEY_PREFIX}:email"
    assert key in fake_redis.store
    
    # 67, 68: PUT commits to DB, updates revision, returns new state
    res2 = config_service.update_channel_state(
        "EMAIL",
        CommunicationChannelState.DISABLED,
        "admin1",
        "tenant1",
        "Because I said so"
    )
    assert res2.state == CommunicationChannelState.DISABLED
    
    # 72: Story 14.4: Cache is immediately invalidated, so raw GET should fail
    assert key not in fake_redis.store

# ---------------------------------------------------------
# Closure Fixes Tests (Admin DI and Cache-Aware Deliveries)
# ---------------------------------------------------------
def test_admin_dependency_injection(db_session, fake_redis):
    from app.main import app
    from app.database import get_db
    from app.redis_client import get_redis_client
    from app.dependencies.prompt_admin_authorization import require_prompt_admin, PromptAdminPrincipal
    from fastapi.testclient import TestClient

    def _set_sms(state):
        db_session.query(CommunicationChannelConfiguration).filter_by(channel="SMS").delete()
        config = CommunicationChannelConfiguration(
            tenant_id="tenant1",
            channel="SMS",
            state=state,
            revision=1,
            updated_by="sys",
            updated_at=datetime.datetime.now(datetime.timezone.utc)
        )
        db_session.add(config)
        db_session.commit()

    _set_sms("DISABLED")

    # Scope overrides
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_redis_client] = lambda: fake_redis
    app.dependency_overrides[require_prompt_admin] = lambda: PromptAdminPrincipal(
        actor_id="admin-1", tenant_id="**platform**", role="super_admin"
    )

    try:
        client = TestClient(app)
        response = client.get("/admin/communication-config/channels/SMS")
        assert response.status_code == 200

        key = f"{_CACHE_KEY_PREFIX}:sms"
        assert f"get:{key}" in fake_redis.calls
        assert f"setex:{key}" in fake_redis.calls

        # Verify exact TTL is 60
        val, expiry = fake_redis.store[key]
        assert expiry - fake_redis.timer.time() == 60
        # the real/default Redis getter is not used since we overrode the dependency
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_redis_client, None)
        app.dependency_overrides.pop(require_prompt_admin, None)


def test_sms_cache_aware_delivery(
    db_session,
    fake_redis,
    monkeypatch,
    sim_timer,
):
    import app.services.twilio_sms as twilio_sms_mod

    from app.models import (
        SMSDelivery,
        Technician,
        CommunicationChannelConfiguration,
    )
    from app.services.ai.FieldOpsAI.repositories.communication_configuration_repository import (
        CommunicationConfigurationRepository,
    )
    from app.services.twilio_sms import (
        send_job_assignment_sms,
    )

    def _set_sms(
        state: str,
    ) -> None:
        config = (
            db_session.query(
                CommunicationChannelConfiguration
            )
            .filter_by(
                channel="SMS"
            )
            .first()
        )

        if config is None:
            config = (
                CommunicationChannelConfiguration(
                    channel="SMS",
                    state=state,
                    revision=1,
                    updated_by="sys",
                    updated_at=(
                        datetime.datetime.now(
                            datetime.timezone.utc
                        )
                    ),
                )
            )

            db_session.add(
                config
            )

        else:
            config.state = state

        db_session.commit()

    # -----------------------------------------------------
    # 1. Populate Redis while the database says DISABLED.
    # -----------------------------------------------------

    _set_sms(
        "DISABLED"
    )

    repository = (
        CommunicationConfigurationRepository(
            db_session
        )
    )

    configuration_service = (
        CommunicationConfigurationService(
            repository,
            db_session,
            redis_client=fake_redis,
        )
    )

    disabled_configuration = (
        configuration_service
        .get_channel_configuration(
            "SMS"
        )
    )

    assert (
        disabled_configuration.state
        == CommunicationChannelState.DISABLED
    )

    sms_cache_key = (
        f"{_CACHE_KEY_PREFIX}:sms"
    )

    assert (
        sms_cache_key
        in fake_redis.store
    )

    # -----------------------------------------------------
    # 2. Change the database to ENABLED.
    #
    # Redis must continue returning DISABLED until the
    # cached entry reaches its 60-second expiry.
    # -----------------------------------------------------

    _set_sms(
        "ENABLED"
    )

    monkeypatch.setattr(
        twilio_sms_mod,
        "get_redis_client",
        lambda: fake_redis,
    )

    # Do not call the real Twilio provider.
    # None activates the existing local simulated-success
    # behavior when delivery is eventually allowed.
    monkeypatch.setattr(
        twilio_sms_mod,
        "twilio_client",
        None,
    )

    # Keep this test focused on configuration caching.
    # SMS rate limiting is tested separately.
    monkeypatch.setattr(
        twilio_sms_mod,
        "check_rate_limit",
        lambda redis_client, tech_id: True,
    )

    technician = Technician(
        tech_id="tech-1-sms",
        tenant_id="default",
        technician_name="Tech 1",
        technician_skill="General",
        technician_location="Local",
        phone_number="+15555555555",
        sms_opt_out=False,
    )

    db_session.add(
        technician
    )
    db_session.commit()

    async def run_delivery():
        return await send_job_assignment_sms(
            db=db_session,
            job_id="job1",
            job_title="Title",
            location="Location",
            priority="High",
            tech_ids=[
                "tech-1-sms",
            ],
        )

    # -----------------------------------------------------
    # 3. Before expiry, cached DISABLED must block SMS.
    # -----------------------------------------------------

    first_result = asyncio.run(
        run_delivery()
    )

    assert first_result["sent"] == 0
    assert first_result["blocked"] == 1
    assert first_result["blocked_reasons"] == {
        "SMS_DISABLED": 1,
    }

    first_deliveries = (
        db_session.query(
            SMSDelivery
        )
        .filter_by(
            tech_id="tech-1-sms"
        )
        .order_by(
            SMSDelivery.id.asc()
        )
        .all()
    )

    assert len(
        first_deliveries
    ) == 1

    assert (
        first_deliveries[0].status
        == "failed"
    )

    assert (
        first_deliveries[0].error_message
        == "SMS_DISABLED"
    )

    # -----------------------------------------------------
    # 4. Expire Redis without sleeping.
    # -----------------------------------------------------

    sim_timer.tick(
        61
    )

    fake_redis.calls.clear()

    # -----------------------------------------------------
    # 5. After expiry, the database ENABLED value must be
    #    loaded and SMS delivery must succeed.
    # -----------------------------------------------------

    second_result = asyncio.run(
        run_delivery()
    )

    assert second_result["sent"] == 1
    assert second_result["failed"] == 0
    assert second_result["blocked"] == 0
    assert second_result["blocked_reasons"] == {}

    deliveries = (
        db_session.query(
            SMSDelivery
        )
        .filter_by(
            tech_id="tech-1-sms"
        )
        .order_by(
            SMSDelivery.id.desc()
        )
        .all()
    )

    assert len(
        deliveries
    ) == 2

    latest_delivery = deliveries[0]

    assert (
        latest_delivery.status
        == "sent"
    )

    assert (
        latest_delivery.sms_sid
        is not None
    )

    # The expired cache entry must have caused a database
    # read and a fresh SETEX operation.
    assert (
        f"get:{sms_cache_key}"
        in fake_redis.calls
    )

    assert (
        f"setex:{sms_cache_key}"
        in fake_redis.calls
    )

    cached_value, expires_at = (
        fake_redis.store[
            sms_cache_key
        ]
    )

    cached_data = json.loads(
        cached_value
    )

    assert (
        cached_data["state"]
        == "ENABLED"
    )

    assert (
        expires_at
        - sim_timer.time()
        == 60
    )


def test_email_cache_aware_delivery(
    db_session,
    fake_redis,
    sim_timer,
    monkeypatch,
):
    import app.services.notification_services as notif_mod

    from app.services.ai.FieldOpsAI.repositories.communication_configuration_repository import (
        CommunicationConfigurationRepository,
    )
    from app.services.notification_services import (
        NotificationRouter,
    )

    def _set_email(
        state: str,
    ) -> None:
        config = (
            db_session.query(
                CommunicationChannelConfiguration
            )
            .filter_by(
                channel="EMAIL"
            )
            .first()
        )

        if config is None:
            config = CommunicationChannelConfiguration(
                tenant_id="tenant1",
                channel="EMAIL",
                state=state,
                revision=1,
                updated_by="sys",
                updated_at=datetime.datetime.now(
                    datetime.timezone.utc
                ),
            )
            db_session.add(config)

        else:
            config.state = state

        db_session.commit()

    # -----------------------------------------------------
    # Step 1: Store EMAIL as DISABLED and populate Redis.
    # -----------------------------------------------------

    _set_email("DISABLED")

    repository = CommunicationConfigurationRepository(
        db_session
    )

    configuration_service = CommunicationConfigurationService(
        repository,
        db_session,
        redis_client=fake_redis,
    )

    cached_configuration = (
        configuration_service
        .get_channel_configuration(
            "EMAIL"
        )
    )

    assert (
        cached_configuration.state
        == CommunicationChannelState.DISABLED
    )

    email_cache_key = (
        f"{_CACHE_KEY_PREFIX}:email"
    )

    assert (
        email_cache_key
        in fake_redis.store
    )

    # -----------------------------------------------------
    # Step 2: Change only the database to ENABLED.
    #
    # Redis still contains DISABLED until its TTL expires.
    # -----------------------------------------------------

    _set_email("ENABLED")

    class _FakeEmailAdapter:
        def __init__(self):
            self.calls = []

        async def send_email(
            self,
            *args,
            **kwargs,
        ):
            self.calls.append(
                {
                    "args": args,
                    "kwargs": kwargs,
                }
            )
            return True

    email_adapter = _FakeEmailAdapter()

    class FakeSessionProxy:
        """
        Reuse the pytest-managed database session without
        allowing NotificationRouter to close it.
        """

        def __init__(
            self,
            session,
        ):
            self._session = session

        def __getattr__(
            self,
            name,
        ):
            return getattr(
                self._session,
                name,
            )

        def close(self):
            # The pytest db_session fixture owns this session.
            pass

        def __enter__(self):
            return self

        def __exit__(
            self,
            exc_type,
            exc_value,
            traceback,
        ):
            return False

    monkeypatch.setattr(
        notif_mod,
        "SessionLocal",
        lambda: FakeSessionProxy(
            db_session
        ),
    )

    # Provide all dependencies required by NotificationRouter.
    router = NotificationRouter(
        fcm_service=AsyncMock(
            return_value={
                "sent": 0,
                "failed": 0,
                "delivery_ids": [],
            }
        ),
        sms_service=AsyncMock(
            return_value={
                "sent": 0,
                "failed": 0,
                "blocked": 0,
                "blocked_reasons": {},
            }
        ),
        email_service=email_adapter,
        ws_manager=MagicMock(),
        redis_client=fake_redis,
        communication_integration=(
            _FakeCommIntegration()
        ),
    )

    # Use the complete real JobStatusEvent helper that already
    # exists earlier in this test file.
    event = _build_completed_event()

    async def run_delivery():
        await router._send_email(
            event,
            "customer",
            {},
            {},
            "job_done_survey",
            category=(
                CommunicationMessageCategory.STANDARD
            ),
        )

    # -----------------------------------------------------
    # Step 3: Cached DISABLED must block the provider.
    # -----------------------------------------------------

    with pytest.raises(
        CommunicationChannelDisabledError
    ):
        asyncio.run(
            run_delivery()
        )

    assert email_adapter.calls == []

    assert (
        f"get:{email_cache_key}"
        in fake_redis.calls
    )

    # -----------------------------------------------------
    # Step 4: Expire the Redis entry without waiting.
    # -----------------------------------------------------

    sim_timer.tick(61)
    fake_redis.calls.clear()

    # -----------------------------------------------------
    # Step 5: Database now says ENABLED.
    #
    # The provider should be called and Redis should be
    # populated again with TTL 60.
    # -----------------------------------------------------

    asyncio.run(
        run_delivery()
    )

    assert len(
        email_adapter.calls
    ) == 1

    assert (
        f"get:{email_cache_key}"
        in fake_redis.calls
    )

    assert (
        f"setex:{email_cache_key}"
        in fake_redis.calls
    )

    cached_value, expires_at = (
        fake_redis.store[
            email_cache_key
        ]
    )

    cached_data = json.loads(
        cached_value
    )

    assert (
        cached_data["state"]
        == "ENABLED"
    )

    assert (
        expires_at
        - sim_timer.time()
        == 60
    )

# ==============================================================================
# Story 14.4  Immediate Invalidation Tests
# ==============================================================================

def test_14_4_successful_invalidation(db_session, fake_redis, sim_timer):
    from app.services.ai.FieldOpsAI.repositories.communication_configuration_repository import CommunicationConfigurationRepository
    repo = CommunicationConfigurationRepository(db_session)
    service = CommunicationConfigurationService(repo, db_session, redis_client=fake_redis)

    # Pre-populate both keys
    service.get_channel_configuration("SMS")
    service.get_channel_configuration("EMAIL")
    
    fake_redis.calls.clear()
    
    # 1, 3, 5, 6, 7, 9 - SMS state change deletes only SMS key after commit
    service.update_channel_state(
        channel="SMS",
        new_state=CommunicationChannelState.DISABLED,
        actor_id="u",
        actor_tenant_id="t",
        reason="some reason"
    )
    
    assert f"delete:fieldops:communication-config:v1:sms" in fake_redis.calls
    assert f"delete:fieldops:communication-config:v1:email" not in fake_redis.calls
    assert "fieldops:communication-config:v1:sms" not in fake_redis.store
    assert "fieldops:communication-config:v1:email" in fake_redis.store

    fake_redis.calls.clear()

    # 2, 4 - EMAIL state change leaves SMS key unchanged
    service.update_channel_state(
        channel="EMAIL",
        new_state=CommunicationChannelState.DISABLED,
        actor_id="u",
        actor_tenant_id="t",
        reason="another reason"
    )
    assert f"delete:fieldops:communication-config:v1:email" in fake_redis.calls
    assert f"delete:fieldops:communication-config:v1:sms" not in fake_redis.calls
    assert "fieldops:communication-config:v1:email" not in fake_redis.store
    
    fake_redis.calls.clear()
    
    # 10 - No-op performs no invalidation
    service.update_channel_state(
        channel="SMS",
        new_state=CommunicationChannelState.DISABLED,
        actor_id="u",
        actor_tenant_id="t",
        reason="noop reason"
    )
    assert f"delete:fieldops:communication-config:v1:sms" not in fake_redis.calls

def test_14_4_failed_commit_leaves_cache(db_session, fake_redis, monkeypatch):
    from app.services.ai.FieldOpsAI.repositories.communication_configuration_repository import CommunicationConfigurationRepository
    from app.models import CommunicationConfigurationAudit
    repo = CommunicationConfigurationRepository(db_session)
    service = CommunicationConfigurationService(repo, db_session, redis_client=fake_redis)

    # 8 - Cache deletion does not happen before a failed commit
    # 41, 42, 43 - Failed commit leaves cache untouched, revision unchanged, no audit
    service.get_channel_configuration("SMS")
    
    config_before = repo.get_by_channel("SMS")
    state_before = config_before.state
    revision_before = config_before.revision
    audit_count_before = db_session.query(CommunicationConfigurationAudit).count()
    cache_val_before = fake_redis.store.get("fieldops:communication-config:v1:sms")
    fake_redis.calls.clear()
    
    def fake_commit():
        raise Exception("DB Error")
        
    monkeypatch.setattr(db_session, "commit", fake_commit)
    
    with pytest.raises(Exception):
        service.update_channel_state(
            channel="SMS",
            new_state=CommunicationChannelState.DISABLED,
            actor_id="u",
            actor_tenant_id="t",
            reason="some reason"
        )
        
    db_session.rollback()
    config_after = repo.get_by_channel("SMS")
    assert config_after.state == state_before
    assert config_after.revision == revision_before
    
    audit_count_after = db_session.query(CommunicationConfigurationAudit).count()
    assert audit_count_after == audit_count_before
    
    assert fake_redis.store.get("fieldops:communication-config:v1:sms") == cache_val_before
    assert f"delete:fieldops:communication-config:v1:sms" not in fake_redis.calls
    assert f"setex:fieldops:communication-config:v1:sms" not in fake_redis.calls
    
def test_14_4_immediate_get_behavior(db_session, fake_redis, sim_timer):
    from app.services.ai.FieldOpsAI.repositories.communication_configuration_repository import CommunicationConfigurationRepository
    repo = CommunicationConfigurationRepository(db_session)
    service = CommunicationConfigurationService(repo, db_session, redis_client=fake_redis)

    # 11, 12, 13, 14
    service.get_channel_configuration("SMS")
    service.update_channel_state(
        channel="SMS",
        new_state=CommunicationChannelState.DISABLED,
        actor_id="u",
        actor_tenant_id="t",
        reason="some reason"
    )
    
    fake_redis.calls.clear()
    res = service.get_channel_configuration("SMS")
    assert res.state == CommunicationChannelState.DISABLED
    assert f"get:fieldops:communication-config:v1:sms" in fake_redis.calls
    assert f"setex:fieldops:communication-config:v1:sms" in fake_redis.calls
    
    fake_redis.calls.clear()
    res2 = service.get_channel_configuration("SMS")
    assert res2.state == CommunicationChannelState.DISABLED
    assert f"get:fieldops:communication-config:v1:sms" in fake_redis.calls
    assert f"setex:fieldops:communication-config:v1:sms" not in fake_redis.calls

def test_14_4_cross_process_behavior(fake_redis):
    import os
    import tempfile
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.models import Base
    from app.services.ai.FieldOpsAI.repositories.communication_configuration_repository import CommunicationConfigurationRepository
    from app.services.ai.FieldOpsAI.services.communication_configuration_service import CommunicationConfigurationService
    from app.services.ai.FieldOpsAI.schemas.communication_configuration import CommunicationChannelState
    
    fd, temp_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        engine = create_engine(f"sqlite:///{temp_path}")
        Base.metadata.create_all(engine)
        
        session1 = Session(engine)
        session2 = Session(engine)
        
        repo1 = CommunicationConfigurationRepository(session1)
        service_a = CommunicationConfigurationService(repo1, session1, redis_client=fake_redis)
        
        repo2 = CommunicationConfigurationRepository(session2)
        service_b = CommunicationConfigurationService(repo2, session2, redis_client=fake_redis)
        
        # prime db with initial data
        from app.models import CommunicationChannelConfiguration
        session1.add(CommunicationChannelConfiguration(tenant_id="tenant1",channel="SMS", state="ENABLED", revision=1, updated_by="sys"))
        session1.add(CommunicationChannelConfiguration(tenant_id="tenant1",channel="EMAIL", state="ENABLED", revision=1, updated_by="sys"))
        session1.commit()
        
        # prime both caches on session2
        service_b.get_channel_configuration("SMS")
        service_b.get_channel_configuration("EMAIL")
        
        # Update on session1
        service_a.update_channel_state("SMS", CommunicationChannelState.DISABLED, "u", "t", "valid reason here")
        service_a.update_channel_state("EMAIL", CommunicationChannelState.DISABLED, "u", "t", "valid reason here")
        
        # Session2 should immediately see it because cache was invalidated and populate_existing gets fresh DB rows
        res_sms = service_b.get_channel_configuration("SMS")
        assert res_sms.state == CommunicationChannelState.DISABLED
        
        res_email = service_b.get_channel_configuration("EMAIL")
        assert res_email.state == CommunicationChannelState.DISABLED
        
        session1.close()
        session2.close()
        engine.dispose()
    finally:
        try:
            session1.close()
        except:
            pass

        try:
            session2.close()
        except:
            pass

        engine.dispose()

        import gc
        gc.collect()

        os.remove(temp_path)
    
def test_14_4_redis_failure_handling(db_session, fake_redis, monkeypatch):
    # 48-58
    from app.services.ai.FieldOpsAI.repositories.communication_configuration_repository import CommunicationConfigurationRepository
    repo = CommunicationConfigurationRepository(db_session)
    service = CommunicationConfigurationService(repo, db_session, redis_client=fake_redis)
    
    service.get_channel_configuration("SMS")
    
    # 49 - DELETE failure triggers SETEX replacement attempt
    fake_redis.fail_delete = True
    fake_redis.calls.clear()
    
    res = service.update_channel_state(
        channel="SMS",
        new_state=CommunicationChannelState.DISABLED,
        actor_id="u",
        actor_tenant_id="t",
        reason="valid reason here"
    )
    assert res.state == CommunicationChannelState.DISABLED
    assert f"delete:fieldops:communication-config:v1:sms" in fake_redis.calls
    assert f"setex:fieldops:communication-config:v1:sms" in fake_redis.calls
    
    # Verify replaced payload
    val, expires_at_before = fake_redis.store["fieldops:communication-config:v1:sms"]
    data = json.loads(val)
    assert data["state"] == "DISABLED"
    
    # DELETE and SETEX failure still preserve committed DB state
    fake_redis.fail_setex = True
    
    logged_warnings = []
    def mock_warning(msg, *args, **kwargs):
        logged_warnings.append(msg)
        
    monkeypatch.setattr("app.services.ai.FieldOpsAI.services.communication_configuration_service.logger.warning", mock_warning)
    
    res2 = service.update_channel_state(
        channel="SMS",
        new_state=CommunicationChannelState.EMERGENCY_ONLY,
        actor_id="u",
        actor_tenant_id="t",
        reason="another reason"
    )
    assert res2.state == CommunicationChannelState.EMERGENCY_ONLY

    # direct evidence checks
    db_config = repo.get_by_channel("SMS")
    assert db_config.state == CommunicationChannelState.EMERGENCY_ONLY.value
    assert db_config.revision > 1
    
    # one audit row committed
    from app.models import CommunicationConfigurationAudit
    audit_count = db_session.query(CommunicationConfigurationAudit).filter_by(channel="SMS", new_state="EMERGENCY_ONLY").count()
    assert audit_count == 1
    
    # old cache payload may still contain DISABLED
    val_after, expires_at_after = fake_redis.store["fieldops:communication-config:v1:sms"]
    data_after = json.loads(val_after)
    assert data_after["state"] == "DISABLED"
    
    # cache TTL was not extended
    assert expires_at_after == expires_at_before
    
    # sanitized cache_sync_degraded log was emitted
    log_emitted = any("cache_sync_degraded" in msg for msg in logged_warnings)
    assert log_emitted
    
    # raw exception text was not logged
    exception_emitted = any("ConnectionError" in msg for msg in logged_warnings)
    assert not exception_emitted

    
@pytest.mark.parametrize("channel", ["SMS", "EMAIL"])
def test_14_4_admin_api(db_session, fake_redis, channel):
    from app.main import app
    from app.database import get_db
    from app.redis_client import get_redis_client
    from app.dependencies.prompt_admin_authorization import require_prompt_admin, PromptAdminPrincipal
    from fastapi.testclient import TestClient
    
    other_channel = "EMAIL" if channel == "SMS" else "SMS"
    
    overrides = app.dependency_overrides.copy()
    try:
        app.dependency_overrides[get_db] = lambda: db_session
        app.dependency_overrides[get_redis_client] = lambda: fake_redis
        app.dependency_overrides[require_prompt_admin] = lambda: PromptAdminPrincipal(
            actor_id="admin-1", tenant_id="**platform**", role="super_admin"
        )
        
        with TestClient(app) as client:
            client.get(f"/admin/communication-config/channels/{channel}")
            client.get(f"/admin/communication-config/channels/{other_channel}")
            
            put_res = client.put(f"/admin/communication-config/channels/{channel}", json={"state": "DISABLED", "reason": "this is a valid reason"})
            assert put_res.status_code == 200
            
            # Cache no longer serves old state, unrelated channel remains
            assert f"fieldops:communication-config:v1:{channel.lower()}" not in fake_redis.store
            assert f"fieldops:communication-config:v1:{other_channel.lower()}" in fake_redis.store
            
            get_res = client.get(f"/admin/communication-config/channels/{channel}")
            assert get_res.json()["state"] == "DISABLED"
            
            # Repopulated TTL is exactly 60
            val, expires_at = fake_redis.store[f"fieldops:communication-config:v1:{channel.lower()}"]
            import time
            ttl = expires_at - fake_redis.timer.time()
            assert abs(ttl - 60) < 1
    finally:
        app.dependency_overrides = overrides

def test_14_4_sms_immediate_provider_boundary(db_session, fake_redis, monkeypatch, no_sms_rate_limit):
    from app.services.twilio_sms import send_job_assignment_sms
    from unittest.mock import patch, MagicMock
    from app.models import Technician
    from app.services.ai.FieldOpsAI.repositories.communication_configuration_repository import CommunicationConfigurationRepository
    from app.services.ai.FieldOpsAI.services.communication_configuration_service import CommunicationConfigurationService
    from app.services.ai.FieldOpsAI.schemas.communication_configuration import CommunicationChannelState, CommunicationMessageCategory
    import asyncio
    
    monkeypatch.setattr("app.services.twilio_sms.get_redis_client", lambda: fake_redis)
    
    repo = CommunicationConfigurationRepository(db_session)
    service = CommunicationConfigurationService(repo, db_session, redis_client=fake_redis)
    
    tech = Technician(technician_id=99, tech_id="tech99", technician_name="T", technician_skill="S", technician_location="L", phone_number="+1234567890", sms_opt_out=0)
    db_session.add(tech)
    db_session.commit()
    
    # prime cached ENABLED
    service.update_channel_state("SMS", CommunicationChannelState.ENABLED, "u", "t", "r"*10)
    service.get_channel_configuration("SMS")
    
    # call update_channel_state(SMS, DISABLED)
    service.update_channel_state("SMS", CommunicationChannelState.DISABLED, "u", "t", "r"*10)
    
    with patch("app.services.twilio_sms.twilio_client") as mock_twilio:
        # immediately execute real SMS delivery path
        # do not advance fake clock
        result = asyncio.run(send_job_assignment_sms(db_session, "j", "t", "l", "p", ["tech99"]))
        mock_twilio.messages.create.assert_not_called()
        
    # test DISABLED -> ENABLED
    service.update_channel_state("SMS", CommunicationChannelState.ENABLED, "u", "t", "r"*10)
    with patch("app.services.twilio_sms.twilio_client") as mock_twilio:
        mock_twilio.messages.create.return_value = MagicMock(sid="123")
        result = asyncio.run(send_job_assignment_sms(db_session, "j", "t", "l", "p", ["tech99"]))
        mock_twilio.messages.create.assert_called_once()
        
    # test ENABLED -> EMERGENCY_ONLY
    service.update_channel_state("SMS", CommunicationChannelState.EMERGENCY_ONLY, "u", "t", "r"*10)
    with patch("app.services.twilio_sms.twilio_client") as mock_twilio:
        mock_twilio.messages.create.return_value = MagicMock(sid="123")
        
        # standard is blocked
        result = asyncio.run(send_job_assignment_sms(db_session, "j", "t", "l", "p", ["tech99"]))
        mock_twilio.messages.create.assert_not_called()
        
        # emergency allowed
        result = asyncio.run(send_job_assignment_sms(db_session, "j", "t", "l", "p", ["tech99"], category=CommunicationMessageCategory.EMERGENCY))
        mock_twilio.messages.create.assert_called_once()

def test_14_4_email_immediate_provider_boundary(db_session, fake_redis, monkeypatch):
    import asyncio
    from app.services.ai.FieldOpsAI.repositories.communication_configuration_repository import CommunicationConfigurationRepository
    from app.services.ai.FieldOpsAI.services.communication_configuration_service import CommunicationConfigurationService
    from app.services.ai.FieldOpsAI.schemas.communication_configuration import CommunicationChannelState, CommunicationMessageCategory
    import app.services.notification_services as _notification_module
    
    repo = CommunicationConfigurationRepository(db_session)
    service = CommunicationConfigurationService(repo, db_session, redis_client=fake_redis)
    
    class FakeSessionLocal:
        def __init__(self):
            self.db = db_session
        def __enter__(self):
            return self.db
        def __exit__(self, *args):
            pass
        def __call__(self):
            return self.db
            
    monkeypatch.setattr(_notification_module, "SessionLocal", FakeSessionLocal())
    monkeypatch.setattr(_notification_module, "get_redis_client", lambda: fake_redis)
    
    # prime cached ENABLED
    _set_email_state(db_session, "ENABLED")
    service.get_channel_configuration("EMAIL")
    
    email_svc = _TrackingEmailService()
    router = _make_router(email_svc, db_session)
    router.redis = fake_redis
    
    event = _build_completed_event()
    
    # call update_channel_state(EMAIL, DISABLED)
    service.update_channel_state("EMAIL", CommunicationChannelState.DISABLED, "u", "t", "r"*10)
    
    # immediately execute real EMAIL delivery path
    asyncio.run(router.route(event))
    assert len(email_svc.calls) == 0
    
    # test DISABLED -> ENABLED
    service.update_channel_state("EMAIL", CommunicationChannelState.ENABLED, "u", "t", "r"*10)
    asyncio.run(router.route(event))
    assert len(email_svc.calls) == 1
    
    # test ENABLED -> EMERGENCY_ONLY
    service.update_channel_state("EMAIL", CommunicationChannelState.EMERGENCY_ONLY, "u", "t", "r"*10)
    
    # standard is blocked
    email_svc.calls.clear()
    asyncio.run(router.route(event))
    assert len(email_svc.calls) == 0
    
    # emergency allowed
    asyncio.run(router._send_email(
        _build_completed_event(),
        "customer",
        {},
        {},
        "job_done_survey",
        category=CommunicationMessageCategory.EMERGENCY,
    ))
    assert len(email_svc.calls) == 1
