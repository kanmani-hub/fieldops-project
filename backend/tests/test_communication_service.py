"""
End-to-end unit tests for the production-safe Communication
Service.
"""

from __future__ import annotations

import json

from collections.abc import Iterator

import pytest

from sqlalchemy import create_engine
from sqlalchemy.orm import (
    Session,
    sessionmaker,
)
from sqlalchemy.pool import StaticPool

from app.models import (
    AIBrandSafetyRule,
    AIGuardrailViolation,
    NotificationTemplate,
)
from app.services.ai.FieldOpsAI.schemas.communication import (
    CommunicationContext,
    CommunicationDecision,
)
from app.services.ai.FieldOpsAI.services.communication_service import (
    CommunicationService,
    SafeCommunicationUnavailableError,
)
from app.services.ai.guardrails.fallback_service import (
    FallbackTemplateSource,
)


# ==========================================================
# Test Doubles
# ==========================================================


class FakeAgent:
    """
    Controlled replacement for CommunicationAgent.
    """

    def __init__(
        self,
        *,
        decision: CommunicationDecision | None = None,
        error: Exception | None = None,
    ) -> None:
        self.decision = decision
        self.error = error

        self.received_context: (
            CommunicationContext
            | None
        ) = None

    def generate(
        self,
        context: CommunicationContext,
    ) -> CommunicationDecision:
        self.received_context = context

        if self.error is not None:
            raise self.error

        if self.decision is None:
            raise RuntimeError(
                "No fake decision configured."
            )

        return self.decision


class FakeRedis:
    """
    Minimal Redis replacement.
    """

    def __init__(
        self,
    ) -> None:
        self.values: dict[str, str] = {}

        self.fail_get = False
        self.fail_setex = False
        self.fail_delete = False

    def get(
        self,
        key: str,
    ) -> str | None:
        if self.fail_get:
            raise RuntimeError(
                "Redis unavailable."
            )

        return self.values.get(
            key
        )

    def setex(
        self,
        key: str,
        seconds: int,
        value: str,
    ) -> bool:
        _ = seconds

        if self.fail_setex:
            raise RuntimeError(
                "Redis unavailable."
            )

        self.values[key] = value

        return True

    def delete(
        self,
        key: str,
    ) -> bool:
        if self.fail_delete:
            raise RuntimeError(
                "Redis unavailable."
            )

        return (
            self.values.pop(
                key,
                None,
            )
            is not None
        )


# ==========================================================
# Database Fixture
# ==========================================================


@pytest.fixture
def db_session() -> Iterator[Session]:
    """
    Create an isolated test database containing only the tables
    required by the communication workflow.
    """

    engine = create_engine(
        "sqlite://",
        connect_args={
            "check_same_thread": False,
        },
        poolclass=StaticPool,
    )

    NotificationTemplate.__table__.create(
        bind=engine
    )

    AIBrandSafetyRule.__table__.create(
        bind=engine
    )

    AIGuardrailViolation.__table__.create(
        bind=engine
    )

    testing_session = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )

    session = testing_session()

    try:
        yield session

    finally:
        session.close()

        AIGuardrailViolation.__table__.drop(
            bind=engine
        )

        AIBrandSafetyRule.__table__.drop(
            bind=engine
        )

        NotificationTemplate.__table__.drop(
            bind=engine
        )

        engine.dispose()


# ==========================================================
# Helpers
# ==========================================================


def build_context(
    *,
    channel: str = "SMS",
) -> CommunicationContext:
    """
    Build an original context containing real local PII.
    """

    return CommunicationContext(
        job_id="JOB-1001",
        correlation_id="correlation-1001",
        notification_type="job_assigned",
        recipient_type="CUSTOMER",
        channel=channel,
        locale="en",
        customer_name="Ruby Devi",
        technician_name="Arun Kumar",
        job_status="ASSIGNED",
        job_title="Air conditioner repair",
        eta="30 minutes",
        sentiment="NEUTRAL",
    )


def build_sms_decision(
    message: str,
) -> CommunicationDecision:
    """
    Build one SMS decision.
    """

    return CommunicationDecision(
        channel="SMS",
        title=None,
        subject=None,
        message=message,
        tone="PROFESSIONAL",
        confidence=0.95,
    )


def build_service(
    db: Session,
    *,
    agent: FakeAgent,
    tenant_id: str = "tenant-1",
    redis: FakeRedis | None = None,
) -> CommunicationService:
    """
    Build the production service using a fake agent.
    """

    return CommunicationService(
        db=db,
        tenant_id=tenant_id,
        redis_client=redis,
        agent=agent,
        fingerprint_key="test-audit-secret",
    )


def add_competitor_rule(
    db: Session,
    *,
    tenant_id: str,
) -> None:
    """
    Add one database-backed competitor rule.
    """

    db.add(
        AIBrandSafetyRule(
            tenant_id=tenant_id,
            rule_id="COMPETITOR_ACME",
            category="COMPETITOR",
            match_type="PHRASE",
            pattern="Acme Services",
            severity="ERROR",
            active=True,
            case_sensitive=False,
            created_by="admin-1",
        )
    )

    db.commit()


def add_unsafe_database_template(
    db: Session,
) -> None:
    """
    Add an unsafe template to confirm fallback validation blocks
    it.
    """

    db.add(
        NotificationTemplate(
            name="Unsafe SMS fallback",
            type="job_assigned",
            channel="sms",
            locale="en",
            format="text",
            title_template=None,
            body_template=(
                "Hello {{customer_name}}, shut up and wait."
            ),
            variables=[
                {"name": "customer_name", "required": False}
            ],
            version=1,
            is_active=True,
        )
    )

    db.commit()


# ==========================================================
# Tests
# ==========================================================


def test_safe_ai_output_is_allowed_and_pii_is_restored(
    db_session: Session,
) -> None:
    """
    Safe placeholder-based AI output is restored only after all
    guardrails pass.
    """

    agent = FakeAgent(
        decision=build_sms_decision(
            (
                "Hello {{customer_name}}, "
                "{{technician_name}} is assigned."
            )
        )
    )

    result = build_service(
        db_session,
        agent=agent,
    ).generate(
        context=build_context()
    )

    assert result.used_fallback is False
    assert result.fallback_source is None
    assert result.guardrail_result.passed is True
    assert result.audit_record_count == 0

    assert result.decision.message == (
        "Hello Ruby Devi, Arun Kumar is assigned."
    )

    assert agent.received_context is not None

    assert agent.received_context.customer_name == (
        "{{customer_name}}"
    )

    assert agent.received_context.technician_name == (
        "{{technician_name}}"
    )

    assert (
        db_session.query(
            AIGuardrailViolation
        ).count()
        == 0
    )


def test_profanity_triggers_builtin_fallback_and_audit(
    db_session: Session,
) -> None:
    """
    Unsafe AI text is replaced by a built-in approved template.
    """

    agent = FakeAgent(
        decision=build_sms_decision(
            "Hello {{customer_name}}, this is bullshit."
        )
    )

    result = build_service(
        db_session,
        agent=agent,
    ).generate(
        context=build_context()
    )

    assert result.used_fallback is True

    assert result.fallback_source == (
        FallbackTemplateSource.BUILTIN
    )

    assert "bullshit" not in (
        result.decision.message.lower()
    )

    assert "Ruby Devi" in (
        result.decision.message
    )

    assert result.audit_record_count >= 1

    row = (
        db_session.query(
            AIGuardrailViolation
        )
        .one()
    )

    assert row.category == "PROFANITY"
    assert row.fallback_triggered is True
    assert row.pipeline_decision == "FALLBACK"


def test_provider_failure_uses_fallback_and_system_audit(
    db_session: Session,
) -> None:
    """
    Provider or response parsing failures never escape to the
    caller as unsafe output.
    """

    agent = FakeAgent(
        error=RuntimeError(
            "Simulated provider failure."
        )
    )

    result = build_service(
        db_session,
        agent=agent,
    ).generate(
        context=build_context()
    )

    assert result.used_fallback is True
    assert result.guardrail_result.passed is False

    row = (
        db_session.query(
            AIGuardrailViolation
        )
        .one()
    )

    assert row.checker_name == "provider_execution"
    assert row.violation_code == "AI_GENERATION_FAILED"
    assert row.category == "SYSTEM"
    assert row.fallback_triggered is True


def test_tenant_competitor_rule_triggers_fallback(
    db_session: Session,
) -> None:
    """
    Tenant database rules are connected to the live pipeline.
    """

    add_competitor_rule(
        db_session,
        tenant_id="tenant-1",
    )

    agent = FakeAgent(
        decision=build_sms_decision(
            "Please use Acme Services instead."
        )
    )

    result = build_service(
        db_session,
        agent=agent,
    ).generate(
        context=build_context()
    )

    assert result.used_fallback is True

    row = (
        db_session.query(
            AIGuardrailViolation
        )
        .one()
    )

    assert row.violation_code == (
        "BRAND_COMPETITOR_MENTION"
    )

    assert row.fallback_triggered is True


def test_competitor_rules_are_tenant_isolated(
    db_session: Session,
) -> None:
    """
    A rule belonging to Tenant 2 must not affect Tenant 1.
    """

    add_competitor_rule(
        db_session,
        tenant_id="tenant-2",
    )

    agent = FakeAgent(
        decision=build_sms_decision(
            "Acme Services has an update."
        )
    )

    result = build_service(
        db_session,
        agent=agent,
        tenant_id="tenant-1",
    ).generate(
        context=build_context()
    )

    assert result.used_fallback is False
    assert result.guardrail_result.passed is True


def test_channel_mismatch_triggers_correct_channel_fallback(
    db_session: Session,
) -> None:
    """
    Email output cannot be used for an SMS request.
    """

    email_decision = CommunicationDecision(
        channel="EMAIL",
        title=None,
        subject="Job assigned",
        message=(
            "Hello {{customer_name}}, your job is assigned."
        ),
        tone="PROFESSIONAL",
        confidence=0.95,
    )

    agent = FakeAgent(
        decision=email_decision
    )

    result = build_service(
        db_session,
        agent=agent,
    ).generate(
        context=build_context(
            channel="SMS"
        )
    )

    assert result.used_fallback is True
    assert result.decision.channel == "SMS"

    row = (
        db_session.query(
            AIGuardrailViolation
        )
        .one()
    )

    assert row.category == (
        "CHANNEL_MISMATCH"
    )


def test_unsafe_database_fallback_is_blocked(
    db_session: Session,
) -> None:
    """
    A misconfigured database fallback is validated and blocked.
    """

    add_unsafe_database_template(
        db_session
    )

    agent = FakeAgent(
        decision=build_sms_decision(
            "Hello {{customer_name}}, shut up."
        )
    )

    with pytest.raises(
        SafeCommunicationUnavailableError,
        match="Fallback communication also failed",
    ):
        build_service(
            db_session,
            agent=agent,
        ).generate(
            context=build_context()
        )

    rows = (
        db_session.query(
            AIGuardrailViolation
        )
        .order_by(
            AIGuardrailViolation.created_at.asc()
        )
        .all()
    )

    assert len(rows) >= 2

    assert any(
        row.agent_name
        == "guardrail_fallback_service"
        for row in rows
    )

    assert all(
        row.fallback_triggered is False
        for row in rows
    )


def test_raw_pii_and_generated_text_are_not_stored(
    db_session: Session,
) -> None:
    """
    Audit rows contain hashes and safe metadata, not raw names
    or unsafe generated messages.
    """

    agent = FakeAgent(
        decision=build_sms_decision(
            "Ruby Devi, shut up."
        )
    )

    result = build_service(
        db_session,
        agent=agent,
    ).generate(
        context=build_context()
    )

    assert result.used_fallback is True

    row = (
        db_session.query(
            AIGuardrailViolation
        )
        .one()
    )

    safe_audit_text = (
        row.safe_message
        + " "
        + json.dumps(
            row.safe_metadata,
            sort_keys=True,
        )
    ).lower()

    assert "ruby devi" not in safe_audit_text
    assert "arun kumar" not in safe_audit_text
    assert "shut up" not in safe_audit_text

    assert len(row.prompt_hash) == 64
    assert len(row.output_hash) == 64

    assert row.job_id == "{{job_id}}"


def test_redis_failure_still_loads_database_rules(
    db_session: Session,
) -> None:
    """
    Redis failure does not disable PostgreSQL brand rules.
    """

    add_competitor_rule(
        db_session,
        tenant_id="tenant-1",
    )

    redis = FakeRedis()
    redis.fail_get = True
    redis.fail_setex = True

    agent = FakeAgent(
        decision=build_sms_decision(
            "Please contact Acme Services."
        )
    )

    result = build_service(
        db_session,
        agent=agent,
        redis=redis,
    ).generate(
        context=build_context()
    )

    assert result.used_fallback is True

    row = (
        db_session.query(
            AIGuardrailViolation
        )
        .one()
    )

    assert row.violation_code == (
        "BRAND_COMPETITOR_MENTION"
    )